"""Tests for scripts/ingest_fixtures.py — FDCO parsing and fixture calendar writing."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.ingest_fixtures as ingest


# ── _parse_fdco_kickoff ───────────────────────────────────────────────────────

def test_parse_fdco_kickoff_with_time_gmt():
    """12:30 UK time in winter (GMT) = 12:30 UTC."""
    ko = ingest._parse_fdco_kickoff("15/01/2026", "12:30")
    assert ko is not None
    assert ko.hour == 12
    assert ko.minute == 30
    assert ko.tzinfo is not None


def test_parse_fdco_kickoff_with_time_bst():
    """15:00 UK time in summer (BST = UTC+1) = 14:00 UTC."""
    ko = ingest._parse_fdco_kickoff("10/05/2026", "15:00")
    assert ko is not None
    assert ko.hour == 14  # BST → UTC
    assert ko.minute == 0


def test_parse_fdco_kickoff_blank_time_defaults_noon():
    ko = ingest._parse_fdco_kickoff("10/05/2026", "")
    assert ko is not None
    assert ko.hour == 12 or ko.hour == 11  # noon UK = noon or 11 UTC depending on DST


def test_parse_fdco_kickoff_two_digit_year():
    ko = ingest._parse_fdco_kickoff("10/05/26", "12:00")
    assert ko is not None
    assert ko.year == 2026


def test_parse_fdco_kickoff_invalid_date_returns_none():
    assert ingest._parse_fdco_kickoff("not-a-date", "12:00") is None


def test_parse_fdco_kickoff_invalid_time_falls_back():
    ko = ingest._parse_fdco_kickoff("10/05/2026", "XX:YY")
    assert ko is not None  # falls back to default noon


# ── _fetch_fdco_fixtures_csv (mocked HTTP) ────────────────────────────────────

_SAMPLE_FDCO_CSV = """Div,Date,Time,HomeTeam,AwayTeam,Res
E0,10/05/2026,15:00,Arsenal,Chelsea,
D1,09/05/2026,18:30,Bayern Munich,Dortmund,
E0,17/05/2026,12:30,Liverpool,Man City,
ZZ,10/05/2026,15:00,Unknown A,Unknown B,
"""


def test_fetch_fdco_parses_known_leagues(monkeypatch, tmp_path):
    import urllib.request

    def fake_urlopen(req, timeout=None):
        import io
        class FakeResp:
            def read(self): return _SAMPLE_FDCO_CSV.encode()
            def __enter__(self): return self
            def __exit__(self, *a): pass
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(ingest, "_today", lambda: date(2026, 5, 1))

    fixtures = ingest._fetch_fdco_fixtures_csv()
    sport_keys = {f["sport_key"] for f in fixtures}
    # E0 = soccer_epl, D1 = soccer_germany_bundesliga; ZZ unknown → excluded
    assert "soccer_epl" in sport_keys
    assert "soccer_germany_bundesliga" in sport_keys
    # Unknown Div not included
    unknown = [f for f in fixtures if f["home"] == "Unknown A"]
    assert not unknown


def test_fetch_fdco_excludes_past_fixtures(monkeypatch):
    import urllib.request

    past_csv = "Div,Date,Time,HomeTeam,AwayTeam\nE0,01/01/2020,15:00,Arsenal,Chelsea\n"

    def fake_urlopen(req, timeout=None):
        class FakeResp:
            def read(self): return past_csv.encode()
            def __enter__(self): return self
            def __exit__(self, *a): pass
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    fixtures = ingest._fetch_fdco_fixtures_csv()
    assert fixtures == []


def test_fetch_fdco_returns_empty_on_error(monkeypatch):
    import urllib.request

    def boom(req, timeout=None):
        raise OSError("network error")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    fixtures = ingest._fetch_fdco_fixtures_csv()
    assert fixtures == []


# ── _dedup ────────────────────────────────────────────────────────────────────

def test_dedup_removes_exact_duplicates():
    f = {"sport_key": "soccer_epl", "kickoff_utc": "2026-05-10T14:00:00+00:00",
         "home": "A", "away": "B", "league": "EPL", "source": "fdco", "status": "scheduled"}
    result = ingest._dedup([f, f.copy()])
    assert len(result) == 1


def test_dedup_keeps_different_fixtures():
    f1 = {"sport_key": "soccer_epl", "kickoff_utc": "2026-05-10T14:00:00+00:00",
          "home": "A", "away": "B", "league": "EPL", "source": "fdco", "status": "scheduled"}
    f2 = {"sport_key": "soccer_epl", "kickoff_utc": "2026-05-10T16:00:00+00:00",
          "home": "C", "away": "D", "league": "EPL", "source": "fdco", "status": "scheduled"}
    result = ingest._dedup([f1, f2])
    assert len(result) == 2


# ── _merge ────────────────────────────────────────────────────────────────────

def test_merge_prefers_primary_for_shared_leagues():
    primary = [{"sport_key": "soccer_epl", "home": "A", "away": "B",
                "kickoff_utc": "T", "league": "EPL", "source": "afd", "status": "scheduled"}]
    secondary = [{"sport_key": "soccer_epl", "home": "C", "away": "D",
                  "kickoff_utc": "T", "league": "EPL", "source": "fdco", "status": "scheduled"}]
    result = ingest._merge(primary, secondary)
    # Only the primary (afd) EPL fixture included
    assert len(result) == 1
    assert result[0]["source"] == "afd"


def test_merge_includes_secondary_only_leagues():
    primary = [{"sport_key": "soccer_epl", "home": "A", "away": "B",
                "kickoff_utc": "T", "league": "EPL", "source": "afd", "status": "scheduled"}]
    secondary = [{"sport_key": "soccer_efl_champ", "home": "C", "away": "D",
                  "kickoff_utc": "T", "league": "Championship", "source": "fdco",
                  "status": "scheduled"}]
    result = ingest._merge(primary, secondary)
    assert len(result) == 2
    sport_keys = {f["sport_key"] for f in result}
    assert "soccer_epl" in sport_keys
    assert "soccer_efl_champ" in sport_keys


# ── main() integration ────────────────────────────────────────────────────────

def test_main_dry_run_does_not_write(tmp_path, monkeypatch):
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(ingest, "_CALENDAR_PATH", tmp_path / "fixture_calendar.json")

    ingest.main.__module__  # ensure importable
    import sys as _sys
    monkeypatch.setattr(_sys, "argv", ["ingest_fixtures.py", "--dry-run"])

    ingest.main()
    assert not (tmp_path / "fixture_calendar.json").exists()


def test_main_writes_calendar_json(tmp_path, monkeypatch):
    import urllib.request

    sample_csv = "Div,Date,Time,HomeTeam,AwayTeam\nE0,10/05/2026,15:00,Arsenal,Chelsea\n"

    def fake_urlopen(req, timeout=None):
        class FakeResp:
            def read(self): return sample_csv.encode()
            def __enter__(self): return self
            def __exit__(self, *a): pass
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(ingest, "_today", lambda: date(2026, 5, 1))
    monkeypatch.setattr(ingest, "_CALENDAR_PATH", tmp_path / "fixture_calendar.json")

    import sys as _sys
    monkeypatch.setattr(_sys, "argv", ["ingest_fixtures.py"])
    ingest.main()

    cal = json.loads((tmp_path / "fixture_calendar.json").read_text())
    assert "fixtures" in cal
    assert "generated_at" in cal
    assert len(cal["fixtures"]) >= 1
    assert cal["fixtures"][0]["sport_key"] == "soccer_epl"
