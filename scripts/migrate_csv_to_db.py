"""One-shot, idempotent CSV → DB importer for Phase A.3.

Scope: WSL-side CSVs only. Reads logs/bets.csv, logs/closing_lines.csv,
logs/drift.csv, and logs/paper/*.csv into the schema produced by
src/storage/migrate.py. Pi CSVs are NOT in scope (Phase A.10).

Idempotent by construction: every row gets a deterministic UUID5 derived
from its natural key, then INSERTed via `INSERT ... SELECT ... WHERE NOT
EXISTS (...)`. Re-running the importer produces zero new rows. The same
SQL pattern works on SQL Server (via pyodbc) and SQLite (sqlite3).

Usage:
    python3 scripts/migrate_csv_to_db.py --dsn "$AZURE_SQL_DSN"
    python3 scripts/migrate_csv_to_db.py --sqlite path/to/db.sqlite
    python3 scripts/migrate_csv_to_db.py --sqlite :memory:  # for tests

Optional:
    --logs-dir <path>   override the default <repo>/logs directory
"""
from __future__ import annotations

import argparse
import csv
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_LOGS_DIR = ROOT / "logs"

# Stable namespace for deterministic UUID5 generation. Changing this string
# breaks idempotency across reruns — do not edit.
_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "kaunitz.bets:v1")

# Imported from _keys.py — the canonical label→sport_key map shared across
# the storage layer. Add new leagues there, not here.
from src.storage._keys import LABEL_TO_KEY as _LABEL_TO_KEY  # noqa: E402


# ---- helpers ---------------------------------------------------------------

def _u5(parts: tuple) -> str:
    return str(uuid.uuid5(_NAMESPACE, "|".join(str(p) for p in parts)))


