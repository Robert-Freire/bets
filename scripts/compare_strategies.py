"""
Strategy comparison report.

Reads logs/paper/*.csv and logs/bets.csv. Prints a Markdown table to stdout
and writes docs/STRATEGY_COMPARISON.md.

Usage:
    python3 scripts/compare_strategies.py
"""

import argparse
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
DRIFT_CSV = _ROOT / "logs" / "drift.csv"
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


def _filter_to_current_window(rows: list[dict]) -> list[dict]:
    """Keep only rows whose strategy_config_hash matches the most-recent hash in the file.
    Rows without the column (pre-R.11) collapse into one 'pre-R.11' window with hash=''.
    Returns input unchanged if no rows have any hash field at all (full pre-R.11 file)."""
    if not rows:
        return rows
    hashes = {r.get("strategy_config_hash", "") for r in rows}
    if hashes == {""}:
        return rows  # entirely pre-R.11; nothing to filter
    rows_sorted = sorted(rows, key=lambda r: r.get("scanned_at", ""))
    current_hash = rows_sorted[-1].get("strategy_config_hash", "")
    return [r for r in rows if r.get("strategy_config_hash", "") == current_hash]


def _load_drift_index() -> dict[tuple, tuple[float, float]] | None:
    """Return {(kickoff, home, away, market, line, side): (prob_t60, prob_close)} or None."""
    if not DRIFT_CSV.exists():
        return None

    # Collect T-60 and T-1 Pinnacle odds per bet key
    t60: dict[tuple, float] = {}
    t1:  dict[tuple, float] = {}

    for row in _read_csv(DRIFT_CSV):
        pin_odds_str = row.get("pinnacle_odds", "")
        if not pin_odds_str:
            continue
        try:
            pin_prob = 1.0 / float(pin_odds_str)
        except (ValueError, ZeroDivisionError):
            continue

        key = (
            row.get("kickoff", ""),
            row.get("home", ""),
            row.get("away", ""),
            row.get("market", ""),
            str(row.get("line", "")),
            row.get("side", ""),
        )
        t = row.get("t_minus_min", "")
        try:
            t_int = int(t)
        except (ValueError, TypeError):
            continue

        if t_int == 60:
            t60[key] = pin_prob
        elif t_int == 1:
            t1[key] = pin_prob

    # Build index only for keys that have both T-60 and T-1 readings
    index = {k: (t60[k], t1[k]) for k in t60 if k in t1}
    return index if index else None


def _stats(rows: list[dict], drift_index: dict | None = None) -> dict:
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
        # Prefer edge (true value: cons − effective_implied_prob); fall back to edge_gross for pre-fix rows
        v = r.get("edge") or r.get("edge_gross")
        if v in ("", None):
            continue
        try:
            edge_values.append(float(v))
        except (ValueError, TypeError):
            pass
    avg_edge = sum(edge_values) / len(edge_values) if edge_values else None

    # Drift-toward-you: compare Pinnacle implied prob at T-60 vs T-1
    drift_pct = None
    if drift_index:
        moved_toward = 0
        total_with_drift = 0
        for r in rows:
            key = (
                r.get("kickoff", ""),
                r.get("home", ""),
                r.get("away", ""),
                r.get("market", ""),
                str(r.get("line", "")),
                r.get("side", ""),
            )
            if key in drift_index:
                prob_t60, prob_close = drift_index[key]
                total_with_drift += 1
                if prob_close > prob_t60:
                    moved_toward += 1
        if total_with_drift:
            drift_pct = moved_toward / total_with_drift

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
        "drift_pct": drift_pct,
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


def _per_sport_rows(entries: list[tuple[str, list[dict]]]) -> list[dict]:
    """Return qualifying (sport, variant) rows for the per-sport table.

    Includes A_production for any sport where it has ≥1 CLV bet, and all other
    variants where n_with_clv ≥ 10 in that sport.
    """
    # Group rows by (variant, sport)
    from collections import defaultdict
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for name, rows in entries:
        for r in rows:
            sport = r.get("sport", "")
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

    # EPL first, then alphabetical; within sport sort by avg_clv desc (None last)
    def _sport_key(row):
        sport_order = 0 if row["sport"] == "EPL" else 1
        return (sport_order, row["sport"], row["avg_clv"] is None, -(row["avg_clv"] or 0))

    out.sort(key=_sport_key)
    return out


_CONF_ORDER  = {"HIGH": 0, "MED": 1, "LOW": 2}
_MKT_ORDER   = {"h2h": 0, "totals": 1, "btts": 2}
_SIG_ORDER   = {"agrees": 0, "disagrees": 1, "no_signal": 2}


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
    """Group rows by (variant, slice_key), return rows where n_with_clv >= threshold."""
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


