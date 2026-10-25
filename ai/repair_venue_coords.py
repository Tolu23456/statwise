#!/usr/bin/env python3
"""
Repair wrong coordinates in ai/data/team_venues.json (plan Step 1.2 "Add venue data").

The geocode cache picked up several bad hits during bulk resolution — e.g. a
batch of Turkish clubs landed on New York (40.7, -74.4) and Real Sociedad on
Caguas, Puerto Rico.  Those poison the travel/fatigue features with thousand-
kilometre phantom journeys.

Detection uses an external signal (a rough country reference point + radius),
never a median of the cache itself, because a wrong cluster can become the
majority and then hide itself.

Repair re-geocodes only the flagged entries, biased to their own country, and
keeps the old value unless the new one is actually closer to the country
reference.

Usage:
    .venv/bin/python ai/repair_venue_coords.py            # dry run (report only)
    .venv/bin/python ai/repair_venue_coords.py --apply    # write the cache
"""
from __future__ import annotations

import json
import math
import re
import os
import sys
import time
import urllib.parse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from model.team_venues import (  # noqa: E402
    VENUE_CACHE, _COUNTRY_ISO, _get, _load_cache, _save_cache,
)

# (reference latitude, reference longitude, max plausible distance km)
COUNTRY_REF = {
    "United Kingdom": (54.0, -2.0, 700), "England": (52.5, -1.5, 650),
    "Scotland": (56.7, -4.2, 400), "Wales": (52.3, -3.7, 300),
    "Northern Ireland": (54.6, -6.7, 250), "Ireland": (53.4, -8.0, 400),
    "Republic of Ireland": (53.4, -8.0, 400),
    # Spain/Portugal include Atlantic territories (Canaries, Madeira, Azores),
    # Mexico includes Baja California — these legitimately sit far from the
    # reference point and must not be flagged as bad data.
    "Spain": (40.2, -3.6, 2100), "Portugal": (39.6, -8.2, 1600),
    "Italy": (42.8, 12.6, 900), "France": (46.6, 2.4, 1050),
    "Germany": (51.0, 10.0, 750), "Netherlands": (52.2, 5.5, 350),
    "The Netherlands": (52.2, 5.5, 350), "Belgium": (50.6, 4.6, 320),
    "Switzerland": (46.8, 8.2, 380), "Austria": (47.6, 14.1, 520),
    "Denmark": (56.0, 9.5, 420), "Sweden": (62.0, 15.0, 1150),
    "Norway": (64.0, 12.0, 1250), "Finland": (63.0, 26.0, 1050),
    "Poland": (52.0, 19.5, 650), "Czech Republic": (49.8, 15.5, 420),
    "Czechia": (49.8, 15.5, 420), "Romania": (45.9, 25.0, 650),
    "Bulgaria": (42.7, 25.5, 430), "Greece": (39.0, 22.0, 750),
    "Turkey": (39.0, 35.0, 1050), "Türkiye": (39.0, 35.0, 1050),
    "Croatia": (45.2, 16.5, 520), "Serbia": (44.0, 20.8, 430),
    "Ukraine": (49.0, 32.0, 950),
    # Russia and Japan are transcontinental/insular: Vladivostok and Okinawa
    # are legitimate home cities sitting thousands of km from the reference.
    "Russia": (58.0, 60.0, 6000), "Japan": (36.5, 138.0, 1700),
    "Argentina": (-35.0, -64.0, 1500), "Brazil": (-10.0, -52.0, 2300),
    "Chile": (-33.0, -71.0, 1800), "Mexico": (23.6, -102.0, 1900),
    "United States": (39.0, -98.0, 2500), "USA": (39.0, -98.0, 2500),
    "Canada": (56.0, -106.0, 2600), "Japan": (36.5, 138.0, 1300),
    "China": (35.0, 105.0, 2500), "South Korea": (36.5, 127.8, 750),
    "Korea Republic": (36.5, 127.8, 750), "Saudi Arabia": (24.0, 45.0, 1300),
    "Egypt": (26.5, 30.0, 1100), "South Africa": (-29.0, 25.0, 1400),
    "Australia": (-25.0, 134.0, 3000),
    # South America and the rest of the continents: without a reference these
    # countries were never validated at all, which is how the "Unknown" batch
    # (and anything else mis-geocoded there) stayed invisible.
    "Peru": (-9.2, -75.0, 1600), "Colombia": (4.6, -74.1, 1900),
    "Venezuela": (8.0, -66.5, 1700), "Uruguay": (-33.0, -56.0, 900),
    "Ecuador": (-1.5, -78.5, 1000), "Bolivia": (-16.5, -64.5, 1600),
    "Paraguay": (-23.5, -58.0, 900),
    "Slovakia": (48.7, 19.7, 300), "Cyprus": (35.0, 33.2, 140),
    "Kazakhstan": (48.0, 67.0, 3000), "Azerbaijan": (40.3, 47.7, 500),
}

