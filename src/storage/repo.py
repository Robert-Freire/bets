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

# Reverse lookup so DB-side sport_key joins back to the human label the
# dashboard / CSV format expects ("EPL", "Bundesliga", etc.). Tennis labels
# are dynamic and stored as the label itself in sport_key, so the reverse
# falls through unchanged.
KEY_TO_LABEL: dict[str, str] = {v: k for k, v in LABEL_TO_KEY.items()}

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

    # --- read API (A.5: dashboard reads DB-first with CSV fallback) -------

    def db_status(self) -> str:
        """One of 'ok' (configured + reachable), 'down' (configured but
        connect failed), 'disabled' (no env flags). The dashboard uses
        this to decide whether to render the fallback banner."""
        if self._dsn is None:
            return "disabled"
        if self._db_failed:
            return "down"
        if self._connect() is None:
            return "down"
        return "ok"

    @staticmethod
    def _format_kickoff(dt) -> str:
        if dt is None:
            return ""
        if isinstance(dt, str):
            # SQLite returns datetimes as ISO strings ("YYYY-MM-DD HH:MM:SS").
            # CSV format used elsewhere is "%Y-%m-%d %H:%M" — strip seconds.
            parsed = _parse_dt(dt)
            if parsed is None:
                return dt
            dt = parsed
        return dt.strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def _format_scanned_at(dt) -> str:
        if dt is None:
            return ""
        if isinstance(dt, str):
            parsed = _parse_dt(dt)
            if parsed is None:
                return dt
            dt = parsed
        return dt.strftime("%Y-%m-%d %H:%M UTC")

    @staticmethod
    def _stringify(value) -> str:
        if value is None:
            return ""
        return str(value)

    def get_bets(self) -> list[dict] | None:
        """Return all production bets as CSV-style row dicts, ordered by
        scanned_at ascending. Returns None if DB not enabled OR the DB
        read failed — caller should fall back to the CSV reader."""
        if not self.db_enabled:
            return None
        if self._connect() is None:
            return None
        try:
            cur = self._cur
            cur.execute(
                "SELECT b.scanned_at, f.sport_key, b.market, b.line, "
                "       f.home, f.away, f.kickoff_utc, b.side, bk.name, "
                "       b.odds, b.impl_raw, b.impl_effective, b.edge, "
                "       b.edge_gross, b.effective_odds, b.commission_rate, "
                "       b.consensus, b.pinnacle_cons, b.n_books, b.confidence, "
                "       b.model_signal, b.dispersion, b.outlier_z, "
                "       b.devig_method, b.weight_scheme, b.stake, "
                "       b.actual_stake, b.result, b.pnl, "
                "       b.pinnacle_close_prob, b.clv_pct "
                "FROM bets b "
                "JOIN fixtures f ON f.id = b.fixture_id "
                "JOIN books bk ON bk.id = b.book_id "
                "ORDER BY b.scanned_at"
            )
            out: list[dict] = []
            for r in cur.fetchall():
                (scanned_at, sport_key, market, line, home, away, kickoff,
                 side, book, odds, impl_raw, impl_effective, edge, edge_gross,
                 effective_odds, commission_rate, consensus, pinnacle_cons,
                 n_books, confidence, model_signal, dispersion, outlier_z,
                 devig_method, weight_scheme, stake, actual_stake, result, pnl,
                 pinnacle_close_prob, clv_pct) = r
                out.append({
                    "_source": "db",
                    "scanned_at": self._format_scanned_at(scanned_at),
                    "sport": KEY_TO_LABEL.get(sport_key, sport_key),
                    "market": market or "h2h",
                    "line": self._stringify(line) if line is not None else "",
                    "home": home,
                    "away": away,
                    "kickoff": self._format_kickoff(kickoff),
                    "side": side,
                    "book": book,
                    "odds": self._stringify(odds),
                    "impl_raw": self._stringify(impl_raw),
                    "impl_effective": self._stringify(impl_effective),
                    "edge": self._stringify(edge),
                    "edge_gross": self._stringify(edge_gross),
                    "effective_odds": self._stringify(effective_odds),
                    "commission_rate": self._stringify(commission_rate),
                    "consensus": self._stringify(consensus),
                    "pinnacle_cons": self._stringify(pinnacle_cons),
                    "n_books": self._stringify(n_books),
                    "confidence": confidence or "",
                    "model_signal": model_signal or "?",
                    "dispersion": self._stringify(dispersion),
                    "outlier_z": self._stringify(outlier_z),
                    "devig_method": devig_method or "shin",
                    "weight_scheme": weight_scheme or "uniform",
                    "stake": self._stringify(stake),
                    "actual_stake": self._stringify(actual_stake),
                    # Schema stores 'pending' as default; the dashboard's
                    # convention is empty-string for unsettled.
                    "result": "" if (result or "") == "pending" else (result or ""),
                    "pnl": self._stringify(pnl),
                    "pinnacle_close_prob": self._stringify(pinnacle_close_prob),
                    "clv_pct": self._stringify(clv_pct),
                })
            return out
        except Exception as e:
            print(f"[repo] WARN: get_bets failed; falling back to CSV: {e}",
                  file=sys.stderr)
            self._db_failed = True
            return None

    def get_drift(self) -> dict[tuple, list[dict]] | None:
        """Return drift snapshots keyed by (home, away, kickoff, side,
        market, line) — same shape app.load_drift() produces. Returns
        None on DB-disabled or failure."""
        if not self.db_enabled:
            return None
        if self._connect() is None:
            return None
        try:
            cur = self._cur
            cur.execute(
                "SELECT f.home, f.away, f.kickoff_utc, d.side, d.market, "
                "       d.line, bk.name, d.t_minus_min, d.your_book_odds, "
                "       d.pinnacle_odds, d.n_books, d.captured_at "
                "FROM drift d "
                "JOIN fixtures f ON f.id = d.fixture_id "
                "JOIN books bk ON bk.id = d.book_id "
                "ORDER BY d.captured_at"
            )
            by_bet: dict[tuple, list[dict]] = {}
            for r in cur.fetchall():
                (home, away, kickoff, side, market, line, book, t_minus,
                 your_book_odds, pinnacle_odds, n_books, captured_at) = r
                line_str = "" if line in (0, 0.0, None) else self._stringify(line)
                row = {
                    "captured_at": self._format_scanned_at(captured_at),
                    "home": home, "away": away,
                    "kickoff": self._format_kickoff(kickoff),
                    "side": side, "market": market, "line": line_str,
                    "book": book,
                    "t_minus_min": self._stringify(t_minus),
                    "your_book_odds": self._stringify(your_book_odds),
                    "pinnacle_odds": self._stringify(pinnacle_odds),
                    "n_books": self._stringify(n_books),
                }
                key = (home, away, self._format_kickoff(kickoff), side,
                       market or "h2h", line_str)
                by_bet.setdefault(key, []).append(row)
            for rows in by_bet.values():
                rows.sort(key=lambda r: int(r["t_minus_min"] or 0), reverse=True)
            return by_bet
        except Exception as e:
            print(f"[repo] WARN: get_drift failed; falling back to CSV: {e}",
                  file=sys.stderr)
            self._db_failed = True
            return None

    # --- book_skill (B.0) -----------------------------------------------------

    _BOOK_SKILL_COLS = (
        "book", "league", "market", "window_end", "n_fixtures",
        "brier_vs_close", "brier_vs_outcome", "log_loss",
        "fav_longshot_slope", "home_bias", "draw_bias",
        "flag_rate", "mean_flag_edge",
        "edge_vs_consensus", "edge_vs_pinnacle", "divergence",
        "truth_anchor",
    )

    def write_book_skill(self, rows: list[dict]) -> None:
        """Upsert book_skill rows (delete + re-insert per composite PK).

        No-op if DB not enabled. Pi-safe: never called when BETS_DB_WRITE
        is unset, and never imports pyodbc at top level.
        """
        if not rows or not self.db_enabled:
            return
        with self._db_section() as cur:
            if cur is None:
                return
            cols = ", ".join(self._BOOK_SKILL_COLS)
            placeholders = ", ".join(["?"] * len(self._BOOK_SKILL_COLS))
            for row in rows:
                book = row.get("book") or ""
                league = row.get("league") or ""
                market = row.get("market") or "h2h"
                window_end = row.get("window_end") or ""
                cur.execute(
                    "DELETE FROM book_skill "
                    "WHERE book = ? AND league = ? AND market = ? AND window_end = ?",
                    (book, league, market, window_end),
                )
                _STR_COLS = {"book", "league", "market", "window_end", "truth_anchor"}
                vals: list = []
                for c in self._BOOK_SKILL_COLS:
                    v = row.get(c)
                    if c in _STR_COLS:
                        vals.append(v)
                    elif c == "n_fixtures":
                        vals.append(_i(v))
                    else:
                        vals.append(_f(v))
                cur.execute(
                    f"INSERT INTO book_skill ({cols}) VALUES ({placeholders})",
                    vals,
                )

    # --- update settle --------------------------------------------------------

    def update_bet_settle(self, scan_date: str, kickoff: str, home: str,
                          away: str, market: str, line: str, side: str,
                          book: str, *, result: str, actual_stake: float | str | None,
                          pnl: float | str | None, odds: float | str | None = None) -> bool:
        """Update an existing bet's settle data in the DB.

        The bet UUID is recomputed from the natural key — same as the
        write path. Returns True if the UPDATE succeeded (regardless of
        rows affected — a missing row is not an error here, it just means
        the bet exists in CSV but not yet in DB, e.g. legacy data).
        """
        if not self.db_enabled:
            return False
        bid = bet_uuid(scan_date, kickoff, home, away, market or "h2h",
                       normalise_line(line), side, book)
        with self._db_section() as cur:
            if cur is None:
                return False
            settled_at = datetime.utcnow()
            if odds is not None and _f(odds) is not None:
                cur.execute(
                    "UPDATE bets SET result = ?, actual_stake = ?, pnl = ?, "
                    "odds = ?, settled_at = ? WHERE id = ?",
                    (result or "pending", _f(actual_stake), _f(pnl),
                     _f(odds), settled_at, bid),
                )
            else:
                cur.execute(
                    "UPDATE bets SET result = ?, actual_stake = ?, pnl = ?, "
                    "settled_at = ? WHERE id = ?",
                    (result or "pending", _f(actual_stake), _f(pnl),
                     settled_at, bid),
                )
        return True
