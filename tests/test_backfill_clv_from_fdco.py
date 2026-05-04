"""Tests for scripts/backfill_clv_from_fdco.py (DB-only rewrite, Phase S.4)."""
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQLITE = ROOT / "src" / "storage" / "schema_sqlite.sql"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backfill_clv_from_fdco import (
    _h2h_pin_prob,
    _parse_fdco_date,
    _settle_from_fdco,
    _pnl,
    _load_fdco_index,
)
from src.betting.devig import shin


# ── helpers ───────────────────────────────────────────────────────────────────

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


def _write_fdco_csv(path: Path):
    path.write_text(
        "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,"
        "PSCH,PSCD,PSCA,PC>2.5,PC<2.5\n"
        # Use a past date (2026-04-26) so it's always before today
        "E0,26/04/2026,15:00,Arsenal,Chelsea,2,1,H,2.10,3.50,3.40,1.85,2.05\n"
        "E0,26/04/2026,17:30,Liverpool,Man City,1,1,D,2.50,3.30,2.80,1.90,2.00\n"
        "E0,27/04/2026,14:00,Tottenham,West Ham,0,2,A,1.80,3.80,4.50,1.95,1.95\n"
        # Cancelled: blank odds
        "E0,28/04/2026,15:00,Brighton,Newcastle,,,,,,,\n"
    )


def _bet_row_dict(home="Arsenal", away="Chelsea", kickoff="2026-05-10 15:00",
                  side="HOME", book="bet365", market="h2h", line=""):
    return {
        "scanned_at": "2026-05-01 10:00 UTC", "sport": "EPL",
        "market": market, "line": line,
        "home": home, "away": away, "kickoff": kickoff,
        "side": side, "book": book, "odds": 2.20,
        "impl_raw": 0.48, "impl_effective": 0.50,
        "edge": 0.04, "edge_gross": 0.04,
        "effective_odds": 2.20, "commission_rate": 0.0,
        "consensus": 0.52, "pinnacle_cons": 0.50,
        "n_books": 28, "confidence": "MED",
        "model_signal": "+0.01", "dispersion": 0.02,
        "outlier_z": 0.1, "devig_method": "shin",
        "weight_scheme": "uniform", "stake": 5.0, "result": "",
    }


@pytest.fixture
def fresh_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("BETS_") or k.startswith("AZURE_SQL_"):
            monkeypatch.delenv(k, raising=False)
    return monkeypatch


# ── Pure function tests (preserved) ──────────────────────────────────────────

def test_devig_math_matches_shin_directly():
    row = {"PSCH": "2.10", "PSCD": "3.50", "PSCA": "3.40"}
    fair = shin([1 / 2.10, 1 / 3.50, 1 / 3.40])
    assert _h2h_pin_prob(row, "HOME") == pytest.approx(fair[0])
    assert _h2h_pin_prob(row, "DRAW") == pytest.approx(fair[1])
    assert _h2h_pin_prob(row, "AWAY") == pytest.approx(fair[2])


def test_parse_fdco_date():
    assert _parse_fdco_date("10/05/2026") == datetime(2026, 5, 10, tzinfo=timezone.utc)
    assert _parse_fdco_date("10/05/26") == datetime(2026, 5, 10, tzinfo=timezone.utc)
    assert _parse_fdco_date("") is None


def test_load_fdco_index(tmp_path):
    csv_path = tmp_path / "E0_2526.csv"
    _write_fdco_csv(csv_path)
    idx = _load_fdco_index(csv_path)
    assert ("2026-04-26", "Arsenal", "Chelsea") in idx
    assert ("2026-04-26", "Liverpool", "Man City") in idx


def test_settle_from_fdco_h2h():
    row = {"FTR": "H", "FTHG": "2", "FTAG": "1"}
    assert _settle_from_fdco(row, "h2h", "HOME") == "W"
    assert _settle_from_fdco(row, "h2h", "AWAY") == "L"


def test_pnl_win():
    assert _pnl(10.0, 2.0, "W") == pytest.approx(10.0)


def test_pnl_loss():
    assert _pnl(10.0, 2.0, "L") == pytest.approx(-10.0)


def test_pnl_void():
    assert _pnl(10.0, 2.0, "void") == 0.0


# ── T9: exit without BETS_DB_WRITE ───────────────────────────────────────────

def test_main_exits_without_db_env(fresh_env, monkeypatch, capsys):
    """main() must exit non-zero when BETS_DB_WRITE is not set."""
    monkeypatch.setattr(sys, "argv", ["backfill_clv_from_fdco.py"])
    from scripts import backfill_clv_from_fdco as bf
    with pytest.raises(SystemExit) as exc_info:
        bf.main()
    assert exc_info.value.code != 0
    err = capsys.readouterr().err
    assert "BETS_DB_WRITE" in err


# ── Integration: DB-backed settle via main() ──────────────────────────────────

