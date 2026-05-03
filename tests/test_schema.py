"""Smoke tests for the schema + migrate runner.

The MSSQL canonical schema (src/storage/schema.sql) is mirrored in
src/storage/schema_sqlite.sql for in-memory testing. These tests apply
the SQLite mirror via the migrate runner and verify structure.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQLITE = ROOT / "src" / "storage" / "schema_sqlite.sql"
SCHEMA_MSSQL = ROOT / "src" / "storage" / "schema.sql"

EXPECTED_TABLES = {
    "fixtures",
    "books",
    "strategies",
    "bets",
    "paper_bets",
    "closing_lines",
    "drift",
    "book_skill",
}


def _apply(conn, sql_text: str) -> int:
    from src.storage.migrate import apply_schema

    return apply_schema(conn, sql_text)


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def test_sqlite_schema_creates_all_tables():
    conn = _make_conn()
    _apply(conn, SCHEMA_SQLITE.read_text())
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    tables = {r[0] for r in rows}
    assert EXPECTED_TABLES.issubset(tables), (
        f"Missing tables: {EXPECTED_TABLES - tables}"
    )


def test_sqlite_schema_idempotent():
    conn = _make_conn()
    sql = SCHEMA_SQLITE.read_text()
    _apply(conn, sql)
    before = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
    _apply(conn, sql)  # rerun must be a no-op
    after = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
    assert before == after, "Schema is not idempotent on rerun"


def test_fixtures_has_calendar_columns():
    conn = _make_conn()
    _apply(conn, SCHEMA_SQLITE.read_text())
    cols = {r[1] for r in conn.execute("PRAGMA table_info(fixtures)").fetchall()}
    assert {"source", "status", "ingested_at"}.issubset(cols), (
        f"fixtures table missing calendar columns; got: {cols}"
    )


def test_bets_has_expected_columns():
    conn = _make_conn()
    _apply(conn, SCHEMA_SQLITE.read_text())
    cols = {r[1] for r in conn.execute("PRAGMA table_info(bets)").fetchall()}
    expected = {
        "id", "fixture_id", "book_id", "scanned_at", "market", "line", "side",
        "odds", "impl_raw", "impl_effective", "edge", "edge_gross",
        "effective_odds", "commission_rate", "consensus", "pinnacle_cons",
        "n_books", "confidence", "model_signal", "dispersion", "outlier_z",
        "devig_method", "weight_scheme", "stake", "actual_stake", "result",
        "settled_at", "pnl", "pinnacle_close_prob", "clv_pct", "created_at",
    }
    missing = expected - cols
    assert not missing, f"bets table missing columns: {missing}"


def test_paper_bets_mirrors_bets_plus_strategy():
    conn = _make_conn()
    _apply(conn, SCHEMA_SQLITE.read_text())
    bets_cols = {r[1] for r in conn.execute("PRAGMA table_info(bets)").fetchall()}
    paper_cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(paper_bets)").fetchall()}
    # paper_bets has all bets columns plus strategy_id
    assert bets_cols.issubset(paper_cols), (
        f"paper_bets missing cols from bets: {bets_cols - paper_cols}"
    )
    assert "strategy_id" in paper_cols


def test_foreign_keys_present():
    conn = _make_conn()
    _apply(conn, SCHEMA_SQLITE.read_text())
    bets_fks = {r[2] for r in conn.execute(
        "PRAGMA foreign_key_list(bets)").fetchall()}
    assert {"fixtures", "books"}.issubset(bets_fks), (
        f"bets foreign keys: {bets_fks}"
    )
    paper_fks = {r[2] for r in conn.execute(
        "PRAGMA foreign_key_list(paper_bets)").fetchall()}
    assert {"fixtures", "books", "strategies"}.issubset(paper_fks), (
        f"paper_bets foreign keys: {paper_fks}"
    )


def test_required_indices_present():
    conn = _make_conn()
    _apply(conn, SCHEMA_SQLITE.read_text())
    indices = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    expected_idx = {
        "ix_fixtures_kickoff_sport",
        "ix_bets_scanned",
        "ix_bets_fixture_lookup",
        "ix_paper_bets_strategy_result",
        "ix_paper_bets_fixture_lookup",
    }
    missing = expected_idx - indices
    assert not missing, f"Missing indices: {missing}"


def test_closing_lines_pk_includes_book_and_line():
    conn = _make_conn()
    _apply(conn, SCHEMA_SQLITE.read_text())
    pk_cols = [
        r[1] for r in conn.execute(
            "PRAGMA table_info(closing_lines)").fetchall()
        if r[5] > 0  # 6th col (pk) is rank in PK; >0 → in PK
    ]
    assert set(pk_cols) == {"fixture_id", "side", "market", "line", "book_id"}


def test_drift_pk_includes_t_minus_min():
    conn = _make_conn()
    _apply(conn, SCHEMA_SQLITE.read_text())
    pk_cols = {
        r[1] for r in conn.execute(
            "PRAGMA table_info(drift)").fetchall()
        if r[5] > 0
    }
    assert pk_cols == {
        "fixture_id", "side", "market", "line", "book_id", "t_minus_min"
    }


def test_migrate_runner_reports_changes_and_idempotency(tmp_path):
    """End-to-end: invoke `python -m src.storage.migrate --sqlite ...` twice."""
    db = tmp_path / "smoke.sqlite"
    cmd = [sys.executable, "-m", "src.storage.migrate", "--sqlite", str(db)]

    first = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, check=True)
    assert "applied" in first.stdout, first.stdout
    assert "+8" in first.stdout, f"expected +8 tables on first run; got: {first.stdout}"

    second = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT, check=True)
    assert "no changes" in second.stdout, second.stdout


def test_mssql_schema_uses_idempotent_guards():
    """Sanity-check that schema.sql has IF OBJECT_ID guards (not bare CREATEs)."""
    sql = SCHEMA_MSSQL.read_text()
    creates = [line for line in sql.splitlines() if line.strip().startswith("CREATE TABLE")]
    assert creates, "schema.sql has no CREATE TABLE statements?"
    for line in creates:
        # Each CREATE TABLE must be preceded by an IF OBJECT_ID guard.
        # Cheap proxy: count of IF OBJECT_ID guards >= count of CREATE TABLE.
        pass  # Detailed check below
    n_creates = len(creates)
    n_guards = sum(1 for line in sql.splitlines()
                   if "IF OBJECT_ID" in line and "CREATE TABLE" not in line)
    assert n_guards >= n_creates, (
        f"Found {n_creates} CREATE TABLE but only {n_guards} IF OBJECT_ID guards"
    )
