"""Tests for src/storage/repo.py BetRepo (A.9: DB-only).

Coverage:
- Pi safety: no env flags → no pyodbc import, add_bets is a no-op.
- DB write mode: BETS_DB_WRITE=1 + DSN → DB row lands with the same
  UUID5 the A.3 importer would compute.
- Idempotency: running the same row twice produces ONE DB row
  (INSERT-IF-NOT-EXISTS — no double-writes on cron retries).
- Failure isolation: DB connect failure → no exception raised (silent drop).
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQLITE = ROOT / "src" / "storage" / "schema_sqlite.sql"

# Used by the SQLite-backed dual-write tests: a thin DSN substitute that
# the repo treats as "DB enabled" without going through pyodbc.


@pytest.fixture
def fresh_env(monkeypatch):
    """Strip every BETS_*/AZURE_SQL_* env var; tests opt back in explicitly."""
    for k in list(os.environ):
        if k.startswith("BETS_") or k.startswith("AZURE_SQL_"):
            monkeypatch.delenv(k, raising=False)
    return monkeypatch


def _make_db():
    """In-memory SQLite with the canonical schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQLITE.read_text())
    conn.commit()
    return conn


def _bet_row(scanned_at="2026-04-30 09:00 UTC", side="HOME", book="bet365",
             market="h2h", line=""):
    return {
        "scanned_at": scanned_at, "sport": "EPL", "market": market, "line": line,
        "home": "Arsenal", "away": "Chelsea", "kickoff": "2026-05-10 15:00",
        "side": side, "book": book, "odds": 2.10,
        "impl_raw": 0.476, "impl_effective": 0.480, "edge": 0.030,
        "edge_gross": 0.040, "effective_odds": 2.075, "commission_rate": 0.02,
        "consensus": 0.510, "pinnacle_cons": 0.500, "n_books": 30,
        "confidence": "HIGH", "model_signal": "+0.05", "dispersion": 0.01,
        "outlier_z": 0.0, "devig_method": "shin", "weight_scheme": "uniform",
        "stake": 15.0, "result": "",
    }


# ---- Pi safety contract ---------------------------------------------------

def test_pi_safety_no_env_means_no_pyodbc_import(fresh_env, tmp_path):
    """Without env flags, BetRepo is a no-op and never imports pyodbc."""
    fresh_env.delitem(sys.modules, "pyodbc", raising=False)

    from src.storage.repo import BetRepo
    repo = BetRepo()
    assert repo.db_enabled is False

    repo.add_bets([_bet_row()])  # must not raise; silently no-ops

    assert "pyodbc" not in sys.modules, (
        "pyodbc was imported even though BETS_DB_WRITE was unset — "
        "Pi safety contract violated."
    )
    repo.close()


def test_db_disabled_when_only_flag_set(fresh_env, tmp_path):
    """BETS_DB_WRITE=1 without a DSN must NOT enable DB writes."""
    fresh_env.setenv("BETS_DB_WRITE", "1")
    from src.storage.repo import BetRepo
    repo = BetRepo()
    assert repo.db_enabled is False


# ---- Dual-write mode (SQLite-backed for the test) -------------------------

class _SqliteRepo:
    """BetRepo subclass that swaps pyodbc.connect for sqlite3.connect.

    Lets us exercise the real DB-write code paths without an Azure DB.
    Keeps the tests hermetic and fast.
    """

    def __init__(self, conn, logs_dir):
        from src.storage.repo import BetRepo
        self.repo = BetRepo(logs_dir=logs_dir, dsn="sqlite-test")
        # Override connect: use the provided sqlite conn directly.
        self.repo._conn = conn
        self.repo._cur = conn.cursor()
        # Patch _connect so it returns the existing conn.
        self.repo._connect = lambda: conn  # type: ignore[method-assign]


def test_db_write_produces_matching_uuid(fresh_env, tmp_path):
    """add_bets writes to DB with the same UUID5 the A.3 importer computes."""
    db = _make_db()
    helper = _SqliteRepo(db, tmp_path)
    repo = helper.repo

    row = _bet_row()
    repo.add_bets([row])

    db_rows = db.execute("SELECT id FROM bets").fetchall()
    assert len(db_rows) == 1

    from src.storage._keys import bet_uuid, scan_date_of, normalise_line
    expected = bet_uuid(
        scan_date_of(row["scanned_at"]),
        row["kickoff"], row["home"], row["away"],
        row["market"], normalise_line(row["line"]),
        row["side"], row["book"],
    )
    assert db_rows[0][0] == expected


def test_db_write_idempotent_on_retry(fresh_env, tmp_path):
    """Calling add_bets twice with the same row produces exactly 1 DB row:
    deterministic UUID + INSERT-IF-NOT-EXISTS makes the second insert a no-op."""
    db = _make_db()
    helper = _SqliteRepo(db, tmp_path)
    repo = helper.repo

    repo.add_bets([_bet_row()])
    repo.add_bets([_bet_row()])

    assert db.execute("SELECT COUNT(*) FROM bets").fetchone()[0] == 1


