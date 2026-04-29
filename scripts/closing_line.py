"""
Closing-line snapshot and drift logger.

Runs every 5 minutes via cron. For each active bet in bets.csv:
  - kickoff 55–65 min away  → T-60 drift snapshot
  - kickoff 10–20 min away  → T-15 drift snapshot
  - kickoff 0–6 min away    → T-1 drift snapshot + closing line + CLV
  - Backfills bets.csv with pinnacle_close_prob and clv_pct columns.

Writes:
  logs/closing_lines.csv  — one row per (home, away, kickoff, side) at close
  logs/drift.csv          — one row per bet per drift window (T-60, T-15, T-1)

API cost: only fires when active bets exist in a given window. Typically
3–6 calls/day on match days; zero on idle days.
"""

import csv
import fcntl
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from src.betting.devig import shin as _shin_devig, proportional as _proportional_devig
    _DEVIG = True
except ImportError:
    _DEVIG = False

try:
    from src.betting.commissions import effective_odds as _effective_odds
    _COMMISSIONS = True
except ImportError:
    _COMMISSIONS = False
    def _effective_odds(odds: float, book: str) -> float: return odds  # noqa: E704

API_KEY = os.environ.get("ODDS_API_KEY", "")
if not API_KEY:
    raise RuntimeError("ODDS_API_KEY environment variable not set.")

BASE_URL = "https://api.the-odds-api.com/v4"
BETS_CSV    = _ROOT / "logs" / "bets.csv"
CLOSING_CSV = _ROOT / "logs" / "closing_lines.csv"
DRIFT_CSV   = _ROOT / "logs" / "drift.csv"
PAPER_DIR   = _ROOT / "logs" / "paper"

LABEL_TO_KEY = {
    "EPL":          "soccer_epl",
    "Bundesliga":   "soccer_germany_bundesliga",
    "Serie A":      "soccer_italy_serie_a",
    "Championship": "soccer_efl_champ",
    "Ligue 1":      "soccer_france_ligue_one",
    "Bundesliga 2": "soccer_germany_bundesliga2",
    "NBA":          "basketball_nba",
}

# Drift windows: (min_minutes_to_ko, max_minutes_to_ko, t_label)
DRIFT_WINDOWS = [
    (55, 65, 60),
    (10, 20, 15),
    (0,   6,  1),   # T-1 window also doubles as closing-line window
]


def api_get(path: str, params: dict) -> tuple[list | dict, str]:
    params["apiKey"] = API_KEY
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=15) as r:
        remaining = r.headers.get("X-Requests-Remaining", "?")
        return json.loads(r.read()), remaining


def fetch_odds(sport_key: str) -> tuple[list, str]:
    data, remaining = api_get(
        f"/sports/{sport_key}/odds/",
        {"regions": "uk,eu", "markets": "h2h,totals,btts", "oddsFormat": "decimal"},
    )
    return data, remaining


def _devig(entries: dict[str, float]) -> dict[str, float]:
    sides = list(entries.keys())
    raw = [1.0 / entries[s] for s in sides]
    if _DEVIG:
        try:
            fair = _shin_devig(raw)
        except Exception:
            fair = _proportional_devig(raw)
    else:
        total = sum(raw)
        fair = [r / total for r in raw]
    return dict(zip(sides, fair))


