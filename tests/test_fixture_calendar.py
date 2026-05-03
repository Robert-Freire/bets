"""Tests for src/data/fixture_calendar.py (DB-backed via FixtureRepo)."""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
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


def _make_fixture(sport_key: str, kickoff_utc: str, home: str = "Home FC",
                  away: str = "Away FC") -> dict:
    return {
        "sport_key": sport_key,
        "league": "Test League",
        "home": home,
        "away": away,
        "kickoff_utc": kickoff_utc,
        "source": "fdco",
        "status": "scheduled",
    }


@pytest.fixture(autouse=True)
def _reset_fc_repo(monkeypatch):
    """Reset the module-level _repo after each test."""
    import src.data.fixture_calendar as fc
    monkeypatch.setattr(fc, "_repo", None)


def _inject_repo(monkeypatch, repo):
    import src.data.fixture_calendar as fc
    monkeypatch.setattr(fc, "_repo", repo)


# ── calendar_available ────────────────────────────────────────────────────────

def test_calendar_available_false_when_db_disabled(monkeypatch):
    import src.data.fixture_calendar as fc
    from src.storage.repo import FixtureRepo
    # repo with no DB configured
    monkeypatch.setattr(fc, "_repo", FixtureRepo(dsn=None))
    assert not fc.calendar_available()


def test_calendar_available_false_when_no_ingests(monkeypatch):
    import src.data.fixture_calendar as fc
    conn = _make_db()
    _inject_repo(monkeypatch, _make_repo(conn))
    assert not fc.calendar_available()


def test_calendar_available_true_when_fresh_ingest(monkeypatch):
    import src.data.fixture_calendar as fc
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([_make_fixture("soccer_epl", "2026-05-10T14:00:00+00:00")])
    _inject_repo(monkeypatch, repo)
    assert fc.calendar_available()


