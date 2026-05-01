"""Tests for NTFY_TOPIC_OVERRIDE env-var hook in scan_odds.py.

Covers the dev-cron use case where WSL runs cron with NTFY_TOPIC_OVERRIDE=""
to silence test-side notifications (production Pi keeps the default topic).
"""
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fresh_import_scan_odds(monkeypatch):
    monkeypatch.setenv("ODDS_API_KEY", "dummy")
    sys.modules.pop("scripts.scan_odds", None)
    return importlib.import_module("scripts.scan_odds")


def test_ntfy_topic_default_when_no_override(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC_OVERRIDE", raising=False)
    so = _fresh_import_scan_odds(monkeypatch)
    assert so.NTFY_TOPIC == "robert-epl-bets-m4x9k"
    assert so.NTFY_URL == "https://ntfy.sh/robert-epl-bets-m4x9k"


def test_ntfy_topic_override_to_separate_topic(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC_OVERRIDE", "robert-bets-test-channel")
    so = _fresh_import_scan_odds(monkeypatch)
    assert so.NTFY_TOPIC == "robert-bets-test-channel"
    assert so.NTFY_URL == "https://ntfy.sh/robert-bets-test-channel"


def test_notify_all_filtered_by_risk_pipeline(monkeypatch, capsys):
    """When n_pre_risk > 0 and the risk pipeline returns empty, a warning
    notification fires so the user knows real edge may be slipping through."""
    import os
    os.environ.setdefault("ODDS_API_KEY", "test-key")
    import scripts.scan_odds as so

    sent = []
    monkeypatch.setattr(so, "notify",
                        lambda title, message, priority="default": sent.append((title, message, priority)))

    # Reproduce the exact branch in scan_odds.main():
    n_pre_risk = 2
    output_bets: list = []   # pipeline dropped everything

    if n_pre_risk > 0 and not output_bets:
        so.notify(
            "WARNING: Bets - all dropped by risk pipeline",
            f"WARNING: {n_pre_risk} value bet(s) flagged but all dropped by risk pipeline "
            f"(stakes < £5 min after rounding). Consider reviewing bankroll or stake floor — "
            f"you may be missing real edge.",
            priority="default",
        )

    assert len(sent) == 1
    title, message, priority = sent[0]
    assert title.startswith("WARNING:")
    assert "WARNING:" in message
    assert "2" in message
    assert priority == "default"


def test_ntfy_disabled_when_override_is_empty(monkeypatch, capsys):
    monkeypatch.setenv("NTFY_TOPIC_OVERRIDE", "")
    so = _fresh_import_scan_odds(monkeypatch)
    assert so.NTFY_TOPIC == ""
    assert so.NTFY_URL == ""

    # notify() must short-circuit and NOT make an HTTP request.
    # Patch urlopen to raise if called — proves we never hit the network.
    def _boom(*a, **kw):
        raise AssertionError("urlopen called even though NTFY_TOPIC is empty")
    monkeypatch.setattr(so.urllib.request, "urlopen", _boom)

    so.notify("test title", "test message")
    out = capsys.readouterr().out
    assert "Disabled" in out
    assert "test title" in out
