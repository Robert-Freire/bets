"""Load and clean football-data.co.uk CSVs into a unified DataFrame."""

import pandas as pd
from pathlib import Path
from src.data.downloader import DATA_DIR, SEASONS, season_label

REQUIRED_COLS = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"]

# Bookmaker odds columns we care about (best odds across books)
ODDS_COLS_HOME = ["B365H", "BWH", "IWH", "PSH", "WHH", "VCH"]
ODDS_COLS_DRAW = ["B365D", "BWD", "IWD", "PSD", "WHD", "VCD"]
ODDS_COLS_AWAY = ["B365A", "BWA", "IWA", "PSA", "WHA", "VCA"]


def load_season(season: str) -> pd.DataFrame:
    path = DATA_DIR / f"E0_{season}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Season {season} not downloaded: {path}")

    df = pd.read_csv(path, encoding="latin-1", low_memory=False)
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Season {season} missing columns: {missing}")

    df = df[df["HomeTeam"].notna() & df["AwayTeam"].notna()].copy()
    df["season"] = season
    df["season_label"] = season_label(season)
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["Date", "FTHG", "FTAG"])
    df["FTHG"] = df["FTHG"].astype(int)
    df["FTAG"] = df["FTAG"].astype(int)

    # Best available odds across bookmakers
    home_cols = [c for c in ODDS_COLS_HOME if c in df.columns]
    draw_cols = [c for c in ODDS_COLS_DRAW if c in df.columns]
    away_cols = [c for c in ODDS_COLS_AWAY if c in df.columns]

    if home_cols:
        df["best_odds_H"] = df[home_cols].max(axis=1)
        df["best_odds_D"] = df[draw_cols].max(axis=1)
        df["best_odds_A"] = df[away_cols].max(axis=1)
        df["avg_odds_H"] = df[home_cols].mean(axis=1)
        df["avg_odds_D"] = df[draw_cols].mean(axis=1)
        df["avg_odds_A"] = df[away_cols].mean(axis=1)

    return df.sort_values("Date").reset_index(drop=True)


def load_all(since: str = "1415") -> pd.DataFrame:
    """Load all seasons from `since` onward into a single DataFrame."""
    idx = SEASONS.index(since)
    frames = []
    for season in SEASONS[idx:]:
        try:
            frames.append(load_season(season))
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"WARNING: skipping {season}: {e}")
    if not frames:
        raise RuntimeError("No data loaded. Run downloader first.")
    df = pd.concat(frames, ignore_index=True)
    return df.sort_values("Date").reset_index(drop=True)


def load_league(sport_key: str, since: str = "1415") -> pd.DataFrame:
    """
    Load historical match data for any supported league.
    For EPL, equivalent to load_all(since). For others, loads {fd_code}_{season}.csv files.
    """
    from src.data.downloader import LEAGUES

    league = LEAGUES[sport_key]
    fd_code = league["fd_code"]

    if sport_key == "soccer_epl":
        return load_all(since=since)

    idx = SEASONS.index(since)
    frames = []
    for season in SEASONS[idx:]:
        path = DATA_DIR / f"{fd_code}_{season}.csv"
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, encoding="latin-1", low_memory=False)
            missing = [c for c in REQUIRED_COLS if c not in df.columns]
            if missing:
                continue
            df = df[df["HomeTeam"].notna() & df["AwayTeam"].notna()].copy()
            df["season"] = season
            df["season_label"] = season_label(season)
            df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
            df = df.dropna(subset=["Date", "FTHG", "FTAG"])
            df["FTHG"] = df["FTHG"].astype(int)
            df["FTAG"] = df["FTAG"].astype(int)

            home_cols = [c for c in ODDS_COLS_HOME if c in df.columns]
            draw_cols = [c for c in ODDS_COLS_DRAW if c in df.columns]
            away_cols = [c for c in ODDS_COLS_AWAY if c in df.columns]
            if home_cols:
                df["best_odds_H"] = df[home_cols].max(axis=1)
                df["best_odds_D"] = df[draw_cols].max(axis=1)
                df["best_odds_A"] = df[away_cols].max(axis=1)
                df["avg_odds_H"] = df[home_cols].mean(axis=1)
                df["avg_odds_D"] = df[draw_cols].mean(axis=1)
                df["avg_odds_A"] = df[away_cols].mean(axis=1)

            frames.append(df.sort_values("Date").reset_index(drop=True))
        except Exception as e:
            print(f"  WARNING: skipping {fd_code}_{season}: {e}")

    if not frames:
        raise RuntimeError(f"No data for {sport_key}. Run downloader first.")
    return pd.concat(frames, ignore_index=True).sort_values("Date").reset_index(drop=True)
