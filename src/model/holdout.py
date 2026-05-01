"""Walk-forward hold-out evaluation for MatchPredictor."""
import warnings
from pathlib import Path

import pandas as pd

from src.data.loader import load_league
from src.data.features import build_feature_matrix
from src.data.understat import UNDERSTAT_LEAGUES, load_xg
from src.ratings.pi_ratings import build_rolling_ratings
from src.model.catboost_model import MatchPredictor
from src.model.calibration import evaluate
from src.model.reliability import brier_score


def rolling_holdout_eval(
    sport_key: str,
    *,
    train_window: int = 3,
    since: str = "1415",
    calibrate: bool = False,
) -> pd.DataFrame:
    """
    Walk-forward eval: for each season s with at least `train_window` prior
    seasons of data, train on the previous `train_window` seasons and evaluate
    on s. Returns one row per (sport_key, test_season) with: n_matches,
    model_rps, bookmaker_rps, rps_gap, model_accuracy, has_edge.

    No data is fetched — all data must already be on disk under data/raw/.
    Raises FileNotFoundError if that's not the case.
    """
    matches = load_league(sport_key, since=since)
    completed = matches.dropna(subset=["FTHG", "FTAG", "FTR"]).copy()

    if len(completed) < 200:
        return pd.DataFrame()

    completed = build_rolling_ratings(completed)

    xg = None
    understat_key = UNDERSTAT_LEAGUES.get(sport_key)
    if understat_key:
        try:
            xg = load_xg(league=understat_key, since_season=2014)
        except FileNotFoundError:
            pass

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        completed = build_feature_matrix(completed, xg)

    seasons = sorted(completed["season"].unique())
    rows = []

    for i, test_season in enumerate(seasons):
        if i < train_window:
            continue
        train_seasons = seasons[i - train_window:i]
        train = completed[completed["season"].isin(train_seasons)]
        test = completed[completed["season"] == test_season]

        if len(test) < 10:
            continue

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = MatchPredictor(backend="catboost", calibrate=calibrate)
                model.fit(train)
                model_probs = model.predict_proba(test)
        except Exception as e:
            print(f"  {sport_key}/{test_season}: training failed — {e}")
            continue

        result = evaluate(model_probs, test)
        outcomes = test["FTR"].map({"H": 0, "D": 1, "A": 2}).dropna()
        bs = brier_score(model_probs.loc[outcomes.index], outcomes)
        rows.append({
            "sport_key": sport_key,
            "test_season": test_season,
            "n_matches": result["n_matches"],
            "model_rps": result["model_rps"],
            "bookmaker_rps": result["bookmaker_rps"],
            "rps_gap": result["rps_vs_bookmaker"],
            "model_accuracy": result["model_accuracy"],
            "has_edge": result["has_edge"],
            "brier": round(bs, 5),
        })

    return pd.DataFrame(rows)
