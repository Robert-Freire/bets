"""
Strategy comparison report.

Reads logs/paper/*.csv and logs/bets.csv. Prints a Markdown table to stdout
and writes docs/STRATEGY_COMPARISON.md.

Usage:
    python3 scripts/compare_strategies.py
"""

import csv
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

BETS_CSV  = _ROOT / "logs" / "bets.csv"
PAPER_DIR = _ROOT / "logs" / "paper"
OUT_DOC   = _ROOT / "docs" / "STRATEGY_COMPARISON.md"


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

    edge_values = []
    for r in rows:
        try:
            edge_values.append(float(r.get("edge", r.get("consensus", 0)) or 0))
        except (ValueError, TypeError):
            pass
    avg_edge = sum(edge_values) / len(edge_values) if edge_values else None

    book_counter: Counter = Counter()
    for r in rows:
        b = r.get("book", "")
        if b:
            book_counter[b] += 1
    top_books = ", ".join(f"{b}({c})" for b, c in book_counter.most_common(3))

    return {
        "n_bets": n_bets,
        "n_with_clv": n_with_clv,
        "avg_clv": avg_clv,
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


def build_report() -> str:
    entries = []

    # Production bets.csv — strategy name "production"
    prod_rows = _read_csv(BETS_CSV)
    if prod_rows:
        entries.append(("production", prod_rows))

    # Paper strategy CSVs
    if PAPER_DIR.exists():
        for path in sorted(PAPER_DIR.glob("*.csv")):
            rows = _read_csv(path)
            if rows:
                entries.append((path.stem, rows))

    if not entries:
        return "No data found. Run the scanner first.\n"

    # Compute stats and sort by avg_clv descending (None last)
    results = []
    for name, rows in entries:
        s = _stats(rows)
        results.append((name, s))
    results.sort(key=lambda x: (x[1]["avg_clv"] is None, -(x[1]["avg_clv"] or 0)))

    best_name = results[0][0] if results else ""

    lines = [
        "# Strategy Comparison",
        "",
        "Sorted by average CLV descending. Only rows with a Pinnacle close prob contribute to CLV stats.",
        "Run `python3 scripts/compare_strategies.py` to refresh.",
        "",
        "| Strategy | Bets | CLV bets | Avg CLV | CLV >0 % | Avg Edge | Top books |",
        "|---|---|---|---|---|---|---|",
    ]

    for name, s in results:
        marker = " ★" if name == best_name and s["avg_clv"] is not None else ""
        lines.append(
            f"| {name}{marker} | {s['n_bets']} | {s['n_with_clv']} | "
            f"{_fmt(s['avg_clv'])} | {_fmt(s['pos_clv'])} | "
            f"{_fmt(s['avg_edge'])} | {s['book_dist'] or '—'} |"
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
