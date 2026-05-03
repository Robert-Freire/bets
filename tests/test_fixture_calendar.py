"""Tests for src/data/fixture_calendar.py."""
from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

import pytest


def _write_calendar(tmp_path: Path, fixtures: list[dict]) -> Path:
    cal = tmp_path / "logs" / "fixture_calendar.json"
    cal.parent.mkdir(parents=True, exist_ok=True)
    cal.write_text(json.dumps({"generated_at": "2026-05-03T02:00:00Z", "fixtures": fixtures}))
    return cal


def _make_fixture(sport_key: str, kickoff_utc: str, home: str = "Home FC", away: str = "Away FC") -> dict:
    return {
        "sport_key": sport_key,
        "league": "Test League",
        "home": home,
        "away": away,
        "kickoff_utc": kickoff_utc,
    }


@pytest.fixture(autouse=True)
def _patch_calendar_path(tmp_path, monkeypatch):
    import src.data.fixture_calendar as fc
    cal_path = tmp_path / "logs" / "fixture_calendar.json"
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(fc, "_CALENDAR_PATH", cal_path)
    return cal_path


# ── calendar_available ────────────────────────────────────────────────────────

def test_calendar_available_false_when_missing(tmp_path, monkeypatch):
    import src.data.fixture_calendar as fc
    assert not fc.calendar_available()


def test_calendar_available_true_when_fresh(tmp_path, monkeypatch):
    import src.data.fixture_calendar as fc
    fc._CALENDAR_PATH.write_text("{}")
    assert fc.calendar_available()


def test_calendar_available_false_when_stale(tmp_path, monkeypatch):
    import src.data.fixture_calendar as fc
    import os
    fc._CALENDAR_PATH.write_text("{}")
    stale_mtime = time.time() - (9 * 86400)
    os.utime(fc._CALENDAR_PATH, (stale_mtime, stale_mtime))
    assert not fc.calendar_available()


def test_calendar_available_false_when_corrupt(tmp_path, monkeypatch):
    import src.data.fixture_calendar as fc
    fc._CALENDAR_PATH.write_text("not valid json{{{")
    assert not fc.calendar_available()


# ── has_fixtures ──────────────────────────────────────────────────────────────

def test_has_fixtures_true_when_present(tmp_path):
    import src.data.fixture_calendar as fc
    fc._CALENDAR_PATH.write_text(json.dumps({
        "generated_at": "2026-05-03T02:00:00Z",
        "fixtures": [_make_fixture("soccer_epl", "2026-05-10T14:00:00+00:00")],
    }))
    assert fc.has_fixtures("soccer_epl", date(2026, 5, 10))


def test_has_fixtures_false_when_wrong_league(tmp_path):
    import src.data.fixture_calendar as fc
    fc._CALENDAR_PATH.write_text(json.dumps({
        "generated_at": "2026-05-03T02:00:00Z",
        "fixtures": [_make_fixture("soccer_epl", "2026-05-10T14:00:00+00:00")],
    }))
    assert not fc.has_fixtures("soccer_germany_bundesliga", date(2026, 5, 10))


def test_has_fixtures_false_when_wrong_date(tmp_path):
    import src.data.fixture_calendar as fc
    fc._CALENDAR_PATH.write_text(json.dumps({
        "generated_at": "2026-05-03T02:00:00Z",
        "fixtures": [_make_fixture("soccer_epl", "2026-05-10T14:00:00+00:00")],
    }))
    assert not fc.has_fixtures("soccer_epl", date(2026, 5, 11))


def test_has_fixtures_accepts_string_date(tmp_path):
    import src.data.fixture_calendar as fc
    fc._CALENDAR_PATH.write_text(json.dumps({
        "generated_at": "2026-05-03T02:00:00Z",
        "fixtures": [_make_fixture("soccer_epl", "2026-05-10T14:00:00+00:00")],
    }))
    assert fc.has_fixtures("soccer_epl", "2026-05-10")


def test_has_fixtures_false_when_calendar_missing():
    import src.data.fixture_calendar as fc
    assert not fc.has_fixtures("soccer_epl", date(2026, 5, 10))


# ── get_fixtures ──────────────────────────────────────────────────────────────

def test_get_fixtures_filters_by_league_and_date(tmp_path):
    import src.data.fixture_calendar as fc
    fixtures = [
        _make_fixture("soccer_epl", "2026-05-10T14:00:00+00:00", "Arsenal", "Chelsea"),
        _make_fixture("soccer_epl", "2026-05-17T14:00:00+00:00", "Liverpool", "Man City"),
        _make_fixture("soccer_germany_bundesliga", "2026-05-10T13:30:00+00:00"),
    ]
    fc._CALENDAR_PATH.write_text(json.dumps({"generated_at": "2026-05-03T02:00:00Z", "fixtures": fixtures}))

    result = fc.get_fixtures("soccer_epl", date(2026, 5, 10), date(2026, 5, 10))
    assert len(result) == 1
    assert result[0]["home"] == "Arsenal"


def test_get_fixtures_accepts_string_dates(tmp_path):
    import src.data.fixture_calendar as fc
    fc._CALENDAR_PATH.write_text(json.dumps({
        "generated_at": "2026-05-03T02:00:00Z",
        "fixtures": [_make_fixture("soccer_epl", "2026-05-10T14:00:00+00:00")],
    }))
    result = fc.get_fixtures("soccer_epl", "2026-05-10", "2026-05-10")
    assert len(result) == 1


def test_get_fixtures_inclusive_range(tmp_path):
    import src.data.fixture_calendar as fc
    fixtures = [
        _make_fixture("soccer_epl", "2026-05-10T14:00:00+00:00"),
        _make_fixture("soccer_epl", "2026-05-11T14:00:00+00:00"),
        _make_fixture("soccer_epl", "2026-05-12T14:00:00+00:00"),
    ]
    fc._CALENDAR_PATH.write_text(json.dumps({"generated_at": "2026-05-03T02:00:00Z", "fixtures": fixtures}))

    result = fc.get_fixtures("soccer_epl", date(2026, 5, 10), date(2026, 5, 11))
    assert len(result) == 2


def test_get_fixtures_sorted_by_kickoff(tmp_path):
    import src.data.fixture_calendar as fc
    fixtures = [
        _make_fixture("soccer_epl", "2026-05-10T16:00:00+00:00", "C", "D"),
        _make_fixture("soccer_epl", "2026-05-10T13:00:00+00:00", "A", "B"),
    ]
    fc._CALENDAR_PATH.write_text(json.dumps({"generated_at": "2026-05-03T02:00:00Z", "fixtures": fixtures}))

    result = fc.get_fixtures("soccer_epl", date(2026, 5, 10), date(2026, 5, 10))
    assert result[0]["home"] == "A"
    assert result[1]["home"] == "C"


def test_get_fixtures_returns_empty_when_none_match(tmp_path):
    import src.data.fixture_calendar as fc
    fc._CALENDAR_PATH.write_text(json.dumps({"generated_at": "2026-05-03T02:00:00Z", "fixtures": []}))
    assert fc.get_fixtures("soccer_epl", date(2026, 5, 10), date(2026, 5, 12)) == []
