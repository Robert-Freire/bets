"""Ingest upcoming fixtures from football-data.co.uk and (optionally) api.football-data.org.

Writes logs/fixture_calendar.json for use by src/data/fixture_calendar.py.
Both Pi and WSL run this script — no DB dependency required.

Sources:
  1. FDCO fixtures.csv (free, all leagues, includes kickoff time in UK local time)
  2. api.football-data.org (optional; set FOOTBALL_DATA_API_KEY; EPL/BL/SA/L1 only)

Usage:
    python3 scripts/ingest_fixtures.py [--dry-run] [--leagues E0 D1 ...]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.data.downloader import LEAGUES

_LONDON = ZoneInfo("Europe/London")
_FDCO_FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"
_FDCO_SEASON_URL = "https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"
_CURRENT_SEASON = "2526"
_CALENDAR_PATH = _ROOT / "logs" / "fixture_calendar.json"
_RAW_DIR = _ROOT / "data" / "raw"
_FORWARD_WEEKS = 8

# Reverse map: FDCO code (e.g. "E0") → Odds API sport_key
_FDCO_TO_SPORT: dict[str, str] = {v["fd_code"]: k for k, v in LEAGUES.items()}

# Patchable in tests
_today = date.today

# api.football-data.org competition codes available on the free tier
_AFD_BASE = "https://api.football-data.org/v4"
_AFD_CODES: dict[str, str] = {
    "soccer_epl":                "PL",
    "soccer_germany_bundesliga": "BL1",
    "soccer_italy_serie_a":      "SA",
    "soccer_france_ligue_one":   "FL1",
}


def _parse_fdco_kickoff(date_str: str, time_str: str) -> datetime | None:
    """Parse FDCO Date + Time (UK local) → UTC datetime. Defaults to 12:00 when time absent."""
    date_str = (date_str or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            d = datetime.strptime(date_str, fmt)
            break
        except ValueError:
            continue
    else:
        return None

    h, m = 12, 0
    ts = (time_str or "").strip()
    if ts:
        try:
            parts = ts.split(":")
            h, m = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            pass

    local_dt = d.replace(hour=h, minute=m, tzinfo=_LONDON)
    return local_dt.astimezone(timezone.utc)


def _fetch_fdco_fixtures_csv() -> list[dict]:
    """Fetch FDCO fixtures.csv (combined upcoming fixtures, all leagues)."""
    today = _today()
    cutoff = today + timedelta(weeks=_FORWARD_WEEKS)

    print(f"[fdco] Fetching {_FDCO_FIXTURES_URL} ...")
    try:
        req = urllib.request.Request(
            _FDCO_FIXTURES_URL, headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"[fdco] fixtures.csv fetch failed: {e}")
        return []

    fixtures: list[dict] = []
    reader = csv.DictReader(io.StringIO(raw))
    for row in reader:
        div = (row.get("Div") or "").strip()
        sport_key = _FDCO_TO_SPORT.get(div)
        if not sport_key:
            continue
        ko = _parse_fdco_kickoff(row.get("Date", ""), row.get("Time", ""))
        if ko is None:
            continue
        if not (today <= ko.date() <= cutoff):
            continue
        home = (row.get("HomeTeam") or "").strip()
        away = (row.get("AwayTeam") or "").strip()
        if not home or not away:
            continue
        fixtures.append({
            "sport_key": sport_key,
            "league": LEAGUES[sport_key]["label"],
            "home": home,
            "away": away,
            "kickoff_utc": ko.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "source": "fdco",
            "status": "scheduled",
        })

    print(f"[fdco] fixtures.csv: {len(fixtures)} upcoming fixtures across "
          f"{len({f['sport_key'] for f in fixtures})} leagues")
    return fixtures


def _fetch_fdco_season_csvs(limit_fdco_codes: list[str] | None = None) -> list[dict]:
    """Fall back: parse upcoming fixtures from current-season per-league CSVs.

    Used when fixtures.csv is unavailable. Season CSVs may include kickoff times
    but only for the current season window already published.
    """
    today = _today()
    cutoff = today + timedelta(weeks=_FORWARD_WEEKS)

    leagues_to_check = {
        k: v for k, v in LEAGUES.items()
        if limit_fdco_codes is None or v["fd_code"] in limit_fdco_codes
    }

    fixtures: list[dict] = []
    for sport_key, meta in leagues_to_check.items():
        fd_code = meta["fd_code"]
        csv_path = _RAW_DIR / f"{fd_code}_{_CURRENT_SEASON}.csv"
        if not csv_path.exists():
            continue
        try:
            with open(csv_path, newline="") as f:
                for row in csv.DictReader(f):
                    ko = _parse_fdco_kickoff(row.get("Date", ""), row.get("Time", ""))
                    if ko is None:
                        continue
                    if not (today <= ko.date() <= cutoff):
                        continue
                    # Upcoming rows have no result yet
                    if row.get("FTR", "").strip():
                        continue
                    home = (row.get("HomeTeam") or "").strip()
                    away = (row.get("AwayTeam") or "").strip()
                    if not home or not away:
                        continue
                    fixtures.append({
                        "sport_key": sport_key,
                        "league": meta["label"],
                        "home": home,
                        "away": away,
                        "kickoff_utc": ko.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                        "source": "fdco_season",
                        "status": "scheduled",
                    })
        except Exception as e:
            print(f"[fdco] {fd_code}: season CSV parse error: {e}")

    print(f"[fdco] season CSVs: {len(fixtures)} upcoming fixtures")
    return fixtures


def _fetch_afd(api_key: str) -> list[dict]:
    """Fetch scheduled fixtures from api.football-data.org (free tier, 4 leagues)."""
    today = _today()
    date_to = today + timedelta(weeks=_FORWARD_WEEKS)

    fixtures: list[dict] = []
    for sport_key, comp_code in _AFD_CODES.items():
        url = (
            f"{_AFD_BASE}/competitions/{comp_code}/matches"
            f"?status=SCHEDULED&dateFrom={today}&dateTo={date_to}"
        )
        try:
            req = urllib.request.Request(
                url, headers={"X-Auth-Token": api_key, "User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            print(f"[afd] {comp_code}: ERROR {e}")
            time.sleep(7)
            continue

        matches = data.get("matches", [])
        for m in matches:
            ko_str = m.get("utcDate", "")
            try:
                ko = datetime.fromisoformat(ko_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            home = m.get("homeTeam", {}).get("name", "").strip()
            away = m.get("awayTeam", {}).get("name", "").strip()
            if not home or not away:
                continue
            fixtures.append({
                "sport_key": sport_key,
                "league": LEAGUES[sport_key]["label"],
                "home": home,
                "away": away,
                "kickoff_utc": ko.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                "source": "afd",
                "status": "scheduled",
            })
        print(f"[afd] {comp_code}: {len(matches)} fixtures")
        time.sleep(7)  # free tier: 10 req/min

    return fixtures


def _merge(primary: list[dict], secondary: list[dict]) -> list[dict]:
    """Merge two fixture lists. For leagues in primary, use primary; keep secondary-only leagues."""
    primary_leagues = {f["sport_key"] for f in primary}
    out = list(primary)
    for f in secondary:
        if f["sport_key"] not in primary_leagues:
            out.append(f)
    return out


def _dedup(fixtures: list[dict]) -> list[dict]:
    """Remove duplicates keyed by (sport_key, kickoff_utc, home, away)."""
    seen: set[tuple] = set()
    out: list[dict] = []
    for f in fixtures:
        key = (f["sport_key"], f["kickoff_utc"], f["home"], f["away"])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Parse but don't write")
    parser.add_argument(
        "--leagues", nargs="+", default=None,
        help="Limit to FDCO codes (e.g. E0 D1). Default: all.",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    print(f"=== Fixture calendar ingest === {now.strftime('%Y-%m-%d %H:%M UTC')}")

    # Step 1: FDCO combined fixtures.csv (primary)
    fdco_fixtures = _fetch_fdco_fixtures_csv()

    # Step 1b: fall back to per-league season CSVs if combined fetch produced nothing
    if not fdco_fixtures:
        print("[fdco] fixtures.csv returned 0 results — falling back to season CSVs")
        fdco_fixtures = _fetch_fdco_season_csvs(limit_fdco_codes=args.leagues)

    # Step 2: optional api.football-data.org augmentation for precise kickoff times
    afd_key = os.environ.get("FOOTBALL_DATA_API_KEY", "")
    afd_fixtures: list[dict] = []
    if afd_key:
        print("[afd] FOOTBALL_DATA_API_KEY set — fetching api.football-data.org ...")
        afd_fixtures = _fetch_afd(afd_key)

    # AFD has more precise kickoff times for its 4 leagues; use it as primary where available
    if afd_fixtures:
        fixtures = _merge(afd_fixtures, fdco_fixtures)
    else:
        fixtures = fdco_fixtures

    fixtures = _dedup(fixtures)
    fixtures.sort(key=lambda x: x["kickoff_utc"])

    league_counts = {}
    for f in fixtures:
        league_counts[f["league"]] = league_counts.get(f["league"], 0) + 1
    summary = ", ".join(f"{l}:{n}" for l, n in sorted(league_counts.items()))
    print(f"[calendar] Total: {len(fixtures)} fixtures | {summary}")

    if args.dry_run:
        print("[calendar] Dry-run: not writing")
        return

    _CALENDAR_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fixtures": fixtures,
    }
    _CALENDAR_PATH.write_text(json.dumps(data, indent=2))
    print(f"[calendar] Written to {_CALENDAR_PATH}")


if __name__ == "__main__":
    main()
