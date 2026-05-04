"""
Tests for scripts/compare_strategies.py (DB-backed rewrite, Phase S.4).

Data injection: uses a SQLite BetRepo fixture instead of CSV files.
build_report() accepts an optional `repo` argument for testing.
"""
import csv
import math
import os
import sqlite3
import statistics
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQLITE = ROOT / "src" / "storage" / "schema_sqlite.sql"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.compare_strategies as cs  # noqa: E402


# ── SQLite BetRepo helper ─────────────────────────────────────────────────────

def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQLITE.read_text())
    conn.commit()
    return conn


class _SqliteRepo:
    def __init__(self, conn, logs_dir):
        from src.storage.repo import BetRepo
        self.repo = BetRepo(logs_dir=logs_dir, dsn="sqlite-test")
        self.repo._conn = conn
        self.repo._cur = conn.cursor()
        self.repo._connect = lambda: conn  # type: ignore[method-assign]


@pytest.fixture
def fresh_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("BETS_") or k.startswith("AZURE_SQL_"):
            monkeypatch.delenv(k, raising=False)
    return monkeypatch


@pytest.fixture
def db_repo(fresh_env, monkeypatch, tmp_path):
    """Yield (db_conn, repo) pair with BETS_DB_WRITE=1 set."""
    monkeypatch.setenv("BETS_DB_WRITE", "1")
    conn = _make_db()
    helper = _SqliteRepo(conn, tmp_path)
    return conn, helper.repo


def _paper_row_dict(
    strategy="A_production",
    sport="EPL",
    market="h2h",
    line="",
    home="Arsenal",
    away="Chelsea",
    kickoff="2026-05-02 15:00",
    side="HOME",
    book="skybet",
    odds="2.0",
    consensus="0.55",
    confidence="HIGH",
    model_signal="+0.020",
    edge="0.05",
    edge_gross="0.05",
    clv_pct="0.04",
    pinnacle_close_prob="0.56",
    result="",
    pnl="",
    stake="50",
):
    return {
        "scanned_at": "2026-05-02 10:30 UTC",
        "strategy": strategy,
        "sport": sport,
        "market": market,
        "line": line,
        "home": home,
        "away": away,
        "kickoff": kickoff,
        "side": side,
        "book": book,
        "odds": odds,
        "impl_raw": str(round(1.0 / float(odds), 4)),
        "impl_effective": str(round(1.0 / float(odds), 4)),
        "edge": edge,
        "edge_gross": edge_gross,
        "effective_odds": odds,
        "commission_rate": "0",
        "consensus": consensus,
        "pinnacle_cons": consensus,
        "n_books": "30",
        "confidence": confidence,
        "model_signal": model_signal,
        "dispersion": "0.01",
        "outlier_z": "0.5",
        "devig_method": "shin",
        "weight_scheme": "uniform",
        "stake": stake,
        "pinnacle_close_prob": pinnacle_close_prob,
        "clv_pct": clv_pct,
        "result": result,
        "pnl": pnl,
        "code_sha": "abc123",
        "strategy_config_hash": "h1",
    }


# ── _model_bucket edge cases ──────────────────────────────────────────────────

@pytest.mark.parametrize("signal,expected", [
    ("?", "no_signal"),
    ("", "no_signal"),
    (None, "no_signal"),
    ("+0.123", "agrees"),
    ("-0.045", "disagrees"),
    ("0.000", "disagrees"),
    ("garbage", "no_signal"),
    ("+0", "disagrees"),
])
def test_model_bucket(signal, expected):
    assert cs._model_bucket(signal) == expected


# ── _load_drift_index (reads frozen drift.csv) ────────────────────────────────

def _drift_row(
    *,
    home="Arsenal",
    away="Chelsea",
    kickoff="2026-05-02 15:00",
    side="HOME",
    market="h2h",
    line="",
    t_minus_min="60",
    pinnacle_odds="2.10",
):
    return {
        "captured_at": "2026-05-02 14:00 UTC",
        "home": home, "away": away, "kickoff": kickoff,
        "side": side, "market": market, "line": line,
        "book": "pinnacle", "t_minus_min": t_minus_min,
        "your_book_odds": "", "pinnacle_odds": pinnacle_odds,
        "n_books": "30",
    }


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.touch()
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def test_drift_index_returns_none_when_no_file(monkeypatch, tmp_path):
    monkeypatch.setattr(cs, "DRIFT_CSV", tmp_path / "nonexistent.csv")
    assert cs._load_drift_index() is None


