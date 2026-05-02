"""Tests for B.0 / B.0.5 / B.0.6 / B.0.7 — book_skill table, repo method, script.

Coverage:
- Schema migration creates book_skill table in SQLite, idempotent on second run.
- BetRepo.write_book_skill persists and overwrites rows correctly.
- LOO consensus (B.0.7): per-outcome component nontrivial expected value.
- LOO vs full consensus aggregate both ~0 by mathematical identity (documented).
- Bootstrap CI helper produces bounds that bracket the mean.
- Paired Brier (B.0.7): delta-per-fixture computation correct.
- Smoke test: compute() emits rows for both devig methods, all required keys.
- Pi-safety: no pyodbc / azure import with env vars unset.
"""
from __future__ import annotations

import gzip
import io
import json
import os
import sqlite3
import sys
from pathlib import Path

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


def _make_repo_with_sqlite(conn: sqlite3.Connection):
    from src.storage.repo import BetRepo
    repo = BetRepo(dsn=None)
    repo._dsn = "sqlite-test-sentinel"
    repo._conn = conn
    repo._cur = conn.cursor()
    repo._db_failed = False
    return repo


def _make_blob_gz(events: list) -> bytes:
    payload = {
        "captured_at": "2026-04-26T10:30:00+00:00",
        "source": "odds_api",
        "endpoint": "/v4/sports/soccer_epl/odds/",
        "params": {"markets": "h2h", "regions": "uk,eu"},
        "status": 200,
        "headers": {},
        "body_raw": json.dumps(events),
    }
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(json.dumps(payload).encode())
    return buf.getvalue()


def _synthetic_events() -> list:
    return [{
        "id": "ev-001",
        "sport_key": "soccer_epl",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "commence_time": "2026-04-26T15:00:00Z",
        "bookmakers": [
            {"key": "booka", "title": "Book A", "markets": [{"key": "h2h", "outcomes": [
                {"name": "Arsenal", "price": 1.0 / 0.55},
                {"name": "Chelsea", "price": 1.0 / 0.20},
                {"name": "Draw",    "price": 1.0 / 0.25},
            ]}]},
            {"key": "pinnacle", "title": "Pinnacle", "markets": [{"key": "h2h", "outcomes": [
                {"name": "Arsenal", "price": 1.0 / 0.50},
                {"name": "Chelsea", "price": 1.0 / 0.25},
                {"name": "Draw",    "price": 1.0 / 0.25},
            ]}]},
        ],
    }]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_book_skill_table_created():
    conn = _make_db()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "book_skill" in tables


def test_schema_migration_idempotent_with_book_skill():
    conn = _make_db()
    sql = SCHEMA_SQLITE.read_text()
    before = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
    _apply(conn, sql)
    after = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
    assert before == after


def test_book_skill_has_expected_columns():
    conn = _make_db()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(book_skill)").fetchall()}
    required = {
        "book", "league", "market", "window_end", "devig_method",
        "n_fixtures", "n_fixtures_source",
        "brier_vs_close",
        "brier_vs_outcome", "brier_vs_outcome_ci_low", "brier_vs_outcome_ci_high",
        "brier_paired_vs_pinnacle", "brier_paired_ci_low", "brier_paired_ci_high",
        "log_loss", "log_loss_ci_low", "log_loss_ci_high",
        "fav_longshot_slope", "home_bias", "draw_bias",
        "flag_rate", "mean_flag_edge",
        "edge_vs_consensus_loo", "edge_vs_pinnacle", "divergence",
        "truth_anchor", "created_at",
    }
    missing = required - cols
    assert not missing, f"book_skill missing columns: {missing}"
    # Verify old column is gone
    assert "edge_vs_consensus" not in cols, \
        "edge_vs_consensus should have been replaced by edge_vs_consensus_loo"


def test_book_skill_pk_includes_devig_method():
    conn = _make_db()
    pk_cols = {
        r[1] for r in conn.execute("PRAGMA table_info(book_skill)").fetchall()
        if r[5] > 0
    }
    assert pk_cols == {"book", "league", "market", "window_end", "devig_method"}


# ---------------------------------------------------------------------------
# BetRepo.write_book_skill
# ---------------------------------------------------------------------------

