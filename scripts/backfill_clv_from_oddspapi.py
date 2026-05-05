"""
One-shot CLV backfill from OddsPapi (Pinnacle close odds source).

Why this exists: FDCO stopped publishing Pinnacle close odds (PSCH/PSCD/PSCA,
PC>2.5/PC<2.5) ~mid-Jan 2026 after Pinnacle closed their public API. This
script fills `pinnacle_close_prob` and `clv_pct` from OddsPapi for bets where
FDCO has nothing to give us. It also settles result/pnl from FDCO when those
are still pending (mirrors backfill_clv_from_fdco helpers).

Default: --dry-run. Writes an audit CSV but no DB UPDATEs. Pass --commit
to actually write.

Cache: every API response saved under logs/cache/oddspapi/. Re-runs read
from cache and cost zero requests for already-seen fixtures.

Usage:
    python3 scripts/backfill_clv_from_oddspapi.py --from 2026-05-02 --to 2026-05-03
    python3 scripts/backfill_clv_from_oddspapi.py --from 2026-05-02 --to 2026-05-03 --commit
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.betting.commissions import effective_odds
from src.betting.devig import shin

# Reuse FDCO settlement helpers without modifying the FDCO script.
_fdco_path = _ROOT / "scripts" / "backfill_clv_from_fdco.py"
_spec = importlib.util.spec_from_file_location("fdco_bf", _fdco_path)
_fdco = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fdco)

# ── Config ────────────────────────────────────────────────────────────────────

ODDSPAPI_BASE = "https://api.oddspapi.io/v4"
ODDSPAPI_TIMEOUT = 20
ODDSPAPI_SLEEP = 5.0  # seconds between requests; was 0.25 in Haiku's broken probe
ODDSPAPI_MAX_RETRIES = 2

# sport_key → OddsPapi tournamentId.
# Includes La Liga (not in production scanner scope but appears in paper_bets).
_SPORT_KEY_TO_TID = {
    "soccer_epl":                 17,
    "soccer_efl_champ":           18,
    "soccer_italy_serie_a":       23,
    "soccer_france_ligue_one":    34,
    "soccer_germany_bundesliga":  35,
    "soccer_germany_bundesliga2": 44,
    "soccer_spain_la_liga":       8,
}

# Local override: add SP1 (La Liga) to FDCO settlement map. The FDCO module's
# map only covers leagues in config.json (production scope); we extend it here
# so paper_bets La Liga rows can still get result/pnl settled.
_FDCO_OVERRIDE = {"soccer_spain_la_liga": "SP1"}

# Markets we support
_MARKET_H2H = "101"
_OUTCOME_H2H = {"HOME": "101", "DRAW": "102", "AWAY": "103"}

_CACHE_DIR = _ROOT / "logs" / "cache" / "oddspapi"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── Cache layer ───────────────────────────────────────────────────────────────

class Quota:
    """Mutable counter for API requests issued vs cache hits."""
    def __init__(self) -> None:
        self.requests = 0
        self.cache_hits = 0


def _cache_path(endpoint: str, slug: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", slug)[:120]
    sub = _CACHE_DIR / endpoint
    sub.mkdir(parents=True, exist_ok=True)
    return sub / f"{safe}.json"


def _http_get(url: str, params: dict, quota: Quota) -> dict | list | None:
    """GET with retry on 429 honoring Retry-After. Sleeps ODDSPAPI_SLEEP after."""
    for attempt in range(ODDSPAPI_MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=ODDSPAPI_TIMEOUT)
        except requests.RequestException as exc:
            print(f"  [oddspapi] network error ({exc}); attempt {attempt+1}",
                  file=sys.stderr)
            time.sleep(2 ** attempt)
            continue
        quota.requests += 1
        if resp.status_code == 200:
            time.sleep(ODDSPAPI_SLEEP)
            try:
                return resp.json()
            except ValueError:
                print(f"  [oddspapi] non-JSON body, status 200, len={len(resp.content)}",
                      file=sys.stderr)
                return None
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", "30"))
            print(f"  [oddspapi] 429 rate-limited, sleeping {wait}s "
                  f"(attempt {attempt+1}/{ODDSPAPI_MAX_RETRIES+1})", file=sys.stderr)
            time.sleep(wait + 1)
            continue
        # 4xx other than 429 → don't retry
        if 400 <= resp.status_code < 500:
            print(f"  [oddspapi] HTTP {resp.status_code}: {resp.text[:200]}",
                  file=sys.stderr)
            return None
        # 5xx → retry
        time.sleep(2 ** attempt)
    return None


def _cached_get(endpoint: str, slug: str, params: dict, api_key: str,
                quota: Quota) -> dict | list | None:
    """Cache-first GET. Cache hits cost 0 requests."""
    path = _cache_path(endpoint, slug)
    if path.exists():
        quota.cache_hits += 1
        return json.loads(path.read_text())
    url = f"{ODDSPAPI_BASE}/{endpoint}"
    full = {**params, "apiKey": api_key}
    data = _http_get(url, full, quota)
    if data is not None:
        path.write_text(json.dumps(data))
    return data


# ── Fixture lookup (DB → OddsPapi fixtureId) ──────────────────────────────────

import unicodedata

# Strip ONLY corporate/legal-form tokens — never strip distinguishing words like
# "United"/"City"/"Town" (would collide e.g. Man Utd vs Man City).
_NAME_STRIP = re.compile(
    r"\b(fc|afc|cf|ac|ssc|ssd|asd|sc|sv|tsg|tsv|vfl|vfb|fsv|bsc|"
    r"as|us|aas|aj|ogc|sm|stade)\b",
    re.IGNORECASE,
)
_NUM_PREFIX = re.compile(r"^\s*\d+\s*\.?\s*")  # "1. FC Köln" → "FC Köln"

# Post-normalisation aliases — both sides collapsed to a shared key.
# Keep this list small + explicit; extend as new mismatches appear.
_ALIAS = {
    "cologne":             "koln",                  # FDCO/Odds-API: Köln; OddsPapi: Cologne
    "nuremberg":           "nurnberg",              # ditto Nürnberg / Nuremberg
    "preussen06munster":   "preussenmunster",       # OddsPapi adds founding year "06"
    "olympiquelyon":       "lyon",                  # full club name vs short
    "rennais":             "rennes",                # adjective vs city
    "stadebrest29":        "brest",                 # Stade Brest 29 → Brest
    "racingclubdelens":    "lens",                  # full name vs short
    "lcrlens":             "lens",
    "rclens":              "lens",                  # both → "lens"
    "olympiquemarseille":  "marseille",
    "herthaberlin":        "hertha",                # DB 'Hertha Berlin' vs OddsPapi 'Hertha BSC' (BSC stripped)
}


def _norm(name: str) -> str:
    """Lower, strip diacritics, strip corporate/legal tokens, drop non-alnum.

    Also applies a small alias table for known DB ↔ OddsPapi name divergences.
    """
    if not name:
        return ""
    s = name.replace("ß", "ss").replace("Ø", "O").replace("ø", "o")
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = s.replace("&", " and ")
    s = _NUM_PREFIX.sub(" ", s)        # "1. fc koln" → " fc koln"
    s = _NAME_STRIP.sub(" ", s)        # " fc koln" → "  koln"
    s = re.sub(r"[^a-z0-9]+", "", s)
    return _ALIAS.get(s, s)


def _fixture_lookup(sport_key: str, date_from: str, date_to: str,
                    api_key: str, quota: Quota) -> dict[tuple[str, str], dict]:
    """Return {(home_norm, away_norm): fixture_dict} for a tournament + window.

    Cached as fixtures/{tid}_{from}_{to}.json. Re-runs cost 0.
    """
    tid = _SPORT_KEY_TO_TID.get(sport_key)
    if tid is None:
        return {}
    slug = f"tid{tid}_{date_from}_{date_to}"
    data = _cached_get(
        endpoint="fixtures",
        slug=slug,
        params={"tournamentId": tid, "from": date_from, "to": date_to},
        api_key=api_key,
        quota=quota,
    )
    if not isinstance(data, list):
        return {}
    out: dict[tuple[str, str], dict] = {}
    for fx in data:
        h = _norm(fx.get("participant1Name", ""))
        a = _norm(fx.get("participant2Name", ""))
        if h and a:
            out[(h, a)] = fx
    return out


def _resolve_fixture_id(home_db: str, away_db: str,
                        catalogue: dict[tuple[str, str], dict]) -> dict | None:
    """Find a fixture by normalised (home, away) in a pre-loaded catalogue."""
    h = _norm(home_db)
    a = _norm(away_db)
    if (h, a) in catalogue:
        return catalogue[(h, a)]
    # try swapped (in case OddsPapi listed teams in opposite order — rare but safe)
    if (a, h) in catalogue:
        return catalogue[(a, h)]
    # token-overlap fallback: every catalogue entry whose names share most chars
    for (ch, ca), fx in catalogue.items():
        if (h and h in ch and a and a in ca) or (h and ch in h and a and ca in a):
            return fx
    return None


# ── Closing-line extraction from historical-odds JSON ─────────────────────────

def _kickoff_iso(kickoff_utc) -> str:
    """Coerce DB kickoff_utc (datetime or str) to ISO 'YYYY-MM-DDTHH:MM:SS'."""
    if hasattr(kickoff_utc, "isoformat"):
        return kickoff_utc.isoformat()[:19]
    s = str(kickoff_utc).replace(" ", "T")
    return s[:19]


def _closing_h2h_probs(historical: dict, kickoff_utc) -> dict[str, float] | None:
    """Return {'HOME': p, 'DRAW': p, 'AWAY': p} Shin-devigged, or None.

    Picks per-outcome the latest entry where active=True AND createdAt <= kickoff.
    """
    if not historical or "bookmakers" not in historical:
        return None
    pin = historical["bookmakers"].get("pinnacle")
    if not pin:
        return None
    market = pin.get("markets", {}).get(_MARKET_H2H)
    if not market:
        return None
    outcomes = market.get("outcomes", {})
    ko_iso = _kickoff_iso(kickoff_utc)
    decimals: dict[str, float] = {}
    for side, oid in _OUTCOME_H2H.items():
        oc = outcomes.get(oid, {})
        plist = oc.get("players", {}).get("0") or []
        # Walk back to the last entry with active=True AND createdAt <= kickoff
        chosen = None
        for entry in plist:
            ts = (entry.get("createdAt") or "")[:19]
            if ts and ts <= ko_iso and entry.get("active") and entry.get("price"):
                chosen = entry  # keep walking to find latest
        if not chosen:
            return None
        try:
            decimals[side] = float(chosen["price"])
        except (TypeError, ValueError):
            return None
    if any(d <= 1 for d in decimals.values()):
        return None
    fair = shin([1.0 / decimals["HOME"], 1.0 / decimals["DRAW"],
                 1.0 / decimals["AWAY"]])
    return {"HOME": fair[0], "DRAW": fair[1], "AWAY": fair[2]}


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    # Defaults cover last 7 days (Mon cron looks back at last week's weekend,
    # Wed cron picks up midweek + weekend leftovers). Idempotent re-runs.
    from datetime import timedelta as _td
    _today = datetime.utcnow().date()
    parser.add_argument("--from", dest="date_from",
                        default=(_today - _td(days=7)).isoformat(),
                        help="Kickoff date inclusive YYYY-MM-DD (default: 7d ago)")
    parser.add_argument("--to", dest="date_to",
                        default=(_today - _td(days=1)).isoformat(),
                        help="Kickoff date inclusive YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--commit", action="store_true",
                        help="Actually write to DB (default: dry-run)")
    parser.add_argument("--leagues", nargs="+", default=None,
                        help="Limit to sport_keys (default: all 6 production)")
    args = parser.parse_args()

    if not args.commit:
        print("=== DRY-RUN — no DB writes will happen. Pass --commit when ready. ===")

    # Env checks
    if os.environ.get("BETS_DB_WRITE", "").strip() != "1":
        print("Error: BETS_DB_WRITE=1 required (Azure SQL). See CLAUDE.md A.4.",
              file=sys.stderr)
        sys.exit(1)
    api_key = os.environ.get("ODDSPAPI_KEY", "").strip()
    if not api_key:
        print("Error: ODDSPAPI_KEY env var not set", file=sys.stderr)
        sys.exit(1)

    # Date window expansion (give ±1 day buffer for fixtures-list endpoint)
    try:
        d_from = datetime.strptime(args.date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        d_to   = datetime.strptime(args.date_to,   "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        print("Error: --from/--to must be YYYY-MM-DD", file=sys.stderr)
        sys.exit(1)

    sport_keys = args.leagues or list(_SPORT_KEY_TO_TID.keys())
    sport_keys = [k for k in sport_keys if k in _SPORT_KEY_TO_TID]

    run_iso = datetime.utcnow().strftime("%Y-%m-%dT%H%M%SZ")
    audit_dir = _ROOT / "logs" / "backfill" / "oddspapi" / run_iso
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / "audit.csv"

    quota = Quota()

    # Pre-load FDCO indexes (for result/pnl settlement; same as FDCO script)
    # Extend map with our local overrides (e.g. La Liga / SP1).
    fdco_map = {**_fdco._FDCO_BY_SPORT_KEY, **_FDCO_OVERRIDE}
    fdco_by_league: dict[str, dict] = {}
    needed_fdco = {fdco_map[k] for k in sport_keys if k in fdco_map}
    for league in sorted(needed_fdco):
        path = _fdco._refresh_csv(league)
        if path:
            fdco_by_league[league] = _fdco._load_fdco_index(path)
            print(f"  [fdco] {league}: {len(fdco_by_league[league])} fixtures indexed")

    # Connect to DB
    from src.storage.repo import BetRepo
    repo = BetRepo(logs_dir=_ROOT / "logs")

    # Pre-fetch OddsPapi fixtures catalogues per (sport_key, window)
    catalogues: dict[str, dict[tuple[str, str], dict]] = {}
    # OddsPapi fixtures `to` is exclusive at midnight, so extend by +1d to
    # include the last day's fixtures.
    from datetime import timedelta
    api_to = (d_to + timedelta(days=1)).strftime("%Y-%m-%d")
    print()
    print(f"=== Fetching OddsPapi fixtures catalogues for {len(sport_keys)} leagues ===")
    print(f"    (DB filter: {args.date_from}..{args.date_to}; OddsPapi window: {args.date_from}..{api_to})")
    for sk in sport_keys:
        cat = _fixture_lookup(
            sk, args.date_from, api_to, api_key, quota)
        catalogues[sk] = cat
        print(f"  {sk:34s}: {len(cat)} fixtures in window")

    # Walk DB rows that need backfill
    print()
    print(f"=== Iterating DB rows (kickoff < now, missing CLV or pending) ===")
    now_utc = datetime.utcnow()
    audit_rows: list[dict] = []

    historical_cache: dict[str, dict | None] = {}  # fixtureId → historical JSON
    probs_cache: dict[str, dict[str, float] | None] = {}  # fixtureId → close probs

    counters = {
        "scanned": 0, "out_of_window": 0, "skip_sport": 0,
        "skip_market": 0, "no_fixture_id": 0, "no_historical": 0,
        "no_closing_line": 0, "would_write_clv": 0, "would_settle_result": 0,
        "wrote_clv": 0, "wrote_result": 0,
    }

    for row in repo.iter_unsettled_or_no_clv(now_utc=now_utc):
        counters["scanned"] += 1
        ko = row.get("kickoff_utc")
        ko_iso = _kickoff_iso(ko)
        ko_date = ko_iso[:10]
        if not (args.date_from <= ko_date <= args.date_to):
            counters["out_of_window"] += 1
            continue
        sport_key = row.get("sport_key", "")
        if sport_key not in catalogues:
            counters["skip_sport"] += 1
            continue
        market = row.get("market", "h2h")
        side = row.get("side", "")
        if market != "h2h":
            # totals/btts not supported in this first pass
            counters["skip_market"] += 1
            audit_rows.append({
                **_audit_base(row),
                "status": f"skip_market_{market}",
            })
            continue

        # Find OddsPapi fixtureId
        fx = _resolve_fixture_id(row.get("home", ""), row.get("away", ""),
                                 catalogues[sport_key])
        if fx is None:
            counters["no_fixture_id"] += 1
            audit_rows.append({**_audit_base(row), "status": "no_fixture_match"})
            continue
        fix_id = fx.get("fixtureId")

        # Pull historical-odds for this fixture (cached)
        if fix_id not in historical_cache:
            data = _cached_get(
                endpoint="historical-odds",
                slug=f"{fix_id}_pinnacle",
                params={"fixtureId": fix_id, "bookmakers": "pinnacle"},
                api_key=api_key,
                quota=quota,
            )
            historical_cache[fix_id] = data
            probs_cache[fix_id] = _closing_h2h_probs(data, ko)
        if historical_cache[fix_id] is None:
            counters["no_historical"] += 1
            audit_rows.append({**_audit_base(row), "status": "no_historical_odds",
                               "oddspapi_fixture_id": fix_id})
            continue
        probs = probs_cache[fix_id]
        if probs is None:
            counters["no_closing_line"] += 1
            audit_rows.append({**_audit_base(row), "status": "no_closing_line",
                               "oddspapi_fixture_id": fix_id})
            continue

        pin_prob = probs.get(side)
        # Compute CLV (mirror backfill_clv_from_fdco._clv_from_fdco)
        try:
            odds = float(row.get("odds") or 0)
        except (TypeError, ValueError):
            odds = 0.0
        clv_val: float | None = None
        if pin_prob and pin_prob > 0 and odds > 1:
            eff = effective_odds(odds, row.get("book", ""))
            clv_val = round(eff * pin_prob - 1, 6)
            pin_prob_round = round(pin_prob, 6)
        else:
            pin_prob_round = None
            clv_val = None

        # Settle result/pnl from FDCO if pending and FDCO has the row
        new_result = None
        new_pnl = None
        if row.get("result") == "pending":
            fdco_row = _fdco._lookup_fdco(row, fdco_map, fdco_by_league)
            if fdco_row is not None:
                new_result = _fdco._settle_from_fdco(fdco_row, market, side)
                if new_result is not None:
                    is_paper = row.get("strategy_name") is not None
                    stake_basis = (row.get("stake") if is_paper
                                   else row.get("actual_stake"))
                    try:
                        stake_f = float(stake_basis) if stake_basis is not None else None
                    except (TypeError, ValueError):
                        stake_f = None
                    eff = row.get("effective_odds") or row.get("odds")
                    try:
                        eff_f = float(eff) if eff is not None else None
                    except (TypeError, ValueError):
                        eff_f = None
                    new_pnl = _fdco._pnl(stake_f, eff_f, new_result)

        if pin_prob_round is None and new_result is None:
            audit_rows.append({**_audit_base(row), "status": "nothing_to_write",
                               "oddspapi_fixture_id": fix_id})
            continue

        if pin_prob_round is not None:
            counters["would_write_clv"] += 1
        if new_result is not None:
            counters["would_settle_result"] += 1

        # Audit row (whether or not we commit)
        is_paper = row.get("strategy_name") is not None
        audit_rows.append({
            **_audit_base(row),
            "oddspapi_fixture_id": fix_id,
            "oddspapi_p_home": probs.get("HOME"),
            "oddspapi_p_draw": probs.get("DRAW"),
            "oddspapi_p_away": probs.get("AWAY"),
            "pinnacle_close_prob_after": pin_prob_round,
            "clv_pct_after": clv_val,
            "result_after": new_result,
            "pnl_after": new_pnl,
            "status": "DRY_RUN" if not args.commit else "PENDING_WRITE",
        })

        if args.commit:
            if is_paper:
                ok = repo.settle_paper_bet(
                    row["strategy_name"], row["fixture_id"], side, market,
                    row.get("line"), row["book"],
                    result=new_result, pnl=new_pnl,
                    pin_prob=pin_prob_round, clv_pct=clv_val,
                )
            else:
                ok = repo.settle_bet(
                    row["fixture_id"], side, market,
                    row.get("line"), row["book"],
                    result=new_result, pnl=new_pnl,
                    pin_prob=pin_prob_round, clv_pct=clv_val,
                )
            if ok:
                if pin_prob_round is not None:
                    counters["wrote_clv"] += 1
                if new_result is not None:
                    counters["wrote_result"] += 1
                audit_rows[-1]["status"] = "WRITTEN"

    repo.close()

    # Write audit CSV
    if audit_rows:
        keys = sorted({k for r in audit_rows for k in r.keys()})
        with audit_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            for r in audit_rows:
                w.writerow(r)

    # Summary
    print()
    print(f"=== Summary ===")
    print(f"  rows scanned:               {counters['scanned']}")
    print(f"  out of date window:         {counters['out_of_window']}")
    print(f"  skipped (sport):            {counters['skip_sport']}")
    print(f"  skipped (non-h2h market):   {counters['skip_market']}")
    print(f"  no fixture_id match:        {counters['no_fixture_id']}")
    print(f"  no historical odds:         {counters['no_historical']}")
    print(f"  no closing-line snapshot:   {counters['no_closing_line']}")
    print(f"  would write CLV:            {counters['would_write_clv']}")
    print(f"  would settle result:        {counters['would_settle_result']}")
    if args.commit:
        print(f"  COMMITTED CLV writes:       {counters['wrote_clv']}")
        print(f"  COMMITTED result writes:    {counters['wrote_result']}")
    print()
    print(f"  OddsPapi requests issued:   {quota.requests}")
    print(f"  OddsPapi cache hits:        {quota.cache_hits}")
    print(f"  audit csv:                  {audit_path}")
    if not args.commit:
        print()
        print("DRY-RUN complete. Inspect audit CSV; if happy, re-run with --commit.")


def _audit_base(row: dict) -> dict:
    return {
        "is_paper": row.get("strategy_name") is not None,
        "strategy_name": row.get("strategy_name") or "",
        "fixture_id": row.get("fixture_id"),
        "sport_key": row.get("sport_key"),
        "kickoff_utc": str(row.get("kickoff_utc") or "")[:19],
        "home": row.get("home"),
        "away": row.get("away"),
        "side": row.get("side"),
        "market": row.get("market"),
        "book": row.get("book"),
        "decimal_odds": row.get("odds"),
        "result_before": row.get("result"),
        "pinnacle_close_prob_before": row.get("pinnacle_close_prob"),
    }


if __name__ == "__main__":
    main()
