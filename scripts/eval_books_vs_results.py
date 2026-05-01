"""
Evaluate book sharpness against actual match results — gold-standard sharpness metric.

For each (book, league) pair: devig the book's pre-match h2h odds with Shin,
compare the resulting fair-probabilities against realized FTR outcomes, and
compute mean Brier score. Lower Brier = sharper book.

Read-only. Uses football-data.co.uk CSVs in `data/raw/<CODE>_<SEASON>.csv` —
no API calls, no fetches. Per memory `feedback_reuse_archived_data.md`.

Limitation: FDCO covers ~7 books (Bet365, Bwin, Interwetten, Pinnacle,
William Hill, BetVictor, plus closing-line aggregates). Niche books in the
Odds API (Marathonbet, Smarkets, Matchbook, Codere, Unibet variants) are NOT
present here and need a different evaluation pipeline (post-CLV via
backfill_clv_from_fdco). This script validates major-book ranking only.

Usage:
    python3 scripts/eval_books_vs_results.py                  # all leagues, current season
    python3 scripts/eval_books_vs_results.py --season 2425    # 2024-25 season
    python3 scripts/eval_books_vs_results.py --leagues E0 D1  # subset
"""

import argparse
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.betting.devig import shin

# FDCO column prefix → Odds API book key (best-effort mapping)
BOOK_CODES = {
    "B365": "bet365",
    "BW": "bwin",
    "IW": "interwetten",
    "PS": "pinnacle",
    "WH": "williamhill",
    "VC": "betvictor",
    "1XB": "1xbet",
    "BF": "betfair_sb",
    "BFE": "betfair_ex",
    "PSC": "pinnacle_close",  # closing line — gold-standard reference
    "MaxC": "max_close",
    "AvgC": "avg_close",
}

LEAGUES = {
    "E0": "EPL", "D1": "Bundesliga", "I1": "Serie A", "E1": "Championship",
    "F1": "Ligue 1", "D2": "Bundesliga 2", "SP1": "La Liga", "SP2": "La Liga 2",
    "N1": "Eredivisie", "P1": "Primeira", "F2": "Ligue 2",
}


def brier(probs: list[float], outcome_idx: int) -> float:
    """Multi-class Brier. probs=[H,D,A]. outcome_idx ∈ {0,1,2}."""
    targets = [0.0, 0.0, 0.0]; targets[outcome_idx] = 1.0
    return sum((p - t) ** 2 for p, t in zip(probs, targets))


def evaluate_csv(path: Path) -> dict[str, list[float]]:
    """Returns {book_key: [brier scores per match]}."""
    with open(path, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    out: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        ftr = r.get("FTR")
        if ftr not in ("H", "D", "A"): continue
        outcome = {"H": 0, "D": 1, "A": 2}[ftr]
        for prefix, book in BOOK_CODES.items():
            try:
                h = float(r.get(f"{prefix}H", ""))
                d = float(r.get(f"{prefix}D", ""))
                a = float(r.get(f"{prefix}A", ""))
            except (ValueError, TypeError):
                continue
            if not (h > 1 and d > 1 and a > 1): continue
            try:
                fair = shin([1 / h, 1 / d, 1 / a])
            except Exception:
                continue
            out[book].append(brier(fair, outcome))
    return out


def main():
    parser = argparse.ArgumentParser(description="Book sharpness via Brier vs realized outcomes (read-only).")
    parser.add_argument("--season", default="2526", help="FDCO season code (e.g. 2526 for 2025-26).")
    parser.add_argument("--leagues", nargs="+", default=list(LEAGUES.keys()),
                        help="FDCO league codes to evaluate.")
    parser.add_argument("--min-matches", type=int, default=30,
                        help="Drop books with fewer than this many matches.")
    args = parser.parse_args()

    results: dict[str, dict[str, list[float]]] = {}
    for code in args.leagues:
        if code not in LEAGUES:
            print(f"[skip] unknown league code: {code}")
            continue
        path = _ROOT / "data" / "raw" / f"{code}_{args.season}.csv"
        if not path.exists():
            print(f"[skip] no CSV at {path}")
            continue
        league_results = evaluate_csv(path)
        kept = {b: s for b, s in league_results.items() if len(s) >= args.min_matches}
        if kept:
            results[LEAGUES[code]] = kept

    if not results:
        sys.exit("No data evaluated.")

    print(f"\n{'League':14s} {'n':>4s}  Books ranked by Brier (lower=sharper) — top 5")
    print("-" * 100)
    for label, books in results.items():
        n = len(next(iter(books.values())))
        ranked = sorted(books.items(), key=lambda x: statistics.mean(x[1]))
        cells = ", ".join(f"{b}({statistics.mean(s):.4f})" for b, s in ranked[:5])
        print(f"{label:14s} {n:>4d}  {cells}")

    # Cross-league: which books rank top-3 most often?
    print("\n" + "=" * 100)
    print("CROSS-LEAGUE: top-3 finishes per book")
    print("=" * 100)
    top_appearances: dict[str, list] = defaultdict(list)
    for label, books in results.items():
        ranked = sorted(books.items(), key=lambda x: statistics.mean(x[1]))
        for rank, (b, s) in enumerate(ranked[:3], 1):
            top_appearances[b].append((label, rank, statistics.mean(s)))

    for b in sorted(top_appearances, key=lambda x: -len(top_appearances[x])):
        appearances = top_appearances[b]
        leagues_str = ", ".join(f"{lg}#{r}" for lg, r, _ in appearances)
        avg_brier = statistics.mean(score for _, _, score in appearances)
        print(f"  {b:20s}  {len(appearances):>2d} top-3 finishes  (avg Brier {avg_brier:.4f})  {leagues_str}")

    print("\nCaveats:")
    print(f"  - FDCO covers ~7 books (B365, BW, IW, PS, WH, VC + closes). Marathonbet,")
    print(f"    Smarkets, Matchbook, Codere, Unibet variants etc. are NOT in this dataset.")
    print(f"  - Brier on 3-class h2h: uniform forecast = 0.667; perfect = 0; typical sharp = 0.55–0.60.")
    print(f"  - Lower = sharper. Differences of 0.02+ are meaningful given n≈300/league.")


if __name__ == "__main__":
    main()
