"""Storage repo: dual-writer for bets / paper_bets / closing_lines / drift.

CSV writers are always-on. DB writers are gated by env so that the same
codebase runs unchanged on the Pi (CSV-only) and on WSL (CSV + DB).

Activation rule (BOTH must hold for DB writes):
  - BETS_DB_WRITE=1
  - AZURE_SQL_DSN  set to a complete pyodbc DSN string
                   (or AZURE_SQL_SERVER + AZURE_SQL_USER + AZURE_SQL_DATABASE
                    + AZURE_SQL_KV_VAULT + AZURE_SQL_KV_SECRET, in which case
                    the password is fetched from Azure Key Vault once and the
                    DSN is built at boot time).

Pi safety contract:
  - Module imports nothing beyond stdlib at top level. pyodbc and the
    Azure CLI are touched only inside `_connect()` which is only called
    when the env flags are set. After `git pull` on the Pi (no env flags),
    the DB code path stays dormant; behavior is byte-identical to the
    pre-A.4 inline CSV writers.

Failure isolation:
  - DB inserts run inside try/except. If the DB is unreachable mid-scan,
    the CSV append still happens and the error is logged; the scan does
    not abort. A repeated failure surfaces via stderr but doesn't crash.

UUID determinism:
  - Bet/fixture IDs come from `src.storage._keys`, the same module the
    A.3 importer uses. So a row written live and the same row imported
    from CSV later produce the same UUID — no duplicates.
"""
from __future__ import annotations

import csv
import fcntl
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable

from src.storage._keys import (
    LABEL_TO_KEY,
    PINNACLE_BOOK,
    bet_uuid,
    fixture_uuid,
    normalise_line,
    paper_bet_uuid,
    scan_date_of,
)

# ---- field schemas (mirror the existing inline writers verbatim) ----------

BETS_FIELDS = [
    "scanned_at", "sport", "market", "line", "home", "away", "kickoff",
    "side", "book", "odds", "impl_raw", "impl_effective",
    "edge", "edge_gross", "effective_odds", "commission_rate",
    "consensus", "pinnacle_cons",
    "n_books", "confidence", "model_signal", "dispersion", "outlier_z",
    "devig_method", "weight_scheme",
    "stake", "result",
]

PAPER_FIELDS = [
    "scanned_at", "strategy", "sport", "market", "line", "home", "away",
    "kickoff", "side", "book", "odds", "impl_raw", "impl_effective",
    "edge", "edge_gross", "effective_odds", "commission_rate",
    "consensus", "pinnacle_cons",
    "n_books", "confidence", "model_signal", "dispersion", "outlier_z",
    "devig_method", "weight_scheme", "code_sha", "strategy_config_hash",
    "stake", "pinnacle_close_prob", "clv_pct",
]

CLOSING_FIELDS = [
    "captured_at", "home", "away", "kickoff", "side", "market", "line",
    "pinnacle_devig_prob", "pinnacle_raw_odds",
    "your_book_flagged_odds", "your_book_close_odds", "clv_pct",
]

DRIFT_FIELDS = [
    "captured_at", "home", "away", "kickoff", "side", "market", "line",
    "book", "t_minus_min", "your_book_odds", "pinnacle_odds", "n_books",
]


# ---- env / DSN resolution -------------------------------------------------

