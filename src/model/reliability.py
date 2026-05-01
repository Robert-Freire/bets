"""Reliability (calibration) curve and Brier score utilities."""

import numpy as np
import pandas as pd


def reliability_curve(
    probs: pd.Series,
    outcomes: pd.Series,
    n_bins: int = 10,
) -> pd.DataFrame:
    """
    Compute reliability curve for one outcome class.

    Parameters
    ----------
    probs    : predicted probability for the class (one column from predict_proba)
    outcomes : binary series (1 if this class occurred, 0 otherwise)
    n_bins   : number of equal-width bins

    Returns
    -------
    DataFrame with columns: bin_lo, bin_hi, mean_pred, empirical_freq, count
    """
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    rows = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (probs >= lo) & (probs < hi)
        count = mask.sum()
        if count == 0:
            rows.append({"bin_lo": lo, "bin_hi": hi, "mean_pred": np.nan, "empirical_freq": np.nan, "count": 0})
        else:
            rows.append({
                "bin_lo": round(lo, 4),
                "bin_hi": round(hi, 4),
                "mean_pred": round(float(probs[mask].mean()), 4),
                "empirical_freq": round(float(outcomes[mask].mean()), 4),
                "count": int(count),
            })
    return pd.DataFrame(rows)


def brier_score(probs_df: pd.DataFrame, outcomes: pd.Series) -> float:
    """
    Multiclass Brier score: mean sum of squared errors across all three outcome columns.

    Parameters
    ----------
    probs_df : DataFrame with columns [home_win, draw, away_win]
    outcomes : Series of 0/1/2 (home/draw/away)

    Returns
    -------
    float (lower is better, 0 = perfect)
    """
    p = probs_df[["home_win", "draw", "away_win"]].values
    n = len(outcomes)
    one_hot = np.zeros_like(p)
    for i, o in enumerate(outcomes):
        one_hot[i, int(o)] = 1.0
    return float(np.mean(np.sum((p - one_hot) ** 2, axis=1)))


def calibration_verdict(
    curve_df: pd.DataFrame,
    max_abs_deviation: float = 0.04,
    min_count: int = 30,
) -> str:
    """
    Return 'WELL_CALIBRATED', 'MISCALIBRATED', or 'INSUFFICIENT_DATA'.

    A league is WELL_CALIBRATED if the max absolute deviation between
    mean_pred and empirical_freq is < max_abs_deviation in bins with count >= min_count.
    """
    qualified = curve_df[(curve_df["count"] >= min_count) & curve_df["mean_pred"].notna()]
    if len(qualified) == 0:
        return "INSUFFICIENT_DATA"
    max_dev = (qualified["mean_pred"] - qualified["empirical_freq"]).abs().max()
    return "WELL_CALIBRATED" if max_dev < max_abs_deviation else "MISCALIBRATED"
