#!/usr/bin/env python3
"""
StatWise — Multi-Sport Dataset Harvester
=========================================
Downloads open historical match datasets for the upcoming sports modules:
tennis, basketball, baseball, volleyball.

Every source is free/open. Each file is written to
    ai/data/<sport>/raw/<name>.csv
plus a manifest at
    ai/data/<sport>/MANIFEST.json
describing rows, columns and provenance.

Usage:
    python ai/gather_sports_data.py            # all sports
    python ai/gather_sports_data.py tennis     # single sport
"""
from __future__ import annotations
import os, sys, json, time, urllib.request, urllib.error, csv, io, logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gather")

ROOT     = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
UA       = "StatWise/1.0 (+https://statwise.app)"
TIMEOUT  = 45


def _http_get(url: str, timeout: int = TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_get_zip(url: str, timeout: int = TIMEOUT) -> bytes:
    return _http_get(url, timeout)


def _write(path: str, data: bytes) -> int:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return len(data)


def _csv_info(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            reader = csv.reader(fh)
            header = next(reader, [])
            rows = sum(1 for _ in reader)
        return {"rows": rows, "columns": header[:40]}
    except Exception as exc:  # pragma: no cover
        return {"rows": 0, "columns": [], "error": str(exc)}


# ───────────────────────────── sources ─────────────────────────────
# Each entry: (label, url, filename, optional post-processor)

TENNIS = [
    # Jeff Sackmann – Tennis Match Charting Project (point-level + match-level)
    ("tennis_mcp_m_matches",   "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master/charting-m-matches.csv",        "m_matches.csv"),
    ("tennis_mcp_w_matches",   "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master/charting-w-matches.csv",        "w_matches.csv"),
    ("tennis_mcp_m_points_2020s", "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master/charting-m-points-2020s.csv", "m_points_2020s.csv"),
    ("tennis_mcp_m_points_2010s", "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master/charting-m-points-2010s.csv", "m_points_2010s.csv"),
    ("tennis_mcp_m_points_to_2009", "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master/charting-m-points-to-2009.csv", "m_points_to_2009.csv"),
    ("tennis_mcp_w_points_2020s", "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master/charting-w-points-2020s.csv", "w_points_2020s.csv"),
    ("tennis_mcp_w_points_2010s", "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master/charting-w-points-2010s.csv", "w_points_2010s.csv"),
    ("tennis_mcp_w_points_to_2009", "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master/charting-w-points-to-2009.csv", "w_points_to_2009.csv"),
    # serve/return/rally aggregates (feature engineering ready)
    ("tennis_mcp_m_stats_serve_basics", "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master/charting-m-stats-ServeBasics.csv", "m_stats_serve_basics.csv"),
    ("tennis_mcp_m_stats_serve_influence", "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master/charting-m-stats-ServeInfluence.csv", "m_stats_serve_influence.csv"),
    ("tennis_mcp_m_stats_rally", "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master/charting-m-stats-Rally.csv", "m_stats_rally.csv"),
    ("tennis_mcp_m_stats_sv_break_total", "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master/charting-m-stats-SvBreakTotal.csv", "m_stats_sv_break_total.csv"),
    ("tennis_mcp_m_stats_net_points", "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master/charting-m-stats-NetPoints.csv", "m_stats_net_points.csv"),
    ("tennis_mcp_m_stats_overview", "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master/charting-m-stats-Overview.csv", "m_stats_overview.csv"),
    ("tennis_mcp_w_stats_serve_basics", "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master/charting-w-stats-ServeBasics.csv", "w_stats_serve_basics.csv"),
    ("tennis_mcp_w_stats_rally", "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master/charting-w-stats-Rally.csv", "w_stats_rally.csv"),
    ("tennis_mcp_w_stats_overview", "https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master/charting-w-stats-Overview.csv", "w_stats_overview.csv"),
    # 538 serve timing
    ("tennis_538_serve_times", "https://raw.githubusercontent.com/fivethirtyeight/data/master/tennis-time/serve_times.csv", "538_serve_times.csv"),
    ("tennis_538_events_time", "https://raw.githubusercontent.com/fivethirtyeight/data/master/tennis-time/events_time.csv", "538_events_time.csv"),
    ("tennis_538_players_time", "https://raw.githubusercontent.com/fivethirtyeight/data/master/tennis-time/players_time.csv", "538_players_time.csv"),
]

BASKETBALL = [
    ("nba_538_elo",        "https://raw.githubusercontent.com/fivethirtyeight/data/master/nba-elo/nbaallelo.csv",                 "nbaallelo.csv"),
    ("nba_538_raptor_team", "https://raw.githubusercontent.com/fivethirtyeight/data/master/nba-raptor/modern_RAPTOR_by_team.csv",  "modern_raptor_by_team.csv"),
    ("nba_538_raptor_player", "https://raw.githubusercontent.com/fivethirtyeight/data/master/nba-raptor/modern_RAPTOR_by_player.csv", "modern_raptor_by_player.csv"),
    ("nba_538_raptor_hist_team", "https://raw.githubusercontent.com/fivethirtyeight/data/master/nba-raptor/historical_RAPTOR_by_team.csv", "historical_raptor_by_team.csv"),
]

BASEBALL = [
    ("mlb_538_elo",       "https://raw.githubusercontent.com/fivethirtyeight/data/master/mlb-elo/mlb_elo.csv",                     "mlb_elo.csv"),
    ("mlb_538_quasi_win_shares", "https://raw.githubusercontent.com/fivethirtyeight/data/master/mlb-quasi-win-shares/quasi_winshares.csv", "quasi_winshares.csv"),
    ("mlb_538_allstar_talent", "https://raw.githubusercontent.com/fivethirtyeight/data/master/mlb-allstar-teams/allstar_player_talent.csv", "allstar_player_talent.csv"),
]

VOLLEYBALL = [
    ("volleybox_ewc_2024", "https://volleybox.net/data/men/EPL", "volleybox_men.html"),
    ("volleybox_w_ewc_2024", "https://volleybox.net/data/women/EPL", "volleybox_women.html"),
    ("volleybox_m_wwc", "https://volleybox.net/data/men/WWC", "volleybox_men_wwc.html"),
    ("volleybox_m_cwc", "https://volleybox.net/data/men/CWC", "volleybox_men_cwc.html"),
]


SPORTS = {
    "tennis":     TENNIS,
    "basketball": BASKETBALL,
    "baseball":   BASEBALL,
    "volleyball": VOLLEYBALL,
}


def harvest(sport: str, sources: list) -> dict:
    out_dir = os.path.join(DATA_DIR, sport, "raw")
    os.makedirs(out_dir, exist_ok=True)
    manifest = {"sport": sport, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "files": []}

    for label, url, fname in sources:
        dest = os.path.join(out_dir, fname)
        if os.path.exists(dest) and os.path.getsize(dest) > 1000:
            info = _csv_info(dest) if fname.endswith(".csv") else {"rows": 0, "columns": [], "bytes": os.path.getsize(dest)}
            manifest["files"].append({"label": label, "url": url, "file": fname, "status": "cached", **info})
            logger.info("%s/%s -> cached", sport, fname)
            continue
        try:
            data = _http_get(url)
            size = _write(dest, data)
            info = _csv_info(dest) if fname.endswith(".csv") else {"rows": 0, "columns": [], "bytes": size}
            manifest["files"].append({"label": label, "url": url, "file": fname, "status": "ok", "bytes": size, **info})
            logger.info("%s/%s -> %s bytes, %s rows", sport, fname, f"{size:,}", info.get("rows"))
        except Exception as exc:
            manifest["files"].append({"label": label, "url": url, "file": fname, "status": "failed", "error": str(exc)})
            logger.warning("%s/%s FAILED: %s", sport, fname, exc)
    mpath = os.path.join(DATA_DIR, sport, "MANIFEST.json")
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return manifest


def main() -> int:
    wanted = sys.argv[1:] or list(SPORTS.keys())
    for sport in wanted:
        if sport not in SPORTS:
            logger.warning("Unknown sport '%s' (known: %s)", sport, ", ".join(SPORTS))
            continue
        logger.info("=== harvesting %s ===", sport)
        try:
            harvest(sport, SPORTS[sport])
        except Exception as exc:
            logger.error("sport %s failed: %s", sport, exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
