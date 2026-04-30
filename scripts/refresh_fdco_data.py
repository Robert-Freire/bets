"""
Bulk-download football-data.co.uk CSVs for leagues not already in data/raw/.

Usage:
    python3 scripts/refresh_fdco_data.py [--leagues L1 L2 ...] [--force]

Downloads {LEAGUE}_{YYMM}.csv files from:
    https://www.football-data.co.uk/mmz4281/{YYMM}/{LEAGUE}.csv

Skips existing files unless --force is passed. Skips 404s gracefully.
"""

import argparse
import sys
import time
from pathlib import Path

import requests

_RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

NEW_LEAGUES = ["B1", "E2", "E3", "EC", "F2", "G1", "I2", "N1", "P1",
               "SC0", "SC1", "SC2", "SC3", "SP1", "SP2", "T1"]

SEASONS = ["1415", "1516", "1617", "1718", "1819", "1920",
           "2021", "2122", "2223", "2324", "2425", "2526"]

BASE_URL = "https://www.football-data.co.uk/mmz4281/{season}/{league}.csv"


def download_all(leagues: list[str], force: bool = False) -> dict:
    """Download all season CSVs for the given leagues. Returns a summary dict."""
    downloaded, skipped_exists, skipped_404, errors = [], [], [], []

    for league in leagues:
        for season in SEASONS:
            dest = _RAW_DIR / f"{league}_{season}.csv"
            if dest.exists() and not force:
                skipped_exists.append(str(dest.name))
                continue

            url = BASE_URL.format(season=season, league=league)
            try:
                resp = requests.get(url, timeout=15)
            except requests.RequestException as exc:
                errors.append((dest.name, str(exc)))
                continue

            if resp.status_code == 404:
                skipped_404.append(f"{league}_{season}")
                continue

            if resp.status_code != 200:
                errors.append((dest.name, f"HTTP {resp.status_code}"))
                continue

            content = resp.content
            if len(content) < 100:
                # Empty or near-empty response — treat as 404
                skipped_404.append(f"{league}_{season} (empty body)")
                continue

            dest.write_bytes(content)
            downloaded.append(dest.name)
            print(f"  ✓ {dest.name} ({len(content):,} bytes)")
            time.sleep(0.15)  # polite rate-limiting

    return {
        "downloaded": downloaded,
        "skipped_exists": skipped_exists,
        "skipped_404": skipped_404,
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--leagues", nargs="+", default=NEW_LEAGUES,
                        help="League codes to download (default: all 16 new leagues)")
    parser.add_argument("--force", action="store_true",
                        help="Re-download even if file already exists")
    args = parser.parse_args()

    _RAW_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {len(args.leagues)} leagues × {len(SEASONS)} seasons "
          f"→ {_RAW_DIR}")

    summary = download_all(args.leagues, force=args.force)

    print(f"\nDownloaded:      {len(summary['downloaded'])}")
    print(f"Skipped (exist): {len(summary['skipped_exists'])}")
    print(f"Skipped (404):   {len(summary['skipped_404'])}")
    if summary["errors"]:
        print(f"Errors:          {len(summary['errors'])}")
        for name, msg in summary["errors"]:
            print(f"  {name}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
