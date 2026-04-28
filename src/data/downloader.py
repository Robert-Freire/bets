"""Downloads Premier League match data from football-data.co.uk."""

import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "raw"

BASE_URL = "https://www.football-data.co.uk/mmz4281/{season}/E0.csv"

# All available EPL seasons (format: SSSS = start+end year digits)
SEASONS = [
    "9394", "9495", "9596", "9697", "9798", "9899",
    "9900", "0001", "0102", "0203", "0304", "0405",
    "0506", "0607", "0708", "0809", "0910", "1011",
    "1112", "1213", "1314", "1415", "1516", "1617",
    "1718", "1819", "1920", "2021", "2122", "2223",
    "2324", "2425", "2526",
]

# Seasons with xG data from Understat (2014/15 onward)
XG_SEASONS = [s for s in SEASONS if int(s[:2]) >= 14 or (int(s[:2]) <= 5 and int(s[:2]) >= 0 and int(s[2:]) <= 5)]

def season_label(code: str) -> str:
    """Convert '2324' -> '2023/24'."""
    start = int(code[:2])
    end = int(code[2:])
    start_year = (1900 + start) if start >= 93 else (2000 + start)
    return f"{start_year}/{str(start_year + 1)[-2:]}"


def download_season(season: str, force: bool = False) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    dest = DATA_DIR / f"E0_{season}.csv"
    if dest.exists() and not force:
        return dest
    url = BASE_URL.format(season=season)
    print(f"Downloading {season_label(season)} ... ", end="", flush=True)
    urllib.request.urlretrieve(url, dest)
    print("done")
    return dest


def download_all(since: str = "1415", force: bool = False) -> list[Path]:
    """Download all seasons from `since` onward."""
    idx = SEASONS.index(since)
    targets = SEASONS[idx:]
    paths = []
    for season in targets:
        try:
            paths.append(download_season(season, force=force))
        except Exception as e:
            print(f"  WARNING: could not download {season}: {e}")
    return paths


if __name__ == "__main__":
    download_all(since="1415")
