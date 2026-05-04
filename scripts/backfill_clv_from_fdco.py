"""
Monday-morning CLV backfill from football-data.co.uk free Pinnacle closing odds.

DB-only rewrite: reads pending rows from Azure SQL, matches against FDCO CSVs,
and writes result/pnl/settled_at/pinnacle_close_prob/clv_pct back to the DB.
No CSV mutation anywhere in this script.

Requires BETS_DB_WRITE=1 + AZURE_SQL_* env vars (see CLAUDE.md A.4).

Usage:
    python3 scripts/backfill_clv_from_fdco.py [--dry-run] [--leagues E0 D1 ...]
                                              [--since YYYY-MM-DD]
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.betting.commissions import effective_odds
from src.betting.devig import shin
from src.betting.team_names import API_TO_FD
from src.config import load_leagues as _load_leagues

_RAW_DIR  = _ROOT / "data" / "raw"
_SEASON   = "2526"
_FDCO_URL = "https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"
_STALE_DAYS = 3

# sport_key → FDCO league code (e.g. "soccer_epl" → "E0")
# load_leagues() entries use "key" which is the same value stored as sport_key in DB.
_FDCO_BY_SPORT_KEY: dict[str, str] = {
    e["key"]: e["fdco_code"]
    for e in _load_leagues()
    if "fdco_code" in e
}


# ── Preserved helpers (unchanged from pre-rewrite) ────────────────────────────

def _refresh_csv(league: str) -> Path | None:
    """Download {league}_{SEASON}.csv if missing or older than _STALE_DAYS."""
    dest = _RAW_DIR / f"{league}_{_SEASON}.csv"
    fresh = dest.exists() and (
        datetime.now(timezone.utc).timestamp() - dest.stat().st_mtime
        < _STALE_DAYS * 86400
    )
    if fresh:
        return dest
    url = _FDCO_URL.format(season=_SEASON, league=league)
    try:
        resp = requests.get(url, timeout=15)
    except requests.RequestException as exc:
        print(f"  [fdco] {league}: download failed ({exc}); using existing copy if any")
        return dest if dest.exists() else None
    if resp.status_code != 200 or len(resp.content) < 100:
        print(f"  [fdco] {league}: HTTP {resp.status_code}; using existing copy if any")
        return dest if dest.exists() else None
    _RAW_DIR.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    print(f"  [fdco] {league}: refreshed ({len(resp.content):,} bytes)")
    time.sleep(0.15)
    return dest


def _parse_fdco_date(s: str) -> datetime | None:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _load_fdco_index(path: Path) -> dict[tuple[str, str, str], dict]:
    """(yyyy-mm-dd, home, away) → row dict, with PSC* + PC>2.5/PC<2.5 only."""
    out: dict[tuple[str, str, str], dict] = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            d = _parse_fdco_date(row.get("Date", ""))
            if not d:
                continue
            home = row.get("HomeTeam", "").strip()
            away = row.get("AwayTeam", "").strip()
            if not home or not away:
                continue
            out[(d.strftime("%Y-%m-%d"), home, away)] = row
    return out


def _h2h_pin_prob(row: dict, side: str) -> float | None:
    try:
        h, d, a = float(row["PSCH"]), float(row["PSCD"]), float(row["PSCA"])
    except (KeyError, ValueError, TypeError):
        return None
    if h <= 1 or d <= 1 or a <= 1:
        return None
    fair = shin([1.0 / h, 1.0 / d, 1.0 / a])
    return {"HOME": fair[0], "DRAW": fair[1], "AWAY": fair[2]}.get(side)


def _totals_pin_prob(row: dict, side: str) -> float | None:
    try:
        ov = float(row["PC>2.5"])
        un = float(row["PC<2.5"])
    except (KeyError, ValueError, TypeError):
        return None
    if ov <= 1 or un <= 1:
        return None
    fair = shin([1.0 / ov, 1.0 / un])
    return {"OVER": fair[0], "UNDER": fair[1]}.get(side)


# ── New helpers ───────────────────────────────────────────────────────────────

def _settle_from_fdco(fdco_row: dict, market: str, side: str) -> str | None:
    """Return 'W', 'L', or None if the result cannot be determined."""
    if market == "h2h":
        ftr = (fdco_row.get("FTR") or "").strip()
        if ftr not in ("H", "D", "A"):
            return None
        return "W" if {"HOME": "H", "DRAW": "D", "AWAY": "A"}.get(side) == ftr else "L"
    if market == "totals":
        try:
            tot = float(fdco_row["FTHG"]) + float(fdco_row["FTAG"])
        except (KeyError, ValueError, TypeError):
            return None
        if side == "OVER":
            return "W" if tot > 2.5 else "L"
        if side == "UNDER":
            return "W" if tot < 2.5 else "L"
    return None


def _pnl(stake: float | None, eff_odds: float | None, result: str) -> float | None:
    """Compute P&L given stake, effective odds, and result string."""
    if stake is None or eff_odds is None or stake <= 0 or eff_odds <= 1:
        return None
    if result == "W":
        return round(stake * (eff_odds - 1), 2)
    if result == "L":
        return round(-stake, 2)
    if result == "void":
        return 0.0
    return None


def _lookup_fdco(row: dict, fdco_by_sport: dict,
                 fdco_by_league: dict[str, dict]) -> dict | None:
    """Return the FDCO row matching this DB row, or None."""
    sport_key = row.get("sport_key", "")
    league = fdco_by_sport.get(sport_key)
    if not league or league not in fdco_by_league:
        return None

    ko_str = str(row.get("kickoff_utc", ""))
    # kickoff_utc may be "YYYY-MM-DD HH:MM:SS" or "YYYY-MM-DDTHH:MM:SS"
    ko_date = ko_str[:10] if ko_str else None
    if not ko_date:
        return None

    home_fd = API_TO_FD.get(row.get("home", ""), row.get("home", ""))
    away_fd = API_TO_FD.get(row.get("away", ""), row.get("away", ""))
    return fdco_by_league[league].get((ko_date, home_fd, away_fd))


def _clv_from_fdco(row: dict, fdco_row: dict) -> tuple[float | None, float | None]:
    """Return (pin_prob, clv_pct) for a DB row given its FDCO match."""
    market = row.get("market", "h2h")
    side = row.get("side", "")
    if market == "h2h":
        pin_prob = _h2h_pin_prob(fdco_row, side)
    elif market == "totals":
        pin_prob = _totals_pin_prob(fdco_row, side)
    else:
        return None, None
    if pin_prob is None or pin_prob <= 0:
        return None, None
    try:
        odds = float(row.get("odds") or 0)
    except (TypeError, ValueError):
        return None, None
    if odds <= 1:
        return None, None
    eff = effective_odds(odds, row.get("book", ""))
    clv_pct = eff * pin_prob - 1
    return round(pin_prob, 6), round(clv_pct, 6)


# ── Repo factory (monkeypatchable in tests) ───────────────────────────────────

def _make_repo(BetRepo):
    """Construct BetRepo pointed at the project logs dir.

    Separate function so tests can monkeypatch _make_repo to inject a
    pre-wired SQLite repo without importing BetRepo at module level.
    """
    return BetRepo(logs_dir=_ROOT / "logs")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Log intended UPDATEs without executing them")
    parser.add_argument("--leagues", nargs="+", default=None,
                        help="Limit to FDCO league codes (e.g. E0 D1)")
    parser.add_argument("--since", default=None,
                        help="Only backfill kickoffs on or after YYYY-MM-DD")
    args = parser.parse_args()

    # T9: Refuse to run without DB
    if os.environ.get("BETS_DB_WRITE", "").strip() != "1":
        print(
            "Result/CLV backfill writes to Azure SQL only. "
            "Set BETS_DB_WRITE=1 + AZURE_SQL_* env vars (see CLAUDE.md A.4).",
            file=sys.stderr,
        )
        sys.exit(1)

    now_utc = datetime.now(timezone.utc)
    since: datetime | None = None
    if args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    leagues = args.leagues or sorted(set(_FDCO_BY_SPORT_KEY.values()))
    print(
        f"=== FDCO CLV backfill (DB-only) === {now_utc.strftime('%Y-%m-%d %H:%M UTC')} "
        f"| leagues={leagues}{' (dry-run)' if args.dry_run else ''}"
    )

    # Load FDCO CSVs
    fdco_by_league: dict[str, dict] = {}
    for league in leagues:
        path = _refresh_csv(league)
        if path is None:
            print(f"  [fdco] {league}: no CSV available, skipping")
            continue
        fdco_by_league[league] = _load_fdco_index(path)
        print(f"  [fdco] {league}: {len(fdco_by_league[league])} fixtures indexed")

    from src.storage.repo import BetRepo as _BetRepo
    repo = _make_repo(_BetRepo)

    # Counters
    bet_w = bet_l = bet_void = 0
    paper_w = paper_l = paper_void = 0
    clv_bets = 0
    clv_paper = 0
    no_match = 0
    already_complete = 0

    # T5: driver loop
    for row in repo.iter_unsettled_or_no_clv(now_utc=now_utc.replace(tzinfo=None)):
        market = row.get("market", "h2h")
        side = row.get("side", "")
        is_paper = row.get("strategy_name") is not None

        # Skip BTTS (no FDCO column)
        if market == "btts":
            continue
        # Skip totals on non-2.5 lines
        if market == "totals":
            line_val = row.get("line")
            if line_val is None or abs(float(line_val) - 2.5) > 0.001:
                continue

        # --since filter
        if since:
            ko_str = str(row.get("kickoff_utc", ""))
            try:
                ko_dt = datetime.strptime(ko_str[:19], "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                try:
                    ko_dt = datetime.strptime(ko_str[:16], "%Y-%m-%dT%H:%M").replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    ko_dt = None
            if ko_dt and ko_dt < since:
                continue

        fdco_row = _lookup_fdco(row, _FDCO_BY_SPORT_KEY, fdco_by_league)
        if fdco_row is None:
            no_match += 1
            continue

        pin_prob, clv_val = _clv_from_fdco(row, fdco_row)

        new_result: str | None = None
        new_pnl: float | None = None
        if row.get("result") == "pending":
            new_result = _settle_from_fdco(fdco_row, market, side)
            if new_result is not None:
                # Paper: stake basis is stake; production: actual_stake or None
                stake_basis = (
                    row.get("stake") if is_paper else row.get("actual_stake")
                )
                try:
                    stake_f = float(stake_basis) if stake_basis is not None else None
                except (TypeError, ValueError):
                    stake_f = None
                eff = row.get("effective_odds") or row.get("odds")
                try:
                    eff_f = float(eff) if eff is not None else None
                except (TypeError, ValueError):
                    eff_f = None
                new_pnl = _pnl(stake_f, eff_f, new_result)

        # Skip rows that need no update
        if new_result is None and pin_prob is None:
            already_complete += 1
            continue

        if args.dry_run:
            print(
                f"  [dry-run] {'paper' if is_paper else 'bet'} "
                f"{row.get('home')} vs {row.get('away')} "
                f"{market}/{side}: result={new_result} pin={pin_prob} clv={clv_val}"
            )
            continue

        if is_paper:
            ok = repo.settle_paper_bet(
                row["strategy_name"],
                row["fixture_id"],
                side,
                market,
                row.get("line"),
                row["book"],
                result=new_result,
                pnl=new_pnl,
                pin_prob=pin_prob,
                clv_pct=clv_val,
            )
            if ok:
                if new_result == "W":
                    paper_w += 1
                elif new_result == "L":
                    paper_l += 1
                elif new_result == "void":
                    paper_void += 1
                if pin_prob is not None:
                    clv_paper += 1
        else:
            ok = repo.settle_bet(
                row["fixture_id"],
                side,
                market,
                row.get("line"),
                row["book"],
                result=new_result,
                pnl=new_pnl,
                pin_prob=pin_prob,
                clv_pct=clv_val,
            )
            if ok:
                if new_result == "W":
                    bet_w += 1
                elif new_result == "L":
                    bet_l += 1
                elif new_result == "void":
                    bet_void += 1
                if pin_prob is not None:
                    clv_bets += 1

    repo.close()

    # T6: summary
    print(
        f"[fdco] settled: bets W/L/void = {bet_w}/{bet_l}/{bet_void} | "
        f"paper W/L/void = {paper_w}/{paper_l}/{paper_void}"
    )
    print(
        f"       clv backfilled: bets={clv_bets} paper={clv_paper}"
    )
    print(
        f"       no FDCO match: {no_match} | already complete: {already_complete}"
    )


if __name__ == "__main__":
    main()
