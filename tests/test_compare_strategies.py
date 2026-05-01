"""
Tests for scripts/compare_strategies.py — covers the C.1–C.9 phases of
PLAN_COMPARE_2026-04 (0-bet variants, 95% CI, per-sport / per-confidence /
per-market / model-signal slicing, drift→you %, median CLV, low-n marker,
sample-size warning).

Uses synthetic paper CSVs and a synthetic logs/drift.csv pointed at by
monkeypatching the module-level paths. Designed to catch silent slicing
regressions if the paper-CSV schema or drift-CSV schema drifts.
"""
import csv
import importlib
import math
import statistics
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.compare_strategies as cs  # noqa: E402


# ── fixture builders ──────────────────────────────────────────────────────────


def _paper_row(
    *,
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
):
    """Build one paper-CSV row with realistic values; columns mirror scan_odds.py:_PAPER_FIELDNAMES."""
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
        "stake": "50",
        "pinnacle_close_prob": pinnacle_close_prob,
        "clv_pct": clv_pct,
    }


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
    """Build one drift-CSV row; columns mirror closing_line.py drift writer."""
    return {
        "captured_at": "2026-05-02 14:00 UTC",
        "home": home,
        "away": away,
        "kickoff": kickoff,
        "side": side,
        "market": market,
        "line": line,
        "book": "pinnacle",
        "t_minus_min": t_minus_min,
        "your_book_odds": "",
        "pinnacle_odds": pinnacle_odds,
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


@pytest.fixture
def paper_dir(tmp_path, monkeypatch):
    """Empty paper dir; tests populate it then monkeypatch module paths."""
    paper = tmp_path / "paper"
    paper.mkdir()
    drift = tmp_path / "drift.csv"
    out = tmp_path / "OUT.md"
    monkeypatch.setattr(cs, "PAPER_DIR", paper)
    monkeypatch.setattr(cs, "DRIFT_CSV", drift)
    monkeypatch.setattr(cs, "OUT_DOC", out)
    return paper


# ── _model_bucket edge cases (C.8) ────────────────────────────────────────────


@pytest.mark.parametrize("signal,expected", [
    ("?", "no_signal"),
    ("", "no_signal"),
    (None, "no_signal"),
    ("+0.123", "agrees"),
    ("-0.045", "disagrees"),
    ("0.000", "disagrees"),    # zero edge → "model edge ≤ 0" → disagrees
    ("garbage", "no_signal"),
    ("+0", "disagrees"),
])
def test_model_bucket(signal, expected):
    assert cs._model_bucket(signal) == expected


# ── _load_drift_index schema + pairing ────────────────────────────────────────


def test_drift_index_returns_none_when_no_file(paper_dir, monkeypatch):
    # DRIFT_CSV path doesn't exist
    assert cs._load_drift_index() is None


def test_drift_index_pairs_t60_with_t1_only(paper_dir):
    drift = cs.DRIFT_CSV
    rows = [
        # Arsenal: full pair → should be in index
        _drift_row(home="Arsenal", away="Chelsea", t_minus_min="60", pinnacle_odds="2.10"),
        _drift_row(home="Arsenal", away="Chelsea", t_minus_min="1",  pinnacle_odds="1.95"),
        # Liverpool: only T-60 → should NOT be in index
        _drift_row(home="Liverpool", away="Tottenham Hotspur", t_minus_min="60", pinnacle_odds="3.40"),
        # Chelsea/Arsenal mid-window T-15 → ignored entirely
        _drift_row(home="Arsenal", away="Chelsea", t_minus_min="15", pinnacle_odds="2.05"),
    ]
    _write_csv(drift, rows)

    idx = cs._load_drift_index()
    assert idx is not None
    arsenal_key = ("2026-05-02 15:00", "Arsenal", "Chelsea", "h2h", "", "HOME")
    assert arsenal_key in idx
    liverpool_key = ("2026-05-02 15:00", "Liverpool", "Tottenham Hotspur", "h2h", "", "AWAY")
    assert liverpool_key not in idx
    # Probs are raw 1/odds at T-60 and T-1
    t60, t1 = idx[arsenal_key]
    assert t60 == pytest.approx(1 / 2.10)
    assert t1 == pytest.approx(1 / 1.95)


def test_drift_index_skips_unparseable_rows(paper_dir):
    drift = cs.DRIFT_CSV
    rows = [
        _drift_row(t_minus_min="60", pinnacle_odds=""),         # missing odds
        _drift_row(t_minus_min="60", pinnacle_odds="not_a_num"),  # garbage odds
        _drift_row(t_minus_min="abc", pinnacle_odds="2.10"),    # garbage t_minus
    ]
    _write_csv(drift, rows)
    assert cs._load_drift_index() is None  # no valid pairs


# ── _stats math (C.2 CI, C.6 median, C.4 drift_pct) ───────────────────────────


def test_stats_zero_rows_returns_none_metrics():
    s = cs._stats([])
    assert s["n_bets"] == 0
    assert s["n_with_clv"] == 0
    assert s["avg_clv"] is None
    assert s["median_clv"] is None
    assert s["ci95_half"] is None


def test_stats_single_clv_row_no_ci_but_has_median():
    rows = [_paper_row(clv_pct="0.04")]
    s = cs._stats(rows)
    assert s["n_with_clv"] == 1
    assert s["avg_clv"] == pytest.approx(0.04)
    assert s["median_clv"] == pytest.approx(0.04)
    assert s["ci95_half"] is None  # n=1 → no SE


def test_stats_ci_math_matches_se_formula():
    clvs = [0.04, 0.05, -0.02, 0.10, 0.00]
    rows = [_paper_row(clv_pct=str(v)) for v in clvs]
    s = cs._stats(rows)
    expected_mean = sum(clvs) / len(clvs)
    expected_se = statistics.stdev(clvs) / math.sqrt(len(clvs))
    expected_ci = 1.96 * expected_se
    assert s["avg_clv"] == pytest.approx(expected_mean)
    assert s["ci95_half"] == pytest.approx(expected_ci)
    assert s["median_clv"] == pytest.approx(statistics.median(clvs))


def test_stats_drift_pct_correct_direction(paper_dir):
    """T-60 prob 0.476 → T-1 prob 0.513 means market moved toward HOME side."""
    drift = cs.DRIFT_CSV
    _write_csv(drift, [
        _drift_row(t_minus_min="60", pinnacle_odds="2.10"),
        _drift_row(t_minus_min="1",  pinnacle_odds="1.95"),
    ])
    idx = cs._load_drift_index()
    rows = [_paper_row(side="HOME")]  # paper bet on HOME
    s = cs._stats(rows, drift_index=idx)
    assert s["drift_pct"] == pytest.approx(1.0)


def test_stats_drift_pct_against_you(paper_dir):
    """T-60 prob 0.513 → T-1 prob 0.476 means market moved AWAY from HOME side."""
    drift = cs.DRIFT_CSV
    _write_csv(drift, [
        _drift_row(t_minus_min="60", pinnacle_odds="1.95"),
        _drift_row(t_minus_min="1",  pinnacle_odds="2.10"),
    ])
    idx = cs._load_drift_index()
    rows = [_paper_row(side="HOME")]
    s = cs._stats(rows, drift_index=idx)
    assert s["drift_pct"] == pytest.approx(0.0)


# ── build_report integration tests ────────────────────────────────────────────


def test_report_includes_all_strategies_even_when_no_csv(paper_dir):
    """C.1: every entry in STRATEGIES must show up, even if its CSV is absent."""
    # Only A_production has data; everyone else should still appear as 0-bet
    _write_csv(paper_dir / "A_production.csv", [_paper_row()])

    report = cs.build_report()
    from src.betting.strategies import STRATEGIES
    for s in STRATEGIES:
        assert s.name in report, f"{s.name} missing from report"


def test_report_renders_ci_with_at_least_two_clv_rows(paper_dir):
    """C.2: '± x.xx%' formatting present when n_with_clv ≥ 2."""
    rows = [_paper_row(clv_pct="0.04"), _paper_row(clv_pct="0.05")]
    _write_csv(paper_dir / "A_production.csv", rows)
    report = cs.build_report()
    # Expect a "± N.NN%" token in A_production's row
    a_line = next(line for line in report.split("\n") if "A_production" in line and "|" in line)
    assert "±" in a_line, f"95% CI '±' marker missing: {a_line}"


def test_report_low_n_marker_present(paper_dir):
    """C.9: rows with n_with_clv < 10 get '[low n] ' prefix."""
    _write_csv(paper_dir / "A_production.csv", [_paper_row()])  # 1 CLV row
    report = cs.build_report()
    assert "[low n] A_production" in report


def test_report_sample_size_warning_present(paper_dir):
    """C.9: sample-size guardrail line at top of report."""
    _write_csv(paper_dir / "A_production.csv", [_paper_row()])
    report = cs.build_report()
    assert "Sample size note" in report


def test_report_per_sport_section_when_data_qualifies(paper_dir):
    """C.3: per-sport section appears when at least one (variant, sport) pair qualifies."""
    # A_production with 1 EPL bet (qualifies on the baseline rule)
    _write_csv(paper_dir / "A_production.csv", [_paper_row(sport="EPL")])
    report = cs.build_report()
    assert "## CLV by sport" in report
    assert "| EPL | A_production |" in report


def test_report_per_sport_section_omitted_when_no_clv(paper_dir):
    """C.3: section is omitted entirely (no empty header) when no rows qualify."""
    # Row with no CLV → doesn't qualify for per-sport baseline rule
    _write_csv(paper_dir / "A_production.csv", [_paper_row(clv_pct="", pinnacle_close_prob="")])
    report = cs.build_report()
    assert "## CLV by sport" not in report


def test_report_per_confidence_section_renders(paper_dir):
    """C.5: HIGH→MED→LOW order, n≥5 gate per (variant, confidence)."""
    rows = [_paper_row(confidence="HIGH", clv_pct=str(0.01 * i)) for i in range(5)]
    rows += [_paper_row(confidence="MED", clv_pct=str(0.01 * i)) for i in range(5)]
    _write_csv(paper_dir / "A_production.csv", rows)
    report = cs.build_report()
    assert "## CLV by confidence" in report
    high_idx = report.index("| HIGH | A_production")
    med_idx  = report.index("| MED | A_production")
    assert high_idx < med_idx, "HIGH must come before MED in confidence table"


def test_report_per_market_section_renders(paper_dir):
    """C.7: h2h→totals→btts order, n≥5 gate."""
    rows = [_paper_row(market="h2h", clv_pct=str(0.01 * i)) for i in range(5)]
    rows += [_paper_row(market="totals", line="2.5", clv_pct=str(0.01 * i)) for i in range(5)]
    _write_csv(paper_dir / "A_production.csv", rows)
    report = cs.build_report()
    assert "## CLV by market" in report
    h2h_idx = report.index("| h2h | A_production")
    tot_idx = report.index("| totals | A_production")
    assert h2h_idx < tot_idx, "h2h must come before totals in market table"


def test_report_model_signal_section_renders(paper_dir):
    """C.8: agrees→disagrees→no_signal order, n≥5 gate."""
    rows = [_paper_row(model_signal="+0.020", clv_pct=str(0.01 * i)) for i in range(5)]
    rows += [_paper_row(model_signal="-0.020", clv_pct=str(0.01 * i)) for i in range(5)]
    _write_csv(paper_dir / "A_production.csv", rows)
    report = cs.build_report()
    assert "## CLV by model signal" in report
    agr_idx = report.index("| agrees | A_production")
    dis_idx = report.index("| disagrees | A_production")
    assert agr_idx < dis_idx, "agrees must come before disagrees"


def test_report_drift_warning_fires_on_extreme_value(paper_dir):
    """C.4: emit a sanity warning when drift_pct ∈ {0.0, 1.0} with n_bets ≥ 10."""
    # 10 paper rows all on HOME side, drift index says HOME prob rose for all
    rows = [
        _paper_row(home=f"Team{i}", away=f"Opp{i}", kickoff=f"2026-05-02 1{i}:00", side="HOME")
        for i in range(10)
    ]
    _write_csv(paper_dir / "A_production.csv", rows)

    drift_rows = []
    for i in range(10):
        drift_rows.append(_drift_row(home=f"Team{i}", away=f"Opp{i}",
                                     kickoff=f"2026-05-02 1{i}:00", side="HOME",
                                     t_minus_min="60", pinnacle_odds="2.10"))
        drift_rows.append(_drift_row(home=f"Team{i}", away=f"Opp{i}",
                                     kickoff=f"2026-05-02 1{i}:00", side="HOME",
                                     t_minus_min="1",  pinnacle_odds="1.95"))
    _write_csv(cs.DRIFT_CSV, drift_rows)

    report = cs.build_report()
    assert "Drift sanity warnings" in report
    assert "drift=100%" in report


def test_report_does_not_crash_when_paper_dir_empty(paper_dir):
    """C.1: 0-bet variants from STRATEGIES still render even with no CSVs at all."""
    report = cs.build_report()
    from src.betting.strategies import STRATEGIES
    # Every variant should appear with 0 bets
    for s in STRATEGIES:
        assert s.name in report
    assert "[low n]" in report  # all variants tagged low-n


def test_paper_csv_schema_compatible_with_scan_odds(monkeypatch):
    """Regression: if scan_odds.py adds/removes a paper-CSV column, the test fixture
    columns must still be a subset of what scan_odds writes — otherwise this test
    file is masking schema drift.
    """
    monkeypatch.setenv("ODDS_API_KEY", "dummy")
    # Force fresh import so the env-var check at module top runs again
    sys.modules.pop("scripts.scan_odds", None)
    scan_odds = importlib.import_module("scripts.scan_odds")

    fixture_cols = set(_paper_row().keys())
    actual_cols = set(scan_odds._PAPER_FIELDNAMES)
    extra_in_fixture = fixture_cols - actual_cols
    assert not extra_in_fixture, (
        f"Test fixture has columns absent from scan_odds._PAPER_FIELDNAMES: {extra_in_fixture}. "
        "Update _paper_row() to match the current schema."
    )


# ── R.11: eval-window filter ─────────────────────────────────────────────────


def test_filter_returns_input_when_all_rows_pre_r11():
    """All rows lack strategy_config_hash → no filtering, return as-is."""
    rows = [_paper_row(), _paper_row(home="Liverpool")]
    for r in rows:
        r.pop("strategy_config_hash", None)
    out = cs._filter_to_current_window(rows)
    assert out == rows


def test_filter_keeps_only_most_recent_hash_window():
    """Mixed rows: old hash + new hash → only new-hash rows kept."""
    old = _paper_row(home="OldA")
    old["strategy_config_hash"] = "AAAAAAAAAAAA"
    old["scanned_at"] = "2026-04-15 10:00 UTC"
    new1 = _paper_row(home="NewA")
    new1["strategy_config_hash"] = "BBBBBBBBBBBB"
    new1["scanned_at"] = "2026-05-01 10:00 UTC"
    new2 = _paper_row(home="NewB")
    new2["strategy_config_hash"] = "BBBBBBBBBBBB"
    new2["scanned_at"] = "2026-05-01 11:00 UTC"

    out = cs._filter_to_current_window([old, new1, new2])
    assert len(out) == 2
    assert all(r["strategy_config_hash"] == "BBBBBBBBBBBB" for r in out)


def test_filter_separates_pre_r11_rows_from_post_r11():
    """Empty hash and non-empty hash are different windows. Most-recent wins."""
    pre = _paper_row(home="Pre")
    pre["strategy_config_hash"] = ""
    pre["scanned_at"] = "2026-04-15 10:00 UTC"
    post = _paper_row(home="Post")
    post["strategy_config_hash"] = "ABCDEF123456"
    post["scanned_at"] = "2026-05-01 10:00 UTC"

    out = cs._filter_to_current_window([pre, post])
    assert len(out) == 1
    assert out[0]["home"] == "Post"


def test_filter_handles_empty_input():
    assert cs._filter_to_current_window([]) == []


def test_report_default_filters_to_current_window(paper_dir):
    """build_report() default: hides older-window rows; mentions filter in header."""
    old = _paper_row(home="OldRow", clv_pct="0.10")
    old["strategy_config_hash"] = "AAAAAAAAAAAA"
    old["scanned_at"] = "2026-04-15 10:00 UTC"
    new = _paper_row(home="NewRow", clv_pct="0.05")
    new["strategy_config_hash"] = "BBBBBBBBBBBB"
    new["scanned_at"] = "2026-05-01 10:00 UTC"
    _write_csv(paper_dir / "A_production.csv", [old, new])

    report = cs.build_report()
    assert "current config window" in report.lower() or "current config" in report.lower()
    assert "A_production" in report and "1/2" in report


def test_report_all_history_includes_old_rows(paper_dir):
    """build_report(all_history=True): includes both windows; header reflects mode."""
    old = _paper_row(home="OldRow", clv_pct="0.10")
    old["strategy_config_hash"] = "AAAAAAAAAAAA"
    old["scanned_at"] = "2026-04-15 10:00 UTC"
    new = _paper_row(home="NewRow", clv_pct="0.05")
    new["strategy_config_hash"] = "BBBBBBBBBBBB"
    new["scanned_at"] = "2026-05-01 10:00 UTC"
    _write_csv(paper_dir / "A_production.csv", [old, new])

    report = cs.build_report(all_history=True)
    assert "ALL HISTORY" in report or "all history" in report.lower()
    assert "1/2" not in report
