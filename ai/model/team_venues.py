"""
Team -> venue/city resolution (seeded map + Open-Meteo geocoding fallback)
and historical/forecast weather fetching.

Caches:
  ai/data/team_venues.json        team -> {city, country, lat, lon}
  ai/data/weather_cache/{key}.json  per-city daily archive weather 2000->today

Used by the historical backfill (ai/enrich_weather_history.py) and the live
predictor (forecast weather at prediction time).
"""
from __future__ import annotations
import gzip, json, os, re, time, urllib.parse, urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENUE_CACHE = os.path.join(BASE, "data", "team_venues.json")
WX_CACHE_DIR = os.path.join(BASE, "data", "weather_cache")

# Clubs whose name doesn't contain their city. Exact canonical names.
SEED = {
    # England — London & odd names
    "Arsenal FC": ("London", "England"), "Tottenham Hotspur FC": ("London", "England"),
    "Chelsea FC": ("London", "England"), "West Ham United FC": ("London", "England"),
    "Crystal Palace FC": ("London", "England"), "Fulham FC": ("London", "England"),
    "Queens Park Rangers FC": ("London", "England"), "Wimbledon": ("London", "England"),
    "Charlton Athletic FC": ("London", "England"), "Millwall FC": ("London", "England"),
    "Brentford FC": ("London", "England"), "Leyton Orient FC": ("London", "England"),
    "AFC Bournemouth": ("Bournemouth", "England"), "Brighton & Hove Albion FC": ("Brighton", "England"),
    "Wolverhampton Wanderers FC": ("Wolverhampton", "England"),
    "West Bromwich Albion FC": ("West Bromwich", "England"),
    "Sheffield United FC": ("Sheffield", "England"), "Sheffield Wednesday FC": ("Sheffield", "England"),
    "Norwich City FC": ("Norwich", "England"), "Preston North End FC": ("Preston", "England"),
    # Scotland
    "Celtic FC": ("Glasgow", "Scotland"), "Rangers FC": ("Glasgow", "Scotland"),
    " Aberdeen FC": ("Aberdeen", "Scotland"),
    # Spain
    "Real Sociedad": ("San Sebastián", "Spain"), "Athletic Club": ("Bilbao", "Spain"),
    "Real Madrid CF": ("Madrid", "Spain"), "Atlético de Madrid": ("Madrid", "Spain"),
    "RC Celta de Vigo": ("Vigo", "Spain"), "Real Betis": ("Seville", "Spain"),
    "Sevilla FC": ("Seville", "Spain"), "UD Las Palmas": ("Las Palmas", "Spain"),
    # Italy — most contain city; odd ones:
    "Hellas Verona FC": ("Verona", "Italy"), "US Lecce": ("Lecce", "Italy"),
    # Germany
    "Borussia Mönchengladbach": ("Mönchengladbach", "Germany"),
    "TSG 1899 Hoffenheim": ("Sinsheim", "Germany"), "1. FC Köln": ("Cologne", "Germany"),
    "Bayer 04 Leverkusen": ("Leverkusen", "Germany"), "VfB Stuttgart": ("Stuttgart", "Germany"),
    # France
    "Paris Saint-Germain FC": ("Paris", "France"), "OGC Nice": ("Nice", "France"),
    "RC Lens": ("Lens", "France"), "FC Nantes": ("Nantes", "France"),
    # Netherlands
    "AFC Ajax": ("Amsterdam", "Netherlands"), "Feyenoord": ("Rotterdam", "Netherlands"),
    "PSV": ("Eindhoven", "Netherlands"), "AZ Alkmaar": ("Alkmaar", "Netherlands"),
    "FC Twente": ("Enschede", "Netherlands"), "SBV Excelsior": ("Rotterdam", "Netherlands"),
    # Portugal
    "Sporting CP": ("Lisbon", "Portugal"), "Sporting Lisbon": ("Lisbon", "Portugal"),
    "SL Benfica": ("Lisbon", "Portugal"), "FC Porto": ("Porto", "Portugal"),
    "Boavista FC": ("Porto", "Portugal"), "Vitória de Guimarães": ("Guimarães", "Portugal"),
    # Turkey
    "Galatasaray": ("Istanbul", "Turkey"), "Fenerbahçe": ("Istanbul", "Turkey"),
    "Beşiktaş JK": ("Istanbul", "Turkey"), "İstanbul Başakşehir FK": ("Istanbul", "Turkey"),
    "Trabzonspor": ("Trabzon", "Turkey"),
    # Greece
    "Olympiacos": ("Piraeus", "Greece"), "AEK Athens": ("Athens", "Greece"),
    "Panathinaikos": ("Athens", "Greece"), "PAOK": ("Thessaloniki", "Greece"),
    "Aris Thessaloniki": ("Thessaloniki", "Greece"),
    # Belgium
    "Club Brugge": ("Bruges", "Belgium"), "RSC Anderlecht": ("Brussels", "Belgium"),
    "Standard Liège": ("Liège", "Belgium"), "KRC Genk": ("Genk", "Belgium"),
    # Switzerland / Austria / Russia / Ukraine / Serbia
    "FC Basel": ("Basel", "Switzerland"), "BSC Young Boys": ("Bern", "Switzerland"),
    "Red Star Belgrade": ("Belgrade", "Serbia"), "Dinamo Zagreb": ("Zagreb", "Croatia"),
    "Dynamo Kyiv": ("Kyiv", "Ukraine"), "Shakhtar Donetsk": ("Donetsk", "Ukraine"),
    "FC Zenit": ("Saint Petersburg", "Russia"), "PFC CSKA Moscow": ("Moscow", "Russia"),
    "Spartak Moscow": ("Moscow", "Russia"), "Lokomotiv Moscow": ("Moscow", "Russia"),
    "FK Crvena Zvezda": ("Belgrade", "Serbia"),
    # Americas
    "Boca Juniors": ("Buenos Aires", "Argentina"), "River Plate": ("Buenos Aires", "Argentina"),
    "Flamengo": ("Rio de Janeiro", "Brazil"), "Vasco da Gama": ("Rio de Janeiro", "Brazil"),
    "Fluminense": ("Rio de Janeiro", "Brazil"), "Botafogo": ("Rio de Janeiro", "Brazil"),
    "Cruzeiro": ("Belo Horizonte", "Brazil"), "Atlético Mineiro": ("Belo Horizonte", "Brazil"),
    "Grêmio": ("Porto Alegre", "Brazil"), "Internacional": ("Porto Alegre", "Brazil"),
    "Corinthians": ("São Paulo", "Brazil"), "São Paulo": ("São Paulo", "Brazil"),
    "Palmeiras": ("São Paulo", "Brazil"), "Santos": ("Santos", "Brazil"),
    "LA Galaxy": ("Los Angeles", "USA"), "LAFC": ("Los Angeles", "USA"),
    "Inter Miami": ("Fort Lauderdale", "USA"), "New York City FC": ("New York", "USA",
    ),
    "New York Red Bulls": ("Harrison", "USA"), "Seattle Sounders": ("Seattle", "USA"),
    "Portland Timbers": ("Portland", "USA"), "Sporting Kansas City": ("Kansas City", "USA"),
    "Real Salt Lake": ("Salt Lake City", "USA"), "CF Montréal": ("Montreal", "Canada"),
    "Toronto FC": ("Toronto", "Canada"), "Vancouver Whitecaps": ("Vancouver", "Canada"),
    "Club América": ("Mexico City", "Mexico"), "Guadalajara": ("Guadalajara", "Mexico"),
    "Monterrey": ("Monterrey", "Mexico"), "Tigres UANL": ("Monterrey", "Mexico"),
    "Colo-Colo": ("Santiago", "Chile"), "Universidad de Chile": ("Santiago", "Chile"),
    "Nacional": ("Montevideo", "Uruguay"), "Peñarol": ("Montevideo", "Uruguay"),
    # Asia / Middle East
    "Urawa Red Diamonds": ("Saitama", "Japan"), "Kashima Antlers": ("Kashima", "Japan"),
    "Yokohama F. Marinos": ("Yokohama", "Japan"), "FC Tokyo": ("Tokyo", "Japan"),
    "Gamba Osaka": ("Osaka", "Japan"), "Al Hilal": ("Riyadh", "Saudi Arabia"),
    "Al Nassr": ("Riyadh", "Saudi Arabia"), "Al Ittihad": ("Jeddah", "Saudi Arabia"),
    "Al Ahli": ("Jeddah", "Saudi Arabia"),
}

