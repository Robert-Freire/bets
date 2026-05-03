"""Tests for scripts/remediate_fixture_uuids.py."""
from __future__ import annotations

import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQLITE = ROOT / "src" / "storage" / "schema_sqlite.sql"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.storage._keys import _NAMESPACE, fixture_uuid as _new_uuid
import scripts.remediate_fixture_uuids as rem


def _u5_old(*parts) -> str:
    """Reproduces the pre-39cb08f key shape — for test data setup only."""
    return str(uuid.uuid5(_NAMESPACE, "|".join(parts)))


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQLITE.read_text())
    conn.commit()
    return conn


def _insert_fixture(conn, fid, sport_key="soccer_epl", home="Arsenal", away="Chelsea",
                    kickoff="2026-05-10T14:00:00+00:00", ingested_at=None):
    conn.execute(
        "INSERT INTO fixtures (id, sport_key, home, away, kickoff_utc, ingested_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (fid, sport_key, home, away, kickoff, ingested_at),
    )
    conn.commit()


def _insert_bet(conn, fid, bid=None):
    if bid is None:
        bid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO books (name) SELECT 'bet365' WHERE NOT EXISTS "
        "(SELECT 1 FROM books WHERE name='bet365')"
    )
    book_id = conn.execute("SELECT id FROM books WHERE name='bet365'").fetchone()[0]
    conn.execute(
        "INSERT INTO bets (id, fixture_id, book_id, scanned_at, market, side, odds, result) "
        "VALUES (?, ?, ?, '2026-05-03T09:00:00', 'h2h', 'HOME', 2.1, 'pending')",
        (bid, fid, book_id),
    )
    conn.commit()
    return bid


# ── noop when all rows are already new-style ──────────────────────────────────

def test_remediate_noop_when_all_new_style():
    conn = _make_db()
    new_id = _new_uuid("soccer_epl", "2026-05-10T14:00:00+00:00", "Arsenal", "Chelsea")
    _insert_fixture(conn, new_id)
    stats = rem.remediate(conn, dry_run=False)
    assert stats["skipped_new"] == 1
    assert stats["to_migrate"] == 0
    assert conn.execute("SELECT id FROM fixtures").fetchone()[0] == new_id


# ── rename: old UUID → new UUID, FK updated ───────────────────────────────────

def test_remediate_renames_old_to_new():
    conn = _make_db()
    old_id = _u5_old("fixture", "2026-05-10T14:00:00+00:00", "Arsenal", "Chelsea")
    _insert_fixture(conn, old_id)
    bet_id = _insert_bet(conn, old_id)

    stats = rem.remediate(conn, dry_run=False)

    assert stats["to_migrate"] == 1
    assert stats["errors"] == 0
    new_id = _new_uuid("soccer_epl", "2026-05-10T14:00:00+00:00", "Arsenal", "Chelsea")
    assert conn.execute("SELECT id FROM fixtures WHERE id=?", (new_id,)).fetchone() is not None
    assert conn.execute("SELECT id FROM fixtures WHERE id=?", (old_id,)).fetchone() is None
    bet_fixture = conn.execute("SELECT fixture_id FROM bets WHERE id=?", (bet_id,)).fetchone()[0]
    assert bet_fixture == new_id


# ── collision: old row + new row exist; FKs updated, old row deleted ──────────

def test_remediate_merges_on_collision():
    conn = _make_db()
    now = "2026-05-10T14:00:00.000Z"
    old_id = _u5_old("fixture", "2026-05-10T14:00:00+00:00", "Arsenal", "Chelsea")
    new_id = _new_uuid("soccer_epl", "2026-05-10T14:00:00+00:00", "Arsenal", "Chelsea")

    _insert_fixture(conn, old_id, ingested_at=None)
    _insert_fixture(conn, new_id, ingested_at=now)  # calendar already wrote this
    bet_id = _insert_bet(conn, old_id)

    stats = rem.remediate(conn, dry_run=False)

    assert stats["collisions"] == 1
    assert stats["errors"] == 0
    # Old row gone
    assert conn.execute("SELECT id FROM fixtures WHERE id=?", (old_id,)).fetchone() is None
    # New row survives with its ingested_at intact
    row = conn.execute(
        "SELECT ingested_at FROM fixtures WHERE id=?", (new_id,)
    ).fetchone()
    assert row is not None
    assert row[0] == now
    # FK updated
    assert conn.execute(
        "SELECT fixture_id FROM bets WHERE id=?", (bet_id,)
    ).fetchone()[0] == new_id