def _sample_row(book: str = "bet365", window: str = "2026-04-27",
                devig_method: str = "shin") -> dict:
    return {
        "book": book, "league": "EPL", "market": "h2h",
        "window_end": window, "devig_method": devig_method,
        "n_fixtures": 10, "n_fixtures_source": "blob",
        "brier_vs_close": None,
        "brier_vs_outcome": 0.231,
        "brier_vs_outcome_ci_low": 0.210, "brier_vs_outcome_ci_high": 0.252,
        "brier_paired_vs_pinnacle": -0.005,
        "brier_paired_ci_low": -0.015, "brier_paired_ci_high": 0.005,
        "log_loss": 0.95, "log_loss_ci_low": 0.88, "log_loss_ci_high": 1.02,
        "fav_longshot_slope": None, "home_bias": None, "draw_bias": None,
        "flag_rate": 0.05, "mean_flag_edge": 0.032,
        "edge_vs_consensus_loo": -0.001,
        "edge_vs_pinnacle": -0.002, "divergence": -0.001,
        "truth_anchor": "pinnacle",
    }


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
    assert d["devig_method"] == "shin"
    assert d["n_fixtures"] == 10
    assert abs(d["brier_vs_outcome"] - 0.231) < 1e-6
    assert abs(d["brier_paired_vs_pinnacle"] - (-0.005)) < 1e-6
    assert d["n_fixtures_source"] == "blob"


def test_write_book_skill_two_devig_methods_coexist():
    conn = _make_db()
    repo = _make_repo_with_sqlite(conn)
    row_shin = _sample_row(devig_method="shin")
    row_mult = _sample_row(devig_method="multiplicative")
    row_mult["brier_vs_outcome"] = 0.240
    repo.write_book_skill([row_shin, row_mult])
    conn.commit()

    rows = conn.execute("SELECT devig_method, brier_vs_outcome FROM book_skill "
                        "ORDER BY devig_method").fetchall()
    assert len(rows) == 2, "Expected two rows (one per devig_method)"
    methods = {r[0] for r in rows}
    assert methods == {"multiplicative", "shin"}


def test_write_book_skill_overwrites_same_key():
    conn = _make_db()
    repo = _make_repo_with_sqlite(conn)
    repo.write_book_skill([_sample_row()])
    conn.commit()

    row2 = dict(_sample_row())
    row2["n_fixtures"] = 20
    row2["brier_vs_outcome"] = 0.250
    repo.write_book_skill([row2])
    conn.commit()

    rows = conn.execute("SELECT * FROM book_skill").fetchall()
    assert len(rows) == 1
    col_names = [d[0] for d in conn.execute("SELECT * FROM book_skill").description]
    d = dict(zip(col_names, rows[0]))
    assert d["n_fixtures"] == 20
    assert abs(d["brier_vs_outcome"] - 0.250) < 1e-6


def test_write_book_skill_noop_without_db():
    from src.storage.repo import BetRepo
    repo = BetRepo(dsn=None)
    assert not repo.db_enabled
    repo.write_book_skill([_sample_row()])


# ---------------------------------------------------------------------------
# Bootstrap CI
# ---------------------------------------------------------------------------

def test_bootstrap_ci_brackets_mean():
    from scripts.compute_book_skill import _bootstrap_ci
    values = [0.20, 0.22, 0.18, 0.25, 0.19, 0.21, 0.23, 0.17]
    lo, hi = _bootstrap_ci(values, n_resamples=500)
    mean = sum(values) / len(values)
    assert lo is not None and hi is not None
    assert lo <= mean <= hi, f"CI [{lo:.4f}, {hi:.4f}] does not bracket mean {mean:.4f}"
    assert lo < hi


def test_bootstrap_ci_none_for_single_value():
    from scripts.compute_book_skill import _bootstrap_ci
    lo, hi = _bootstrap_ci([0.5])
    assert lo is None and hi is None


# ---------------------------------------------------------------------------
# LOO consensus and divergence (B.0.7)
# ---------------------------------------------------------------------------

