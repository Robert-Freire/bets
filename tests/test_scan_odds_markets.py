"""Verify fetch_odds requests only the h2h market (M.0)."""
import os

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
