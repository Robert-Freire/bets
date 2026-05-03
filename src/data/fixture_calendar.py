"""Fixture calendar — forward-looking fixture lookup from logs/fixture_calendar.json.

Pi-safe: no DB dependency. Populated by scripts/ingest_fixtures.py (runs weekly).
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

_ROOT = Path(__file__).resolve().parents[2]
_CALENDAR_PATH = _ROOT / "logs" / "fixture_calendar.json"
_STALE_DAYS = 8  # calendar older than this is treated as unavailable


class KickoffCluster(NamedTuple):
    window_start: datetime
    window_end: datetime
    leagues: list[str]
    n_fixtures: int


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
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError):
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


def next_kickoff_clusters(
    league_keys: list[str],
    hours_ahead: int = 168,
    cluster_window_min: int = 90,
) -> list[KickoffCluster]:
    """Group upcoming kickoffs that fall within cluster_window_min of each other.

    Returns clusters in chronological order. Each cluster represents a window of
    fixtures for which a single scan at T-90min before window_start is sufficient.
    Used to compute optimal cron scan times.
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=hours_ahead)

    fixtures = _load()
    if fixtures is None:
        return []

    kickoffs: list[tuple[datetime, str]] = []
    for f in fixtures:
        if f.get("sport_key") not in league_keys:
            continue
        ko = _parse_ko(f)
        if ko and now < ko <= cutoff:
            kickoffs.append((ko, f["sport_key"]))

    if not kickoffs:
        return []

    kickoffs.sort()
    clusters: list[KickoffCluster] = []
    ws, we = kickoffs[0][0], kickoffs[0][0]
    leagues_set: set[str] = {kickoffs[0][1]}
    count = 1

    for ko, sk in kickoffs[1:]:
        if (ko - we).total_seconds() <= cluster_window_min * 60:
            we = max(we, ko)
            leagues_set.add(sk)
            count += 1
        else:
            clusters.append(KickoffCluster(ws, we, sorted(leagues_set), count))
            ws, we = ko, ko
            leagues_set = {sk}
            count = 1

    clusters.append(KickoffCluster(ws, we, sorted(leagues_set), count))
    return clusters