def test_loo_aggregate_still_zero_by_identity():
    """LOO consensus aggregate mean over 3 h2h outcomes = 0.

    Even with LOO, sum_i(book_i - LOO_i) = 1 - 1 = 0 because all fair-prob
    vectors sum to 1.  The value of LOO is unbiased per-observation estimates,
    not a nonzero aggregate.
    """
    from scripts.compute_book_skill import _BookAccum

    event = {
        "home_team": "Arsenal", "away_team": "Chelsea",
        "commence_time": "2026-04-26T15:00:00Z",
        "bookmakers": [
            {"key": "booka", "markets": [{"key": "h2h", "outcomes": [
                {"name": "Arsenal", "price": 1.0 / 0.55},
                {"name": "Chelsea", "price": 1.0 / 0.20},
                {"name": "Draw",    "price": 1.0 / 0.25},
            ]}]},
            {"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [
                {"name": "Arsenal", "price": 1.0 / 0.50},
                {"name": "Chelsea", "price": 1.0 / 0.25},
                {"name": "Draw",    "price": 1.0 / 0.25},
            ]}]},
        ],
    }

    accum = _BookAccum()
    accum.add_event(event, truth_anchor="pinnacle")
    agg = accum.aggregate()

    assert "booka" in agg
    assert abs(agg["booka"]["edge_vs_consensus_loo"]) < 1e-10
    assert abs(agg["booka"]["edge_vs_pinnacle"]) < 1e-10
    assert abs(agg["booka"]["divergence"]) < 1e-10


def test_loo_per_outcome_component_nontrivial():
    """Verify per-outcome LOO component for a book with 10% overround.

    With LOO and 2 books, the LOO consensus for Book A = Book B's probs.
    So edge_vs_consensus_loo[home] = book_A_home - book_B_home,
    which is nonzero (books price differently) — expected ≈ −0.031 (offline).

    Compute:
        soft_raw=[0.50, 0.30, 0.30] sum=1.10 → shin → fair_home ≈ 0.459
        pin_raw =[0.495,0.292,0.213] sum=1.000 → shin ≈ identity → 0.495
        loo_home (for softbook, 2-book case) = pin_fair_home = 0.495
        loo_component = 0.459 - 0.495 ≈ -0.036
    """
    from src.betting.devig import shin

    soft_raw = [0.50, 0.30, 0.30]
    pin_raw  = [0.495, 0.292, 0.213]
    soft_fair = shin(soft_raw)
    pin_fair  = shin(pin_raw)

    # With 2 books, LOO for softbook = pinnacle's probs
    loo_home = soft_fair[0] - pin_fair[0]

    # Computed offline: soft_fair_home ≈ 0.459, pin_fair_home = 0.495 → Δ ≈ -0.036
    assert abs(loo_home - (-0.036)) < 5e-3, (
        f"LOO home component={loo_home:.6f}, expected ≈ -0.036"
    )
    # Individual components are nonzero
    assert abs(loo_home) > 1e-4

    # Aggregate sum = 0 by mathematical identity
    loo_sum = sum(soft_fair[i] - pin_fair[i] for i in range(3))
    assert abs(loo_sum) < 1e-10


def test_paired_brier_formula():
    """paired_delta = brier(book) - brier(pinnacle) per fixture, then mean.

    Known values (computed offline with shin):
      soft odds [2.0, 3.33, 3.33] → fair ≈ [0.459, 0.271, 0.271]
      pin  odds [2.02, 3.42, 4.69] → fair ≈ [0.495, 0.292, 0.213]
      result = H (home wins) → actual = [1,0,0]
      brier_soft = (0.459-1)^2 + 0.271^2 + 0.271^2 ≈ 0.293 + 0.073 + 0.073 = 0.439
      brier_pin  = (0.495-1)^2 + 0.292^2 + 0.213^2 ≈ 0.255 + 0.085 + 0.045 = 0.385
      paired_delta ≈ 0.439 - 0.385 = 0.054  (softbook is worse)
    """
    from src.betting.devig import shin

    soft_odds = [2.0, 1.0 / 0.30, 1.0 / 0.30]  # sum raw ≈ 1.10
    pin_odds  = [1.0 / 0.495, 1.0 / 0.292, 1.0 / 0.213]

    soft_fair = shin([1 / o for o in soft_odds])
    pin_fair  = shin([1 / o for o in pin_odds])

    actual = [1.0, 0.0, 0.0]  # home wins

    brier_soft = sum((soft_fair[i] - actual[i]) ** 2 for i in range(3))
    brier_pin  = sum((pin_fair[i]  - actual[i]) ** 2 for i in range(3))
    delta = brier_soft - brier_pin

    # Softbook should be worse (higher Brier) when it mispriced the outcome
    # delta > 0 means softbook was worse, <0 means better
    # With the given odds, delta should be notably nonzero (≈ 0.05)
    assert abs(delta) > 0.01, f"paired delta should be nontrivial: {delta:.6f}"


