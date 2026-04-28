"""
Weekly sports discovery script.
Scans all active sports on The Odds API, checks bookmaker coverage and
value bet potential, reports any new candidates worth adding to the scanner.

Run with: ODDS_API_KEY=xxx python3 scripts/check_sports.py
Quota cost: 1 free call (sports list) + 2 units per new sport checked.
"""

import json
import os
import statistics
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_KEY = os.environ.get("ODDS_API_KEY", "")
if not API_KEY:
    raise RuntimeError("ODDS_API_KEY environment variable not set.")

NTFY_TOPIC = "robert-epl-bets-m4x9k"
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"
BASE_URL = "https://api.the-odds-api.com/v4"

# Sports already in the main scanner — skip these
KNOWN_SPORTS = {
    "soccer_epl",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "basketball_nba",
}

# Min avg bookmakers to be worth including
MIN_AVG_BOOKS = 20
# Min value bets found to flag as a candidate
MIN_VALUE_BETS = 1
MIN_EDGE = 0.03

# Cache file — tracks sports we've already evaluated so we don't recheck every week
CACHE_FILE = Path(__file__).parent.parent / "logs" / "sports_cache.json"


def api_get(path: str, params: dict, count_quota: bool = True) -> tuple:
    params["apiKey"] = API_KEY
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=15) as r:
        remaining = r.headers.get("X-Requests-Remaining", "?")
        return json.loads(r.read()), remaining


def load_cache() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def save_cache(cache: dict):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


def get_active_sports() -> list[dict]:
    data, _ = api_get("/sports/", {"all": "false"})
    return [s for s in data if s.get("active")]


def check_sport_coverage(sport_key: str) -> tuple[int, float, int, str]:
    """Returns (n_fixtures, avg_books, n_value_bets, quota_remaining)."""
    try:
        data, remaining = api_get(
            f"/sports/{sport_key}/odds/",
            {"regions": "uk,eu", "markets": "h2h", "oddsFormat": "decimal"},
        )
    except urllib.error.HTTPError:
        return 0, 0.0, 0, "?"

    if not data:
        return 0, 0.0, 0, "?"

    book_counts = [len(ev.get("bookmakers", [])) for ev in data]
    avg_books = statistics.mean(book_counts) if book_counts else 0
    n_fixtures = len(data)

    # Quick value bet scan
    n_value = 0
    for ev in data:
        impl: dict[str, list] = {}
        book_list = []
        home, away = ev["home_team"], ev["away_team"]
        for b in ev.get("bookmakers", []):
            for m in b.get("markets", []):
                if m["key"] != "h2h":
                    continue
                oc = {o["name"]: o["price"] for o in m["outcomes"]}
                entries = {"H": oc.get(home), "A": oc.get(away)}
                if oc.get("Draw"):
                    entries["D"] = oc["Draw"]
                if not all(v and v > 1.0 for v in entries.values()):
                    continue
                for side, odds in entries.items():
                    impl.setdefault(side, []).append(1 / odds)
                book_list.append({"book": b["key"], **entries})

        if len(book_list) < 10:
            continue
        cons = {s: statistics.mean(vals) for s, vals in impl.items()}
        for b in book_list:
            for side, odds in b.items():
                if side == "book" or side not in cons:
                    continue
                if cons[side] - 1 / odds >= MIN_EDGE and 1.2 <= odds <= 15.0:
                    n_value += 1
                    break

    return n_fixtures, round(avg_books, 1), n_value, remaining


def notify(title: str, message: str, priority: str = "default"):
    try:
        req = urllib.request.Request(
            NTFY_URL,
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Tags": "mag"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.URLError as e:
        print(f"[ntfy] Failed: {e}")


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"=== Weekly Sports Discovery === {now}\n")

    sports = get_active_sports()
    cache = load_cache()

    # Filter: skip known sports, skip already-evaluated with no value
    to_check = []
    for s in sports:
        key = s["key"]
        if key in KNOWN_SPORTS:
            continue
        # Skip if checked in last 14 days with poor coverage
        if key in cache:
            cached = cache[key]
            if cached.get("avg_books", 0) < MIN_AVG_BOOKS and cached.get("n_value", 0) == 0:
                continue  # not worth rechecking yet
        to_check.append(s)

    print(f"Active sports: {len(sports)} | New/unchecked to probe: {len(to_check)}")
    print(f"Quota cost: ~{len(to_check) * 2} units\n")

    candidates = []
    quota_remaining = "?"

    for s in to_check:
        key, title = s["key"], s.get("title", s["key"])
        n_fix, avg_books, n_val, quota_remaining = check_sport_coverage(key)

        cache[key] = {
            "title": title,
            "checked_at": now,
            "n_fixtures": n_fix,
            "avg_books": avg_books,
            "n_value": n_val,
        }

        status = "candidate!" if avg_books >= MIN_AVG_BOOKS and n_val >= MIN_VALUE_BETS else "skip"
        print(f"  {title:<35} {n_fix:>3} fixtures  {avg_books:>5.1f} avg books  "
              f"{n_val:>3} value bets  [{status}]")

        if avg_books >= MIN_AVG_BOOKS and n_val >= MIN_VALUE_BETS:
            candidates.append((title, key, n_fix, avg_books, n_val))

    save_cache(cache)
    print(f"\nAPI quota remaining: {quota_remaining}")

    if not candidates:
        print("\nNo new sports candidates found this week.")
        notify(
            "Sports Check - No new candidates",
            f"Checked {len(to_check)} sports. None meet the coverage threshold yet.",
            priority="low",
        )
        return

    print(f"\n*** {len(candidates)} NEW CANDIDATE(S) ***")
    lines = []
    for title, key, n_fix, avg_books, n_val in candidates:
        print(f"  {title} ({key})")
        print(f"    {n_fix} fixtures | {avg_books} avg books | {n_val} value bets")
        lines.append(f"{title}: {n_fix} fixtures, {avg_books} avg books, {n_val} value bets")

    notify(
        f"Sports Check - {len(candidates)} new candidate(s)!",
        "Consider adding to scanner:\n\n" + "\n".join(lines),
        priority="high",
    )


if __name__ == "__main__":
    main()