# ── idempotent ────────────────────────────────────────────────────────────────

def test_remediate_idempotent():
    conn = _make_db()
    old_id = _u5_old("fixture", "2026-05-10T14:00:00+00:00", "Arsenal", "Chelsea")
    _insert_fixture(conn, old_id)

    stats1 = rem.remediate(conn, dry_run=False)
    assert stats1["to_migrate"] == 1

    stats2 = rem.remediate(conn, dry_run=False)
    assert stats2["to_migrate"] == 0
    assert stats2["skipped_new"] == 1


# ── rows with unrecognised UUIDs are migrated (not refused) ──────────────────

def test_remediate_migrates_unrecognised_uuid():
    """A row with a UUID that matches neither old nor new derivation is migrated
    to the correct new UUID — we do not refuse, since refusing would be worse
    than migrating an unusual row."""
    conn = _make_db()
    rogue_id = str(uuid.uuid4())
    _insert_fixture(conn, rogue_id)

    stats = rem.remediate(conn, dry_run=False)

    assert stats["to_migrate"] == 1
    assert stats["errors"] == 0
    # New-style UUID now exists
    new_id = _new_uuid("soccer_epl", "2026-05-10", "Arsenal", "Chelsea")
    assert conn.execute(
        "SELECT id FROM fixtures WHERE id=?", (new_id,)
    ).fetchone() is not None
    # Rogue row gone
    assert conn.execute(
        "SELECT id FROM fixtures WHERE id=?", (rogue_id,)
    ).fetchone() is None


# ── CSV-format kickoff round-trip regression ──────────────────────────────────

def test_remediate_handles_csv_format_kickoff():
    """Production CSV stores kickoff as '2026-05-09 16:30' (space, no tz).
    After migrate_csv_to_db, MSSQL stores datetime2 and pyodbc reads back
    as '2026-05-09 16:30:00' — different from the CSV input that was
    originally hashed. Remediation must still migrate these rows correctly
    (the old UUID can't be reconstructed; we migrate by inequality).
    """
    conn = _make_db()
    # The old UUID was derived from the raw CSV string before DB round-trip.
    csv_format = "2026-05-09 16:30"
    historic_id = _u5_old("fixture", csv_format, "Arsenal", "Chelsea")
    # What pyodbc would return after storing datetime2
    db_format = "2026-05-09 16:30:00"
    _insert_fixture(conn, historic_id, kickoff=db_format)

    stats = rem.remediate(conn, dry_run=False)

    assert stats["to_migrate"] == 1
    assert stats["errors"] == 0
    # New UUID keyed on the date part only
    new_id = _new_uuid("soccer_epl", "2026-05-09", "Arsenal", "Chelsea")
    assert conn.execute(
        "SELECT id FROM fixtures WHERE id=?", (new_id,)
    ).fetchone() is not None
    assert conn.execute(
        "SELECT id FROM fixtures WHERE id=?", (historic_id,)
    ).fetchone() is None


# ── migrate_csv_to_db uses canonical fixture_uuid ────────────────────────────

def test_migrate_csv_to_db_uses_canonical_fixture_uuid():
    """upsert_fixture must produce the same UUID as fixture_uuid(sport_key, ...)."""
    conn = _make_db()
    conn.execute("PRAGMA foreign_keys = OFF")

    from scripts.migrate_csv_to_db import Importer
    importer = Importer.__new__(Importer)
    importer.cur = conn.cursor()
    importer.summary = type("S", (), {
        "fixtures": type("F", (), {"inserted": 0, "skipped": 0})()
    })()
    importer._books = {}
    importer._strategies = {}
    importer._fixtures = {}

    fid = importer.upsert_fixture(
        "2026-05-10T14:00:00+00:00", "Arsenal", "Chelsea", "EPL"
    )
    expected = _new_uuid("soccer_epl", "2026-05-10T14:00:00+00:00", "Arsenal", "Chelsea")
    assert fid == expected, f"importer produced {fid!r}, expected {expected!r}"
