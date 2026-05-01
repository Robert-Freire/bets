"""Tests for src/model/reliability.py (Phase 7.3)."""
import numpy as np
import pandas as pd
import pytest

from src.model.reliability import reliability_curve, brier_score, calibration_verdict


def test_reliability_curve_perfect_calibration():
    """A perfectly-calibrated input should produce near-zero deviation."""
    n = 1000
    rng = np.random.default_rng(0)
    probs = pd.Series(rng.uniform(0, 1, n))
    # Outcomes drawn from the predicted probabilities → perfect calibration
    outcomes = pd.Series((rng.uniform(0, 1, n) < probs).astype(float))

    curve = reliability_curve(probs, outcomes, n_bins=5)
    qualified = curve[curve["count"] >= 10].dropna()
    assert len(qualified) > 0
    max_dev = (qualified["mean_pred"] - qualified["empirical_freq"]).abs().max()
    assert max_dev < 0.15, f"Expected near-zero deviation for perfect calibration, got {max_dev:.4f}"


def test_reliability_curve_miscalibrated():
    """A deliberately-miscalibrated input should produce large deviation."""
    n = 500
    # Model always predicts 0.5 but truth is always 0
    probs = pd.Series(np.full(n, 0.5))
    outcomes = pd.Series(np.zeros(n))

    curve = reliability_curve(probs, outcomes, n_bins=10)
    qualified = curve[curve["count"] > 0].dropna()
    max_dev = (qualified["mean_pred"] - qualified["empirical_freq"]).abs().max()
    assert max_dev >= 0.1, f"Expected large deviation for miscalibrated input, got {max_dev:.4f}"


def test_brier_score_perfect():
    """Perfect predictions should yield Brier score of 0."""
    probs_df = pd.DataFrame({
        "home_win": [1.0, 0.0, 0.0],
        "draw":     [0.0, 1.0, 0.0],
        "away_win": [0.0, 0.0, 1.0],
    })
    outcomes = pd.Series([0, 1, 2])
    assert brier_score(probs_df, outcomes) == pytest.approx(0.0, abs=1e-9)


def test_brier_score_worst():
    """Opposite-of-truth predictions should yield maximum Brier score (= 2)."""
    probs_df = pd.DataFrame({
        "home_win": [0.0, 0.0, 1.0],
        "draw":     [0.0, 0.0, 0.0],
        "away_win": [1.0, 1.0, 0.0],
    })
    outcomes = pd.Series([0, 1, 2])
    assert brier_score(probs_df, outcomes) == pytest.approx(2.0, abs=1e-9)


def test_calibration_verdict_well():
    """Curve with small deviation → WELL_CALIBRATED."""
    curve = pd.DataFrame({
        "bin_lo": [0.0, 0.1],
        "bin_hi": [0.1, 0.2],
        "mean_pred": [0.05, 0.15],
        "empirical_freq": [0.05, 0.14],
        "count": [50, 50],
    })
    assert calibration_verdict(curve) == "WELL_CALIBRATED"


def test_calibration_verdict_miscalibrated():
    """Curve with large deviation → MISCALIBRATED."""
    curve = pd.DataFrame({
        "bin_lo": [0.0, 0.1],
        "bin_hi": [0.1, 0.2],
        "mean_pred": [0.05, 0.15],
        "empirical_freq": [0.40, 0.50],
        "count": [50, 50],
    })
    assert calibration_verdict(curve) == "MISCALIBRATED"


def test_calibration_verdict_insufficient_data():
    """Curve with no bins meeting min_count → INSUFFICIENT_DATA."""
    curve = pd.DataFrame({
        "bin_lo": [0.0],
        "bin_hi": [0.1],
        "mean_pred": [0.05],
        "empirical_freq": [0.05],
        "count": [5],
    })
    assert calibration_verdict(curve, min_count=30) == "INSUFFICIENT_DATA"
