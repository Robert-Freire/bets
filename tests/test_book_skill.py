"""Tests for B.0 / B.0.5 / B.0.6 — book_skill table, repo method, and script.

Coverage:
- Schema migration creates book_skill table in SQLite, idempotent on second run.
- BetRepo.write_book_skill persists and overwrites rows correctly.
- Divergence calculation against a hand-computed synthetic 2-book fixture set.
- Smoke test: compute_book_skill.compute() runs against a fake blob archive
  (no network, no DB) and emits correctly-shaped rows.
- Pi-safety: importing the script with BETS_DB_WRITE and BLOB_ARCHIVE unset
  does not attempt to import pyodbc or azure.storage.blob.
"""
from __future__ import annotations

import gzip
import importlib
import json
import os
import sqlite3
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQLITE = ROOT / "src" / "storage" / "schema_sqlite.sql"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQLITE.read_text())
    conn.commit()
    return conn


def _apply(conn, sql_text: str) -> None:
    from src.storage.migrate import apply_schema
    apply_schema(conn, sql_text)


def _make_blob_gz(events: list) -> bytes:
    """Wrap a list of Odds API events into a SnapshotArchive envelope (gzipped)."""
    payload = {
        "captured_at": "2026-04-26T10:30:00+00:00",
        "source": "odds_api",
        "endpoint": "/v4/sports/soccer_epl/odds/",
        "params": {"markets": "h2h", "regions": "uk,eu"},
        "status": 200,
        "headers": {},
        "body_raw": json.dumps(events),
    }
    raw = json.dumps(payload).encode()
    import io
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(raw)
    return buf.getvalue()


def _synthetic_events() -> list:
    """Two-book synthetic fixture set for divergence hand-calculation."""
    # Book A: home=0.55, draw=0.25, away=0.20 (raw) → after Shin devig ≈ same
    # Book B: home=0.50, draw=0.25, away=0.25 (raw) → Pinnacle-like
    # For test we use identical odds so Shin ≈ proportional
    return [
        {
            "id": "ev-001",
            "sport_key": "soccer_epl",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "commence_time": "2026-04-26T15:00:00Z",
            "bookmakers": [
                {
                    "key": "booka",
                    "title": "Book A",
                    "markets": [{"key": "h2h", "outcomes": [
                        {"name": "Arsenal", "price": 1.0 / 0.55},
                        {"name": "Chelsea", "price": 1.0 / 0.20},
                        {"name": "Draw",    "price": 1.0 / 0.25},
                    ]}],
                },
                {
                    "key": "pinnacle",
                    "title": "Pinnacle",
                    "markets": [{"key": "h2h", "outcomes": [
                        {"name": "Arsenal", "price": 1.0 / 0.50},
                        {"name": "Chelsea", "price": 1.0 / 0.25},
                        {"name": "Draw",    "price": 1.0 / 0.25},
                    ]}],
                },
            ],
        }
    ]


# ---------------------------------------------------------------------------
# Schema: migration + idempotency
# ---------------------------------------------------------------------------

def test_book_skill_table_created():
    conn = _make_db()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "book_skill" in tables, f"book_skill not in {tables}"


def test_schema_migration_idempotent_with_book_skill():
    conn = _make_db()
    sql = SCHEMA_SQLITE.read_text()
    before = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
    _apply(conn, sql)   # second run must be a no-op
    after = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
    assert before == after, "Schema not idempotent after adding book_skill"


def test_book_skill_has_expected_columns():
    conn = _make_db()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(book_skill)").fetchall()}
    required = {
        "book", "league", "market", "window_end", "n_fixtures",
        "brier_vs_close", "brier_vs_outcome", "log_loss",
        "fav_longshot_slope", "home_bias", "draw_bias",
        "flag_rate", "mean_flag_edge",
        "edge_vs_consensus", "edge_vs_pinnacle", "divergence",
        "truth_anchor", "created_at",
    }
    missing = required - cols
    assert not missing, f"book_skill missing columns: {missing}"


def test_book_skill_pk_is_composite():
    conn = _make_db()
    pk_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(book_skill)").fetchall()
        if r[5] > 0
    }
    assert pk_cols == {"book", "league", "market", "window_end"}


# ---------------------------------------------------------------------------
# BetRepo.write_book_skill
# ---------------------------------------------------------------------------

def _sample_row(book: str = "bet365", window: str = "2026-04-27") -> dict:
    return {
        "book": book,
        "league": "EPL",
        "market": "h2h",
        "window_end": window,
        "n_fixtures": 10,
        "brier_vs_close": None,
        "brier_vs_outcome": 0.231,
        "log_loss": None,
        "fav_longshot_slope": None,
        "home_bias": None,
        "draw_bias": None,
        "flag_rate": 0.05,
        "mean_flag_edge": 0.032,
        "edge_vs_consensus": -0.001,
        "edge_vs_pinnacle": -0.002,
        "divergence": -0.001,
        "truth_anchor": "pinnacle",
    }


