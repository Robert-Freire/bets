"""
Daily multi-sport value bet scanner using the Kaunitz consensus strategy.
Reads ODDS_API_KEY from environment. Run with:
    ODDS_API_KEY=xxx python3 scripts/scan_odds.py
"""

import argparse
import csv
import fcntl
import functools
import json
import os
import statistics
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Allow importing from src/ regardless of working directory
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.config import load_config as _src_load_config, _validate as _validate_leagues
from src.data.fixture_calendar import canary_verdict as _canary_verdict

try:
    from src.betting.devig import shin as _shin_devig, proportional as _proportional_devig
    _DEVIG = True
except ImportError:
    _DEVIG = False

# A.4: BetRepo dual-writes CSV + (optionally) Azure SQL. Pi-safe: the
# DB code path stays dormant unless BETS_DB_WRITE=1 + DSN env are set.
from src.storage.repo import BetRepo, PAPER_FIELDS as _PAPER_FIELDS_REPO

# A.5.5: SnapshotArchive captures every raw API response to Azure Blob
# Storage when BLOB_ARCHIVE=1 + connection inputs are set. Pi-safe: lazy
# import of azure.storage.blob; module imports nothing extra at top level.
from src.storage.snapshots import get_archive as _get_snapshot_archive

try:
    from src.betting.risk import (
        get_bankroll as _get_bankroll,
        compute_raw_stake as _compute_raw_stake,
        load_drawdown_state as _load_drawdown_state,
        drawdown_multiplier as _drawdown_multiplier,
        apply_risk_pipeline as _apply_risk_pipeline,
    )
    _RISK = True
except ImportError:
    _RISK = False

try:
    from src.betting.strategies import STRATEGIES, evaluate_strategy
    _STRATEGIES = True
except ImportError:
    _STRATEGIES = False
    STRATEGIES = []

try:
    from src.betting.commissions import (
        commission_rate as _commission_rate,
        effective_odds as _effective_odds,
        effective_implied_prob as _effective_implied_prob,
    )
    _COMMISSIONS = True
except ImportError:
    _COMMISSIONS = False
    def _commission_rate(book: str) -> float: return 0.0  # noqa: E704
    def _effective_odds(odds: float, book: str) -> float: return odds  # noqa: E704
    def _effective_implied_prob(odds: float, book: str) -> float: return 1.0 / odds  # noqa: E704

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

from src.betting.team_names import API_TO_FD as _API_TO_FD


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


# NTFY_TOPIC_OVERRIDE env var: set on WSL/dev cron to a different topic (separate
# channel) or "" (empty = disable ntfy entirely). Default = production topic.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC_OVERRIDE", "robert-epl-bets-m4x9k")
NTFY_URL = f"https://ntfy.sh/{NTFY_TOPIC}" if NTFY_TOPIC else ""

BASE_URL = "https://api.the-odds-api.com/v4"
MIN_EDGE = 0.03        # Kaunitz-only threshold (no model required)
MODEL_MIN_EDGE = 0.02  # lower threshold — only shown when model agrees
MAX_DISPERSION = 0.04  # reject market if stdev of fair probs across books exceeds this
OUTLIER_Z_THRESHOLD = 2.5  # reject flagged book if its z-score vs the rest exceeds this

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

_NBA_SPORT = ("basketball_nba", "NBA", 20)


def _load_config() -> dict:
    return _src_load_config()


_CONFIG = _load_config()

# FIXED_SPORTS: football leagues from config + NBA (NBA dropped from cron but
# still reachable via --sports nba; lives outside the config-managed set).
FIXED_SPORTS = [
    (e["key"], e["label"], e["min_books"]) for e in _CONFIG["leagues"]
] + [_NBA_SPORT]

# Min books per sport — below this the consensus is unreliable
DEFAULT_MIN_BOOKS = 15
SPORT_MIN_BOOKS = {k: v for k, _, v in FIXED_SPORTS}
SPORT_MIN_EDGE = {}


def api_get(path: str, params: dict) -> tuple[list | dict, str]:
    params["apiKey"] = API_KEY
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=15) as r:
        status = r.status
        headers = dict(r.headers.items())
        body = r.read()
    parts = path.strip("/").split("/")
    sport_key = parts[1] if len(parts) >= 2 and parts[0] == "sports" else ""
    try:
        _get_snapshot_archive().archive(
            source="odds_api", endpoint=path, params=params,
            status=status, headers=headers, body=body, sport_key=sport_key,
        )
    except Exception as _e:
        print(f"[snapshots] WARN: archive call raised: {_e}", file=sys.stderr)
    remaining = headers.get("x-requests-remaining", headers.get("X-Requests-Remaining", "?"))
    return json.loads(body), remaining


def get_active_tennis_sports(max_tournaments: int = 99) -> list[tuple[str, str, int]]:
    """Fetch active tennis tournaments dynamically (free call — no quota cost)."""
    data, _ = api_get("/sports/", {"all": "false"})
    tennis = []
    for s in data:
        if s.get("active") and s["key"].startswith("tennis_"):
            label = s.get("title", s["key"])
            tennis.append((s["key"], label, 15))
    return tennis[:max_tournaments]


