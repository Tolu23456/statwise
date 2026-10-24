#!/usr/bin/env python3
"""Fetch weather data for matches using Open-Meteo API."""
import requests
import pandas as pd
from datetime import datetime
import time

# Common stadium coordinates (expand as needed)
VENUE_COORDS = {
    "default": (51.5, -0.1),
}

def get_weather(lat, lon, date_str):
    try:
        dt = pd.to_datetime(date_str)
        url = "https://archive-api.open-meteo.com/v1/archive"
        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": dt.strftime("%Y-%m-%d"),
            "end_date": dt.strftime("%Y-%m-%d"),
            "hourly": "temperature_2m,precipitation,windspeed_10m,weathercode",
            "timezone": "UTC"
        }
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            hourly = data.get('hourly', {})
            if hourly.get('temperature_2m'):
                # Use first/closest hour
                return {
                    'temp_c': hourly['temperature_2m'][0],
                    'precip_mm': hourly.get('precipitation', [0])[0],
                    'wind_kmh': hourly.get('windspeed_10m', [0])[0],
                    'weather_code': hourly.get('weathercode', [0])[0]
                }
    except Exception as e:
        print(f"Weather fetch error: {e}")
    return None

def enrich_df_with_weather(df):
    df = df.copy()
    for idx, row in df.iterrows():
        if pd.isna(row.get('temp_c')):
            w = get_weather(51.5, -0.1, row['date'])
            if w:
                df.at[idx, 'temp_c'] = w['temp_c']
                df.at[idx, 'precip_mm'] = w['precip_mm']
                df.at[idx, 'wind_kmh'] = w['wind_kmh']
                df.at[idx, 'weather_code'] = w['weather_code']
            time.sleep(0.1)  # rate limit
    return df
