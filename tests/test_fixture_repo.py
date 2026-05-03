"""Tests for src/storage/repo.py FixtureRepo."""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime, timedelta
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


def _make_repo(conn):
    from src.storage.repo import FixtureRepo
    return FixtureRepo(conn=conn)


def _fx(sport_key="soccer_epl", kickoff="2026-05-10T14:00:00+00:00",
        home="Arsenal", away="Chelsea"):
    return {
        "sport_key": sport_key, "league": "EPL",
        "home": home, "away": away, "kickoff_utc": kickoff,
        "source": "fdco", "status": "scheduled",
    }


# ── db_enabled ────────────────────────────────────────────────────────────────

def test_db_enabled_false_when_dsn_none():
    from src.storage.repo import FixtureRepo
    repo = FixtureRepo(dsn=None)
    assert not repo.db_enabled


def test_db_enabled_true_with_injected_conn():
    conn = _make_db()
    repo = _make_repo(conn)
    assert repo.db_enabled


# ── upsert_many ───────────────────────────────────────────────────────────────

def test_upsert_many_inserts_rows():
    conn = _make_db()
    repo = _make_repo(conn)
    n = repo.upsert_many([_fx()])
    assert n == 1
    assert conn.execute("SELECT COUNT(*) FROM fixtures").fetchone()[0] == 1


def test_upsert_many_is_idempotent():
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([_fx()])
    repo.upsert_many([_fx()])
    assert conn.execute("SELECT COUNT(*) FROM fixtures").fetchone()[0] == 1


def test_upsert_many_sets_ingested_at():
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([_fx()])
    val = conn.execute("SELECT ingested_at FROM fixtures").fetchone()[0]
    assert val is not None


def test_upsert_many_updates_ingested_at_on_re_upsert():
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([_fx()])
    ts1 = conn.execute("SELECT ingested_at FROM fixtures").fetchone()[0]
    import time as _time
    _time.sleep(0.01)
    repo.upsert_many([_fx()])
    ts2 = conn.execute("SELECT ingested_at FROM fixtures").fetchone()[0]
    assert ts2 >= ts1


def test_upsert_many_deduplicates_by_uuid():
    """Two fixtures differing only by kickoff minute (same date) are one row."""
    conn = _make_db()
    repo = _make_repo(conn)
    f1 = _fx(kickoff="2026-05-10T14:00:00+00:00")
    f2 = _fx(kickoff="2026-05-10T14:05:00+00:00")  # same date
    repo.upsert_many([f1, f2])
    assert conn.execute("SELECT COUNT(*) FROM fixtures").fetchone()[0] == 1


def test_upsert_many_sets_source_and_status():
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([_fx()])
    row = conn.execute("SELECT source, status FROM fixtures").fetchone()
    assert row[0] == "fdco"
    assert row[1] == "scheduled"


def test_upsert_many_returns_zero_for_empty_input():
    conn = _make_db()
    repo = _make_repo(conn)
    assert repo.upsert_many([]) == 0


def test_upsert_many_noop_when_db_disabled():
    from src.storage.repo import FixtureRepo
    repo = FixtureRepo(dsn=None)
    assert repo.upsert_many([_fx()]) == 0


# ── get_fixtures ──────────────────────────────────────────────────────────────

def test_get_fixtures_returns_matching_rows():
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([_fx()])
    rows = repo.get_fixtures("soccer_epl", date(2026, 5, 10), date(2026, 5, 10))
    assert len(rows) == 1
    assert rows[0]["home"] == "Arsenal"


def test_get_fixtures_filters_by_sport_key():
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([
        _fx(sport_key="soccer_epl"),
        _fx(sport_key="soccer_germany_bundesliga", home="Bayern", away="Dortmund"),
    ])
    rows = repo.get_fixtures("soccer_epl", date(2026, 5, 10), date(2026, 5, 10))
    assert len(rows) == 1
    assert rows[0]["sport_key"] == "soccer_epl"


def test_get_fixtures_inclusive_date_range():
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([
        _fx(kickoff="2026-05-10T14:00:00+00:00"),
        _fx(kickoff="2026-05-11T14:00:00+00:00", home="Liverpool", away="Man City"),
        _fx(kickoff="2026-05-12T14:00:00+00:00", home="Everton", away="Spurs"),
    ])
    rows = repo.get_fixtures("soccer_epl", date(2026, 5, 10), date(2026, 5, 11))
    assert len(rows) == 2


def test_get_fixtures_sorted_by_kickoff():
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([
        _fx(kickoff="2026-05-10T16:00:00+00:00", home="Late", away="Team"),
        _fx(kickoff="2026-05-10T12:00:00+00:00", home="Early", away="Team"),
    ])
    rows = repo.get_fixtures("soccer_epl", date(2026, 5, 10), date(2026, 5, 10))
    assert rows[0]["home"] == "Early"
    assert rows[1]["home"] == "Late"


def test_get_fixtures_returns_empty_when_disabled():
    from src.storage.repo import FixtureRepo
    repo = FixtureRepo(dsn=None)
    assert repo.get_fixtures("soccer_epl", date(2026, 5, 10), date(2026, 5, 10)) == []


# ── count_fixtures ────────────────────────────────────────────────────────────

def test_count_fixtures_returns_correct_count():
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([
        _fx(kickoff="2026-05-10T14:00:00+00:00"),
        _fx(kickoff="2026-05-10T16:00:00+00:00", home="Liverpool", away="Man City"),
    ])
    assert repo.count_fixtures("soccer_epl", date(2026, 5, 10), date(2026, 5, 10)) == 2


def test_count_fixtures_returns_zero_when_disabled():
    from src.storage.repo import FixtureRepo
    repo = FixtureRepo(dsn=None)
    assert repo.count_fixtures("soccer_epl", date(2026, 5, 10), date(2026, 5, 10)) == 0


# ── count_ingested_fixtures ───────────────────────────────────────────────────

def test_count_ingested_fixtures_only_counts_repo_rows():
    """BetRepo fixture rows (ingested_at=NULL) are not counted."""
    conn = _make_db()
    repo = _make_repo(conn)
    # Manually insert a row without ingested_at (simulates a BetRepo row)
    conn.execute(
        "INSERT INTO fixtures (id, sport_key, home, away, kickoff_utc) "
        "VALUES ('bet-row', 'soccer_epl', 'A', 'B', '2026-05-10T14:00:00+00:00')"
    )
    conn.commit()
    assert repo.count_ingested_fixtures() == 0

    repo.upsert_many([_fx()])
    assert repo.count_ingested_fixtures() == 1


# ── latest_ingest_at ──────────────────────────────────────────────────────────

def test_latest_ingest_at_none_when_no_rows():
    conn = _make_db()
    repo = _make_repo(conn)
    assert repo.latest_ingest_at() is None


def test_latest_ingest_at_returns_datetime_after_upsert():
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([_fx()])
    ts = repo.latest_ingest_at()
    assert ts is not None
    assert isinstance(ts, datetime)


def test_latest_ingest_at_updates_after_re_upsert():
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([_fx()])
    ts1 = repo.latest_ingest_at()
    import time as _time
    _time.sleep(0.02)
    repo.upsert_many([_fx()])
    ts2 = repo.latest_ingest_at()
    assert ts2 >= ts1


def test_latest_ingest_at_none_when_disabled():
    from src.storage.repo import FixtureRepo
    repo = FixtureRepo(dsn=None)
    assert repo.latest_ingest_at() is None
