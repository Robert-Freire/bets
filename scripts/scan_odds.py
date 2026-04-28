"""
Daily multi-sport value bet scanner using the Kaunitz consensus strategy.
Reads ODDS_API_KEY from environment. Run with:
    ODDS_API_KEY=xxx python3 scripts/scan_odds.py
"""

import csv
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
MIN_EDGE = 0.03
BANKROLL = 1000.0

# Bookmakers with a UK Gambling Commission licence — the only ones usable from the UK.
# Consensus is still computed across ALL books (better signal), but value bets are
# only flagged when a UK-licensed book is the one offering the edge.
UK_LICENSED_BOOKS = {
    "betfair_ex_uk",   # Betfair Exchange
    "betfair_sb_uk",   # Betfair Sportsbook
    "smarkets",        # Smarkets Exchange
    "matchbook",       # Matchbook Exchange
    "betfred_uk",      # Betfred
    "williamhill",     # William Hill
    "coral",           # Coral
    "ladbrokes_uk",    # Ladbrokes
    "skybet",          # Sky Bet
    "paddypower",      # Paddy Power
    "boylesports",     # BoyleSports
    "betvictor",       # BetVictor
    "betway",          # Betway
    "leovegas",        # LeoVegas
    "casumo",          # Casumo
    "virginbet",       # Virgin Bet
    "livescorebet",    # LiveScore Bet
    "sport888",        # 888Sport
    "grosvenor",       # Grosvenor
}

# Fixed sports to always scan
FIXED_SPORTS = [
    ("soccer_epl",                "EPL",          20),   # min_books
    ("soccer_germany_bundesliga", "Bundesliga",   20),
    ("soccer_italy_serie_a",      "Serie A",      20),
    ("soccer_efl_champ",          "Championship", 25),
    ("soccer_france_ligue_one",   "Ligue 1",      20),
    ("soccer_germany_bundesliga2","Bundesliga 2", 20),
    ("basketball_nba",            "NBA",          20),
]

# Min books per sport — below this the consensus is unreliable
DEFAULT_MIN_BOOKS = 15
SPORT_MIN_BOOKS = {k: v for k, _, v in FIXED_SPORTS}
SPORT_MIN_EDGE = {}


def api_get(path: str, params: dict) -> tuple[list | dict, str]:
    params["apiKey"] = API_KEY
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=15) as r:
        remaining = r.headers.get("X-Requests-Remaining", "?")
        return json.loads(r.read()), remaining


def get_active_tennis_sports() -> list[tuple[str, str, int]]:
    """Fetch active tennis tournaments dynamically (free call — no quota cost)."""
    data, _ = api_get("/sports/", {"all": "false"})
    tennis = []
    for s in data:
        if s.get("active") and s["key"].startswith("tennis_"):
            label = s.get("title", s["key"])
            tennis.append((s["key"], label, 15))
    return tennis


def fetch_odds(sport_key: str) -> tuple[list, str]:
    data, remaining = api_get(
        f"/sports/{sport_key}/odds/",
        {"regions": "uk,eu", "markets": "h2h", "oddsFormat": "decimal"},
    )
    return data, remaining


def find_value_bets(events: list, sport_key: str) -> list[dict]:
    min_books = SPORT_MIN_BOOKS.get(sport_key, DEFAULT_MIN_BOOKS)
    min_edge = SPORT_MIN_EDGE.get(sport_key, MIN_EDGE)
    bets = []

    for ev in events:
        home, away, commence = ev["home_team"], ev["away_team"], ev["commence_time"]
        impl: dict[str, list] = {}
        book_list = []

        for b in ev.get("bookmakers", []):
            for m in b.get("markets", []):
                if m["key"] != "h2h":
                    continue
                oc = {o["name"]: o["price"] for o in m["outcomes"]}
                entries = {
                    "H": oc.get(home),
                    "A": oc.get(away),
                }
                draw = oc.get("Draw")
                if draw:
                    entries["D"] = draw

                if not all(v and v > 1.0 for v in entries.values()):
                    continue

                for side, odds in entries.items():
                    impl.setdefault(side, []).append(1 / odds)
                book_list.append({"book": b["key"], **entries})

        if len(book_list) < min_books:
            continue

        cons = {s: statistics.mean(vals) for s, vals in impl.items()}
        n_books = len(book_list)
        confidence = "HIGH" if n_books >= 30 else ("MED" if n_books >= 20 else "LOW")

        for b in book_list:
            if b["book"] not in UK_LICENSED_BOOKS:
                continue  # consensus uses all books, but only flag UK-accessible ones
            for side, odds in b.items():
                if side == "book" or side not in cons:
                    continue
                edge = cons[side] - 1 / odds
                if edge >= min_edge and 1.2 <= odds <= 15.0:
                    bets.append({
                        "commence": commence,
                        "home": home,
                        "away": away,
                        "side": side,
                        "book": b["book"],
                        "odds": odds,
                        "impl": round(1 / odds, 4),
                        "cons": round(cons[side], 4),
                        "edge": round(edge, 4),
                        "n_books": n_books,
                        "confidence": confidence,
                    })

    # Deduplicate: keep best edge per fixture + side
    bets.sort(key=lambda x: x["edge"], reverse=True)
    seen: set = set()
    out = []
    for vb in bets:
        k = (vb["home"], vb["away"], vb["side"])
        if k not in seen:
            seen.add(k)
            out.append(vb)
    return out


