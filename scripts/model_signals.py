"""
Pre-compute CatBoost model signals for all supported football leagues.
Run this before scanning to populate logs/model_signals.json.

Usage:
    python3 scripts/model_signals.py
    python3 scripts/model_signals.py --league soccer_epl   # single league
"""

import sys
import json
import argparse
import warnings
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

from src.data.downloader import LEAGUES, download_league
from src.data.loader import load_league
from src.data.features import build_feature_matrix, FEATURE_COLS
from src.data.understat import UNDERSTAT_LEAGUES, download_xg, load_xg
from src.ratings.pi_ratings import PiRatings, build_rolling_ratings
from src.model.catboost_model import MatchPredictor

TRAIN_WINDOW = 3
OUTPUT = ROOT / "logs" / "model_signals.json"
OUTPUT_CALIBRATED = ROOT / "logs" / "model_signals_calibrated.json"


def extract_team_states(matches_with_features: pd.DataFrame) -> dict:
    """For each team, extract rolling stats from their most recent completed match."""
    states = {}
    for _, row in matches_with_features.sort_values("Date").iterrows():
        for prefix, team in [("home", row["HomeTeam"]), ("away", row["AwayTeam"])]:
            states[team] = {
                "gf_5":      row.get(f"{prefix}_gf_5"),
                "ga_5":      row.get(f"{prefix}_ga_5"),
                "xgf_5":     row.get(f"{prefix}_xgf_5"),
                "xga_5":     row.get(f"{prefix}_xga_5"),
                "pts_5":     row.get(f"{prefix}_pts_5"),
                "gf_10":     row.get(f"{prefix}_gf_10"),
                "ga_10":     row.get(f"{prefix}_ga_10"),
                "xgf_10":    row.get(f"{prefix}_xgf_10"),
                "xga_10":    row.get(f"{prefix}_xga_10"),
                "pts_10":    row.get(f"{prefix}_pts_10"),
                "n_matches": row.get(f"{prefix}_n_matches"),
            }
    return states


def build_fixture_features(home: str, away: str, pi: PiRatings, states: dict) -> dict | None:
    """Construct feature vector for a future fixture from current team states."""
    if home not in states or away not in states:
        return None
    h = states[home]
    a = states[away]

    def diff(hv, av):
        return (hv - av) if hv is not None and av is not None else None

    return {
        **pi.get_features(home, away),
        "home_gf_5": h["gf_5"],   "home_ga_5":  h["ga_5"],
        "away_gf_5": a["gf_5"],   "away_ga_5":  a["ga_5"],
        "home_gf_10": h["gf_10"], "home_ga_10": h["ga_10"],
        "away_gf_10": a["gf_10"], "away_ga_10": a["ga_10"],
        "home_xgf_5":  h["xgf_5"],  "home_xga_5":  h["xga_5"],
        "away_xgf_5":  a["xgf_5"],  "away_xga_5":  a["xga_5"],
        "home_xgf_10": h["xgf_10"], "home_xga_10": h["xga_10"],
        "away_xgf_10": a["xgf_10"], "away_xga_10": a["xga_10"],
        "home_pts_5": h["pts_5"], "away_pts_5": a["pts_5"],
        "home_pts_10": h["pts_10"], "away_pts_10": a["pts_10"],
        "diff_gf_5":   diff(h["gf_5"],   a["gf_5"]),
        "diff_ga_5":   diff(h["ga_5"],   a["ga_5"]),
        "diff_xgf_5":  diff(h["xgf_5"],  a["xgf_5"]),
        "diff_xga_5":  diff(h["xga_5"],  a["xga_5"]),
        "diff_pts_5":  diff(h["pts_5"],  a["pts_5"]),
        "diff_gf_10":  diff(h["gf_10"],  a["gf_10"]),
        "diff_xgf_10": diff(h["xgf_10"], a["xgf_10"]),
        "diff_pts_10": diff(h["pts_10"], a["pts_10"]),
        "home_n_matches": h["n_matches"],
        "away_n_matches": a["n_matches"],
    }