def _kv_fetch(vault: str, secret: str) -> str | None:
    """Run `az keyvault secret show` once and return the secret value.

    Caches into the calling repo's instance, not module-global, so test
    isolation is straightforward. Returns None on failure (and logs)."""
    try:
        out = subprocess.run(
            ["az", "keyvault", "secret", "show",
             "--vault-name", vault, "--name", secret,
             "--query", "value", "-o", "tsv"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        print(f"[repo] WARN: Key Vault fetch failed ({vault}/{secret}): {e}",
              file=sys.stderr)
        return None


def _resolve_dsn() -> str | None:
    """Build a pyodbc DSN from env, or None if dual-write is disabled.

    Resolution order:
      1. BETS_DB_WRITE != "1"  →  None (DB disabled)
      2. AZURE_SQL_DSN literal →  use as-is
      3. AZURE_SQL_SERVER+USER+DATABASE+KV_VAULT+KV_SECRET →  fetch pwd, build
      4. otherwise              →  None (and warn once)
    """
    if os.environ.get("BETS_DB_WRITE", "").strip() != "1":
        return None
    dsn = os.environ.get("AZURE_SQL_DSN", "").strip()
    if dsn:
        return dsn
    server = os.environ.get("AZURE_SQL_SERVER", "").strip()
    user = os.environ.get("AZURE_SQL_USER", "").strip()
    database = os.environ.get("AZURE_SQL_DATABASE", "").strip()
    vault = os.environ.get("AZURE_SQL_KV_VAULT", "").strip()
    secret = os.environ.get("AZURE_SQL_KV_SECRET", "").strip()
    if not (server and user and database and vault and secret):
        print("[repo] WARN: BETS_DB_WRITE=1 but DSN inputs incomplete; "
              "DB writes disabled. Set AZURE_SQL_DSN or the SERVER/USER/"
              "DATABASE/KV_VAULT/KV_SECRET quintet.", file=sys.stderr)
        return None
    pwd = _kv_fetch(vault, secret)
    if not pwd:
        return None
    return (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server=tcp:{server},1433;Database={database};Uid={user};Pwd={pwd};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;"
    )


# ---- CSV helpers ----------------------------------------------------------

def _atomic_append_csv(path: Path, fields: list[str], rows: Iterable[dict]) -> None:
    """Append rows to `path`; write header if file is new. fcntl-locked."""
    rows = list(rows)
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_hdr = not path.exists()
    with open(path, "a", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if write_hdr:
            w.writeheader()
        w.writerows(rows)


# ---- BetRepo --------------------------------------------------------------

def _f(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _i(s):
    f = _f(s)
    return int(f) if f is not None else None


def _parse_dt(s):
    if s is None:
        return None
    if isinstance(s, datetime):
        return s
    s = str(s).strip()
    if not s:
        return None
    s = s.replace(" UTC", "").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


class BetRepo:
    """Dual-write repo: CSV always, DB if env allows.

    A BetRepo holds the DB connection (when enabled) for its lifetime,
    so a single scan run reuses one pyodbc connection across all rows.
    Construct one per scan/closing-line process; call `close()` at the end.
    """

    def __init__(self, logs_dir: Path | None = None,
                 dsn: str | None = "__resolve__"):
        self.logs_dir = Path(logs_dir) if logs_dir else (
            Path(__file__).resolve().parents[2] / "logs"
        )
        self.bets_csv = self.logs_dir / "bets.csv"
        self.paper_dir = self.logs_dir / "paper"
        self.closing_csv = self.logs_dir / "closing_lines.csv"
        self.drift_csv = self.logs_dir / "drift.csv"

        # Pass dsn=None explicitly to force CSV-only (used by tests).
        # Sentinel "__resolve__" means: read env at construction time.
        if dsn == "__resolve__":
            dsn = _resolve_dsn()
        self._dsn = dsn
        self._conn = None
        self._cur = None
        self._db_failed = False  # latched after first DB error this run
        self._book_ids: dict[str, int] = {}

    # --- DB lifecycle ------------------------------------------------------

    @property
    def db_enabled(self) -> bool:
        return self._dsn is not None and not self._db_failed

    def _connect(self):
        if self._conn is not None:
            return self._conn
        if self._dsn is None:
            return None
        try:
            import pyodbc  # lazy import (Pi safety)
            self._conn = pyodbc.connect(self._dsn)
            self._cur = self._conn.cursor()
            return self._conn
        except Exception as e:
            print(f"[repo] WARN: DB connect failed; falling back to CSV-only: {e}",
                  file=sys.stderr)
            self._db_failed = True
            return None

    def close(self):
        if self._conn is not None:
            try:
                self._conn.commit()
                self._conn.close()
            except Exception:
                pass
            self._conn = None
            self._cur = None

    @contextmanager
    def _db_section(self):
        """Wrap a block of DB writes; on any exception, latch _db_failed."""
        if self._connect() is None:
            yield None
            return
        try:
            yield self._cur
            self._conn.commit()
        except Exception as e:
            print(f"[repo] WARN: DB write failed; CSV is canonical for this "
                  f"scan: {e}", file=sys.stderr)
            try:
                self._conn.rollback()
            except Exception:
                pass
            self._db_failed = True

    # --- shared DB helpers -------------------------------------------------

    def _book_id(self, name: str) -> int:
        if name in self._book_ids:
            return self._book_ids[name]
        self._cur.execute(
            "INSERT INTO books (name) "
            "SELECT ? WHERE NOT EXISTS (SELECT 1 FROM books WHERE name = ?)",
            (name, name),
        )
        self._cur.execute("SELECT id FROM books WHERE name = ?", (name,))
        bid = self._cur.fetchone()[0]
        self._book_ids[name] = bid
        return bid

    def _strategy_id(self, name: str) -> int:
        self._cur.execute(
            "INSERT INTO strategies (name) "
            "SELECT ? WHERE NOT EXISTS (SELECT 1 FROM strategies WHERE name = ?)",
            (name, name),
        )
        self._cur.execute("SELECT id FROM strategies WHERE name = ?", (name,))
        return self._cur.fetchone()[0]

    def _ensure_fixture(self, kickoff: str, home: str, away: str,
                        sport_label: str) -> str:
        fid = fixture_uuid(kickoff, home, away)
        sport_key = LABEL_TO_KEY.get(sport_label, sport_label or "unknown")
        self._cur.execute(
            "INSERT INTO fixtures (id, sport_key, league, home, away, kickoff_utc) "
            "SELECT ?, ?, ?, ?, ?, ? "
            "WHERE NOT EXISTS (SELECT 1 FROM fixtures WHERE id = ?)",
            (fid, sport_key, sport_label or None, home, away,
             _parse_dt(kickoff), fid),
        )
        return fid

    # --- bets --------------------------------------------------------------

    _BET_COLS = (
        "id", "fixture_id", "book_id", "scanned_at", "market", "line", "side",
        "odds", "impl_raw", "impl_effective", "edge", "edge_gross",
        "effective_odds", "commission_rate", "consensus", "pinnacle_cons",
        "n_books", "confidence", "model_signal", "dispersion", "outlier_z",
        "devig_method", "weight_scheme", "stake", "result",
    )

    def _bet_values(self, row: dict, fixture_id: str, book_id: int,
                    bet_id: str) -> tuple:
        return (
            bet_id,
            fixture_id,
            book_id,
            _parse_dt(row.get("scanned_at")),
            row.get("market") or "h2h",
            _f(row.get("line")),
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
        )

    def add_bets(self, rows: list[dict]) -> None:
        rows = list(rows)
        _atomic_append_csv(self.bets_csv, BETS_FIELDS, rows)
        if not rows or not self.db_enabled:
            return
        with self._db_section() as cur:
            if cur is None:
                return
            cols = ", ".join(self._BET_COLS)
            placeholders = ", ".join(["?"] * len(self._BET_COLS))
            insert_sql = (
                f"INSERT INTO bets ({cols}) "
                f"SELECT {placeholders} "
                f"WHERE NOT EXISTS (SELECT 1 FROM bets WHERE id = ?)"
            )
            for row in rows:
                kickoff = row.get("kickoff", "")
                home = row.get("home", "")
                away = row.get("away", "")
                side = row.get("side", "")
                book = row.get("book", "")
                market = row.get("market") or "h2h"
                line = normalise_line(row.get("line"))
                sd = scan_date_of(row.get("scanned_at", ""))
                bid = bet_uuid(sd, kickoff, home, away, market, line, side, book)
                fid = self._ensure_fixture(kickoff, home, away, row.get("sport", ""))
                bk = self._book_id(book)
                values = self._bet_values(row, fid, bk, bid)
                cur.execute(insert_sql, (*values, bid))

    # --- paper bets --------------------------------------------------------

    _PAPER_COLS = (
        "id", "strategy_id", "fixture_id", "book_id", "scanned_at", "market",
        "line", "side", "odds", "impl_raw", "impl_effective", "edge",
        "edge_gross", "effective_odds", "commission_rate", "consensus",
        "pinnacle_cons", "n_books", "confidence", "model_signal", "dispersion",
        "outlier_z", "devig_method", "weight_scheme", "stake", "result",
    )

    def add_paper_bets(self, strategy_name: str, rows: list[dict]) -> Path:
        rows = list(rows)
        path = self.paper_dir / f"{strategy_name}.csv"
        _atomic_append_csv(path, PAPER_FIELDS, rows)
        if not rows or not self.db_enabled:
            return path
        with self._db_section() as cur:
            if cur is None:
                return path
            cols = ", ".join(self._PAPER_COLS)
            placeholders = ", ".join(["?"] * len(self._PAPER_COLS))
            insert_sql = (
                f"INSERT INTO paper_bets ({cols}) "
                f"SELECT {placeholders} "
                f"WHERE NOT EXISTS (SELECT 1 FROM paper_bets WHERE id = ?)"
            )
            sid = self._strategy_id(strategy_name)
            for row in rows:
                kickoff = row.get("kickoff", "")
                home = row.get("home", "")
                away = row.get("away", "")
                side = row.get("side", "")
                book = row.get("book", "")
                market = row.get("market") or "h2h"
                line = normalise_line(row.get("line"))
                sd = scan_date_of(row.get("scanned_at", ""))
                pid = paper_bet_uuid(strategy_name, sd, kickoff, home, away,
                                     market, line, side, book)
                fid = self._ensure_fixture(kickoff, home, away, row.get("sport", ""))
                bk = self._book_id(book)
                bet_vals = self._bet_values(row, fid, bk, pid)
                # Splice strategy_id at index 1 — order matches _PAPER_COLS.
                values = (bet_vals[0], sid) + bet_vals[1:]
                cur.execute(insert_sql, (*values, pid))
        return path

    # --- closing lines -----------------------------------------------------

    def add_closing_lines(self, rows: list[dict]) -> None:
        rows = list(rows)
        _atomic_append_csv(self.closing_csv, CLOSING_FIELDS, rows)
        if not rows or not self.db_enabled:
            return
        with self._db_section() as cur:
            if cur is None:
                return
            insert_sql = (
                "INSERT INTO closing_lines ("
                "  fixture_id, side, market, line, book_id, captured_at, "
                "  pinnacle_close_prob, pinnacle_raw_odds, "
                "  your_book_flagged_odds, your_book_close_odds, clv_pct"
                ") SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ? "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM closing_lines "
                "  WHERE fixture_id = ? AND side = ? AND market = ? "
                "    AND line = ? AND book_id = ?)"
            )
            pin_book_id = self._book_id(PINNACLE_BOOK)
            for row in rows:
                kickoff = row.get("kickoff", "")
                home = row.get("home", "")
                away = row.get("away", "")
                side = row.get("side", "")
                market = row.get("market") or "h2h"
                line_val = _f(row.get("line"))
                line_pk = 0.0 if line_val is None else line_val
                fid = self._ensure_fixture(kickoff, home, away, row.get("sport", ""))
                cur.execute(insert_sql, (
                    fid, side, market, line_pk, pin_book_id,
                    _parse_dt(row.get("captured_at")),
                    _f(row.get("pinnacle_devig_prob")
                       or row.get("pinnacle_close_prob")),
                    _f(row.get("pinnacle_raw_odds")),
                    _f(row.get("your_book_flagged_odds")),
                    _f(row.get("your_book_close_odds")),
                    _f(row.get("clv_pct")),
                    fid, side, market, line_pk, pin_book_id,
                ))

    # --- drift -------------------------------------------------------------

    def add_drift_snapshot(self, rows: list[dict]) -> None:
        rows = list(rows)
        _atomic_append_csv(self.drift_csv, DRIFT_FIELDS, rows)
        if not rows or not self.db_enabled:
            return
        with self._db_section() as cur:
            if cur is None:
                return
            insert_sql = (
                "INSERT INTO drift ("
                "  fixture_id, side, market, line, book_id, t_minus_min, "
                "  captured_at, your_book_odds, pinnacle_odds, n_books"
                ") SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ? "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM drift "
                "  WHERE fixture_id = ? AND side = ? AND market = ? "
                "    AND line = ? AND book_id = ? AND t_minus_min = ?)"
            )
            for row in rows:
                kickoff = row.get("kickoff", "")
                home = row.get("home", "")
                away = row.get("away", "")
                side = row.get("side", "")
                book = row.get("book", "") or PINNACLE_BOOK
                market = row.get("market") or "h2h"
                line_val = _f(row.get("line"))
                line_pk = 0.0 if line_val is None else line_val
                t_minus = _i(row.get("t_minus_min"))
                if t_minus is None:
                    continue
                fid = self._ensure_fixture(kickoff, home, away, row.get("sport", ""))
                bk = self._book_id(book)
                cur.execute(insert_sql, (
                    fid, side, market, line_pk, bk, t_minus,
                    _parse_dt(row.get("captured_at")),
                    _f(row.get("your_book_odds")),
                    _f(row.get("pinnacle_odds")),
                    _i(row.get("n_books")),
                    fid, side, market, line_pk, bk, t_minus,
                ))
