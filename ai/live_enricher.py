#!/usr/bin/env python3
"""Live match enrichment from ESPN."""
import requests
import json
from datetime import datetime

def fetch_live_scores():
    try:
        url = "https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            events = data.get('events', [])
            results = []
            for ev in events:
                comp = ev.get('competitions', [{}])[0]
                status = comp.get('status', {})
                teams = comp.get('competitors', [])
                home = away = None
                for t in teams:
                    if t.get('homeAway') == 'home':
                        home = {'name': t['team']['name'], 'score': t.get('score')}
                    else:
                        away = {'name': t['team']['name'], 'score': t.get('score')}
                results.append({
                    'id': ev.get('id'),
                    'name': ev.get('name'),
                    'status': status.get('type', {}).get('name'),
                    'minute': status.get('displayClock'),
                    'home': home,
                    'away': away
                })
            return results
    except Exception as e:
        print(f"Live fetch error: {e}")
    return []

if __name__ == '__main__':
    print(json.dumps(fetch_live_scores(), indent=2))
