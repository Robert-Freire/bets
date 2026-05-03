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


# ── _norm_name ────────────────────────────────────────────────────────────────

def test_norm_name_strips_fc_suffix():
    assert ingest._norm_name("Arsenal FC") == "arsenal"
    assert ingest._norm_name("Arsenal") == "arsenal"


def test_norm_name_strips_afc_suffix():
    assert ingest._norm_name("Cardiff AFC") == "cardiff"


def test_norm_name_folds_accents():
    assert ingest._norm_name("Mönchengladbach") == "monchengladbach"
    assert ingest._norm_name("Paris Saint-Germain") == "paris saint-germain"


def test_norm_name_case_insensitive():
    assert ingest._norm_name("ARSENAL FC") == ingest._norm_name("arsenal fc")


# ── _dedup ────────────────────────────────────────────────────────────────────

def test_dedup_removes_exact_duplicates():
    f = {"sport_key": "soccer_epl", "kickoff_utc": "2026-05-10T14:00:00+00:00",
         "home": "Arsenal", "away": "Chelsea", "league": "EPL"}
    result = ingest._dedup([f, f.copy()])
    assert len(result) == 1


def test_dedup_matches_fc_vs_no_fc():
    """Dedup must collapse 'Arsenal FC' (AFD) and 'Arsenal' (FDCO) as the same fixture."""
    afd_row = {"sport_key": "soccer_epl", "kickoff_utc": "2026-05-10T14:00:00+00:00",
               "home": "Arsenal FC", "away": "Chelsea FC", "league": "EPL"}
    fdco_row = {"sport_key": "soccer_epl", "kickoff_utc": "2026-05-10T14:00:00+00:00",
                "home": "Arsenal", "away": "Chelsea", "league": "EPL"}
    result = ingest._dedup([fdco_row, afd_row])
    assert len(result) == 1


def test_dedup_last_entry_wins():
    f1 = {"sport_key": "soccer_epl", "kickoff_utc": "2026-05-10T14:00:00+00:00",
          "home": "Arsenal", "away": "Chelsea", "league": "EPL", "tag": "first"}
    f2 = {**f1, "tag": "second"}
    result = ingest._dedup([f1, f2])
    assert result[0]["tag"] == "second"


def test_dedup_keeps_different_fixtures():
    f1 = {"sport_key": "soccer_epl", "kickoff_utc": "2026-05-10T14:00:00+00:00",
          "home": "Arsenal", "away": "Chelsea", "league": "EPL"}
    f2 = {"sport_key": "soccer_epl", "kickoff_utc": "2026-05-10T16:00:00+00:00",
          "home": "Liverpool", "away": "Man City", "league": "EPL"}
    result = ingest._dedup([f1, f2])
    assert len(result) == 2


# ── _merge ────────────────────────────────────────────────────────────────────

def test_merge_prefers_primary_on_same_fixture():
    """Primary (AFD) row wins over secondary (FDCO) for the same fixture."""
    afd = [{"sport_key": "soccer_epl", "home": "Arsenal FC", "away": "Chelsea FC",
            "kickoff_utc": "2026-05-10T14:05:00+00:00", "league": "EPL", "tag": "afd"}]
    fdco = [{"sport_key": "soccer_epl", "home": "Arsenal", "away": "Chelsea",
             "kickoff_utc": "2026-05-10T14:00:00+00:00", "league": "EPL", "tag": "fdco"}]
    result = ingest._merge(afd, fdco)
    assert len(result) == 1
    assert result[0]["tag"] == "afd"


def test_merge_preserves_fdco_beyond_afd_window():
    """Regression: union-merge must not drop FDCO fixtures beyond AFD's time horizon."""
    today = "2026-05-10"
    far = "2026-06-14"  # well beyond 10-day AFD window
    afd = [{"sport_key": "soccer_epl", "home": "Arsenal FC", "away": "Chelsea FC",
            "kickoff_utc": f"{today}T14:00:00+00:00", "league": "EPL"}]
    fdco = [
        {"sport_key": "soccer_epl", "home": "Arsenal", "away": "Chelsea",
         "kickoff_utc": f"{today}T14:00:00+00:00", "league": "EPL"},   # same as AFD
        {"sport_key": "soccer_epl", "home": "Liverpool", "away": "Man City",
         "kickoff_utc": f"{far}T14:00:00+00:00", "league": "EPL"},     # beyond AFD window
    ]
    result = ingest._merge(afd, fdco)
    # Both fixtures present: one deduped (today's) + one FDCO-only (far)
    assert len(result) == 2
    kickoff_dates = {f["kickoff_utc"][:10] for f in result}
    assert today in kickoff_dates
    assert far in kickoff_dates
    # The today fixture should use AFD's version (more precise time)
    today_f = next(f for f in result if f["kickoff_utc"].startswith(today))
    assert today_f["home"] == "Arsenal FC"


def test_merge_includes_secondary_only_leagues():
    primary = [{"sport_key": "soccer_epl", "home": "Arsenal", "away": "Chelsea",
                "kickoff_utc": "2026-05-10T14:00:00+00:00", "league": "EPL"}]
    secondary = [{"sport_key": "soccer_efl_champ", "home": "Leeds", "away": "Hull",
                  "kickoff_utc": "2026-05-10T15:00:00+00:00", "league": "Championship"}]
    result = ingest._merge(primary, secondary)
    assert len(result) == 2
    sport_keys = {f["sport_key"] for f in result}
    assert "soccer_epl" in sport_keys
    assert "soccer_efl_champ" in sport_keys


# ── _current_season ───────────────────────────────────────────────────────────

def test_current_season_during_season(monkeypatch):
    monkeypatch.setattr(ingest, "_today", lambda: date(2026, 5, 3))
    assert ingest._current_season() == "2526"


