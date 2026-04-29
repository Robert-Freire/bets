"""
Tests for per-row CLV recomputation in closing_line.update_csv_clv (Phase 5.8.3).
"""
import csv
import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# closing_line.py requires ODDS_API_KEY at import time
os.environ.setdefault("ODDS_API_KEY", "dummy_test_key")

from scripts.closing_line import update_csv_clv


PAPER_HEADER = (
    "scanned_at,strategy,sport,market,line,home,away,kickoff,"
    "side,book,odds,impl_raw,impl_effective,edge,edge_gross,effective_odds,"
    "commission_rate,consensus,pinnacle_cons,n_books,confidence,model_signal,"
    "dispersion,outlier_z,stake,pinnacle_close_prob,clv_pct\n"
)

FIXTURE_ROW = (
    "2026-05-01 10:00,{strategy},EPL,h2h,,Arsenal,Chelsea,"
    "2026-05-03 15:00,HOME,{book},{odds},0.48,0.50,0.04,0.04,"
    "{eff_odds},{comm},0.52,0.50,28,MED,+0.01,0.02,0.1,35,,\n"
)


def _make_csv(path: Path, strategy: str, book: str, odds: float):
    eff_odds = round(odds * (1 - 0.05) if "betfair" in book else odds, 4)
    comm = 0.05 if "betfair" in book else 0.02 if "smarkets" in book else 0.0
    path.write_text(
        PAPER_HEADER
        + FIXTURE_ROW.format(
            strategy=strategy,
            book=book,
            odds=odds,
            eff_odds=eff_odds,
            comm=comm,
        )
    )


def test_per_row_clv_differs_by_book(tmp_path):
    """Betfair and Williamhill on the same fixture must get different CLV values."""
    path_a = tmp_path / "A_production.csv"
    path_e = tmp_path / "E_exchanges_only.csv"

    _make_csv(path_a, "A_production",    "williamhill", 2.1)
    _make_csv(path_e, "E_exchanges_only", "betfair_ex_uk", 2.1)

    pin_prob = 0.50
    updates = {
        ("Arsenal", "Chelsea", "2026-05-03 15:00", "HOME", "h2h", ""): {
            "pinnacle_close_prob": str(pin_prob),
            "clv_pct": "0.05",  # stale production CLV — should be ignored per-row
        }
    }

    update_csv_clv(path_a, updates)
    update_csv_clv(path_e, updates)

    def _read_clv(path):
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
        return float(rows[0]["clv_pct"])

    clv_wh = _read_clv(path_a)
    clv_bf = _read_clv(path_e)

    # Betfair commission (5%) shrinks effective odds → smaller CLV
    assert clv_bf < clv_wh, (
        f"Betfair CLV ({clv_bf:.4f}) should be < Williamhill CLV ({clv_wh:.4f})"
    )


def test_clv_not_overwritten(tmp_path):
    """Rows that already have pinnacle_close_prob set must not be overwritten."""
    path = tmp_path / "A_production.csv"
    path.write_text(
        PAPER_HEADER
        + "2026-05-01 10:00,A_production,EPL,h2h,,Arsenal,Chelsea,"
        "2026-05-03 15:00,HOME,williamhill,2.1,0.48,0.48,0.04,0.04,"
        "2.1,0.0,0.52,0.50,28,MED,+0.01,0.02,0.1,35,0.48,0.008\n"
    )
    updates = {
        ("Arsenal", "Chelsea", "2026-05-03 15:00", "HOME", "h2h", ""): {
            "pinnacle_close_prob": "0.55",
            "clv_pct": "0.05",
        }
    }
    update_csv_clv(path, updates)
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    # Original values preserved
    assert rows[0]["pinnacle_close_prob"] == "0.48"
    assert rows[0]["clv_pct"] == "0.008"
