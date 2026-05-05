"""
Strategy comparison report.

Reads paper_bets from Azure SQL via BetRepo. Prints a Markdown table to stdout
and writes one report file per day under logs/strategy_comparisons/.

Requires BETS_DB_WRITE=1 + AZURE_SQL_* env vars (see CLAUDE.md A.4).

Usage:
    python3 scripts/compare_strategies.py
    python3 scripts/compare_strategies.py --out path/to/file.md
"""

import argparse
import math
import os
import statistics
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.betting.strategies import STRATEGIES  # noqa: E402

OUT_DIR = _ROOT / "logs" / "strategy_comparisons"

# Buckets for favourite-longshot bias slice. Bounds are [lo, hi).
PROB_BUCKETS: list[tuple[float, float, str]] = [
    (0.00, 0.20, "0–20% (longshots)"),
    (0.20, 0.35, "20–35%"),
    (0.35, 0.50, "35–50%"),
    (0.50, 0.65, "50–65%"),
    (0.65, 0.80, "65–80%"),
    (0.80, 1.01, "80%+ (favourites)"),
]


# ── _filter_to_current_window (no-op for DB rows) ────────────────────────────

def _filter_to_current_window(rows: list[dict]) -> list[dict]:
    """No-op: DB rows don't carry strategy_config_hash; all rows are current."""
    return rows


# ── stats ─────────────────────────────────────────────────────────────────────

def _stats(rows: list[dict]) -> dict:
    n_bets = len(rows)
    clv_rows = [r for r in rows if r.get("clv_pct") not in ("", None)]
    n_with_clv = len(clv_rows)

    clv_values: list[float] = []
    for r in clv_rows:
        try:
            clv_values.append(float(r["clv_pct"]))
        except (ValueError, TypeError):
            pass

    avg_clv = sum(clv_values) / len(clv_values) if clv_values else None
    pos_clv = sum(1 for v in clv_values if v > 0) / len(clv_values) if clv_values else None
    median_clv = statistics.median(clv_values) if clv_values else None

    ci95_half = None
    if len(clv_values) >= 2:
        se = statistics.stdev(clv_values) / math.sqrt(len(clv_values))
        ci95_half = 1.96 * se

    edge_values: list[float] = []
    for r in rows:
        v = r.get("edge") or r.get("edge_gross")
        if v in ("", None):
            continue
        try:
            edge_values.append(float(v))
        except (ValueError, TypeError):
            pass
    avg_edge = sum(edge_values) / len(edge_values) if edge_values else None

    # Settled P&L stats
    settled_rows = [r for r in rows if r.get("result") in ("W", "L", "void")]
    settled = len(settled_rows)
    wins = sum(1 for r in settled_rows if r.get("result") == "W")
    win_pct = wins / settled if settled > 0 else None

    total_pnl: float | None = None
    roi_pct: float | None = None
    if settled > 0:
        pnl_vals = []
        stake_sum = 0.0
        for r in settled_rows:
            try:
                pnl_vals.append(float(r.get("pnl") or 0))
            except (TypeError, ValueError):
                pnl_vals.append(0.0)
            try:
                stake_sum += float(r.get("stake") or 0)
            except (TypeError, ValueError):
                pass
        total_pnl = sum(pnl_vals)
        roi_pct = (total_pnl / stake_sum * 100) if stake_sum > 0 else None

    return {
        "n_bets": n_bets,
        "n_with_clv": n_with_clv,
        "avg_clv": avg_clv,
        "median_clv": median_clv,
        "ci95_half": ci95_half,
        "pos_clv": pos_clv,
        "avg_edge": avg_edge,
        "settled": settled,
        "wins": wins,
        "win_pct": win_pct,
        "total_pnl": total_pnl,
        "roi_pct": roi_pct,
    }


def _fmt(val, fmt=".2%", fallback="—") -> str:
    if val is None:
        return fallback
    try:
        return format(val, fmt)
    except (ValueError, TypeError):
        return fallback


def _fmt_clv_ci(avg_clv, ci95_half) -> str:
    if avg_clv is None:
        return "—"
    base = format(avg_clv, ".2%")
    if ci95_half is None:
        return base
    return f"{base} ± {format(ci95_half, '.2%')}"


