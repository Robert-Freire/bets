"""Shared deterministic-UUID + label helpers for storage code.

The same namespace and natural keys are used by:
  - `scripts/migrate_csv_to_db.py` (one-shot CSV backfill, A.3)
  - `src/storage/repo.py`           (live dual-write repo, A.4)

Changing `_NAMESPACE` or any of the natural-key tuples breaks
idempotency between the importer and the live writer. Don't.
"""
from __future__ import annotations

import uuid

_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "kaunitz.bets:v1")

# CSV `sport` label → canonical Odds-API `sport_key`. Tennis labels are
# dynamic and fall through unchanged (stored as-is in fixtures.sport_key).
LABEL_TO_KEY: dict[str, str] = {
    "EPL":          "soccer_epl",
    "Bundesliga":   "soccer_germany_bundesliga",
    "Serie A":      "soccer_italy_serie_a",
    "Championship": "soccer_efl_champ",
    "Ligue 1":      "soccer_france_ligue_one",
    "Bundesliga 2": "soccer_germany_bundesliga2",
    "NBA":          "basketball_nba",
}

# Constant book name written into closing_lines rows. Closing lines are
# anchored against Pinnacle, not the flagged book — the schema PK still
# requires a book_id, so we use this canonical Pinnacle row.
PINNACLE_BOOK = "pinnacle"


def _u5(parts: tuple) -> str:
    return str(uuid.uuid5(_NAMESPACE, "|".join(str(p) for p in parts)))


def fixture_uuid(kickoff: str, home: str, away: str) -> str:
    """Stable fixture UUID. Independent of sport label so a corrected
    label later does not split the fixture."""
    return _u5(("fixture", kickoff, home, away))


def bet_uuid(scan_date: str, kickoff: str, home: str, away: str,
             market: str, line: str, side: str, book: str) -> str:
    """Stable bet UUID. Includes scan_date so the scanner re-flagging the
    same bet on a different day produces a new row (matches CSV dedup)."""
    return _u5(("bet", scan_date, kickoff, home, away, market, line, side, book))


def paper_bet_uuid(strategy: str, scan_date: str, kickoff: str, home: str,
                   away: str, market: str, line: str, side: str,
                   book: str) -> str:
    return _u5(("paper", strategy, scan_date, kickoff, home, away, market,
                line, side, book))


def normalise_line(line: str | int | float | None) -> str:
    """CSV-style canonical form: empty for h2h/btts, decimal string otherwise."""
    if line is None:
        return ""
    s = str(line).strip()
    return s


def scan_date_of(scanned_at: str) -> str:
    return (scanned_at or "")[:10]
