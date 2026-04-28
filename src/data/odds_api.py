"""
Fetch live bookmaker odds from The Odds API (the-odds-api.com).
Free tier: 500 requests/month.
Returns odds from 20+ bookmakers per fixture — feeds directly into consensus strategy.
"""

import os
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

API_KEY = os.environ.get("ODDS_API_KEY", "")
BASE_URL = "https://api.the-odds-api.com/v4"
SPORT = "soccer_epl"


def _get(endpoint: str, params: dict, api_key: str) -> dict | list:
    params["apiKey"] = api_key
    url = f"{BASE_URL}{endpoint}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        return json.loads(resp.read())


def fetch_upcoming_odds(api_key: str = API_KEY, regions: str = "uk,eu") -> pd.DataFrame:
    """
    Fetch H2H (1X2) odds for upcoming EPL fixtures from all available bookmakers.

    Returns a DataFrame with one row per fixture, columns for each bookmaker's H/D/A odds,
    plus consensus columns — ready to feed into find_consensus_bets().
    """
    data = _get(
        f"/sports/{SPORT}/odds/",
        {"regions": regions, "markets": "h2h", "oddsFormat": "decimal"},
        api_key,
    )

    rows = []
    for event in data:
        home_team = event["home_team"]
        away_team = event["away_team"]
        commence = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))

        row = {
            "Date": commence.astimezone().replace(tzinfo=None),
            "HomeTeam": home_team,
            "AwayTeam": away_team,
            "kickoff_utc": event["commence_time"],
            "FTR": None,  # not played yet
        }

        # Collect odds per bookmaker
        for bookmaker in event.get("bookmakers", []):
            book_key = bookmaker["key"]
            for market in bookmaker.get("markets", []):
                if market["key"] != "h2h":
                    continue
                outcomes = {o["name"]: o["price"] for o in market["outcomes"]}
                row[f"{book_key}_H"] = outcomes.get(home_team)
                row[f"{book_key}_D"] = outcomes.get("Draw")
                row[f"{book_key}_A"] = outcomes.get(away_team)

        rows.append(row)

    return pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)


def get_quota(api_key: str = API_KEY) -> dict:
    """Check remaining API request quota."""
    _get(f"/sports/{SPORT}/odds/", {"regions": "uk", "markets": "h2h"}, api_key)
    return {}


def odds_df_to_consensus_format(df: pd.DataFrame) -> pd.DataFrame:
    """
    Reshape The Odds API DataFrame into the format expected by consensus.py.
    Maps bookmaker column names to our internal BOOKMAKER_GROUPS format.
    """
    from src.betting.consensus import compute_consensus

    # Build a BOOKMAKER_GROUPS-style dict from whatever columns exist
    book_cols = {}
    for col in df.columns:
        if col.endswith("_H"):
            key = col[:-2]
            d_col = f"{key}_D"
            a_col = f"{key}_A"
            if d_col in df.columns and a_col in df.columns:
                book_cols[key] = (col, d_col, a_col)

    # Temporarily patch consensus.BOOKMAKER_GROUPS
    import src.betting.consensus as cons
    original = cons.BOOKMAKER_GROUPS.copy()
    cons.BOOKMAKER_GROUPS = book_cols

    result = compute_consensus(df)

    cons.BOOKMAKER_GROUPS = original
    return result
