"""One-shot remediation for fixture_uuid signature change in commit 39cb08f.

For each fixtures row whose id does not match the current
  fixture_uuid(sport_key, kickoff_utc, home, away)
derive the correct UUID and migrate the row + its FK references atomically.

Migrate-by-natural-key-inequality: we do NOT try to reconstruct the original
input string that produced the old UUID (impossible — pyodbc reads datetime2
back as a Python datetime, which stringifies differently from the CSV input
that was originally hashed).  Instead: if old_id != new_id, migrate.

Run on WSL once after the code fix lands:
    export $(cat .env.dev)
    python3 scripts/remediate_fixture_uuids.py --dry-run    # preview
    python3 scripts/remediate_fixture_uuids.py              # commit

Idempotent: rows already on the correct UUID are skipped. Safe to re-run.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.storage._keys import fixture_uuid as _new_uuid


def _discover_fk_tables(cur) -> list[str]:
    """Return table names that have a fixture_id column (FK consumers).

    Queries INFORMATION_SCHEMA on MSSQL; falls back to hardcoded list for
    SQLite (which has no INFORMATION_SCHEMA).
    """
    try:
        cur.execute(
            "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE COLUMN_NAME = 'fixture_id' AND TABLE_SCHEMA = 'dbo'"
        )
        tables = [r[0] for r in cur.fetchall()]
        if tables:
            return tables
    except Exception:
        pass
    # SQLite fallback (used in tests)
    return ["bets", "paper_bets", "closing_lines"]


def remediate(conn, *, dry_run: bool) -> dict:
    cur = conn.cursor()

    cur.execute("SELECT id, sport_key, home, away, kickoff_utc FROM fixtures")
    rows = cur.fetchall()

    fk_tables = _discover_fk_tables(cur)
    print(f"[remediate] FK tables: {fk_tables}")

    stats = {"skipped_new": 0, "to_migrate": 0, "collisions": 0,
             "fks_updated": 0, "errors": 0}
    migrations: list[tuple[str, str, tuple]] = []

    for row in rows:
        old_id, sport_key, home, away, ko = row
        # fixture_uuid only uses kickoff_utc[:10] — the date part.  str(ko)[:10]
        # yields "YYYY-MM-DD" regardless of whether ko is a datetime object
        # ("2026-05-09 16:30:00"), a bare date string ("2026-05-09"), or an
        # ISO string with timezone ("2026-05-09T16:30:00+00:00").
        ko_date = str(ko)[:10] if ko else ""

        new_id = _new_uuid(sport_key or "", ko_date, home or "", away or "")

        if old_id == new_id:
            stats["skipped_new"] += 1
            continue

        migrations.append((old_id, new_id, (sport_key, home, away, ko_date)))
        stats["to_migrate"] += 1

    if not migrations:
        print("[remediate] Nothing to migrate — all rows already on new UUID scheme.")
        return stats

    existing_ids = {r[0] for r in rows}
    collisions = [(old, new, d) for old, new, d in migrations if new in existing_ids]
    non_collisions = [(old, new, d) for old, new, d in migrations if new not in existing_ids]
    stats["collisions"] = len(collisions)

    print(f"[remediate] Plan: {len(non_collisions)} renames, "
          f"{len(collisions)} collision merges, "
          f"{len(fk_tables)} FK table(s) to update. "
          f"dry_run={dry_run}")

    if dry_run:
        for old, new, (sk, home, away, ko) in non_collisions[:5]:
            print(f"  rename {old} → {new}  ({sk} | {home} vs {away} | {ko})")
        if len(non_collisions) > 5:
            print(f"  ... and {len(non_collisions) - 5} more")
        for old, new, (sk, home, away, ko) in collisions[:5]:
            print(f"  merge  {old} → {new}  ({sk} | {home} vs {away} | {ko})")
        return stats

    try:
        # Update FKs first, then move/delete rows (MSSQL FK constraint order).

        for old_id, new_id, (sport_key, home, away, ko_date) in non_collisions:
            cur.execute("SELECT * FROM fixtures WHERE id = ?", (old_id,))
            col_names = [d[0] for d in cur.description]
            full_row = dict(zip(col_names, cur.fetchone()))
            full_row["id"] = new_id

            placeholders = ", ".join(["?"] * len(col_names))
            cols = ", ".join(col_names)
            cur.execute(
                f"INSERT INTO fixtures ({cols}) VALUES ({placeholders})",
                [full_row[c] for c in col_names],
            )

            for table in fk_tables:
                cur.execute(
                    f"UPDATE {table} SET fixture_id = ? WHERE fixture_id = ?",
                    (new_id, old_id),
                )
                stats["fks_updated"] += cur.rowcount

            cur.execute("DELETE FROM fixtures WHERE id = ?", (old_id,))

        for old_id, new_id, _ in collisions:
            for table in fk_tables:
                cur.execute(
                    f"UPDATE {table} SET fixture_id = ? WHERE fixture_id = ?",
                    (new_id, old_id),
                )
                stats["fks_updated"] += cur.rowcount
            cur.execute("DELETE FROM fixtures WHERE id = ?", (old_id,))

        conn.commit()
        print(f"[remediate] Done: {len(non_collisions)} renamed, "
              f"{len(collisions)} merged, {stats['fks_updated']} FK rows updated.")

        for table in fk_tables:
            cur.execute(
                f"SELECT COUNT(*) FROM {table} t "
                f"WHERE NOT EXISTS (SELECT 1 FROM fixtures f WHERE f.id = t.fixture_id)"
            )
            orphans = cur.fetchone()[0]
            if orphans:
                print(f"[remediate] WARN: {orphans} orphaned fixture_id in {table}",
                      file=sys.stderr)

    except Exception as e:
        conn.rollback()
        print(f"[remediate] ERROR: transaction rolled back: {e}", file=sys.stderr)
        stats["errors"] += 1

    return stats


def main() -> int:
    if os.environ.get("BETS_DB_WRITE", "").strip() != "1":
        print("ERROR: BETS_DB_WRITE=1 required (sanity gate).", file=sys.stderr)
        return 1

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without writing")
    parser.add_argument("--sqlite", metavar="PATH",
                        help="Use a SQLite DB instead of AZURE_SQL_DSN (for tests)")
    args = parser.parse_args()

    if args.sqlite:
        import sqlite3
        conn = sqlite3.connect(args.sqlite)
        conn.execute("PRAGMA foreign_keys = ON")
    else:
        dsn = os.environ.get("AZURE_SQL_DSN", "").strip()
        if not dsn:
            print("ERROR: AZURE_SQL_DSN not set.", file=sys.stderr)
            return 1
        import pyodbc
        conn = pyodbc.connect(dsn)

    stats = remediate(conn, dry_run=args.dry_run)
    conn.close()
    return 1 if stats.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
