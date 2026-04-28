"""
Daily EPL value bet scanner with ntfy.sh push notifications.
Run with:
    ODDS_API_KEY=xxx python3 scripts/scan_odds.py
"""

import json
import os
import urllib.request
import urllib.parse
import urllib.error
import statistics
from datetime import datetime, timezone

API_KEY = os.environ.get("ODDS_API_KEY", "")
if not API_KEY:
    raise RuntimeError("ODDS_API_KEY environment variable not set.")

NTFY_TOPIC = "robert-epl-bets-m4x9k"  # your private ntfy.sh topic
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

BASE_URL = "https://api.the-odds-api.com/v4"
SPORT = "soccer_epl"
MIN_EDGE = 0.03
MIN_BOOKS = 5
BANKROLL = 1000.0

SIDE_LABEL = {"H": "HOME", "D": "DRAW", "A": "AWAY"}


def fetch_odds():
    params = urllib.parse.urlencode({
        "apiKey": API_KEY,
        "regions": "uk,eu",
        "markets": "h2h",
        "oddsFormat": "decimal",
    })
    with urllib.request.urlopen(f"{BASE_URL}/sports/{SPORT}/odds/?{params}", timeout=15) as r:
        remaining = r.headers.get("X-Requests-Remaining", "?")
        return json.loads(r.read()), remaining


def find_value_bets(events):
    bets = []
    for ev in events:
        home, away, commence = ev["home_team"], ev["away_team"], ev["commence_time"]
        impl = {"H": [], "D": [], "A": []}
        book_list = []

        for b in ev.get("bookmakers", []):
            for m in b.get("markets", []):
                if m["key"] != "h2h":
                    continue
                oc = {o["name"]: o["price"] for o in m["outcomes"]}
                oh, od, oa = oc.get(home), oc.get("Draw"), oc.get(away)
                if oh and od and oa and all(x > 1.0 for x in [oh, od, oa]):
                    impl["H"].append(1 / oh)
                    impl["D"].append(1 / od)
                    impl["A"].append(1 / oa)
                    book_list.append({"book": b["key"], "H": oh, "D": od, "A": oa})

        if len(book_list) < MIN_BOOKS:
            continue

        cons = {s: statistics.mean(impl[s]) for s in "HDA"}

        for b in book_list:
            for s in "HDA":
                edge = cons[s] - 1 / b[s]
                if edge >= MIN_EDGE and 1.2 <= b[s] <= 15.0:
                    bets.append({
                        "commence": commence,
                        "home": home,
                        "away": away,
                        "side": s,
                        "book": b["book"],
                        "odds": b[s],
                        "impl": round(1 / b[s], 4),
                        "cons": round(cons[s], 4),
                        "edge": round(edge, 4),
                    })

    bets.sort(key=lambda x: x["edge"], reverse=True)
    seen, out = set(), []
    for vb in bets:
        k = (vb["home"], vb["away"], vb["side"])
        if k not in seen:
            seen.add(k)
            out.append(vb)
    return out


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
        print(f"[ntfy] Notification sent to topic '{NTFY_TOPIC}'")
    except urllib.error.URLError as e:
        print(f"[ntfy] Failed to send notification: {e}")


def format_bet(vb: dict) -> str:
    dt = datetime.fromisoformat(vb["commence"].replace("Z", "+00:00")).strftime("%a %d %b %H:%M UTC")
    kelly = max(0, min(0.5 * (vb["cons"] * vb["odds"] - 1) / (vb["odds"] - 1), 0.05))
    stake = round(kelly * BANKROLL, 2)
    return (
        f"{vb['home']} vs {vb['away']} [{SIDE_LABEL[vb['side']]}]\n"
        f"  {vb['book']} @ {vb['odds']} | Edge {vb['edge']:.1%} | Stake £{stake}\n"
        f"  {dt}"
    )


def main():
    events, remaining = fetch_odds()
    bets = find_value_bets(events)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    avg_books = sum(len(e.get("bookmakers", [])) for e in events) // max(len(events), 1)

    print(f"=== EPL Value Bets === {now}")
    print(f"Fixtures: {len(events)} | Avg books: {avg_books} | API quota left: {remaining}")
    print(f"Edge >= {MIN_EDGE:.0%} bets found: {len(bets)}")
    print()

    if not bets:
        print("No value bets today.")
        notify(
            title="EPL Bets - No value today",
            message=f"Scanned {len(events)} fixtures across {avg_books} bookmakers. No edge >= 3% found.",
            priority="low",
        )
        return

    # Print full detail to stdout (visible in routines UI)
    for vb in bets:
        dt = datetime.fromisoformat(vb["commence"].replace("Z", "+00:00")).strftime("%a %d %b %H:%M UTC")
        kelly = max(0, min(0.5 * (vb["cons"] * vb["odds"] - 1) / (vb["odds"] - 1), 0.05))
        stake = round(kelly * BANKROLL, 2)
        print(f"BET: {vb['home']} vs {vb['away']} [{SIDE_LABEL[vb['side']]}]")
        print(f"  Bookmaker : {vb['book']} @ {vb['odds']}")
        print(f"  Edge      : {vb['edge']:.1%}  (consensus {vb['cons']:.1%} vs implied {vb['impl']:.1%})")
        print(f"  Kick-off  : {dt}")
        print(f"  Stake     : £{stake}  (half-Kelly, £{BANKROLL:.0f} bankroll)")
        print()

    # Push notification — title shows count, body lists all bets
    title = f"EPL Bets - {len(bets)} value bet{'s' if len(bets) > 1 else ''} today"
    message = "\n\n".join(format_bet(vb) for vb in bets)
    notify(title=title, message=message, priority="high")


if __name__ == "__main__":
    main()
