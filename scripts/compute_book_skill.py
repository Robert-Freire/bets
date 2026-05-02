"""
Compute per-(book, league, market) skill and bias signals.

B.0.5 — consensus-divergence signals (flag_rate, mean_flag_edge,
         edge_vs_consensus, edge_vs_pinnacle, divergence) from scan-time
         blob archive.  Requires BLOB_ARCHIVE=1 + AZURE_BLOB_CONN.

B.0.6 — Brier-vs-outcome for the 5 FDCO-covered books (Pinnacle, Bet365,
         Bwin, BetVictor, Betfair Exchange) using FDCO closing odds.
         William Hill was dropped from FDCO format by the 2025/26 season;
         rows use whatever columns are present.  No network required.

Writes to book_skill table in Azure SQL (requires BETS_DB_WRITE=1).
Without BETS_DB_WRITE, computed rows are printed but not stored.
Without BLOB_ARCHIVE, B.0.5 is skipped cleanly — B.0.6 still runs.

Idempotent on window_end: existing rows for the same window are deleted
and replaced.

Usage:
    python3 scripts/compute_book_skill.py [--window-end YYYY-MM-DD]
                                          [--market h2h]
                                          [--leagues EPL Bundesliga ...]
                                          [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.betting.devig import shin
from src.config import load_leagues
from src.storage.repo import BetRepo
from src.storage.snapshots import (
    extract_events,
    get_archive,
    load_snapshot_envelope,
)

_WINDOW_WEEKS = 8

# Leagues where Pinnacle is the truth anchor for edge_vs_pinnacle
_PINNACLE_ANCHOR_LEAGUES = {"EPL", "Bundesliga", "Serie A", "Ligue 1"}
# For Championship + Bundesliga 2 use Bet365+Bwin sharp consensus
_SHARP_ANCHOR_BOOKS = ("bet365", "bwin")
_SHARP_ANCHOR_LABEL = "bet365+bwin"

# FDCO closing-odds column triplets (H, D, A) per bookmaker API key.
# Columns reflect the 2025/26 FDCO format.  WH dropped from current format.
_FDCO_BOOK_COLS: dict[str, tuple[str, str, str]] = {
    "pinnacle":      ("PSCH",   "PSCD",   "PSCA"),
    "bet365":        ("B365CH", "B365CD", "B365CA"),
    "bwin":          ("BWCH",   "BWCD",   "BWCA"),
    "betvictor":     ("BVCH",   "BVCD",   "BVCA"),
    "betfair_ex_uk": ("BFECH",  "BFECD",  "BFECA"),
}


# ---------------------------------------------------------------------------
# Date / season helpers
# ---------------------------------------------------------------------------

def _most_recent_sunday(ref: date | None = None) -> date:
    """Return the most recent Sunday on or before *ref* (default: today)."""
    d = ref or date.today()
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _fdco_season(d: date) -> str:
    """Return FDCO season code for the season containing *d*.

    FDCO seasons run Aug–Jul; e.g. 2025-08-01 → '2526'.
    """
    year = d.year if d.month >= 8 else d.year - 1
    return f"{str(year)[2:]}{str(year + 1)[2:]}"


def _blob_date_in_range(key: str, since: date, until: date) -> bool:
    """Parse yyyy/mm/dd from blob key path and check if it falls in [since, until]."""
    parts = key.split("/")
    # key format: source/endpoint/yyyy/mm/dd/...
    for i, p in enumerate(parts):
        if len(p) == 4 and p.isdigit() and i + 2 < len(parts):
            try:
                d = date(int(parts[i]), int(parts[i + 1]), int(parts[i + 2]))
                return since <= d <= until
            except (ValueError, IndexError):
                pass
    return False


def _parse_fdco_date(s: str) -> date | None:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Blob-based per-book observation accumulators
# ---------------------------------------------------------------------------

class _BookAccum:
    """Accumulates per-(book, outcome) scan-time observations for one league.

    Note on edge_vs_consensus / edge_vs_pinnacle semantics:
    Both are means over all (fixture × scan × outcome) observations.
    Because each book's Shin-devigged probs sum to 1.0, and the consensus
    is a mean of such prob vectors, the 3-outcome sum of (book − consensus)
    is exactly 0 per fixture per scan.  The aggregate mean is therefore
    always ~0.  This is correct behaviour — the diagnostic value lies in
    tracking the trend across window_end values, not the absolute level.
    See tests/test_book_skill.py:test_edge_vs_consensus_per_outcome_components
    for a per-component breakdown that confirms the intermediate math.
    """

    def __init__(self) -> None:
        self.edge_vs_consensus: dict[str, list[float]] = defaultdict(list)
        self.edge_vs_pinnacle: dict[str, list[float]] = defaultdict(list)
        self.fixture_ids: dict[str, set[tuple]] = defaultdict(set)

    def add_event(self, event: dict, truth_anchor: str) -> None:
        """Process one Odds API event (fixture) and record per-book observations."""
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        kickoff = event.get("commence_time", "")
        fixture_key = (home, away, kickoff)

        bookmakers = event.get("bookmakers", [])
        probs_by_book: dict[str, list[float]] = {}

        for bm in bookmakers:
            bk = bm.get("key", "").lower()
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                outcomes = market.get("outcomes", [])
                if len(outcomes) != 3:
                    continue
                try:
                    raw_probs = [1.0 / o["price"] for o in outcomes]
                    if any(p <= 0 for p in raw_probs):
                        continue
                    fair = shin(raw_probs)
                except Exception:
                    continue
                name_to_fair = {o["name"]: f for o, f in zip(outcomes, fair)}
                p_home = name_to_fair.get(event.get("home_team", ""))
                p_away = name_to_fair.get(event.get("away_team", ""))
                p_draw = name_to_fair.get("Draw")
                if None in (p_home, p_draw, p_away):
                    continue
                probs_by_book[bk] = [p_home, p_draw, p_away]
                break

        if len(probs_by_book) < 2:
            return

        n_books = len(probs_by_book)
        consensus = [
            sum(p[i] for p in probs_by_book.values()) / n_books
            for i in range(3)
        ]

        if truth_anchor == "pinnacle":
            anchor_probs = probs_by_book.get("pinnacle")
        else:
            anchors = [probs_by_book[b] for b in _SHARP_ANCHOR_BOOKS
                       if b in probs_by_book]
            if len(anchors) == 2:
                anchor_probs = [sum(a[i] for a in anchors) / 2 for i in range(3)]
            elif len(anchors) == 1:
                anchor_probs = anchors[0]
            else:
                anchor_probs = None

        for bk, bk_probs in probs_by_book.items():
            self.fixture_ids[bk].add(fixture_key)
            for i in range(3):
                self.edge_vs_consensus[bk].append(bk_probs[i] - consensus[i])
                if anchor_probs is not None:
                    self.edge_vs_pinnacle[bk].append(bk_probs[i] - anchor_probs[i])

    def aggregate(self) -> dict[str, dict]:
        """Return {book: {n_fixtures_blob, edge_vs_consensus, edge_vs_pinnacle, divergence}}."""
        result = {}
        for bk in self.fixture_ids:
            n = len(self.fixture_ids[bk])
            evc_list = self.edge_vs_consensus.get(bk, [])
            evp_list = self.edge_vs_pinnacle.get(bk, [])
            evc = sum(evc_list) / len(evc_list) if evc_list else None
            evp = sum(evp_list) / len(evp_list) if evp_list else None
            div = (evp - evc) if (evc is not None and evp is not None) else None
            result[bk] = {
                "n_fixtures_blob": n,
                "edge_vs_consensus": evc,
                "edge_vs_pinnacle": evp,
                "divergence": div,
            }
        return result


# ---------------------------------------------------------------------------
# B.0.5: flag signals — DB-first, CSV fallback
# ---------------------------------------------------------------------------

def _read_bets_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def _load_flag_signals(
    repo: BetRepo,
    logs_dir: Path,
    since: date,
    until: date,
) -> dict[tuple[str, str, str], dict]:
    """Return flag stats per (book, league, market). DB-first, CSV fallback."""
    raw_rows: list[dict] | None = repo.get_bets()  # returns None when DB disabled
    if raw_rows is None:
        raw_rows = _read_bets_csv(logs_dir / "bets.csv")

    stats: dict[tuple[str, str, str], dict] = defaultdict(
        lambda: {"n_flags": 0, "edge_sum": 0.0}
    )
    for row in raw_rows:
        ko = row.get("kickoff", "")
        try:
            ko_date = datetime.strptime(ko[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if not (since <= ko_date <= until):
            continue
        book = (row.get("book") or "").lower()
        league = row.get("sport") or ""
        market = row.get("market") or "h2h"
        if not book or not league:
            continue
        try:
            edge = float(row.get("edge") or 0)
        except (TypeError, ValueError):
            edge = 0.0
        key = (book, league, market)
        stats[key]["n_flags"] += 1
        stats[key]["edge_sum"] += edge
    return dict(stats)


# ---------------------------------------------------------------------------
# B.0.6: Brier-vs-outcome from FDCO
# ---------------------------------------------------------------------------

def _compute_fdco_brier(
    fdco_code: str, season: str, since: date, until: date,
) -> dict[str, dict]:
    """Return {book_key: {"n_fixtures": int, "brier_sum": float}} for one league."""
    path = _ROOT / "data" / "raw" / f"{fdco_code}_{season}.csv"
    if not path.exists():
        print(f"  [B.0.6] {path.name} not found — skipping.",
              file=sys.stderr)
        return {}

    stats: dict[str, dict] = {
        bk: {"n_fixtures": 0, "brier_sum": 0.0}
        for bk in _FDCO_BOOK_COLS
    }

    for enc in ("utf-8-sig", "latin1"):
        try:
            with open(path, newline="", encoding=enc) as f:
                rows = list(csv.DictReader(f))
            break
        except UnicodeDecodeError:
            rows = []

    ftr_map = {"H": 0, "D": 1, "A": 2}

    for row in rows:
        d = _parse_fdco_date(row.get("Date", ""))
        if d is None or not (since <= d <= until):
            continue
        ftr = row.get("FTR", "").strip()
        if ftr not in ftr_map:
            continue
        outcome_idx = ftr_map[ftr]
        actual = [0.0, 0.0, 0.0]
        actual[outcome_idx] = 1.0

        for bk, (ch, cd, ca) in _FDCO_BOOK_COLS.items():
            try:
                odds_h = float(row.get(ch, "") or 0)
                odds_d = float(row.get(cd, "") or 0)
                odds_a = float(row.get(ca, "") or 0)
            except (TypeError, ValueError):
                continue
            if odds_h <= 1.0 or odds_d <= 1.0 or odds_a <= 1.0:
                continue
            try:
                fair = shin([1.0 / odds_h, 1.0 / odds_d, 1.0 / odds_a])
            except Exception:
                continue
            brier = sum((fair[i] - actual[i]) ** 2 for i in range(3))
            stats[bk]["n_fixtures"] += 1
            stats[bk]["brier_sum"] += brier

    return stats


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------

def compute(
    window_end: date,
    market: str = "h2h",
    target_labels: list[str] | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """Compute book_skill rows for the 8-week window ending *window_end*.

    Returns the list of row dicts (written to DB unless dry_run=True).
    """
    window_start = window_end - timedelta(weeks=_WINDOW_WEEKS)
    window_end_str = window_end.isoformat()
    season = _fdco_season(window_end)
    print(f"[book_skill] window: {window_start} → {window_end}  "
          f"market={market}  season={season}")

    leagues = load_leagues()
    if target_labels:
        leagues = [lg for lg in leagues if lg["label"] in target_labels]

    archive = get_archive()
    archive_enabled = archive.enabled
    if not archive_enabled:
        print("[book_skill] BLOB_ARCHIVE not enabled — skipping B.0.5 (blob signals).")

    repo = BetRepo()
    flag_stats = _load_flag_signals(repo, _ROOT / "logs", window_start, window_end)
    rows: list[dict] = []

    for lg in leagues:
        sport_key = lg["key"]
        label = lg["label"]
        fdco_code = lg.get("fdco_code")
        truth_anchor = (
            "pinnacle" if label in _PINNACLE_ANCHOR_LEAGUES else _SHARP_ANCHOR_LABEL
        )

        print(f"\n[book_skill] League: {label} ({sport_key})")

        # ----- B.0.5 blob signals -----
        accum = _BookAccum()
        if archive_enabled:
            prefix = f"odds_api/v4_sports_{sport_key}_odds/"
            keys = archive.list_blob_keys(prefix=prefix)
            keys_in_range = [k for k in keys
                             if _blob_date_in_range(k, window_start, window_end)]
            print(f"  [B.0.5] {len(keys_in_range)} blobs in window "
                  f"(total under prefix: {len(keys)})")
            for key in keys_in_range:
                gz = archive.download_blob(key)
                if gz is None:
                    continue
                envelope = load_snapshot_envelope(gz)
                if envelope is None:
                    continue
                events = extract_events(envelope)
                for ev in events:
                    if market == "h2h":
                        accum.add_event(ev, truth_anchor)

        blob_agg = accum.aggregate()

        # ----- B.0.6 FDCO Brier -----
        fdco_brier: dict[str, dict] = {}
        if fdco_code:
            fdco_brier = _compute_fdco_brier(fdco_code, season,
                                             window_start, window_end)
            n_pin = fdco_brier.get("pinnacle", {}).get("n_fixtures", 0)
            print(f"  [B.0.6] FDCO {fdco_code}: "
                  f"{n_pin} pinnacle-covered fixtures in window")
        else:
            print(f"  [B.0.6] No FDCO code for {label} — skipping Brier.")

        # ----- Merge into rows -----
        all_books = set(blob_agg) | set(fdco_brier)
        for bk in all_books:
            blob = blob_agg.get(bk, {})
            fdco = fdco_brier.get(bk, {})

            n_fix_blob = blob.get("n_fixtures_blob", 0)
            n_fix_fdco = fdco.get("n_fixtures", 0)

            if n_fix_blob > 0:
                n_fixtures = n_fix_blob
                n_fixtures_source = "blob"
            elif n_fix_fdco > 0:
                n_fixtures = n_fix_fdco
                n_fixtures_source = "fdco"
            else:
                continue

            flag_key = (bk, label, market)
            fdata = flag_stats.get(flag_key, {})
            n_flags = fdata.get("n_flags", 0)
            edge_sum = fdata.get("edge_sum", 0.0)
            flag_rate = n_flags / n_fixtures if n_fixtures > 0 else None
            mean_flag_edge = edge_sum / n_flags if n_flags > 0 else None

            brier_n = fdco.get("n_fixtures", 0)
            brier_sum = fdco.get("brier_sum", 0.0)
            brier_outcome = brier_sum / brier_n if brier_n > 0 else None

            row = {
                "book": bk,
                "league": label,
                "market": market,
                "window_end": window_end_str,
                "n_fixtures": n_fixtures,
                "brier_vs_close": None,
                "brier_vs_outcome": brier_outcome,
                "log_loss": None,
                "fav_longshot_slope": None,
                "home_bias": None,
                "draw_bias": None,
                "flag_rate": flag_rate,
                "mean_flag_edge": mean_flag_edge,
                "edge_vs_consensus": blob.get("edge_vs_consensus"),
                "edge_vs_pinnacle": blob.get("edge_vs_pinnacle"),
                "divergence": blob.get("divergence"),
                "truth_anchor": truth_anchor,
                "n_fixtures_source": n_fixtures_source,
            }
            rows.append(row)

    print(f"\n[book_skill] {len(rows)} rows computed.")

    if dry_run:
        for r in rows[:5]:
            print(f"  {r['book']:20s}  {r['league']:12s}  "
                  f"n={r['n_fixtures']} ({r['n_fixtures_source']})  "
                  f"flag_rate={r.get('flag_rate')}  "
                  f"brier={r.get('brier_vs_outcome')}")
        return rows

    if repo.db_enabled:
        repo.write_book_skill(rows)
        repo.close()
        print(f"[book_skill] Written {len(rows)} rows to book_skill table.")
    else:
        print("[book_skill] BETS_DB_WRITE not enabled — rows not persisted.")

    return rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--window-end",
        metavar="YYYY-MM-DD",
        help="Window end date (default: most recent Sunday)",
    )
    p.add_argument(
        "--market",
        default="h2h",
        help="Market to analyse (default: h2h)",
    )
    p.add_argument(
        "--leagues",
        nargs="+",
        metavar="LABEL",
        help="Limit to specific league labels, e.g. EPL Bundesliga",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute but do not write to DB; print a sample.",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.window_end:
        we = date.fromisoformat(args.window_end)
    else:
        we = _most_recent_sunday()

    compute(
        window_end=we,
        market=args.market,
        target_labels=args.leagues,
        dry_run=args.dry_run,
    )
