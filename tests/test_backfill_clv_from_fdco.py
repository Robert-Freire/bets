"""Tests for scripts/backfill_clv_from_fdco.py."""
import csv
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import backfill_clv_from_fdco as bf
from src.betting.commissions import effective_odds
from src.betting.devig import shin


BETS_HEADER = (
    "scanned_at,sport,market,line,home,away,kickoff,side,book,odds,"
    "impl_raw,impl_effective,edge,edge_gross,effective_odds,commission_rate,"
    "consensus,pinnacle_cons,n_books,confidence,model_signal,dispersion,outlier_z,"
    "devig_method,weight_scheme,stake,result,pinnacle_close_prob,clv_pct"
)
PAPER_HEADER = (
    "scanned_at,strategy,sport,market,line,home,away,kickoff,side,book,odds,"
    "impl_raw,impl_effective,edge,edge_gross,effective_odds,commission_rate,"
    "consensus,pinnacle_cons,n_books,confidence,model_signal,dispersion,outlier_z,"
    "devig_method,weight_scheme,code_sha,strategy_config_hash,stake,"
    "pinnacle_close_prob,clv_pct"
)


def _write_fdco_csv(path: Path):
    """Tiny FDCO-shaped CSV with PSCH/PSCD/PSCA + PC>2.5/PC<2.5."""
    path.write_text(
        "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,"
        "PSCH,PSCD,PSCA,PC>2.5,PC<2.5\n"
        # Three rows: home win, draw, totals 2.5
        "E0,10/05/2026,15:00,Arsenal,Chelsea,2,1,H,2.10,3.50,3.40,1.85,2.05\n"
        "E0,10/05/2026,17:30,Liverpool,Man City,1,1,D,2.50,3.30,2.80,1.90,2.00\n"
        "E0,11/05/2026,14:00,Tottenham,West Ham,0,2,A,1.80,3.80,4.50,1.95,1.95\n"
        # Cancelled fixture: blank Pinnacle closing odds
        "E0,12/05/2026,15:00,Brighton,Newcastle,,,,,,,\n"
    )


def _bet_row(home, away, kickoff, side, book, odds, market="h2h", line=""):
    return (
        f"2026-05-01 10:00 UTC,EPL,{market},{line},{home},{away},{kickoff},"
        f"{side},{book},{odds},0.48,0.50,0.04,0.04,{odds},0.0,"
        "0.52,0.50,28,MED,+0.01,0.02,0.1,shin,uniform,5.0,,,"
    )


def _paper_row(home, away, kickoff, side, book, odds, market="h2h", line=""):
    return (
        f"2026-05-01 10:00 UTC,A_production,EPL,{market},{line},{home},{away},{kickoff},"
        f"{side},{book},{odds},0.48,0.50,0.04,0.04,{odds},0.0,"
        "0.52,0.50,28,MED,+0.01,0.02,0.1,shin,uniform,abc123,def456,5.0,,"
    )


class _FrozenDateTime(bf.datetime):
    @classmethod
    def now(cls, tz=None):
        return bf.datetime(2026, 6, 1, 12, 0, tzinfo=tz)


@pytest.fixture
def fdco_dir(tmp_path, monkeypatch):
    """Set up isolated FDCO + logs dirs for each test."""
    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True)
    _write_fdco_csv(raw / "E0_2526.csv")

    logs = tmp_path / "logs"
    logs.mkdir()
    paper = logs / "paper"
    paper.mkdir()

    monkeypatch.setattr(bf, "_RAW_DIR", raw)
    monkeypatch.setattr(bf, "_BETS_CSV", logs / "bets.csv")
    monkeypatch.setattr(bf, "_PAPER_DIR", paper)
    monkeypatch.setattr(bf, "datetime", _FrozenDateTime)
    # Bypass network refresh — the on-disk CSV was just written.
    monkeypatch.setattr(bf, "_refresh_csv", lambda league: raw / f"{league}_2526.csv"
                        if (raw / f"{league}_2526.csv").exists() else None)
    return tmp_path


def _run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["backfill_clv_from_fdco.py", *argv])
    bf.main()


