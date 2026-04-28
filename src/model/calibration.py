"""
Model calibration and evaluation metrics.

Primary metric: Ranked Probability Score (RPS) — lower is better.
Compare model RPS vs bookmaker implied RPS to measure edge.
"""

import numpy as np
import pandas as pd


def rps(probs: np.ndarray, outcome: int) -> float:
    """
    Ranked Probability Score for a single match.

    Parameters
    ----------
    probs : array of shape (3,) — [P(home), P(draw), P(away)]
    outcome : 0=home win, 1=draw, 2=away win

    Returns
    -------
    RPS score (lower = better, 0 = perfect)
    """
    actual = np.zeros(3)
    actual[outcome] = 1.0
    cum_pred = np.cumsum(probs)
    cum_actual = np.cumsum(actual)
    return float(np.sum((cum_pred - cum_actual) ** 2) / 2)


def rps_batch(probs_df: pd.DataFrame, outcomes: pd.Series) -> pd.Series:
    """
    Compute RPS for each match in a batch.

    Parameters
    ----------
    probs_df : DataFrame with columns [home_win, draw, away_win]
    outcomes : Series of 0/1/2 (home/draw/away)
    """
    scores = []
    for (_, row), outcome in zip(probs_df.iterrows(), outcomes):
        p = np.array([row["home_win"], row["draw"], row["away_win"]])
        scores.append(rps(p, int(outcome)))
    return pd.Series(scores, index=probs_df.index)


def result_to_outcome(ftr: str) -> int:
    """Convert FTR string ('H', 'D', 'A') to outcome int (0, 1, 2)."""
    return {"H": 0, "D": 1, "A": 2}[ftr]


def odds_to_probs(odds_h: float, odds_d: float, odds_a: float) -> np.ndarray:
    """Convert raw bookmaker odds to fair probabilities (margin removed)."""
    raw = np.array([1/odds_h, 1/odds_d, 1/odds_a])
    return raw / raw.sum()


def bookmaker_rps(matches: pd.DataFrame, odds_cols: tuple = ("avg_odds_H", "avg_odds_D", "avg_odds_A")) -> pd.Series:
    """
    Compute RPS for bookmaker average odds as a benchmark.

    matches must have odds columns and FTR column.
    """
    scores = []
    oh, od, oa = odds_cols
    for _, row in matches.iterrows():
        if pd.isna(row.get(oh)) or pd.isna(row.get("FTR")):
            scores.append(np.nan)
            continue
        probs = odds_to_probs(row[oh], row[od], row[oa])
        outcome = result_to_outcome(row["FTR"])
        scores.append(rps(probs, outcome))
    return pd.Series(scores, index=matches.index)


def evaluate(model_probs: pd.DataFrame, matches: pd.DataFrame) -> dict:
    """
    Full evaluation: model RPS vs bookmaker RPS.

    Parameters
    ----------
    model_probs : DataFrame with [home_win, draw, away_win] columns
    matches : original matches DataFrame with FTR and odds columns

    Returns
    -------
    dict with summary stats
    """
    outcomes = matches["FTR"].map({"H": 0, "D": 1, "A": 2})
    model_scores = rps_batch(model_probs, outcomes)
    book_scores = bookmaker_rps(matches)

    valid = ~(model_scores.isna() | book_scores.isna())

    model_mean = model_scores[valid].mean()
    book_mean = book_scores[valid].mean()

    # Accuracy
    model_pred = model_probs[["home_win", "draw", "away_win"]].idxmax(axis=1).map(
        {"home_win": "H", "draw": "D", "away_win": "A"}
    ).values
    accuracy = (model_pred == matches["FTR"].values).mean()

    return {
        "n_matches": valid.sum(),
        "model_rps": round(model_mean, 5),
        "bookmaker_rps": round(book_mean, 5),
        "rps_vs_bookmaker": round(model_mean - book_mean, 5),
        "model_accuracy": round(accuracy, 4),
        "has_edge": model_mean < book_mean,
    }
