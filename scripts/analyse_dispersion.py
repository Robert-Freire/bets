"""
Dispersion shape analysis — reads per-book Shin-fair probabilities from a single
Odds API h2h snapshot and classifies the disagreement structure per fixture×outcome.

Read-only: no CSV / DB / ntfy / blob writes.

PREFERS reading from an existing snapshot (Azure Blob archive, or any local
gzipped JSON envelope produced by `SnapshotArchive`). Avoids burning fresh
API credits when the data is already on disk.

Usage:
    # Re-analyse a previously-archived snapshot (preferred — no API cost)
    python3 scripts/analyse_dispersion.py --blob /tmp/la_liga.json.gz

    # Fall back to a fresh fetch only when no snapshot is available
    export $(cat .env.dev) && python3 scripts/analyse_dispersion.py --sport soccer_spain_la_liga --fetch
"""

import argparse
import gzip
import json
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.betting.devig import shin

MIN_BOOKS_PER_OUTCOME = 10
MAD_MULT = 1.5  # books > median ± MAD_MULT × MAD are tail-cluster members
BIMODAL_THRESHOLD_PCT = 30  # ≥30% bimodal rows → sharp-weighting hypothesis testable
DIRECTIONAL_BIAS_THRESHOLD = 5  # |low - high| ≥ 5 across rows = structural, not noise


def _median(xs): return statistics.median(xs)
def _mad(xs):
    m = _median(xs)
    return _median([abs(x - m) for x in xs])


def load_events_from_blob(path: Path) -> tuple[list, str]:
    """Read a SnapshotArchive envelope (gzipped JSON) and return (events, captured_at)."""
    with open(path, "rb") as f:
        envelope = json.loads(gzip.decompress(f.read()))
    body_raw = envelope.get("body_raw")
    if isinstance(body_raw, str):
        events = json.loads(body_raw)
    else:
        events = body_raw if isinstance(body_raw, list) else envelope.get("body", [])
    return events, envelope.get("captured_at", "?")


def load_events_from_api(sport_key: str) -> tuple[list, str]:
    """Fall-back path. Costs 2 credits (regions=uk,eu × markets=h2h)."""
    import scripts.scan_odds as scan_odds
    events, remaining = scan_odds.fetch_odds(sport_key)
    return events, f"fresh fetch (api_remaining={remaining})"


def extract_per_outcome(events: list) -> list[tuple[str, str, dict[str, float]]]:
    """For each fixture × outcome with ≥MIN_BOOKS, returns (label, outcome, {book: fair_prob})."""
    rows: list[tuple[str, str, dict[str, float]]] = []
    for ev in events:
        home, away = ev["home_team"], ev["away_team"]
        label = f"{home} v {away}"
        by_outcome: dict[str, dict[str, float]] = {"H": {}, "D": {}, "A": {}}
        for b in ev.get("bookmakers", []):
            for m in b.get("markets", []):
                if m["key"] != "h2h":
                    continue
                oc = {o["name"]: o["price"] for o in m["outcomes"]}
                entries = [oc.get(home), oc.get("Draw"), oc.get(away)]
                if not all(v and v > 1.0 for v in entries):
                    continue
                fair = shin([1 / x for x in entries])
                by_outcome["H"][b["key"]] = fair[0]
                by_outcome["D"][b["key"]] = fair[1]
                by_outcome["A"][b["key"]] = fair[2]
        for outcome, bp in by_outcome.items():
            if len(bp) >= MIN_BOOKS_PER_OUTCOME:
                rows.append((label, outcome, bp))
    return rows


def classify_row(book_probs: dict[str, float]) -> tuple[str, dict[str, str]]:
    """Returns (verdict, {book: 'low'|'centre'|'high'})."""
    vals = list(book_probs.values())
    m = _median(vals)
    md = _mad(vals)
    if md < 1e-6:
        return "unimodal-tight", {b: "centre" for b in book_probs}
    threshold = MAD_MULT * md
    classifications: dict[str, str] = {}
    lows = highs = 0
    for b, v in book_probs.items():
        if v < m - threshold:
            classifications[b] = "low"; lows += 1
        elif v > m + threshold:
            classifications[b] = "high"; highs += 1
        else:
            classifications[b] = "centre"
    if lows >= 2 and highs >= 2:
        verdict = "bimodal"
    elif lows >= 2 or highs >= 2:
        verdict = "unimodal-fat"
    else:
        verdict = "unimodal-tight"
    return verdict, classifications


