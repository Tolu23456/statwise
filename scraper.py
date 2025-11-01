import os
import requests
from supabase import create_client, Client
from datetime import datetime, timezone

# Environment variables from GitHub Secrets
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_API_KEY")  # Service role key
FOOTBALL_DATA_TOKEN = os.environ.get("FOOTBALL_DATA_TOKEN")

if not SUPABASE_URL or not SUPABASE_KEY or not FOOTBALL_DATA_TOKEN:
    raise Exception("Missing environment variables. Check GitHub Secrets or .env file.")

# Initialize Supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# 12 Competitions
LEAGUES = {
    "WC": "FIFA World Cup",
    "CL": "UEFA Champions League",
    "BL1": "Bundesliga",
    "DED": "Eredivisie",
    "BSA": "Campeonato Brasileiro Série A",
    "PD": "Primera Division",
    "FL1": "Ligue 1",
    "ELC": "Championship",
    "PPL": "Primeira Liga",
    "EC": "European Championship",
    "SA": "Serie A",
    "PL": "Premier League"
}

FOOTBALL_API_URL = "https://api.football-data.org/v4/competitions/{league}/matches"

def fetch_upcoming_matches(league_code):
    """Fetch upcoming matches for a given league"""
    headers = {"X-Auth-Token": FOOTBALL_DATA_TOKEN}
    response = requests.get(FOOTBALL_API_URL.format(league=league_code), headers=headers)
    if response.status_code != 200:
        print(f"⚠️ Failed to fetch {league_code}: {response.status_code}")
        return []

    data = response.json()
    upcoming_matches = []
    for match in data.get("matches", []):
        kickoff = datetime.fromisoformat(match["utcDate"].replace("Z", "+00:00"))
        if kickoff <= datetime.now(timezone.utc):
            continue  # Only upcoming matches

        home_team = match["homeTeam"]["name"] if match.get("homeTeam") else None
        away_team = match["awayTeam"]["name"] if match.get("awayTeam") else None
        if not home_team or not away_team:
            continue

        upcoming_matches.append({
            "external_match_id": str(match.get("id")),
            "home_team": home_team,
            "away_team": away_team,
            "league": league_code,
            "kickoff_time": kickoff.isoformat(),
            "status": match.get("status", "SCHEDULED"),
            "home_score": None,
            "away_score": None,
            "statistics": {},
            "odds": {},
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        })
    print(f"✅ Fetched {len(upcoming_matches)} upcoming matches from {league_code}")
    return upcoming_matches

def upsert_matches(matches):
    """Upsert matches into Supabase"""
    count_upserted = 0
    for match in matches:
        try:
            supabase.table("matches").upsert(match, on_conflict="external_match_id").execute()
            count_upserted += 1
        except Exception as e:
            print(f"❌ Failed to upsert match {match['external_match_id']}: {e}")
    print(f"✅ Successfully upserted {count_upserted} matches.")

def main():
    all_matches = []
    for code in LEAGUES:
        matches = fetch_upcoming_matches(code)
        all_matches.extend(matches)

    upsert_matches(all_matches)
    print("🎯 Scraper run completed!")

if __name__ == "__main__":
    main()