#: The geocoder's failure default: every club whose country came through as
#: "Unknown" got this single point in Bangladesh.  Coordinates this specific
#: are a fingerprint, not a location.
JUNK_COORD = (25.85106, 88.24131)

# team-name decorations we may strip when the club name embeds its city
# ("Kayserispor" -> "Kayseri")
_SUFFIXES = ["spor kulübü", "spor klubu", "spor fk", "spor", "fk", "sk", "ks",
             "as", "sp", "cf", "fc", "cfc", "afc", "bsc", "ssc", "us", "sv",
             "deportivo", "club de futbol"]

# Places where the geocoder is ambiguous ("Chelsea" also exists in the US) —
# the answer is not in doubt, so we write it directly.
# team -> (city, country, lat, lon)
DIRECT_FIX = {
    "Real Sociedad": ("San Sebastián", "Spain", 43.3183, -1.9812),
    "DC United": ("Washington", "United States", 38.9072, -77.0369),
    "D.C. United": ("Washington", "United States", 38.9072, -77.0369),
    "Beira Mar": ("Aveiro", "Portugal", 40.6405, -8.6538),
    "GD Estoril Praia": ("Estoril", "Portugal", 38.7000, -9.3960),
    "Gibraltar": ("Gibraltar", "Gibraltar", 36.1400, -5.3500),
    "SKA Khabarovsk": ("Khabarovsk", "Russia", 48.4827, 135.0838),
    # England/Scotland towns the geocoder would not return under country bias
    "Boston": ("Boston", "United Kingdom", 52.9756, -0.0214),
    "Boston Utd": ("Boston", "United Kingdom", 52.9756, -0.0214),
    "Dover Athletic": ("Dover", "United Kingdom", 51.1267, 1.3134),
    "King’s Lynn": ("King's Lynn", "United Kingdom", 52.7490, 0.4030),
    "Montrose": ("Montrose", "United Kingdom", 56.7105, -2.4710),
    # Turkish clubs whose re-geocode missed or landed on the wrong town
    "Galatasaray SK": ("Istanbul", "Turkey", 41.0383, 28.9873),
    "Fenerbahce SK": ("Istanbul", "Turkey", 40.9822, 29.0414),
    "Yukatel Kayserispor": ("Kayseri", "Turkey", 38.7333, 35.4833),
    "Aytemiz Alanyaspor": ("Alanya", "Turkey", 36.5438, 32.0033),
    "Atiker Konyaspor": ("Konya", "Turkey", 37.8667, 32.4833),
    "BB Erzurumspor": ("Erzurum", "Turkey", 39.9067, 41.2769),
    "Gençlerbirliği": ("Ankara", "Turkey", 39.9334, 32.8597),
    "Diyarbakirspor": ("Diyarbakır", "Turkey", 37.9144, 40.2306),
    "Eyupspor": ("Istanbul", "Turkey", 41.0360, 28.9010),
    "Eyüpspor": ("Istanbul", "Turkey", 41.0360, 28.9010),
    "Osmanlispor": ("Ankara", "Turkey", 39.9400, 32.8600),
    "Oftasspor": ("Of", "Turkey", 40.4467, 39.8758),
    "King\x92s Lynn": ("King's Lynn", "United Kingdom", 52.7490, 0.4030),
    "Altay": ("İzmir", "Turkey", 38.4237, 27.1428),
    "Amedspor": ("Diyarbakır", "Turkey", 37.9144, 40.2306),
    "A. Sebatspor": ("Akçaabat", "Turkey", 41.0226, 39.7258),
    "Ad. Demirspor": ("Adana", "Turkey", 37.0000, 35.3213),
    "Buyuksehyr": ("Istanbul", "Turkey", 41.0082, 28.9784),
    "Dardanelspor": ("Çanakkale", "Turkey", 40.1553, 26.4142),
    "Sekerspor": ("Ankara", "Turkey", 39.9334, 32.8597),
    # ── the "Unknown" batch: 94 clubs whose country label came through as
    #    "Unknown", so the geocoder had no bias and returned its default point
    #    in Bangladesh (25.85106, 88.24131) for every one of them.  All are
    #    Copa Libertadores / Champions League sides — verified home cities.
    "2 de Mayo": ("Pedro Juan Caballero", "Paraguay", -22.5500, -55.7300),
    "AA Argentinos Juniors": ("Buenos Aires", "Argentina", -34.6037, -58.4637),
    "AC Sparta Praha": ("Prague", "Czechia", 50.0736, 14.4161),
    "Academia Puerto Cabello": ("Puerto Cabello", "Venezuela", 10.4731, -68.0125),
    "Barcelona SC": ("Guayaquil", "Ecuador", -2.1967, -79.9203),
    "CA Boston River": ("Montevideo", "Uruguay", -34.9011, -56.1645),
    "CA Bucaramanga": ("Bucaramanga", "Colombia", 7.1193, -73.1227),
    "CA Central Córdoba": ("Santiago del Estero", "Argentina", -27.7950, -64.2615),
    "CA Huracán": ("Buenos Aires", "Argentina", -34.6486, -58.4432),
    "CA Lanús": ("Lanús", "Argentina", -34.7000, -58.3833),
    "CA Mineiro": ("Belo Horizonte", "Brazil", -19.9167, -43.9344),
    "CA Patronato": ("Paraná", "Argentina", -31.7333, -60.5333),
    "CA Peñarol": ("Montevideo", "Uruguay", -34.8750, -56.1650),
    "CA Platense": ("Buenos Aires", "Argentina", -34.5667, -58.4667),
    "CA Rosario Central": ("Rosario", "Argentina", -32.9575, -60.6503),
    "CA Talleres": ("Córdoba", "Argentina", -31.4167, -64.1833),
    "CA Vélez Sarsfield": ("Buenos Aires", "Argentina", -34.6333, -58.5000),
    "CAR Independiente del Valle": ("Sangolquí", "Ecuador", -0.3333, -78.4667),
    "CD Blooming": ("Santa Cruz de la Sierra", "Bolivia", -17.7833, -63.1821),
    "CD Cobresal": ("El Salvador", "Chile", -22.3333, -69.3333),
    "CD Coquimbo Unido": ("Coquimbo", "Chile", -29.9500, -71.3500),
    "CD Godoy Cruz Antonio Tomba": ("Godoy Cruz", "Argentina", -32.9333, -68.8333),
    "CD Huachipato": ("Talcahuano", "Chile", -36.7333, -73.1167),
    "CD Independiente Medellín": ("Medellín", "Colombia", 6.2442, -75.5812),
    "CD Iquique": ("Iquique", "Chile", -20.2133, -70.1517),
    "CD Maldonado": ("Maldonado", "Uruguay", -34.9000, -54.9500),
    "CD Nublense": ("Chillán", "Chile", -36.6067, -72.1033),
    "CD Palestino": ("Santiago", "Chile", -33.4500, -70.6667),
    "CD Tolima": ("Ibagué", "Colombia", 4.4389, -75.2322),
    "CD Universidad Católica": ("Santiago", "Chile", -33.4167, -70.5167),
    "CDC Atlético Nacional": ("Medellín", "Colombia", 6.2200, -75.5700),
    "CDP Curicó Unido": ("Curicó", "Chile", -34.9833, -71.2333),
    "CDP Junior FC": ("Barranquilla", "Colombia", 10.9639, -74.7964),
    "CF Universidad de Chile": ("Santiago", "Chile", -33.4400, -70.6100),
    "CS Cristal": ("Lima", "Peru", -12.0500, -77.1300),
    "CS Independiente Rivadavia": ("Mendoza", "Argentina", -32.8900, -68.8267),
    "CSCyD El Nacional": ("Quito", "Ecuador", -0.1800, -78.4700),
    "CSD Colo-Colo": ("Santiago", "Chile", -33.4600, -70.7400),
    "CSD Sport Huancayo": ("Huancayo", "Peru", -12.0667, -75.2167),
    "Carabobo FC": ("Valencia", "Venezuela", 10.1667, -68.0000),
    "Caracas FC": ("Caracas", "Venezuela", 10.4806, -66.9036),
    "Club Alianza Lima": ("Lima", "Peru", -12.0750, -77.0217),
    "Club Always Ready": ("La Paz", "Bolivia", -16.5000, -68.1500),
    "Club Aurora": ("Cochabamba", "Bolivia", -17.4139, -66.1564),
    "Club Bolívar": ("La Paz", "Bolivia", -16.4957, -68.1336),
    "Club Cerro Porteño": ("Asunción", "Paraguay", -25.2867, -57.6342),
    "Club Guaraní": ("Asunción", "Paraguay", -25.2833, -57.6333),
    "Club Libertad Asuncion": ("Asunción", "Paraguay", -25.2900, -57.6300),
    "Club Nacional": ("Asunción", "Paraguay", -25.2150, -57.6100),
    "Club Nacional Potosí": ("Potosí", "Bolivia", -19.5833, -65.7500),
    "Club Nacional de Football": ("Montevideo", "Uruguay", -34.9000, -56.1600),
    "Club The Strongest": ("La Paz", "Bolivia", -16.4960, -68.1400),
    "Club Universitario de Deportes": ("Lima", "Peru", -12.0500, -77.0167),
    "Cusco FC": ("Cusco", "Peru", -13.5320, -71.9675),
    "Defensor SC": ("Montevideo", "Uruguay", -34.9100, -56.1800),
    "Deportivo La Guaira FC": ("La Guaira", "Venezuela", 10.6011, -66.9333),
    "Deportivo Pereira": ("Pereira", "Colombia", 4.8133, -75.7114),
    "Deportivo Táchira FC": ("San Cristóbal", "Venezuela", 7.7667, -72.2333),
    "Estudiantes de La Plata": ("La Plata", "Argentina", -34.9215, -57.9545),
    "FBC Melgar": ("Arequipa", "Peru", -16.4067, -71.5228),
    "FC København": ("Copenhagen", "Denmark", 55.6633, 12.5683),
    "FK Bodø/Glimt": ("Bodø", "Norway", 67.2800, 14.4050),
    "FK Kairat": ("Almaty", "Kazakhstan", 43.2380, 76.8820),
    "FK Shakhtar Donetsk": ("Kharkiv", "Ukraine", 49.9935, 36.2304),
    "Fenerbahçe SK": ("Istanbul", "Turkey", 40.9822, 29.0414),
    "Fortaleza EC": ("Fortaleza", "Brazil", -3.7319, -38.5267),
    "GNK Dinamo Zagreb": ("Zagreb", "Croatia", 45.8150, 15.9819),
    "Independiente Santa Fe": ("Bogotá", "Colombia", 4.6875, -74.0950),
    "Juventud de Las Piedras": ("Las Piedras", "Uruguay", -34.7167, -56.2500),
    "LDU de Quito": ("Quito", "Ecuador", -0.1917, -78.4667),
    "Magallanes": ("Santiago", "Chile", -33.5000, -70.6500),
    "Metropolitanos FC": ("Caracas", "Venezuela", 10.4667, -66.9000),
    "Millonarios FC": ("Bogotá", "Colombia", 4.6500, -74.0800),
    "Monagas SC": ("Maturín", "Venezuela", 9.7500, -63.1667),
    "O'Higgins FC": ("Rancagua", "Chile", -34.1700, -70.7400),
    "Olimpia Asuncion": ("Asunción", "Paraguay", -25.2833, -57.6333),
    "PAE AEK": ("Athens", "Greece", 37.9833, 23.7333),
    "PAE Olympiakos SFP": ("Piraeus", "Greece", 37.9420, 23.6460),
    "Paphos FC": ("Paphos", "Cyprus", 34.7720, 32.4297),
    "Portuguesa FC": ("Acarigua", "Venezuela", 9.5600, -69.2000),
    "Qarabağ Ağdam FK": ("Baku", "Azerbaijan", 40.4093, 49.8671),
    "Rionegro Águilas": ("Rionegro", "Colombia", 6.1536, -75.3764),
    "Royale Union Saint-Gilloise": ("Brussels", "Belgium", 50.8276, 4.3406),
    "SC Corinthians Paulista": ("São Paulo", "Brazil", -23.5450, -46.4740),
    "SD Aucas": ("Quito", "Ecuador", -0.1917, -78.4400),
    "SK Slavia Praha": ("Prague", "Czechia", 50.0680, 14.4670),
    "Sabah FK": ("Baku", "Azerbaijan", 40.4000, 49.8700),
    "San Antonio Bulo Bulo": ("Cochabamba", "Bolivia", -17.4139, -66.1564),
    "Sportivo Trinidense": ("Asunción", "Paraguay", -25.2900, -57.6000),
    "Universidad Catolica": ("Santiago", "Chile", -33.4167, -70.5167),
    "Universidad Central de Venezuela FC": ("Caracas", "Venezuela", 10.4500, -66.9000),
    "Viking FK": ("Stavanger", "Norway", 58.9700, 5.7330),
    "Zamora FC": ("Barinas", "Venezuela", 8.6200, -70.2100),
    "ŠK Slovan Bratislava": ("Bratislava", "Slovakia", 48.1480, 17.1060),
}

