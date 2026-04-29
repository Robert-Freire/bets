"""
Daily multi-sport value bet scanner using the Kaunitz consensus strategy.
Reads ODDS_API_KEY from environment. Run with:
    ODDS_API_KEY=xxx python3 scripts/scan_odds.py
"""

import argparse
import csv
import fcntl
import json
import os
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Allow importing from src/ regardless of working directory
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from src.betting.devig import shin as _shin_devig, proportional as _proportional_devig
    _DEVIG = True
except ImportError:
    _DEVIG = False

API_KEY = os.environ.get("ODDS_API_KEY", "")
if not API_KEY:
    raise RuntimeError("ODDS_API_KEY environment variable not set.")

# CatBoost model signals cache — populated by scripts/model_signals.py
_SIGNALS_PATH = Path(__file__).parent.parent / "logs" / "model_signals.json"
_MODEL_SIGNALS: dict = {}
try:
    with open(_SIGNALS_PATH) as _f:
        _cache = json.load(_f)
        _MODEL_SIGNALS = _cache.get("signals", {})
    print(f"[model] Loaded {len(_MODEL_SIGNALS)} signals from {_SIGNALS_PATH.name} "
          f"(generated {_cache.get('generated_at', '?')[:10]})")
except FileNotFoundError:
    print("[model] No model_signals.json found — run scripts/model_signals.py to enable CatBoost indicator")
except Exception as _e:
    print(f"[model] Could not load signals: {_e}")

# Odds API team names → football-data.co.uk names (covers all scanner leagues)
_API_TO_FD = {
    # EPL
    "Manchester City":        "Man City",
    "Manchester United":      "Man United",
    "Tottenham Hotspur":      "Tottenham",
    "Newcastle United":       "Newcastle",
    "West Ham United":        "West Ham",
    "Brighton & Hove Albion": "Brighton",
    "Wolverhampton Wanderers":"Wolves",
    "Nottingham Forest":      "Nott'm Forest",
    "Sheffield United":       "Sheffield United",
    "Leicester City":         "Leicester",
    "Luton Town":             "Luton",
    # Bundesliga
    "Borussia Dortmund":      "Dortmund",
    "Bayer Leverkusen":       "Leverkusen",
    "Eintracht Frankfurt":    "Ein Frankfurt",
    "VfL Wolfsburg":          "Wolfsburg",
    "1. FC Union Berlin":     "Union Berlin",
    "SC Freiburg":            "Freiburg",
    "1. FSV Mainz 05":        "Mainz",
    "FC Augsburg":            "Augsburg",
    "VfB Stuttgart":          "Stuttgart",
    "TSG Hoffenheim":         "Hoffenheim",
    "SV Werder Bremen":       "Werder Bremen",
    "1. FC Heidenheim 1846":  "Heidenheim",
    "1. FC Heidenheim":       "Heidenheim",
    "VfL Bochum":             "Bochum",
    "FC St. Pauli":           "St Pauli",
    "Borussia Mönchengladbach": "M'gladbach",
    "Borussia Monchengladbach": "M'gladbach",
    # Bundesliga 2
    "Hamburger SV":           "Hamburg",
    "1. FC Kaiserslautern":   "Kaiserslautern",
    "FC Köln":                "FC Koln",
    "Fortuna Düsseldorf":     "Fortuna Dusseldorf",
    "Hertha BSC":             "Hertha",
    "Hannover 96":            "Hannover",
    "1. FC Nürnberg":         "Nurnberg",
    "Karlsruher SC":          "Karlsruhe",
    "SV Darmstadt 98":        "Darmstadt",
    "SpVgg Greuther Fürth":   "Greuther Furth",
    "SV Elversberg":          "Elversberg",
    "1. FC Magdeburg":        "Magdeburg",
    "SC Paderborn 07":        "Paderborn",
    "SSV Ulm 1846":           "Ulm",
    "Eintracht Braunschweig": "Braunschweig",
    "SSV Jahn Regensburg":    "Regensburg",
    # Serie A
    "AC Milan":               "Milan",
    "Inter Milan":            "Inter",
    "AS Roma":                "Roma",
    "SS Lazio":               "Lazio",
    "ACF Fiorentina":         "Fiorentina",
    "Hellas Verona":          "Verona",
    "US Monza":               "Monza",
    # Ligue 1
    "Paris Saint-Germain":    "Paris SG",
    "Paris Saint Germain":    "Paris SG",
    "Olympique de Marseille": "Marseille",
    "Olympique Lyonnais":     "Lyon",
    "AS Monaco":              "Monaco",
    "LOSC Lille":             "Lille",
    "Stade Rennais":          "Rennes",
    "OGC Nice":               "Nice",
    "RC Strasbourg":          "Strasbourg",
    "RC Lens":                "Lens",
    "FC Nantes":              "Nantes",
    "Toulouse FC":            "Toulouse",
    "FC Lorient":             "Lorient",
    "Stade de Reims":         "Reims",
    "Clermont Foot":          "Clermont",
    "Saint-Etienne":          "St Etienne",
    # Championship
    "Sheffield Wednesday":    "Sheffield Weds",
    "West Bromwich Albion":   "West Brom",
    "Oxford United":          "Oxford",
    "Preston North End":      "Preston",
    "Plymouth Argyle":        "Plymouth",
    "Blackburn Rovers":       "Blackburn",
    "Norwich City":           "Norwich",
    "Cardiff City":           "Cardiff",
    "Stoke City":             "Stoke",
    "Queens Park Rangers":    "QPR",
    "Hull City":              "Hull",
    "Derby County":           "Derby",
    "Swansea City":           "Swansea",
    "Coventry City":          "Coventry",
    "Leeds United":           "Leeds",
    "Birmingham City":        "Birmingham",
}


