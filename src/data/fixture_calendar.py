"""Fixture calendar — forward-looking fixture lookup from logs/fixture_calendar.json.

Pi-safe: no DB dependency. Populated by scripts/ingest_fixtures.py (runs weekly).
"""
from __future__ import annotations

import json
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
    """True if the fixture calendar JSON exists and was refreshed within the last 8 days."""
    try:
        age = datetime.now().timestamp() - _CALENDAR_PATH.stat().st_mtime
        return age < _STALE_DAYS * 86400
    except FileNotFoundError:
        return False


def _load() -> list[dict]:
    try:
        data = json.loads(_CALENDAR_PATH.read_text())
        return data.get("fixtures", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _parse_ko(f: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(f["kickoff_utc"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None


def has_fixtures(league_key: str, on_date: date | str) -> bool:
    """True if the calendar has any fixture for league_key on on_date (UTC date)."""
    if isinstance(on_date, str):
        on_date = date.fromisoformat(on_date)
    for f in _load():
        if f.get("sport_key") != league_key:
            continue
        ko = _parse_ko(f)
        if ko and ko.date() == on_date:
            return True
    return False


def get_fixtures(
    league_key: str, from_date: date, to_date: date
) -> list[dict]:
    """All fixtures for league_key in [from_date, to_date] (UTC dates, inclusive)."""
    out = []
    for f in _load():
        if f.get("sport_key") != league_key:
            continue
        ko = _parse_ko(f)
        if ko and from_date <= ko.date() <= to_date:
            out.append(f)
    out.sort(key=lambda x: x.get("kickoff_utc", ""))
    return out


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

    kickoffs: list[tuple[datetime, str]] = []
    for f in _load():
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