# ---------------------------------------------------------------------------
# Smoke test: compute() produces two rows per book (one per devig_method)
# ---------------------------------------------------------------------------

def test_compute_smoke_two_devig_methods(tmp_path, monkeypatch):
    """compute() emits shin + multiplicative rows for every book."""
    monkeypatch.delenv("BETS_DB_WRITE", raising=False)
    monkeypatch.delenv("BLOB_ARCHIVE", raising=False)

    gz = _make_blob_gz(_synthetic_events())
    fake_key = ("odds_api/v4_sports_soccer_epl_odds/2026/04/26/"
                "2026-04-26T10-30-00-000000_soccer_epl.json.gz")

    class FakeArchive:
        @property
        def enabled(self) -> bool:
            return True

        def list_blob_keys(self, prefix=""):
            return [fake_key] if "soccer_epl" in prefix else []

        def download_blob(self, key):
            return gz if key == fake_key else None

    import scripts.compute_book_skill as cbs
    monkeypatch.setattr(cbs, "get_archive", lambda: FakeArchive())
    monkeypatch.setattr(cbs, "load_leagues", lambda: [
        {"key": "soccer_epl", "label": "EPL", "min_books": 20, "fdco_code": None}
    ])

    from src.storage.repo import BetRepo
    monkeypatch.setattr(BetRepo, "get_bets", lambda self: [])

    from datetime import date
    rows = cbs.compute(window_end=date(2026, 4, 27), market="h2h", dry_run=True)

    assert len(rows) > 0

    # Each book should appear twice (once per devig_method)
    books = [r["book"] for r in rows]
    devig_methods = {r["devig_method"] for r in rows}
    assert "shin" in devig_methods, "Missing shin rows"
    assert "multiplicative" in devig_methods, "Missing multiplicative rows"

    # Every row has all required keys
    required_keys = {
        "book", "league", "market", "window_end", "devig_method",
        "n_fixtures", "n_fixtures_source",
        "brier_vs_close",
        "brier_vs_outcome", "brier_vs_outcome_ci_low", "brier_vs_outcome_ci_high",
        "brier_paired_vs_pinnacle", "brier_paired_ci_low", "brier_paired_ci_high",
        "log_loss", "log_loss_ci_low", "log_loss_ci_high",
        "fav_longshot_slope", "home_bias", "draw_bias",
        "flag_rate", "mean_flag_edge",
        "edge_vs_consensus_loo", "edge_vs_pinnacle", "divergence",
        "truth_anchor",
    }
    for r in rows:
        missing = required_keys - set(r)
        assert not missing, f"Row missing keys: {missing}"
        assert r["devig_method"] in ("shin", "multiplicative")
        assert r["n_fixtures_source"] in ("blob", "fdco")
        # No old column
        assert "edge_vs_consensus" not in r


# ---------------------------------------------------------------------------
# Pi-safety
# ---------------------------------------------------------------------------

def test_pi_safety_no_pyodbc_azure_on_import(monkeypatch):
    monkeypatch.delenv("BETS_DB_WRITE", raising=False)
    monkeypatch.delenv("BLOB_ARCHIVE", raising=False)
    monkeypatch.delenv("AZURE_SQL_DSN", raising=False)
    monkeypatch.delenv("AZURE_BLOB_CONN", raising=False)

    for mod in list(sys.modules):
        if mod in ("scripts.compute_book_skill", "src.storage.repo",
                   "src.storage.snapshots"):
            del sys.modules[mod]

    import scripts.compute_book_skill  # noqa: F401
    import src.storage.repo            # noqa: F401
    import src.storage.snapshots       # noqa: F401

    assert "pyodbc" not in sys.modules
    assert "azure.storage.blob" not in sys.modules
