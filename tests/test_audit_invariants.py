"""Smoke tests for scripts/audit_invariants.py.

Exercises each check function against an in-memory SQLite DB (same mirror
schema used across the test suite).  Checks that use MSSQL-specific syntax
(DATEADD/GETUTCDATE for I-6, I-7, I-9; TOP 1 for I-10) are covered with
lightweight mocks rather than SQLite.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQLITE = ROOT / "src" / "storage" / "schema_sqlite.sql"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_invariants import (
    FAIL, OK, WARN,
    _check_i1_pnl,
    _check_i2_edge,
    _check_i4_pnl_parity,
    _check_i5_stake_parity,
    _check_i8_clv_bounds,
    _check_i10_loo_nonzero,
    _check_i11_divergence,
    _check_i12_row_pairs,
    _check_i13_n_fixtures,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQLITE.read_text())
    conn.commit()
    return conn


def _seed(conn: sqlite3.Connection,
          fixture_id="F1", sport_key="soccer_epl",
          book_id=1, book_name="bet365") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO fixtures (id, sport_key, home, away, kickoff_utc)"
        " VALUES (?, ?, 'Home', 'Away', '2026-04-01T15:00:00Z')",
        (fixture_id, sport_key),
    )
    conn.execute(
        "INSERT OR IGNORE INTO books (id, name) VALUES (?, ?)",
        (book_id, book_name),
    )
    conn.commit()


def _insert_bet(conn, bet_id, fixture_id="F1", book_id=1, **kw):
    defaults = dict(
        market="h2h", line=None, side="HOME", odds=2.10,
        commission_rate=0.0, edge=0.03, stake=10.0,
        actual_stake=None, result="pending", pnl=None,
        clv_pct=None, settled_at=None,
        scanned_at="2026-04-30T09:00:00Z",
    )
    defaults.update(kw)
    conn.execute(
        "INSERT INTO bets (id, fixture_id, book_id, scanned_at, market, line,"
        " side, odds, commission_rate, edge, stake, actual_stake, result, pnl,"
        " clv_pct, settled_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (bet_id, fixture_id, book_id,
         defaults["scanned_at"], defaults["market"], defaults["line"],
         defaults["side"], defaults["odds"], defaults["commission_rate"],
         defaults["edge"], defaults["stake"], defaults["actual_stake"],
         defaults["result"], defaults["pnl"], defaults["clv_pct"],
         defaults["settled_at"]),
    )
    conn.commit()


def _insert_book_skill(conn, book="bet365", league="EPL", market="h2h",
                       window_end="2026-04-27", **kw):
    defaults = dict(
        n_fixtures=50,
        edge_vs_consensus_loo=0.005,
        edge_vs_pinnacle=0.010,
        divergence=0.005,
        devig_method="shin",
    )
    defaults.update(kw)
    conn.execute(
        "INSERT INTO book_skill (book, league, market, window_end, devig_method,"
        " n_fixtures, edge_vs_consensus_loo, edge_vs_pinnacle, divergence)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (book, league, market, window_end, defaults["devig_method"],
         defaults["n_fixtures"], defaults["edge_vs_consensus_loo"],
         defaults["edge_vs_pinnacle"], defaults["divergence"]),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# I-1 — P&L arithmetic
# ---------------------------------------------------------------------------

class TestI1Pnl:
    def test_ok_when_empty(self):
        conn = _make_db()
        status, _ = _check_i1_pnl(conn.cursor())
        assert status == OK

    def test_ok_correct_pnl(self):
        conn = _make_db()
        _seed(conn)
        # W: pnl = stake * (odds - 1) = 10 * 1.10 = 11.00
        _insert_bet(conn, "B1", odds=2.10, actual_stake=10.0,
                    result="W", pnl=11.0)
        # L: pnl = -stake = -10.00
        _insert_bet(conn, "B2", odds=2.10, actual_stake=10.0,
                    result="L", pnl=-10.0)
        # V: pnl = 0
        _insert_bet(conn, "B3", odds=2.10, actual_stake=10.0,
                    result="V", pnl=0.0)
        status, _ = _check_i1_pnl(conn.cursor())
        assert status == OK

    def test_fail_on_wrong_pnl(self):
        conn = _make_db()
        _seed(conn)
        # pnl should be 11.0 but stored as 5.0
        _insert_bet(conn, "B1", odds=2.10, actual_stake=10.0,
                    result="W", pnl=5.0)
        status, msg = _check_i1_pnl(conn.cursor())
        assert status == FAIL
        assert "mismatch" in msg

    def test_fail_on_null_pnl_for_settled_row(self):
        conn = _make_db()
        _seed(conn)
        _insert_bet(conn, "B1", odds=2.10, actual_stake=10.0,
                    result="W", pnl=None)
        status, msg = _check_i1_pnl(conn.cursor())
        assert status == FAIL
        assert "NULL pnl" in msg

    def test_ok_with_commission(self):
        conn = _make_db()
        _seed(conn)
        # W with 2% commission: pnl = 10 * 1.10 * 0.98 = 10.78
        _insert_bet(conn, "B1", odds=2.10, actual_stake=10.0,
                    commission_rate=0.02, result="W", pnl=10.78)
        status, _ = _check_i1_pnl(conn.cursor())
        assert status == OK


# ---------------------------------------------------------------------------
# I-2 — Edge bounds
# ---------------------------------------------------------------------------

class TestI2Edge:
    def test_ok_when_empty(self):
        conn = _make_db()
        status, _ = _check_i2_edge(conn.cursor())
        assert status == OK

    def test_ok_within_bounds(self):
        conn = _make_db()
        _seed(conn)
        _insert_bet(conn, "B1", edge=0.03)
        _insert_bet(conn, "B2", edge=-0.05)
        status, _ = _check_i2_edge(conn.cursor())
        assert status == OK

    def test_fail_on_edge_too_high(self):
        conn = _make_db()
        _seed(conn)
        _insert_bet(conn, "B1", edge=0.25)
        status, msg = _check_i2_edge(conn.cursor())
        assert status == FAIL
        assert "1 rows" in msg

    def test_fail_on_edge_too_low(self):
        conn = _make_db()
        _seed(conn)
        _insert_bet(conn, "B1", edge=-0.21)
        status, msg = _check_i2_edge(conn.cursor())
        assert status == FAIL

    def test_paper_bets_negative_edge_not_checked(self):
        # paper_bets edge can be negative for strategy-specific devigging — not flagged
        conn = _make_db()
        _seed(conn)
        conn.execute("INSERT OR IGNORE INTO strategies (id, name) VALUES (1, 'I_power_devig')")
        conn.commit()
        conn.execute(
            "INSERT INTO paper_bets (id, strategy_id, fixture_id, book_id, scanned_at,"
            " market, side, odds, edge, stake, result)"
            " VALUES ('PB1', 1, 'F1', 1, '2026-05-01T09:00:00Z', 'h2h', 'HOME', 2.10, -0.45, 10.0, 'pending')"
        )
        conn.commit()
        status, _ = _check_i2_edge(conn.cursor())
        assert status == OK  # paper_bets not checked


# ---------------------------------------------------------------------------
# I-4 — P&L parity
# ---------------------------------------------------------------------------

class TestI4PnlParity:
    def test_ok_when_empty(self):
        conn = _make_db()
        status, _ = _check_i4_pnl_parity(conn.cursor())
        assert status == OK

    def test_ok_with_consistent_rows(self):
        conn = _make_db()
        _seed(conn)
        _insert_bet(conn, "B1", actual_stake=10.0, result="W", pnl=11.0)
        _insert_bet(conn, "B2", actual_stake=10.0, result="L", pnl=-10.0)
        status, _ = _check_i4_pnl_parity(conn.cursor())
        assert status == OK


# ---------------------------------------------------------------------------
# I-5 — Stake parity
# ---------------------------------------------------------------------------

class TestI5StakeParity:
    def test_ok_when_empty(self):
        conn = _make_db()
        status, _ = _check_i5_stake_parity(conn.cursor())
        assert status == OK

    def test_ok_with_settled_bets(self):
        conn = _make_db()
        _seed(conn)
        _insert_bet(conn, "B1", actual_stake=10.0, result="W", pnl=11.0)
        _insert_bet(conn, "B2", actual_stake=15.0, result="L", pnl=-15.0)
        status, msg = _check_i5_stake_parity(conn.cursor())
        assert status == OK
        assert "25.00" in msg


# ---------------------------------------------------------------------------
# I-8 — CLV bounds
# ---------------------------------------------------------------------------

class TestI8ClvBounds:
    def test_ok_when_no_clv(self):
        conn = _make_db()
        status, _ = _check_i8_clv_bounds(conn.cursor())
        assert status == OK

    def test_ok_within_bounds(self):
        conn = _make_db()
        _seed(conn)
        _insert_bet(conn, "B1", actual_stake=10.0, result="W",
                    pnl=11.0, clv_pct=0.05)
        _insert_bet(conn, "B2", actual_stake=10.0, result="L",
                    pnl=-10.0, clv_pct=-0.03)
        status, _ = _check_i8_clv_bounds(conn.cursor())
        assert status == OK

    def test_fail_on_clv_out_of_bounds(self):
        conn = _make_db()
        _seed(conn)
        _insert_bet(conn, "B1", actual_stake=10.0, result="W",
                    pnl=11.0, clv_pct=0.75)
        status, msg = _check_i8_clv_bounds(conn.cursor())
        assert status == FAIL
        assert "1 bets" in msg


# ---------------------------------------------------------------------------
# I-10 — LOO non-zero (mocked: uses TOP 1 which SQLite doesn't support)
# ---------------------------------------------------------------------------

class TestI10LooNonzero:
    def _mock_cur(self, fetchone_returns):
        cur = MagicMock()
        cur.fetchone.side_effect = fetchone_returns
        return cur

    def test_ok_when_no_rows(self):
        cur = self._mock_cur([None])
        status, msg = _check_i10_loo_nonzero(cur)
        assert status == OK
        assert "no rows" in msg

    def test_fail_when_avg_abs_too_small(self):
        cur = self._mock_cur([("2026-04-27",), (0.000005,)])
        status, msg = _check_i10_loo_nonzero(cur)
        assert status == FAIL
        assert "≤ 0.0001" in msg

    def test_ok_when_avg_abs_sufficient(self):
        cur = self._mock_cur([("2026-04-27",), (0.005,)])
        status, msg = _check_i10_loo_nonzero(cur)
        assert status == OK


# ---------------------------------------------------------------------------
# I-11 — Divergence identity
# ---------------------------------------------------------------------------

class TestI11Divergence:
    def test_ok_when_no_rows(self):
        conn = _make_db()
        status, _ = _check_i11_divergence(conn.cursor())
        assert status == OK

    def test_ok_when_identity_holds(self):
        conn = _make_db()
        _seed(conn)
        _insert_book_skill(conn, edge_vs_pinnacle=0.010,
                           edge_vs_consensus_loo=0.005, divergence=0.005)
        status, _ = _check_i11_divergence(conn.cursor())
        assert status == OK

    def test_fail_when_divergence_wrong(self):
        conn = _make_db()
        _seed(conn)
        # divergence should be 0.010 - 0.005 = 0.005, stored as 0.999
        _insert_book_skill(conn, edge_vs_pinnacle=0.010,
                           edge_vs_consensus_loo=0.005, divergence=0.999)
        status, msg = _check_i11_divergence(conn.cursor())
        assert status == FAIL
        assert "1/1" in msg


# ---------------------------------------------------------------------------
# I-12 — Row pairs (shin + multiplicative)
# ---------------------------------------------------------------------------

class TestI12RowPairs:
    def test_ok_when_no_rows(self):
        conn = _make_db()
        status, _ = _check_i12_row_pairs(conn.cursor())
        assert status == OK

    def test_ok_with_correct_pairs(self):
        conn = _make_db()
        _seed(conn)
        _insert_book_skill(conn, devig_method="shin")
        _insert_book_skill(conn, devig_method="multiplicative")
        status, _ = _check_i12_row_pairs(conn.cursor())
        assert status == OK

    def test_fail_when_only_one_method(self):
        conn = _make_db()
        _seed(conn)
        _insert_book_skill(conn, devig_method="shin")
        status, msg = _check_i12_row_pairs(conn.cursor())
        assert status == FAIL

    def test_fail_when_unknown_method(self):
        conn = _make_db()
        _seed(conn)
        # Cardinality = 2 but method identity fails: "shin" + "other" is not
        # the required "shin" + "multiplicative" pair.
        _insert_book_skill(conn, devig_method="shin")
        _insert_book_skill(conn, devig_method="other")
        status, msg = _check_i12_row_pairs(conn.cursor())
        assert status == FAIL


# ---------------------------------------------------------------------------
# I-13 — n_fixtures > 0
# ---------------------------------------------------------------------------

class TestI13NFixtures:
    def test_ok_when_no_rows(self):
        conn = _make_db()
        status, _ = _check_i13_n_fixtures(conn.cursor())
        assert status == OK

    def test_ok_with_valid_rows(self):
        conn = _make_db()
        _seed(conn)
        _insert_book_skill(conn, n_fixtures=50, devig_method="shin")
        _insert_book_skill(conn, n_fixtures=30, devig_method="multiplicative")
        status, _ = _check_i13_n_fixtures(conn.cursor())
        assert status == OK

    def test_fail_on_zero_fixtures(self):
        conn = _make_db()
        _seed(conn)
        _insert_book_skill(conn, n_fixtures=0)
        status, msg = _check_i13_n_fixtures(conn.cursor())
        assert status == FAIL
        assert "1 book_skill rows" in msg