def test_drift_index_pairs_t60_with_t1_only(monkeypatch, tmp_path):
    drift = tmp_path / "drift.csv"
    monkeypatch.setattr(cs, "DRIFT_CSV", drift)
    rows = [
        _drift_row(home="Arsenal", away="Chelsea", t_minus_min="60", pinnacle_odds="2.10"),
        _drift_row(home="Arsenal", away="Chelsea", t_minus_min="1",  pinnacle_odds="1.95"),
        _drift_row(home="Liverpool", away="Tottenham Hotspur", t_minus_min="60", pinnacle_odds="3.40"),
    ]
    _write_csv(drift, rows)
    idx = cs._load_drift_index()
    assert idx is not None
    arsenal_key = ("2026-05-02 15:00", "Arsenal", "Chelsea", "h2h", "", "HOME")
    assert arsenal_key in idx
    liverpool_key = ("2026-05-02 15:00", "Liverpool", "Tottenham Hotspur", "h2h", "", "AWAY")
    assert liverpool_key not in idx


def test_drift_index_skips_unparseable_rows(monkeypatch, tmp_path):
    drift = tmp_path / "drift.csv"
    monkeypatch.setattr(cs, "DRIFT_CSV", drift)
    rows = [
        _drift_row(t_minus_min="60", pinnacle_odds=""),
        _drift_row(t_minus_min="60", pinnacle_odds="not_a_num"),
        _drift_row(t_minus_min="abc", pinnacle_odds="2.10"),
    ]
    _write_csv(drift, rows)
    assert cs._load_drift_index() is None


# ── _stats math ───────────────────────────────────────────────────────────────

def _row(**kwargs):
    """Minimal row dict for _stats tests."""
    defaults = {
        "clv_pct": None, "edge": None, "edge_gross": None,
        "result": "pending", "pnl": None, "stake": "50",
        "market": "h2h", "consensus": None, "model_signal": None,
        "side": "HOME", "home": "A", "away": "B",
        "kickoff": "2026-05-02 15:00", "line": "", "book_id": 1,
        "book": "skybet",
    }
    defaults.update(kwargs)
    return defaults


def test_stats_zero_rows_returns_none_metrics():
    s = cs._stats([])
    assert s["n_bets"] == 0
    assert s["avg_clv"] is None
    assert s["settled"] == 0


def test_stats_single_clv_row():
    rows = [_row(clv_pct=0.04)]
    s = cs._stats(rows)
    assert s["n_with_clv"] == 1
    assert s["avg_clv"] == pytest.approx(0.04)
    assert s["median_clv"] == pytest.approx(0.04)
    assert s["ci95_half"] is None


def test_stats_ci_math():
    clvs = [0.04, 0.05, -0.02, 0.10, 0.00]
    rows = [_row(clv_pct=v) for v in clvs]
    s = cs._stats(rows)
    expected_mean = sum(clvs) / len(clvs)
    expected_se = statistics.stdev(clvs) / math.sqrt(len(clvs))
    assert s["avg_clv"] == pytest.approx(expected_mean)
    assert s["ci95_half"] == pytest.approx(1.96 * expected_se)


def test_stats_settled_pl():
    rows = [
        _row(result="W", pnl=10.0, stake="10"),
        _row(result="L", pnl=-10.0, stake="10"),
        _row(result="W", pnl=10.0, stake="10"),
        _row(result="W", pnl=10.0, stake="10"),
        _row(result="L", pnl=-10.0, stake="10"),
    ]
    s = cs._stats(rows)
    assert s["settled"] == 5
    assert s["wins"] == 3
    assert s["win_pct"] == pytest.approx(0.6)
    assert s["total_pnl"] == pytest.approx(10.0)
    assert s["roi_pct"] == pytest.approx(10.0 / 50.0 * 100)


def test_stats_settled_below_5_no_roi():
    rows = [_row(result="W", pnl=10.0, stake="10")] * 4
    s = cs._stats(rows)
    assert s["settled"] == 4
    assert s["roi_pct"] is not None  # computed; formatting suppressed by caller


# ── build_report integration ──────────────────────────────────────────────────

def test_build_report_requires_db_env(fresh_env, monkeypatch, tmp_path):
    """build_report() exits non-zero when BETS_DB_WRITE is unset."""
    # BETS_DB_WRITE not set (fresh_env stripped it)
    db = _make_db()
    helper = _SqliteRepo(db, tmp_path)
    with pytest.raises(SystemExit) as exc_info:
        cs.build_report(repo=helper.repo)
    assert exc_info.value.code != 0


def test_build_report_empty_db_shows_no_data(db_repo, monkeypatch, tmp_path):
    conn, repo = db_repo
    # fetch_paper_bets_for_compare returns [] (empty DB) — report shows configured variants
    report = cs.build_report(repo=repo)
    from src.betting.strategies import STRATEGIES
    for s in STRATEGIES:
        assert s.name in report