def _model_signal(home: str, away: str, sport_key: str, book_impl_prob: float, side: str) -> str:
    """Return signed model edge as '+0.123'/'-0.051', or '?' if no signal available."""
    if not _MODEL_SIGNALS:
        return "?"
    h = _API_TO_FD.get(home, home)
    a = _API_TO_FD.get(away, away)
    probs = _MODEL_SIGNALS.get(f"{sport_key}:{h}|{a}")
    if probs is None:
        return "?"
    edge = probs.get(side, 0.0) - book_impl_prob
    return f"{edge:+.3f}"


def _signal_is_positive(signal: str) -> bool:
    """True if model signal is a positive numeric edge (model agrees)."""
    try:
        return float(signal) > 0
    except (ValueError, TypeError):
        return signal == "agree"  # backward compat with old CSV rows


NTFY_TOPIC = "robert-epl-bets-m4x9k"
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}"

BASE_URL = "https://api.the-odds-api.com/v4"
MIN_EDGE = 0.03        # Kaunitz-only threshold (no model required)
MODEL_MIN_EDGE = 0.02  # lower threshold — only shown when model agrees
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


def get_active_tennis_sports(max_tournaments: int = 99) -> list[tuple[str, str, int]]:
    """Fetch active tennis tournaments dynamically (free call — no quota cost)."""
    data, _ = api_get("/sports/", {"all": "false"})
    tennis = []
    for s in data:
        if s.get("active") and s["key"].startswith("tennis_"):
            label = s.get("title", s["key"])
            tennis.append((s["key"], label, 15))
    return tennis[:max_tournaments]


def fetch_odds(sport_key: str) -> tuple[list, str]:
    data, remaining = api_get(
        f"/sports/{sport_key}/odds/",
        {"regions": "uk,eu", "markets": "h2h", "oddsFormat": "decimal"},
    )
    return data, remaining


def _devig_triplet(entries: dict) -> dict[str, float]:
    """
    De-vig a book's odds dict using Shin (1993). Returns fair probs keyed by side.
    Falls back to proportional if Shin fails, raw 1/odds if devig unavailable.
    """
    sides = list(entries.keys())
    raw = [1.0 / entries[s] for s in sides]
    if _DEVIG:
        try:
            fair = _shin_devig(raw)
        except Exception:
            fair = _proportional_devig(raw)
    else:
        fair = [r / sum(raw) for r in raw]
    return dict(zip(sides, fair))