def _fmt_pnl_cols(s: dict) -> str:
    """Format Settled | Win % | ROI % columns."""
    settled = s.get("settled", 0)
    if not settled:
        return "— | — | —"
    settled_str = str(settled)
    win_pct_str = _fmt(s.get("win_pct"), ".0%") if settled >= 5 else "—"
    roi = s.get("roi_pct")
    roi_str = f"{roi:+.1f}%" if (settled >= 5 and roi is not None) else "—"
    return f"{settled_str} | {win_pct_str} | {roi_str}"


def _bucket_for(p: float) -> str | None:
    for lo, hi, label in PROB_BUCKETS:
        if lo <= p < hi:
            return label
    return None


def _bucket_stats(rows: list[dict]) -> list[dict]:
    """Bucket rows by `consensus` (Shin-fair prob), summarise CLV per bucket."""
    buckets: dict[str, list[float]] = {label: [] for _, _, label in PROB_BUCKETS}

    for r in rows:
        cons = r.get("consensus")
        clv  = r.get("clv_pct")
        if cons in ("", None) or clv in ("", None):
            continue
        try:
            cons_f = float(cons)
            clv_f  = float(clv)
        except (ValueError, TypeError):
            continue
        label = _bucket_for(cons_f)
        if label is not None:
            buckets[label].append(clv_f)

    out = []
    for _, _, label in PROB_BUCKETS:
        vals = buckets[label]
        n = len(vals)
        avg = sum(vals) / n if n else None
        pos = sum(1 for v in vals if v > 0) / n if n else None
        out.append({"bucket": label, "n": n, "avg_clv": avg, "pos_clv": pos})
    return out


def _per_sport_rows(entries: list[tuple[str, list[dict]]]) -> list[dict]:
    from collections import defaultdict
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for name, rows in entries:
        for r in rows:
            sport = r.get("sport") or r.get("sport_key", "")
            if sport:
                grouped[(name, sport)].append(r)

    out = []
    for (name, sport), rows in grouped.items():
        s = _stats(rows)
        qualifies = (
            (name == "A_production" and s["n_with_clv"] >= 1)
            or s["n_with_clv"] >= 10
        )
        if qualifies:
            out.append({"sport": sport, "variant": name, **s})

    if not out:
        return []

    def _sport_key(row):
        sport_order = 0 if row["sport"] == "EPL" else 1
        return (sport_order, row["sport"], row["avg_clv"] is None, -(row["avg_clv"] or 0))

    out.sort(key=_sport_key)
    return out


_CONF_ORDER = {"HIGH": 0, "MED": 1, "LOW": 2}
_MKT_ORDER  = {"h2h": 0, "totals": 1, "btts": 2}
_SIG_ORDER  = {"agrees": 0, "disagrees": 1, "no_signal": 2}


def _model_bucket(signal) -> str:
    if signal in ("?", "", None):
        return "no_signal"
    try:
        return "agrees" if float(str(signal).lstrip("+")) > 0 else "disagrees"
    except (ValueError, TypeError):
        return "no_signal"


def _sliced_rows(
    entries: list[tuple[str, list[dict]]],
    key_fn,
    threshold: int = 5,
) -> list[dict]:
    from collections import defaultdict
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for name, rows in entries:
        for r in rows:
            k = key_fn(r)
            if k is not None:
                grouped[(name, k)].append(r)

    out = []
    for (name, k), rows in grouped.items():
        s = _stats(rows)
        if s["n_with_clv"] >= threshold:
            out.append({"slice_key": k, "variant": name, **s})
    return out


def _dedupe_pool(entries: list[tuple[str, list[dict]]]) -> list[dict]:
    """Pool unique bets across all paper strategies, deduped by natural key."""
    seen: set[tuple] = set()
    pooled: list[dict] = []
    for _, rows in entries:
        for r in rows:
            key = (
                r.get("kickoff", "") or r.get("kickoff_utc", ""),
                r.get("home", ""),
                r.get("away", ""),
                r.get("market", ""),
                str(r.get("line", "")),
                r.get("side", ""),
                r.get("book", ""),
            )
            if key in seen:
                continue
            seen.add(key)
            pooled.append(r)
    return pooled