def test_build_report_includes_all_strategies(db_repo, monkeypatch, tmp_path):
    conn, repo = db_repo
    pr = _paper_row_dict(home="Arsenal", away="Chelsea")
    repo.add_paper_bets("A_production", [pr])
    report = cs.build_report(repo=repo)
    from src.betting.strategies import STRATEGIES
    for s in STRATEGIES:
        assert s.name in report, f"{s.name} missing from report"


_TEAMS = [
    ("Arsenal", "Chelsea"), ("Liverpool", "Man City"), ("Tottenham", "West Ham"),
    ("Everton", "Wolves"), ("Leicester", "Newcastle"), ("Aston Villa", "Brighton"),
    ("Fulham", "Brentford"), ("Burnley", "Luton"), ("Sheffield Utd", "Nott'm Forest"),
    ("Crystal Palace", "Bournemouth"),
]


def _unique_rows(n: int, **kwargs) -> list[dict]:
    """Generate n unique paper_bet rows using different home/away pairs."""
    return [
        _paper_row_dict(home=_TEAMS[i % len(_TEAMS)][0],
                        away=_TEAMS[i % len(_TEAMS)][1],
                        **kwargs)
        for i in range(n)
    ]


def _insert_paper_rows_with_clv(conn, repo, strategy: str, rows: list[dict]) -> None:
    """Insert paper bets and backfill CLV so fetch_paper_bets_for_compare returns populated rows."""
    repo.add_paper_bets(strategy, rows)
    # Backfill CLV + result for all newly inserted rows via settle_paper_bet
    cur = conn.cursor()
    pb_rows = cur.execute(
        "SELECT p.fixture_id, p.side, p.market, p.line, bk.name "
        "FROM paper_bets p JOIN books bk ON bk.id = p.book_id "
        "JOIN strategies s ON s.id = p.strategy_id WHERE s.name = ?",
        (strategy,)
    ).fetchall()
    for (fid, side, market, line, book) in pb_rows:
        # Match back to input row to get the intended clv_pct/result
        # Use a fixed CLV for simplicity; per-row values are set in specific tests
        repo.settle_paper_bet(
            strategy, fid, side, market, line, book,
            result=None,
            pnl=None,
            pin_prob=0.50,
            clv_pct=0.04,
        )


def _insert_rows_with_custom_clv(conn, repo, strategy: str, rows: list[dict]) -> None:
    """Insert paper bets with per-row CLV and result values.

    Each row dict should carry 'clv_pct', 'result', 'pnl' fields.
    """
    repo.add_paper_bets(strategy, rows)
    cur = conn.cursor()
    pb_rows = cur.execute(
        "SELECT p.fixture_id, p.side, p.market, p.line, bk.name, f.home, f.away "
        "FROM paper_bets p JOIN books bk ON bk.id = p.book_id "
        "JOIN strategies s ON s.id = p.strategy_id "
        "JOIN fixtures f ON f.id = p.fixture_id WHERE s.name = ?",
        (strategy,)
    ).fetchall()
    for (fid, side, market, line, book, home, away) in pb_rows:
        # Find the matching input row
        matched = next(
            (r for r in rows if r.get("home") == home and r.get("away") == away),
            None,
        )
        if matched is None:
            continue
        result = matched.get("result") or None
        if result == "":
            result = None
        pnl_val = matched.get("pnl")
        try:
            pnl_f = float(pnl_val) if pnl_val not in (None, "") else None
        except (TypeError, ValueError):
            pnl_f = None
        clv_raw = matched.get("clv_pct")
        try:
            clv_f = float(clv_raw) if clv_raw not in (None, "") else None
        except (TypeError, ValueError):
            clv_f = None
        repo.settle_paper_bet(
            strategy, fid, side, market, line, book,
            result=result,
            pnl=pnl_f,
            pin_prob=0.50 if clv_f is not None else None,
            clv_pct=clv_f,
        )


def test_build_report_sorted_by_avg_clv(db_repo, tmp_path):
    conn, repo = db_repo
    high_rows = _unique_rows(3, clv_pct="0.08")
    low_rows  = _unique_rows(3, clv_pct="-0.02")
    _insert_rows_with_custom_clv(conn, repo, "A_production", high_rows)
    _insert_rows_with_custom_clv(conn, repo, "B_power_devig", low_rows)
    report = cs.build_report(repo=repo)
    a_idx = report.index("A_production")
    b_idx = report.index("B_power_devig")
    assert a_idx < b_idx


def test_build_report_new_pnl_columns_appear(db_repo, tmp_path):
    conn, repo = db_repo
    rows = _unique_rows(5, result="W", pnl="10.0", clv_pct="0.04")
    _insert_rows_with_custom_clv(conn, repo, "A_production", rows)
    report = cs.build_report(repo=repo)
    assert "Settled" in report
    assert "Win %" in report
    assert "ROI %" in report


