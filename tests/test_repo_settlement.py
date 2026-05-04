"""Tests for BetRepo.settle_bet and settle_paper_bet (Phase S.4 T2)."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQLITE = ROOT / "src" / "storage" / "schema_sqlite.sql"


@pytest.fixture
def fresh_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("BETS_") or k.startswith("AZURE_SQL_"):
            monkeypatch.delenv(k, raising=False)
    return monkeypatch


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQLITE.read_text())
    conn.commit()
    return conn


class _SqliteRepo:
    def __init__(self, conn, logs_dir):
        from src.storage.repo import BetRepo
        self.repo = BetRepo(logs_dir=logs_dir, dsn="sqlite-test")
        self.repo._conn = conn
        self.repo._cur = conn.cursor()
        self.repo._connect = lambda: conn  # type: ignore[method-assign]


def _bet_row(side="HOME", book="bet365", market="h2h", line="",
             kickoff="2026-04-10 15:00"):
    return {
        "scanned_at": "2026-04-05 09:00 UTC", "sport": "EPL",
        "market": market, "line": line,
        "home": "Arsenal", "away": "Chelsea",
        "kickoff": kickoff,
        "side": side, "book": book, "odds": 2.10,
        "impl_raw": 0.476, "impl_effective": 0.480,
        "edge": 0.030, "edge_gross": 0.040,
        "effective_odds": 2.075, "commission_rate": 0.02,
        "consensus": 0.510, "pinnacle_cons": 0.500,
        "n_books": 30, "confidence": "HIGH",
        "model_signal": "+0.05", "dispersion": 0.01,
        "outlier_z": 0.0, "devig_method": "shin",
        "weight_scheme": "uniform", "stake": 15.0, "result": "",
    }


# ── settle_bet ────────────────────────────────────────────────────────────────

def test_settle_bet_populates_columns(fresh_env, tmp_path):
    db = _make_db()
    helper = _SqliteRepo(db, tmp_path)
    repo = helper.repo

    repo.add_bets([_bet_row()])

    # Get fixture_id
    fid = db.execute("SELECT fixture_id FROM bets").fetchone()[0]
    bk_name = "bet365"

    ok = repo.settle_bet(
        fid, "HOME", "h2h", None, bk_name,
        result="W", pnl=10.5, pin_prob=0.42, clv_pct=0.03,
    )
    assert ok is True

    row = db.execute(
        "SELECT result, pnl, settled_at, pinnacle_close_prob, clv_pct FROM bets"
    ).fetchone()
    assert row[0] == "W"
    assert abs(row[1] - 10.5) < 1e-6
    assert row[2] is not None   # settled_at populated
    assert abs(row[3] - 0.42) < 1e-6
    assert abs(row[4] - 0.03) < 1e-6


def test_settle_bet_result_write_is_one_shot(fresh_env, tmp_path):
    """Re-settling an already-settled bet returns True for CLV refresh
    but leaves settled_at unchanged."""
    db = _make_db()
    helper = _SqliteRepo(db, tmp_path)
    repo = helper.repo

    repo.add_bets([_bet_row()])
    fid = db.execute("SELECT fixture_id FROM bets").fetchone()[0]
    bk = "bet365"

    repo.settle_bet(fid, "HOME", "h2h", None, bk,
                    result="W", pnl=10.5, pin_prob=0.42, clv_pct=0.03)
    settled_at_first = db.execute("SELECT settled_at FROM bets").fetchone()[0]

    # Second call: result no-op (already W), CLV refresh
    ok2 = repo.settle_bet(fid, "HOME", "h2h", None, bk,
                          result="L", pnl=-15.0, pin_prob=0.45, clv_pct=0.06)
    # CLV refresh should still succeed
    assert ok2 is True

    row = db.execute("SELECT result, settled_at, pinnacle_close_prob FROM bets").fetchone()
    # result unchanged (W, not overwritten to L)
    assert row[0] == "W"
    # settled_at unchanged
    assert row[1] == settled_at_first
    # CLV refreshed
    assert abs(row[2] - 0.45) < 1e-6


def test_settle_bet_db_disabled_returns_false(fresh_env, tmp_path):
    from src.storage.repo import BetRepo
    repo = BetRepo(logs_dir=tmp_path)  # dsn=None via env
    assert repo.db_enabled is False
    ok = repo.settle_bet("fid", "HOME", "h2h", None, "bet365",
                         result="W", pnl=5.0, pin_prob=0.5, clv_pct=0.02)
    assert ok is False


# ── settle_paper_bet ──────────────────────────────────────────────────────────

def test_settle_paper_bet_populates_columns(fresh_env, tmp_path):
    db = _make_db()
    helper = _SqliteRepo(db, tmp_path)
    repo = helper.repo

    paper_row = {**_bet_row(), "strategy": "A_production",
                 "code_sha": "", "strategy_config_hash": "",
                 "pinnacle_close_prob": "", "clv_pct": ""}
    repo.add_paper_bets("A_production", [paper_row])

    fid = db.execute("SELECT fixture_id FROM paper_bets").fetchone()[0]
    bk = "bet365"

    ok = repo.settle_paper_bet(
        "A_production", fid, "HOME", "h2h", None, bk,
        result="L", pnl=-15.0, pin_prob=0.48, clv_pct=-0.01,
    )
    assert ok is True

    row = db.execute(
        "SELECT result, pnl, settled_at, pinnacle_close_prob, clv_pct FROM paper_bets"
    ).fetchone()
    assert row[0] == "L"
    assert abs(row[1] - (-15.0)) < 1e-6
    assert row[2] is not None
    assert abs(row[3] - 0.48) < 1e-6
    assert abs(row[4] - (-0.01)) < 1e-6


def test_settle_paper_bet_strategy_scoped(fresh_env, tmp_path):
    """Two strategies, same fixture/side/book: only the targeted strategy row updates."""
    db = _make_db()
    helper = _SqliteRepo(db, tmp_path)
    repo = helper.repo

    base_row = {**_bet_row(), "strategy": "A_production",
                "code_sha": "", "strategy_config_hash": "",
                "pinnacle_close_prob": "", "clv_pct": ""}

    repo.add_paper_bets("A_production", [base_row])
    repo.add_paper_bets("B_power_devig", [base_row])

    fid = db.execute("SELECT fixture_id FROM paper_bets LIMIT 1").fetchone()[0]
    bk = "bet365"

    # Settle only A_production
    ok = repo.settle_paper_bet(
        "A_production", fid, "HOME", "h2h", None, bk,
        result="W", pnl=10.5, pin_prob=0.42, clv_pct=0.03,
    )
    assert ok is True

    # Check A_production row updated; B_power_devig row unchanged
    rows = db.execute(
        "SELECT s.name, pb.result, pb.pnl FROM paper_bets pb "
        "JOIN strategies s ON s.id = pb.strategy_id ORDER BY s.name"
    ).fetchall()
    by_name = {r[0]: (r[1], r[2]) for r in rows}

    assert by_name["A_production"][0] == "W"
    assert abs(by_name["A_production"][1] - 10.5) < 1e-6
    # B_power_devig must still be 'pending' with NULL pnl
    assert by_name["B_power_devig"][0] == "pending"
    assert by_name["B_power_devig"][1] is None


def test_settle_paper_bet_result_one_shot(fresh_env, tmp_path):
    db = _make_db()
    helper = _SqliteRepo(db, tmp_path)
    repo = helper.repo

    base_row = {**_bet_row(), "strategy": "A_production",
                "code_sha": "", "strategy_config_hash": "",
                "pinnacle_close_prob": "", "clv_pct": ""}
    repo.add_paper_bets("A_production", [base_row])

    fid = db.execute("SELECT fixture_id FROM paper_bets").fetchone()[0]
    bk = "bet365"

    repo.settle_paper_bet("A_production", fid, "HOME", "h2h", None, bk,
                          result="W", pnl=10.5, pin_prob=0.42, clv_pct=0.03)
    at1 = db.execute("SELECT settled_at FROM paper_bets").fetchone()[0]

    # Re-settle: result write is no-op (already W); CLV refresh still happens
    ok2 = repo.settle_paper_bet("A_production", fid, "HOME", "h2h", None, bk,
                                result="L", pnl=-15.0, pin_prob=0.50, clv_pct=0.05)
    assert ok2 is True

    row = db.execute("SELECT result, settled_at, pinnacle_close_prob FROM paper_bets").fetchone()
    assert row[0] == "W"         # not overwritten
    assert row[1] == at1         # settled_at unchanged
    assert abs(row[2] - 0.50) < 1e-6  # CLV refreshed


# ── iter_unsettled_or_no_clv ──────────────────────────────────────────────────

def test_iter_unsettled_yields_past_kickoff_rows(fresh_env, tmp_path):
    db = _make_db()
    helper = _SqliteRepo(db, tmp_path)
    repo = helper.repo

    # Past kickoff
    repo.add_bets([_bet_row(kickoff="2026-04-10 15:00")])

    from datetime import datetime
    rows = list(repo.iter_unsettled_or_no_clv(
        now_utc=datetime(2026, 5, 1, 12, 0)
    ))
    assert len(rows) == 1
    assert rows[0]["result"] == "pending"
    assert rows[0]["strategy_name"] is None


def test_iter_unsettled_skips_future_kickoff(fresh_env, tmp_path):
    db = _make_db()
    helper = _SqliteRepo(db, tmp_path)
    repo = helper.repo

    repo.add_bets([_bet_row(kickoff="2099-01-01 15:00")])

    from datetime import datetime
    rows = list(repo.iter_unsettled_or_no_clv(
        now_utc=datetime(2026, 5, 1, 12, 0)
    ))
    assert len(rows) == 0


def test_iter_unsettled_includes_paper_bets(fresh_env, tmp_path):
    db = _make_db()
    helper = _SqliteRepo(db, tmp_path)
    repo = helper.repo

    base_row = {**_bet_row(kickoff="2026-04-10 15:00"), "strategy": "A_production",
                "code_sha": "", "strategy_config_hash": "",
                "pinnacle_close_prob": "", "clv_pct": ""}
    repo.add_paper_bets("A_production", [base_row])

    from datetime import datetime
    rows = list(repo.iter_unsettled_or_no_clv(now_utc=datetime(2026, 5, 1, 12, 0)))
    paper_rows = [r for r in rows if r["strategy_name"] is not None]
    assert len(paper_rows) == 1
    assert paper_rows[0]["strategy_name"] == "A_production"