def _get_canary_league() -> str:
    """Resolve the football league fetched first inside the per-league loop.
    If it returns 0 events we treat that as an Odds API empty-payload window and
    skip the rest of the football sweep instead of paying for ~5 more empty calls.
    Order: CANARY_LEAGUE env var → config.json → default 'soccer_epl'."""
    if "CANARY_LEAGUE" in os.environ:
        return os.environ["CANARY_LEAGUE"]
    config_path = Path(__file__).resolve().parent.parent / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            if "canary_league" in cfg:
                return cfg["canary_league"]
        except Exception:
            pass
    return "soccer_epl"


def _resolve_canary(configured: str, football: list[tuple[str, str, int]]) -> str:
    """If the configured canary league isn't in the football list this scan,
    fall back to the first football league so protection still fires after a
    typo, an NBA/tennis key by mistake, or an off-season league."""
    football_keys = {s[0] for s in football}
    if football and configured not in football_keys:
        fallback = football[0][0]
        print(f"[canary] WARN — configured {configured!r} not in football "
              f"list {sorted(football_keys)}; falling back to {fallback}.")
        return fallback
    return configured


def _health_check_sports() -> set[str]:
    """Free /sports/?all=false call. Logs which football leagues the API reports
    as active, so we can correlate /sports/ status with empty-payload incidents
    over time and decide whether the free endpoint can replace the in-loop check."""
    try:
        data, _ = api_get("/sports/", {"all": "false"})
    except Exception as e:
        print(f"[health] /sports/ call failed: {e}")
        return set()
    active = {s["key"] for s in data if s.get("active")}
    football_keys = SPORT_GROUPS["football"]
    active_football = sorted(football_keys & active)
    inactive_football = sorted(football_keys - active)
    print(f"[health] /sports/ active football: {len(active_football)}/{len(football_keys)}"
          + (f"  inactive: {inactive_football}" if inactive_football else ""))
    return active


def fetch_odds(sport_key: str) -> tuple[list, str]:
    markets = ",".join(["h2h"] + _CONFIG.get("extra_markets", []))
    data, remaining = api_get(
        f"/sports/{sport_key}/odds/",
        {"regions": "uk,eu", "markets": markets, "oddsFormat": "decimal"},
    )
    return data, remaining


def _devig_book(entries: dict) -> dict[str, float]:
    """De-vig any N-outcome market using Shin (1993). Raises on bad odds rather than
    silently mislabeling the devig_method in the output row."""
    sides = list(entries.keys())
    raw = [1.0 / entries[s] for s in sides]
    if _DEVIG:
        fair = _shin_devig(raw)
    else:
        fair = [r / sum(raw) for r in raw]
    return dict(zip(sides, fair))


