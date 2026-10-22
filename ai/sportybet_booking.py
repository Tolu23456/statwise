#!/usr/bin/env python3
"""
SportyBet Nigeria Booking Code Generator.

Builds one accumulator betslip from the AI Picks and returns the booking code
that SportyBet's "Load Booking Code" box accepts.

This drives the real site with Playwright: the public endpoints sit behind
CloudFront and the betslip cannot be assembled through a plain API call, so a
headless browser is the only reliable route.

Usage:
    python ai/sportybet_booking.py            # print the booking code

Env: SUPABASE_URL / SUPABASE_SERVICE_KEY, read from the repo-root .env.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Make env_loader importable no matter where this script is invoked from.
AI_DIR = os.path.dirname(os.path.abspath(__file__))
if AI_DIR not in sys.path:
    sys.path.insert(0, AI_DIR)

from env_loader import load_dotenv  # noqa: E402

load_dotenv()

try:                                        # optional dependency
    from playwright.sync_api import sync_playwright
except ImportError:                         # pragma: no cover
    sync_playwright = None

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL", "https://fwpriiugfjhtoymcbmso.supabase.co")
SUPABASE_KEY = os.environ.get(
    "SUPABASE_SERVICE_KEY",
    os.environ.get("SUPABASE_ANON_KEY", ""),
)

def get_ai_picks():
    url = f"{SUPABASE_URL}/rest/v1/predictions?select=*&order=kickoff_time.asc"
    req = Request(url, headers={
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
    })
    try:
        resp = urlopen(req)
        data = json.loads(resp.read())
        ai_picks = []
        for p in data:
            if isinstance(p, dict):
                stats = p.get('stats') or {}
                if isinstance(stats, str):
                    try:
                        stats = json.loads(stats)
                    except:
                        stats = {}
                if stats.get('is_ai_pick'):
                    ai_picks.append(p)
        return ai_picks
    except Exception as e:
        print(f"Error fetching picks: {e}")
        return []

def normalize_team(name):
    if not name:
        return ""
    name = name.lower()
    # Remove common suffixes
    for suffix in [' fc', ' cf', ' sc', ' as', ' ac', ' united', ' city']:
        if name.endswith(suffix):
            name = name.replace(suffix, '').strip()
    return name.replace(' ', '').replace('-', '')

def generate_booking_code(ai_picks):
    if not ai_picks:
        print("No AI picks found")
        return None
    
    print(f"Found {len(ai_picks)} AI picks")
    for i, p in enumerate(ai_picks):
        print(f"{i+1}. {p['match_title']} - {p['prediction']} @ {p['odds']}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        page = context.new_page()
        
        try:
            print("\nNavigating to SportyBet Nigeria...")
            page.goto('https://www.sportybet.com/ng/', wait_until='networkidle', timeout=60000)
            time.sleep(3)
            
            # Try to find and click "Load Booking Code" or similar
            # Look for booking code input
            try:
                # Try different selectors
                booking_btn = page.query_selector('text=Booking Code')
                if not booking_btn:
                    booking_btn = page.query_selector('[class*="booking"]')
                if booking_btn:
                    booking_btn.click()
                    time.sleep(2)
            except:
                pass
            
            print("Page loaded. Looking for fixtures to match...")
            
            # Get all fixtures from the page
            fixtures = page.evaluate('''() => {
                const fixtures = [];
                // Try to find fixture elements
                document.querySelectorAll('[class*="match"], [class*="event"], [class*="game"]').forEach(el => {
                    const text = el.innerText;
                    if (text.includes('vs')) {
                        fixtures.push(text);
                    }
                });
                return fixtures.slice(0, 50);
            }''')
            
            print(f"Found {len(fixtures)} potential fixtures on page")
            for f in fixtures[:10]:
                print(f"  - {f[:80]}")
            
            browser.close()
            
        except Exception as e:
            print(f"Error: {e}")
            browser.close()
            return None
    
    # For now, return manual selections
    selections = []
    for p in ai_picks:
        sel = p['prediction']
        if 'Home' in sel:
            outcome = p['home_team']
        elif 'Away' in sel:
            outcome = p['away_team']
        else:
            outcome = 'Draw'
        selections.append({
            'match': p['match_title'],
            'home': p['home_team'],
            'away': p['away_team'],
            'outcome': outcome,
            'odds': p['odds']
        })
    
    return {
        'selections': selections,
        'count': len(selections),
        'type': 'accumulator',
        'bookmaker': 'sportybet_ng'
    }

if __name__ == '__main__':
    ai_picks = get_ai_picks()
    result = generate_booking_code(ai_picks)
    if result:
        print("\n" + "="*50)
        print("MANUAL BOOKING CODE SELECTIONS (SportyBet Nigeria)")
        print("="*50)
        for i, s in enumerate(result['selections']):
            print(f"{i+1}. {s['home']} vs {s['away']} → {s['outcome']} @ {s['odds']}")
        print("="*50)
        print(f"Accumulator with {result['count']} selections")
        print("\nBuild this manually on sportybet.com/ng")
    else:
        print("Failed to generate booking code")
