"""Fixture calendar — forward-looking fixture lookup backed by the fixtures table.

Reads from Azure SQL via FixtureRepo (WSL / post-A.10 Pi).  When the DB is
unavailable (Pi pre-A.10, or DB env vars unset), `calendar_available()` returns
False and every public function falls back to a safe no-op / empty result.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.storage.repo import FixtureRepo

_STALE_DAYS = 8  # calendar older than this is treated as unavailable

# Module-level repo; lazily constructed on first use.  Tests may replace this
# via monkeypatch or by calling _set_repo().
_repo: "FixtureRepo | None" = None


def _get_repo() -> "FixtureRepo":
    global _repo
    if _repo is None:
        from src.storage.repo import FixtureRepo
        _repo = FixtureRepo()
    return _repo


def _set_repo(repo: "FixtureRepo") -> None:
    """Replace the module-level repo — used by tests for SQLite injection."""
    global _repo
    _repo = repo


def _to_date(d: date | str) -> date:
    return date.fromisoformat(d) if isinstance(d, str) else d


def calendar_available() -> bool:
    """True iff the DB is reachable and fixture data was ingested within 8 days."""
    repo = _get_repo()
    if not repo.db_enabled:
        return False
    latest = repo.latest_ingest_at()
    if latest is None:
        return False
    # latest_ingest_at returns a naive UTC datetime; compare against UTC now.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if latest.tzinfo is not None:
        latest = latest.replace(tzinfo=None)
    age_s = (now - latest).total_seconds()
    return age_s < _STALE_DAYS * 86400


def has_fixtures(league_key: str, on_date: date | str) -> bool:
    """True if the calendar has any fixture for league_key on on_date (UTC date)."""
    on_date = _to_date(on_date)
    repo = _get_repo()
    if not repo.db_enabled:
        return False
    return repo.count_fixtures(league_key, on_date, on_date) > 0


def get_fixtures(
    league_key: str, from_date: date | str, to_date: date | str
) -> list[dict]:
    """All fixtures for league_key in [from_date, to_date] (UTC dates, inclusive).

    Returns [] when the DB is unavailable or no matches found.
    """
    from_date = _to_date(from_date)
    to_date = _to_date(to_date)
    repo = _get_repo()
    if not repo.db_enabled:
        return []
    return repo.get_fixtures(league_key, from_date, to_date)


def canary_verdict(
    league: str, today_date: date, lookahead: date
) -> tuple[str, list[dict]]:
    """Classify a 0-event canary scan for the given league + look-ahead window.

    Returns (verdict, near_fixtures) where verdict is one of:
      'alert'   — fixtures found in window; 0 events is a confirmed outage.
      'silent'  — no fixtures in window; legitimate quiet period.
      'unknown' — DB unavailable or data stale; fall back to unconditional alert.
    """
    if not calendar_available():
        return "unknown", []
    near = get_fixtures(league, today_date, lookahead)
    return ("alert", near) if near else ("silent", [])