# Cities the geocoder cannot pick reliably out of a club name.  Geocoded with
# the entry's own country bias, so "Boston" resolves to Lincolnshire not the US.
SEED_CITY = {
    # England
    "Arsenal": "London", "Chelsea": "London", "Queens Park Rangers": "London",
    "Newcastle United": "Newcastle upon Tyne", "Notts County": "Nottingham",
    "Port Vale": "Stoke-on-Trent", "Port Vale FC": "Stoke-on-Trent",
    "West Bromwich Albion": "West Bromwich",
    "Milton Keynes Dons": "Milton Keynes", "Boston": "Boston",
    "Boston Utd": "Boston", "Dover Athletic": "Dover",
    "King’s Lynn": "King's Lynn", "Dag and Red": "Dagenham",
    # Scotland
    "Hibernian FC": "Edinburgh", "Heart of Midlothian": "Edinburgh",
    "Spartans": "Edinburgh", "Queens Park": "Glasgow", "Scotland": "Glasgow",
    "Raith Rvs": "Kirkcaldy", "Hamilton": "Hamilton", "St Mirren FC": "Paisley",
    "Morton": "Greenock", "East Fife": "Methil", "Berwick": "Berwick-upon-Tweed",
    "Montrose": "Montrose", "Clyde": "Cumbernauld", "Livingston FC": "Livingston",
    "Albion Rvs": "Coatbridge", "Queen of Sth": "Dumfries",
    "Ross County FC": "Dingwall",
    # Netherlands
    "Cambuur": "Leeuwarden", "SC Cambuur": "Leeuwarden",
    "De Graafschap": "Doetinchem", "Graafschap": "Doetinchem",
    "Excelsior": "Rotterdam", "Sparta": "Rotterdam", "SBV Vitesse": "Arnhem",
    "FC Twente '65": "Enschede", "Go Ahead Eagles": "Deventer",
    "Roda": "Kerkrade", "Roda JC": "Kerkrade",
    "Telstar": "Velsen", "Telstar 1963": "Velsen",
    "Netherlands": "Amsterdam",
    # Turkey (clubs whose name does not embed the city)
    "Genclerbirligi": "Ankara", "Gencilerbirligi": "Ankara",
    "Hacettepespor": "Ankara", "MKE Ankaragücü": "Ankara",
    "Ankaragucu": "Ankara", "Erciyesspor": "Kayseri",
}