def _parse_dt(s: str | None) -> datetime | None:
    """Parse a CSV datetime field. Tolerates the formats this codebase emits.

    Examples seen: "2026-04-29 13:12 UTC", "2026-05-11 19:00",
    ISO 8601 with offset.
    """
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    s = s.replace(" UTC", "").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _f(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _i(s: str | None) -> int | None:
    f = _f(s)
    return int(f) if f is not None else None


def _line(s: str | None) -> float | None:
    """`line` is empty for h2h markets in CSVs but NOT NULL with default 0
    in closing_lines/drift PKs. Bets/paper_bets allow NULL — keep it NULL."""
    return _f(s)


def _scan_date(scanned_at: str) -> str:
    return (scanned_at or "")[:10]


# ---- DB adapter ------------------------------------------------------------

@dataclass
class Stats:
    inserted: int = 0
    skipped: int = 0


@dataclass
class ImportSummary:
    fixtures: Stats = field(default_factory=Stats)
    books: Stats = field(default_factory=Stats)
    strategies: Stats = field(default_factory=Stats)
    bets: Stats = field(default_factory=Stats)
    paper_bets: Stats = field(default_factory=Stats)
    closing_lines: Stats = field(default_factory=Stats)
    drift: Stats = field(default_factory=Stats)


class Importer:
    """Streams CSV rows into the DB. Holds a single connection + cursor.

    Caches `book name → id` and `strategy name → id` lookups to avoid a
    SELECT per inserted row.
    """

    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()
        self._books: dict[str, int] = {}
        self._strategies: dict[str, int] = {}
        self.summary = ImportSummary()

    # books / strategies are upserted lazily and cached.
    def book_id(self, name: str) -> int:
        if name in self._books:
            return self._books[name]
        # The portable INSERT-IF-NOT-EXISTS works for both SQLite and MSSQL.
        self.cur.execute(
            "INSERT INTO books (name) "
            "SELECT ? WHERE NOT EXISTS (SELECT 1 FROM books WHERE name = ?)",
            (name, name),
        )
        if self.cur.rowcount and self.cur.rowcount > 0:
            self.summary.books.inserted += 1
        else:
            self.summary.books.skipped += 1
        self.cur.execute("SELECT id FROM books WHERE name = ?", (name,))
        bid = self.cur.fetchone()[0]
        self._books[name] = bid
        return bid

    def strategy_id(self, name: str) -> int:
        if name in self._strategies:
            return self._strategies[name]
        self.cur.execute(
            "INSERT INTO strategies (name) "
            "SELECT ? WHERE NOT EXISTS (SELECT 1 FROM strategies WHERE name = ?)",
            (name, name),
        )
        if self.cur.rowcount and self.cur.rowcount > 0:
            self.summary.strategies.inserted += 1
        else:
            self.summary.strategies.skipped += 1
        self.cur.execute("SELECT id FROM strategies WHERE name = ?", (name,))
        sid = self.cur.fetchone()[0]
        self._strategies[name] = sid
        return sid

    def upsert_fixture(self, kickoff: str, home: str, away: str,
                       sport_label: str) -> str:
        fid = _u5(("fixture", kickoff, home, away))
        sport_key = _LABEL_TO_KEY.get(sport_label, sport_label)
        self.cur.execute(
            "INSERT INTO fixtures (id, sport_key, league, home, away, kickoff_utc) "
            "SELECT ?, ?, ?, ?, ?, ? "
            "WHERE NOT EXISTS (SELECT 1 FROM fixtures WHERE id = ?)",
            (fid, sport_key, sport_label, home, away,
             _parse_dt(kickoff), fid),
        )
        if self.cur.rowcount and self.cur.rowcount > 0:
            self.summary.fixtures.inserted += 1
        else:
            self.summary.fixtures.skipped += 1
        return fid

    # row-level inserters --------------------------------------------------

    _BET_COLS = (
        "id", "fixture_id", "book_id", "scanned_at", "market", "line", "side",
        "odds", "impl_raw", "impl_effective", "edge", "edge_gross",
        "effective_odds", "commission_rate", "consensus", "pinnacle_cons",
        "n_books", "confidence", "model_signal", "dispersion", "outlier_z",
        "devig_method", "weight_scheme", "stake", "result",
        "actual_stake", "settled_at", "pnl", "pinnacle_close_prob", "clv_pct",
    )

    def _row_to_bet_values(self, row: dict, fixture_id: str, book_id: int,
                            bet_id: str) -> tuple:
        return (
            bet_id,
            fixture_id,
            book_id,
            _parse_dt(row.get("scanned_at")),
            row.get("market") or "h2h",
            _line(row.get("line")),
            row.get("side"),
            _f(row.get("odds")),
            _f(row.get("impl_raw")),
            _f(row.get("impl_effective")),
            _f(row.get("edge")),
            _f(row.get("edge_gross")),
            _f(row.get("effective_odds")),
            _f(row.get("commission_rate")),
            _f(row.get("consensus")),
            _f(row.get("pinnacle_cons")),
            _i(row.get("n_books")),
            row.get("confidence") or None,
            row.get("model_signal") or None,
            _f(row.get("dispersion")),
            _f(row.get("outlier_z")),
            row.get("devig_method") or None,
            row.get("weight_scheme") or None,
            _f(row.get("stake")),
            row.get("result") or "pending",
            _f(row.get("actual_stake")),
            _parse_dt(row.get("settled_at")),
            _f(row.get("pnl")),
            _f(row.get("pinnacle_close_prob")),
            _f(row.get("clv_pct")),
        )

    def insert_bet(self, row: dict) -> None:
        kickoff = row.get("kickoff", "")
        home = row.get("home", "")
        away = row.get("away", "")
        side = row.get("side", "")
        book = row.get("book", "")
        market = row.get("market") or "h2h"
        line_val = (row.get("line") or "").strip()
        sd = _scan_date(row.get("scanned_at", ""))
        bet_id = _u5(("bet", sd, kickoff, home, away, market, line_val, side, book))

        fid = self.upsert_fixture(kickoff, home, away, row.get("sport", ""))
        bid = self.book_id(book)
        values = self._row_to_bet_values(row, fid, bid, bet_id)

        cols = ", ".join(self._BET_COLS)
        placeholders = ", ".join(["?"] * len(self._BET_COLS))
        self.cur.execute(
            f"INSERT INTO bets ({cols}) "
            f"SELECT {placeholders} "
            f"WHERE NOT EXISTS (SELECT 1 FROM bets WHERE id = ?)",
            (*values, bet_id),
        )
        if self.cur.rowcount and self.cur.rowcount > 0:
            self.summary.bets.inserted += 1
        else:
            self.summary.bets.skipped += 1

    _PAPER_COLS = (
        "id", "strategy_id", "fixture_id", "book_id", "scanned_at", "market",
        "line", "side", "odds", "impl_raw", "impl_effective", "edge",
        "edge_gross", "effective_odds", "commission_rate", "consensus",
        "pinnacle_cons", "n_books", "confidence", "model_signal", "dispersion",
        "outlier_z", "devig_method", "weight_scheme", "stake", "result",
        "actual_stake", "settled_at", "pnl", "pinnacle_close_prob", "clv_pct",
    )

    def insert_paper_bet(self, row: dict, strategy_name: str) -> None:
        kickoff = row.get("kickoff", "")
        home = row.get("home", "")
        away = row.get("away", "")
        side = row.get("side", "")
        book = row.get("book", "")
        market = row.get("market") or "h2h"
        line_val = (row.get("line") or "").strip()
        sd = _scan_date(row.get("scanned_at", ""))
        pid = _u5(("paper", strategy_name, sd, kickoff, home, away, market,
                   line_val, side, book))

        fid = self.upsert_fixture(kickoff, home, away, row.get("sport", ""))
        bid = self.book_id(book)
        sid = self.strategy_id(strategy_name)

        # Reuse the bet row builder, then splice in strategy_id at index 1.
        bet_values = self._row_to_bet_values(row, fid, bid, pid)
        values = (bet_values[0], sid) + bet_values[1:]

        cols = ", ".join(self._PAPER_COLS)
        placeholders = ", ".join(["?"] * len(self._PAPER_COLS))
        self.cur.execute(
            f"INSERT INTO paper_bets ({cols}) "
            f"SELECT {placeholders} "
            f"WHERE NOT EXISTS (SELECT 1 FROM paper_bets WHERE id = ?)",
            (*values, pid),
        )
        if self.cur.rowcount and self.cur.rowcount > 0:
            self.summary.paper_bets.inserted += 1
        else:
            self.summary.paper_bets.skipped += 1

    def insert_closing_line(self, row: dict) -> None:
        kickoff = row.get("kickoff", "")
        home = row.get("home", "")
        away = row.get("away", "")
        market = row.get("market") or "h2h"
        line_val = _line(row.get("line"))
        # The schema PK requires NOT NULL `line`; sentinel 0 for h2h-style rows.
        line_pk = 0.0 if line_val is None else line_val
        side = row.get("side", "")
        book = row.get("book", "")

        fid = self.upsert_fixture(kickoff, home, away, row.get("sport", ""))
        bid = self.book_id(book)

        self.cur.execute(
            "INSERT INTO closing_lines ("
            "  fixture_id, side, market, line, book_id, captured_at, "
            "  pinnacle_close_prob, pinnacle_raw_odds, your_book_flagged_odds, "
            "  your_book_close_odds, clv_pct"
            ") SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ? "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM closing_lines "
            "  WHERE fixture_id = ? AND side = ? AND market = ? "
            "    AND line = ? AND book_id = ?)",
            (
                fid, side, market, line_pk, bid,
                _parse_dt(row.get("captured_at") or row.get("scanned_at")),
                _f(row.get("pinnacle_close_prob")),
                _f(row.get("pinnacle_raw_odds")),
                _f(row.get("your_book_flagged_odds") or row.get("flagged_odds")),
                _f(row.get("your_book_close_odds") or row.get("close_odds")),
                _f(row.get("clv_pct")),
                fid, side, market, line_pk, bid,
            ),
        )
        if self.cur.rowcount and self.cur.rowcount > 0:
            self.summary.closing_lines.inserted += 1
        else:
            self.summary.closing_lines.skipped += 1

    def insert_drift(self, row: dict) -> None:
        kickoff = row.get("kickoff", "")
        home = row.get("home", "")
        away = row.get("away", "")
        market = row.get("market") or "h2h"
        line_val = _line(row.get("line"))
        line_pk = 0.0 if line_val is None else line_val
        side = row.get("side", "")
        book = row.get("book", "")
        t_minus = _i(row.get("t_minus_min"))
        if t_minus is None:
            self.summary.drift.skipped += 1
            return

        fid = self.upsert_fixture(kickoff, home, away, row.get("sport", ""))
        bid = self.book_id(book)

        self.cur.execute(
            "INSERT INTO drift ("
            "  fixture_id, side, market, line, book_id, t_minus_min, "
            "  captured_at, your_book_odds, pinnacle_odds, n_books"
            ") SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ? "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM drift "
            "  WHERE fixture_id = ? AND side = ? AND market = ? "
            "    AND line = ? AND book_id = ? AND t_minus_min = ?)",
            (
                fid, side, market, line_pk, bid, t_minus,
                _parse_dt(row.get("captured_at") or row.get("scanned_at")),
                _f(row.get("your_book_odds")),
                _f(row.get("pinnacle_odds")),
                _i(row.get("n_books")),
                fid, side, market, line_pk, bid, t_minus,
            ),
        )
        if self.cur.rowcount and self.cur.rowcount > 0:
            self.summary.drift.inserted += 1
        else:
            self.summary.drift.skipped += 1


# ---- file-level orchestration ---------------------------------------------

def _iter_csv(path: Path) -> Iterator[dict]:
    if not path.exists():
        return iter(())
    return _open_and_iter(path)


def _open_and_iter(path: Path) -> Iterator[dict]:
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            yield row


def import_all(imp: Importer, logs_dir: Path) -> ImportSummary:
    bets_csv = logs_dir / "bets.csv"
    bets_legacy_csv = logs_dir / "bets_legacy.csv"
    closing_csv = logs_dir / "closing_lines.csv"
    drift_csv = logs_dir / "drift.csv"
    paper_dir = logs_dir / "paper"

    for row in _iter_csv(bets_legacy_csv):
        imp.insert_bet(row)

    for row in _iter_csv(bets_csv):
        imp.insert_bet(row)

    for row in _iter_csv(closing_csv):
        imp.insert_closing_line(row)

    for row in _iter_csv(drift_csv):
        imp.insert_drift(row)

    if paper_dir.exists():
        for variant_csv in sorted(paper_dir.glob("*.csv")):
            strategy_name = variant_csv.stem
            for row in _iter_csv(variant_csv):
                # Prefer the strategy column in-row over the filename so that
                # cross-pollinated rows (if any) land under the correct
                # strategy. Fall back to the filename stem.
                strat = (row.get("strategy") or "").strip() or strategy_name
                imp.insert_paper_bet(row, strat)

    imp.conn.commit()
    return imp.summary


# ---- CLI -------------------------------------------------------------------

def _print_summary(s: ImportSummary) -> None:
    rows = [
        ("fixtures",      s.fixtures),
        ("books",         s.books),
        ("strategies",    s.strategies),
        ("bets",          s.bets),
        ("paper_bets",    s.paper_bets),
        ("closing_lines", s.closing_lines),
        ("drift",         s.drift),
    ]
    width = max(len(name) for name, _ in rows)
    print("[migrate-csv] per-table results:")
    for name, st in rows:
        print(f"  {name:<{width}}  imported={st.inserted:>6}  skipped={st.skipped:>6}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dsn", help="pyodbc DSN for Azure SQL / MSSQL")
    g.add_argument("--sqlite", help="SQLite path (or :memory:)")
    p.add_argument("--logs-dir", default=str(DEFAULT_LOGS_DIR),
                   help=f"override the CSV source directory (default: {DEFAULT_LOGS_DIR})")
    args = p.parse_args(argv)

    if args.dsn:
        import pyodbc
        conn = pyodbc.connect(args.dsn)
    else:
        import sqlite3
        conn = sqlite3.connect(args.sqlite)
        conn.execute("PRAGMA foreign_keys = ON")

    imp = Importer(conn)
    summary = import_all(imp, Path(args.logs_dir))
    _print_summary(summary)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
