"""Tests for the A.5 read API on BetRepo (get_bets / get_drift /
update_bet_settle / db_status) plus the /health endpoint and
DB-first/CSV-fallback behavior of app.py.

We exercise the real DB-write SQL paths against in-memory SQLite —
same trick as test_repo_dual_write.py — so the tests are hermetic.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQLITE = ROOT / "src" / "storage" / "schema_sqlite.sql"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQLITE.read_text())
    conn.commit()
    return conn


def _bet_row(scanned_at="2026-04-30 09:00 UTC", side="HOME", book="bet365",
             market="h2h", line="", home="Arsenal", away="Chelsea"):
    return {
        "scanned_at": scanned_at, "sport": "EPL", "market": market, "line": line,
        "home": home, "away": away, "kickoff": "2026-05-10 15:00",
        "side": side, "book": book, "odds": 2.10,
        "impl_raw": 0.476, "impl_effective": 0.480, "edge": 0.030,
        "edge_gross": 0.040, "effective_odds": 2.075, "commission_rate": 0.02,
        "consensus": 0.510, "pinnacle_cons": 0.500, "n_books": 30,
        "confidence": "HIGH", "model_signal": "+0.05", "dispersion": 0.01,
        "outlier_z": 0.0, "devig_method": "shin", "weight_scheme": "uniform",
        "stake": 15.0, "result": "",
    }


def _make_repo(conn, tmp_path):
    """BetRepo with a SQLite connection wired in (bypasses pyodbc)."""
    from src.storage.repo import BetRepo
    repo = BetRepo(logs_dir=tmp_path, dsn="sqlite-test")
    repo._conn = conn
    repo._cur = conn.cursor()
    repo._connect = lambda: conn  # type: ignore[method-assign]
    return repo


@pytest.fixture
def fresh_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("BETS_") or k.startswith("AZURE_SQL_"):
            monkeypatch.delenv(k, raising=False)
    return monkeypatch


# ---- db_status ------------------------------------------------------------

def test_db_status_disabled_when_env_missing(fresh_env, tmp_path):
    from src.storage.repo import BetRepo
    repo = BetRepo(logs_dir=tmp_path)
    assert repo.db_status() == "disabled"


def test_db_status_down_on_bad_dsn(fresh_env, tmp_path, monkeypatch):
    monkeypatch.setenv("BETS_DB_WRITE", "1")
    monkeypatch.setenv("AZURE_SQL_DSN", "Driver={Nonexistent};Server=nope;")
    from src.storage.repo import BetRepo
    repo = BetRepo(logs_dir=tmp_path)
    assert repo.db_status() == "down"


def test_db_status_ok_when_connection_works(fresh_env, tmp_path):
    db = _make_db()
    repo = _make_repo(db, tmp_path)
    assert repo.db_status() == "ok"


# ---- get_bets -------------------------------------------------------------

def test_get_bets_returns_csv_style_dicts(fresh_env, tmp_path):
    db = _make_db()
    repo = _make_repo(db, tmp_path)

    repo.add_bets([_bet_row(), _bet_row(book="williamhill")])
    rows = repo.get_bets()
    assert rows is not None
    assert len(rows) == 2

    sample = rows[0]
    # All the dict keys the dashboard's _normalise_row + summary_stats expect
    expected_keys = {
        "_source", "scanned_at", "sport", "market", "line", "home", "away",
        "kickoff", "side", "book", "odds", "impl_raw", "impl_effective",
        "edge", "edge_gross", "effective_odds", "commission_rate",
        "consensus", "pinnacle_cons", "n_books", "confidence",
        "model_signal", "dispersion", "outlier_z", "devig_method",
        "weight_scheme", "stake", "actual_stake", "result", "pnl",
        "pinnacle_close_prob", "clv_pct",
    }
    assert expected_keys.issubset(sample.keys())
    # Sport label round-trips back from sport_key
    assert sample["sport"] == "EPL"
    # 'pending' DB default → "" for the dashboard
    assert sample["result"] == ""
    # Decimals stringify cleanly
    assert sample["odds"] != ""


def test_get_bets_returns_none_when_disabled(fresh_env, tmp_path):
    from src.storage.repo import BetRepo
    repo = BetRepo(logs_dir=tmp_path)
    assert repo.get_bets() is None


# ---- get_drift ------------------------------------------------------------

def test_get_drift_groups_by_bet(fresh_env, tmp_path):
    db = _make_db()
    repo = _make_repo(db, tmp_path)

    drift_rows = [
        {"captured_at": "2026-05-10 14:00 UTC",
         "home": "Arsenal", "away": "Chelsea",
         "kickoff": "2026-05-10 15:00", "side": "HOME",
         "market": "h2h", "line": "", "book": "bet365",
         "t_minus_min": 60, "your_book_odds": 2.10,
         "pinnacle_odds": 1.95, "n_books": 30, "sport": "EPL"},
        {"captured_at": "2026-05-10 14:45 UTC",
         "home": "Arsenal", "away": "Chelsea",
         "kickoff": "2026-05-10 15:00", "side": "HOME",
         "market": "h2h", "line": "", "book": "bet365",
         "t_minus_min": 15, "your_book_odds": 2.05,
         "pinnacle_odds": 1.90, "n_books": 30, "sport": "EPL"},
    ]
    repo.add_drift_snapshot(drift_rows)
    out = repo.get_drift()
    assert out is not None

    key = ("Arsenal", "Chelsea", "2026-05-10 15:00", "HOME", "h2h", "")
    assert key in out
    assert len(out[key]) == 2
    # Sorted descending by t_minus_min
    assert int(out[key][0]["t_minus_min"]) > int(out[key][1]["t_minus_min"])


# ---- update_bet_settle ----------------------------------------------------

def test_update_bet_settle_writes_to_db(fresh_env, tmp_path):
    db = _make_db()
    repo = _make_repo(db, tmp_path)
    row = _bet_row()
    repo.add_bets([row])

    ok = repo.update_bet_settle(
        scan_date="2026-04-30",
        kickoff="2026-05-10 15:00", home="Arsenal", away="Chelsea",
        market="h2h", line="", side="HOME", book="bet365",
        result="W", actual_stake=20.0, pnl=22.0, odds=2.10,
    )
    assert ok == 1
    db.commit()

    r = db.execute(
        "SELECT result, actual_stake, pnl FROM bets"
    ).fetchone()
    assert r == ("W", 20.0, 22.0)


def test_update_bet_settle_returns_false_when_db_disabled(fresh_env, tmp_path):
    from src.storage.repo import BetRepo
    repo = BetRepo(logs_dir=tmp_path)
    ok = repo.update_bet_settle(
        scan_date="2026-04-30",
        kickoff="2026-05-10 15:00", home="X", away="Y", market="h2h",
        line="", side="HOME", book="bet365",
        result="W", actual_stake=10.0, pnl=5.0,
    )
    assert ok == 0


# ---- /health + dashboard fallback -----------------------------------------

def test_health_endpoint_disabled(fresh_env, tmp_path, monkeypatch):
    """Without DB env, /health → 200 db=disabled."""
    import app as _app
    client = _app.app.test_client()
    rsp = client.get("/health")
    assert rsp.status_code == 200
    body = rsp.get_json()
    assert body["db"] == "disabled"
    assert "csv" not in body


def test_dashboard_renders_when_db_disabled(fresh_env, tmp_path, monkeypatch):
    """Dashboard renders (empty bets list) when DB env is not set."""
    import app as _app
    client = _app.app.test_client()
    rsp = client.get("/")
    assert rsp.status_code == 200
    assert b"Betting Dashboard" in rsp.data
    assert b"DB unreachable" not in rsp.data


def test_dashboard_shows_banner_when_db_down(fresh_env, tmp_path, monkeypatch):
    """When DB is configured but the connect fails, /  shows the banner."""
    monkeypatch.setenv("BETS_DB_WRITE", "1")
    monkeypatch.setenv("AZURE_SQL_DSN", "Driver={Nonexistent};Server=nope;")

    import app as _app
    client = _app.app.test_client()
    rsp = client.get("/")
    assert rsp.status_code == 200
    assert b"DB unreachable" in rsp.data
    rsp = client.get("/health")
    assert rsp.status_code == 503