# ConIFA / non-geographic sides that were never real venues — a coordinate for
# them is a guess, and a guess poisons travel distances.  Leave them unknown.
CLEAR = {
    "Barawa", "Cascadia", "Chagos Islands", "Kabylia", "Kernow",
    "Matabeleland", "Somaliland", "Yorkshire", "Chechnya", "West Papua",
}


# country spellings the geocoder uses vs. what the cache stores
COUNTRY_CANON = {
    "England": "United Kingdom", "Scotland": "United Kingdom",
    "Wales": "United Kingdom", "Northern Ireland": "United Kingdom",
    "Republic of Ireland": "Ireland", "USA": "United States",
    "The Netherlands": "Netherlands", "Türkiye": "Turkey",
    "Republic of Türkiye": "Turkey", "Korea Republic": "South Korea",
}


def _country_keys(country: str | None) -> tuple[str | None, str | None]:
    """(iso code, canonical country name) for a stored country label."""
    if not country:
        return None, None
    country = country.strip()
    canon = COUNTRY_CANON.get(country, country)
    iso = _COUNTRY_ISO.get(country) or _COUNTRY_ISO.get(canon)
    return iso, canon


def haversine_km(lat1, lon1, lat2, lon2):
    p = math.radians
    dlat, dlon = p(lat2 - lat1), p(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(p(lat1)) * math.cos(p(lat2)) * math.sin(dlon / 2) ** 2)
    return 6371.0 * 2.0 * math.asin(min(1.0, math.sqrt(a)))