def test_h2h_home_draw_away_backfill(fdco_dir, monkeypatch, capsys):
    bets = fdco_dir / "logs" / "bets.csv"
    bets.write_text(
        BETS_HEADER + "\n"
        + _bet_row("Arsenal",   "Chelsea",  "2026-05-10 15:00", "HOME", "williamhill",   2.20) + "\n"
        + _bet_row("Liverpool", "Man City", "2026-05-10 17:30", "DRAW", "skybet",        3.40) + "\n"
        + _bet_row("Tottenham", "West Ham", "2026-05-11 14:00", "AWAY", "smarkets",      4.60) + "\n"
    )

    _run_main(monkeypatch, "--leagues", "E0")

    rows = list(csv.DictReader(bets.open()))
    assert len(rows) == 3

    # Home: PSCH/PSCD/PSCA = 2.10/3.50/3.40
    fair_home = shin([1/2.10, 1/3.50, 1/3.40])
    assert float(rows[0]["pinnacle_close_prob"]) == pytest.approx(fair_home[0], abs=1e-5)
    assert float(rows[0]["clv_pct"]) == pytest.approx(2.20 * fair_home[0] - 1, abs=1e-5)

    # Draw: PSCH/PSCD/PSCA = 2.50/3.30/2.80
    fair_draw = shin([1/2.50, 1/3.30, 1/2.80])
    assert float(rows[1]["pinnacle_close_prob"]) == pytest.approx(fair_draw[1], abs=1e-5)

    # Away with smarkets commission (0.02): clv = (odds * 0.98) * pin - 1
    fair_away = shin([1/1.80, 1/3.80, 1/4.50])
    expected_clv = effective_odds(4.60, "smarkets") * fair_away[2] - 1
    assert float(rows[2]["pinnacle_close_prob"]) == pytest.approx(fair_away[2], abs=1e-5)
    assert float(rows[2]["clv_pct"]) == pytest.approx(expected_clv, abs=1e-5)

    out = capsys.readouterr().out
    assert "backfilled 3" in out


def test_totals_over_under_backfill(fdco_dir, monkeypatch):
    bets = fdco_dir / "logs" / "bets.csv"
    bets.write_text(
        BETS_HEADER + "\n"
        + _bet_row("Arsenal",   "Chelsea",  "2026-05-10 15:00", "OVER",  "williamhill", 1.95, market="totals", line="2.5") + "\n"
        + _bet_row("Liverpool", "Man City", "2026-05-10 17:30", "UNDER", "skybet",      2.05, market="totals", line="2.5") + "\n"
    )
    _run_main(monkeypatch, "--leagues", "E0")

    rows = list(csv.DictReader(bets.open()))
    fair_a = shin([1/1.85, 1/2.05])  # over, under
    assert float(rows[0]["pinnacle_close_prob"]) == pytest.approx(fair_a[0], abs=1e-5)
    fair_b = shin([1/1.90, 1/2.00])
    assert float(rows[1]["pinnacle_close_prob"]) == pytest.approx(fair_b[1], abs=1e-5)


def test_totals_non_2_5_line_skipped(fdco_dir, monkeypatch):
    bets = fdco_dir / "logs" / "bets.csv"
    bets.write_text(
        BETS_HEADER + "\n"
        + _bet_row("Arsenal", "Chelsea", "2026-05-10 15:00", "OVER", "williamhill",
                   1.95, market="totals", line="3.5") + "\n"
    )
    _run_main(monkeypatch, "--leagues", "E0")
    row = next(csv.DictReader(bets.open()))
    assert row["pinnacle_close_prob"] == ""


def test_unmapped_team_skipped_not_crashed(fdco_dir, monkeypatch):
    bets = fdco_dir / "logs" / "bets.csv"
    bets.write_text(
        BETS_HEADER + "\n"
        + _bet_row("Nonexistent FC", "Phantom United",
                   "2026-05-10 15:00", "HOME", "skybet", 2.0) + "\n"
    )
    _run_main(monkeypatch, "--leagues", "E0")  # must not raise
    row = next(csv.DictReader(bets.open()))
    assert row["pinnacle_close_prob"] == ""


