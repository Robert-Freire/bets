"""Fixture calendar — forward-looking fixture lookup from logs/fixture_calendar.json.

Pi-safe: no DB dependency. Populated by scripts/ingest_fixtures.py (runs weekly).
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_CALENDAR_PATH = _ROOT / "logs" / "fixture_calendar.json"
_STALE_DAYS = 8  # calendar older than this is treated as unavailable


def calendar_available() -> bool:
    """True if the fixture calendar JSON exists, is fresh (< 8 days old), and parseable."""
    try:
        age = time.time() - _CALENDAR_PATH.stat().st_mtime
        if age >= _STALE_DAYS * 86400:
            return False
        json.loads(_CALENDAR_PATH.read_text())
        return True
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False


def _load() -> list[dict] | None:
    """Load fixture list from the JSON cache.

    Returns None when the file is missing or corrupt — callers must distinguish
    this from an empty-but-valid list (no fixtures currently scheduled).
    """
    try:
        data = json.loads(_CALENDAR_PATH.read_text())
        return data.get("fixtures", [])
    except (OSError, json.JSONDecodeError):
        return None


def _parse_ko(f: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(f["kickoff_utc"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None


def _to_date(d: date | str) -> date:
    return date.fromisoformat(d) if isinstance(d, str) else d


def has_fixtures(league_key: str, on_date: date | str) -> bool:
    """True if the calendar has any fixture for league_key on on_date (UTC date)."""
    on_date = _to_date(on_date)
    fixtures = _load()
    if fixtures is None:
        return False
    for f in fixtures:
        if f.get("sport_key") != league_key:
            continue
        ko = _parse_ko(f)
        if ko and ko.date() == on_date:
            return True
    return False


def get_fixtures(
    league_key: str, from_date: date | str, to_date: date | str
) -> list[dict]:
    """All fixtures for league_key in [from_date, to_date] (UTC dates, inclusive).

    Returns [] when the calendar is missing or corrupt (same as no matches —
    callers that need to distinguish the two cases should use canary_verdict()).
    """
    from_date = _to_date(from_date)
    to_date = _to_date(to_date)
    fixtures = _load()
    if fixtures is None:
        return []
    out = []
    for f in fixtures:
        if f.get("sport_key") != league_key:
            continue
        ko = _parse_ko(f)
        if ko and from_date <= ko.date() <= to_date:
            out.append(f)
    out.sort(key=lambda x: x.get("kickoff_utc", ""))
    return out


def canary_verdict(
    league: str, today_date: date, lookahead: date
) -> tuple[str, list[dict]]:
    """Classify a 0-event canary scan for the given league + look-ahead window.

    Returns (verdict, near_fixtures) where verdict is one of:
      'alert'   — fixtures found in window; 0 events is a confirmed outage.
      'silent'  — no fixtures in window; legitimate quiet period (international
                  break, empty week, end of season).
      'unknown' — calendar absent, stale, or unreadable; fall back to the
                  existing unconditional alert behaviour.
    """
    if not calendar_available():
        return "unknown", []
    fixtures = _load()
    if fixtures is None:
        return "unknown", []
    near = [
        f for f in fixtures
        if f.get("sport_key") == league
        and (ko := _parse_ko(f)) is not None
        and today_date <= ko.date() <= lookahead
    ]
    return ("alert", near) if near else ("silent", [])
