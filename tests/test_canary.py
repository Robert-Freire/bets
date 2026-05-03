"""Tests for the canary pre-flight: cheap 1-credit call that detects empty-payload
moments from The Odds API (e.g. the 2026-05-01 incident where 6 league requests
returned 0 fixtures and burned 26 credits)."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# scan_odds raises at import time if ODDS_API_KEY is missing; provide a stub.
os.environ.setdefault("ODDS_API_KEY", "test-key")

import scripts.scan_odds as scan_odds  # noqa: E402


# ── _get_canary_league() ──────────────────────────────────────────────────────

def test_canary_league_env_var_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("CANARY_LEAGUE", "soccer_germany_bundesliga")
    monkeypatch.setattr(scan_odds, "__file__",
                        str(tmp_path / "scripts" / "scan_odds.py"))
    (tmp_path / "config.json").write_text(json.dumps({"canary_league": "soccer_epl"}))
    assert scan_odds._get_canary_league() == "soccer_germany_bundesliga"


def test_canary_league_config_json_used_when_no_env(monkeypatch, tmp_path):
    monkeypatch.delenv("CANARY_LEAGUE", raising=False)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    monkeypatch.setattr(scan_odds, "__file__", str(scripts_dir / "scan_odds.py"))
    (tmp_path / "config.json").write_text(
        json.dumps({"canary_league": "soccer_italy_serie_a"})
    )
    assert scan_odds._get_canary_league() == "soccer_italy_serie_a"


def test_canary_league_default_when_no_env_no_config(monkeypatch, tmp_path):
    monkeypatch.delenv("CANARY_LEAGUE", raising=False)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    monkeypatch.setattr(scan_odds, "__file__", str(scripts_dir / "scan_odds.py"))
    # No config.json present.
    assert scan_odds._get_canary_league() == "soccer_epl"


def test_canary_league_corrupt_config_falls_back_to_default(monkeypatch, tmp_path):
    monkeypatch.delenv("CANARY_LEAGUE", raising=False)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    monkeypatch.setattr(scan_odds, "__file__", str(scripts_dir / "scan_odds.py"))
    (tmp_path / "config.json").write_text("not valid json{{{")
    assert scan_odds._get_canary_league() == "soccer_epl"


# ── _health_check_sports() ────────────────────────────────────────────────────

def test_health_check_returns_active_keys(monkeypatch, capsys):
    fake = [
        {"key": "soccer_epl", "active": True},
        {"key": "soccer_germany_bundesliga", "active": True},
        {"key": "soccer_italy_serie_a", "active": False},  # inactive
        {"key": "tennis_wta_madrid", "active": True},
    ]
    monkeypatch.setattr(scan_odds, "api_get", lambda *a, **kw: (fake, "499"))

    active = scan_odds._health_check_sports()
    assert "soccer_epl" in active
    assert "soccer_italy_serie_a" not in active
    out = capsys.readouterr().out
    assert "[health]" in out
    # The inactive Serie A line is in the football set, so it should be flagged.
    assert "soccer_italy_serie_a" in out


def test_health_check_swallows_errors(monkeypatch, capsys):
    def boom(*a, **kw):
        raise RuntimeError("API down")
    monkeypatch.setattr(scan_odds, "api_get", boom)

    active = scan_odds._health_check_sports()
    assert active == set()
    assert "[health]" in capsys.readouterr().out


# ── _resolve_canary() — misconfiguration guard ────────────────────────────────

def _football_list():
    return [
        ("soccer_epl", "EPL", 20),
        ("soccer_germany_bundesliga", "Bundesliga", 20),
        ("soccer_italy_serie_a", "Serie A", 20),
    ]


def test_resolve_canary_passes_through_when_configured_league_present():
    assert scan_odds._resolve_canary("soccer_germany_bundesliga",
                                     _football_list()) == "soccer_germany_bundesliga"


def test_resolve_canary_falls_back_when_configured_league_missing(capsys):
    # Typo / nonexistent league.
    out = scan_odds._resolve_canary("soccer_brazillll", _football_list())
    assert out == "soccer_epl"  # first football league
    assert "WARN" in capsys.readouterr().out


def test_resolve_canary_falls_back_when_configured_league_is_non_football(capsys):
    # Someone sets CANARY_LEAGUE=basketball_nba — wrong scope, fall back.
    out = scan_odds._resolve_canary("basketball_nba", _football_list())
    assert out == "soccer_epl"
    assert "WARN" in capsys.readouterr().out


def test_resolve_canary_no_op_when_no_football_in_scan():
    # NBA-only or tennis-only scans: no football, nothing to reorder, no warning.
    out = scan_odds._resolve_canary("soccer_epl", football=[])
    assert out == "soccer_epl"


# ── canary_verdict() — pure helper unit tests ─────────────────────────────────
# The in-loop canary wiring in scan_odds.main() is not unit-tested here (see
# existing comment below).  canary_verdict() encapsulates the calendar-aware
# decision so the logic itself is fully testable.

import sqlite3

import src.data.fixture_calendar as _fc

SCHEMA_SQLITE = ROOT / "src" / "storage" / "schema_sqlite.sql"


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQLITE.read_text())
    conn.commit()
    return conn


def _make_fc_repo(conn):
    from src.storage.repo import FixtureRepo
    return FixtureRepo(conn=conn)


def _seed(conn, fixtures):
    from src.storage.repo import FixtureRepo
    repo = FixtureRepo(conn=conn)
    repo.upsert_many(fixtures)
    return repo


def _fx(sport_key="soccer_epl", kickoff="2026-05-10T14:00:00+00:00"):
    return {"sport_key": sport_key, "league": "EPL", "home": "A", "away": "B",
            "kickoff_utc": kickoff, "source": "fdco", "status": "scheduled"}


@pytest.fixture(autouse=True)
def _reset_fc_repo(monkeypatch):
    monkeypatch.setattr(_fc, "_repo", None)


def test_canary_verdict_alert_when_fixtures_expected(monkeypatch):
    conn = _make_db()
    repo = _seed(conn, [_fx()])
    monkeypatch.setattr(_fc, "_repo", repo)
    from datetime import date
    verdict, near = _fc.canary_verdict("soccer_epl", date(2026, 5, 10), date(2026, 5, 12))
    assert verdict == "alert"
    assert len(near) == 1


def test_canary_verdict_silent_when_no_fixtures(monkeypatch):
    conn = _make_db()
    repo = _seed(conn, [_fx(sport_key="soccer_germany_bundesliga")])
    monkeypatch.setattr(_fc, "_repo", repo)
    from datetime import date
    verdict, near = _fc.canary_verdict("soccer_epl", date(2026, 5, 10), date(2026, 5, 12))
    assert verdict == "silent"
    assert near == []


def test_canary_verdict_unknown_when_db_disabled(monkeypatch):
    from src.storage.repo import FixtureRepo
    monkeypatch.setattr(_fc, "_repo", FixtureRepo(dsn=None))
    from datetime import date
    verdict, near = _fc.canary_verdict("soccer_epl", date(2026, 5, 10), date(2026, 5, 12))
    assert verdict == "unknown"
    assert near == []


def test_canary_verdict_unknown_when_no_ingests(monkeypatch):
    conn = _make_db()
    monkeypatch.setattr(_fc, "_repo", _make_fc_repo(conn))
    from datetime import date
    verdict, near = _fc.canary_verdict("soccer_epl", date(2026, 5, 10), date(2026, 5, 12))
    assert verdict == "unknown"


def test_canary_verdict_unknown_when_calendar_stale(monkeypatch):
    from datetime import timedelta
    conn = _make_db()
    repo = _seed(conn, [_fx()])
    stale_ts = (datetime.utcnow() - timedelta(days=9)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    conn.execute("UPDATE fixtures SET ingested_at = ?", (stale_ts,))
    conn.commit()
    monkeypatch.setattr(_fc, "_repo", repo)
    from datetime import date
    verdict, near = _fc.canary_verdict("soccer_epl", date(2026, 5, 10), date(2026, 5, 12))
    assert verdict == "unknown"


def test_canary_verdict_silent_when_calendar_empty_but_fresh(monkeypatch):
    """Fresh calendar with no matching fixtures → 'silent', not 'unknown'."""
    conn = _make_db()
    repo = _seed(conn, [_fx(sport_key="soccer_germany_bundesliga")])
    monkeypatch.setattr(_fc, "_repo", repo)
    from datetime import date
    verdict, near = _fc.canary_verdict("soccer_epl", date(2026, 5, 10), date(2026, 5, 12))
    assert verdict == "silent"


# In-loop canary trip is exercised by the scanner end-to-end and is not unit
# tested here — main() reorders football so the canary league fetches first,
# then sets a skip flag if it returned 0 events. See scan_odds.main() for the
# logic; CLAUDE.md "How the scanner works" documents the behaviour.


# ── _check_calendar_match() — observation-only postponement detector ──────────

def _event(commence_iso: str) -> dict:
    return {"commence_time": commence_iso}


def test_check_calendar_match_silent_for_non_football(capsys, monkeypatch):
    # Calendar has fixtures but sport is NBA → check is skipped entirely
    monkeypatch.setattr(scan_odds, "_get_calendar_fixtures",
                        lambda *a, **kw: [{"sport_key": "x"}])
    scan_odds._check_calendar_match("basketball_nba", "NBA", [])
    assert "[mismatch]" not in capsys.readouterr().out


def test_check_calendar_match_silent_when_no_calendar_fixtures(capsys, monkeypatch):
    # Calendar absent / no fixtures expected → nothing to compare, no log
    monkeypatch.setattr(scan_odds, "_get_calendar_fixtures", lambda *a, **kw: [])
    scan_odds._check_calendar_match("soccer_epl", "EPL", [_event("2099-01-01T14:00:00Z")])
    assert "[mismatch]" not in capsys.readouterr().out


def test_check_calendar_match_silent_when_counts_agree(capsys, monkeypatch):
    from datetime import datetime, timezone
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT14:00:00Z")
    monkeypatch.setattr(scan_odds, "_get_calendar_fixtures",
                        lambda *a, **kw: [{"sport_key": "soccer_epl"}])
    scan_odds._check_calendar_match("soccer_epl", "EPL", [_event(today_iso)])
    assert "[mismatch]" not in capsys.readouterr().out


def test_check_calendar_match_logs_when_api_short(capsys, monkeypatch):
    # Calendar expects 3, API returns 1 in window → log mismatch
    monkeypatch.setattr(
        scan_odds, "_get_calendar_fixtures",
        lambda *a, **kw: [{"sport_key": "soccer_epl"}] * 3,
    )
    from datetime import datetime, timezone
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT14:00:00Z")
    scan_odds._check_calendar_match("soccer_epl", "EPL", [_event(today_iso)])
    out = capsys.readouterr().out
    assert "[mismatch]" in out
    assert "API 1" in out
    assert "calendar 3" in out


def test_check_calendar_match_ignores_api_events_outside_window(capsys, monkeypatch):
    # API returns 5 events but only 1 is within the 2-day window;
    # calendar expects 1 → counts agree, no log
    monkeypatch.setattr(
        scan_odds, "_get_calendar_fixtures",
        lambda *a, **kw: [{"sport_key": "soccer_epl"}],
    )
    from datetime import datetime, timezone, timedelta
    today = datetime.now(timezone.utc)
    in_window = today.strftime("%Y-%m-%dT14:00:00Z")
    far = (today + timedelta(days=10)).strftime("%Y-%m-%dT14:00:00Z")
    events = [_event(in_window)] + [_event(far)] * 4
    scan_odds._check_calendar_match("soccer_epl", "EPL", events)
    assert "[mismatch]" not in capsys.readouterr().out


def test_check_calendar_match_tolerates_bad_commence_time(capsys, monkeypatch):
    # Malformed commence_time → skipped silently, doesn't crash
    monkeypatch.setattr(
        scan_odds, "_get_calendar_fixtures",
        lambda *a, **kw: [{"sport_key": "soccer_epl"}],
    )
    scan_odds._check_calendar_match("soccer_epl", "EPL", [_event("not-a-date")])
    out = capsys.readouterr().out
    # Should log mismatch (API 0 vs calendar 1) without raising
    assert "[mismatch]" in out