def build_report(all_history: bool = False) -> str:
    entries: list[tuple[str, list[dict]]] = []
    filter_summary: list[tuple[str, int, int]] = []  # (variant, current_n, total_n) when they differ

    # Paper strategy CSVs (A_production is the canonical proxy for production)
    if PAPER_DIR.exists():
        for path in sorted(PAPER_DIR.glob("*.csv")):
            rows = _read_csv(path)
            if rows:
                if not all_history:
                    filtered = _filter_to_current_window(rows)
                    if len(filtered) != len(rows):
                        filter_summary.append((path.stem, len(filtered), len(rows)))
                    rows = filtered
                entries.append((path.stem, rows))

    # C.1: include configured variants with no CSV yet (0-bet rows)
    seen_names = {name for name, _ in entries}
    for s in STRATEGIES:
        if s.name not in seen_names:
            entries.append((s.name, []))

    if not entries:
        return "No data found. Run the scanner first.\n"

    drift_index = _load_drift_index()

    # Compute stats and sort: active variants by avg_clv desc, then no-CLV, then 0-bet
    results = []
    for name, rows in entries:
        s = _stats(rows, drift_index=drift_index)
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
        # R.11: eval-window filter transparency
        ("> **Eval-window filter:** showing CURRENT config window per variant only "
         "(rows whose `strategy_config_hash` matches the most recent scan). "
         "Pass `--all-history` to include older config windows / pre-R.11 rows."
         if not all_history else
         "> **Eval-window filter:** showing ALL HISTORY (`--all-history`). "
         "May mix rows generated under different strategy configs — interpret with care."),
        "",
    ]
    if filter_summary and not all_history:
        lines.append("> Variants with hidden older-window rows: " +
                     ", ".join(f"`{v}` ({c}/{t})" for v, c, t in filter_summary) +
                     " — format: `current/total`.")
        lines.append("")
    lines += [
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
        "| Strategy | Bets | CLV bets | Avg CLV ± 95% CI | Med CLV | CLV >0 % | Drift→you % | Avg Edge | Top books |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    drift_warnings: list[str] = []

    for name, s in results:
        marker = " ★" if name == best_name else ""
        # C.9: low-n marker
        low_n = s["n_with_clv"] < 10
        prefix = "[low n] " if low_n else ""

        # C.4: sanity-flag extreme drift values
        dp = s["drift_pct"]
        if dp is not None and s["n_bets"] >= 10 and dp in (0.0, 1.0):
            drift_warnings.append(
                f"⚠️ `{name}` drift={dp:.0%} with n={s['n_bets']} — likely sign error in drift direction."
            )

        if s["n_bets"] == 0:
            # C.1: 0-bet row
            lines.append(f"| {prefix}{name} | 0 | — | — | — | — | — | — | — |")
        else:
            clv_ci = _fmt_clv_ci(s["avg_clv"], s["ci95_half"])
            lines.append(
                f"| {prefix}{name}{marker} | {s['n_bets']} | {s['n_with_clv']} | "
                f"{clv_ci} | {_fmt(s['median_clv'])} | {_fmt(s['pos_clv'])} | "
                f"{_fmt(dp)} | {_fmt(s['avg_edge'])} | {s['book_dist'] or '—'} |"
            )

    if drift_warnings:
        lines += ["", "**Drift sanity warnings:**"] + [f"- {w}" for w in drift_warnings]

    # C.2: note about CI interpretation
    lines += [
        "",
        "*95% CI is `±1.96·σ/√n`. A variant whose CI bracket includes 0 has not yet"
        " shown a statistically distinguishable signal.*",
    ]

    # ---- C.3: Per-sport breakdown ------------------------------------------
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

    # ---- C.5: Per-confidence breakdown -------------------------------------
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

    # ---- C.7: Per-market breakdown -----------------------------------------
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

    # ---- C.8: Model-signal stratification ----------------------------------
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
    parser = argparse.ArgumentParser(description="Strategy comparison report (CLV-based).")
    parser.add_argument("--all-history", action="store_true",
                        help="Include rows from ALL config windows (default: filter to current "
                             "strategy_config_hash per variant — code-change pollution excluded).")
    args = parser.parse_args()
    report = build_report(all_history=args.all_history)
    print(report)
    OUT_DOC.parent.mkdir(exist_ok=True)
    OUT_DOC.write_text(report)
    print(f"[compare] Report written to {OUT_DOC.relative_to(_ROOT)}")


if __name__ == "__main__":
    main()