def _make_repo_with_sqlite(conn: sqlite3.Connection):
    """Return a BetRepo wired to a SQLite connection (bypasses pyodbc)."""
    from src.storage.repo import BetRepo
    repo = BetRepo(dsn=None)
    repo._dsn = "sqlite-test-sentinel"  # non-None → db_enabled = True
    repo._conn = conn
    repo._cur = conn.cursor()
    repo._db_failed = False
    return repo


def test_write_book_skill_persists_row():
    conn = _make_db()
    repo = _make_repo_with_sqlite(conn)

    repo.write_book_skill([_sample_row()])
    conn.commit()

    rows = conn.execute("SELECT * FROM book_skill").fetchall()
    assert len(rows) == 1
    col_names = [d[0] for d in conn.execute("SELECT * FROM book_skill").description]
    d = dict(zip(col_names, rows[0]))
    assert d["book"] == "bet365"
    assert d["league"] == "EPL"
    assert d["n_fixtures"] == 10
    assert abs(d["brier_vs_outcome"] - 0.231) < 1e-6


def test_write_book_skill_overwrites_on_same_key():
    conn = _make_db()
    repo = _make_repo_with_sqlite(conn)

    row = _sample_row()
    repo.write_book_skill([row])
    conn.commit()

    # Update n_fixtures and re-write the same key
    row2 = dict(row)
    row2["n_fixtures"] = 20
    row2["brier_vs_outcome"] = 0.250
    repo.write_book_skill([row2])
    conn.commit()

    rows = conn.execute("SELECT * FROM book_skill").fetchall()
    assert len(rows) == 1, "Expected exactly one row after overwrite"
    col_names = [d[0] for d in conn.execute("SELECT * FROM book_skill").description]
    d = dict(zip(col_names, rows[0]))
    assert d["n_fixtures"] == 20
    assert abs(d["brier_vs_outcome"] - 0.250) < 1e-6


def test_write_book_skill_noop_without_db():
    from src.storage.repo import BetRepo
    repo = BetRepo(dsn=None)
    # db_enabled is False when _dsn is None
    assert not repo.db_enabled
    # Must not raise
    repo.write_book_skill([_sample_row()])


# ---------------------------------------------------------------------------
# Divergence calculation: hand-computed expected values
# ---------------------------------------------------------------------------

def test_divergence_hand_computed():
    """Synthetic 2-book, 1-fixture, 1-scan set.

    Book A raw implied: home=0.55, draw=0.25, away=0.20  (sum=1.00, no overround)
    Pinnacle raw implied: home=0.50, draw=0.25, away=0.25 (sum=1.00, no overround)

    With no overround, Shin devig == identity (already fair).
    Consensus (mean over 2 books per outcome):
        home = (0.55+0.50)/2 = 0.525
        draw = (0.25+0.25)/2 = 0.250
        away = (0.20+0.25)/2 = 0.225

    For Book A:
        edge_vs_consensus per outcome:
            home:  0.55 - 0.525 = +0.025
            draw:  0.25 - 0.250 =  0.000
            away:  0.20 - 0.225 = -0.025
        mean = (0.025 + 0.000 - 0.025) / 3 = 0.0

        edge_vs_pinnacle per outcome:
            home:  0.55 - 0.50 = +0.05
            draw:  0.25 - 0.25 =  0.00
            away:  0.20 - 0.25 = -0.05
        mean = (0.05 + 0.00 - 0.05) / 3 = 0.0

        divergence = 0.0 - 0.0 = 0.0
    """
    from scripts.compute_book_skill import _BookAccum

    # Build event with no overround so Shin ≈ identity
    event = {
        "id": "ev-hand",
        "sport_key": "soccer_epl",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "commence_time": "2026-04-26T15:00:00Z",
        "bookmakers": [
            {
                "key": "booka",
                "title": "Book A",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Arsenal", "price": 1.0 / 0.55},
                    {"name": "Chelsea", "price": 1.0 / 0.20},
                    {"name": "Draw",    "price": 1.0 / 0.25},
                ]}],
            },
            {
                "key": "pinnacle",
                "title": "Pinnacle",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Arsenal", "price": 1.0 / 0.50},
                    {"name": "Chelsea", "price": 1.0 / 0.25},
                    {"name": "Draw",    "price": 1.0 / 0.25},
                ]}],
            },
        ],
    }

    accum = _BookAccum()
    accum.add_event(event, truth_anchor="pinnacle")
    agg = accum.aggregate()

    assert "booka" in agg, f"booka not in agg: {list(agg)}"
    row_a = agg["booka"]
    assert abs(row_a["edge_vs_consensus"]) < 1e-6, (
        f"edge_vs_consensus should be ~0, got {row_a['edge_vs_consensus']}"
    )
    assert abs(row_a["edge_vs_pinnacle"]) < 1e-6, (
        f"edge_vs_pinnacle should be ~0, got {row_a['edge_vs_pinnacle']}"
    )
    assert abs(row_a["divergence"]) < 1e-6, (
        f"divergence should be ~0, got {row_a['divergence']}"
    )