def test_dual_write_paper_bets(fresh_env, tmp_path):
    db = _make_db()
    helper = _SqliteRepo(db, tmp_path)
    repo = helper.repo

    rows = [
        {**_bet_row(), "strategy": "A_production", "code_sha": "", "strategy_config_hash": "",
         "pinnacle_close_prob": "", "clv_pct": ""},
        {**_bet_row(book="williamhill"), "strategy": "A_production", "code_sha": "",
         "strategy_config_hash": "", "pinnacle_close_prob": "", "clv_pct": ""},
    ]
    repo.add_paper_bets("A_production", rows)

    assert db.execute(
        "SELECT COUNT(*) FROM paper_bets pb JOIN strategies s ON s.id=pb.strategy_id "
        "WHERE s.name = 'A_production'"
    ).fetchone()[0] == 2


def test_dual_write_closing_lines_uses_pinnacle_book(fresh_env, tmp_path):
    db = _make_db()
    helper = _SqliteRepo(db, tmp_path)
    repo = helper.repo

    closing_row = {
        "captured_at": "2026-05-10 14:55 UTC",
        "home": "Arsenal", "away": "Chelsea",
        "kickoff": "2026-05-10 15:00", "side": "HOME",
        "market": "h2h", "line": "",
        "pinnacle_devig_prob": 0.515, "pinnacle_raw_odds": 1.94,
        "your_book_flagged_odds": 2.10, "your_book_close_odds": 2.05,
        "clv_pct": 0.054,
        "sport": "EPL",
    }
    repo.add_closing_lines([closing_row])
    rows = db.execute(
        "SELECT b.name, cl.pinnacle_close_prob FROM closing_lines cl "
        "JOIN books b ON b.id = cl.book_id"
    ).fetchall()
    assert rows == [("pinnacle", 0.515)]


def test_dual_write_drift(fresh_env, tmp_path):
    db = _make_db()
    helper = _SqliteRepo(db, tmp_path)
    repo = helper.repo

    drift_row = {
        "captured_at": "2026-05-10 14:00 UTC",
        "home": "Arsenal", "away": "Chelsea",
        "kickoff": "2026-05-10 15:00", "side": "HOME",
        "market": "h2h", "line": "", "book": "bet365",
        "t_minus_min": 60, "your_book_odds": 2.10,
        "pinnacle_odds": 1.95, "n_books": 30,
        "sport": "EPL",
    }
    repo.add_drift_snapshot([drift_row])
    n = db.execute("SELECT COUNT(*) FROM drift").fetchone()[0]
    assert n == 1


# ---- Failure isolation ----------------------------------------------------

def test_db_connect_failure_does_not_raise(fresh_env, tmp_path, monkeypatch):
    """A bogus DSN must NOT raise — write silently drops and latches db_failed."""
    monkeypatch.setenv("BETS_DB_WRITE", "1")
    monkeypatch.setenv("AZURE_SQL_DSN", "Driver={Nonexistent};Server=nope;")
    from src.storage.repo import BetRepo
    repo = BetRepo()
    assert repo.db_enabled is True  # env says yes; failure latches on first connect
    repo.add_bets([_bet_row()])  # must not raise
    assert repo.db_enabled is False  # latched after failed connect


def test_invalid_dsn_does_not_raise_on_paper_or_closing(fresh_env, tmp_path, monkeypatch):
    """Same as above but for paper_bets + closing_lines paths."""
    monkeypatch.setenv("BETS_DB_WRITE", "1")
    monkeypatch.setenv("AZURE_SQL_DSN", "Driver={Nonexistent};Server=nope;")
    from src.storage.repo import BetRepo
    repo = BetRepo()
    repo.add_paper_bets("A_production", [{**_bet_row(), "strategy": "A_production"}])
    repo.add_closing_lines([{
        "captured_at": "2026-05-10 14:55 UTC",
        "home": "X", "away": "Y", "kickoff": "2026-05-10 15:00",
        "side": "HOME", "market": "h2h", "line": "",
        "pinnacle_devig_prob": 0.5,
    }])
    # No exception raised; DB latched as failed
    assert repo.db_enabled is False


def test_connect_retry_succeeds_on_second_attempt(fresh_env, monkeypatch, capsys):
    """_pyodbc_connect retries after a transient failure and succeeds on attempt 2."""
    import types
    import src.storage.repo as repo_mod

    call_count = 0

    class _FakeConn:
        pass

    def _fake_connect(dsn):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("HYT00: Login timeout expired")
        return _FakeConn()

    fake_pyodbc = types.ModuleType("pyodbc")
    fake_pyodbc.connect = _fake_connect
    monkeypatch.setitem(sys.modules, "pyodbc", fake_pyodbc)
    monkeypatch.setattr(repo_mod._time, "sleep", lambda s: None)

    conn = repo_mod._pyodbc_connect("DSN=fake")
    assert isinstance(conn, _FakeConn)
    assert call_count == 2
    captured = capsys.readouterr()
    assert "attempt 1 failed" in captured.err
    assert "attempt 2" in captured.err


def test_connect_retry_exhausted_raises(fresh_env, monkeypatch):
    """_pyodbc_connect raises after all attempts are exhausted."""
    import types
    import src.storage.repo as repo_mod

    fake_pyodbc = types.ModuleType("pyodbc")
    fake_pyodbc.connect = lambda dsn: (_ for _ in ()).throw(Exception("always fails"))
    monkeypatch.setitem(sys.modules, "pyodbc", fake_pyodbc)
    monkeypatch.setattr(repo_mod._time, "sleep", lambda s: None)

    with pytest.raises(Exception, match="always fails"):
        repo_mod._pyodbc_connect("DSN=fake")
