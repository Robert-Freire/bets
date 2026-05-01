"""Tests for M.2: config-driven league list and extra_markets."""
import json
import os

import pytest

os.environ.setdefault("ODDS_API_KEY", "test-key")

import scripts.scan_odds as scan_odds  # noqa: E402


# ── (a) Explicit LEAGUES_CONFIG with valid leagues array loads correctly ───────

def test_leagues_config_env_loads_leagues(tmp_path, monkeypatch):
    cfg = {
        "leagues": [
            {"key": "soccer_epl", "label": "EPL", "min_books": 20},
            {"key": "soccer_germany_bundesliga", "label": "Bundesliga", "min_books": 20},
        ],
        "extra_markets": [],
    }
    cfg_file = tmp_path / "custom.json"
    cfg_file.write_text(json.dumps(cfg))

    monkeypatch.setenv("LEAGUES_CONFIG", str(cfg_file))
    result = scan_odds._load_config()

    assert len(result["leagues"]) == 2
    assert result["leagues"][0]["key"] == "soccer_epl"
    assert result["extra_markets"] == []


# ── (b) No LEAGUES_CONFIG + config.json without 'leagues' → hardcoded fallback ─

def test_fallback_when_leagues_absent(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"canary_league": "soccer_epl"}))

    monkeypatch.delenv("LEAGUES_CONFIG", raising=False)

    # Patch the Path resolution inside _load_config to use our temp config.json
    import pathlib
    monkeypatch.setattr(scan_odds, "Path", lambda *a, **kw: (
        pathlib.Path(cfg_file) if a and str(pathlib.Path(*a)).endswith("config.json")
        else pathlib.Path(*a, **kw)
    ))

    result = scan_odds._load_config()

    keys = [e["key"] for e in result["leagues"]]
    assert "soccer_epl" in keys
    assert "soccer_germany_bundesliga" in keys
    assert len(keys) == 6  # six hardcoded football leagues


# ── (c) extra_markets reaches the fetch_odds request params ───────────────────

def test_extra_markets_in_request(monkeypatch):
    monkeypatch.setitem(scan_odds._CONFIG, "extra_markets", ["spreads"])

    captured = {}

    def fake_api_get(path, params):
        captured["params"] = params
        return [], "500"

    monkeypatch.setattr(scan_odds, "api_get", fake_api_get)
    scan_odds.fetch_odds("soccer_epl")

    assert captured["params"]["markets"] == "h2h,spreads"


# ── (d) LEAGUES_CONFIG → file exists but no 'leagues' key → clear error ───────

def test_leagues_config_env_missing_leagues_raises(tmp_path, monkeypatch):
    cfg_file = tmp_path / "empty.json"
    cfg_file.write_text("{}")

    monkeypatch.setenv("LEAGUES_CONFIG", str(cfg_file))

    with pytest.raises(RuntimeError, match="leagues"):
        scan_odds._load_config()


# ── (e) LEAGUES_CONFIG → non-existent file → clear error ─────────────────────

def test_leagues_config_env_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("LEAGUES_CONFIG", str(tmp_path / "nonexistent.json"))

    with pytest.raises(RuntimeError, match="does not exist"):
        scan_odds._load_config()