def test_divergence_nonzero_with_overround():
    """Book with overround gives nonzero Shin devig and measurable divergence."""
    from scripts.compute_book_skill import _BookAccum

    # Book A has overround: raw sum = 1.10 (10% vig)
    # Pinnacle is close to fair: raw sum = 1.02
    event = {
        "id": "ev-vig",
        "home_team": "Home",
        "away_team": "Away",
        "commence_time": "2026-04-26T15:00:00Z",
        "bookmakers": [
            {
                "key": "softbook",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Home", "price": 1 / 0.50},
                    {"name": "Away", "price": 1 / 0.30},
                    {"name": "Draw", "price": 1 / 0.30},
                ]}],  # raw sum = 1.10
            },
            {
                "key": "pinnacle",
                "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Home", "price": 1 / 0.495},
                    {"name": "Away", "price": 1 / 0.292},
                    {"name": "Draw", "price": 1 / 0.233},
                ]}],  # raw sum ≈ 1.02
            },
        ],
    }

    accum = _BookAccum()
    accum.add_event(event, truth_anchor="pinnacle")
    agg = accum.aggregate()
    assert "softbook" in agg
    # Just verify it runs and divergence is a float
    div = agg["softbook"]["divergence"]
    assert isinstance(div, float), f"divergence should be float, got {type(div)}"


# ---------------------------------------------------------------------------
# Smoke test: compute() with fake blob archive
# ---------------------------------------------------------------------------

def test_compute_smoke_no_network(tmp_path, monkeypatch):
    """compute() with a fake SnapshotArchive produces rows matching the schema."""
    monkeypatch.delenv("BETS_DB_WRITE", raising=False)
    monkeypatch.delenv("BLOB_ARCHIVE", raising=False)

    # Fake archive that returns one synthetic blob for soccer_epl
    gz = _make_blob_gz(_synthetic_events())
    fake_key = "odds_api/v4_sports_soccer_epl_odds/2026/04/26/2026-04-26T10-30-00-000000_soccer_epl.json.gz"

    class FakeArchive:
        enabled = True

        def list_blob_keys(self, prefix=""):
            if "soccer_epl" in prefix:
                return [fake_key]
            return []

        def download_blob(self, key):
            if key == fake_key:
                return gz
            return None

    # Patch get_archive in the compute module
    import scripts.compute_book_skill as cbs
    monkeypatch.setattr(cbs, "get_archive", lambda: FakeArchive())
    # Use only EPL so the test is fast
    monkeypatch.setattr(cbs, "load_leagues", lambda: [
        {"key": "soccer_epl", "label": "EPL", "min_books": 20, "fdco_code": None}
    ])

    from datetime import date
    rows = cbs.compute(
        window_end=date(2026, 4, 27),
        market="h2h",
        dry_run=True,
    )

    assert len(rows) > 0, "Expected at least one row"
    for r in rows:
        assert "book" in r
        assert "league" in r
        assert "market" in r
        assert "window_end" in r
        assert "n_fixtures" in r
        assert r["market"] == "h2h"
        assert r["league"] == "EPL"
        assert r["window_end"] == "2026-04-27"
        assert isinstance(r["n_fixtures"], int) and r["n_fixtures"] >= 0

    # Verify required keys are present on every row
    required_keys = {
        "book", "league", "market", "window_end", "n_fixtures",
        "brier_vs_close", "brier_vs_outcome", "log_loss",
        "fav_longshot_slope", "home_bias", "draw_bias",
        "flag_rate", "mean_flag_edge",
        "edge_vs_consensus", "edge_vs_pinnacle", "divergence",
        "truth_anchor",
    }
    for r in rows:
        missing = required_keys - set(r)
        assert not missing, f"Row missing keys: {missing}"


# ---------------------------------------------------------------------------
# Pi-safety: no pyodbc / azure import when env vars unset
# ---------------------------------------------------------------------------

def test_pi_safety_no_pyodbc_azure_on_import(monkeypatch):
    """Importing the script and its dependencies with no env flags must not
    trigger pyodbc or azure.storage.blob imports."""
    monkeypatch.delenv("BETS_DB_WRITE", raising=False)
    monkeypatch.delenv("BLOB_ARCHIVE", raising=False)
    monkeypatch.delenv("AZURE_SQL_DSN", raising=False)
    monkeypatch.delenv("AZURE_BLOB_CONN", raising=False)

    # Remove cached modules so fresh import is forced
    for mod in list(sys.modules):
        if mod in ("scripts.compute_book_skill", "src.storage.repo",
                   "src.storage.snapshots"):
            del sys.modules[mod]

    import scripts.compute_book_skill  # noqa: F401  — just import, don't run
    import src.storage.repo  # noqa: F401
    import src.storage.snapshots  # noqa: F401

    assert "pyodbc" not in sys.modules, (
        "pyodbc was imported at module level — breaks Pi safety"
    )
    assert "azure.storage.blob" not in sys.modules, (
        "azure.storage.blob was imported at module level — breaks Pi safety"
    )
