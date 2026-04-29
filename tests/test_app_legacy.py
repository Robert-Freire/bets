"""
Tests for app.py dual-file loading and save routing (Phase 5.8.1).
"""
import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


LEGACY_HEADER = (
    "scanned_at,sport,home,away,kickoff,side,book,odds,edge,consensus,"
    "n_books,confidence,model_signal,stake,result,actual_stake,pnl\n"
)
NEW_HEADER = (
    "scanned_at,sport,market,line,home,away,kickoff,side,book,odds,"
    "impl_raw,impl_effective,edge,edge_gross,effective_odds,commission_rate,"
    "consensus,pinnacle_cons,n_books,confidence,model_signal,dispersion,outlier_z,"
    "stake,result\n"
)


def _write(path: Path, header: str, rows: list[str]):
    path.write_text(header + "".join(rows))


def _import_app(monkeypatch, tmp_path):
    """Import app.py with BETS_CSV and BETS_LEGACY_CSV redirected to tmp_path."""
    import importlib
    import app as _app
    monkeypatch.setattr(_app, "BETS_CSV", tmp_path / "bets.csv")
    monkeypatch.setattr(_app, "BETS_LEGACY_CSV", tmp_path / "bets_legacy.csv")
    return _app


def test_load_legacy_only(monkeypatch, tmp_path):
    legacy = tmp_path / "bets_legacy.csv"
    _write(legacy, LEGACY_HEADER, [
        "2026-04-01 10:00 UTC,EPL,Arsenal,Chelsea,2026-04-05 15:00,HOME,bet365,2.1,"
        "0.04,0.48,28,MED,?,35,,30,\n"
    ])
    import app as _app
    monkeypatch.setattr(_app, "BETS_CSV", tmp_path / "bets.csv")
    monkeypatch.setattr(_app, "BETS_LEGACY_CSV", legacy)

    bets = _app.load_bets()
    assert len(bets) == 1
    assert bets[0]["_source"] == "legacy"
    assert bets[0]["market"] == "h2h"
    assert bets[0]["commission_rate"] == "0"


def test_load_legacy_and_new(monkeypatch, tmp_path):
    legacy = tmp_path / "bets_legacy.csv"
    new    = tmp_path / "bets.csv"
    _write(legacy, LEGACY_HEADER, [
        "2026-04-01 10:00 UTC,EPL,Arsenal,Chelsea,2026-04-05 15:00,HOME,bet365,2.1,"
        "0.04,0.48,28,MED,?,35,,30,\n"
    ])
    _write(new, NEW_HEADER, [
        "2026-04-28 10:00 UTC,EPL,h2h,,Liverpool,Man City,2026-05-01 15:00,AWAY,"
        "betfair_ex_uk,2.5,0.4,0.42,0.05,0.04,2.4,0.05,0.41,0.38,30,HIGH,+0.03,"
        "0.02,0.01,45,\n"
    ])
    import app as _app
    monkeypatch.setattr(_app, "BETS_CSV", new)
    monkeypatch.setattr(_app, "BETS_LEGACY_CSV", legacy)

    bets = _app.load_bets()
    assert len(bets) == 2
    sources = {b["_source"] for b in bets}
    assert sources == {"legacy", "new"}
    # IDs are sequential
    assert [b["id"] for b in bets] == [0, 1]


def test_save_routes_legacy_to_legacy_file(monkeypatch, tmp_path):
    legacy = tmp_path / "bets_legacy.csv"
    new    = tmp_path / "bets.csv"
    _write(legacy, LEGACY_HEADER, [
        "2026-04-01 10:00 UTC,EPL,Arsenal,Chelsea,2026-04-05 15:00,HOME,bet365,2.1,"
        "0.04,0.48,28,MED,?,35,,30,\n"
    ])

    import app as _app
    monkeypatch.setattr(_app, "BETS_CSV", new)
    monkeypatch.setattr(_app, "BETS_LEGACY_CSV", legacy)

    bets = _app.load_bets()
    assert bets[0]["_source"] == "legacy"

    # Simulate updating result on the legacy bet
    bets[0]["result"] = "W"
    bets[0]["actual_stake"] = "30"
    bets[0]["pnl"] = "33.0"
    _app.save_bets(bets)

    # Change must be in bets_legacy.csv, not bets.csv
    assert legacy.exists()
    with open(legacy, newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["result"] == "W"
    # bets.csv must not be created (no new-source rows)
    assert not new.exists()