def test_idempotent_rerun(fdco_dir, monkeypatch):
    bets = fdco_dir / "logs" / "bets.csv"
    bets.write_text(
        BETS_HEADER + "\n"
        + _bet_row("Arsenal", "Chelsea", "2026-05-10 15:00", "HOME", "williamhill", 2.20) + "\n"
    )
    _run_main(monkeypatch, "--leagues", "E0")
    first = bets.read_text()
    _run_main(monkeypatch, "--leagues", "E0")
    second = bets.read_text()
    assert first == second


def test_dry_run_writes_nothing(fdco_dir, monkeypatch):
    bets = fdco_dir / "logs" / "bets.csv"
    original = (
        BETS_HEADER + "\n"
        + _bet_row("Arsenal", "Chelsea", "2026-05-10 15:00", "HOME", "williamhill", 2.20) + "\n"
    )
    bets.write_text(original)
    _run_main(monkeypatch, "--dry-run", "--leagues", "E0")
    assert bets.read_text() == original


def test_paper_csv_also_backfilled(fdco_dir, monkeypatch):
    bets = fdco_dir / "logs" / "bets.csv"
    bets.write_text(BETS_HEADER + "\n")
    paper = fdco_dir / "logs" / "paper" / "A_production.csv"
    paper.write_text(
        PAPER_HEADER + "\n"
        + _paper_row("Arsenal", "Chelsea", "2026-05-10 15:00", "HOME", "williamhill", 2.20) + "\n"
    )
    _run_main(monkeypatch, "--leagues", "E0")
    row = next(csv.DictReader(paper.open()))
    assert row["pinnacle_close_prob"] != ""
    assert float(row["pinnacle_close_prob"]) > 0


def test_blank_pinnacle_columns_skipped(fdco_dir, monkeypatch):
    """Cancelled fixtures (blank PSC*) must be skipped, not crash."""
    bets = fdco_dir / "logs" / "bets.csv"
    bets.write_text(
        BETS_HEADER + "\n"
        + _bet_row("Brighton", "Newcastle", "2026-05-12 15:00", "HOME", "skybet", 2.0) + "\n"
    )
    _run_main(monkeypatch, "--leagues", "E0")
    row = next(csv.DictReader(bets.open()))
    assert row["pinnacle_close_prob"] == ""


def test_future_kickoff_not_backfilled(fdco_dir, monkeypatch):
    """Kickoffs in the future (relative to now) must not be backfilled."""
    bets = fdco_dir / "logs" / "bets.csv"
    bets.write_text(
        BETS_HEADER + "\n"
        + _bet_row("Arsenal", "Chelsea", "2099-01-01 15:00", "HOME", "skybet", 2.0) + "\n"
    )
    _run_main(monkeypatch, "--leagues", "E0")
    row = next(csv.DictReader(bets.open()))
    assert row["pinnacle_close_prob"] == ""


def test_clv_with_betfair_exchange_commission(fdco_dir, monkeypatch):
    bets = fdco_dir / "logs" / "bets.csv"
    bets.write_text(
        BETS_HEADER + "\n"
        + _bet_row("Arsenal", "Chelsea", "2026-05-10 15:00", "HOME", "betfair_ex_uk", 2.20) + "\n"
    )
    _run_main(monkeypatch, "--leagues", "E0")
    row = next(csv.DictReader(bets.open()))
    fair = shin([1/2.10, 1/3.50, 1/3.40])
    expected = effective_odds(2.20, "betfair_ex_uk") * fair[0] - 1
    assert float(row["clv_pct"]) == pytest.approx(expected, abs=1e-5)


def test_devig_math_matches_shin_directly():
    """Sanity check: the pin_prob the script computes equals shin(...)."""
    row = {"PSCH": "2.10", "PSCD": "3.50", "PSCA": "3.40"}
    fair = shin([1/2.10, 1/3.50, 1/3.40])
    assert bf._h2h_pin_prob(row, "HOME") == pytest.approx(fair[0])
    assert bf._h2h_pin_prob(row, "DRAW") == pytest.approx(fair[1])
    assert bf._h2h_pin_prob(row, "AWAY") == pytest.approx(fair[2])
