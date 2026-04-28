"""
End-to-end pipeline: download data → fit model → evaluate → find value bets.

Two strategies:
  1. Statistical model (Dixon-Coles / XGBoost) — predict true probabilities
  2. Consensus strategy (Kaunitz 2017) — bet when one bookmaker deviates from market

Usage:
    python3 main.py
"""

import sys
import warnings
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
warnings.filterwarnings("ignore")

from src.data.downloader import download_all
from src.data.loader import load_all
from src.data.understat import download_xg, load_xg
from src.data.features import build_feature_matrix
from src.ratings.pi_ratings import build_rolling_ratings
from src.model.dixon_coles import DixonColesModel
from src.model.xgboost_model import MatchPredictor
from src.model.calibration import evaluate
from src.betting.value import find_value_bets
from src.betting.kelly import size_bets, simulate_bankroll
from src.betting.consensus import compute_consensus, backtest_consensus, find_consensus_bets


def run_pipeline(since_season: str = "1415", bankroll: float = 1000.0):
    print("=== Premier League Value Betting Pipeline ===\n")

    # 1. Download data
    print("Step 1: Downloading data...")
    download_all(since=since_season)
    download_xg()

    # 2. Load + build features
    print("\nStep 2: Loading and building features...")
    matches = load_all(since=since_season)
    xg = load_xg(since_season=2014)
    matches = build_rolling_ratings(matches)
    matches = build_feature_matrix(matches, xg)
    print(f"  {len(matches):,} matches, {matches['season'].nunique()} seasons")

    # ── Strategy 1: Kaunitz Consensus ──────────────────────────────────────
    print("\n" + "="*55)
    print("STRATEGY 1: Kaunitz Consensus (bookmaker deviation)")
    print("="*55)
    matches = compute_consensus(matches)

    print(f"\n{'Edge':>8}  {'Bets':>6}  {'Win%':>6}  {'ROI':>8}  {'P&L':>8}")
    print("-" * 42)
    for edge in [0.01, 0.02, 0.03, 0.04, 0.05]:
        r = backtest_consensus(matches, min_edge=edge, bankroll=bankroll)
        if r["n_bets"] > 0:
            print(f"{edge:>8.0%}  {r['n_bets']:>6}  {r['win_rate']:>5.1%}  "
                  f"{r['roi']:>+8.2%}  {r['total_pnl']:>+8.0f}")

    # Best threshold detail
    best = find_consensus_bets(matches, min_edge=0.02)
    if not best.empty:
        print(f"\nTop 5 consensus bets found (edge >= 2%):")
        print(best[["date", "home_team", "away_team", "bookmaker", "bet_side",
                     "book_odds", "consensus_prob", "edge", "result"]].head(5).to_string(index=False))

    # ── Strategy 2: XGBoost Walk-Forward ───────────────────────────────────
    print("\n" + "="*55)
    print("STRATEGY 2: XGBoost + xG features (walk-forward)")
    print("="*55)

    seasons = sorted(matches["season"].unique())
    TRAIN_WINDOW = 3
    all_preds, all_tests = [], []

    for i, test_season in enumerate(seasons[TRAIN_WINDOW:], start=TRAIN_WINDOW):
        train_seasons = seasons[max(0, i - TRAIN_WINDOW):i]
        train = matches[matches["season"].isin(train_seasons)].copy()
        test = matches[matches["season"] == test_season].copy()

        test_valid = test[
            (test["home_n_matches"] >= 3) &
            (test["away_n_matches"] >= 3) &
            test["outcome"].notna()
        ]
        if len(test_valid) < 20:
            continue

        model = MatchPredictor(backend="catboost", calibrate=False)
        model.fit(train)
        preds = model.predict_proba(test_valid)
        all_preds.append(preds)
        all_tests.append(test_valid)

    if all_preds:
        all_preds_df = pd.concat(all_preds, ignore_index=True)
        all_test_df = pd.concat(all_tests, ignore_index=True)

        print("\nEvaluation vs bookmaker:")
        result = evaluate(all_preds_df, all_test_df)
        for k, v in result.items():
            print(f"  {k}: {v}")

        print("\nValue bets at different thresholds:")
        print(f"{'Edge':>8}  {'Bets':>6}  {'ROI':>8}")
        for edge in [0.03, 0.05, 0.07]:
            vb = find_value_bets(all_preds_df, all_test_df, min_edge=edge)
            if not vb.empty:
                sized = size_bets(vb, bankroll=bankroll)
                pnl = sized.apply(
                    lambda r: r["bet_amount"] * (r["best_odds"] - 1)
                    if r["result"] == r["bet_side"] else -r["bet_amount"],
                    axis=1,
                ).sum()
                roi = pnl / sized["bet_amount"].sum() if sized["bet_amount"].sum() > 0 else 0
                print(f"{edge:>8.0%}  {len(vb):>6}  {roi:>+8.2%}")

        print("\nTop feature importances:")
        try:
            model = MatchPredictor(backend="catboost", calibrate=False)
            model.fit(all_test_df)
            fi = model.feature_importance()
            print(fi.head(10).to_string(index=False))
        except Exception:
            pass

    print("\n=== Done ===")


if __name__ == "__main__":
    run_pipeline()