def _is_junk(entry: dict) -> bool:
    """True for the geocoder-failure fingerprint: no country, or the default
    point in Bangladesh that every country-less lookup returned."""
    if (entry.get("country") or "").strip().lower() in ("", "unknown"):
        return True
    lat, lon = entry.get("lat"), entry.get("lon")
    if lat is None or lon is None:
        return True
    return (abs(lat - JUNK_COORD[0]) < 0.01 and abs(lon - JUNK_COORD[1]) < 0.01)


def flag_bad(cache: dict) -> list[tuple[str, dict, float]]:
    out = []
    for team, entry in cache.items():
        if not isinstance(entry, dict) or entry.get("lat") is None:
            continue
        if _is_junk(entry):
            out.append((team, entry, 16000.0))   # sentinel distance: always first
            continue
        ref = COUNTRY_REF.get((entry.get("country") or "").strip())
        if not ref:
            continue
        d = haversine_km(entry["lat"], entry["lon"], ref[0], ref[1])
        if d > ref[2]:
            out.append((team, entry, d))
    out.sort(key=lambda x: -x[2])
    return out


def queries_for(team: str, entry: dict) -> list[str]:
    """Search terms most likely to locate this club, most specific first."""
    q = []
    if entry.get("city"):
        q.append(entry["city"])
    name = team.strip()
    low = name.casefold()
    for suf in _SUFFIXES:                       # "Kayserispor" -> "Kayseri"
        if low.endswith(suf):
            cand = name[: -len(suf)].strip(" -'")
            if len(cand) >= 3:
                q.append(cand)
            break
    q.append(name)
    toks = [t for t in re.split(r"[^0-9A-Za-zÀ-ÿ]+", name) if len(t) >= 4]
    if toks:
        q.append(toks[0])                       # "Dover Athletic" -> "Dover"
        if len(toks) > 1:
            q.append(toks[-1])
    seen, out = set(), []
    for s in q:
        s = s.strip()
        if s and s.casefold() not in seen:
            seen.add(s.casefold())
            out.append(s)
    return out


