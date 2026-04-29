"""Downloads Premier League match data from football-data.co.uk."""

import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "raw"

BASE_URL = "https://www.football-data.co.uk/mmz4281/{season}/E0.csv"

BASE_URL_LEAGUE = "https://www.football-data.co.uk/mmz4281/{season}/{fd_code}.csv"

# All football leagues supported by the scanner, mapped to football-data.co.uk codes
LEAGUES = {
    "soccer_epl":                  {"fd_code": "E0", "label": "EPL"},
    "soccer_germany_bundesliga":   {"fd_code": "D1", "label": "Bundesliga"},
    "soccer_italy_serie_a":        {"fd_code": "I1", "label": "Serie A"},
    "soccer_efl_champ":            {"fd_code": "E1", "label": "Championship"},
    "soccer_france_ligue_one":     {"fd_code": "F1", "label": "Ligue 1"},
    "soccer_germany_bundesliga2":  {"fd_code": "D2", "label": "Bundesliga 2"},
}

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


def download_league(sport_key: str, since: str = "1415", force: bool = False) -> list[Path]:
    """Download historical match data for a specific league."""
    league = LEAGUES[sport_key]
    fd_code = league["fd_code"]
    label = league["label"]
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    idx = SEASONS.index(since)
    paths = []
    for season in SEASONS[idx:]:
        dest = DATA_DIR / f"{fd_code}_{season}.csv"
        if dest.exists() and not force:
            paths.append(dest)
            continue
        url = BASE_URL_LEAGUE.format(season=season, fd_code=fd_code)
        try:
            print(f"  [{label}] {season_label(season)} ... ", end="", flush=True)
            urllib.request.urlretrieve(url, dest)
            print("done")
            paths.append(dest)
        except Exception as e:
            print(f"ERROR: {e}")
    return paths


def download_all_leagues(since: str = "1415", force: bool = False) -> dict[str, list[Path]]:
    """Download data for all supported leagues."""
    results = {}
    for sport_key in LEAGUES:
        if sport_key == "soccer_epl":
            continue  # EPL handled by existing download_all()
        print(f"\nDownloading {LEAGUES[sport_key]['label']}...")
        results[sport_key] = download_league(sport_key, since=since, force=force)
    return results


if __name__ == "__main__":
    download_all(since="1415")
