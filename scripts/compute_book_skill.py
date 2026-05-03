"""
Compute per-(book, league, market) skill and bias signals.

B.0.5 — consensus-divergence signals from scan-time blob archive.
         Requires BLOB_ARCHIVE=1 + AZURE_BLOB_CONN.

B.0.6 — Brier-vs-outcome + log-loss for the 5 FDCO-covered books using
         FDCO closing odds.  No network required.

B.0.7 — Methodology hardening: LOO consensus (replaces self-contaminated
         full consensus), paired Brier vs Pinnacle close, bootstrap CIs,
         dual de-vig (shin + multiplicative).  Two rows emitted per
         (book, league, market, window_end) — one per devig_method.
         Each blob and each FDCO CSV is read exactly once per league;
         events/rows are passed to both devig accumulators in the same
         iteration.

Writes to book_skill table in Azure SQL (requires BETS_DB_WRITE=1).
Without BETS_DB_WRITE, computed rows are printed but not stored.
Without BLOB_ARCHIVE, B.0.5 is skipped cleanly — B.0.6 still runs.

Idempotent on (window_end, devig_method): existing rows for the same key
are deleted and replaced.

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
import math
import os
import random
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.betting.devig import proportional, shin
from src.config import load_leagues
from src.storage.repo import BetRepo
from src.storage.snapshots import (
    extract_events,
    get_archive,
    load_snapshot_envelope,
)

_WINDOW_WEEKS = 8
_BOOTSTRAP_RESAMPLES = 1000
_SHRINKAGE_N0 = 50  # empirical-Bayes prior strength; w = n/(n+N0) for n<200

# Ordered dict: insertion order is the iteration order; 'shin' first so log
# lines print naturally, but correctness does NOT depend on order.
_DEVIG_FNS: dict[str, Callable] = {
    "shin":           shin,
    "multiplicative": proportional,
}

_PINNACLE_ANCHOR_LEAGUES = {"EPL", "Bundesliga", "Serie A", "Ligue 1"}
_SHARP_ANCHOR_BOOKS = ("bet365", "bwin")
_SHARP_ANCHOR_LABEL = "bet365+bwin"

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
    d = ref or date.today()
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _fdco_season(d: date) -> str:
    """Return FDCO season code for the season containing *d* (Aug–Jul boundary)."""
    year = d.year if d.month >= 8 else d.year - 1
    return f"{str(year)[2:]}{str(year + 1)[2:]}"


def _blob_date_in_range(key: str, since: date, until: date) -> bool:
    parts = key.split("/")
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
# Bootstrap CI
# ---------------------------------------------------------------------------

def _bootstrap_ci(
    values: list[float],
    n_resamples: int = _BOOTSTRAP_RESAMPLES,
    seed: int | None = None,
) -> tuple[float | None, float | None]:
    """Fixture-level bootstrap CI (2.5 / 97.5 percentile of resample means).

    Pass *seed* (e.g. derived from window_end) for reproducible CIs within
    a given run.  Returns (None, None) if fewer than 2 observations.
    """
    n = len(values)
    if n < 2:
        return None, None
    try:
        import numpy as np
        rng = np.random.default_rng(seed)
        arr = np.array(values, dtype=float)
        idx = rng.integers(0, n, size=(n_resamples, n))
        means = arr[idx].mean(axis=1)
        return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
    except ImportError:
        if seed is not None:
            random.seed(seed)
        means = sorted(
            sum(random.choices(values, k=n)) / n
            for _ in range(n_resamples)
        )
        lo = means[max(0, int(0.025 * n_resamples))]
        hi = means[min(n_resamples - 1, int(0.975 * n_resamples))]
        return lo, hi


# ---------------------------------------------------------------------------
# B.0.5: blob-based per-book observation accumulator
# ---------------------------------------------------------------------------

class _BookAccum:
    """Accumulates scan-time per-book observations for one league + devig method.

    LOO consensus: each book is excluded from its own benchmark mean before
    differencing (Leave-One-Out), removing the self-contamination bias present
    in the full consensus.  The 3-outcome aggregate mean is still ~0 by
    mathematical identity (all fair-prob vectors sum to 1), but the
    per-outcome components are unbiased and the trend across window_end
    values is diagnostic.
    """

    def __init__(self, devig_fn: Callable = shin) -> None:
        self._devig_fn = devig_fn
        self.edge_vs_consensus_loo: dict[str, list[float]] = defaultdict(list)
        self.edge_vs_pinnacle: dict[str, list[float]] = defaultdict(list)
        self.fixture_ids: dict[str, set[tuple]] = defaultdict(set)

    def add_event(self, event: dict, truth_anchor: str) -> None:
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        kickoff = event.get("commence_time", "")
        fixture_key = (home, away, kickoff)

        probs_by_book: dict[str, list[float]] = {}
        for bm in event.get("bookmakers", []):
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
                    fair = self._devig_fn(raw_probs)
                except Exception:
                    continue
                name_to_fair = {o["name"]: f for o, f in zip(outcomes, fair)}
                p_home = name_to_fair.get(home)
                p_away = name_to_fair.get(away)
                p_draw = name_to_fair.get("Draw")
                if None in (p_home, p_draw, p_away):
                    continue
                probs_by_book[bk] = [p_home, p_draw, p_away]
                break

        if len(probs_by_book) < 2:
            return

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
            other = [p for k, p in probs_by_book.items() if k != bk]
            if other:
                n_other = len(other)
                loo = [sum(p[i] for p in other) / n_other for i in range(3)]
                for i in range(3):
                    self.edge_vs_consensus_loo[bk].append(bk_probs[i] - loo[i])
            if anchor_probs is not None:
                for i in range(3):
                    self.edge_vs_pinnacle[bk].append(bk_probs[i] - anchor_probs[i])

    def aggregate(self) -> dict[str, dict]:
        # Global means for empirical-Bayes shrinkage of home/draw bias
        # loo_list interleaves [home_diff, draw_diff, away_diff] per fixture (3 per fixture)
        all_home = [v for lst in self.edge_vs_consensus_loo.values() for v in lst[0::3]]
        all_draw = [v for lst in self.edge_vs_consensus_loo.values() for v in lst[1::3]]
        global_home = sum(all_home) / len(all_home) if all_home else 0.0
        global_draw = sum(all_draw) / len(all_draw) if all_draw else 0.0

        result = {}
        for bk in self.fixture_ids:
            n = len(self.fixture_ids[bk])
            loo_list = self.edge_vs_consensus_loo.get(bk, [])
            evp_list = self.edge_vs_pinnacle.get(bk, [])
            loo = sum(loo_list) / len(loo_list) if loo_list else None
            evp = sum(evp_list) / len(evp_list) if evp_list else None
            div = (evp - loo) if (evp is not None and loo is not None) else None

            home_vals = loo_list[0::3]
            draw_vals = loo_list[1::3]
            raw_home = sum(home_vals) / len(home_vals) if home_vals else None
            raw_draw = sum(draw_vals) / len(draw_vals) if draw_vals else None
            w = n / (n + _SHRINKAGE_N0) if n < 200 else 1.0
            home_bias = raw_home * w + global_home * (1 - w) if raw_home is not None else None
            draw_bias = raw_draw * w + global_draw * (1 - w) if raw_draw is not None else None

            result[bk] = {
                "n_fixtures_blob": n,
                "edge_vs_consensus_loo": loo,
                "edge_vs_pinnacle": evp,
                "divergence": div,
                "home_bias_blob": home_bias,
                "draw_bias_blob": draw_bias,
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
    raw_rows: list[dict] | None = repo.get_bets()
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
# B.0.6 / B.0.7: FDCO CSV loader + per-devig Brier/paired/log-loss computer
# ---------------------------------------------------------------------------

def _load_fdco_rows(fdco_code: str, season: str) -> list[dict]:
    """Load FDCO CSV rows once. Returns [] if file missing."""
    path = _ROOT / "data" / "raw" / f"{fdco_code}_{season}.csv"
    if not path.exists():
        print(f"  [B.0.6] {path.name} not found — skipping Brier.",
              file=sys.stderr)
        return []
    for enc in ("utf-8-sig", "latin1"):
        try:
            with open(path, newline="", encoding=enc) as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            pass
    return []


def _brier_from_rows(
    rows: list[dict],
    since: date,
    until: date,
    devig_fn: Callable,
) -> dict[str, dict]:
    """Compute per-book Brier / paired-Brier / log-loss from pre-loaded rows."""
    brier_vals: dict[str, list[float]] = defaultdict(list)
    paired_deltas: dict[str, list[float]] = defaultdict(list)
    log_loss_vals: dict[str, list[float]] = defaultdict(list)
    n_fixtures: dict[str, int] = defaultdict(int)

    ftr_map = {"H": 0, "D": 1, "A": 2}
    pin_cols = _FDCO_BOOK_COLS["pinnacle"]

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

        brier_pin: float | None = None
        try:
            ph = float(row.get(pin_cols[0], "") or 0)
            pd_ = float(row.get(pin_cols[1], "") or 0)
            pa = float(row.get(pin_cols[2], "") or 0)
            if min(ph, pd_, pa) > 1.0:
                pin_fair = devig_fn([1.0 / ph, 1.0 / pd_, 1.0 / pa])
                brier_pin = sum((pin_fair[i] - actual[i]) ** 2 for i in range(3))
        except (TypeError, ValueError, ZeroDivisionError):
            pass

        for bk, (ch, cd, ca) in _FDCO_BOOK_COLS.items():
            try:
                odds_h = float(row.get(ch, "") or 0)
                odds_d = float(row.get(cd, "") or 0)
                odds_a = float(row.get(ca, "") or 0)
            except (TypeError, ValueError):
                continue
            if min(odds_h, odds_d, odds_a) <= 1.0:
                continue
            try:
                fair = devig_fn([1.0 / odds_h, 1.0 / odds_d, 1.0 / odds_a])
            except Exception:
                continue

            brier = sum((fair[i] - actual[i]) ** 2 for i in range(3))
            brier_vals[bk].append(brier)
            n_fixtures[bk] += 1

            if brier_pin is not None:
                paired_deltas[bk].append(brier - brier_pin)

            p_outcome = max(fair[outcome_idx], 1e-15)
            log_loss_vals[bk].append(-math.log(p_outcome))

    result: dict[str, dict] = {}
    for bk in _FDCO_BOOK_COLS:
        bv = brier_vals[bk]
        pd_list = paired_deltas[bk]
        ll = log_loss_vals[bk]
        n = n_fixtures[bk]
        bci = _bootstrap_ci(bv)
        pci = _bootstrap_ci(pd_list)
        lci = _bootstrap_ci(ll)
        result[bk] = {
            "n_fixtures": n,
            "brier_mean": sum(bv) / n if n > 0 else None,
            "brier_ci": bci,
            "paired_mean": sum(pd_list) / len(pd_list) if pd_list else None,
            "paired_ci": pci,
            "log_loss_mean": sum(ll) / len(ll) if ll else None,
            "log_loss_ci": lci,
        }
    return result


# ---------------------------------------------------------------------------
# B.1: bias signals from FDCO closing odds
# ---------------------------------------------------------------------------

def _bias_from_rows(
    rows: list[dict],
    since: date,
    until: date,
    devig_fn: Callable,
) -> dict[str, dict]:
    """Compute fav-longshot slope + home/draw bias from FDCO closing odds.

    Returns dict[book -> {home_bias, draw_bias, fav_longshot_slope}].

    home/draw bias: mean signed deviation of each book's devigged prob from the
    LOO consensus (book excluded from its own benchmark). Positive = book's
    implied prob is higher than the LOO consensus (i.e. the book prices that
    outcome as *more* likely than peers); negative = book is generous on that
    outcome (higher odds, lower implied prob).

    fav-longshot slope: OLS slope of realised_freq ~ bucket_midpoint across 10
    equal-width probability buckets. ~1.0 = calibrated; >1 = favourite-biased;
    <1 = longshot-biased. Requires >= 3 non-empty buckets (>= 3 obs each).

    Empirical-Bayes shrinkage (n < 200): w = n/(n+50). Bias shrunk toward
    global cross-book mean; slope shrunk toward 1.0 (perfect calibration).
    """
    ftr_map = {"H": 0, "D": 1, "A": 2}

    home_devs: dict[str, list[float]] = defaultdict(list)
    draw_devs: dict[str, list[float]] = defaultdict(list)
    bucket_obs: dict[str, list[tuple[float, int]]] = defaultdict(list)
    n_fixtures: dict[str, int] = defaultdict(int)

    for row in rows:
        d = _parse_fdco_date(row.get("Date", ""))
        if d is None or not (since <= d <= until):
            continue
        ftr = row.get("FTR", "").strip()
        if ftr not in ftr_map:
            continue
        outcome_idx = ftr_map[ftr]

        probs_by_book: dict[str, list[float]] = {}
        for bk, (ch, cd, ca) in _FDCO_BOOK_COLS.items():
            try:
                odds_h = float(row.get(ch, "") or 0)
                odds_d = float(row.get(cd, "") or 0)
                odds_a = float(row.get(ca, "") or 0)
            except (TypeError, ValueError):
                continue
            if min(odds_h, odds_d, odds_a) <= 1.0:
                continue
            try:
                fair = devig_fn([1.0 / odds_h, 1.0 / odds_d, 1.0 / odds_a])
            except Exception:
                continue
            probs_by_book[bk] = fair

        if len(probs_by_book) < 2:
            continue

        for bk, bk_probs in probs_by_book.items():
            others = [p for k, p in probs_by_book.items() if k != bk]
            if not others:
                continue
            loo = [sum(p[i] for p in others) / len(others) for i in range(3)]
            home_devs[bk].append(bk_probs[0] - loo[0])
            draw_devs[bk].append(bk_probs[1] - loo[1])
            n_fixtures[bk] += 1
            for i in range(3):
                bucket_obs[bk].append((bk_probs[i], 1 if i == outcome_idx else 0))

    # Global means for shrinkage prior
    all_home = [v for vals in home_devs.values() for v in vals]
    all_draw = [v for vals in draw_devs.values() for v in vals]
    global_home = sum(all_home) / len(all_home) if all_home else 0.0
    global_draw = sum(all_draw) / len(all_draw) if all_draw else 0.0

    result: dict[str, dict] = {}
    for bk in set(home_devs) | set(bucket_obs):
        n = n_fixtures.get(bk, 0)
        w = n / (n + _SHRINKAGE_N0) if n < 200 else 1.0

        hd = home_devs.get(bk, [])
        dd = draw_devs.get(bk, [])
        raw_home = sum(hd) / len(hd) if hd else None
        raw_draw = sum(dd) / len(dd) if dd else None
        home_bias = raw_home * w + global_home * (1 - w) if raw_home is not None else None
        draw_bias = raw_draw * w + global_draw * (1 - w) if raw_draw is not None else None

        slope = None
        obs = bucket_obs.get(bk, [])
        if obs:
            bucket_hits: dict[int, list[int]] = defaultdict(list)
            for prob, hit in obs:
                bucket_hits[min(int(prob * 10), 9)].append(hit)
            pts = [
                (i * 0.1 + 0.05, sum(v) / len(v))
                for i, v in sorted(bucket_hits.items())
                if len(v) >= 3
            ]
            if len(pts) >= 3:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                n_pts = len(pts)
                sx = sum(xs)
                sy = sum(ys)
                sxy = sum(x * y for x, y in zip(xs, ys))
                sx2 = sum(x * x for x in xs)
                denom = n_pts * sx2 - sx * sx
                if abs(denom) > 1e-10:
                    raw_slope = (n_pts * sxy - sx * sy) / denom
                    # Shrink toward calibrated null (1.0) not global mean, because
                    # a slope of 1.0 is the universal no-bias prior for any book.
                    slope = 1.0 + (raw_slope - 1.0) * w

        result[bk] = {
            "home_bias": home_bias,
            "draw_bias": draw_bias,
            "fav_longshot_slope": slope,
        }

    return result


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

    Emits two rows per (book, league, market, window_end): one per devig_method.
    Each blob and each FDCO CSV is read/decompressed exactly once per league.
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

        # ----- B.0.5: list blobs once, feed events to ALL accumulators -----
        accums: dict[str, _BookAccum] = {
            m: _BookAccum(devig_fn=fn) for m, fn in _DEVIG_FNS.items()
        }
        if archive_enabled:
            prefix = f"odds_api/v4_sports_{sport_key}_odds/"
            all_keys = archive.list_blob_keys(prefix=prefix)
            keys_in_range = [k for k in all_keys
                             if _blob_date_in_range(k, window_start, window_end)]
            print(f"  [B.0.5] {len(keys_in_range)} blobs in window "
                  f"(total under prefix: {len(all_keys)})")
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
                        for accum in accums.values():
                            accum.add_event(ev, truth_anchor)

        blob_agg_by_method = {m: accums[m].aggregate() for m in _DEVIG_FNS}

        # ----- B.0.6: load FDCO CSV once, compute under all methods -----
        fdco_rows = _load_fdco_rows(fdco_code, season) if fdco_code else []
        fdco_brier_by_method: dict[str, dict[str, dict]] = {}
        fdco_bias_by_method: dict[str, dict[str, dict]] = {}
        if fdco_rows:
            for m, fn in _DEVIG_FNS.items():
                fdco_brier_by_method[m] = _brier_from_rows(
                    fdco_rows, window_start, window_end, fn
                )
                fdco_bias_by_method[m] = _bias_from_rows(
                    fdco_rows, window_start, window_end, fn
                )
            n_pin = fdco_brier_by_method["shin"].get("pinnacle", {}).get("n_fixtures", 0)
            print(f"  [B.0.6/B.1] FDCO {fdco_code}: "
                  f"{n_pin} pinnacle-covered fixtures in window")
        else:
            for m in _DEVIG_FNS:
                fdco_brier_by_method[m] = {}
                fdco_bias_by_method[m] = {}
            if fdco_code:
                print(f"  [B.0.6] No FDCO data for {label}.")

        # ----- Build rows: one per (book, devig_method) -----
        for devig_method in _DEVIG_FNS:
            blob_agg = blob_agg_by_method[devig_method]
            fdco_brier = fdco_brier_by_method[devig_method]
            fdco_bias = fdco_bias_by_method[devig_method]

            all_books = set(blob_agg) | set(fdco_brier) | set(fdco_bias)
            for bk in all_books:
                blob = blob_agg.get(bk, {})
                fdco = fdco_brier.get(bk, {})
                bias = fdco_bias.get(bk, {})

                n_fix_blob = blob.get("n_fixtures_blob", 0)
                n_fix_fdco = fdco.get("n_fixtures", 0)

                if n_fix_blob > 0:
                    n_fixtures = n_fix_blob
                    n_fixtures_source: str = "blob"
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

                bci = fdco.get("brier_ci", (None, None))
                pci = fdco.get("paired_ci", (None, None))
                lci = fdco.get("log_loss_ci", (None, None))

                # Bias: FDCO (closing odds) preferred over blob (scan-time).
                # fav_longshot_slope is FDCO-only (blob has no outcomes).
                home_bias = bias.get("home_bias") if bias else blob.get("home_bias_blob")
                draw_bias = bias.get("draw_bias") if bias else blob.get("draw_bias_blob")
                fav_longshot_slope = bias.get("fav_longshot_slope") if bias else None

                row: dict = {
                    "book": bk,
                    "league": label,
                    "market": market,
                    "window_end": window_end_str,
                    "devig_method": devig_method,
                    "n_fixtures": n_fixtures,
                    "n_fixtures_source": n_fixtures_source,
                    "brier_vs_close": None,
                    "brier_vs_outcome": fdco.get("brier_mean"),
                    "brier_vs_outcome_ci_low": bci[0],
                    "brier_vs_outcome_ci_high": bci[1],
                    "brier_paired_vs_pinnacle": fdco.get("paired_mean"),
                    "brier_paired_ci_low": pci[0],
                    "brier_paired_ci_high": pci[1],
                    "log_loss": fdco.get("log_loss_mean"),
                    "log_loss_ci_low": lci[0],
                    "log_loss_ci_high": lci[1],
                    "fav_longshot_slope": fav_longshot_slope,
                    "home_bias": home_bias,
                    "draw_bias": draw_bias,
                    "flag_rate": flag_rate,
                    "mean_flag_edge": mean_flag_edge,
                    "edge_vs_consensus_loo": blob.get("edge_vs_consensus_loo"),
                    "edge_vs_pinnacle": blob.get("edge_vs_pinnacle"),
                    "divergence": blob.get("divergence"),
                    "truth_anchor": truth_anchor,
                }
                rows.append(row)

    n_methods = len(_DEVIG_FNS)
    n_books = len(rows) // n_methods if n_methods else 0
    print(f"\n[book_skill] {len(rows)} rows computed "
          f"(~{n_books} books × {n_methods} devig methods).")

    if dry_run:
        for r in rows[:6]:
            print(f"  {r['book']:20s}  {r['league']:12s}  "
                  f"{r['devig_method']:14s}  "
                  f"n={r['n_fixtures']} ({r['n_fixtures_source']})  "
                  f"paired={r.get('brier_paired_vs_pinnacle')}")
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
    p.add_argument("--window-end", metavar="YYYY-MM-DD",
                   help="Window end date (default: most recent Sunday)")
    p.add_argument("--market", default="h2h",
                   help="Market to analyse (default: h2h)")
    p.add_argument("--leagues", nargs="+", metavar="LABEL",
                   help="Limit to specific league labels, e.g. EPL Bundesliga")
    p.add_argument("--dry-run", action="store_true",
                   help="Compute but do not write to DB; print a sample.")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    we = date.fromisoformat(args.window_end) if args.window_end else _most_recent_sunday()
    compute(window_end=we, market=args.market,
            target_labels=args.leagues, dry_run=args.dry_run)
