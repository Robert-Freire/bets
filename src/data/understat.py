"""
Fetch match-level xG data from Understat for supported leagues.
Uses the understat Python package (async, backed by Understat's internal API).

Seasons: 2014 = 2014/15, 2015 = 2015/16, ..., 2024 = 2024/25
"""

import asyncio
from pathlib import Path

import aiohttp
import pandas as pd
import understat as understat_lib

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "raw" / "xg"

# Understat uses the start year of the season (2023 = 2023/24)
UNDERSTAT_SEASONS = list(range(2014, 2025))  # 2014/15 through 2024/25

# Leagues with xG data on Understat, mapped from scanner sport_key
UNDERSTAT_LEAGUES = {
    "soccer_epl":               "EPL",
    "soccer_germany_bundesliga":"Bundesliga",
    "soccer_italy_serie_a":     "Serie_A",
    "soccer_france_ligue_one":  "Ligue_1",
}


async def _fetch_season(session: aiohttp.ClientSession, league: str, season: int) -> list[dict]:
    u = understat_lib.Understat(session)
    return await u.get_league_results(league, season)


async def _download_seasons(league: str, seasons: list[int]) -> dict[int, list[dict]]:
    results = {}
    async with aiohttp.ClientSession() as session:
        for season in seasons:
            print(f"  [{league}] {season}/{str(season+1)[-2:]} ... ", end="", flush=True)
            try:
                data = await _fetch_season(session, league, season)
                results[season] = data
                print(f"{len(data)} matches")
            except Exception as e:
                print(f"ERROR: {e}")
            await asyncio.sleep(0.5)
    return results


def _parse_matches(raw: list[dict], season: int) -> pd.DataFrame:
    rows = []
    for m in raw:
        if not m.get("isResult"):
            continue
        rows.append({
            "understat_id": m["id"],
            "season": season,
            "date": pd.to_datetime(m["datetime"]),
            "home_team": m["h"]["title"],
            "away_team": m["a"]["title"],
            "home_goals": int(m["goals"]["h"]),
            "away_goals": int(m["goals"]["a"]),
            "home_xg": float(m["xG"]["h"]),
            "away_xg": float(m["xG"]["a"]),
        })
    return pd.DataFrame(rows)


def download_xg(league: str = "EPL", seasons: list[int] | None = None, force: bool = False) -> pd.DataFrame:
    """
    Download xG data for the given league and seasons, save to CSV.

    Parameters
    ----------
    league   : Understat league key (e.g. "EPL", "Bundesliga", "Serie_A", "Ligue_1")
    seasons  : list of start years (e.g. [2022, 2023, 2024]). Defaults to all.
    force    : re-download even if cache exists.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if seasons is None:
        seasons = UNDERSTAT_SEASONS

    to_fetch, cached = [], []
    for s in seasons:
        path = DATA_DIR / f"xg_{league}_{s}.csv"
        (cached if (path.exists() and not force) else to_fetch).append(s)

    all_frames = [pd.read_csv(DATA_DIR / f"xg_{league}_{s}.csv", parse_dates=["date"]) for s in cached]

    if to_fetch:
        print(f"Downloading xG for {len(to_fetch)} seasons ({league}) from Understat...")
        raw = asyncio.run(_download_seasons(league, to_fetch))
        for season, data in raw.items():
            df = _parse_matches(data, season)
            df.to_csv(DATA_DIR / f"xg_{league}_{season}.csv", index=False)
            all_frames.append(df)

    if not all_frames:
        return pd.DataFrame()
    return pd.concat(all_frames, ignore_index=True).sort_values("date").reset_index(drop=True)


def load_xg(league: str = "EPL", since_season: int = 2014) -> pd.DataFrame:
    """Load cached xG data from disk for a given league."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    frames = []
    for path in sorted(DATA_DIR.glob(f"xg_{league}_*.csv")):
        season = int(path.stem.split("_")[-1])
        if season >= since_season:
            frames.append(pd.read_csv(path, parse_dates=["date"]))
    if not frames:
        raise FileNotFoundError(f"No xG data for {league}. Run download_xg('{league}') first.")
    return pd.concat(frames, ignore_index=True).sort_values("date").reset_index(drop=True)


if __name__ == "__main__":
    for league in UNDERSTAT_LEAGUES.values():
        df = download_xg(league)
        print(f"{league}: {len(df)} matches with xG\n")