def find_value_bets(events: list, sport_key: str) -> list[dict]:
    min_books = SPORT_MIN_BOOKS.get(sport_key, DEFAULT_MIN_BOOKS)
    min_edge = SPORT_MIN_EDGE.get(sport_key, MODEL_MIN_EDGE)  # scan at 2% to catch model bets
    bets = []

    for ev in events:
        home, away, commence = ev["home_team"], ev["away_team"], ev["commence_time"]
        # impl now accumulates Shin fair probs (not raw 1/odds)
        impl: dict[str, list] = {}
        book_list = []

        for b in ev.get("bookmakers", []):
            for m in b.get("markets", []):
                if m["key"] != "h2h":
                    continue
                oc = {o["name"]: o["price"] for o in m["outcomes"]}
                entries: dict[str, float] = {"H": oc.get(home), "A": oc.get(away)}
                draw = oc.get("Draw")
                if draw:
                    entries["D"] = draw

                if not all(v and v > 1.0 for v in entries.values()):
                    continue

                fair = _devig_triplet(entries)
                for side, fp in fair.items():
                    impl.setdefault(side, []).append(fp)
                book_list.append({"book": b["key"], "fair": fair, **entries})

        if len(book_list) < min_books:
            continue

        # Consensus = mean of Shin-devigged fair probs across all books
        cons = {s: statistics.mean(vals) for s, vals in impl.items()}
        n_books = len(book_list)
        confidence = "HIGH" if n_books >= 30 else ("MED" if n_books >= 20 else "LOW")

        # Pinnacle's devigged prob, used as a sharp-book anchor signal
        pinnacle_fair: dict[str, float] = next(
            (b["fair"] for b in book_list if b["book"] == "pinnacle"), {}
        )

        for b in book_list:
            if b["book"] not in UK_LICENSED_BOOKS:
                continue  # consensus uses all books, but only flag UK-accessible ones
            for side, odds in b.items():
                if side in ("book", "fair") or side not in cons:
                    continue
                # Edge: how much better does the consensus think this side is vs this book's own fair prob?
                fair_book_side = b["fair"].get(side, 1.0 / odds)
                edge = cons[side] - fair_book_side
                if edge >= min_edge and 1.2 <= odds <= 15.0:
                    impl_prob = round(1.0 / odds, 4)
                    bets.append({
                        "commence": commence,
                        "home": home,
                        "away": away,
                        "side": side,
                        "book": b["book"],
                        "odds": odds,
                        "impl": impl_prob,
                        "cons": round(cons[side], 4),
                        "edge": round(edge, 4),
                        "pinnacle_cons": round(pinnacle_fair.get(side, 0.0), 4),
                        "n_books": n_books,
                        "confidence": confidence,
                        "model_signal": _model_signal(home, away, sport_key, impl_prob, side),
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


SPORT_GROUPS = {
    "football": {s[0] for s in FIXED_SPORTS if s[0].startswith("soccer_")},
    "nba":      {"basketball_nba"},
    "tennis":   set(),  # populated dynamically
}


def build_sport_list(filter_group: str | None, max_tennis: int = 8) -> list[tuple[str, str, int]]:
    # max_tennis=8: caps API calls to ~16/run (8 tournaments × 2 regions); avoids burning monthly quota on low-liquidity events
    tennis_sports = get_active_tennis_sports(max_tennis)
    all_sports = FIXED_SPORTS + tennis_sports

    if not filter_group:
        return all_sports

    group = filter_group.lower()
    if group == "tennis":
        return get_active_tennis_sports(max_tennis)
    if group in SPORT_GROUPS:
        keys = SPORT_GROUPS[group]
        return [s for s in all_sports if s[0] in keys]
    # treat as a specific sport key
    return [s for s in all_sports if s[0] == group]


_SCAN_STATE_PATH = Path(__file__).parent.parent / "logs" / "scan_state.json"


def _maybe_notify_no_bets(n_sports: int):
    """Send a no-bets push at most once per 6-hour window to avoid notification spam."""
    state: dict = {}
    if _SCAN_STATE_PATH.exists():
        try:
            state = json.loads(_SCAN_STATE_PATH.read_text())
        except Exception:
            pass

    last_str = state.get("last_no_bets_at")
    if last_str:
        last_dt = datetime.fromisoformat(last_str)
        age_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
        if age_hours < 6:
            print(f"[ntfy] Skipping no-bets notification (last sent {age_hours:.1f}h ago)")
            return

    notify("Bets - No value today",
           f"Scanned {n_sports} sports. No edge ≥ 2% with model agreement.",
           priority="low")

    state["last_no_bets_at"] = datetime.now(timezone.utc).isoformat()
    _SCAN_STATE_PATH.write_text(json.dumps(state, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sports", default=None,
                        help="Limit scan to: football, tennis, nba, or a specific sport key")
    parser.add_argument("--max-tennis", type=int, default=99,
                        help="Cap number of active tennis tournaments to scan (saves API quota)")
    args = parser.parse_args()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    label = f" [{args.sports}]" if args.sports else ""
    print(f"=== Multi-Sport Value Bet Scanner{label} === {now}\n")

    all_sports = build_sport_list(args.sports, args.max_tennis)

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

    # Split into Kaunitz bets (≥3%, shown regardless) and model-filtered bets (2-3%, model agrees)
    kaunitz_bets = [b for b in all_bets if b["edge"] >= MIN_EDGE]
    model_bets   = [b for b in all_bets if b["edge"] < MIN_EDGE
                    and _signal_is_positive(b.get("model_signal", "?"))]
    output_bets  = kaunitz_bets + model_bets

    print(f"\nAPI quota remaining: {quota_remaining}")
    print(f"Kaunitz bets (≥3%): {len(kaunitz_bets)}  |  Model-filtered bets (2-3% + agree): {len(model_bets)}\n")

    if not output_bets:
        print("No value bets found today across all sports.")
        _maybe_notify_no_bets(len(all_sports))
        return

    # Print full detail
    side_labels = {"H": "HOME", "D": "DRAW", "A": "AWAY"}
    for section, bets in [("≥3% Kaunitz", kaunitz_bets), ("2-3% Model-filtered", model_bets)]:
        if not bets:
            continue
        print(f"=== {section} ===")
        current_sport = None
        for vb in sorted(bets, key=lambda x: (x["sport"], -x["edge"])):
            if vb["sport"] != current_sport:
                current_sport = vb["sport"]
                print(f"--- {current_sport} ---")
            dt = datetime.fromisoformat(vb["commence"].replace("Z", "+00:00")).strftime("%a %d %b %H:%M UTC")
            kelly = max(0, min(0.5 * (vb["cons"] * vb["odds"] - 1) / (vb["odds"] - 1), 0.05))
            stake = round(kelly * BANKROLL, 2)
            side = side_labels.get(vb["side"], vb["side"])
            ms = vb.get("model_signal", "?")
            try:
                ms_label = f"model {float(ms):+.1%}"
            except (ValueError, TypeError):
                ms_label = f"model {ms}"
            print(f"  {vb['home']} vs {vb['away']} [{side}]")
            print(f"    {vb['book']} @ {vb['odds']} | Edge {vb['edge']:.1%} | "
                  f"Consensus {vb['cons']:.1%} | {vb['n_books']} books [{vb['confidence']}] | "
                  f"{ms_label} | Stake £{stake} | {dt}")
    print()

    # Write bets to CSV log — deduped against same-day entries
    log_file = Path(__file__).parent.parent / "logs" / "bets.csv"
    scan_date = now[:10]  # "YYYY-MM-DD"
    existing_keys: set = set()
    if log_file.exists():
        with open(log_file, newline="") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            for row in csv.DictReader(f):
                row_date = row.get("scanned_at", "")[:10]
                if row_date == scan_date:
                    existing_keys.add((
                        row.get("kickoff", ""),
                        row.get("home", ""),
                        row.get("away", ""),
                        row.get("side", ""),
                        row.get("book", ""),
                    ))

    new_rows = []
    for vb in output_bets:
        dt = datetime.fromisoformat(vb["commence"].replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
        side = {"H": "HOME", "D": "DRAW", "A": "AWAY"}.get(vb["side"], vb["side"])
        key = (dt, vb["home"], vb["away"], side, vb["book"])
        if key in existing_keys:
            continue
        kelly = max(0, min(0.5 * (vb["cons"] * vb["odds"] - 1) / (vb["odds"] - 1), 0.05))
        new_rows.append({
            "scanned_at": now,
            "sport": vb["sport"],
            "home": vb["home"],
            "away": vb["away"],
            "kickoff": dt,
            "side": side,
            "book": vb["book"],
            "odds": vb["odds"],
            "edge": round(vb["edge"], 4),
            "consensus": round(vb["cons"], 4),
            "pinnacle_cons": round(vb.get("pinnacle_cons", 0.0), 4),
            "n_books": vb["n_books"],
            "confidence": vb["confidence"],
            "model_signal": vb.get("model_signal", "?"),
            "stake": round(kelly * BANKROLL, 2),
            "result": "",
        })

    write_header = not log_file.exists()
    with open(log_file, "a", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        writer = csv.DictWriter(f, fieldnames=[
            "scanned_at", "sport", "home", "away", "kickoff",
            "side", "book", "odds", "edge", "consensus", "pinnacle_cons",
            "n_books", "confidence", "model_signal", "stake", "result"
        ])
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)
    skipped = len(output_bets) - len(new_rows)
    print(f"[log] {len(new_rows)} bets appended to logs/bets.csv"
          + (f" ({skipped} duplicate(s) skipped)" if skipped else ""))

    # Kaunitz bets (≥3%): notify by confidence tier
    high = [vb for vb in kaunitz_bets if vb["confidence"] == "HIGH"]
    med  = [vb for vb in kaunitz_bets if vb["confidence"] == "MED"]
    low  = [vb for vb in kaunitz_bets if vb["confidence"] == "LOW"]

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

    # Model-filtered bets (2-3% + model agrees): single notification, low priority
    if model_bets:
        notify(
            title=f"Bets MODEL - {len(model_bets)} bet{'s' if len(model_bets) > 1 else ''} (2-3% + model agree)",
            message="\n\n".join(format_bet_line(vb) for vb in model_bets),
            priority="low",
        )


if __name__ == "__main__":
    main()
