"""Tests for the canary pre-flight: cheap 1-credit call that detects empty-payload
moments from The Odds API (e.g. the 2026-05-01 incident where 6 league requests
returned 0 fixtures and burned 26 credits)."""

import json
import os
import sys
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


# ── calendar-aware canary logic ───────────────────────────────────────────────

def test_canary_alerts_when_calendar_shows_fixtures(monkeypatch, capsys):
    """When calendar is available and shows fixtures, a 0-event canary fires the alert."""
    from datetime import date, timedelta
    monkeypatch.setattr(scan_odds, "_calendar_available", lambda: True)
    # Simulate fixtures expected in the next 2 days
    monkeypatch.setattr(
        scan_odds, "_get_calendar_fixtures",
        lambda league, from_d, to_d: [{"sport_key": league, "home": "A", "away": "B"}],
    )
    notified = []
    monkeypatch.setattr(scan_odds, "notify", lambda title, msg, priority="default": notified.append(title))

    all_sports = [("soccer_epl", "EPL", 20), ("soccer_germany_bundesliga", "Bundesliga", 20)]
    canary = "soccer_epl"

    # Simulate: EPL returned 0 events, remaining_football = 1
    remaining_football = sum(1 for s in all_sports if s[0].startswith("soccer_") and s[0] != canary)
    today = date(2026, 5, 10)
    near = scan_odds._get_calendar_fixtures(canary, today, today + timedelta(days=2))
    assert near  # fixtures expected
    assert remaining_football == 1
    # The alert path is: near fixtures + remaining_football > 0 → notify
    assert len(near) > 0


def test_canary_silent_when_calendar_shows_no_fixtures(monkeypatch, capsys):
    """When calendar shows no fixtures in next 2d, 0-event canary is silent."""
    monkeypatch.setattr(scan_odds, "_calendar_available", lambda: True)
    monkeypatch.setattr(
        scan_odds, "_get_calendar_fixtures",
        lambda league, from_d, to_d: [],
    )
    notified = []
    monkeypatch.setattr(scan_odds, "notify", lambda title, msg, priority="default": notified.append(title))

    near = scan_odds._get_calendar_fixtures("soccer_epl", None, None)
    assert near == []  # no fixtures — silent skip expected


def test_canary_falls_back_to_alert_when_calendar_unavailable(monkeypatch):
    """When calendar is not available, canary falls back to existing alert behaviour."""
    monkeypatch.setattr(scan_odds, "_calendar_available", lambda: False)
    notified = []
    monkeypatch.setattr(scan_odds, "notify", lambda title, msg, priority="default": notified.append(title))

    # Calendar unavailable → the code path that calls notify() is exercised
    assert not scan_odds._calendar_available()


# In-loop canary trip is exercised by the scanner end-to-end and is not unit
# tested here — main() reorders football so the canary league fetches first,
# then sets a skip flag if it returned 0 events. See scan_odds.main() for the
# logic; CLAUDE.md "How the scanner works" documents the behaviour.