def process_league(sport_key: str, existing_signals: dict, calibrate: bool = False) -> int:
    """Train model and generate signals for one league. Returns number of signals added."""
    label = LEAGUES[sport_key]["label"]
    print(f"\n{'='*50}")
    print(f"  {label}")
    print(f"{'='*50}")

    try:
        matches = load_league(sport_key, since="1415")
    except RuntimeError:
        print(f"  No data found — run: python3 -c \"from src.data.downloader import download_league; download_league('{sport_key}')\"")
        return 0

    completed = matches.dropna(subset=["FTHG", "FTAG", "FTR"]).copy()
    if len(completed) < 200:
        print(f"  Not enough data ({len(completed)} matches), skipping")
        return 0

    print(f"  {len(completed)} completed matches across {completed['season'].nunique()} seasons")

    completed = build_rolling_ratings(completed)

    # Load xG for leagues that have Understat coverage
    xg = None
    understat_key = UNDERSTAT_LEAGUES.get(sport_key)
    if understat_key:
        try:
            xg = load_xg(league=understat_key, since_season=2014)
            print(f"  xG: {len(xg)} matches loaded for {understat_key}")
        except FileNotFoundError:
            try:
                print(f"  Downloading xG for {understat_key}...")
                xg = download_xg(league=understat_key)
            except Exception as e:
                print(f"  xG unavailable: {e}")

    completed = build_feature_matrix(completed, xg)

    pi = PiRatings()
    pi.fit(completed.sort_values("Date"))

    seasons = sorted(completed["season"].unique())
    train_seasons = seasons[-(TRAIN_WINDOW + 1):-1]
    if len(train_seasons) < 2:
        print(f"  Not enough seasons to train, skipping")
        return 0

    train_data = completed[completed["season"].isin(train_seasons)]
    print(f"  Training on: {train_seasons}")

    model = MatchPredictor(backend="catboost", calibrate=calibrate)
    model.fit(train_data)

    team_states = extract_team_states(completed)
    latest_season = completed["season"].max()
    teams = sorted(
        set(completed[completed["season"] == latest_season]["HomeTeam"].unique()) |
        set(completed[completed["season"] == latest_season]["AwayTeam"].unique())
    )
    print(f"  {len(teams)} teams in {latest_season} season")

    count = 0
    for home in teams:
        for away in teams:
            if home == away:
                continue
            feats = build_fixture_features(home, away, pi, team_states)
            if feats is None:
                continue
            row_df = pd.DataFrame([feats])
            try:
                preds = model.predict_proba(row_df)
                existing_signals[f"{sport_key}:{home}|{away}"] = {
                    "H": round(float(preds.iloc[0]["home_win"]), 4),
                    "D": round(float(preds.iloc[0]["draw"]), 4),
                    "A": round(float(preds.iloc[0]["away_win"]), 4),
                }
                count += 1
            except Exception:
                pass

    print(f"  Generated {count} signals")
    return count


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", default=None, help="Only process this sport_key (e.g. soccer_epl)")
    parser.add_argument("--download", action="store_true", help="Download missing data first")
    parser.add_argument("--calibrate", action="store_true",
                        help="Use calibrated model; writes to model_signals_calibrated.json (not the live file)")
    args = parser.parse_args()

    if args.download:
        print("Downloading missing league data...")
        for sport_key in LEAGUES:
            if sport_key == "soccer_epl":
                continue
            download_league(sport_key, since="1415")

    output_path = OUTPUT_CALIBRATED if args.calibrate else OUTPUT
    if args.calibrate:
        print("Calibrate mode: writing to model_signals_calibrated.json (live file untouched)")

    # Load existing signals to merge into (preserves other leagues when updating one)
    signals = {}
    if output_path.exists():
        try:
            with open(output_path) as f:
                signals = json.load(f).get("signals", {})
        except Exception:
            pass

    leagues_to_run = [args.league] if args.league else list(LEAGUES.keys())
    total = 0
    for sport_key in leagues_to_run:
        if sport_key not in LEAGUES:
            print(f"Unknown sport key: {sport_key}")
            continue
        # Remove old signals for this league before regenerating
        signals = {k: v for k, v in signals.items() if not k.startswith(f"{sport_key}:")}
        # Also handle old EPL signals (no prefix) for backward compat
        if sport_key == "soccer_epl":
            signals = {k: v for k, v in signals.items() if ":" in k or not k}
        total += process_league(sport_key, signals, calibrate=args.calibrate)

    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w") as f:
        json.dump({"generated_at": datetime.utcnow().isoformat(), "signals": signals}, f, indent=2)

    print(f"\nSaved {len(signals)} total signals to {output_path}")


if __name__ == "__main__":
    main()