def _pick_hit(hits: list[dict], query: str, iso: str | None, country: str | None,
              require_country: bool = False):
    """Best hit for this query: right country first, then name match, then size.

    require_country=True (used for city seeds) refuses any hit outside the
    expected country — an exact name match in the wrong country is worthless.
    """
    q = query.casefold()

    def score(h):
        name = (h.get("name") or "").casefold()
        c_ok = bool((iso and (h.get("country_code") or "").upper() == iso)
                    or (country and (h.get("country") or "").lower() == country.lower()))
        exact = name == q
        pref = name.startswith(q) or q in name
        return (c_ok, exact, pref, h.get("population") or 0)

    ranked = sorted(hits, key=score, reverse=True)
    if not ranked:
        return None
    best = ranked[0]
    c_ok, exact, _, _ = score(best)
    if require_country:
        return best if c_ok else None
    # accept an out-of-country hit only when the name is an exact match
    if c_ok or exact:
        return best
    return None


def regeocode(team: str, entry: dict) -> dict | None:
    country = entry.get("country")
    iso, canon = _country_keys(country)
    country = canon or country
    for query in queries_for(team, entry):
        url = ("https://geocoding-api.open-meteo.com/v1/search?count=5"
               "&language=en&format=json&name=" + urllib.parse.quote(query))
        time.sleep(0.22)
        hits = (_get(url).get("results") or [])
        pick = _pick_hit(hits, query, iso, country)
        if pick and pick.get("latitude") is not None:
            return {"city": pick.get("name"), "country": pick.get("country") or country,
                    "lat": pick.get("latitude"), "lon": pick.get("longitude")}
    return None