def analyse(events: list, source_desc: str) -> dict:
    rows = extract_per_outcome(events)
    print(f"Loaded {len(events)} fixtures from {source_desc}")
    print(f"Analyzable fixture×outcome rows (≥{MIN_BOOKS_PER_OUTCOME} books): {len(rows)}\n")

    shape_counts: dict[str, int] = defaultdict(int)
    book_low: dict[str, int] = defaultdict(int)
    book_high: dict[str, int] = defaultdict(int)
    book_centre: dict[str, int] = defaultdict(int)
    book_seen: dict[str, int] = defaultdict(int)
    book_low_by_outcome: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    book_high_by_outcome: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    bimodal_examples: list = []

    for label, outcome, bp in rows:
        verdict, classifications = classify_row(bp)
        shape_counts[verdict] += 1
        for b, c in classifications.items():
            book_seen[b] += 1
            if c == "low":
                book_low[b] += 1
                book_low_by_outcome[b][outcome] += 1
            elif c == "high":
                book_high[b] += 1
                book_high_by_outcome[b][outcome] += 1
            else:
                book_centre[b] += 1
        if verdict == "bimodal" and len(bimodal_examples) < 2:
            m = _median(list(bp.values())); md = _mad(list(bp.values()))
            bimodal_examples.append((label, outcome, m, MAD_MULT * md, sorted(bp.items(), key=lambda x: x[1])))

    total = sum(shape_counts.values())
    bimodal_pct = shape_counts.get("bimodal", 0) * 100 / max(total, 1)

    # ── Output: shape distribution
    print("=" * 72)
    print("SHAPE DISTRIBUTION")
    print("=" * 72)
    for shape in ("unimodal-tight", "unimodal-fat", "bimodal"):
        n = shape_counts.get(shape, 0)
        bar = "█" * int(n * 40 / max(total, 1))
        print(f"  {shape:18s}: {n:3d} ({n*100/max(total,1):5.1f}%)  {bar}")
    print(f"  total: {total}")
    verdict_str = "sharp-weighting hypothesis is testable" if bimodal_pct >= BIMODAL_THRESHOLD_PCT else "mostly unimodal — flat consensus may be fine"
    print(f"\nBimodal share: {bimodal_pct:.1f}%  →  {verdict_str}\n")

    # ── Output: per-book bias
    print("=" * 72)
    print("PER-BOOK CLUSTER MEMBERSHIP (books with ≥10 appearances)")
    print("=" * 72)
    print(f"{'book':24s} {'seen':>4s} {'low':>4s} {'cen':>4s} {'high':>4s} {'low%':>5s} {'high%':>5s} {'bias':>5s}")
    for b in sorted(book_seen, key=lambda x: -(book_low[x] - book_high[x])):
        n = book_seen[b]
        if n < 10: continue
        lo, hi, ce = book_low[b], book_high[b], book_centre[b]
        bias = lo - hi
        flag = ""
        if abs(bias) >= DIRECTIONAL_BIAS_THRESHOLD:
            flag = "  ← LOW-cluster regular" if bias > 0 else "  ← HIGH-cluster regular"
        elif (ce / n) >= 0.85:
            flag = "  ← centre-dominant (sharp signature)"
        print(f"  {b:24s} {n:>4d} {lo:>4d} {ce:>4d} {hi:>4d} {lo*100/n:>4.0f}% {hi*100/n:>4.0f}% {bias:>+5d}{flag}")
    print()

    # ── Output: outcome-specific signature
    print("=" * 72)
    print("OUTCOME-SPECIFIC BIAS (low/high cluster counts per H/D/A)")
    print("=" * 72)
    print("Books with strong asymmetry across outcomes have a structural pricing model,")
    print("not just noise. Watch for books that are systematically low-on-favourites and")
    print("high-on-underdogs (sharp signature) vs the reverse (soft signature).\n")
    print(f"{'book':24s} {'H low/high':>12s} {'D low/high':>12s} {'A low/high':>12s}")
    for b in sorted(book_seen, key=lambda x: -book_seen[x]):
        if book_seen[b] < 30: continue
        cells = []
        for outcome in ("H", "D", "A"):
            lo = book_low_by_outcome[b].get(outcome, 0)
            hi = book_high_by_outcome[b].get(outcome, 0)
            cells.append(f"{lo:>3d}/{hi:<3d}")
        print(f"  {b:24s} {cells[0]:>12s} {cells[1]:>12s} {cells[2]:>12s}")
    print()

    # ── Output: bimodal examples
    if bimodal_examples:
        print("=" * 72)
        print(f"BIMODAL EXAMPLES — book-by-book per fixture×outcome ({len(bimodal_examples)} shown)")
        print("=" * 72)
        for label, outcome, median, threshold, sorted_books in bimodal_examples:
            print(f"\n{label}  outcome={outcome}  median={median:.4f}  ±{threshold:.4f}")
            print(f"  {'book':24s} {'fair_p':>8s}  {'cluster':>10s}")
            for b, v in sorted_books:
                if v < median - threshold: c = "low"
                elif v > median + threshold: c = "high"
                else: c = "centre"
                print(f"  {b:24s} {v:>8.4f}  {c:>10s}")

    return {
        "n_fixtures": len(events),
        "n_rows": total,
        "shape_counts": dict(shape_counts),
        "bimodal_pct": bimodal_pct,
        "book_bias": {b: book_low[b] - book_high[b] for b in book_seen if book_seen[b] >= 10},
    }


def main():
    parser = argparse.ArgumentParser(description="Dispersion shape analysis (read-only).")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--blob", type=Path, help="Path to a gzipped SnapshotArchive envelope (preferred — no API cost).")
    src.add_argument("--sport", help="Odds API sport_key. Use with --fetch to spend 2cr on a fresh fetch.")
    parser.add_argument("--fetch", action="store_true", help="Allow fresh API fetch (only with --sport).")
    args = parser.parse_args()

    if args.blob:
        if not args.blob.exists():
            sys.exit(f"Blob not found: {args.blob}")
        events, source_desc = load_events_from_blob(args.blob)
    else:
        if not args.fetch:
            sys.exit("--sport requires --fetch to confirm you want to spend API credits. "
                     "Prefer --blob with a previously-archived snapshot.")
        if not os.environ.get("ODDS_API_KEY"):
            sys.exit("ODDS_API_KEY not set.")
        events, source_desc = load_events_from_api(args.sport)

    analyse(events, source_desc)


if __name__ == "__main__":
    main()
