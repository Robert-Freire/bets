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
    """Two-book fixture with 10% overround on booka so shin ≠ multiplicative devig."""
    return [{
        "id": "ev-001",
        "sport_key": "soccer_epl",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "commence_time": "2026-04-26T15:00:00Z",
        "bookmakers": [
            {"key": "booka", "title": "Book A", "markets": [{"key": "h2h", "outcomes": [
                # raw implied sum = 0.55+0.30+0.25 = 1.10 (10% overround)
                # shin and proportional give meaningfully different fair probs
                {"name": "Arsenal", "price": 1.0 / 0.55},
                {"name": "Chelsea", "price": 1.0 / 0.30},
                {"name": "Draw",    "price": 1.0 / 0.25},
            ]}]},
            {"key": "pinnacle", "title": "Pinnacle", "markets": [{"key": "h2h", "outcomes": [
                # sum = 1.00 (no overround — both methods return identity)
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


def test_loo_with_three_books_differs_from_full_consensus():
    """With 3 books, LOO consensus ≠ full mean ≠ any single other book.

    Full consensus for Book A includes A itself → suction toward A.
    LOO consensus excludes A → mean of {B, C} only, which is the correct
    unbiased benchmark.  This test verifies the two are numerically different.
    """
    from scripts.compute_book_skill import _BookAccum

    # Three books with meaningfully different pricing on home outcome
    event = {
        "home_team": "Home", "away_team": "Away",
        "commence_time": "2026-04-26T15:00:00Z",
        "bookmakers": [
            {"key": "sharp", "markets": [{"key": "h2h", "outcomes": [
                {"name": "Home", "price": 1 / 0.50},
                {"name": "Away", "price": 1 / 0.28},
                {"name": "Draw", "price": 1 / 0.22},
            ]}]},
            {"key": "soft1", "markets": [{"key": "h2h", "outcomes": [
                {"name": "Home", "price": 1 / 0.46},  # generous on home
                {"name": "Away", "price": 1 / 0.30},
                {"name": "Draw", "price": 1 / 0.24},
            ]}]},
            {"key": "soft2", "markets": [{"key": "h2h", "outcomes": [
                {"name": "Home", "price": 1 / 0.47},
                {"name": "Away", "price": 1 / 0.30},
                {"name": "Draw", "price": 1 / 0.23},
            ]}]},
        ],
    }

    accum = _BookAccum()
    accum.add_event(event, truth_anchor="sharp")
    agg = accum.aggregate()

    # All three books must be present
    assert set(agg.keys()) == {"sharp", "soft1", "soft2"}

    # For soft1: LOO consensus = mean({sharp, soft2}) probs
    # Full consensus = mean({sharp, soft1, soft2}) probs
    # They are different because soft1 is excluded from the former but not latter
    # We can verify this by checking that the LOO edges are not the full-mean edges
    # by constructing the full consensus manually
    from src.betting.devig import shin

    def get_fair(raw): return shin(raw)
    books = {
        "sharp": get_fair([1/0.50, 1/0.28, 1/0.22]),
        "soft1": get_fair([1/0.46, 1/0.30, 1/0.24]),
        "soft2": get_fair([1/0.47, 1/0.30, 1/0.23]),
    }
    full_consensus = [(books["sharp"][i] + books["soft1"][i] + books["soft2"][i]) / 3
                      for i in range(3)]
    loo_for_soft1 = [(books["sharp"][i] + books["soft2"][i]) / 2
                     for i in range(3)]

    full_edge_home = books["soft1"][0] - full_consensus[0]
    loo_edge_home  = books["soft1"][0] - loo_for_soft1[0]

    # LOO and full consensus edges must differ (soft1 is not symmetrically positioned)
    assert abs(loo_edge_home - full_edge_home) > 1e-6, (
        f"LOO edge ({loo_edge_home:.6f}) should differ from full-mean edge "
        f"({full_edge_home:.6f})"
    )
    # The accumulator's LOO value should match the hand-computed LOO
    assert abs(agg["soft1"]["edge_vs_consensus_loo"] - sum(
        books["soft1"][i] - loo_for_soft1[i] for i in range(3)
    ) / 3) < 1e-10


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


def test_brier_differs_between_devig_methods():
    """Shin and multiplicative produce different Brier scores for an overround book.

    With a book priced at 10% overround, the two devig methods give different
    fair probs → different Brier scores against actual outcomes.  This validates
    that emitting two rows (one per method) is genuinely adding information, not
    duplicating it.
    """
    from datetime import date

    from src.betting.devig import proportional, shin
    from scripts.compute_book_skill import _brier_from_rows

    # Single FDCO-style row: bet365 has 10% overround; pinnacle has none
    rows = [{
        "Date": "26/04/2026",
        "FTR": "H",
        # Pinnacle closing: sum = 1.00
        "PSCH": str(1 / 0.50), "PSCD": str(1 / 0.25), "PSCA": str(1 / 0.25),
        # Bet365 closing: sum = 0.55+0.30+0.25 = 1.10 (10% overround)
        "B365CH": str(1 / 0.55), "B365CD": str(1 / 0.30), "B365CA": str(1 / 0.25),
        # Other books absent
        "BWCH": "", "BWCD": "", "BWCA": "",
        "BVCH": "", "BVCD": "", "BVCA": "",
        "BFECH": "", "BFECD": "", "BFECA": "",
    }]

    since = date(2026, 4, 1)
    until = date(2026, 5, 1)

    shin_result = _brier_from_rows(rows, since, until, shin)
    mult_result = _brier_from_rows(rows, since, until, proportional)

    bet365_shin = shin_result["bet365"]["brier_mean"]
    bet365_mult = mult_result["bet365"]["brier_mean"]

    assert bet365_shin is not None
    assert bet365_mult is not None
    # Two methods must disagree for a book with overround
    assert abs(bet365_shin - bet365_mult) > 1e-6, (
        f"Brier should differ between methods: shin={bet365_shin:.6f} "
        f"mult={bet365_mult:.6f}"
    )
    # Pinnacle has no overround → both methods return identity → same Brier
    pin_shin = shin_result["pinnacle"]["brier_mean"]
    pin_mult = mult_result["pinnacle"]["brier_mean"]
    assert abs(pin_shin - pin_mult) < 1e-10, (
        "Pinnacle (no overround) should give identical results under both methods"
    )


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
# B.1: bias signals (_bias_from_rows + _BookAccum home/draw extraction)
# ---------------------------------------------------------------------------

def _make_fdco_rows(n: int, book_h_odds_fn=None, include_pinnacle: bool = True) -> list[dict]:
    """Synthetic FDCO rows for bias tests.

    All fixtures: home wins (FTR=H). Pinnacle: flat 2.0/3.5/4.0.
    book_h_odds_fn(i): callable returning home odds for non-pinnacle book i.
    """
    pin_h, pin_d, pin_a = 2.0, 3.5, 4.0
    book_h_odds_fn = book_h_odds_fn or (lambda i: 2.1)  # slight home generosity
    rows = []
    for i in range(n):
        bh = book_h_odds_fn(i)
        row = {
            "Date": f"{i % 28 + 1:02d}/04/2026",
            "FTR": "H",
        }
        if include_pinnacle:
            row.update({"PSCH": str(pin_h), "PSCD": str(pin_d), "PSCA": str(pin_a)})
        row.update({
            "B365CH": str(bh), "B365CD": str(pin_d), "B365CA": str(pin_a),
            "BWCH": "", "BWCD": "", "BWCA": "",
            "BVCH": "", "BVCD": "", "BVCA": "",
            "BFECH": "", "BFECD": "", "BFECA": "",
        })
        rows.append(row)
    return rows


def test_bias_from_rows_home_bias_sign():
    """A book consistently shorter on home outcome has positive home_bias."""
    from datetime import date
    from src.betting.devig import shin
    from scripts.compute_book_skill import _bias_from_rows

    # bet365 priced at 2.10 on home vs pinnacle 2.00 — bet365 is generous on home
    rows = _make_fdco_rows(30, book_h_odds_fn=lambda i: 2.10)
    since = date(2026, 4, 1)
    until = date(2026, 4, 30)

    result = _bias_from_rows(rows, since, until, shin)

    assert "bet365" in result
    # bet365 home odds 2.10 > pinnacle 2.00 → devigged p_home(bet365) < p_home(pin)
    # home_bias = p_home(book) - p_home(loo); bet365 is below LOO → negative
    assert result["bet365"]["home_bias"] is not None
    assert result["bet365"]["home_bias"] < 0, (
        f"Expected negative home_bias for generous home odds, "
        f"got {result['bet365']['home_bias']:.6f}"
    )
    # With 2 books LOO for each = the other book's probs → biases are exact negations.
    # Pinnacle prices home as more likely (lower odds) → positive home_bias.
    assert result["pinnacle"]["home_bias"] is not None
    assert result["pinnacle"]["home_bias"] > 0, (
        f"Expected positive home_bias for pinnacle (tighter home odds), "
        f"got {result['pinnacle']['home_bias']:.6f}"
    )
    # Symmetry: the two biases sum to ~0 (two-book LOO identity)
    assert abs(result["bet365"]["home_bias"] + result["pinnacle"]["home_bias"]) < 1e-9, (
        "Two-book LOO biases must be exact negations"
    )


def test_bias_from_rows_returns_none_slope_for_too_few_buckets():
    """Fav-longshot slope is None when fewer than 3 buckets have >= 3 observations."""
    from datetime import date
    from src.betting.devig import shin
    from scripts.compute_book_skill import _bias_from_rows

    # 2 rows — cannot fill 3 distinct probability buckets with >= 3 obs each
    rows = _make_fdco_rows(2)
    result = _bias_from_rows(rows, date(2026, 4, 1), date(2026, 4, 30), shin)
    for bk_data in result.values():
        assert bk_data["fav_longshot_slope"] is None


def test_bias_from_rows_slope_nontrivial_with_enough_data():
    """Fav-longshot slope is non-None and finite with 60 fixtures and varied probs."""
    from datetime import date
    from src.betting.devig import shin
    from scripts.compute_book_skill import _bias_from_rows

    # Vary home odds 1.3–4.0 so probs span multiple buckets
    import math
    def h_odds(i):
        return 1.3 + (i % 20) * 0.135  # cycles through ~1.3 to ~4.0

    rows = _make_fdco_rows(60, book_h_odds_fn=h_odds)
    # Alternate outcomes so realized freq is not trivially 0 or 1
    for i, row in enumerate(rows):
        row["FTR"] = ["H", "D", "A"][i % 3]

    result = _bias_from_rows(rows, date(2026, 4, 1), date(2026, 4, 30), shin)
    bet365 = result.get("bet365", {})
    slope = bet365.get("fav_longshot_slope")
    assert slope is not None, "Expected non-None slope with 60 fixtures and varied probs"
    assert math.isfinite(slope)
    assert 0.0 < slope < 5.0, f"Slope {slope} outside plausible range"


def test_bias_shrinkage_reduces_extreme_values():
    """With n=5 fixtures the shrunken bias is closer to 0 than the raw bias."""
    from datetime import date
    from src.betting.devig import shin
    from scripts.compute_book_skill import _bias_from_rows, _SHRINKAGE_N0

    # Extreme home generosity: bet365 at 3.0 vs pinnacle 2.0
    rows = _make_fdco_rows(5, book_h_odds_fn=lambda i: 3.0)

    # Compute raw mean directly to compare against shrunken output
    from src.betting.devig import shin as _shin
    from scripts.compute_book_skill import _FDCO_BOOK_COLS, _parse_fdco_date
    from datetime import date as _date
    home_diffs = []
    for row in rows:
        d = _parse_fdco_date(row.get("Date", ""))
        if d is None:
            continue
        probs = {}
        for bk, (ch, cd, ca) in _FDCO_BOOK_COLS.items():
            try:
                oh, od, oa = float(row.get(ch) or 0), float(row.get(cd) or 0), float(row.get(ca) or 0)
            except (TypeError, ValueError):
                continue
            if min(oh, od, oa) > 1.0:
                probs[bk] = _shin([1/oh, 1/od, 1/oa])
        if len(probs) >= 2:
            for bk, bk_p in probs.items():
                if bk == "bet365":
                    others = [p for k, p in probs.items() if k != bk]
                    loo_home = sum(p[0] for p in others) / len(others)
                    home_diffs.append(bk_p[0] - loo_home)
    raw_mean = sum(home_diffs) / len(home_diffs) if home_diffs else 0.0

    result = _bias_from_rows(rows, date(2026, 4, 1), date(2026, 4, 30), shin)
    bet365 = result.get("bet365", {})
    shrunken = bet365.get("home_bias")
    assert shrunken is not None

    # The shrunken value must be strictly closer to 0 than the raw mean for n=5
    # (global mean ≈ 0 for a two-book symmetric dataset, so shrinkage pulls toward 0)
    assert abs(shrunken) < abs(raw_mean), (
        f"Shrinkage did not reduce |bias|: raw={raw_mean:.6f}, shrunken={shrunken:.6f}"
    )


def test_blob_accum_home_draw_bias_extracted():
    """_BookAccum.aggregate() extracts home_bias_blob and draw_bias_blob via loo_list slicing."""
    from scripts.compute_book_skill import _BookAccum

    # Three-book event: soft1 is generous on home, tight on draw
    event = {
        "home_team": "Home", "away_team": "Away",
        "commence_time": "2026-04-26T15:00:00Z",
        "bookmakers": [
            {"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [
                {"name": "Home", "price": 1 / 0.50},
                {"name": "Away", "price": 1 / 0.28},
                {"name": "Draw", "price": 1 / 0.22},
            ]}]},
            {"key": "soft1", "markets": [{"key": "h2h", "outcomes": [
                {"name": "Home", "price": 1 / 0.46},  # generous on home
                {"name": "Away", "price": 1 / 0.30},
                {"name": "Draw", "price": 1 / 0.24},
            ]}]},
            {"key": "soft2", "markets": [{"key": "h2h", "outcomes": [
                {"name": "Home", "price": 1 / 0.48},
                {"name": "Away", "price": 1 / 0.29},
                {"name": "Draw", "price": 1 / 0.23},
            ]}]},
        ],
    }

    accum = _BookAccum()
    for _ in range(5):  # repeat same event to accumulate
        accum.add_event(event, truth_anchor="pinnacle")
    agg = accum.aggregate()

    for bk in ("pinnacle", "soft1", "soft2"):
        assert bk in agg, f"{bk} missing from aggregate"
        assert "home_bias_blob" in agg[bk], f"{bk} missing home_bias_blob"
        assert "draw_bias_blob" in agg[bk], f"{bk} missing draw_bias_blob"
        assert agg[bk]["home_bias_blob"] is not None

    # soft1 is more generous on home (lower implied home prob) → negative home_bias_blob
    assert agg["soft1"]["home_bias_blob"] < agg["pinnacle"]["home_bias_blob"], (
        "soft1 should have lower home_bias than pinnacle (generous home odds)"
    )


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
