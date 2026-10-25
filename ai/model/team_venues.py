"""
Team -> venue/city resolution (seeded map + Open-Meteo geocoding fallback)
and historical/forecast weather fetching.

Caches:
  ai/data/team_venues.json        team -> {city, country, lat, lon}
  ai/data/weather_cache/{key}.json  per-city daily archive weather 2000->today
  ai/data/weather_cache/forecast/{lat}_{lon}_{date}.json  per-fixture forecast
                                   (TTL: 24h far out, 3h inside 2 days, 7d once played)

Used by the historical backfill (ai/enrich_weather_history.py) and the live
predictor (forecast weather at prediction time).
"""
from __future__ import annotations
import gzip, datetime as dt, json, math, os, re, sys, time, urllib.error, urllib.parse, urllib.request

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


def _get_archive(url: str) -> tuple[dict | list | None, int]:
    """Like _get, but surfaces the HTTP status so callers can back off on 429s.

    Returns (parsed_json, http_status). Status 0 = network error/timeout.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"})
    status, last_err = 0, None
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = r.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            return json.loads(raw), 200
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            e.read()
        except Exception:
            pass
    except Exception as e:  # URLError / timeout
        last_err = str(e)
    print(f"      archive fetch failed (http {status}{' ' + last_err if last_err else ''})",
          file=sys.stderr, flush=True)
    return None, status


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


def resolve_venue(team: str, country: str | None = None, verbose: bool = False,
                  cache: dict | None = None, persist: bool = True) -> dict | None:
    """Return {city, country, lat, lon} for a team; None if unresolvable."""
    own_cache = cache is None
    if own_cache:
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
    if persist and own_cache:
        _save_cache(VENUE_CACHE, cache)
    if verbose:
        print(f"  {'ok  ' if result else 'MISS'} {team} -> {result}")
    return result


def resolve_many(teams: list[tuple[str, str | None]], workers: int = 4) -> dict[str, dict | None]:
    """Resolve many teams concurrently; shares one cache, saves once at the end."""
    from concurrent.futures import ThreadPoolExecutor
    cache = _load_cache(VENUE_CACHE)
    todo = [(t, c) for t, c in teams
            if not (cache.get(t) and cache[t] and cache[t].get("lat") is not None)]
    print(f"  {len(teams) - len(todo)} already cached, {len(todo)} to resolve")
    if todo:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(lambda tc: resolve_venue(tc[0], tc[1], cache=cache, persist=False), todo))
        _save_cache(VENUE_CACHE, cache)
    out = {}
    for t, _ in teams:
        v = cache.get(t)
        out[t] = v if (v and v.get("lat") is not None) else None
    return out


def fetch_archive_weather(lat: float, lon: float) -> dict[str, dict]:
    """Daily weather 2000-01-01 -> today for one location. Cached per rounded city."""
    out = fetch_archive_weather_many([(lat, lon)])
    return out.get(_wx_key(lat, lon), {})


def _wx_key(lat: float, lon: float) -> str:
    return f"{round(lat, 1):.1f}_{round(lon, 1):.1f}"


def fetch_archive_weather_many(coords: list[tuple[float, float]],
                               batch: int = 30, pause: float = 2.0) -> dict[str, dict]:
    """Archive weather for many locations, batched (30 cities/request), cached per city.

    Open-Meteo accepts comma-separated coordinates; one request covers the whole
    2000->today daily range for every city in the batch.
    """
    import datetime as _dt
    os.makedirs(WX_CACHE_DIR, exist_ok=True)
    today = _dt.date.today().isoformat()

    pending: dict[str, tuple[float, float]] = {}
    result: dict[str, dict] = {}
    for lat, lon in coords:
        if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
                and math.isfinite(float(lat)) and math.isfinite(float(lon))
                and -90.0 <= float(lat) <= 90.0 and -180.0 <= float(lon) <= 180.0):
            continue                      # a malformed coordinate 400s the whole batch
        key = _wx_key(lat, lon)
        if key in result:
            continue
        cached = _load_cache(os.path.join(WX_CACHE_DIR, key + ".json"))
        if cached.get("_through") == today:
            result[key] = cached["days"]
        else:
            pending[key] = (lat, lon)

    # Worklist rather than fixed chunks: Open-Meteo answers HTTP 400 when a
    # request spans too many locations x days (30 cities over 2000->today is
    # over the limit, 30 over a short range is fine).  On a 400 we halve the
    # chunk and requeue, so the batch size adapts to the API instead of
    # failing the run.  429 stays a back-off.
    queue = [list(pending.items())[i:i + batch] for i in range(0, len(pending), batch)]
    ci = 0
    consecutive_fail = 0
    last_ok = time.time()
    chunk_fails: dict[tuple, int] = {}
    # Open-Meteo throttles shared CI runner IPs hard (the log showed 429 after
    # ~3 successful batches, then nothing for 80 minutes).  Cycling the queue
    # with an escalating cooldown instead of abandoning the run is what makes
    # progress compound: every run saves its fetch cache, so a patient run
    # finishes what a panicky one threw away.
    stall_limit = float(os.environ.get("WX_STALL_SECONDS", "1800") or 1800)
    while queue:
        chunk = queue.pop(0)
        ci += 1
        lats = ",".join(f"{lat}" for _, (lat, _) in chunk)
        lons = ",".join(f"{lon}" for _, (_, lon) in chunk)
        url = (f"https://archive-api.open-meteo.com/v1/archive?latitude={lats}&longitude={lons}"
               f"&start_date=2000-01-01&end_date={today}"
               "&daily=temperature_2m_mean,precipitation_sum,wind_speed_10m_max,weather_code"
               "&timezone=auto")
        data, status, wait, split = None, 0, 0, False
        for attempt in range(4):
            data, status = _get_archive(url)
            if data:
                break
            if status == 400 and len(chunk) > 1:
                split = True
                break
            # Short retries + cycling beats one 20-minute block per chunk: a
            # throttled IP recovers in bursts, so keep re-asking.
            wait = (60 if status == 429 else 20) + 30 * attempt
            time.sleep(wait)
        if split:
            half = max(1, len(chunk) // 2)
            queue.insert(0, chunk[half:])
            queue.insert(0, chunk[:half])
            continue
        if not data:
            consecutive_fail += 1
            ck = tuple(k for k, _ in chunk)
            chunk_fails[ck] = chunk_fails.get(ck, 0) + 1
            if chunk_fails[ck] >= 6:
                # a chunk that still 400s/429s at this size is poison — drop it
                # rather than burn the whole run re-asking (the rest requeues)
                print(f"  batch {ci}: dropping chunk of {len(chunk)} city(ies) after "
                      f"{chunk_fails[ck]} failures (http {status})", flush=True)
                if not queue:
                    print("  nothing left to try — stopping; rerun later, cache resumes.",
                          flush=True)
                    break
                continue
            queue.append(chunk)                      # retry later, don't drop it
            since = time.time() - last_ok
            if consecutive_fail % 3 == 0:
                if since > stall_limit:
                    print(f"  no city fetched for {since / 60:.0f} min — stopping; "
                          f"rerun later, cache resumes.", flush=True)
                    break
                cooldown = min(300 * (consecutive_fail // 3), 900)
                print(f"  {consecutive_fail} throttled batches, {since / 60:.0f} min since "
                      f"last success — cooling down {cooldown}s ({len(queue)} queued)",
                      flush=True)
                time.sleep(cooldown)
            else:
                print(f"  batch {ci}: FAILED (http {status}) "
                      f"[{consecutive_fail} consecutive, {len(queue)} queued]", flush=True)
            continue
        consecutive_fail = 0
        last_ok = time.time()
        if ci % 25 == 0 or not queue:
            print(f"  batch {ci}: {len(queue)} chunk(s) left", flush=True)
        items = data if isinstance(data, list) else [data] if data else []
        positional = len(items) == len(chunk)
        got = 0
        for ii, loc in enumerate(items):
            dly = loc.get("daily") or {}
            times = dly.get("time") or []
            if not times:
                continue
            # match back to the requested city (API echoes lat/lon); fall back to order
            key = None
            try:
                rlat, rlon = round(float(loc.get("latitude")), 1), round(float(loc.get("longitude")), 1)
                for k, (lat, lon) in chunk:
                    if round(lat, 1) == rlat and round(lon, 1) == rlon:
                        key = k
                        break
            except (TypeError, ValueError):
                pass
            if key is None and positional:
                key = chunk[ii][0]
            if key is None:
                continue
            days = {}
            tm = dly.get("time")
            tcol = dly.get("temperature_2m_mean") or [None] * len(tm)
            pcol = dly.get("precipitation_sum") or [None] * len(tm)
            wcol = dly.get("wind_speed_10m_max") or [None] * len(tm)
            ccol = dly.get("weather_code") or [None] * len(tm)
            for i, d in enumerate(tm):
                days[d] = {"temp_c": tcol[i], "precip_mm": pcol[i],
                           "wind_kmh": wcol[i], "weather_code": ccol[i]}
            if days:
                _save_cache(os.path.join(WX_CACHE_DIR, key + ".json"),
                            {"_through": today, "days": days})
                result[key] = days
                got += 1
        print(f"  batch {ci}: +{got}/{len(chunk)} cities "
              f"({len(result)}/{len(pending)} done, {len(queue)} chunk(s) left)", flush=True)
        if queue:
            time.sleep(pause)
    return result


#: Directory for forecast caches: {lat}_{lon}_{date}.json -> {fetched_at, data}.
#: Lives inside weather_cache/, which is gitignored like the archive cache.
FC_CACHE_DIR = os.path.join(WX_CACHE_DIR, "forecast")


def _forecast_ttl(date_str: str) -> int:
    """How long a forecast fetched for `date_str` stays trustworthy.

    Far-out forecasts move around the most, so they are re-fetched sooner than
    near-term ones (which barely change hour to hour).  A date that has already
    passed is observation, not forecast, and can be kept for a week.
    """
    try:
        target = dt.datetime.strptime(date_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return 3 * 3600
    days = (target - dt.date.today()).days
    if days < 0:
        return 7 * 86400
    if days <= 2:
        return 3 * 3600
    if days <= 6:
        return 6 * 3600
    return 24 * 3600


def fetch_forecast_weather(lat: float, lon: float, date_str: str) -> dict | None:
    """Forecast/actual weather for an upcoming match date at a location.

    Cached per (lat, lon, date) with a distance-aware TTL (see
    `_forecast_ttl`), so the prediction pipeline makes one API call per
    fixture per freshness window instead of one per run.
    """
    key = f"{lat:.3f}_{lon:.3f}_{date_str}"
    path = os.path.join(FC_CACHE_DIR, key + ".json")
    cached = _load_cache(path)
    if isinstance(cached, dict) and "fetched_at" in cached:
        age = time.time() - float(cached.get("fetched_at") or 0)
        # a miss is retried on a short leash; a hit lives for the full TTL
        ttl = 3600 if cached.get("data") is None else _forecast_ttl(date_str)
        if 0 <= age < ttl:
            return cached.get("data")

    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
           f"&daily=temperature_2m_mean,precipitation_sum,wind_speed_10m_max,weather_code"
           f"&start_date={date_str}&end_date={date_str}&timezone=auto")
    data = _get(url)
    dly = data.get("daily") or {}
    if not dly.get("time"):
        # cache the miss too, otherwise a bad date hammers the API every run
        _save_cache(path, {"fetched_at": time.time(), "data": None})
        return None
    result = {
        "temp_c": (dly.get("temperature_2m_mean") or [None])[0],
        "precip_mm": (dly.get("precipitation_sum") or [None])[0],
        "wind_kmh": (dly.get("wind_speed_10m_max") or [None])[0],
        "weather_code": (dly.get("weather_code") or [None])[0],
    }
    _save_cache(path, {"fetched_at": time.time(), "data": result})
    return result
