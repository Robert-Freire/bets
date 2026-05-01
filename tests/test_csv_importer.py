"""Tests for scripts/migrate_csv_to_db.py (Phase A.3).

Strategy:
- Stand up a fresh in-memory SQLite DB with the sibling schema.
- Stage minimal CSVs in a tmp logs/ directory mirroring the layout the
  importer expects.
- Run the importer once → assert per-table row counts.
- Run it again → assert zero new rows (idempotency).
- Spot-check that re-running yields the SAME UUIDs (deterministic IDs).
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQLITE = ROOT / "src" / "storage" / "schema_sqlite.sql"

# Add scripts/ to path so we can import the importer module.
sys.path.insert(0, str(ROOT / "scripts"))


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()
    sql = SCHEMA_SQLITE.read_text()
    # The schema file contains multiple statements; sqlite3.executescript
    # handles them all in one go.
    cur.executescript(sql)
    conn.commit()
    return conn


# Two scan dates × two markets × two books = enough to cover the natural-key
# uniqueness logic without burying test intent.
BETS_HEADER = (
    "scanned_at,sport,market,line,home,away,kickoff,side,book,odds,impl_raw,"
    "impl_effective,edge,edge_gross,effective_odds,commission_rate,consensus,"
    "pinnacle_cons,n_books,confidence,model_signal,dispersion,outlier_z,"
    "devig_method,weight_scheme,stake,result"
)

BETS_ROWS = [
    # Same fixture, same market+side, two books — two distinct bets.
    "2026-04-29 13:12 UTC,EPL,h2h,,Arsenal,Chelsea,2026-05-10 15:00,HOME,bet365,2.10,0.476,0.480,0.030,0.040,2.075,0.02,0.510,0.500,30,HIGH,+0.05,0.01,0.0,shin,uniform,15.0,",
    "2026-04-29 13:12 UTC,EPL,h2h,,Arsenal,Chelsea,2026-05-10 15:00,HOME,williamhill,2.05,0.488,0.490,0.020,0.025,2.045,0.02,0.510,0.500,30,HIGH,+0.04,0.01,0.0,shin,uniform,10.0,",
    # Same bet logically, but a later scan date — should produce a NEW row,
    # because the scanner re-flags daily.
    "2026-04-30 09:00 UTC,EPL,h2h,,Arsenal,Chelsea,2026-05-10 15:00,HOME,bet365,2.12,0.472,0.476,0.034,0.044,2.099,0.02,0.510,0.500,30,HIGH,+0.06,0.01,0.0,shin,uniform,15.0,",
    # Different fixture entirely.
    "2026-04-29 13:12 UTC,Bundesliga,h2h,,Bayern Munich,Wolfsburg,2026-05-09 16:30,AWAY,smarkets,5.40,0.185,0.187,0.010,0.018,5.355,0.02,0.195,0.190,28,MED,?,0.03,0.0,shin,uniform,5.0,",
    # Totals market on the EPL fixture — same fixture row in fixtures, new bet.
    "2026-04-29 13:12 UTC,EPL,totals,2.5,Arsenal,Chelsea,2026-05-10 15:00,Over,bet365,1.95,0.513,0.515,0.025,0.030,1.945,0.02,0.540,0.530,30,HIGH,?,0.02,0.0,shin,uniform,8.0,",
]

PAPER_HEADER = (
    "scanned_at,strategy,sport,market,line,home,away,kickoff,side,book,odds,"
    "impl_raw,impl_effective,edge,edge_gross,effective_odds,commission_rate,"
    "consensus,pinnacle_cons,n_books,confidence,model_signal,dispersion,"
    "outlier_z,devig_method,weight_scheme,stake,pinnacle_close_prob,clv_pct"
)

A_PROD_ROWS = [
    "2026-04-29 13:12 UTC,A_production,EPL,h2h,,Arsenal,Chelsea,2026-05-10 15:00,HOME,bet365,2.10,0.476,0.480,0.030,0.040,2.075,0.02,0.510,0.500,30,HIGH,+0.05,0.01,0.0,shin,uniform,15.0,,",
    "2026-04-29 13:12 UTC,A_production,Bundesliga,h2h,,Bayern Munich,Wolfsburg,2026-05-09 16:30,AWAY,smarkets,5.40,0.185,0.187,0.010,0.018,5.355,0.02,0.195,0.190,28,MED,?,0.03,0.0,shin,uniform,5.0,,",
]

B_STRICT_ROWS = [
    "2026-04-29 13:12 UTC,B_strict,EPL,h2h,,Arsenal,Chelsea,2026-05-10 15:00,HOME,bet365,2.10,0.476,0.480,0.030,0.040,2.075,0.02,0.510,0.500,30,HIGH,+0.05,0.01,0.0,shin,uniform,15.0,,",
]


@pytest.fixture
def staged_logs(tmp_path: Path) -> Path:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "bets.csv").write_text(BETS_HEADER + "\n" + "\n".join(BETS_ROWS) + "\n")
    paper = logs / "paper"
    paper.mkdir()
    (paper / "A_production.csv").write_text(
        PAPER_HEADER + "\n" + "\n".join(A_PROD_ROWS) + "\n"
    )
    (paper / "B_strict.csv").write_text(
        PAPER_HEADER + "\n" + "\n".join(B_STRICT_ROWS) + "\n"
    )
    return logs


def _run_import(conn, logs_dir: Path):
    from migrate_csv_to_db import Importer, import_all
    imp = Importer(conn)
    return import_all(imp, logs_dir)


def test_first_run_imports_expected_counts(staged_logs):
    conn = _make_db()
    s = _run_import(conn, staged_logs)

    assert s.bets.inserted == 5
    assert s.paper_bets.inserted == 3  # 2 A + 1 B
    # Two distinct fixtures (Arsenal/Chelsea, Bayern/Wolfsburg). Both bets and
    # paper_bets share fixtures, so insert is once per fixture; subsequent
    # references are skipped.
    assert s.fixtures.inserted == 2
    # Three books (bet365, williamhill, smarkets) referenced in CSVs.
    assert s.books.inserted == 3
    assert s.strategies.inserted == 2  # A_production, B_strict


def test_second_run_is_a_no_op(staged_logs):
    conn = _make_db()
    _run_import(conn, staged_logs)
    s = _run_import(conn, staged_logs)
    assert s.bets.inserted == 0
    assert s.paper_bets.inserted == 0
    assert s.fixtures.inserted == 0
    assert s.books.inserted == 0
    assert s.strategies.inserted == 0


def test_uuids_are_deterministic_across_runs(staged_logs, tmp_path):
    conn1 = _make_db()
    _run_import(conn1, staged_logs)
    ids1 = sorted(r[0] for r in conn1.execute("SELECT id FROM bets").fetchall())
    pids1 = sorted(r[0] for r in conn1.execute("SELECT id FROM paper_bets").fetchall())

    conn2 = _make_db()  # fresh DB
    _run_import(conn2, staged_logs)
    ids2 = sorted(r[0] for r in conn2.execute("SELECT id FROM bets").fetchall())
    pids2 = sorted(r[0] for r in conn2.execute("SELECT id FROM paper_bets").fetchall())

    assert ids1 == ids2, "bet UUIDs differ across fresh runs (non-deterministic)"
    assert pids1 == pids2, "paper_bet UUIDs differ across fresh runs"


def test_fixtures_inferred_from_unique_kickoff_home_away(staged_logs):
    conn = _make_db()
    _run_import(conn, staged_logs)
    fixtures = conn.execute(
        "SELECT home, away, sport_key FROM fixtures ORDER BY home"
    ).fetchall()
    assert ("Arsenal", "Chelsea", "soccer_epl") in fixtures
    assert ("Bayern Munich", "Wolfsburg", "soccer_germany_bundesliga") in fixtures


def test_per_scan_date_yields_distinct_bets(staged_logs):
    """Same fixture+market+side+book on different scan_dates → 2 rows."""
    conn = _make_db()
    _run_import(conn, staged_logs)
    rows = conn.execute(
        "SELECT scanned_at FROM bets b "
        "JOIN fixtures f ON f.id = b.fixture_id "
        "JOIN books bk ON bk.id = b.book_id "
        "WHERE f.home = 'Arsenal' AND b.side = 'HOME' AND b.market = 'h2h' "
        "  AND bk.name = 'bet365' "
        "ORDER BY scanned_at"
    ).fetchall()
    assert len(rows) == 2  # two distinct scan dates produced two distinct bet rows


def test_paper_bets_link_to_correct_strategy(staged_logs):
    conn = _make_db()
    _run_import(conn, staged_logs)
    rows = conn.execute(
        "SELECT s.name, COUNT(*) FROM paper_bets pb "
        "JOIN strategies s ON s.id = pb.strategy_id "
        "GROUP BY s.name ORDER BY s.name"
    ).fetchall()
    assert rows == [("A_production", 2), ("B_strict", 1)]


def test_missing_optional_files_do_not_error(tmp_path):
    """No closing_lines.csv, no drift.csv → importer must skip silently."""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "bets.csv").write_text(BETS_HEADER + "\n")  # header only, no rows
    conn = _make_db()
    s = _run_import(conn, logs)
    assert s.bets.inserted == 0
    assert s.closing_lines.inserted == 0
    assert s.drift.inserted == 0


def test_null_handling_for_late_added_columns(staged_logs):
    """bets.csv has no settled_at/pnl/pinnacle_close_prob/clv_pct — those
    columns must land NULL in the DB rather than '' or 0."""
    conn = _make_db()
    _run_import(conn, staged_logs)
    nulls = conn.execute(
        "SELECT COUNT(*) FROM bets WHERE pinnacle_close_prob IS NULL"
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM bets").fetchone()[0]
    assert nulls == total, "expected NULL, got non-NULL coercion"


def test_cli_subprocess_round_trip(staged_logs, tmp_path):
    """End-to-end: invoke the script via python -m and a SQLite path."""
    db = tmp_path / "import.sqlite"
    # Apply schema.
    subprocess.run(
        [sys.executable, "-m", "src.storage.migrate", "--sqlite", str(db)],
        cwd=ROOT, check=True, capture_output=True,
    )
    # Run importer.
    out = subprocess.run(
        [sys.executable, "scripts/migrate_csv_to_db.py",
         "--sqlite", str(db),
         "--logs-dir", str(staged_logs)],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    assert "imported=" in out.stdout
    # Re-run → all imported counts go to 0.
    out2 = subprocess.run(
        [sys.executable, "scripts/migrate_csv_to_db.py",
         "--sqlite", str(db),
         "--logs-dir", str(staged_logs)],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    # The "bets" line on the second run should report imported=0.
    bets_line = [ln for ln in out2.stdout.splitlines()
                 if ln.strip().startswith("bets ")]
    assert bets_line and "imported=     0" in bets_line[0], out2.stdout
