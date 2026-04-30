"""Tests for src/betting/walk_forward.py (Phase R.5.5a)."""

import numpy as np
import pandas as pd

from src.betting.walk_forward import load_backtest_data, walk_forward_backtest


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _fabricated_matches(n: int = 60) -> pd.DataFrame:
    """
    Minimal DataFrame in the same shape backtest_consensus() expects.

    Uses only B365 odds so there is always at least one bookmaker per row.
    Odds are set so no side clears min_edge=0.99 (used for the no-bets sanity test).
    """
    dates = pd.date_range("2020-01-01", periods=n, freq="W")
    rng = np.random.default_rng(seed=42)
    # Balanced odds ~2.0/3.4/2.0 — consensus ≈ fair; no 99% edge available
    rows = {
        "Date": dates,
        "HomeTeam": ["H"] * n,
        "AwayTeam": ["A"] * n,
        "FTR": rng.choice(["H", "D", "A"], size=n),
        "B365H": [2.0] * n,
        "B365D": [3.4] * n,
        "B365A": [2.0] * n,
        # Second book to give n_books_used >= 2
        "BWH": [2.05] * n,
        "BWD": [3.35] * n,
        "BWA": [2.05] * n,
    }
    return pd.DataFrame(rows)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_loader_returns_large_time_ordered_dataframe():
    """Loader returns ≥1000 rows sorted by Date."""
    m = load_backtest_data()
    assert len(m) >= 1000, f"Expected >=1000 matches, got {len(m)}"
    dates = pd.to_datetime(m["Date"], errors="coerce")
    assert dates.is_monotonic_increasing, "Matches must be time-ordered (ascending Date)"


def test_walk_forward_returns_n_splits_rows():
    """walk_forward_backtest(n_splits=5) returns exactly 5 rows with fold_idx [0..4]."""
    m = _fabricated_matches(60)
    result = walk_forward_backtest(m, consensus_method="raw", min_edge=0.02, n_splits=5)
    assert len(result) == 5, f"Expected 5 rows, got {len(result)}"
    assert list(result["fold_idx"]) == [0, 1, 2, 3, 4]


def test_folds_are_temporally_ordered():
    """Each fold's end_date is <= the next fold's start_date."""
    m = _fabricated_matches(60)
    result = walk_forward_backtest(m, consensus_method="raw", min_edge=0.02, n_splits=5)
    for i in range(len(result) - 1):
        end_i = result.loc[i, "end_date"]
        start_next = result.loc[i + 1, "start_date"]
        assert end_i <= start_next, (
            f"Fold {i} end_date ({end_i}) > fold {i+1} start_date ({start_next})"
        )


def test_walk_forward_is_deterministic():
    """Two identical calls return DataFrames with identical numeric columns."""
    m = _fabricated_matches(60)
    r1 = walk_forward_backtest(m, consensus_method="raw", min_edge=0.02, n_splits=5)
    r2 = walk_forward_backtest(m, consensus_method="raw", min_edge=0.02, n_splits=5)
    numeric = ["n_bets", "n_won", "total_staked", "total_pnl", "roi"]
    pd.testing.assert_frame_equal(r1[numeric], r2[numeric], check_exact=True)


def test_no_bets_when_edge_impossible():
    """With min_edge=0.99, every fold reports n_bets=0 and roi=0."""
    m = _fabricated_matches(60)
    result = walk_forward_backtest(m, consensus_method="raw", min_edge=0.99, n_splits=5)
    assert (result["n_bets"] == 0).all(), "Expected 0 bets per fold at min_edge=0.99"
    assert (result["roi"] == 0.0).all(), "Expected roi=0 per fold when no bets placed"