_NOISE = {"fc", "cf", "ac", "sc", "afc", "bsc", "ssc", "us", "sv", "vfb", "vfl", "tsg",
          "fk", "pfc", "sk", "skc", "rc", "cd", "sd", "ud", "ca", "club", "united", "city",
          "the", "de", "of", "jk", "kv", "krc", "rsc", "sbv", "ogc", "az", "psv", "ii"}
_COUNTRY_ISO = {
    "England": "GB", "Scotland": "GB", "Wales": "GB", "Northern Ireland": "GB",
    "USA": "US", "United States": "US", "Netherlands": "NL", "Türkiye": "TR",
    "Turkey": "TR", "Russia": "RU", "Czech Republic": "CZ", "Czechia": "CZ",
}


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                if raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                return json.loads(raw)
        except Exception:
            if attempt == 3:
                return {}
            time.sleep(1.5 * (attempt + 1))
    return {}


def _load_cache(path: str):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def _candidates(team: str) -> list[str]:
    name = re.sub(r"[^\w\s&áéíóúàèìòùâêîôûäëïöüñçøåæßışğüčšžý]", " ", team)
    toks = [t for t in name.split() if t.lower() not in _NOISE and not t.isdigit() and len(t) > 1]
    cands = []
    if toks:
        cands.append(" ".join(toks))
        cands.append(toks[-1])
        if len(toks) > 1:
            cands.append(toks[0])
    seen, out = set(), []
    for c in cands:
        c = c.strip()
        if c and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out


