"""Offline tests for scripts/research_lib/fetch.py — no real network calls."""

import base64
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures" / "research"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import research_lib.fetch as fetch_mod
from research_lib.fetch import BODY_CAP, FetchResult, fetch


# ── mock helpers ──────────────────────────────────────────────────────────────

class _Resp:
    """Minimal requests.Response stand-in."""
    def __init__(self, text="", json_data=None, status_code=200, content_type="text/html"):
        self.text = text
        self._json = json_data
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}

    def json(self):
        return self._json


def _make_get(url_map: dict):
    """Return a mock requests.get that routes by substring match in url_map keys."""
    def _get(url, **kwargs):
        for key, resp in url_map.items():
            if key in url:
                return resp
        raise AssertionError(f"Unexpected URL in test: {url}")
    return _get


# ── arXiv ─────────────────────────────────────────────────────────────────────

def test_fetch_arxiv_ok(monkeypatch):
    atom = (FIXTURES / "arxiv_atom.xml").read_text()
    monkeypatch.setattr(fetch_mod.requests, "get", _make_get({"export.arxiv.org": _Resp(text=atom)}))

    r = fetch("https://arxiv.org/abs/1710.02824")

    assert r.status == "ok"
    assert "Kaunitz" in r.body_text or "Beating" in r.body_text
    assert r.body_hash != ""
    assert len(r.body_text) <= BODY_CAP
    assert r.error is None


def test_fetch_arxiv_bad_status(monkeypatch):
    monkeypatch.setattr(fetch_mod.requests, "get", _make_get({"export.arxiv.org": _Resp(status_code=429)}))
    r = fetch("https://arxiv.org/abs/1710.02824")
    assert r.status == "skip"
    assert "429" in r.error


def test_fetch_arxiv_invalid_url(monkeypatch):
    r = fetch("https://arxiv.org/abs/")
    assert r.status == "error"


# ── Reddit ────────────────────────────────────────────────────────────────────

def test_fetch_reddit_ok(monkeypatch):
    data = json.loads((FIXTURES / "reddit.json").read_text())
    monkeypatch.setattr(fetch_mod.requests, "get", _make_get({"reddit.com": _Resp(json_data=data)}))

    r = fetch("https://www.reddit.com/r/algobetting/.json")

    assert r.status == "ok"
    assert "Value betting" in r.body_text or "CLV" in r.body_text
    assert len(r.body_text) <= BODY_CAP


def test_fetch_reddit_429(monkeypatch):
    monkeypatch.setattr(fetch_mod.requests, "get", _make_get({"reddit.com": _Resp(status_code=429)}))
    r = fetch("https://www.reddit.com/r/algobetting/.json")
    assert r.status == "skip"


# ── HN Algolia ────────────────────────────────────────────────────────────────

def test_fetch_hn_ok(monkeypatch):
    data = json.loads((FIXTURES / "hn.json").read_text())
    monkeypatch.setattr(fetch_mod.requests, "get", _make_get({"hn.algolia.com": _Resp(json_data=data)}))

    r = fetch("https://hn.algolia.com/api/v1/search_by_date?query=sports+betting&hitsPerPage=20")

    assert r.status == "ok"
    assert "Kaunitz" in r.body_text or "betting" in r.body_text.lower()
    assert len(r.body_text) <= BODY_CAP


# ── GitHub repo ───────────────────────────────────────────────────────────────

def test_fetch_github_repo_ok(monkeypatch):
    readme = json.loads((FIXTURES / "github_readme.json").read_text())
    commits = json.loads((FIXTURES / "github_commits.json").read_text())
    monkeypatch.setattr(fetch_mod.requests, "get", _make_get({
        "/readme": _Resp(json_data=readme),
        "/commits": _Resp(json_data=commits),
    }))

    r = fetch("https://github.com/Lisandro79/BeatTheBookie")

    assert r.status == "ok"
    assert "BeatTheBookie" in r.body_text
    assert "Kelly" in r.body_text
    assert len(r.body_text) <= BODY_CAP


def test_fetch_github_repo_readme_404_still_returns_commits(monkeypatch):
    commits = json.loads((FIXTURES / "github_commits.json").read_text())
    monkeypatch.setattr(fetch_mod.requests, "get", _make_get({
        "/readme": _Resp(status_code=404),
        "/commits": _Resp(json_data=commits),
    }))

    r = fetch("https://github.com/Lisandro79/BeatTheBookie")
    assert r.status == "ok"
    assert "commits" in r.body_text.lower() or "Kelly" in r.body_text


# ── GitHub topic ──────────────────────────────────────────────────────────────

def test_fetch_github_topic_ok(monkeypatch):
    html = (FIXTURES / "topic.html").read_text()
    monkeypatch.setattr(fetch_mod.requests, "get", _make_get({"github.com/topics/": _Resp(text=html)}))

    r = fetch("https://github.com/topics/value-betting")

    assert r.status == "ok"
    # Real repos are extracted from both relative and absolute hrefs.
    lines = r.body_text.splitlines()
    assert "https://github.com/Lisandro79/BeatTheBookie" in lines
    assert "https://github.com/georgedouzas/sports-betting" in lines
    assert "https://github.com/konstanzer/online-sports-betting" in lines
    # Real GitHub h3s contain two anchors (owner + repo); only the two-segment one wins.
    assert "https://github.com/cengizmandros/odds-arb-scanner" in lines
    assert "https://github.com/cengizmandros" not in lines
    # Header/footer noise must not leak through.
    assert "https://docs.github.com" not in r.body_text
    assert "https://github.blog" not in r.body_text
    assert "https://support.github.com" not in r.body_text
    # No malformed double-prefix concatenations.
    assert "https://github.comhttps://" not in r.body_text
    # Excluded path prefixes should be rejected.
    assert "/topics/sports" not in r.body_text
    assert "/orgs/anthropic" not in r.body_text
    assert "/marketplace" not in r.body_text
    # Duplicates from multiple <a> tags should be deduped.
    assert lines.count("https://github.com/Lisandro79/BeatTheBookie") == 1
    assert len(r.body_text) <= BODY_CAP