def test_build_report_settled_less_than_5_shows_dashes(db_repo, tmp_path):
    conn, repo = db_repo
    rows = _unique_rows(4, result="W", pnl="10.0", clv_pct="0.04")
    _insert_rows_with_custom_clv(conn, repo, "A_production", rows)
    report = cs.build_report(repo=repo)
    a_line = next(
        (line for line in report.split("\n") if "A_production" in line and "|" in line),
        None,
    )
    assert a_line is not None
    assert "4" in a_line
    cols = [c.strip() for c in a_line.split("|")]
    pnl_cols = [c for c in cols if c][-3:]
    assert pnl_cols[1] == "—"
    assert pnl_cols[2] == "—"


def test_build_report_low_n_marker(db_repo, tmp_path):
    conn, repo = db_repo
    repo.add_paper_bets("A_production", [_paper_row_dict(home="Arsenal", away="Chelsea")])
    report = cs.build_report(repo=repo)
    assert "[low n] A_production" in report


def test_build_report_per_sport_section(db_repo, tmp_path):
    conn, repo = db_repo
    rows = [_paper_row_dict(sport="EPL", home="Arsenal", away="Chelsea", clv_pct="0.04")]
    _insert_rows_with_custom_clv(conn, repo, "A_production", rows)
    report = cs.build_report(repo=repo)
    assert "## CLV by sport" in report


def test_build_report_no_per_sport_when_no_clv(db_repo, tmp_path):
    conn, repo = db_repo
    repo.add_paper_bets(
        "A_production",
        [_paper_row_dict(clv_pct="", pinnacle_close_prob="", home="Arsenal", away="Chelsea")]
    )
    report = cs.build_report(repo=repo)
    assert "## CLV by sport" not in report


def test_build_report_per_confidence_section(db_repo, tmp_path):
    conn, repo = db_repo
    rows = (
        _unique_rows(5, confidence="HIGH", clv_pct="0.04")
        + [_paper_row_dict(home=_TEAMS[i + 5][0], away=_TEAMS[i + 5][1],
                           confidence="MED", clv_pct="0.02") for i in range(5)]
    )
    _insert_rows_with_custom_clv(conn, repo, "A_production", rows)
    report = cs.build_report(repo=repo)
    assert "## CLV by confidence" in report
    high_idx = report.index("| HIGH | A_production")
    med_idx  = report.index("| MED | A_production")
    assert high_idx < med_idx


def test_build_report_per_market_section(db_repo, tmp_path):
    conn, repo = db_repo
    h2h_rows    = _unique_rows(5, market="h2h", clv_pct="0.04")
    totals_rows = [_paper_row_dict(home=_TEAMS[i + 5][0], away=_TEAMS[i + 5][1],
                                   market="totals", line="2.5", clv_pct="0.03")
                   for i in range(5)]
    _insert_rows_with_custom_clv(conn, repo, "A_production", h2h_rows + totals_rows)
    report = cs.build_report(repo=repo)
    assert "## CLV by market" in report


def test_build_report_model_signal_section(db_repo, tmp_path):
    conn, repo = db_repo
    agrees_rows    = _unique_rows(5, model_signal="+0.020", clv_pct="0.04")
    disagrees_rows = [_paper_row_dict(home=_TEAMS[i + 5][0], away=_TEAMS[i + 5][1],
                                      model_signal="-0.020", clv_pct="0.01")
                      for i in range(5)]
    _insert_rows_with_custom_clv(conn, repo, "A_production", agrees_rows + disagrees_rows)
    report = cs.build_report(repo=repo)
    assert "## CLV by model signal" in report


def test_build_report_ci_with_two_clv_rows(db_repo, tmp_path):
    conn, repo = db_repo
    rows = [
        _paper_row_dict(clv_pct="0.04", home="Arsenal", away="Chelsea"),
        _paper_row_dict(clv_pct="0.05", home="Liverpool", away="Man City"),
    ]
    _insert_rows_with_custom_clv(conn, repo, "A_production", rows)
    report = cs.build_report(repo=repo)
    a_line = next(line for line in report.split("\n") if "A_production" in line and "|" in line)
    assert "±" in a_line


def test_build_report_sample_size_note(db_repo, tmp_path):
    conn, repo = db_repo
    repo.add_paper_bets("A_production", [_paper_row_dict(home="Arsenal", away="Chelsea")])
    report = cs.build_report(repo=repo)
    assert "Sample size note" in report


# ── _filter_to_current_window (no-op in DB mode) ─────────────────────────────

def test_filter_returns_input_unchanged():
    rows = [{"x": 1}, {"x": 2}]
    assert cs._filter_to_current_window(rows) == rows


def test_filter_handles_empty_input():
    assert cs._filter_to_current_window([]) == []