def find_value_bets(events: list, sport_key: str) -> list[dict]:
    min_books = SPORT_MIN_BOOKS.get(sport_key, DEFAULT_MIN_BOOKS)
    min_edge = SPORT_MIN_EDGE.get(sport_key, MODEL_MIN_EDGE)
    bets = []

    for ev in events:
        home, away, commence = ev["home_team"], ev["away_team"], ev["commence_time"]

        # --- H2H market ---
        h2h_impl: dict[str, list] = {}
        h2h_books: list[dict] = []
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
                fair = _devig_book(entries)
                for side, fp in fair.items():
                    h2h_impl.setdefault(side, []).append(fp)
                h2h_books.append({"book": b["key"], "fair": fair, **entries})

        if len(h2h_books) >= min_books:
            cons = {s: statistics.mean(v) for s, v in h2h_impl.items()}
            disp = {s: (statistics.stdev(v) if len(v) >= 2 else 0.0) for s, v in h2h_impl.items()}
            n = len(h2h_books)
            conf = "HIGH" if n >= 30 else ("MED" if n >= 20 else "LOW")
            pin_fair = next((b["fair"] for b in h2h_books if b["book"] == "pinnacle"), {})
            for b in h2h_books:
                if b["book"] not in UK_LICENSED_BOOKS:
                    continue
                for side, odds in b.items():
                    if side in ("book", "fair") or side not in cons:
                        continue
                    if disp.get(side, 0.0) > MAX_DISPERSION:
                        continue
                    edge_gross = cons[side] - b["fair"].get(side, 1.0 / odds)
                    edge = cons[side] - _effective_implied_prob(odds, b["book"])
                    if edge >= min_edge and 1.2 <= odds <= 15.0:
                        other_probs = [b2["fair"][side] for b2 in h2h_books
                                       if b2["book"] != b["book"] and side in b2["fair"]]
                        if len(other_probs) >= 2:
                            om, os_ = statistics.mean(other_probs), statistics.stdev(other_probs)
                            z = (b["fair"][side] - om) / os_ if os_ > 0 else 0.0
                        else:
                            z = 0.0
                        if abs(z) > OUTLIER_Z_THRESHOLD:
                            continue
                        impl_raw = round(1.0 / odds, 4)
                        bets.append({
                            "market": "h2h", "line": "",
                            "commence": commence, "home": home, "away": away,
                            "side": side, "book": b["book"], "odds": odds,
                            "impl_raw": impl_raw,
                            "impl_effective": round(_effective_implied_prob(odds, b["book"]), 4),
                            "cons": round(cons[side], 4),
                            "edge": round(edge, 4),
                            "edge_gross": round(edge_gross, 4),
                            "pinnacle_cons": round(pin_fair.get(side, 0.0), 4),
                            "n_books": n, "confidence": conf,
                            "model_signal": _model_signal(home, away, sport_key, impl_raw, side),
                            "dispersion": round(disp.get(side, 0.0), 4),
                            "outlier_z": round(z, 3),
                        })

        # --- Totals market (group by line point) ---
        totals_by_line: dict[float, dict] = {}
        for b in ev.get("bookmakers", []):
            for m in b.get("markets", []):
                if m["key"] != "totals":
                    continue
                by_pt: dict[float, dict[str, float]] = {}
                for o in m.get("outcomes", []):
                    pt = o.get("point")
                    if pt is None:
                        continue
                    by_pt.setdefault(float(pt), {})[o["name"].upper()] = o["price"]
                for pt, oc in by_pt.items():
                    over, under = oc.get("OVER"), oc.get("UNDER")
                    if not (over and under and over > 1.0 and under > 1.0):
                        continue
                    entries = {"OVER": over, "UNDER": under}
                    fair = _devig_book(entries)
                    if pt not in totals_by_line:
                        totals_by_line[pt] = {"impl": {}, "books": []}
                    for side, fp in fair.items():
                        totals_by_line[pt]["impl"].setdefault(side, []).append(fp)
                    totals_by_line[pt]["books"].append({"book": b["key"], "fair": fair, **entries})

        for pt, data in totals_by_line.items():
            if len(data["books"]) < min_books:
                continue
            cons = {s: statistics.mean(v) for s, v in data["impl"].items()}
            disp = {s: (statistics.stdev(v) if len(v) >= 2 else 0.0) for s, v in data["impl"].items()}
            n = len(data["books"])
            conf = "HIGH" if n >= 30 else ("MED" if n >= 20 else "LOW")
            pin_fair = next((b["fair"] for b in data["books"] if b["book"] == "pinnacle"), {})
            for b in data["books"]:
                if b["book"] not in UK_LICENSED_BOOKS:
                    continue
                for side in ("OVER", "UNDER"):
                    odds = b.get(side)
                    if not odds or side not in cons:
                        continue
                    if disp.get(side, 0.0) > MAX_DISPERSION:
                        continue
                    edge_gross = cons[side] - b["fair"].get(side, 1.0 / odds)
                    edge = cons[side] - _effective_implied_prob(odds, b["book"])
                    if edge >= min_edge and 1.2 <= odds <= 15.0:
                        other_probs = [b2["fair"][side] for b2 in data["books"]
                                       if b2["book"] != b["book"] and side in b2["fair"]]
                        if len(other_probs) >= 2:
                            om, os_ = statistics.mean(other_probs), statistics.stdev(other_probs)
                            z = (b["fair"][side] - om) / os_ if os_ > 0 else 0.0
                        else:
                            z = 0.0
                        if abs(z) > OUTLIER_Z_THRESHOLD:
                            continue
                        impl_raw = round(1.0 / odds, 4)
                        bets.append({
                            "market": "totals", "line": pt,
                            "commence": commence, "home": home, "away": away,
                            "side": side, "book": b["book"], "odds": odds,
                            "impl_raw": impl_raw,
                            "impl_effective": round(_effective_implied_prob(odds, b["book"]), 4),
                            "cons": round(cons[side], 4),
                            "edge": round(edge, 4),
                            "edge_gross": round(edge_gross, 4),
                            "pinnacle_cons": round(pin_fair.get(side, 0.0), 4),
                            "n_books": n, "confidence": conf,
                            "model_signal": "?",
                            "dispersion": round(disp.get(side, 0.0), 4),
                            "outlier_z": round(z, 3),
                        })

        # --- BTTS market ---
        btts_impl: dict[str, list] = {}
        btts_books: list[dict] = []
        for b in ev.get("bookmakers", []):
            for m in b.get("markets", []):
                if m["key"] != "btts":
                    continue
                oc = {o["name"].upper(): o["price"] for o in m.get("outcomes", [])}
                yes_o, no_o = oc.get("YES"), oc.get("NO")
                if not (yes_o and no_o and yes_o > 1.0 and no_o > 1.0):
                    continue
                entries = {"YES": yes_o, "NO": no_o}
                fair = _devig_book(entries)
                for side, fp in fair.items():
                    btts_impl.setdefault(side, []).append(fp)
                btts_books.append({"book": b["key"], "fair": fair, **entries})

        if len(btts_books) >= min_books:
            cons = {s: statistics.mean(v) for s, v in btts_impl.items()}
            disp = {s: (statistics.stdev(v) if len(v) >= 2 else 0.0) for s, v in btts_impl.items()}
            n = len(btts_books)
            conf = "HIGH" if n >= 30 else ("MED" if n >= 20 else "LOW")
            pin_fair = next((b["fair"] for b in btts_books if b["book"] == "pinnacle"), {})
            for b in btts_books:
                if b["book"] not in UK_LICENSED_BOOKS:
                    continue
                for side in ("YES", "NO"):
                    odds = b.get(side)
                    if not odds or side not in cons:
                        continue
                    if disp.get(side, 0.0) > MAX_DISPERSION:
                        continue
                    edge_gross = cons[side] - b["fair"].get(side, 1.0 / odds)
                    edge = cons[side] - _effective_implied_prob(odds, b["book"])
                    if edge >= min_edge and 1.2 <= odds <= 15.0:
                        other_probs = [b2["fair"][side] for b2 in btts_books
                                       if b2["book"] != b["book"] and side in b2["fair"]]
                        if len(other_probs) >= 2:
                            om, os_ = statistics.mean(other_probs), statistics.stdev(other_probs)
                            z = (b["fair"][side] - om) / os_ if os_ > 0 else 0.0
                        else:
                            z = 0.0
                        if abs(z) > OUTLIER_Z_THRESHOLD:
                            continue
                        impl_raw = round(1.0 / odds, 4)
                        bets.append({
                            "market": "btts", "line": "",
                            "commence": commence, "home": home, "away": away,
                            "side": side, "book": b["book"], "odds": odds,
                            "impl_raw": impl_raw,
                            "impl_effective": round(_effective_implied_prob(odds, b["book"]), 4),
                            "cons": round(cons[side], 4),
                            "edge": round(edge, 4),
                            "edge_gross": round(edge_gross, 4),
                            "pinnacle_cons": round(pin_fair.get(side, 0.0), 4),
                            "n_books": n, "confidence": conf,
                            "model_signal": "?",
                            "dispersion": round(disp.get(side, 0.0), 4),
                            "outlier_z": round(z, 3),
                        })

    # Deduplicate: best edge per (fixture, market, line, side)
    bets.sort(key=lambda x: x["edge"], reverse=True)
    seen: set = set()
    out = []
    for vb in bets:
        k = (vb["home"], vb["away"], vb["market"], str(vb.get("line", "")), vb["side"])
        if k not in seen:
            seen.add(k)
            out.append(vb)
    return out


