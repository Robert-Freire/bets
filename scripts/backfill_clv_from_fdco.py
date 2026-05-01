"""
Monday-morning CLV backfill from football-data.co.uk free Pinnacle closing odds.

Replaces the every-5-min closing_line.py polling for the top-4 leagues.
Walks bets.csv + logs/paper/*.csv and fills pinnacle_close_prob + clv_pct
for any past-kickoff h2h or totals-2.5 bet that doesn't already have them.

CSV-only writes. The Azure SQL closing_lines table is intentionally not
populated here; bets.csv stays the canonical source for CLV during this
swap. See docs/CLAUDE.md "CLV diagnostics" for context.

Usage:
    python3 scripts/backfill_clv_from_fdco.py [--dry-run] [--leagues E0 D1 ...]
                                              [--since YYYY-MM-DD]
"""

import argparse
import csv
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.betting.commissions import effective_odds
from src.betting.devig import shin
from src.betting.team_names import API_TO_FD

_RAW_DIR  = _ROOT / "data" / "raw"
_BETS_CSV = _ROOT / "logs" / "bets.csv"
_PAPER_DIR = _ROOT / "logs" / "paper"
_SEASON   = "2526"
_FDCO_URL = "https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"
_STALE_DAYS = 3

# Sport label → FDCO league code
SPORT_TO_FDCO = {
    "EPL":          "E0",
    "Bundesliga":   "D1",
    "Serie A":      "I1",
    "Ligue 1":      "F1",
    "Championship": "E1",
    "Bundesliga 2": "D2",
}


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


def _kickoff_date(kickoff_str: str) -> str | None:
    try:
        return datetime.strptime(kickoff_str, "%Y-%m-%d %H:%M").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _backfill_row(row: dict, fdco_by_league: dict[str, dict]) -> tuple[float, float] | None:
    """Return (pin_prob, clv_pct) for a row, or None if not backfillable."""
    sport = row.get("sport", "")
    league = SPORT_TO_FDCO.get(sport)
    if not league or league not in fdco_by_league:
        return None
    market = row.get("market", "h2h")
    if market == "btts":
        return None
    if market == "totals" and str(row.get("line", "")).strip() not in ("2.5", "2.5.0"):
        return None

    ko_date = _kickoff_date(row.get("kickoff", ""))
    if not ko_date:
        return None

    home_fd = API_TO_FD.get(row.get("home", ""), row.get("home", ""))
    away_fd = API_TO_FD.get(row.get("away", ""), row.get("away", ""))
    fdco_row = fdco_by_league[league].get((ko_date, home_fd, away_fd))
    if fdco_row is None:
        return None

    side = row.get("side", "")
    if market == "h2h":
        pin_prob = _h2h_pin_prob(fdco_row, side)
    else:
        pin_prob = _totals_pin_prob(fdco_row, side)
    if pin_prob is None or pin_prob <= 0:
        return None

    try:
        odds = float(row.get("odds") or 0)
    except (TypeError, ValueError):
        return None
    if odds <= 1:
        return None
    eff = effective_odds(odds, row.get("book", ""))
    clv_pct = eff * pin_prob - 1
    return round(pin_prob, 6), round(clv_pct, 6)


def _process_csv(path: Path, fdco_by_league: dict[str, dict],
                 since: datetime | None, now: datetime,
                 dry_run: bool) -> tuple[int, int, int]:
    """Returns (backfilled, no_match, already_populated) for one CSV."""
    if not path.exists():
        return (0, 0, 0)
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    for col in ("pinnacle_close_prob", "clv_pct"):
        if col not in fieldnames:
            fieldnames.append(col)

    backfilled = no_match = already = 0
    for row in rows:
        if row.get("pinnacle_close_prob"):
            already += 1
            continue
        ko_str = row.get("kickoff", "")
        try:
            ko_dt = datetime.strptime(ko_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ko_dt > now:
            continue
        if since and ko_dt < since:
            continue

        result = _backfill_row(row, fdco_by_league)
        if result is None:
            no_match += 1
            continue
        pin_prob, clv_pct = result
        row["pinnacle_close_prob"] = f"{pin_prob:.6f}"
        row["clv_pct"] = f"{clv_pct:.6f}"
        backfilled += 1

    if backfilled and not dry_run:
        tmp = path.with_suffix(".csv.tmp")
        with open(tmp, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        tmp.replace(path)
    return (backfilled, no_match, already)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute backfill but don't write CSVs")
    parser.add_argument("--leagues", nargs="+", default=None,
                        help="Limit to FDCO league codes (e.g. E0 D1)")
    parser.add_argument("--since", default=None,
                        help="Only backfill kickoffs on or after YYYY-MM-DD")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    since = None
    if args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    leagues = args.leagues or sorted(set(SPORT_TO_FDCO.values()))
    print(f"=== FDCO CLV backfill === {now.strftime('%Y-%m-%d %H:%M UTC')} "
          f"| leagues={leagues}{' (dry-run)' if args.dry_run else ''}")

    fdco_by_league: dict[str, dict] = {}
    for league in leagues:
        path = _refresh_csv(league)
        if path is None:
            print(f"  [fdco] {league}: no CSV available, skipping")
            continue
        fdco_by_league[league] = _load_fdco_index(path)
        print(f"  [fdco] {league}: {len(fdco_by_league[league])} fixtures indexed")

    csv_paths = [_BETS_CSV] + sorted(_PAPER_DIR.glob("*.csv")) if _PAPER_DIR.exists() else [_BETS_CSV]
    total_b = total_n = total_a = 0
    files_changed = 0
    for path in csv_paths:
        if not path.exists():
            continue
        b, n, a = _process_csv(path, fdco_by_league, since, now, args.dry_run)
        total_b += b
        total_n += n
        total_a += a
        if b:
            files_changed += 1
            print(f"  [{path.name}] backfilled {b} | no FDCO match {n} | already populated {a}")

    print(f"\n[fdco] backfilled {total_b} bets across {files_changed} files; "
          f"skipped {total_n} (no FDCO match); skipped {total_a} (already populated)")


if __name__ == "__main__":
    main()
