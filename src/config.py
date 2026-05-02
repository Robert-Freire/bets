"""Shared league config loader — single source of truth for active scanner leagues.

Priority: LEAGUES_CONFIG env var → config.json → hardcoded fallback.
All scripts should import from here instead of duplicating the load logic.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

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


def _validate(leagues: list, source) -> None:
    for entry in leagues:
        missing = [k for k in ("key", "label", "min_books") if k not in entry]
        if missing:
            raise RuntimeError(
                f"League entry in {source} missing required keys {missing}: {entry}"
            )
