"""Shared config loader — single source of truth for leagues and book attributes.

Priority: LEAGUES_CONFIG env var → config.json → hardcoded fallback.
All scripts should import from here instead of duplicating the load logic.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

_HARDCODED_BOOKS = [
    {"key": "betfair_ex_uk",  "label": "Betfair Exchange (UK MBR)",  "type": "exchange",    "license": "UK",     "commission_rate": 0.05},
    {"key": "smarkets",       "label": "Smarkets",                   "type": "exchange",    "license": "UK",     "commission_rate": 0.02},
    {"key": "matchbook",      "label": "Matchbook",                  "type": "exchange",    "license": "UK",     "commission_rate": 0.02},
    {"key": "pinnacle",       "label": "Pinnacle",                   "type": "sportsbook",  "license": "non-UK", "commission_rate": 0.0},
    {"key": "betfair_sb_uk",  "label": "Betfair Sportsbook",         "type": "sportsbook",  "license": "UK",     "commission_rate": 0.0},
    {"key": "betfred_uk",     "label": "Betfred",                    "type": "sportsbook",  "license": "UK",     "commission_rate": 0.0},
    {"key": "williamhill",    "label": "William Hill",               "type": "sportsbook",  "license": "UK",     "commission_rate": 0.0},
    {"key": "coral",          "label": "Coral",                      "type": "sportsbook",  "license": "UK",     "commission_rate": 0.0},
    {"key": "ladbrokes_uk",   "label": "Ladbrokes",                  "type": "sportsbook",  "license": "UK",     "commission_rate": 0.0},
    {"key": "skybet",         "label": "Sky Bet",                    "type": "sportsbook",  "license": "UK",     "commission_rate": 0.0},
    {"key": "paddypower",     "label": "Paddy Power",                "type": "sportsbook",  "license": "UK",     "commission_rate": 0.0},
    {"key": "boylesports",    "label": "BoyleSports",                "type": "sportsbook",  "license": "UK",     "commission_rate": 0.0},
    {"key": "betvictor",      "label": "BetVictor",                  "type": "sportsbook",  "license": "UK",     "commission_rate": 0.0},
    {"key": "betway",         "label": "Betway",                     "type": "sportsbook",  "license": "UK",     "commission_rate": 0.0},
    {"key": "leovegas",       "label": "LeoVegas",                   "type": "sportsbook",  "license": "UK",     "commission_rate": 0.0},
    {"key": "casumo",         "label": "Casumo",                     "type": "sportsbook",  "license": "UK",     "commission_rate": 0.0},
    {"key": "virginbet",      "label": "Virgin Bet",                 "type": "sportsbook",  "license": "UK",     "commission_rate": 0.0},
    {"key": "livescorebet",   "label": "LiveScore Bet",              "type": "sportsbook",  "license": "UK",     "commission_rate": 0.0},
    {"key": "sport888",       "label": "888Sport",                   "type": "sportsbook",  "license": "UK",     "commission_rate": 0.0},
    {"key": "grosvenor",      "label": "Grosvenor",                  "type": "sportsbook",  "license": "UK",     "commission_rate": 0.0},
]

_HARDCODED_FOOTBALL = [
    {"key": "soccer_epl",                 "label": "EPL",          "min_books": 20},
    {"key": "soccer_germany_bundesliga",  "label": "Bundesliga",   "min_books": 20},
    {"key": "soccer_italy_serie_a",       "label": "Serie A",      "min_books": 20},
    {"key": "soccer_efl_champ",           "label": "Championship", "min_books": 25},
    {"key": "soccer_france_ligue_one",    "label": "Ligue 1",      "min_books": 20},
    {"key": "soccer_germany_bundesliga2", "label": "Bundesliga 2", "min_books": 20},
]


def load_config() -> dict:
    """Load raw scanner config respecting LEAGUES_CONFIG env var."""
    env_path = os.environ.get("LEAGUES_CONFIG")
    if env_path:
        path = Path(env_path)
        if not path.exists():
            raise RuntimeError(f"LEAGUES_CONFIG={env_path} does not exist.")
        cfg = json.loads(path.read_text())
        if "leagues" not in cfg:
            raise RuntimeError(
                f"Config {path} has no 'leagues' key. "
                'Required: [{"key": ..., "label": ..., "min_books": ...}, ...]'
            )
        _validate(cfg["leagues"], path)
        return cfg

    path = _ROOT / "config.json"
    cfg: dict = {}
    if path.exists():
        try:
            cfg = json.loads(path.read_text())
        except Exception:
            pass
    if "leagues" not in cfg:
        cfg["leagues"] = list(_HARDCODED_FOOTBALL)
    else:
        _validate(cfg["leagues"], path)
    return cfg


def load_leagues() -> list[dict]:
    """Return active leagues enriched with fdco_code from downloader.LEAGUES.

    Each entry: {key, label, min_books, fdco_code (when known)}.
    Adding a league to config.json / config.dev.json automatically flows through
    to any script that builds its mappings from this function.
    """
    from src.data.downloader import LEAGUES as _DL

    result = []
    for entry in load_config()["leagues"]:
        enriched = dict(entry)
        if "fdco_code" not in enriched:
            dl_entry = _DL.get(entry["key"], {})
            if "fd_code" in dl_entry:
                enriched["fdco_code"] = dl_entry["fd_code"]
        result.append(enriched)
    return result


def load_books() -> list[dict]:
    """Return book definitions from config.json, falling back to the hardcoded list.

    Each entry: {key, label, type, license, commission_rate}.
    """
    cfg = load_config()
    books = cfg.get("books", _HARDCODED_BOOKS)
    _validate_books(books, "config")
    return books


def _validate(leagues: list, source) -> None:
    for entry in leagues:
        missing = [k for k in ("key", "label", "min_books") if k not in entry]
        if missing:
            raise RuntimeError(
                f"League entry in {source} missing required keys {missing}: {entry}"
            )


def _validate_books(books: list, source) -> None:
    keys_seen: set[str] = set()
    for entry in books:
        missing = [k for k in ("key", "label", "type", "license", "commission_rate") if k not in entry]
        if missing:
            raise RuntimeError(
                f"Book entry in {source} missing required keys {missing}: {entry}"
            )
        if entry["key"] in keys_seen:
            raise RuntimeError(f"Duplicate book key in {source}: {entry['key']}")
        keys_seen.add(entry["key"])