def format_bet_line(vb: dict) -> str:
    dt = datetime.fromisoformat(vb["commence"].replace("Z", "+00:00")).strftime("%a %d %b %H:%M UTC")
    kelly = max(0, min(0.5 * (vb["cons"] * vb["odds"] - 1) / (vb["odds"] - 1), 0.05))
    stake = round(kelly * BANKROLL, 2)
    side = {"H": "HOME", "D": "DRAW", "A": "AWAY"}.get(vb["side"], vb["side"])
    return (
        f"{vb['home']} vs {vb['away']} [{side}]\n"
        f"  {vb['book']} @ {vb['odds']} | Edge {vb['edge']:.1%} | "
        f"{vb['n_books']} books [{vb['confidence']}] | Stake £{stake}\n"
        f"  {dt}"
    )


def notify(title: str, message: str, priority: str = "default"):
    try:
        req = urllib.request.Request(
            NTFY_URL,
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": "soccer,money_with_wings",
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
        print(f"[ntfy] Sent: '{title}'")
    except urllib.error.URLError as e:
        print(f"[ntfy] Failed: {e}")


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"=== Multi-Sport Value Bet Scanner === {now}\n")

    # Build sport list: fixed + dynamic tennis
    tennis_sports = get_active_tennis_sports()
    all_sports = FIXED_SPORTS + tennis_sports

    quota_remaining = "?"
    all_bets: list[dict] = []
    sport_summary: list[str] = []

    for sport_key, label, _ in all_sports:
        try:
            events, quota_remaining = fetch_odds(sport_key)
            bets = find_value_bets(events, sport_key)
            all_bets.extend([{**b, "sport": label} for b in bets])
            flag = f"  ** {len(bets)} value bet(s)!" if bets else ""
            print(f"  {label:<28} {len(events):>3} fixtures, "
                  f"{sum(len(e.get('bookmakers',[])) for e in events)//max(len(events),1):>2} avg books"
                  f"{flag}")
            if bets:
                sport_summary.append(f"{label}: {len(bets)} bet(s)")
        except Exception as e:
            print(f"  {label:<28} ERROR: {e}")

    print(f"\nAPI quota remaining: {quota_remaining}")
    print(f"Total value bets (>= 3% edge): {len(all_bets)}\n")

    if not all_bets:
        print("No value bets found today across all sports.")
        notify("Bets - No value today", f"Scanned {len(all_sports)} sports. No edge >= 3% found.", priority="low")
        return

    # Print full detail
    side_labels = {"H": "HOME", "D": "DRAW", "A": "AWAY"}
    current_sport = None
    for vb in sorted(all_bets, key=lambda x: (x["sport"], -x["edge"])):
        if vb["sport"] != current_sport:
            current_sport = vb["sport"]
            print(f"--- {current_sport} ---")
        dt = datetime.fromisoformat(vb["commence"].replace("Z", "+00:00")).strftime("%a %d %b %H:%M UTC")
        kelly = max(0, min(0.5 * (vb["cons"] * vb["odds"] - 1) / (vb["odds"] - 1), 0.05))
        stake = round(kelly * BANKROLL, 2)
        side = side_labels.get(vb["side"], vb["side"])
        print(f"  {vb['home']} vs {vb['away']} [{side}]")
        print(f"    {vb['book']} @ {vb['odds']} | Edge {vb['edge']:.1%} | "
              f"Consensus {vb['cons']:.1%} | {vb['n_books']} books [{vb['confidence']}] | "
              f"Stake £{stake} | {dt}")
    print()

    # Write bets to CSV log
    log_file = Path(__file__).parent.parent / "logs" / "bets.csv"
    write_header = not log_file.exists()
    with open(log_file, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "scanned_at", "sport", "home", "away", "kickoff",
            "side", "book", "odds", "edge", "consensus", "n_books", "confidence", "stake", "result"
        ])
        if write_header:
            writer.writeheader()
        for vb in all_bets:
            dt = datetime.fromisoformat(vb["commence"].replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
            kelly = max(0, min(0.5 * (vb["cons"] * vb["odds"] - 1) / (vb["odds"] - 1), 0.05))
            writer.writerow({
                "scanned_at": now,
                "sport": vb["sport"],
                "home": vb["home"],
                "away": vb["away"],
                "kickoff": dt,
                "side": {"H": "HOME", "D": "DRAW", "A": "AWAY"}.get(vb["side"], vb["side"]),
                "book": vb["book"],
                "odds": vb["odds"],
                "edge": round(vb["edge"], 4),
                "consensus": round(vb["cons"], 4),
                "n_books": vb["n_books"],
                "confidence": vb["confidence"],
                "stake": round(kelly * BANKROLL, 2),
                "result": "",  # filled in manually after the match
            })
    print(f"[log] {len(all_bets)} bets appended to logs/bets.csv")

    # Split by confidence and send separate notifications
    high = [vb for vb in all_bets if vb["confidence"] == "HIGH"]
    med  = [vb for vb in all_bets if vb["confidence"] == "MED"]
    low  = [vb for vb in all_bets if vb["confidence"] == "LOW"]

    if high:
        notify(
            title=f"Bets HIGH - {len(high)} bet{'s' if len(high) > 1 else ''} (>=30 books)",
            message="\n\n".join(format_bet_line(vb) for vb in high),
            priority="high",
        )
    if med:
        notify(
            title=f"Bets MED - {len(med)} bet{'s' if len(med) > 1 else ''} (20-29 books)",
            message="\n\n".join(format_bet_line(vb) for vb in med),
            priority="default",
        )
    if low:
        notify(
            title=f"Bets LOW - {len(low)} bet{'s' if len(low) > 1 else ''} (<20 books)",
            message="\n\n".join(format_bet_line(vb) for vb in low),
            priority="low",
        )


if __name__ == "__main__":
    main()
