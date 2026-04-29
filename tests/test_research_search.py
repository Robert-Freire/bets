"""Offline tests for scripts/research_lib/search.py — no real network calls."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures" / "research"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import research_lib.search as search_mod
from research_lib.search import BACKENDS, search


class _Resp:
    """Minimal requests.Response stand-in."""
    def __init__(self, text="", json_data=None, status_code=200):
        self.text = text
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._json


# ── backends constant ─────────────────────────────────────────────────────────

def test_backends_has_four_members():
    assert set(BACKENDS) == {"arxiv", "hn", "github", "ddg"}


# ── arXiv ─────────────────────────────────────────────────────────────────────

def test_search_arxiv_returns_entry_ids(monkeypatch):
    xml_text = (FIXTURES / "search_arxiv.xml").read_text()
    monkeypatch.setattr(search_mod.requests, "get", lambda url, **kw: _Resp(text=xml_text))
    urls = search("value betting", "arxiv")
    assert len(urls) == 2
    assert all("arxiv.org/abs/" in u for u in urls)


def test_search_arxiv_http_error_returns_empty(monkeypatch):
    monkeypatch.setattr(search_mod.requests, "get", lambda url, **kw: _Resp(status_code=503))
    urls = search("value betting", "arxiv")
    assert urls == []


# ── HN ────────────────────────────────────────────────────────────────────────

def test_search_hn_skips_null_urls(monkeypatch):
    data = json.loads((FIXTURES / "search_hn.json").read_text())
    monkeypatch.setattr(search_mod.requests, "get", lambda url, **kw: _Resp(json_data=data))
    urls = search("value betting", "hn")
    assert len(urls) == 2
    assert all("hn-example.com" in u for u in urls)


# ── GitHub ────────────────────────────────────────────────────────────────────

def test_search_github_skips_null_html_url(monkeypatch):
    data = json.loads((FIXTURES / "search_github.json").read_text())
    monkeypatch.setattr(search_mod.requests, "get", lambda url, **kw: _Resp(json_data=data))
    urls = search("value betting", "github")
    assert len(urls) == 2
    assert all("github.com" in u for u in urls)


# ── DDG ───────────────────────────────────────────────────────────────────────

def test_search_ddg_extracts_result_a_links(monkeypatch):
    html_text = (FIXTURES / "search_ddg.html").read_text()
    monkeypatch.setattr(search_mod.requests, "get", lambda url, **kw: _Resp(text=html_text))
    urls = search("value betting", "ddg")
    assert len(urls) == 2
    assert all(u.startswith("https://ddg-example.com") for u in urls)


def test_search_ddg_exception_returns_empty(monkeypatch):
    def _raise(*args, **kw):
        raise OSError("network failure")
    monkeypatch.setattr(search_mod.requests, "get", _raise)
    urls = search("value betting", "ddg")
    assert urls == []


# ── unknown backend ───────────────────────────────────────────────────────────

def test_search_unknown_backend_returns_empty():
    urls = search("value betting", "unknown_backend")
    assert urls == []


# ── assemble_pending tags integration ─────────────────────────────────────────

def test_assemble_pending_with_tags_adds_backend_prefix():
    from research_lib.fetch import FetchResult
    from research_lib.state import assemble_pending

    r = FetchResult(
        url="https://example.com/paper",
        status="ok",
        body_text="some content",
        body_hash="abc123",
        fetched_at="2026-04-29T10:00:00Z",
    )
    segments = assemble_pending([r], tags={"https://example.com/paper": "arxiv"})
    assert len(segments) == 1
    assert "[backend:arxiv] https://example.com/paper" in segments[0]


def test_assemble_pending_without_tags_no_backend_prefix():
    from research_lib.fetch import FetchResult
    from research_lib.state import assemble_pending

    r = FetchResult(
        url="https://example.com/paper",
        status="ok",
        body_text="some content",
        body_hash="abc123",
        fetched_at="2026-04-29T10:00:00Z",
    )
    segments = assemble_pending([r])
    assert len(segments) == 1
    assert "[backend:" not in segments[0]
    assert "## Source: https://example.com/paper" in segments[0]