def test_calendar_available_false_when_stale(monkeypatch):
    import src.data.fixture_calendar as fc
    conn = _make_db()
    repo = _make_repo(conn)
    # Write a fixture, then manually backdate ingested_at to 9 days ago
    repo.upsert_many([_make_fixture("soccer_epl", "2026-05-10T14:00:00+00:00")])
    stale_ts = (datetime.utcnow() - timedelta(days=9)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    conn.execute("UPDATE fixtures SET ingested_at = ?", (stale_ts,))
    conn.commit()
    _inject_repo(monkeypatch, repo)
    assert not fc.calendar_available()


# ── has_fixtures ──────────────────────────────────────────────────────────────

def test_has_fixtures_true_when_present(monkeypatch):
    import src.data.fixture_calendar as fc
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([_make_fixture("soccer_epl", "2026-05-10T14:00:00+00:00")])
    _inject_repo(monkeypatch, repo)
    assert fc.has_fixtures("soccer_epl", date(2026, 5, 10))


def test_has_fixtures_false_when_wrong_league(monkeypatch):
    import src.data.fixture_calendar as fc
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([_make_fixture("soccer_epl", "2026-05-10T14:00:00+00:00")])
    _inject_repo(monkeypatch, repo)
    assert not fc.has_fixtures("soccer_germany_bundesliga", date(2026, 5, 10))


def test_has_fixtures_false_when_wrong_date(monkeypatch):
    import src.data.fixture_calendar as fc
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([_make_fixture("soccer_epl", "2026-05-10T14:00:00+00:00")])
    _inject_repo(monkeypatch, repo)
    assert not fc.has_fixtures("soccer_epl", date(2026, 5, 11))


def test_has_fixtures_accepts_string_date(monkeypatch):
    import src.data.fixture_calendar as fc
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([_make_fixture("soccer_epl", "2026-05-10T14:00:00+00:00")])
    _inject_repo(monkeypatch, repo)
    assert fc.has_fixtures("soccer_epl", "2026-05-10")


def test_has_fixtures_false_when_db_disabled(monkeypatch):
    import src.data.fixture_calendar as fc
    from src.storage.repo import FixtureRepo
    monkeypatch.setattr(fc, "_repo", FixtureRepo(dsn=None))
    assert not fc.has_fixtures("soccer_epl", date(2026, 5, 10))


# ── get_fixtures ──────────────────────────────────────────────────────────────

def test_get_fixtures_filters_by_league_and_date(monkeypatch):
    import src.data.fixture_calendar as fc
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([
        _make_fixture("soccer_epl", "2026-05-10T14:00:00+00:00", "Arsenal", "Chelsea"),
        _make_fixture("soccer_epl", "2026-05-17T14:00:00+00:00", "Liverpool", "Man City"),
        _make_fixture("soccer_germany_bundesliga", "2026-05-10T13:30:00+00:00"),
    ])
    _inject_repo(monkeypatch, repo)
    result = fc.get_fixtures("soccer_epl", date(2026, 5, 10), date(2026, 5, 10))
    assert len(result) == 1
    assert result[0]["home"] == "Arsenal"


def test_get_fixtures_accepts_string_dates(monkeypatch):
    import src.data.fixture_calendar as fc
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([_make_fixture("soccer_epl", "2026-05-10T14:00:00+00:00")])
    _inject_repo(monkeypatch, repo)
    result = fc.get_fixtures("soccer_epl", "2026-05-10", "2026-05-10")
    assert len(result) == 1


def test_get_fixtures_inclusive_range(monkeypatch):
    import src.data.fixture_calendar as fc
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([
        _make_fixture("soccer_epl", "2026-05-10T14:00:00+00:00"),
        _make_fixture("soccer_epl", "2026-05-11T14:00:00+00:00", "Liverpool", "Man City"),
        _make_fixture("soccer_epl", "2026-05-12T14:00:00+00:00", "Everton", "Spurs"),
    ])
    _inject_repo(monkeypatch, repo)
    result = fc.get_fixtures("soccer_epl", date(2026, 5, 10), date(2026, 5, 11))
    assert len(result) == 2


def test_get_fixtures_sorted_by_kickoff(monkeypatch):
    import src.data.fixture_calendar as fc
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([
        _make_fixture("soccer_epl", "2026-05-10T16:00:00+00:00", "C", "D"),
        _make_fixture("soccer_epl", "2026-05-10T13:00:00+00:00", "A", "B"),
    ])
    _inject_repo(monkeypatch, repo)
    result = fc.get_fixtures("soccer_epl", date(2026, 5, 10), date(2026, 5, 10))
    assert result[0]["home"] == "A"
    assert result[1]["home"] == "C"


def test_get_fixtures_returns_empty_when_none_match(monkeypatch):
    import src.data.fixture_calendar as fc
    conn = _make_db()
    repo = _make_repo(conn)
    # No fixtures inserted
    _inject_repo(monkeypatch, repo)
    assert fc.get_fixtures("soccer_epl", date(2026, 5, 10), date(2026, 5, 12)) == []


# ── canary_verdict ────────────────────────────────────────────────────────────

def test_canary_verdict_alert_when_fixtures_expected(monkeypatch):
    import src.data.fixture_calendar as fc
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([_make_fixture("soccer_epl", "2026-05-10T14:00:00+00:00", "A", "B")])
    _inject_repo(monkeypatch, repo)
    verdict, near = fc.canary_verdict("soccer_epl", date(2026, 5, 10), date(2026, 5, 12))
    assert verdict == "alert"
    assert len(near) == 1


def test_canary_verdict_silent_when_no_fixtures(monkeypatch):
    import src.data.fixture_calendar as fc
    conn = _make_db()
    repo = _make_repo(conn)
    repo.upsert_many([_make_fixture("soccer_germany_bundesliga",
                                    "2026-05-10T14:00:00+00:00")])
    _inject_repo(monkeypatch, repo)
    verdict, near = fc.canary_verdict("soccer_epl", date(2026, 5, 10), date(2026, 5, 12))
    assert verdict == "silent"
    assert near == []


def test_canary_verdict_unknown_when_db_disabled(monkeypatch):
    import src.data.fixture_calendar as fc
    from src.storage.repo import FixtureRepo
    monkeypatch.setattr(fc, "_repo", FixtureRepo(dsn=None))
    verdict, near = fc.canary_verdict("soccer_epl", date(2026, 5, 10), date(2026, 5, 12))
    assert verdict == "unknown"
    assert near == []


def test_canary_verdict_unknown_when_no_ingests(monkeypatch):
    import src.data.fixture_calendar as fc
    conn = _make_db()
    _inject_repo(monkeypatch, _make_repo(conn))
    verdict, near = fc.canary_verdict("soccer_epl", date(2026, 5, 10), date(2026, 5, 12))
    assert verdict == "unknown"


def test_canary_verdict_silent_when_calendar_empty(monkeypatch):
    """Empty-but-fresh calendar (no fixtures) returns 'silent', not 'unknown'."""
    import src.data.fixture_calendar as fc
    conn = _make_db()
    repo = _make_repo(conn)
    # Ingest one fixture so calendar_available() returns True, then delete it
    repo.upsert_many([_make_fixture("soccer_epl", "2026-05-10T14:00:00+00:00")])
    conn.execute("DELETE FROM fixtures")
    # Manually insert a row with ingested_at to keep calendar_available True
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    conn.execute(
        "INSERT INTO fixtures (id, sport_key, home, away, kickoff_utc, ingested_at) "
        "VALUES ('sentinel', 'soccer_epl', 'A', 'B', '2026-05-10T14:00:00+00:00', ?)",
        (now,),
    )
    conn.commit()
    _inject_repo(monkeypatch, repo)
    # Query for a different league → no fixtures → 'silent'
    verdict, near = fc.canary_verdict("soccer_germany_bundesliga",
                                       date(2026, 5, 10), date(2026, 5, 12))
    assert verdict == "silent"