def _side_label(vb: dict) -> str:
    market = vb.get("market", "h2h")
    if market == "h2h":
        return {"H": "HOME", "D": "DRAW", "A": "AWAY"}.get(vb["side"], vb["side"])
    if market == "totals":
        return f"{vb['side']} {vb.get('line', '')}"
    if market == "btts":
        return f"BTTS {vb['side']}"
    return vb["side"]


def format_bet_line(vb: dict) -> str:
    dt = datetime.fromisoformat(vb["commence"].replace("Z", "+00:00")).strftime("%a %d %b %H:%M UTC")
    side = _side_label(vb)
    return (
        f"{vb['home']} vs {vb['away']} [{side}]\n"
        f"  {vb['book']} @ {vb['odds']} | Edge {vb['edge']:.1%} | "
        f"{vb['n_books']} books [{vb['confidence']}] | Stake £{vb['stake']}\n"
        f"  {dt}"
    )


def notify(title: str, message: str, priority: str = "default"):
    if not NTFY_TOPIC:
        print(f"[ntfy] Disabled (NTFY_TOPIC_OVERRIDE empty): '{title}'")
        return
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


# --- Notification dedupe (logs/notified.json) ---
_NOTIFIED_PATH = Path(__file__).parent.parent / "logs" / "notified.json"
_NOTIFY_DEDUP_HOURS = 12
_NOTIFY_ODDS_IMPROVEMENT = 0.02  # re-notify if odds improved by ≥2%


def _notified_key(vb: dict) -> str:
    return "|".join([
        str(vb.get("kickoff", "")), vb.get("home", ""), vb.get("away", ""),
        vb.get("side", ""), vb.get("book", ""),
        vb.get("market", "h2h"), str(vb.get("line", "")),
    ])


def _load_notified() -> dict:
    if _NOTIFIED_PATH.exists():
        try:
            return json.loads(_NOTIFIED_PATH.read_text())
        except Exception:
            pass
    return {}


def _save_notified(notified: dict):
    tmp = _NOTIFIED_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(notified, indent=2))
    os.replace(tmp, _NOTIFIED_PATH)


def _filter_notify(bets: list[dict], notified: dict, now_dt: datetime) -> list[dict]:
    """Return bets that haven't been notified in the last 12h (unless odds improved ≥2%)."""
    out = []
    for vb in bets:
        key = _notified_key(vb)
        entry = notified.get(key)
        if entry:
            last_dt = datetime.fromisoformat(entry["last_notified_at"])
            if (now_dt - last_dt).total_seconds() / 3600 < _NOTIFY_DEDUP_HOURS:
                last_odds = entry.get("last_odds", 0.0)
                improvement = (vb["odds"] - last_odds) / last_odds if last_odds > 0 else 0
                if improvement < _NOTIFY_ODDS_IMPROVEMENT:
                    print(f"[ntfy] Skipping duplicate: {vb['home']} vs {vb['away']} [{vb['side']}] "
                          f"@ {vb['book']} (last notified {(now_dt - last_dt).total_seconds()/3600:.1f}h ago)")
                    continue
        out.append(vb)
    return out


