"""
Phase 7.0: Sanity baseline — reproduce headline numbers from CLAUDE.md.
Trains on EPL seasons [1920, 2021, 2122], evaluates on 2223.
"""
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

from src.data.loader import load_all
from src.data.features import build_feature_matrix
from src.data.understat import load_xg
from src.ratings.pi_ratings import build_rolling_ratings
from src.model.catboost_model import MatchPredictor
from src.model.calibration import evaluate

TRAIN_SEASONS = ["1920", "2021", "2122"]
TEST_SEASON = "2223"


def main():
    print("Loading EPL data...")
    matches = load_all(since="1415")
    completed = matches.dropna(subset=["FTHG", "FTAG", "FTR"]).copy()
    print(f"  {len(completed)} completed matches")

    print("Building pi-ratings...")
    completed = build_rolling_ratings(completed)

    print("Loading xG...")
    try:
        xg = load_xg(league="EPL", since_season=2014)
        print(f"  {len(xg)} xG matches loaded")
    except FileNotFoundError:
        print("  xG not available — proceeding without")
        xg = None

    print("Building feature matrix...")
    completed = build_feature_matrix(completed, xg)

    train = completed[completed["season"].isin(TRAIN_SEASONS)]
    test = completed[completed["season"] == TEST_SEASON]
    print(f"  Train: {len(train)} matches ({TRAIN_SEASONS})")
    print(f"  Test:  {len(test)} matches ({TEST_SEASON})")

    if len(test) == 0:
        print(f"ERROR: No test data for season {TEST_SEASON}")
        sys.exit(1)

    print("Fitting CatBoost (calibrate=False)...")
    model = MatchPredictor(backend="catboost", calibrate=False)
    model.fit(train)

    print("Evaluating...")
    model_probs = model.predict_proba(test)
    results = evaluate(model_probs, test)

    print("\n=== Baseline Results (7.0) ===")
    for k, v in results.items():
        print(f"  {k}: {v}")

    gap = results["rps_vs_bookmaker"]
    if not (0.005 <= gap <= 0.030):
        print(f"\nWARNING: rps_vs_bookmaker={gap:.5f} is outside expected range [0.005, 0.030]")
        print("Something may have shifted in the data pipeline — review before proceeding!")
    else:
        print(f"\nBaseline check OK: rps gap {gap:.5f} in expected range [0.005, 0.030]")

    return results


if __name__ == "__main__":
    main()
