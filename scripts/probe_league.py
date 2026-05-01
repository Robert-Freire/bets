"""
One-shot league coverage probe. Fetches h2h odds for a single league and
prints fixture-level stats plus a summary row. Read-only: no CSV writes,
no ntfy, no DB, no blob archive.

Usage:
    export $(cat .env.dev) && python3 scripts/probe_league.py --sport soccer_spain_la_liga
"""

import argparse
import os
import statistics
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("ODDS_API_KEY", os.environ.get("ODDS_API_KEY", ""))

import scripts.scan_odds as scan_odds  # noqa: E402

KAUNITZ_EDGE = 0.03  # minimum edge to count as a value-bet hit


def probe(sport_key: str) -> dict:
    events, remaining = scan_odds.fetch_odds(sport_key)
    if not events:
        print(f"[{sport_key}] 0 fixtures returned (off-season or bad key).")
        return {}

    per_fixture = []
    for ev in events:
        home, away = ev["home_team"], ev["away_team"]

        h2h_impl: dict[str, list] = {}
        h2h_books: dict[str, dict] = {}
        for b in ev.get("bookmakers", []):
            for m in b.get("markets", []):
                if m["key"] != "h2h":
                    continue
                oc = {o["name"]: o["price"] for o in m["outcomes"]}
                entries: dict[str, float] = {"H": oc.get(home), "A": oc.get(away)}
                draw = oc.get("Draw")
                if draw:
                    entries["D"] = draw
                if not all(v and v > 1.0 for v in entries.values()):
                    continue
                fair = scan_odds._devig_book(entries)
                for side, fp in fair.items():
                    h2h_impl.setdefault(side, []).append(fp)
                h2h_books[b["key"]] = fair

        n_books = len(h2h_books)
        if n_books < 2:
            per_fixture.append({
                "home": home, "away": away,
                "n_books": n_books, "max_disp": None, "n_hits": 0,
            })
            continue

        cons = {s: statistics.mean(v) for s, v in h2h_impl.items()}
        disp_per_side = {
            s: (statistics.stdev(v) if len(v) >= 2 else 0.0)
            for s, v in h2h_impl.items()
        }
        max_disp = max(disp_per_side.values()) if disp_per_side else 0.0

        n_hits = 0
        for book_key, fair in h2h_books.items():
            if book_key not in scan_odds.UK_LICENSED_BOOKS:
                continue
            for side, fp in fair.items():
                edge = cons.get(side, 0.0) - fp
                if edge >= KAUNITZ_EDGE and max_disp <= scan_odds.MAX_DISPERSION:
                    n_hits += 1

        per_fixture.append({
            "home": home, "away": away,
            "n_books": n_books, "max_disp": max_disp, "n_hits": n_hits,
        })
        print(
            f"  {home} v {away}: books={n_books:2d}  max_disp={max_disp:.4f}  hits={n_hits}"
        )

    # summary stats
    valid = [f for f in per_fixture if f["max_disp"] is not None]
    n_fixtures = len(per_fixture)
    avg_books = statistics.mean(f["n_books"] for f in per_fixture) if per_fixture else 0.0
    p95_disp = (
        sorted(f["max_disp"] for f in valid)[int(len(valid) * 0.95)]
        if valid else 0.0
    )
    n_value_hits = sum(f["n_hits"] for f in per_fixture)

    print(
        f"\n[{sport_key}] SUMMARY  fixtures={n_fixtures}  avg_books={avg_books:.1f}"
        f"  p95_disp={p95_disp:.4f}  n_3pct_hits={n_value_hits}"
        f"  api_remaining={remaining}"
    )
    return {
        "sport_key": sport_key,
        "n_fixtures": n_fixtures,
        "avg_books": round(avg_books, 1),
        "p95_dispersion": round(p95_disp, 4),
        "n_3pct_hits": n_value_hits,
    }


def main():
    parser = argparse.ArgumentParser(description="League coverage probe (read-only).")
    parser.add_argument("--sport", required=True, help="Odds API sport_key")
    args = parser.parse_args()

    if not os.environ.get("ODDS_API_KEY"):
        sys.exit("ODDS_API_KEY not set.")

    probe(args.sport)


if __name__ == "__main__":
    main()
