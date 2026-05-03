#!/usr/bin/env python3
"""
Numerical audit invariants I-1..I-13 over DB state.

Groups 1–4 ship now; groups 5–6 (I-14, I-15) deferred until book_skill
rows are populated.  Runs Mon 09:10 BST via GitHub Actions after FDCO
backfill (08:00) and compute_book_skill (09:05).

Requires BETS_DB_WRITE=1 + AZURE_SQL_KV_VAULT/AZURE_SQL_KV_SECRET (fetched
via `az keyvault secret show` — OIDC login must precede this script in CI).

Exit codes: 0 = all OK/WARN, 1 = ≥1 FAIL or DB unavailable.
"""
from __future__ import annotations

import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.config import load_leagues
from src.storage.repo import BetRepo

_NTFY_TOPIC = os.environ.get("NTFY_TOPIC_OVERRIDE", "robert-epl-bets-m4x9k")
_NTFY_URL = f"https://ntfy.sh/{_NTFY_TOPIC}" if _NTFY_TOPIC else ""

# Config-sourced, not user input — safe to interpolate into SQL.
_FDCO_SPORT_KEYS = frozenset(e["key"] for e in load_leagues() if "fdco_code" in e)
if not _FDCO_SPORT_KEYS:
    sys.exit("[audit] FATAL: no FDCO-covered leagues found in config — check load_leagues()")
_FDCO_KEYS_SQL = ", ".join(f"'{k}'" for k in _FDCO_SPORT_KEYS)

