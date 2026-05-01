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
