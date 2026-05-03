"""Ingest upcoming fixtures from football-data.co.uk and (optionally) api.football-data.org.

Writes logs/fixture_calendar.json for use by src/data/fixture_calendar.py.
Both Pi and WSL run this script — no DB dependency required.

Sources:
  1. FDCO fixtures.csv (free, all leagues, includes kickoff time in UK local time)
  2. api.football-data.org (optional; set FOOTBALL_DATA_API_KEY; EPL/BL/SA/L1 only,
     10-day window per call — free-tier date-range limit)

Usage:
    python3 scripts/ingest_fixtures.py [--dry-run] [--allow-empty] [--leagues E0 D1 ...]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
import unicodedata
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
_CALENDAR_PATH = _ROOT / "logs" / "fixture_calendar.json"
_RAW_DIR = _ROOT / "data" / "raw"
_FORWARD_WEEKS = 8
# api.football-data.org free tier rejects date ranges wider than ~10 days
_AFD_WINDOW_DAYS = 10

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


def _current_season() -> str:
    """FDCO season code derived from today: 'YYZZ' (e.g. '2526' for 2025/26).
    Season starts in July, so month >= 7 → use current year as start year.
    """
    today = _today()
    y = today.year % 100
    if today.month >= 7:
        return f"{y:02d}{(y + 1) % 100:02d}"
    return f"{(y - 1) % 100:02d}{y:02d}"


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


def _norm_name(name: str) -> str:
    """NFD-fold accents, lowercase, strip ' FC'/' AFC' suffix.

    Handles EPL naming differences between sources (AFD 'Arsenal FC' vs FDCO
    'Arsenal').  Continental clubs whose FDCO names are abbreviated (e.g. FDCO
    'Dortmund' vs AFD 'Borussia Dortmund') may still produce duplicate rows
    when AFD is enabled for those leagues; acceptable for the current state
    where FOOTBALL_DATA_API_KEY is not set.
    """
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = name.strip().lower()
    for suffix in (" fc", " afc"):
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
            break
    return name


def _fixture_key(f: dict) -> tuple | None:
    """Dedup key: (sport_key, kickoff_date, norm_home, norm_away).

    Keys on the date part of kickoff_utc (not the full timestamp) so that AFD
    and FDCO rows for the same fixture can be matched even if their reported
    kickoff minute differs slightly.
    """
    ko = f.get("kickoff_utc", "")
    date_part = ko[:10] if len(ko) >= 10 else ""
    if not date_part:
        return None
    home = _norm_name(f.get("home", ""))
    away = _norm_name(f.get("away", ""))
    if not home or not away:
        return None
    return (f.get("sport_key", ""), date_part, home, away)


def _dedup(fixtures: list[dict]) -> list[dict]:
    """Remove duplicates by fixture key; last entry wins on collision."""
    seen: dict[tuple, dict] = {}
    for f in fixtures:
        key = _fixture_key(f)
        if key:
            seen[key] = f
    return list(seen.values())


def _merge(primary: list[dict], secondary: list[dict]) -> list[dict]:
    """Union-merge: keep all fixtures from both sources, preferring primary on collision.

    Unlike league-level winner-take-all, this preserves secondary fixtures
    beyond the primary's time horizon (e.g. FDCO at 8 weeks vs AFD at 10 days).
    Primary rows (AFD, more precise kickoff times) overwrite secondary rows for
    the same fixture via last-wins dedup.
    """
    return _dedup(secondary + primary)


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
        })

    print(f"[fdco] fixtures.csv: {len(fixtures)} upcoming fixtures across "
          f"{len({f['sport_key'] for f in fixtures})} leagues")
    return fixtures


def _fetch_fdco_season_csvs(limit_fdco_codes: list[str] | None = None) -> list[dict]:
    """Fall back: parse upcoming fixtures from current-season per-league CSVs.

    Used when fixtures.csv is unavailable. Season CSVs include kickoff times
    for fixtures already published in the current season window.
    """
    today = _today()
    cutoff = today + timedelta(weeks=_FORWARD_WEEKS)
    season = _current_season()

    leagues_to_check = {
        k: v for k, v in LEAGUES.items()
        if limit_fdco_codes is None or v["fd_code"] in limit_fdco_codes
    }

    fixtures: list[dict] = []
    for sport_key, meta in leagues_to_check.items():
        fd_code = meta["fd_code"]
        csv_path = _RAW_DIR / f"{fd_code}_{season}.csv"
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
                    })
        except Exception as e:
            print(f"[fdco] {fd_code}: season CSV parse error: {e}")

    print(f"[fdco] season CSVs ({season}): {len(fixtures)} upcoming fixtures")
    return fixtures


def _fetch_afd(api_key: str) -> list[dict]:
    """Fetch scheduled fixtures from api.football-data.org (free tier, 4 leagues).

    Free tier caps the date range per request; we use a 10-day window.
    """
    today = _today()
    date_to = today + timedelta(days=_AFD_WINDOW_DAYS)

    fixtures: list[dict] = []
    for i, (sport_key, comp_code) in enumerate(_AFD_CODES.items()):
        if i > 0:
            time.sleep(7)  # free tier: 10 req/min; skip before first call
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
            })
        print(f"[afd] {comp_code}: {len(matches)} fixtures (next {_AFD_WINDOW_DAYS}d)")

    return fixtures


def _write_calendar(
    fixtures: list[dict], now: datetime, *, allow_empty: bool
) -> None:
    """Write logs/fixture_calendar.json atomically via tmp+rename.

    Refuses to overwrite the existing calendar with an empty list unless
    allow_empty=True, preventing a transient FDCO failure from poisoning
    the canary for the full week until the next scheduled run.
    """
    if not fixtures and not allow_empty:
        try:
            existing_count = len(
                json.loads(_CALENDAR_PATH.read_text()).get("fixtures", [])
            )
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            existing_count = 0
        if existing_count:
            print(
                f"[calendar] WARN: ingest returned 0 fixtures. "
                f"Preserving existing {existing_count}-fixture calendar. "
                f"Use --allow-empty to force overwrite."
            )
        else:
            print(
                "[calendar] WARN: ingest returned 0 fixtures and no existing calendar. "
                "Use --allow-empty to write empty calendar."
            )
        return

    _CALENDAR_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fixtures": fixtures,
    }
    tmp = _CALENDAR_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(_CALENDAR_PATH)
    print(f"[calendar] Written {len(fixtures)} fixtures to {_CALENDAR_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Parse but don't write")
    parser.add_argument(
        "--allow-empty", action="store_true",
        help="Write even when 0 fixtures found (bootstrapping / end-of-season)"
    )
    parser.add_argument(
        "--leagues", nargs="+", default=None,
        help="Limit to FDCO codes (e.g. E0 D1). Default: all.",
    )
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    print(f"=== Fixture calendar ingest === {now.strftime('%Y-%m-%d %H:%M UTC')}")

    # Step 1: FDCO combined fixtures.csv (primary for FDCO-only path)
    fdco_fixtures = _fetch_fdco_fixtures_csv()

    # Step 1b: fall back to per-league season CSVs if combined fetch produced nothing
    if not fdco_fixtures:
        print("[fdco] fixtures.csv returned 0 results — falling back to season CSVs")
        fdco_fixtures = _fetch_fdco_season_csvs(limit_fdco_codes=args.leagues)

    # Step 2: optional api.football-data.org augmentation for precise kickoff times.
    # AFD covers only 10 days; merge unions both sources so FDCO's 8-week window
    # is preserved for fixtures beyond the AFD horizon.
    afd_key = os.environ.get("FOOTBALL_DATA_API_KEY", "")
    afd_fixtures: list[dict] = []
    if afd_key:
        print(f"[afd] FOOTBALL_DATA_API_KEY set — fetching api.football-data.org "
              f"(next {_AFD_WINDOW_DAYS}d) ...")
        afd_fixtures = _fetch_afd(afd_key)

    if afd_fixtures:
        fixtures = _merge(afd_fixtures, fdco_fixtures)
    else:
        fixtures = _dedup(fdco_fixtures)

    fixtures.sort(key=lambda x: x["kickoff_utc"])

    league_counts: dict[str, int] = {}
    for f in fixtures:
        league_counts[f["league"]] = league_counts.get(f["league"], 0) + 1
    summary = ", ".join(f"{lg}:{n}" for lg, n in sorted(league_counts.items()))
    print(f"[calendar] Total: {len(fixtures)} fixtures | {summary or '(none)'}")

    if args.dry_run:
        print("[calendar] Dry-run: not writing")
        return

    _write_calendar(fixtures, now, allow_empty=args.allow_empty)


if __name__ == "__main__":
    main()
