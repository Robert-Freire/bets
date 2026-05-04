"""Verify fetch_odds requests only the h2h market (M.0) and boot-time guards."""
import os
import sys

os.environ.setdefault("ODDS_API_KEY", "test-key")

import scripts.scan_odds as scan_odds  # noqa: E402


def test_fetch_odds_requests_only_h2h(monkeypatch):
    captured = {}

    def fake_api_get(path, params):
        captured["params"] = params
        return [], "500"

    monkeypatch.setattr(scan_odds, "api_get", fake_api_get)

    scan_odds.fetch_odds("soccer_epl")

    assert captured["params"]["markets"] == "h2h", (
        f"Expected markets='h2h', got {captured['params']['markets']!r}"
    )
    assert "," not in captured["params"]["markets"], (
        "markets param must not contain multiple values"
    )


def test_main_exits_when_db_write_unset(monkeypatch):
    """A.9: scan_odds.main() must refuse to run without BETS_DB_WRITE=1."""
    monkeypatch.delenv("BETS_DB_WRITE", raising=False)
    monkeypatch.setattr(sys, "argv", ["scan_odds.py"])
    import pytest
    with pytest.raises(SystemExit) as exc_info:
        scan_odds.main()
    assert exc_info.value.code == 1