def test_fetch_github_topic_404(monkeypatch):
    monkeypatch.setattr(fetch_mod.requests, "get", _make_get({"github.com/topics/": _Resp(status_code=404)}))
    r = fetch("https://github.com/topics/nonexistent-topic")
    assert r.status == "skip"
    assert "404" in r.error


# ── Default HTML ──────────────────────────────────────────────────────────────

def test_fetch_html_ok(monkeypatch):
    html = (FIXTURES / "sample.html").read_text()
    monkeypatch.setattr(fetch_mod.requests, "get", _make_get({"example.com": _Resp(text=html)}))

    r = fetch("https://example.com/value-betting-guide")

    assert r.status == "ok"
    assert "Closing Line" in r.body_text or "CLV" in r.body_text
    # nav and footer content should be stripped
    assert "Home | About | Contact" not in r.body_text
    assert "Copyright" not in r.body_text
    # script and style removed
    assert "console.log" not in r.body_text


def test_fetch_html_pdf_skip(monkeypatch):
    monkeypatch.setattr(
        fetch_mod.requests, "get",
        _make_get({"example.com": _Resp(text=b"%PDF", content_type="application/pdf")}),
    )
    r = fetch("https://example.com/paper.pdf")
    assert r.status == "skip"
    assert "PDF" in r.error


def test_fetch_html_503(monkeypatch):
    monkeypatch.setattr(fetch_mod.requests, "get", _make_get({"example.com": _Resp(status_code=503)}))
    r = fetch("https://example.com/down")
    assert r.status == "skip"
    assert "503" in r.error


def test_fetch_html_404_skip(monkeypatch):
    # 4xx other than 429 wasn't handled before — error pages were silently parsed as content.
    monkeypatch.setattr(
        fetch_mod.requests, "get",
        _make_get({"example.com": _Resp(text="<html><body>Not found</body></html>", status_code=404)}),
    )
    r = fetch("https://example.com/missing")
    assert r.status == "skip"
    assert "404" in r.error


def test_fetch_html_strips_aside_and_header(monkeypatch):
    html = """
    <html><body>
    <header><nav>Top nav</nav></header>
    <aside>Sidebar with promos</aside>
    <main><p>The actual content about CLV</p></main>
    <footer>Copyright</footer>
    </body></html>
    """
    monkeypatch.setattr(fetch_mod.requests, "get", _make_get({"example.com": _Resp(text=html)}))
    r = fetch("https://example.com/article")
    assert r.status == "ok"
    assert "actual content about CLV" in r.body_text
    assert "Top nav" not in r.body_text
    assert "Sidebar with promos" not in r.body_text
    assert "Copyright" not in r.body_text


# ── Body cap ──────────────────────────────────────────────────────────────────

def test_body_cap_enforced(monkeypatch):
    large = "x" * (100 * 1024)  # 100 KB of plain text
    html = f"<html><body><p>{large}</p></body></html>"
    monkeypatch.setattr(fetch_mod.requests, "get", _make_get({"example.com": _Resp(text=html)}))

    r = fetch("https://example.com/large")

    assert r.status == "ok"
    assert len(r.body_text) <= BODY_CAP


# ── Hash determinism ──────────────────────────────────────────────────────────

def test_hash_deterministic(monkeypatch):
    html = (FIXTURES / "sample.html").read_text()
    monkeypatch.setattr(fetch_mod.requests, "get", _make_get({"example.com": _Resp(text=html)}))

    r1 = fetch("https://example.com/page")
    r2 = fetch("https://example.com/page")

    assert r1.body_hash == r2.body_hash
    assert r1.body_text == r2.body_text


# ── Connection error + retry ──────────────────────────────────────────────────

def test_connection_error_retries_then_errors(monkeypatch):
    import requests as req

    call_count = {"n": 0}

    def flaky_get(url, **kwargs):
        call_count["n"] += 1
        raise req.ConnectionError("network down")

    monkeypatch.setattr(fetch_mod.requests, "get", flaky_get)
    monkeypatch.setattr(fetch_mod.time, "sleep", lambda s: None)  # no real sleep

    r = fetch("https://example.com/page")

    assert r.status == "error"
    assert "Connection error" in r.error
    assert call_count["n"] == 2  # initial + one retry


def test_connection_error_succeeds_on_retry(monkeypatch):
    import requests as req

    html = (FIXTURES / "sample.html").read_text()
    call_count = {"n": 0}

    def flaky_get(url, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise req.ConnectionError("transient")
        return _Resp(text=html)

    monkeypatch.setattr(fetch_mod.requests, "get", flaky_get)
    monkeypatch.setattr(fetch_mod.time, "sleep", lambda s: None)

    r = fetch("https://example.com/page")

    assert r.status == "ok"
    assert call_count["n"] == 2
