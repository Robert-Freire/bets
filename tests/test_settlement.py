"""Pure-function tests for _settle_from_fdco and _pnl (Phase S.4 T1)."""
import pytest

from scripts.backfill_clv_from_fdco import _settle_from_fdco, _pnl


# ── _settle_from_fdco ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("ftr,side,expected", [
    # FTR=H
    ("H", "HOME", "W"),
    ("H", "DRAW", "L"),
    ("H", "AWAY", "L"),
    # FTR=D
    ("D", "HOME", "L"),
    ("D", "DRAW", "W"),
    ("D", "AWAY", "L"),
    # FTR=A
    ("A", "HOME", "L"),
    ("A", "DRAW", "L"),
    ("A", "AWAY", "W"),
])
def test_settle_h2h(ftr, side, expected):
    row = {"FTR": ftr, "FTHG": "1", "FTAG": "0"}
    assert _settle_from_fdco(row, "h2h", side) == expected


def test_settle_h2h_blank_ftr():
    row = {"FTR": "", "FTHG": "1", "FTAG": "0"}
    assert _settle_from_fdco(row, "h2h", "HOME") is None


def test_settle_h2h_missing_ftr():
    assert _settle_from_fdco({}, "h2h", "HOME") is None


@pytest.mark.parametrize("fthg,ftag,side,expected", [
    # 2+1=3 > 2.5
    ("2", "1", "OVER", "W"),
    ("2", "1", "UNDER", "L"),
    # 1+1=2 < 2.5
    ("1", "1", "OVER", "L"),
    ("1", "1", "UNDER", "W"),
])
def test_settle_totals(fthg, ftag, side, expected):
    row = {"FTR": "H", "FTHG": fthg, "FTAG": ftag}
    assert _settle_from_fdco(row, "totals", side) == expected


def test_settle_totals_blank_goals():
    row = {"FTR": "H", "FTHG": "", "FTAG": ""}
    assert _settle_from_fdco(row, "totals", "OVER") is None


def test_settle_totals_missing_goals():
    assert _settle_from_fdco({}, "totals", "OVER") is None


def test_settle_unknown_market():
    row = {"FTR": "H"}
    assert _settle_from_fdco(row, "btts", "YES") is None


# ── _pnl ─────────────────────────────────────────────────────────────────────

def test_pnl_win():
    assert _pnl(10.0, 2.0, "W") == pytest.approx(10.0)


def test_pnl_loss():
    assert _pnl(10.0, 2.0, "L") == pytest.approx(-10.0)


def test_pnl_void():
    assert _pnl(10.0, 2.0, "void") == 0.0


def test_pnl_result_none():
    assert _pnl(10.0, 2.0, None) is None


def test_pnl_stake_zero():
    assert _pnl(0.0, 2.0, "W") is None


def test_pnl_stake_none():
    assert _pnl(None, 2.0, "W") is None


def test_pnl_odds_exactly_one():
    assert _pnl(10.0, 1.0, "W") is None


def test_pnl_odds_below_one():
    assert _pnl(10.0, 0.9, "W") is None


def test_pnl_odds_none():
    assert _pnl(10.0, None, "W") is None