def _mark_notified(bets: list[dict], notified: dict, now_dt: datetime):
    iso = now_dt.isoformat()
    for vb in bets:
        key = _notified_key(vb)
        existing = notified.get(key, {})
        notified[key] = {
            "first_notified_at": existing.get("first_notified_at", iso),
            "last_notified_at": iso,
            "last_odds": vb["odds"],
        }


_PAPER_DIR = Path(__file__).parent.parent / "logs" / "paper"

_PAPER_FIELDNAMES = [
    "scanned_at", "strategy", "sport", "market", "line", "home", "away", "kickoff",
    "side", "book", "odds", "impl_raw", "impl_effective", "edge", "edge_gross",
    "effective_odds", "commission_rate", "consensus", "pinnacle_cons",
    "n_books", "confidence", "model_signal", "dispersion", "outlier_z",
    "devig_method", "weight_scheme", "code_sha", "strategy_config_hash",
    "stake", "pinnacle_close_prob", "clv_pct",
]


@functools.lru_cache(maxsize=1)
def _git_sha() -> str:
    """Short SHA of git HEAD at scan time. Cached for process lifetime.
    Empty string if git unavailable — provenance degrades gracefully."""
    try:
        return subprocess.check_output(
            ["git", "-C", str(Path(__file__).resolve().parent.parent), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=2,
        ).decode().strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return ""


def _ensure_paper_schema(log_file: Path) -> None:
    """Migrate an existing paper CSV to the current _PAPER_FIELDNAMES schema.
    Idempotent — no-op if the header already matches. Pre-R.11 rows get empty
    strings for code_sha / strategy_config_hash, forming a 'pre-R.11' window
    that compare_strategies handles separately from current-config rows."""
    if not log_file.exists():
        return
    expected = ",".join(_PAPER_FIELDNAMES)
    with open(log_file, newline="") as f:
        first = f.readline().strip()
        if first == expected:
            return
        f.seek(0)
        old_rows = list(csv.DictReader(f))
    print(f"[paper:schema] migrating {log_file.name} → {len(_PAPER_FIELDNAMES)}-col schema (added: code_sha, strategy_config_hash)")
    tmp = log_file.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        writer = csv.DictWriter(f, fieldnames=_PAPER_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for row in old_rows:
            writer.writerow({k: row.get(k, "") for k in _PAPER_FIELDNAMES})
    tmp.replace(log_file)

_H2H_SIDE = {"H": "HOME", "D": "DRAW", "A": "AWAY"}


def _paper_provenance(strategy) -> tuple[str, str]:
    """Return (devig_method, weight_scheme) for a StrategyConfig."""
    if getattr(strategy, "raw_consensus", False):
        return "raw", "uniform"
    devig = getattr(strategy, "devig", "shin")
    weights = getattr(strategy, "sharpness_weights", None)
    consensus_mode = getattr(strategy, "consensus_mode", "mean")
    if weights:
        weight_scheme = "sharp_v1"
    elif consensus_mode == "pinnacle_only":
        weight_scheme = "pinnacle_only"
    elif consensus_mode == "weighted":
        weight_scheme = "pinnacle_weighted"
    else:
        weight_scheme = "uniform"
    return devig, weight_scheme


def _append_paper_csv(strategy_name: str, paper_bets: list[dict],
                      sport_label: str, now: str, scan_date: str, bankroll: float = 1000.0,
                      strategy=None, repo: "BetRepo | None" = None):
    """Write paper strategy bets to logs/paper/<strategy_name>.csv (and DB if `repo` is dual-write)."""
    if not paper_bets:
        return
    _PAPER_DIR.mkdir(exist_ok=True)
    log_file = _PAPER_DIR / f"{strategy_name}.csv"
    _ensure_paper_schema(log_file)

    existing_keys: set = set()
    if log_file.exists():
        with open(log_file, newline="") as f:
            for row in csv.DictReader(f):
                if row.get("scanned_at", "")[:10] == scan_date:
                    existing_keys.add((
                        row.get("kickoff", ""),
                        row.get("home", ""),
                        row.get("away", ""),
                        row.get("side", ""),
                        row.get("book", ""),
                        row.get("market", "h2h"),
                        str(row.get("line", "")),
                    ))

    new_rows = []
    for vb in paper_bets:
        dt = datetime.fromisoformat(vb["commence"].replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
        side = _H2H_SIDE.get(vb["side"], vb["side"])
        key = (dt, vb["home"], vb["away"], side, vb["book"],
               vb.get("market", "h2h"), str(vb.get("line", "")))
        if key in existing_keys:
            continue
        new_rows.append({
            "scanned_at": now,
            "strategy": strategy_name,
            "sport": sport_label,
            "market": vb.get("market", "h2h"),
            "line": vb.get("line", ""),
            "home": vb["home"],
            "away": vb["away"],
            "kickoff": dt,
            "side": side,
            "book": vb["book"],
            "odds": vb["odds"],
            "impl_raw": round(vb.get("impl_raw", 1.0 / vb["odds"]), 4),
            "impl_effective": round(vb.get("impl_effective", _effective_implied_prob(vb["odds"], vb["book"])), 4),
            "edge": round(vb["edge"], 4),
            "edge_gross": round(vb.get("edge_gross", vb["edge"]), 4),
            "effective_odds": round(vb.get("effective_odds", vb["odds"]), 4),
            "commission_rate": round(vb.get("commission_rate", 0.0), 4),
            "consensus": round(vb["cons"], 4),
            "pinnacle_cons": round(vb.get("pinnacle_cons", 0.0), 4),
            "n_books": vb["n_books"],
            "confidence": vb["confidence"],
            "model_signal": vb.get("model_signal", "?"),
            "dispersion": round(vb.get("dispersion", 0.0), 4),
            "outlier_z": round(vb.get("outlier_z", 0.0), 3),
            "devig_method": _paper_provenance(strategy)[0] if strategy else "shin",
            "weight_scheme": _paper_provenance(strategy)[1] if strategy else "uniform",
            "code_sha": _git_sha(),
            "strategy_config_hash": strategy.config_hash() if strategy else "",
            # Per-bet Kelly stake — uses strategy's kelly_fraction (default 0.5 = half-Kelly)
            "stake": round(_compute_raw_stake(vb["cons"], vb["odds"], bankroll, vb["book"],
                                              vb.get("kelly_fraction", 0.5)), 2),
            "pinnacle_close_prob": "",
            "clv_pct": "",
        })

    if not new_rows:
        return

    if repo is not None:
        repo.add_paper_bets(strategy_name, new_rows)
    else:
        write_header = not log_file.exists()
        with open(log_file, "a", newline="") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            writer = csv.DictWriter(f, fieldnames=_PAPER_FIELDNAMES, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerows(new_rows)
    print(f"[paper:{strategy_name}] {len(new_rows)} bet(s) → logs/paper/{strategy_name}.csv")


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
    parser.add_argument("--max-tennis", type=int, default=8,
                        help="Cap number of active tennis tournaments to scan (saves API quota)")
    args = parser.parse_args()

    BANKROLL = _get_bankroll() if _RISK else float(os.environ.get("BANKROLL", 1000.0))

    now_dt = datetime.now(timezone.utc)
    now = now_dt.strftime("%Y-%m-%d %H:%M UTC")
    label = f" [{args.sports}]" if args.sports else ""
    print(f"=== Multi-Sport Value Bet Scanner{label} === {now}")
    print(f"Bankroll: £{BANKROLL:.0f}\n")

    all_sports = build_sport_list(args.sports, args.max_tennis)

    # A.4: BetRepo handles CSV writes (always) + Azure SQL writes (only when
    # BETS_DB_WRITE=1 and DSN env are set — i.e. WSL dev side, not the Pi).
    repo = BetRepo()
    if repo.db_enabled:
        print("[scan] Dual-write mode: CSV + Azure SQL")
    else:
        print("[scan] CSV-only mode (BETS_DB_WRITE not set)")

    # Pre-flight health check (free, 0 credits): logs which football leagues
    # /sports/ reports active. Evidence-only — does not gate the scan.
    _health_check_sports()

    # Reorder: put the canary league first among football so that if its
    # full-fetch returns 0 events (Odds API empty-payload window) we can abort
    # the rest of football before paying for ~5 more empty calls. NBA/tennis
    # are unaffected.
    football = [s for s in all_sports if s[0].startswith("soccer_")]
    non_football = [s for s in all_sports if not s[0].startswith("soccer_")]
    canary_league = _resolve_canary(_get_canary_league(), football)
    football.sort(key=lambda s: 0 if s[0] == canary_league else 1)
    all_sports = football + non_football

    quota_remaining = "?"
    all_bets: list[dict] = []
    sport_summary: list[str] = []
    scan_date = now[:10]  # "YYYY-MM-DD"
    skip_remaining_football = False

    for sport_key, label, _ in all_sports:
        if skip_remaining_football and sport_key.startswith("soccer_"):
            print(f"  {label:<28} skipped (canary {canary_league} returned 0 events)")
            continue
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

            # Canary trip: if the configured football league returned 0 events,
            # consult the fixture calendar before alerting.
            #   'alert'   — fixtures expected in next 2d → confirmed outage.
            #   'silent'  — no fixtures in window → legitimate quiet period, no ntfy.
            #   'unknown' — calendar absent/stale/corrupt → prior alert behaviour.
            # NBA/tennis still proceed below regardless.
            if sport_key == canary_league and len(events) == 0:
                remaining_football = sum(
                    1 for s in all_sports
                    if s[0].startswith("soccer_") and s[0] != canary_league
                )
                if remaining_football:
                    today_date = datetime.now(timezone.utc).date()
                    verdict, near = _canary_verdict(
                        canary_league, today_date, today_date + timedelta(days=2)
                    )
                    if verdict == "silent":
                        print(
                            f"[canary] {canary_league} returned 0 events — "
                            f"calendar confirms no fixtures in next 2d "
                            f"(international break / empty week). Skipping silently."
                        )
                    else:
                        detail = (
                            f"calendar shows {len(near)} fixture(s) expected. "
                            if verdict == "alert" else ""
                        )
                        print(
                            f"[canary] FAIL — {canary_league} returned 0 events. "
                            f"{detail}"
                            f"Skipping {remaining_football} remaining football league(s)."
                        )
                        notify(
                            "Bets - Odds API canary FAIL",
                            f"{canary_league} returned 0 events. "
                            f"{detail}"
                            f"{remaining_football} football league(s) skipped.",
                            priority="high",
                        )
                    skip_remaining_football = True

            # Paper strategies — reuse same events, no extra API calls
            if _STRATEGIES:
                for strategy in STRATEGIES:
                    try:
                        paper_bets = evaluate_strategy(
                            events, sport_key, strategy,
                            model_signals=_MODEL_SIGNALS,
                            api_to_fd=_API_TO_FD,
                        )
                        _append_paper_csv(strategy.name, paper_bets,
                                          sport_label=label, now=now, scan_date=scan_date,
                                          bankroll=BANKROLL, strategy=strategy, repo=repo)
                    except Exception as pe:
                        print(f"[paper:{strategy.name}] ERROR for {label}: {pe}")
        except Exception as e:
            print(f"  {label:<28} ERROR: {e}")

    # Split into Kaunitz bets (≥3%, shown regardless) and model-filtered bets (2-3%, model agrees)
    kaunitz_bets = [b for b in all_bets if b["edge"] >= MIN_EDGE]
    model_bets   = [b for b in all_bets if b["edge"] < MIN_EDGE
                    and _signal_is_positive(b.get("model_signal", "?"))]
    output_bets  = kaunitz_bets + model_bets

    print(f"\nAPI quota remaining: {quota_remaining}")
    print(f"Kaunitz bets (≥3%): {len(kaunitz_bets)}  |  Model-filtered bets (2-3% + agree): {len(model_bets)}")

    if not output_bets:
        print("No value bets found today across all sports.")
        _maybe_notify_no_bets(len(all_sports))
        return

    # Tag each bet's source bucket before the risk pipeline can drop or reorder bets.
    # Re-splitting by edge alone after the pipeline loses the model-agree condition.
    for vb in output_bets:
        vb["_bucket"] = "model" if vb["edge"] < MIN_EDGE else "kaunitz"

    # Compute raw stakes then apply full risk pipeline.
    # Production bets always use default half-Kelly (A_production).
    # If a graduated variant has kelly_fraction != 0.5, thread vb["kelly_fraction"] here.
    for vb in output_bets:
        if _RISK:
            vb["stake"] = _compute_raw_stake(vb["cons"], vb["odds"], BANKROLL, vb["book"])
        else:
            eff = _effective_odds(vb["odds"], vb["book"])
            vb["stake"] = max(0.0, min(0.5 * (vb["cons"] * eff - 1) / (eff - 1), 0.05)) * BANKROLL

    n_pre_risk = len(output_bets)
    dd_mult = 1.0
    if _RISK:
        current_br, high_water = _load_drawdown_state(BANKROLL)
        dd_mult = _drawdown_multiplier(current_br, high_water)
        if dd_mult < 1.0:
            print(f"[risk] Drawdown brake active: bankroll £{current_br:.0f} vs high water £{high_water:.0f} "
                  f"— stakes halved")
        output_bets = _apply_risk_pipeline(output_bets, BANKROLL, dd_mult)
        # Re-split using pre-pipeline tags (edge-only re-split drops the model-agree condition)
        kaunitz_bets = [b for b in output_bets if b.get("_bucket") == "kaunitz"]
        model_bets   = [b for b in output_bets if b.get("_bucket") == "model"]
        print(f"[risk] After risk pipeline: {len(output_bets)} bet(s) "
              f"(portfolio cap {15}%, fixture cap {5}%, rounding £5)")
        if n_pre_risk > 0 and not output_bets:
            print("[ntfy] All bets dropped by risk pipeline — sending notification.")
            notify(
                "WARNING: Bets - all dropped by risk pipeline",
                f"WARNING: {n_pre_risk} value bet(s) flagged but all dropped by risk pipeline "
                f"(stakes < £5 min after rounding). Consider reviewing bankroll or stake floor — "
                f"you may be missing real edge.",
                priority="default",
            )
    print()

    # Print full detail
    for section, bets in [("≥3% Kaunitz", kaunitz_bets), ("2-3% Model-filtered", model_bets)]:
        if not bets:
            continue
        print(f"=== {section} ===")
        current_sport = None
        for vb in sorted(bets, key=lambda x: (x["sport"], x.get("market", "h2h"), -x["edge"])):
            if vb["sport"] != current_sport:
                current_sport = vb["sport"]
                print(f"--- {current_sport} ---")
            dt = datetime.fromisoformat(vb["commence"].replace("Z", "+00:00")).strftime("%a %d %b %H:%M UTC")
            side = _side_label(vb)
            ms = vb.get("model_signal", "?")
            try:
                ms_label = f"model {float(ms):+.1%}"
            except (ValueError, TypeError):
                ms_label = f"model {ms}"
            print(f"  {vb['home']} vs {vb['away']} [{side}]")
            print(f"    {vb['book']} @ {vb['odds']} | Edge {vb['edge']:.1%} | "
                  f"Consensus {vb['cons']:.1%} | {vb['n_books']} books [{vb['confidence']}] | "
                  f"{ms_label} | Stake £{vb['stake']} | {dt}")
    print()

    # Write bets to CSV log — deduped against same-day entries
    log_file = Path(__file__).parent.parent / "logs" / "bets.csv"
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
                        row.get("market", "h2h"),
                        str(row.get("line", "")),
                    ))

    new_rows = []
    for vb in output_bets:
        dt = datetime.fromisoformat(vb["commence"].replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
        side = {"H": "HOME", "D": "DRAW", "A": "AWAY"}.get(vb["side"], vb["side"])
        key = (dt, vb["home"], vb["away"], side, vb["book"],
               vb.get("market", "h2h"), str(vb.get("line", "")))
        if key in existing_keys:
            continue
        new_rows.append({
            "scanned_at": now,
            "sport": vb["sport"],
            "market": vb.get("market", "h2h"),
            "line": vb.get("line", ""),
            "home": vb["home"],
            "away": vb["away"],
            "kickoff": dt,
            "side": side,
            "book": vb["book"],
            "odds": vb["odds"],
            "impl_raw": round(1.0 / vb["odds"], 4),
            "impl_effective": round(_effective_implied_prob(vb["odds"], vb["book"]), 4),
            "edge": round(vb["cons"] - _effective_implied_prob(vb["odds"], vb["book"]), 4),
            "edge_gross": round(vb.get("edge_gross", vb["edge"]), 4),
            "effective_odds": round(_effective_odds(vb["odds"], vb["book"]), 4),
            "commission_rate": round(_commission_rate(vb["book"]), 4),
            "consensus": round(vb["cons"], 4),
            "pinnacle_cons": round(vb.get("pinnacle_cons", 0.0), 4),
            "n_books": vb["n_books"],
            "confidence": vb["confidence"],
            "model_signal": vb.get("model_signal", "?"),
            "dispersion": round(vb.get("dispersion", 0.0), 4),
            "outlier_z": round(vb.get("outlier_z", 0.0), 3),
            "devig_method": "shin",
            "weight_scheme": "uniform",
            "stake": vb["stake"],
            "result": "",
        })

    if new_rows:
        repo.add_bets(new_rows)
    skipped = len(output_bets) - len(new_rows)
    print(f"[log] {len(new_rows)} bets appended to logs/bets.csv"
          + (f" ({skipped} duplicate(s) skipped)" if skipped else ""))

    # Kaunitz bets (≥3%): notify by confidence tier, with dedupe
    notified = _load_notified()
    high = [vb for vb in kaunitz_bets if vb["confidence"] == "HIGH"]
    med  = [vb for vb in kaunitz_bets if vb["confidence"] == "MED"]
    low  = [vb for vb in kaunitz_bets if vb["confidence"] == "LOW"]

    high_new = _filter_notify(high, notified, now_dt)
    if high_new:
        notify(
            title=f"Bets HIGH - {len(high_new)} bet{'s' if len(high_new) > 1 else ''} (>=30 books)",
            message="\n\n".join(format_bet_line(vb) for vb in high_new),
            priority="high",
        )
        _mark_notified(high_new, notified, now_dt)

    med_new = _filter_notify(med, notified, now_dt)
    if med_new:
        notify(
            title=f"Bets MED - {len(med_new)} bet{'s' if len(med_new) > 1 else ''} (20-29 books)",
            message="\n\n".join(format_bet_line(vb) for vb in med_new),
            priority="default",
        )
        _mark_notified(med_new, notified, now_dt)

    low_new = _filter_notify(low, notified, now_dt)
    if low_new:
        notify(
            title=f"Bets LOW - {len(low_new)} bet{'s' if len(low_new) > 1 else ''} (<20 books)",
            message="\n\n".join(format_bet_line(vb) for vb in low_new),
            priority="low",
        )
        _mark_notified(low_new, notified, now_dt)

    # Model-filtered bets (2-3% + model agrees): single notification, low priority
    model_new = _filter_notify(model_bets, notified, now_dt)
    if model_new:
        notify(
            title=f"Bets MODEL - {len(model_new)} bet{'s' if len(model_new) > 1 else ''} (2-3% + model agree)",
            message="\n\n".join(format_bet_line(vb) for vb in model_new),
            priority="low",
        )
        _mark_notified(model_new, notified, now_dt)

    _save_notified(notified)

    repo.close()


if __name__ == "__main__":
    main()