def load_active_bets(now: datetime) -> list[dict]:
    """Bets whose kickoff is still upcoming or within the last 6 minutes (closing window)."""
    if not BETS_CSV.exists():
        return []
    with open(BETS_CSV, newline="") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        rows = list(csv.DictReader(f))

    active = []
    for row in rows:
        if row.get("pinnacle_close_prob"):
            continue
        kickoff_str = row.get("kickoff", "")
        if not kickoff_str:
            continue
        try:
            kickoff = datetime.strptime(kickoff_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        minutes_to_ko = (kickoff - now).total_seconds() / 60
        # Keep bets in the range [-6, 65] minutes from now
        if -6 <= minutes_to_ko <= 65:
            active.append({**row, "_kickoff_dt": kickoff, "_minutes_to_ko": minutes_to_ko,
                           "_market": row.get("market", "h2h"), "_line": row.get("line", "")})
    return active


def load_existing_closing_keys() -> set:
    if not CLOSING_CSV.exists():
        return set()
    with open(CLOSING_CSV, newline="") as f:
        return {
            (r["home"], r["away"], r["kickoff"], r["side"],
             r.get("market", "h2h"), r.get("line", ""))
            for r in csv.DictReader(f)
        }


def load_existing_drift_keys() -> set:
    if not DRIFT_CSV.exists():
        return set()
    with open(DRIFT_CSV, newline="") as f:
        return {
            (r["home"], r["away"], r["kickoff"], r["side"], r["t_minus_min"],
             r.get("market", "h2h"), r.get("line", ""))
            for r in csv.DictReader(f)
        }


def _find_event(events: list, home: str, away: str) -> dict | None:
    for ev in events:
        if ev["home_team"] == home and ev["away_team"] == away:
            return ev
    return None


def _extract_snapshot(event: dict, home: str, away: str, side: str, book: str,
                       market: str = "h2h", line: str = "") -> dict:
    """
    Returns dict with:
      pinnacle_fair: dict[side→prob] or {}
      your_book_odds: float or None
      n_books: int (books offering this market/line)
    """
    pinnacle_fair: dict[str, float] = {}
    your_book_odds = None
    n_books = 0

    for b in event.get("bookmakers", []):
        for m in b.get("markets", []):
            if m["key"] != market:
                continue

            if market == "h2h":
                oc = {o["name"]: o["price"] for o in m["outcomes"]}
                entries = {
                    "HOME": oc.get(home),
                    "DRAW": oc.get("Draw"),
                    "AWAY": oc.get(away),
                }
                entries = {k: v for k, v in entries.items() if v and v > 1.0}
                if side not in entries:
                    continue
                n_books += 1
                if b["key"] == "pinnacle":
                    pinnacle_fair = _devig(entries)
                if b["key"] == book:
                    side_to_outcome = {"HOME": home, "DRAW": "Draw", "AWAY": away}
                    your_book_odds = oc.get(side_to_outcome.get(side, side))

            elif market == "totals":
                target_pt = float(line) if line else None
                by_pt: dict[float, dict[str, float]] = {}
                for o in m.get("outcomes", []):
                    pt = o.get("point")
                    if pt is None:
                        continue
                    by_pt.setdefault(float(pt), {})[o["name"].upper()] = o["price"]
                if target_pt not in by_pt:
                    continue
                oc = by_pt[target_pt]
                over, under = oc.get("OVER"), oc.get("UNDER")
                if not (over and under and over > 1.0 and under > 1.0):
                    continue
                entries = {"OVER": over, "UNDER": under}
                if side not in entries:
                    continue
                n_books += 1
                if b["key"] == "pinnacle":
                    pinnacle_fair = _devig(entries)
                if b["key"] == book:
                    your_book_odds = oc.get(side)

            elif market == "btts":
                oc = {o["name"].upper(): o["price"] for o in m.get("outcomes", [])}
                yes_o, no_o = oc.get("YES"), oc.get("NO")
                if not (yes_o and no_o and yes_o > 1.0 and no_o > 1.0):
                    continue
                entries = {"YES": yes_o, "NO": no_o}
                if side not in entries:
                    continue
                n_books += 1
                if b["key"] == "pinnacle":
                    pinnacle_fair = _devig(entries)
                if b["key"] == book:
                    your_book_odds = oc.get(side)

    return {"pinnacle_fair": pinnacle_fair, "your_book_odds": your_book_odds, "n_books": n_books}


def update_csv_clv(path: "Path", updates: dict):
    """Write pinnacle_close_prob + clv_pct back into any bets CSV for matching rows.

    CLV is recomputed per-row using that row's book and odds, so paper variants
    flagging different books for the same fixture get honest per-book CLV.
    """
    if not updates or not path.exists():
        return

    with open(path, newline="") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    for col in ("pinnacle_close_prob", "clv_pct"):
        if col not in fieldnames:
            fieldnames.append(col)

    changed = False
    for row in rows:
        key = (row.get("home"), row.get("away"), row.get("kickoff"), row.get("side"),
               row.get("market", "h2h"), row.get("line", ""))
        if key in updates and not row.get("pinnacle_close_prob"):
            pin_prob = float(updates[key]["pinnacle_close_prob"])
            row_book = row.get("book", "")
            try:
                row_odds = float(row.get("odds") or 0)
            except ValueError:
                row_odds = 0.0
            eff = _effective_odds(row_odds, row_book) if row_odds else 0.0
            row["pinnacle_close_prob"] = str(pin_prob)
            row["clv_pct"] = str(round(eff * pin_prob - 1, 6)) if eff else ""
            changed = True

    if not changed:
        return

    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)
    print(f"[{path.name}] Updated {sum(1 for r in rows if r.get('pinnacle_close_prob'))} row(s) with CLV data.")


