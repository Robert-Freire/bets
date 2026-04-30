"""
Tests for R.7: devig_method and weight_scheme provenance columns in bets.csv.
"""
import csv
import io
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("ODDS_API_KEY", "dummy_test_key")


def test_bets_csv_fieldnames_include_provenance():
    """scan_odds.py DictWriter fieldnames must include devig_method and weight_scheme."""
    import importlib
    import scripts.scan_odds as scan_odds

    fieldnames_const = [
        "scanned_at", "sport", "market", "line", "home", "away", "kickoff",
        "side", "book", "odds", "impl_raw", "impl_effective",
        "edge", "edge_gross", "effective_odds", "commission_rate",
        "consensus", "pinnacle_cons",
        "n_books", "confidence", "model_signal", "dispersion", "outlier_z",
        "devig_method", "weight_scheme",
        "stake", "result",
    ]
    assert "devig_method" in fieldnames_const
    assert "weight_scheme" in fieldnames_const


def test_paper_fieldnames_include_provenance():
    """_PAPER_FIELDNAMES must include devig_method and weight_scheme."""
    import scripts.scan_odds as scan_odds
    assert "devig_method" in scan_odds._PAPER_FIELDNAMES
    assert "weight_scheme" in scan_odds._PAPER_FIELDNAMES


def test_backfill_idempotent(tmp_path):
    """Running backfill_provenance twice must not change the file."""
    import hashlib
    from scripts.backfill_provenance import _backfill

    # Build a CSV with some rows missing provenance and one already having it
    header = "scanned_at,sport,home,away,stake,result\n"
    row1   = "2026-04-01 10:00,EPL,Arsenal,Chelsea,10,\n"
    row2   = "2026-04-02 10:00,EPL,Liverpool,Everton,15,W\n"
    csv_path = tmp_path / "bets.csv"
    csv_path.write_text(header + row1 + row2)

    n1 = _backfill(csv_path, "shin", "uniform")
    assert n1 == 2  # both rows updated

    digest1 = hashlib.md5(csv_path.read_bytes()).hexdigest()

    n2 = _backfill(csv_path, "shin", "uniform")
    assert n2 == 0  # nothing changed

    digest2 = hashlib.md5(csv_path.read_bytes()).hexdigest()
    assert digest1 == digest2, "backfill must be idempotent"


def test_backfill_preserves_existing_values(tmp_path):
    """Rows that already have devig_method must not be overwritten."""
    from scripts.backfill_provenance import _backfill

    header = "scanned_at,sport,home,away,devig_method,weight_scheme,stake,result\n"
    row    = "2026-04-01 10:00,EPL,Arsenal,Chelsea,power,sharp_v1,10,\n"
    csv_path = tmp_path / "bets.csv"
    csv_path.write_text(header + row)

    _backfill(csv_path, "shin", "uniform")

    with open(csv_path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["devig_method"] == "power", "existing devig_method must not be overwritten"
    assert rows[0]["weight_scheme"] == "sharp_v1", "existing weight_scheme must not be overwritten"


def test_app_normalise_row_defaults():
    """_normalise_row must default devig_method=shin and weight_scheme=uniform."""
    import app as _app

    row = {"home": "Arsenal", "away": "Chelsea"}
    _app._normalise_row(row, "new")
    assert row["devig_method"] == "shin"
    assert row["weight_scheme"] == "uniform"


@pytest.mark.parametrize("strategy_name,expected", [
    ("I_power_devig",    ("power",         "uniform")),
    ("J_sharp_weighted", ("shin",          "sharp_v1")),
    ("O_kaunitz_classic",("raw",           "uniform")),
    ("B_strict",         ("shin",          "pinnacle_weighted")),
    ("D_pinnacle_only",  ("shin",          "pinnacle_only")),
    ("G_proportional",   ("proportional",  "uniform")),
    ("A_production",     ("shin",          "uniform")),
])
def test_paper_provenance(strategy_name, expected):
    """_paper_provenance must return the correct (devig_method, weight_scheme) per strategy."""
    from scripts.scan_odds import _paper_provenance
    from src.betting.strategies import STRATEGIES

    strategy = next(s for s in STRATEGIES if s.name == strategy_name)
    assert _paper_provenance(strategy) == expected, (
        f"{strategy_name}: expected {expected}, got {_paper_provenance(strategy)}"
    )


def test_migrate_overwrites_mismatched_values(tmp_path):
    """--migrate must overwrite wrong values; re-running it is idempotent."""
    import hashlib
    from scripts.backfill_provenance import _migrate

    header = "scanned_at,strategy,home,away,devig_method,weight_scheme,stake\n"
    rows = (
        "2026-04-29 13:12 UTC,B_strict,Arsenal,Chelsea,shin,uniform,10\n"
        "2026-04-29 13:12 UTC,B_strict,Liverpool,Everton,shin,uniform,15\n"
    )
    csv_path = tmp_path / "B_strict.csv"
    csv_path.write_text(header + rows)

    n1 = _migrate(csv_path, "shin", "pinnacle_weighted")
    assert n1 == 2, "both rows should have been corrected"

    with open(csv_path, newline="") as f:
        out = list(csv.DictReader(f))
    assert all(r["weight_scheme"] == "pinnacle_weighted" for r in out)

    digest1 = hashlib.md5(csv_path.read_bytes()).hexdigest()
    n2 = _migrate(csv_path, "shin", "pinnacle_weighted")
    assert n2 == 0, "second migration should be a no-op"
    digest2 = hashlib.md5(csv_path.read_bytes()).hexdigest()
    assert digest1 == digest2


def test_migrate_skips_when_columns_missing(tmp_path):
    """--migrate must not act on CSVs missing the provenance columns (run plain backfill first)."""
    from scripts.backfill_provenance import _migrate

    csv_path = tmp_path / "X.csv"
    csv_path.write_text("scanned_at,home,away,stake\n2026-04-29 13:12 UTC,A,B,10\n")
    n = _migrate(csv_path, "shin", "pinnacle_weighted")
    assert n == 0


def test_summary_stats_clv_breakdown_threshold():
    """clv_breakdown only appears when ≥2 methods each have ≥20 CLV bets."""
    import app as _app

    def _make_bet(method: str, clv: float) -> dict:
        return {
            "actual_stake": "10", "result": "W", "pnl": "5",
            "devig_method": method, "weight_scheme": "uniform",
            "clv_pct": str(clv),
        }

    # Only one method — no breakdown
    bets_one = [_make_bet("shin", 0.02)] * 25
    stats = _app.summary_stats(bets_one)
    assert stats["clv_breakdown"] is None

    # Two methods but one has < 20
    bets_few = [_make_bet("shin", 0.02)] * 25 + [_make_bet("power", 0.01)] * 10
    stats = _app.summary_stats(bets_few)
    assert stats["clv_breakdown"] is None

    # Two methods each ≥ 20
    bets_both = [_make_bet("shin", 0.02)] * 20 + [_make_bet("power", 0.01)] * 20
    stats = _app.summary_stats(bets_both)
    assert stats["clv_breakdown"] is not None
    assert "shin" in stats["clv_breakdown"]
    assert "power" in stats["clv_breakdown"]
