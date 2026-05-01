"""Tests for the calibration fix in MatchPredictor (Phase 7.2)."""
import numpy as np
import pandas as pd
import pytest

from src.model.catboost_model import MatchPredictor
from src.data.features import FEATURE_COLS


def _make_synthetic_data(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """Synthetic dataset with all 3 outcome classes guaranteed present."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2020-01-01", periods=n, freq="3D")
    feature_vals = rng.normal(0, 1, size=(n, len(FEATURE_COLS)))
    df = pd.DataFrame(feature_vals, columns=FEATURE_COLS)
    df["Date"] = dates
    # Random outcomes covering all 3 classes (seed guarantees this for n>=30)
    df["outcome"] = rng.choice([0, 1, 2], size=n)
    # Ensure all 3 classes present even with small n
    for cls in range(3):
        if cls not in df["outcome"].values:
            df.loc[df.index[cls], "outcome"] = cls
    df["FTR"] = df["outcome"].map({0: "H", 1: "D", 2: "A"})
    df["home_n_matches"] = 10
    df["away_n_matches"] = 10
    df["avg_odds_H"] = 2.5
    df["avg_odds_D"] = 3.3
    df["avg_odds_A"] = 3.0
    return df


def test_calibrated_proba_sums_to_one():
    """Calibrated predict_proba rows must sum to 1 ± 1e-6."""
    pytest.importorskip("catboost")
    data = _make_synthetic_data(300)
    train = data.iloc[:240]
    test = data.iloc[240:]

    model = MatchPredictor(backend="catboost", calibrate=True)
    model.fit(train)
    probs = model.predict_proba(test)

    row_sums = probs.sum(axis=1)
    assert (abs(row_sums - 1.0) < 1e-6).all(), f"Row sums not 1: min={row_sums.min():.8f} max={row_sums.max():.8f}"


def test_uncalibrated_proba_sums_to_one():
    """Uncalibrated predict_proba rows must also sum to 1 ± 1e-6."""
    pytest.importorskip("catboost")
    data = _make_synthetic_data(200)
    train = data.iloc[:160]
    test = data.iloc[160:]

    model = MatchPredictor(backend="catboost", calibrate=False)
    model.fit(train)
    probs = model.predict_proba(test)

    row_sums = probs.sum(axis=1)
    assert (abs(row_sums - 1.0) < 1e-6).all()


def test_calibrated_brier_not_worse_than_uncalibrated():
    """
    On synthetic data, calibrated Brier should be ≤ uncalibrated + 0.01.
    Allows variance — the main guarantee is calibration doesn't catastrophically hurt accuracy.
    """
    pytest.importorskip("catboost")
    from src.model.reliability import brier_score

    data = _make_synthetic_data(400)
    train = data.iloc[:320]
    test = data.iloc[320:]
    outcomes = test["outcome"]

    cal_model = MatchPredictor(backend="catboost", calibrate=True)
    cal_model.fit(train)
    cal_probs = cal_model.predict_proba(test)
    cal_brier = brier_score(cal_probs, outcomes)

    uncal_model = MatchPredictor(backend="catboost", calibrate=False)
    uncal_model.fit(train)
    uncal_probs = uncal_model.predict_proba(test)
    uncal_brier = brier_score(uncal_probs, outcomes)

    assert cal_brier <= uncal_brier + 0.01, (
        f"Calibrated Brier ({cal_brier:.4f}) worse than uncalibrated ({uncal_brier:.4f}) by > 0.01"
    )


def test_calibration_bug_fixed():
    """grep-equivalent: calibrate flag must actually engage calibrators for CatBoost."""
    pytest.importorskip("catboost")
    from src.model.catboost_model import CATBOOST_AVAILABLE

    data = _make_synthetic_data(200)
    model = MatchPredictor(backend="catboost", calibrate=True)
    model.fit(data)

    if CATBOOST_AVAILABLE:
        assert model._calibrators is not None, (
            "CatBoost + calibrate=True should set _calibrators; calibration bug may still be present"
        )