def resolve_venue(team: str, country: str | None = None, verbose: bool = False) -> dict | None:
    """Return {city, country, lat, lon} for a team; None if unresolvable."""
    cache = _load_cache(VENUE_CACHE)
    key = team.strip()
    if key in cache and cache[key] and cache[key].get("lat") is not None:
        return cache[key]

    result = None
    if key in SEED:
        city, ctry = SEED[key]
        result = {"city": city, "country": ctry, "lat": None, "lon": None}
        url = ("https://geocoding-api.open-meteo.com/v1/search?count=1&language=en&format=json"
               "&name=" + urllib.parse.quote(city))
        time.sleep(0.22)
        hits = (_get(url).get("results") or [])
        if hits:
            result["lat"] = hits[0].get("latitude")
            result["lon"] = hits[0].get("longitude")
    else:
        iso = _COUNTRY_ISO.get(country or "", None)
        for q in _candidates(team):
            url = ("https://geocoding-api.open-meteo.com/v1/search?count=5&language=en&format=json"
                   "&name=" + urllib.parse.quote(q))
            time.sleep(0.22)
            data = _get(url)
            hits = data.get("results") or []
            if not hits:
                continue
            pick = None
            if iso:
                for h in hits:
                    if (h.get("country_code") or "").upper() == iso:
                        pick = h
                        break
            if pick is None:
                # accept only if the hit's country roughly matches ours
                if country and any((h.get("country") or "").lower() == country.lower() for h in hits):
                    pick = next(h for h in hits if (h.get("country") or "").lower() == country.lower())
            if pick is None and len(hits) == 1 and country is None:
                pick = hits[0]
            if pick:
                result = {"city": pick.get("name"), "country": pick.get("country"),
                          "lat": pick.get("latitude"), "lon": pick.get("longitude")}
                break

    if result is None and country:
        # last resort: country centroid
        url = ("https://geocoding-api.open-meteo.com/v1/search?count=1&language=en&format=json"
               "&name=" + urllib.parse.quote(country))
        time.sleep(0.22)
        hits = (_get(url).get("results") or [])
        if hits:
            h = hits[0]
            result = {"city": None, "country": country, "lat": h.get("latitude"), "lon": h.get("longitude")}

    cache[key] = result
    _save_cache(VENUE_CACHE, cache)
    if verbose:
        print(f"  {'ok  ' if result else 'MISS'} {team} -> {result}")
    return result


def fetch_archive_weather(lat: float, lon: float) -> dict[str, dict]:
    """Daily weather 2000-01-01 -> today for one location. Cached per rounded city."""
    os.makedirs(WX_CACHE_DIR, exist_ok=True)
    key = f"{round(lat, 1):.1f}_{round(lon, 1):.1f}"
    path = os.path.join(WX_CACHE_DIR, key + ".json")
    import datetime as _dt
    today = _dt.date.today().isoformat()
    cached = _load_cache(path)
    if cached.get("_through") == today:
        return cached["days"]

    url = (f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}"
           f"&start_date=2000-01-01&end_date={today}"
           "&daily=temperature_2m_mean,precipitation_sum,wind_speed_10m_max,weather_code&timezone=auto")
    data = _get(url)
    dly = data.get("daily") or {}
    times = dly.get("time") or []
    days = {}
    for i, d in enumerate(times):
        days[d] = {
            "temp_c": (dly.get("temperature_2m_mean") or [None] * len(times))[i],
            "precip_mm": (dly.get("precipitation_sum") or [None] * len(times))[i],
            "wind_kmh": (dly.get("wind_speed_10m_max") or [None] * len(times))[i],
            "weather_code": (dly.get("weather_code") or [None] * len(times))[i],
        }
    if days:
        _save_cache(path, {"_through": today, "lat": lat, "lon": lon, "days": days})
    return days


def fetch_forecast_weather(lat: float, lon: float, date_str: str) -> dict | None:
    """Forecast/actual weather for an upcoming match date at a location."""
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
           f"&daily=temperature_2m_mean,precipitation_sum,wind_speed_10m_max,weather_code"
           f"&start_date={date_str}&end_date={date_str}&timezone=auto")
    data = _get(url)
    dly = data.get("daily") or {}
    if not dly.get("time"):
        return None
    return {
        "temp_c": (dly.get("temperature_2m_mean") or [None])[0],
        "precip_mm": (dly.get("precipitation_sum") or [None])[0],
        "wind_kmh": (dly.get("wind_speed_10m_max") or [None])[0],
        "weather_code": (dly.get("weather_code") or [None])[0],
    }