def test_current_season_after_july(monkeypatch):
    monkeypatch.setattr(ingest, "_today", lambda: date(2026, 8, 1))
    assert ingest._current_season() == "2627"


def test_current_season_century_boundary(monkeypatch):
    monkeypatch.setattr(ingest, "_today", lambda: date(2099, 8, 1))
    assert ingest._current_season() == "9900"


# ── _write_calendar ───────────────────────────────────────────────────────────

def test_write_calendar_writes_atomically(tmp_path):
    cal_path = tmp_path / "logs" / "fixture_calendar.json"
    cal_path.parent.mkdir()
    monkeypatch_path = cal_path  # use local var, no monkeypatch needed
    import scripts.ingest_fixtures as _ingest
    orig = _ingest._CALENDAR_PATH
    _ingest._CALENDAR_PATH = cal_path
    try:
        from datetime import datetime, timezone
        fixtures = [{"sport_key": "soccer_epl", "league": "EPL", "home": "A",
                     "away": "B", "kickoff_utc": "2026-05-10T14:00:00+00:00"}]
        _ingest._write_calendar(fixtures, datetime.now(timezone.utc), allow_empty=False)
        assert cal_path.exists()
        data = json.loads(cal_path.read_text())
        assert len(data["fixtures"]) == 1
        assert not (tmp_path / "logs" / "fixture_calendar.tmp").exists()
    finally:
        _ingest._CALENDAR_PATH = orig


def test_write_calendar_preserves_existing_on_empty_ingest(tmp_path, monkeypatch):
    cal_path = tmp_path / "logs" / "fixture_calendar.json"
    cal_path.parent.mkdir()
    existing = [{"sport_key": "soccer_epl", "league": "EPL", "home": "A",
                 "away": "B", "kickoff_utc": "2026-05-10T14:00:00+00:00"}]
    cal_path.write_text(json.dumps({"generated_at": "2026-05-03T02:00:00Z", "fixtures": existing}))
    monkeypatch.setattr(ingest, "_CALENDAR_PATH", cal_path)

    from datetime import datetime, timezone
    ingest._write_calendar([], datetime.now(timezone.utc), allow_empty=False)

    # File unchanged: still has the original fixture
    data = json.loads(cal_path.read_text())
    assert len(data["fixtures"]) == 1


def test_write_calendar_allow_empty_overwrites(tmp_path, monkeypatch):
    cal_path = tmp_path / "logs" / "fixture_calendar.json"
    cal_path.parent.mkdir()
    existing = [{"sport_key": "soccer_epl", "league": "EPL", "home": "A",
                 "away": "B", "kickoff_utc": "2026-05-10T14:00:00+00:00"}]
    cal_path.write_text(json.dumps({"generated_at": "2026-05-03T02:00:00Z", "fixtures": existing}))
    monkeypatch.setattr(ingest, "_CALENDAR_PATH", cal_path)

    from datetime import datetime, timezone
    ingest._write_calendar([], datetime.now(timezone.utc), allow_empty=True)

    data = json.loads(cal_path.read_text())
    assert data["fixtures"] == []


def test_write_calendar_skips_and_warns_when_no_existing_file(tmp_path, monkeypatch, capsys):
    cal_path = tmp_path / "logs" / "fixture_calendar.json"
    cal_path.parent.mkdir()
    monkeypatch.setattr(ingest, "_CALENDAR_PATH", cal_path)

    from datetime import datetime, timezone
    ingest._write_calendar([], datetime.now(timezone.utc), allow_empty=False)

    assert not cal_path.exists()
    assert "WARN" in capsys.readouterr().out


# ── main() integration ────────────────────────────────────────────────────────

def test_main_dry_run_does_not_write(tmp_path, monkeypatch):
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(ingest, "_CALENDAR_PATH", tmp_path / "fixture_calendar.json")
    monkeypatch.setattr(ingest, "_RAW_DIR", tmp_path)
    monkeypatch.delenv("FOOTBALL_DATA_API_KEY", raising=False)

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
    monkeypatch.delenv("FOOTBALL_DATA_API_KEY", raising=False)

    import sys as _sys
    monkeypatch.setattr(_sys, "argv", ["ingest_fixtures.py"])
    ingest.main()

    cal = json.loads((tmp_path / "fixture_calendar.json").read_text())
    assert "fixtures" in cal
    assert "generated_at" in cal
    assert len(cal["fixtures"]) >= 1
    assert cal["fixtures"][0]["sport_key"] == "soccer_epl"


def test_main_preserves_calendar_when_fdco_offline(tmp_path, monkeypatch):
    """Transient FDCO failure must not poison the calendar."""
    import urllib.request

    cal_path = tmp_path / "logs" / "fixture_calendar.json"
    cal_path.parent.mkdir()
    existing = [{"sport_key": "soccer_epl", "league": "EPL", "home": "A",
                 "away": "B", "kickoff_utc": "2026-05-10T14:00:00+00:00"}]
    cal_path.write_text(json.dumps({"generated_at": "2026-05-03T02:00:00Z", "fixtures": existing}))

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: (_ for _ in ()).throw(OSError("offline")))
    monkeypatch.setattr(ingest, "_CALENDAR_PATH", cal_path)
    monkeypatch.setattr(ingest, "_RAW_DIR", tmp_path)  # no season CSVs
    monkeypatch.delenv("FOOTBALL_DATA_API_KEY", raising=False)

    import sys as _sys
    monkeypatch.setattr(_sys, "argv", ["ingest_fixtures.py"])
    ingest.main()

    data = json.loads(cal_path.read_text())
    assert len(data["fixtures"]) == 1  # original preserved