def build_report(repo=None, all_history: bool = False) -> str:
    """Build the strategy comparison Markdown report.

    repo: a BetRepo instance (or None to construct one internally).
    """
    # T4: refuse without DB
    if os.environ.get("BETS_DB_WRITE", "").strip() != "1":
        print(
            "compare_strategies.py reads from Azure SQL only. "
            "Set BETS_DB_WRITE=1 + AZURE_SQL_* env vars.",
            file=sys.stderr,
        )
        sys.exit(1)

    from src.storage.repo import BetRepo
    if repo is None:
        repo = BetRepo(logs_dir=_ROOT / "logs")

    raw_rows = repo.fetch_paper_bets_for_compare()
    if raw_rows is None:
        return "No data found. Run the scanner first.\n"

    # Group by strategy_name
    from collections import defaultdict
    by_strategy: dict[str, list[dict]] = defaultdict(list)
    for r in raw_rows:
        by_strategy[r["strategy_name"]].append(r)

    entries: list[tuple[str, list[dict]]] = list(by_strategy.items())

    # Include configured variants with no DB rows yet (0-bet)
    seen_names = {name for name, _ in entries}
    for s in STRATEGIES:
        if s.name not in seen_names:
            entries.append((s.name, []))

    if not entries:
        return "No data found. Run the scanner first.\n"

    results = []
    for name, rows in entries:
        s = _stats(rows)
        results.append((name, s))
    results.sort(key=lambda x: (
        x[1]["n_bets"] == 0,
        x[1]["avg_clv"] is None,
        -(x[1]["avg_clv"] or 0),
    ))

    best_name = results[0][0] if results and results[0][1]["avg_clv"] is not None else ""

    lines = [
        "# Strategy Comparison",
        "",
        "Sorted by average CLV descending. Only rows with a Pinnacle close prob contribute to CLV stats.",
        "Run `python3 scripts/compare_strategies.py` to refresh.",
        "",
        "> **Data source:** Azure SQL `paper_bets` table.",
        "",
        "> **Sample size note.** Variants with `<10` CLV bets in this report are"
        " indicative only. Per `RESEARCH_NOTES_2026-04.md` §6, graduation requires"
        " ≥30 CLV bets across ≥3 weekends with positive Avg CLV CI bracket.",
        "",
        "> Variants with 0 bets this period are listed for completeness; if a variant"
        " you expect to fire shows 0, check its filter wiring.",
        "",
        "| Strategy | Bets | CLV bets | Avg CLV ± 95% CI | Med CLV | CLV >0 % | Avg Edge | Settled | Win % | ROI % |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]

    for name, s in results:
        marker = " ★" if name == best_name else ""
        low_n = s["n_with_clv"] < 10
        prefix = "[low n] " if low_n else ""
        pnl_cols = _fmt_pnl_cols(s)

        if s["n_bets"] == 0:
            lines.append(f"| {prefix}{name} | 0 | — | — | — | — | — | — | — | — |")
        else:
            clv_ci = _fmt_clv_ci(s["avg_clv"], s["ci95_half"])
            lines.append(
                f"| {prefix}{name}{marker} | {s['n_bets']} | {s['n_with_clv']} | "
                f"{clv_ci} | {_fmt(s['median_clv'])} | {_fmt(s['pos_clv'])} | "
                f"{_fmt(s['avg_edge'])} | {pnl_cols} |"
            )

    lines += [
        "",
        "*95% CI is `±1.96·σ/√n`. A variant whose CI bracket includes 0 has not yet"
        " shown a statistically distinguishable signal.*",
    ]

    # Per-sport breakdown
    sport_rows = _per_sport_rows(entries)
    if sport_rows:
        lines += [
            "",
            "## CLV by sport",
            "",
            "A_production shown as baseline for any sport with ≥1 CLV bet;"
            " other variants shown only where n_with_clv ≥ 10.",
            "",
            "| Sport | Variant | Bets | CLV bets | Avg CLV | CLV >0 % |",
            "|---|---|---|---|---|---|",
        ]
        for row in sport_rows:
            lines.append(
                f"| {row['sport']} | {row['variant']} | {row['n_bets']} |"
                f" {row['n_with_clv']} | {_fmt(row['avg_clv'])} | {_fmt(row['pos_clv'])} |"
            )

    # Per-confidence breakdown
    conf_rows = _sliced_rows(
        entries,
        lambda r: r.get("confidence") if r.get("confidence") in _CONF_ORDER else None,
    )
    if conf_rows:
        conf_rows.sort(key=lambda x: (x["variant"], _CONF_ORDER.get(x["slice_key"], 99)))
        lines += [
            "",
            "## CLV by confidence",
            "",
            "Rows where n_with_clv ≥ 5 per (variant, confidence) tier.",
            "",
            "| Confidence | Variant | Bets | CLV bets | Avg CLV | CLV >0 % |",
            "|---|---|---|---|---|---|",
        ]
        for row in conf_rows:
            lines.append(
                f"| {row['slice_key']} | {row['variant']} | {row['n_bets']} |"
                f" {row['n_with_clv']} | {_fmt(row['avg_clv'])} | {_fmt(row['pos_clv'])} |"
            )

    # Per-market breakdown
    mkt_rows = _sliced_rows(
        entries,
        lambda r: r.get("market") if r.get("market") in _MKT_ORDER else None,
    )
    if mkt_rows:
        mkt_rows.sort(key=lambda x: (x["variant"], _MKT_ORDER.get(x["slice_key"], 99)))
        lines += [
            "",
            "## CLV by market",
            "",
            "Rows where n_with_clv ≥ 5 per (variant, market).",
            "",
            "| Market | Variant | Bets | CLV bets | Avg CLV | CLV >0 % |",
            "|---|---|---|---|---|---|",
        ]
        for row in mkt_rows:
            lines.append(
                f"| {row['slice_key']} | {row['variant']} | {row['n_bets']} |"
                f" {row['n_with_clv']} | {_fmt(row['avg_clv'])} | {_fmt(row['pos_clv'])} |"
            )

    # Model-signal stratification
    sig_rows = _sliced_rows(
        entries,
        lambda r: _model_bucket(r.get("model_signal")),
    )
    if sig_rows:
        sig_rows.sort(key=lambda x: (x["variant"], _SIG_ORDER.get(x["slice_key"], 99)))
        lines += [
            "",
            "## CLV by model signal",
            "",
            "Rows where n_with_clv ≥ 5 per (variant, model-signal bucket)."
            " `agrees` = model edge > 0; `disagrees` = model edge ≤ 0; `no_signal` = `?` or missing.",
            "",
            "| Signal | Variant | Bets | CLV bets | Avg CLV | CLV >0 % |",
            "|---|---|---|---|---|---|",
        ]
        for row in sig_rows:
            lines.append(
                f"| {row['slice_key']} | {row['variant']} | {row['n_bets']} |"
                f" {row['n_with_clv']} | {_fmt(row['avg_clv'])} | {_fmt(row['pos_clv'])} |"
            )

    # Favourite-longshot bias slice
    pooled = _dedupe_pool(entries)
    bucket_rows = _bucket_stats(pooled)
    n_pool_with_clv = sum(b["n"] for b in bucket_rows)

    lines += [
        "",
        "## CLV by consensus-prob bucket (favourite-longshot bias check)",
        "",
        "Pooled across all paper strategies, deduped by `(kickoff, home, away, market, line, side, book)`. "
        "Bucketed by Shin-devigged consensus probability of the side bet on. "
        "Persistent negative CLV in a single bucket = favourite-longshot bias signal in our flow.",
        f"Sample: {n_pool_with_clv} unique bets with CLV.",
        "",
        "| Bucket | Bets | Avg CLV | CLV >0 % |",
        "|---|---|---|---|",
    ]
    for b in bucket_rows:
        lines.append(
            f"| {b['bucket']} | {b['n']} | {_fmt(b['avg_clv'])} | {_fmt(b['pos_clv'])} |"
        )

    lines += ["", "*Generated from Azure SQL `paper_bets` table.*", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Strategy comparison report (CLV-based).")
    parser.add_argument("--all-history", action="store_true",
                        help="Kept for CLI compat; no effect (DB rows are always current).")
    parser.add_argument("--out", default=None,
                        help="Output path (default: logs/strategy_comparisons/<YYYY-MM-DD>.md)")
    args = parser.parse_args()
    report = build_report(all_history=args.all_history)
    print(report)
    out_path = Path(args.out) if args.out else (
        OUT_DIR / f"{datetime.utcnow().strftime('%Y-%m-%d')}.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    try:
        rel = out_path.relative_to(_ROOT)
    except ValueError:
        rel = out_path
    print(f"[compare] Report written to {rel}")


if __name__ == "__main__":
    main()