def main() -> int:
    apply = "--apply" in sys.argv
    cache = _load_cache(VENUE_CACHE)
    bad = dict((t, (e, d)) for t, e, d in flag_bad(cache))
    print(f"{len(bad)} entries flagged as wrong "
          f"(of {sum(1 for e in cache.values() if isinstance(e, dict) and e.get('lat') is not None)} with coords)")

    fixed, cleared, failed = [], [], []

    # 1. entries we simply clear (non-geographic sides)
    for team in sorted(CLEAR):
        if team in cache:
            print(f"  CLEAR  {team[:34]:34s} (non-geographic side)")
            cache.pop(team, None)
            cleared.append(team)
            bad.pop(team, None)

    # 2. curated answers (applied when flagged, or when the stored point is
    #    already more than 50 km away from the known-correct one)
    for team, (city, country, lat, lon) in sorted(DIRECT_FIX.items()):
        cur = cache.get(team)
        stale = (not isinstance(cur, dict) or cur.get("lat") is None
                 or haversine_km(cur["lat"], cur["lon"], lat, lon) > 50)
        if team not in bad and not stale:
            continue
        print(f"  direct {team[:34]:34s} -> {city} ({country})")
        cache[team] = {"city": city, "country": country, "lat": lat, "lon": lon}
        fixed.append(team)
        bad.pop(team, None)

    # 3. unambiguous cities, geocoded with the entry's own country bias
    for team, city in sorted(SEED_CITY.items()):
        if team not in bad:
            continue
        country = (bad[team][0].get("country") or "").strip() or None
        iso, canon = _country_keys(country)
        country = canon or country
        url = ("https://geocoding-api.open-meteo.com/v1/search?count=10"
               "&language=en&format=json&name=" + urllib.parse.quote(city))
        time.sleep(0.22)
        hits = (_get(url).get("results") or [])
        pick = _pick_hit(hits, city, iso, country, require_country=True)
        if pick and pick.get("latitude") is not None:
            cache[team] = {"city": pick.get("name"), "country": pick.get("country") or country,
                           "lat": pick["latitude"], "lon": pick["longitude"]}
            print(f"  seed   {team[:34]:34s} -> {pick.get('name')} ({pick.get('country')})")
            fixed.append(team)
            bad.pop(team, None)
        else:
            print(f"  FAIL   {team[:34]:34s} seed '{city}' not found in {country}")
            failed.append(team)

    # 4. everything still flagged: re-geocode, accept only a hit that is in
    #    the expected country AND inside the country radius
    for team, (entry, dist) in sorted(bad.items(), key=lambda kv: -kv[1][1]):
        country = (entry.get("country") or "").strip()
        ref = COUNTRY_REF.get(country) or COUNTRY_REF.get(COUNTRY_CANON.get(country, country))
        exp_iso, _ = _country_keys(country)
        new = regeocode(team, entry)
        if new and new.get("lat") is not None and ref:
            new_dist = haversine_km(new["lat"], new["lon"], ref[0], ref[1])
            # compare ISO codes: geocoder says "Republic of Türkiye", cache "Turkey"
            got_iso, got_canon = _country_keys(new.get("country"))
            same_country = (bool(exp_iso and got_iso and exp_iso == got_iso)
                            or (country and (got_canon or "").lower() == country.lower())
                            or (country and (new.get("country") or "").lower() == country.lower()))
            if same_country and new_dist <= ref[2]:
                print(f"  fixed  {team[:34]:34s} {entry.get('city')} ({round(dist):,} km) "
                      f"-> {new.get('city')} ({round(new_dist):,} km)")
                cache[team] = new
                fixed.append(team)
                continue
        # no trustworthy answer: remove rather than keep a poisoned coordinate
        print(f"  CLEAR  {team[:34]:34s} {entry.get('city')} ({round(dist):,} km) "
              f"— no trustworthy hit")
        cache.pop(team, None)
        cleared.append(team)

    print(f"\nfixed={len(fixed)} cleared={len(cleared)} failed_seed={len(failed)}")
    if apply and (fixed or cleared):
        _save_cache(VENUE_CACHE, cache)
        print(f"saved {VENUE_CACHE}")
        still = flag_bad(cache)
        print(f"remaining flagged after repair: {len(still)}")
        for team, entry, dist in still[:15]:
            print(f"   {round(dist):>6,} km  {team[:34]:34s} city={entry.get('city')}")
    elif not apply:
        print("dry run — pass --apply to write the cache")
    return 0


if __name__ == "__main__":
    sys.exit(main())