FAIL = "FAIL"
WARN = "WARN"
OK   = "OK"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _notify(title: str, body: str, priority: str = "default") -> None:
    if not _NTFY_URL:
        print(f"[ntfy] disabled — would send: {title}")
        return
    try:
        req = urllib.request.Request(
            _NTFY_URL,
            data=body.encode(),
            headers={"Title": title, "Priority": priority, "Tags": "rotating_light"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
        print(f"[ntfy] sent: {title!r}")
    except Exception as e:
        print(f"[ntfy] failed: {e}", file=sys.stderr)


def _scalar(cur, sql: str, *params):
    cur.execute(sql, *params)
    row = cur.fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Group 1 — Within-row arithmetic (bets + paper_bets)
# ---------------------------------------------------------------------------

def _check_i1_pnl(cur) -> tuple[str, str]:
    """P&L arithmetic: stored pnl matches recomputed value for all settled rows.

    NULL pnl on a settled row is also a FAIL — it means the settlement
    handler never wrote the value, which is a worse failure than a rounding gap.
    """
    bad = 0
    null_pnl = 0
    for table in ("bets", "paper_bets"):
        cur.execute(
            f"SELECT b.result, b.actual_stake, b.odds, b.commission_rate, b.pnl "
            f"FROM {table} b "
            f"WHERE b.actual_stake IS NOT NULL AND b.result IN ('W','L','V')"
        )
        for result, actual_stake, odds, comm_rate, pnl in cur.fetchall():
            if actual_stake is None or odds is None:
                continue
            if pnl is None:
                null_pnl += 1
                continue
            stake = float(actual_stake)
            o     = float(odds)
            comm  = float(comm_rate or 0)
            if result == "W":
                expected = round(stake * (o - 1) * (1 - comm), 2)
            elif result == "L":
                expected = round(-stake, 2)
            else:  # V
                expected = 0.0
            if abs(float(pnl) - expected) > 0.02:
                bad += 1
    parts = []
    if null_pnl:
        parts.append(f"{null_pnl} settled rows with NULL pnl")
    if bad:
        parts.append(f"{bad} rows with pnl mismatch (tolerance £0.02)")
    if parts:
        return FAIL, "; ".join(parts)
    return OK, "pnl arithmetic consistent across all settled rows"


def _check_i2_edge(cur) -> tuple[str, str]:
    """Edge must lie in [-0.20, 0.20] for every non-null row."""
    total = 0
    for table in ("bets", "paper_bets"):
        n = _scalar(
            cur,
            f"SELECT COUNT(*) FROM {table} WHERE edge IS NOT NULL "
            f"AND (edge < -0.20 OR edge > 0.20)",
        )
        total += n or 0
    if total:
        return FAIL, f"{total} rows with edge outside [-0.20, 0.20]"
    return OK, "all edge values in bounds"


def _check_i3_stake(cur) -> tuple[str, str]:
    """Stake must be ≥5 and divisible by 5 for all non-null stake rows.

    Project convention is whole-£5 stakes only; CAST to BIGINT before % 5
    to avoid decimal remainder noise (e.g. 10.00 % 5 can be non-zero in SQL).
    """
    total = 0
    for table in ("bets", "paper_bets"):
        n = _scalar(
            cur,
            f"SELECT COUNT(*) FROM {table} WHERE stake IS NOT NULL "
            f"AND (stake < 5 OR CAST(stake AS BIGINT) % 5 != 0)",
        )
        total += n or 0
    if total:
        return FAIL, f"{total} rows with invalid stake (not ≥5 or not divisible by 5)"
    return OK, "all stakes ≥5 and divisible by 5"


# ---------------------------------------------------------------------------
# Group 2 — Cross-source / dashboard parity
# ---------------------------------------------------------------------------

def _check_i4_pnl_parity(cur) -> tuple[str, str]:
    """SQL SUM(pnl) matches Python-level aggregate from the same rows."""
    cur.execute(
        "SELECT b.pnl FROM bets b "
        "WHERE b.actual_stake IS NOT NULL AND b.result IN ('W','L','V')"
    )
    rows = cur.fetchall()
    py_sum = sum(float(r[0]) for r in rows if r[0] is not None)
    sql_sum = _scalar(
        cur,
        "SELECT SUM(pnl) FROM bets "
        "WHERE actual_stake IS NOT NULL AND result IN ('W','L','V')",
    ) or 0.0
    delta = abs(py_sum - float(sql_sum))
    if delta > 0.05:
        return FAIL, f"P&L parity failure: SQL={float(sql_sum):.2f} Python={py_sum:.2f} delta={delta:.4f}"
    return OK, f"P&L parity OK: SUM={float(sql_sum):.2f}"


def _check_i5_stake_parity(cur) -> tuple[str, str]:
    """SQL SUM(actual_stake) matches Python-level aggregate."""
    cur.execute(
        "SELECT b.actual_stake FROM bets b "
        "WHERE b.actual_stake IS NOT NULL AND b.result IN ('W','L','V')"
    )
    rows = cur.fetchall()
    py_sum = sum(float(r[0]) for r in rows if r[0] is not None)
    sql_sum = _scalar(
        cur,
        "SELECT SUM(actual_stake) FROM bets "
        "WHERE actual_stake IS NOT NULL AND result IN ('W','L','V')",
    ) or 0.0
    delta = abs(py_sum - float(sql_sum))
    if delta > 0.05:
        return FAIL, f"Staked parity failure: SQL={float(sql_sum):.2f} Python={py_sum:.2f} delta={delta:.4f}"
    return OK, f"Staked parity OK: SUM=£{float(sql_sum):.2f}"


def _check_i6_stale_pending(cur) -> tuple[str, str]:
    """No bets stuck in 'pending' with kickoff > 7 days ago."""
    n = _scalar(
        cur,
        "SELECT COUNT(*) FROM bets b "
        "JOIN fixtures f ON f.id = b.fixture_id "
        "WHERE b.result = 'pending' AND f.kickoff_utc < DATEADD(day, -7, GETUTCDATE())",
    ) or 0
    if n:
        return FAIL, f"{n} bets pending with kickoff >7 days ago"
    return OK, "no stale pending bets"


# ---------------------------------------------------------------------------
# Group 3 — CLV pipeline
# ---------------------------------------------------------------------------

def _check_i7_clv_coverage(cur) -> tuple[str, str]:
    """≥70% of settled football bets on FDCO leagues (kickoff >14d) have clv_pct."""
    cur.execute(
        f"SELECT COUNT(*), SUM(CASE WHEN b.clv_pct IS NOT NULL THEN 1 ELSE 0 END) "
        f"FROM bets b "
        f"JOIN fixtures f ON f.id = b.fixture_id "
        f"WHERE b.result IN ('W','L','V') "
        f"  AND f.sport_key IN ({_FDCO_KEYS_SQL}) "
        f"  AND f.kickoff_utc < DATEADD(day, -14, GETUTCDATE())",
    )
    row = cur.fetchone()
    total, filled = int(row[0] or 0), int(row[1] or 0)
    if total == 0:
        return OK, "CLV coverage: no qualifying bets yet (total=0)"
    pct = filled / total
    if pct < 0.70:
        return FAIL, f"CLV coverage {pct:.0%} < 70% ({filled}/{total} bets on FDCO leagues)"
    return OK, f"CLV coverage {pct:.0%} ({filled}/{total})"


def _check_i8_clv_bounds(cur) -> tuple[str, str]:
    """clv_pct must be in [-0.50, 0.50]; outliers suggest join mismatch."""
    n = _scalar(
        cur,
        "SELECT COUNT(*) FROM bets "
        "WHERE clv_pct IS NOT NULL AND (clv_pct < -0.50 OR clv_pct > 0.50)",
    ) or 0
    if n:
        return FAIL, f"{n} bets with clv_pct outside [-0.50, 0.50] — likely join mismatch"
    return OK, "all clv_pct values in bounds"


def _check_i9_clv_shift(cur) -> tuple[str, str]:
    """Week-over-week avg CLV shift < 10pp (WARNING only, does not page).

    Compares avg clv_pct for bets settled in the last 7 days vs the prior
    7-day window (7–14 days ago).  All-SQL: stateless, works on ephemeral runners.
    """
    cur.execute(
        "SELECT "
        "  AVG(CASE WHEN b.settled_at >= DATEADD(day,-7,GETUTCDATE())  THEN CAST(b.clv_pct AS float) END),"
        "  AVG(CASE WHEN b.settled_at >= DATEADD(day,-14,GETUTCDATE()) "
        "           AND b.settled_at <  DATEADD(day,-7,GETUTCDATE())   THEN CAST(b.clv_pct AS float) END),"
        "  COUNT(CASE WHEN b.settled_at >= DATEADD(day,-7,GETUTCDATE())  AND b.clv_pct IS NOT NULL THEN 1 END),"
        "  COUNT(CASE WHEN b.settled_at >= DATEADD(day,-14,GETUTCDATE())"
        "             AND b.settled_at <  DATEADD(day,-7,GETUTCDATE())   AND b.clv_pct IS NOT NULL THEN 1 END) "
        "FROM bets b "
        "WHERE b.clv_pct IS NOT NULL AND b.result IN ('W','L','V')"
    )
    row = cur.fetchone()
    if row is None:
        return OK, "CLV shift: no data"
    curr_avg, prev_avg, curr_n, prev_n = row
    if curr_avg is None or prev_avg is None or int(curr_n or 0) < 3 or int(prev_n or 0) < 3:
        return OK, f"CLV shift: insufficient data (curr_n={curr_n}, prev_n={prev_n})"
    shift = abs(float(curr_avg) - float(prev_avg)) * 100
    if shift >= 10.0:
        return WARN, (
            f"CLV week-over-week shift {shift:.1f}pp "
            f"(prev={float(prev_avg):.4f} n={prev_n}, now={float(curr_avg):.4f} n={curr_n})"
        )
    return OK, f"CLV shift {shift:.1f}pp (within 10pp threshold)"


# ---------------------------------------------------------------------------
# Group 4 — book_skill construction
# ---------------------------------------------------------------------------

def _check_i10_loo_nonzero(cur) -> tuple[str, str]:
    """mean(ABS(edge_vs_consensus_loo)) > 0.0001 for latest window_end."""
    cur.execute(
        "SELECT TOP 1 window_end FROM book_skill "
        "WHERE edge_vs_consensus_loo IS NOT NULL "
        "ORDER BY window_end DESC"
    )
    row = cur.fetchone()
    if row is None:
        return OK, "book_skill: no rows yet (skipping I-10)"
    latest = row[0]
    avg_abs = _scalar(
        cur,
        "SELECT AVG(ABS(edge_vs_consensus_loo)) FROM book_skill "
        "WHERE window_end = ? AND edge_vs_consensus_loo IS NOT NULL",
        (latest,),
    )
    if avg_abs is None:
        return OK, f"book_skill: no LOO values for window {latest}"
    if float(avg_abs) <= 0.0001:
        return FAIL, f"LOO regression guard: mean(|edge_vs_consensus_loo|)={float(avg_abs):.6f} ≤ 0.0001 (window {latest})"
    return OK, f"LOO non-zero: mean(|edge_vs_consensus_loo|)={float(avg_abs):.6f} (window {latest})"


def _check_i11_divergence(cur) -> tuple[str, str]:
    """divergence == edge_vs_pinnacle - edge_vs_consensus_loo within 1e-7."""
    n = _scalar(
        cur,
        "SELECT COUNT(*) FROM book_skill "
        "WHERE divergence IS NOT NULL "
        "  AND edge_vs_pinnacle IS NOT NULL "
        "  AND edge_vs_consensus_loo IS NOT NULL "
        "  AND ABS(divergence - (edge_vs_pinnacle - edge_vs_consensus_loo)) > 1e-7",
    ) or 0
    total = _scalar(
        cur,
        "SELECT COUNT(*) FROM book_skill WHERE divergence IS NOT NULL",
    ) or 0
    if total == 0:
        return OK, "book_skill: no divergence rows yet"
    if n:
        return FAIL, f"{n}/{total} book_skill rows fail divergence identity"
    return OK, f"divergence identity holds for all {total} rows"


def _check_i12_row_pairs(cur) -> tuple[str, str]:
    """Every (book, league, market, window_end) has exactly one shin row and
    one multiplicative row — checks both cardinality and method identity."""
    total = _scalar(cur, "SELECT COUNT(*) FROM book_skill") or 0
    if total == 0:
        return OK, "book_skill: no rows yet"
    n_bad = _scalar(
        cur,
        "SELECT COUNT(*) FROM ("
        "  SELECT book, league, market, window_end"
        "  FROM book_skill"
        "  GROUP BY book, league, market, window_end"
        "  HAVING COUNT(*) != 2"
        "       OR COUNT(DISTINCT devig_method) != 2"
        "       OR SUM(CASE WHEN devig_method = 'shin'           THEN 1 ELSE 0 END) != 1"
        "       OR SUM(CASE WHEN devig_method = 'multiplicative' THEN 1 ELSE 0 END) != 1"
        ") x",
    ) or 0
    if n_bad:
        return FAIL, f"{n_bad} (book,league,market,window_end) groups missing shin+multiplicative pair"
    return OK, f"all book_skill groups have exactly one shin + one multiplicative row"


def _check_i13_n_fixtures(cur) -> tuple[str, str]:
    """No book_skill rows with n_fixtures <= 0."""
    n = _scalar(
        cur,
        "SELECT COUNT(*) FROM book_skill WHERE n_fixtures <= 0",
    ) or 0
    if n:
        return FAIL, f"{n} book_skill rows with n_fixtures ≤ 0"
    total = _scalar(cur, "SELECT COUNT(*) FROM book_skill") or 0
    if total == 0:
        return OK, "book_skill: no rows yet"
    return OK, "all n_fixtures > 0"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run() -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[audit] start {now}")

    repo = BetRepo()
    conn = repo._connect()
    if conn is None:
        msg = "DB unavailable — check BETS_DB_WRITE=1 and Azure credentials"
        print(f"[audit] FATAL: {msg}", file=sys.stderr)
        _notify("Bets audit FATAL", msg, priority="high")
        return 1

    cur = repo._cur

    checks = [
        ("I-1",  _check_i1_pnl(cur)),
        ("I-2",  _check_i2_edge(cur)),
        ("I-3",  _check_i3_stake(cur)),
        ("I-4",  _check_i4_pnl_parity(cur)),
        ("I-5",  _check_i5_stake_parity(cur)),
        ("I-6",  _check_i6_stale_pending(cur)),
        ("I-7",  _check_i7_clv_coverage(cur)),
        ("I-8",  _check_i8_clv_bounds(cur)),
        ("I-9",  _check_i9_clv_shift(cur)),
        ("I-10", _check_i10_loo_nonzero(cur)),
        ("I-11", _check_i11_divergence(cur)),
        ("I-12", _check_i12_row_pairs(cur)),
        ("I-13", _check_i13_n_fixtures(cur)),
    ]

    repo.close()

    failures = [(k, msg) for k, (status, msg) in checks if status == FAIL]
    warnings = [(k, msg) for k, (status, msg) in checks if status == WARN]

    for name, (status, msg) in checks:
        print(f"[audit] {status:4s}  {name}  {msg}")

    if failures:
        lines = "\n".join(f"{k}: {m}" for k, m in failures)
        if warnings:
            lines += "\nWARN:\n" + "\n".join(f"{k}: {m}" for k, m in warnings)
        _notify(
            f"Bets audit FAIL ({len(failures)} check{'s' if len(failures) > 1 else ''})",
            lines,
            priority="high",
        )
        print(f"[audit] done — {len(failures)} FAIL, {len(warnings)} WARN", file=sys.stderr)
        return 1

    if warnings:
        lines = "\n".join(f"{k}: {m}" for k, m in warnings)
        _notify("Bets audit WARN", lines, priority="default")

    print(f"[audit] done — all OK ({len(warnings)} WARN)")
    return 0


if __name__ == "__main__":
    sys.exit(run())
