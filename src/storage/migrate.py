"""Apply schema.sql to an Azure SQL DB (MSSQL) or SQLite DB.

Usage:
    python3 -m src.storage.migrate --dsn "$AZURE_SQL_DSN"
    python3 -m src.storage.migrate --sqlite path/to/db.sqlite
    python3 -m src.storage.migrate --sqlite :memory:

Re-runs are no-ops: every CREATE is guarded by IF OBJECT_ID(...) IS NULL
(MSSQL) or CREATE TABLE IF NOT EXISTS (SQLite). The runner reports the
table-count delta so you can confirm idempotency.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_MSSQL = ROOT / "src" / "storage" / "schema.sql"
SCHEMA_SQLITE = ROOT / "src" / "storage" / "schema_sqlite.sql"


def split_statements(sql_text: str) -> list[str]:
    """Split a SQL script into individual statements.

    Strips line comments (`-- ...`) and blank lines, then groups remaining
    lines into statements. A statement ends at `;` on its own or at the end
    of a line, but only when not inside a BEGIN/END block (so multi-statement
    IF/BEGIN blocks are sent as one batch).
    """
    statements: list[str] = []
    current: list[str] = []
    depth = 0  # BEGIN/END nesting
    for raw_line in sql_text.splitlines():
        stripped = raw_line.strip().upper()
        if not stripped or stripped.startswith("--"):
            continue
        word = stripped.split()[0] if stripped.split() else ""
        if word == "BEGIN":
            depth += 1
        elif word == "END" or word == "END;":
            depth -= 1
        current.append(raw_line)
        if depth == 0 and raw_line.rstrip().endswith(";"):
            stmt = "\n".join(current).rstrip(";\n ").strip()
            if stmt:
                statements.append(stmt)
            current = []
    if current:
        stmt = "\n".join(current).rstrip(";\n ").strip()
        if stmt:
            statements.append(stmt)
    return statements


def count_tables_mssql(conn) -> int:
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM sys.tables WHERE schema_id = SCHEMA_ID('dbo')"
    )
    return cur.fetchone()[0]


def count_tables_sqlite(conn) -> int:
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    return cur.fetchone()[0]


def apply_schema(conn, sql_text: str) -> int:
    """Execute every statement in sql_text against conn. Returns count applied."""
    statements = split_statements(sql_text)
    cur = conn.cursor()
    for stmt in statements:
        cur.execute(stmt)
    conn.commit()
    return len(statements)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dsn", help="pyodbc DSN for Azure SQL / MSSQL")
    g.add_argument("--sqlite", help="SQLite path (or :memory:)")
    args = p.parse_args()

    if args.dsn:
        import pyodbc

        conn = pyodbc.connect(args.dsn)
        sql = SCHEMA_MSSQL.read_text()
        before = count_tables_mssql(conn)
        applied = apply_schema(conn, sql)
        after = count_tables_mssql(conn)
        target = f"MSSQL via DSN"
    else:
        import sqlite3

        conn = sqlite3.connect(args.sqlite)
        conn.execute("PRAGMA foreign_keys = ON")
        sql = SCHEMA_SQLITE.read_text()
        before = count_tables_sqlite(conn)
        applied = apply_schema(conn, sql)
        after = count_tables_sqlite(conn)
        target = f"SQLite at {args.sqlite}"

    delta = after - before
    if delta == 0:
        print(f"[migrate] {target}: no changes ({applied} stmts; {after} tables present).")
    else:
        print(f"[migrate] {target}: applied {applied} stmts; tables {before} → {after} (+{delta}).")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
