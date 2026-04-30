"""
Strategy comparison report.

Reads logs/paper/*.csv and logs/bets.csv. Prints a Markdown table to stdout
and writes docs/STRATEGY_COMPARISON.md.

Usage:
    python3 scripts/compare_strategies.py
"""

import csv
import math
import statistics
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.betting.strategies import STRATEGIES  # noqa: E402

BETS_CSV  = _ROOT / "logs" / "bets.csv"
PAPER_DIR = _ROOT / "logs" / "paper"
OUT_DOC   = _ROOT / "docs" / "STRATEGY_COMPARISON.md"

# Buckets for favourite-longshot bias slice. Bounds are [lo, hi).
# The top bucket uses 1.01 to keep prob=1.0 inclusive.
PROB_BUCKETS: list[tuple[float, float, str]] = [
    (0.00, 0.20, "0–20% (longshots)"),
    (0.20, 0.35, "20–35%"),
    (0.35, 0.50, "35–50%"),
    (0.50, 0.65, "50–65%"),
    (0.65, 0.80, "65–80%"),
    (0.80, 1.01, "80%+ (favourites)"),
]


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _stats(rows: list[dict]) -> dict:
    n_bets = len(rows)
    clv_rows = [r for r in rows if r.get("clv_pct") not in ("", None)]
    n_with_clv = len(clv_rows)

    clv_values = []
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

    edge_values = []
    for r in rows:
        # Prefer edge_gross (Kaunitz devigged metric, consistent with production filter)
        v = r.get("edge_gross") or r.get("edge")
        if v in ("", None):
            continue
        try:
            edge_values.append(float(v))
        except (ValueError, TypeError):
            pass
    avg_edge = sum(edge_values) / len(edge_values) if edge_values else None

    # book_dist scoped to CLV rows so the denominator matches avg_clv
    book_counter: Counter = Counter()
    for r in clv_rows:
        b = r.get("book", "")
        if b:
            book_counter[b] += 1
    top_books = ", ".join(f"{b}({c})" for b, c in book_counter.most_common(3))

    return {
        "n_bets": n_bets,
        "n_with_clv": n_with_clv,
        "avg_clv": avg_clv,
        "median_clv": median_clv,
        "ci95_half": ci95_half,
        "pos_clv": pos_clv,
        "avg_edge": avg_edge,
        "book_dist": top_books,
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


def _dedupe_pool(entries: list[tuple[str, list[dict]]]) -> list[dict]:
    """Pool unique bets across all paper strategies (one bet may flag in multiple strategies)."""
    seen: set[tuple] = set()
    pooled: list[dict] = []
    for _, rows in entries:
        for r in rows:
            key = (
                r.get("kickoff", ""),
                r.get("home", ""),
                r.get("away", ""),
                r.get("market", ""),
                r.get("line", ""),
                r.get("side", ""),
                r.get("book", ""),
            )
            if key in seen:
                continue
            seen.add(key)
            pooled.append(r)
    return pooled


def build_report() -> str:
    entries: list[tuple[str, list[dict]]] = []

    # Paper strategy CSVs (A_production is the canonical proxy for production)
    if PAPER_DIR.exists():
        for path in sorted(PAPER_DIR.glob("*.csv")):
            rows = _read_csv(path)
            if rows:
                entries.append((path.stem, rows))

    # C.1: include configured variants with no CSV yet (0-bet rows)
    seen_names = {name for name, _ in entries}
    for s in STRATEGIES:
        if s.name not in seen_names:
            entries.append((s.name, []))

    if not entries:
        return "No data found. Run the scanner first.\n"

    # Compute stats and sort: active variants by avg_clv desc, then no-CLV, then 0-bet
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
        # C.9: sample-size warning
        "> **Sample size note.** Variants with `<10` CLV bets in this report are"
        " indicative only. Per `RESEARCH_NOTES_2026-04.md` §6, graduation requires"
        " ≥30 CLV bets across ≥3 weekends with positive Avg CLV CI bracket.",
        "",
        # C.1: note about 0-bet variants
        "> Variants with 0 bets this period are listed for completeness; if a variant"
        " you expect to fire shows 0, check its filter wiring (e.g. `K_draw_bias`"
        " requires `logs/team_xg.json` and an alias-resolved team name).",
        "",
        "| Strategy | Bets | CLV bets | Avg CLV ± 95% CI | Med CLV | CLV >0 % | Avg Edge | Top books |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for name, s in results:
        marker = " ★" if name == best_name else ""
        # C.9: low-n marker
        low_n = s["n_with_clv"] < 10
        prefix = "[low n] " if low_n else ""

        if s["n_bets"] == 0:
            # C.1: 0-bet row
            lines.append(f"| {prefix}{name} | 0 | — | — | — | — | — | — |")
        else:
            clv_ci = _fmt_clv_ci(s["avg_clv"], s["ci95_half"])
            lines.append(
                f"| {prefix}{name}{marker} | {s['n_bets']} | {s['n_with_clv']} | "
                f"{clv_ci} | {_fmt(s['median_clv'])} | {_fmt(s['pos_clv'])} | "
                f"{_fmt(s['avg_edge'])} | {s['book_dist'] or '—'} |"
            )

    # C.2: note about CI interpretation
    lines += [
        "",
        "*95% CI is `±1.96·σ/√n`. A variant whose CI bracket includes 0 has not yet"
        " shown a statistically distinguishable signal.*",
    ]

    # ---- Favourite-longshot bias slice -------------------------------------
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

    lines += ["", f"*Generated: see `logs/paper/` and `logs/bets.csv`.*", ""]
    return "\n".join(lines)


def main():
    report = build_report()
    print(report)
    OUT_DOC.parent.mkdir(exist_ok=True)
    OUT_DOC.write_text(report)
    print(f"[compare] Report written to {OUT_DOC.relative_to(_ROOT)}")


if __name__ == "__main__":
    main()
