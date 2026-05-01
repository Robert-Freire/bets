"""Tests for src/model/holdout.py (Phase 7.1)."""
import pytest
import pandas as pd

from src.model.holdout import rolling_holdout_eval

EXPECTED_COLS = {"sport_key", "test_season", "n_matches", "model_rps", "bookmaker_rps", "rps_gap", "model_accuracy", "has_edge"}


def test_rolling_holdout_eval_epl_basic():
    """rolling_holdout_eval on a small EPL slice returns expected columns with ≥1 row."""
    pytest.importorskip("catboost")
    df = rolling_holdout_eval("soccer_epl", train_window=3, since="2021")
    assert not df.empty, "Expected at least one evaluated season"
    assert EXPECTED_COLS.issubset(set(df.columns)), f"Missing columns: {EXPECTED_COLS - set(df.columns)}"
    assert (df["n_matches"] > 0).all()
    assert df["model_rps"].notna().all()
    assert df["bookmaker_rps"].notna().all()


def test_rolling_holdout_eval_returns_dataframe():
    """rolling_holdout_eval always returns a DataFrame (possibly empty on missing data)."""
    pytest.importorskip("catboost")
    result = rolling_holdout_eval("soccer_epl", train_window=3, since="2021")
    assert isinstance(result, pd.DataFrame)