def test_main_settles_paper_bet_via_db(fresh_env, monkeypatch, tmp_path):
    """With DB enabled + FDCO CSV, a past-kickoff paper_bet gets settled."""
    monkeypatch.setenv("BETS_DB_WRITE", "1")

    # Set up FDCO CSV
    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True)
    _write_fdco_csv(raw / "E0_2526.csv")

    db = _make_db()
    helper = _SqliteRepo(db, tmp_path)
    repo = helper.repo

    base_row = {
        **_bet_row_dict(home="Arsenal", away="Chelsea",
                        kickoff="2026-04-26 15:00", side="HOME"),
        "strategy": "A_production",
        "code_sha": "", "strategy_config_hash": "",
        "pinnacle_close_prob": "", "clv_pct": "",
    }
    repo.add_paper_bets("A_production", [base_row])

    # Verify it's pending
    assert db.execute("SELECT result FROM paper_bets").fetchone()[0] == "pending"

    from scripts import backfill_clv_from_fdco as bf
    monkeypatch.setattr(bf, "_RAW_DIR", raw)
    monkeypatch.setattr(bf, "_refresh_csv",
                        lambda league: raw / f"{league}_2526.csv"
                        if (raw / f"{league}_2526.csv").exists() else None)
    monkeypatch.setattr(sys, "argv", ["backfill_clv_from_fdco.py", "--leagues", "E0"])

    # Inject the pre-wired SQLite repo; patch close() to preserve conn for assertions
    repo.close = lambda: None
    monkeypatch.setattr(bf, "_make_repo", lambda BetRepoClass: repo)

    bf.main()

    row = db.execute(
        "SELECT result, pnl, pinnacle_close_prob FROM paper_bets"
    ).fetchone()
    assert row[0] == "W"       # Arsenal won (FTR=H, HOME bet)
    assert row[1] is not None  # pnl populated
    assert row[2] is not None  # CLV populated


def _run_backfill_with_repo(monkeypatch, tmp_path, raw, repo, *argv):
    """Helper: inject repo into backfill main() and run with given argv."""
    from scripts import backfill_clv_from_fdco as bf
    monkeypatch.setattr(bf, "_RAW_DIR", raw)
    monkeypatch.setattr(bf, "_refresh_csv",
                        lambda league: raw / f"{league}_2526.csv"
                        if (raw / f"{league}_2526.csv").exists() else None)
    monkeypatch.setattr(sys, "argv", ["backfill_clv_from_fdco.py", *argv])
    repo.close = lambda: None  # prevent closing the shared SQLite conn
    monkeypatch.setattr(bf, "_make_repo", lambda BetRepoClass: repo)
    bf.main()


def test_dry_run_makes_no_db_writes(fresh_env, monkeypatch, tmp_path):
    """--dry-run must not write anything to the DB."""
    monkeypatch.setenv("BETS_DB_WRITE", "1")

    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True)
    _write_fdco_csv(raw / "E0_2526.csv")

    db = _make_db()
    helper = _SqliteRepo(db, tmp_path)
    repo = helper.repo

    base_row = {
        **_bet_row_dict(home="Arsenal", away="Chelsea",
                        kickoff="2026-04-26 15:00", side="HOME"),
        "strategy": "A_production",
        "code_sha": "", "strategy_config_hash": "",
        "pinnacle_close_prob": "", "clv_pct": "",
    }
    repo.add_paper_bets("A_production", [base_row])

    _run_backfill_with_repo(monkeypatch, tmp_path, raw, repo, "--dry-run", "--leagues", "E0")

    # result must still be 'pending' — dry-run skipped the DB write
    assert db.execute("SELECT result FROM paper_bets").fetchone()[0] == "pending"


def test_future_kickoff_not_settled(fresh_env, monkeypatch, tmp_path):
    """Fixtures whose kickoff is in the future must never be settled."""
    monkeypatch.setenv("BETS_DB_WRITE", "1")

    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True)
    _write_fdco_csv(raw / "E0_2526.csv")

    db = _make_db()
    helper = _SqliteRepo(db, tmp_path)
    repo = helper.repo

    # Future kickoff — must not appear in iter_unsettled_or_no_clv
    base_row = {
        **_bet_row_dict(home="Arsenal", away="Chelsea",
                        kickoff="2099-01-01 15:00", side="HOME"),
        "strategy": "A_production",
        "code_sha": "", "strategy_config_hash": "",
        "pinnacle_close_prob": "", "clv_pct": "",
    }
    repo.add_paper_bets("A_production", [base_row])

    _run_backfill_with_repo(monkeypatch, tmp_path, raw, repo, "--leagues", "E0")

    assert db.execute("SELECT result FROM paper_bets").fetchone()[0] == "pending"


def test_btts_market_skipped(fresh_env, monkeypatch, tmp_path):
    """BTTS market rows are skipped — no FDCO column for them."""
    monkeypatch.setenv("BETS_DB_WRITE", "1")

    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True)
    _write_fdco_csv(raw / "E0_2526.csv")

    db = _make_db()
    helper = _SqliteRepo(db, tmp_path)
    repo = helper.repo

    base_row = {
        **_bet_row_dict(home="Arsenal", away="Chelsea",
                        kickoff="2026-04-26 15:00", side="YES",
                        market="btts"),
        "strategy": "A_production",
        "code_sha": "", "strategy_config_hash": "",
        "pinnacle_close_prob": "", "clv_pct": "",
    }
    repo.add_paper_bets("A_production", [base_row])

    _run_backfill_with_repo(monkeypatch, tmp_path, raw, repo, "--leagues", "E0")

    assert db.execute("SELECT result FROM paper_bets").fetchone()[0] == "pending"