def update_bets_csv_clv(updates: dict):
    """Backward-compatible wrapper — updates production bets.csv."""
    update_csv_clv(BETS_CSV, updates)


def load_active_paper_bets(now: datetime) -> list[dict]:
    """Active bets from all paper strategy CSVs (same window logic as production)."""
    result = []
    if not PAPER_DIR.exists():
        return result
    for csv_path in sorted(PAPER_DIR.glob("*.csv")):
        try:
            with open(csv_path, newline="") as f:
                rows = list(csv.DictReader(f))
            for row in rows:
                if row.get("pinnacle_close_prob"):
                    continue
                kickoff_str = row.get("kickoff", "")
                if not kickoff_str:
                    continue
                try:
                    kickoff = datetime.strptime(kickoff_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                minutes_to_ko = (kickoff - now).total_seconds() / 60
                if -6 <= minutes_to_ko <= 65:
                    result.append({**row, "_kickoff_dt": kickoff, "_minutes_to_ko": minutes_to_ko,
                                   "_market": row.get("market", "h2h"), "_line": row.get("line", ""),
                                   "_source_csv": str(csv_path)})
        except Exception as e:
            print(f"  [paper] Could not read {csv_path.name}: {e}")
    return result


def main():
    now = datetime.now(timezone.utc)
    print(f"=== Closing Line Snapshot === {now.strftime('%Y-%m-%d %H:%M UTC')}")

    active_bets = load_active_bets(now)
    paper_bets  = load_active_paper_bets(now)

    # Deduplicate paper bets against production by bet key (same fixture/market may appear in many CSVs)
    prod_keys = {
        (b["home"], b["away"], b["kickoff"], b["side"],
         b.get("_market", "h2h"), b.get("_line", ""))
        for b in active_bets
    }
    # Include paper bets on fixtures not already covered by production (e.g. C_loose extra bets)
    extra_paper = [b for b in paper_bets
                   if (b["home"], b["away"], b["kickoff"], b["side"],
                       b.get("_market", "h2h"), b.get("_line", "")) not in prod_keys]

    all_active = active_bets + extra_paper
    if not all_active:
        print("No active bets in window. Nothing to do.")
        return

    prod_count = len(active_bets)
    paper_count = len(paper_bets)
    print(f"{prod_count} production bet(s) + {paper_count} paper bet(s) in window "
          f"({len(extra_paper)} unique paper-only).")

    existing_closing = load_existing_closing_keys()
    existing_drift   = load_existing_drift_keys()

    # Group by sport to minimise API calls (one call per sport, not per fixture)
    by_sport: dict[str, list[dict]] = {}
    _skipped_sports: dict[str, int] = {}
    for bet in all_active:
        sport_key = LABEL_TO_KEY.get(bet.get("sport", ""))
        if not sport_key:
            label = bet.get("sport", "?")
            _skipped_sports[label] = _skipped_sports.get(label, 0) + 1
            continue
        by_sport.setdefault(sport_key, []).append(bet)
    if _skipped_sports:
        # Tennis labels are dynamic and not in LABEL_TO_KEY; CLV excluded until Phase 6.
        n_skipped = sum(_skipped_sports.values())
        print(f"  [skip] {n_skipped} bet(s) with no sport_key mapping (tennis excluded until Phase 6)")

    closing_rows: list[dict] = []
    drift_rows:   list[dict] = []
    clv_updates:  dict       = {}

    for sport_key, bets in by_sport.items():
        try:
            events, remaining = fetch_odds(sport_key)
            print(f"  {sport_key}: {len(events)} fixtures | quota remaining: {remaining}")
        except Exception as e:
            print(f"  {sport_key}: ERROR — {e}")
            continue

        for bet in bets:
            home          = bet["home"]
            away          = bet["away"]
            kickoff_str   = bet["kickoff"]
            side          = bet["side"]
            book          = bet.get("book", "")
            market        = bet.get("_market", "h2h")
            line_val      = bet.get("_line", "")
            minutes_to_ko = bet["_minutes_to_ko"]

            event = _find_event(events, home, away)
            if event is None:
                # Fixture removed from API post-kickoff — expected
                continue

            snap = _extract_snapshot(event, home, away, side, book,
                                     market=market, line=line_val)
            pinnacle_fair  = snap["pinnacle_fair"]
            your_book_odds = snap["your_book_odds"]
            n_books        = snap["n_books"]

            pin_prob  = pinnacle_fair.get(side)
            pin_odds  = round(1.0 / pin_prob, 4) if pin_prob and pin_prob > 0 else None
            captured  = now.strftime("%Y-%m-%d %H:%M UTC")

            # --- Drift snapshots ---
            for win_lo, win_hi, t_label in DRIFT_WINDOWS:
                if win_lo <= minutes_to_ko <= win_hi:
                    dk = (home, away, kickoff_str, side, str(t_label), market, line_val)
                    if dk not in existing_drift:
                        drift_rows.append({
                            "captured_at":    captured,
                            "home":           home,
                            "away":           away,
                            "kickoff":        kickoff_str,
                            "side":           side,
                            "market":         market,
                            "line":           line_val,
                            "book":           book,
                            "t_minus_min":    t_label,
                            "your_book_odds": your_book_odds or "",
                            "pinnacle_odds":  pin_odds or "",
                            "n_books":        n_books,
                        })
                        print(f"  [drift T-{t_label:>2}] {home} vs {away} [{side}]"
                              + (f" | Pinnacle {pin_odds}" if pin_odds else " | no Pinnacle"))

            # --- Closing line (T-1 window also serves as closing) ---
            if 0 <= minutes_to_ko <= 6:
                ck = (home, away, kickoff_str, side, market, line_val)
                if ck not in existing_closing and pin_prob:
                    flagged_odds = float(bet.get("odds") or 0)
                    # Use T-1 re-fetched price if available; it reflects actually-tradable odds.
                    # Fall back to originally flagged odds only if the book is no longer quoted.
                    close_odds = your_book_odds if your_book_odds else flagged_odds
                    # Apply commission so CLV reflects what actually lands in the pocket
                    eff_close = _effective_odds(close_odds, book) if close_odds else 0
                    clv = round(eff_close * pin_prob - 1, 6) if eff_close else ""
                    closing_rows.append({
                        "captured_at":            captured,
                        "home":                   home,
                        "away":                   away,
                        "kickoff":                kickoff_str,
                        "side":                   side,
                        "market":                 market,
                        "line":                   line_val,
                        "pinnacle_devig_prob":    round(pin_prob, 6),
                        "pinnacle_raw_odds":      pin_odds,
                        "your_book_flagged_odds": flagged_odds or "",
                        "your_book_close_odds":   your_book_odds or "",
                        "clv_pct":                clv,
                    })
                    clv_updates[ck] = {
                        "pinnacle_close_prob": str(round(pin_prob, 6)),
                        "clv_pct":             str(clv),
                    }
                    clv_str = f"{clv:+.2%}" if isinstance(clv, float) else "n/a"
                    stale = "" if your_book_odds else " (used flagged odds — book not quoted at T-1)"
                    print(f"  [closing]    {home} vs {away} [{side}] | CLV {clv_str}{stale}")

    # --- Write closing_lines.csv ---
    if closing_rows:
        write_hdr = not CLOSING_CSV.exists()
        with open(CLOSING_CSV, "a", newline="") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            w = csv.DictWriter(f, fieldnames=[
                "captured_at", "home", "away", "kickoff", "side", "market", "line",
                "pinnacle_devig_prob", "pinnacle_raw_odds",
                "your_book_flagged_odds", "your_book_close_odds", "clv_pct",
            ])
            if write_hdr:
                w.writeheader()
            w.writerows(closing_rows)
        print(f"[log] {len(closing_rows)} closing line(s) → closing_lines.csv")
        # Update production bets.csv
        update_csv_clv(BETS_CSV, clv_updates)
        # Update all paper strategy CSVs (same clv_updates keyed by bet identity)
        if PAPER_DIR.exists():
            for paper_path in sorted(PAPER_DIR.glob("*.csv")):
                update_csv_clv(paper_path, clv_updates)

    # --- Write drift.csv ---
    if drift_rows:
        write_hdr = not DRIFT_CSV.exists()
        with open(DRIFT_CSV, "a", newline="") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            w = csv.DictWriter(f, fieldnames=[
                "captured_at", "home", "away", "kickoff", "side", "market", "line",
                "book", "t_minus_min", "your_book_odds", "pinnacle_odds", "n_books",
            ])
            if write_hdr:
                w.writeheader()
            w.writerows(drift_rows)
        print(f"[log] {len(drift_rows)} drift row(s) → drift.csv")

    if not closing_rows and not drift_rows:
        print("No windows matched — nothing written.")


if __name__ == "__main__":
    main()
