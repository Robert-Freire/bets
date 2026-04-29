"""Fetch a URL and return cleaned text capped at 20 KB."""

import base64
import hashlib
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

BODY_CAP = 20 * 1024  # ~20 KB; applied as a character slice on cleaned text
TIMEOUT = 10
USER_AGENT = "bets-research-scanner/0.1"
_GITHUB_API = "https://api.github.com"
_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
_SKIP_HTTP = {429, 500, 501, 502, 503, 504}
_REPO_PATH_RE = re.compile(r"^/[^/]+/[^/]+/?$")
_REPO_ABS_RE  = re.compile(r"^https://github\.com/[^/]+/[^/]+/?$")
_TOPIC_REJECT_PREFIXES = ("/topics/", "/search", "/login", "/signup", "/sponsors", "/marketplace", "/orgs/")


@dataclass
class FetchResult:
    url: str
    status: str        # "ok" | "skip" | "error"
    body_text: str     # cleaned, capped at BODY_CAP bytes
    body_hash: str     # SHA256 of body_text
    fetched_at: str    # ISO 8601 UTC
    error: str | None = field(default=None)


def fetch(url: str) -> FetchResult:
    try:
        if "arxiv.org/abs/" in url:
            return _fetch_arxiv(url)
        if "hn.algolia.com/api/" in url:
            return _fetch_hn(url)
        if "reddit.com" in url and url.endswith(".json"):
            return _fetch_reddit(url)
        if "github.com/topics/" in url:
            return _fetch_github_topic(url)
        if re.match(r"https?://github\.com/[^/]+/[^/]+/?$", url):
            return _fetch_github_repo(url)
        return _fetch_html(url)
    except requests.ConnectionError as exc:
        return _error(url, f"Connection error: {exc}")
    except requests.Timeout:
        return _skip(url, "Timeout")
    except Exception as exc:  # noqa: BLE001
        return _error(url, str(exc))


# ── internal helpers ──────────────────────────────────────────────────────────

def _get(url: str, headers: dict | None = None, **kwargs) -> requests.Response:
    merged = {"User-Agent": USER_AGENT}
    if headers:
        merged.update(headers)
    try:
        return requests.get(url, headers=merged, timeout=TIMEOUT, **kwargs)
    except requests.ConnectionError:
        time.sleep(2)
        return requests.get(url, headers=merged, timeout=TIMEOUT, **kwargs)


def _cap_and_hash(text: str) -> tuple[str, str]:
    # Slice is character-based; resulting bytes ≈ BODY_CAP for ASCII, slightly larger for multi-byte UTF-8.
    capped = text[:BODY_CAP]
    digest = hashlib.sha256(capped.encode("utf-8", errors="replace")).hexdigest()
    return capped, digest


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ok(url: str, text: str) -> FetchResult:
    body, h = _cap_and_hash(text)
    return FetchResult(url=url, status="ok", body_text=body, body_hash=h, fetched_at=_now())


def _skip(url: str, reason: str) -> FetchResult:
    return FetchResult(url=url, status="skip", body_text="", body_hash="", fetched_at=_now(), error=reason)


def _error(url: str, reason: str) -> FetchResult:
    return FetchResult(url=url, status="error", body_text="", body_hash="", fetched_at=_now(), error=reason)


# ── per-handler fetchers ──────────────────────────────────────────────────────

def _fetch_arxiv(url: str) -> FetchResult:
    m = re.search(r"arxiv\.org/abs/([\w.]+)", url)
    if not m:
        return _error(url, "Could not parse arXiv ID")
    api_url = f"http://export.arxiv.org/api/query?id_list={m.group(1)}"
    r = _get(api_url)
    if r.status_code in _SKIP_HTTP:
        return _skip(url, f"HTTP {r.status_code}")
    root = ET.fromstring(r.text)
    entry = root.find("a:entry", _ATOM_NS)
    if entry is None:
        return _skip(url, "No arXiv entry in response")
    title = (entry.findtext("a:title", "", _ATOM_NS) or "").strip()
    summary = (entry.findtext("a:summary", "", _ATOM_NS) or "").strip()
    return _ok(url, f"Title: {title}\n\nAbstract: {summary}")


def _fetch_reddit(url: str) -> FetchResult:
    r = _get(url)
    if r.status_code in _SKIP_HTTP:
        return _skip(url, f"HTTP {r.status_code}")
    data = r.json()
    if isinstance(data, list):
        children = data[0].get("data", {}).get("children", [])
    else:
        children = data.get("data", {}).get("children", [])
    lines = []
    for child in children[:20]:
        d = child.get("data", {})
        title = d.get("title", "")
        body = d.get("selftext", "") or d.get("url", "")
        lines.append(f"## {title}\n{body}")
    return _ok(url, "\n\n".join(lines))


def _fetch_hn(url: str) -> FetchResult:
    r = _get(url)
    if r.status_code in _SKIP_HTTP:
        return _skip(url, f"HTTP {r.status_code}")
    hits = r.json().get("hits", [])
    lines = []
    for hit in hits:
        title = hit.get("title") or hit.get("story_title") or ""
        link = hit.get("url") or ""
        pts = hit.get("points") or 0
        lines.append(f"- {title} ({pts} pts) — {link}")
    return _ok(url, "\n".join(lines))


def _fetch_github_repo(url: str) -> FetchResult:
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+?)/?$", url)
    if not m:
        return _error(url, "Could not parse GitHub owner/repo")
    owner, repo = m.group(1), m.group(2)
    gh_headers = {"Accept": "application/vnd.github+json"}
    parts = []

    r = _get(f"{_GITHUB_API}/repos/{owner}/{repo}/readme", headers=gh_headers)
    if r.status_code == 200:
        raw = base64.b64decode(r.json().get("content", "")).decode("utf-8", errors="replace")
        parts.append(f"# README\n{raw}")
    elif r.status_code in _SKIP_HTTP:
        return _skip(url, f"GitHub readme HTTP {r.status_code}")

    r2 = _get(f"{_GITHUB_API}/repos/{owner}/{repo}/commits?per_page=10", headers=gh_headers)
    if r2.status_code == 200:
        msgs = []
        for c in r2.json():
            sha = c.get("sha", "")[:8]
            msg = (c.get("commit", {}).get("message", "") or "").splitlines()[0]
            msgs.append(f"{sha} {msg}")
        parts.append("# Recent commits\n" + "\n".join(msgs))
    elif r2.status_code in _SKIP_HTTP:
        return _skip(url, f"GitHub commits HTTP {r2.status_code}")

    if not parts:
        return _skip(url, "No content from GitHub repo")
    return _ok(url, "\n\n".join(parts))


def _fetch_github_topic(url: str) -> FetchResult:
    r = _get(url, headers={"Accept": "text/html"})
    if r.status_code in _SKIP_HTTP:
        return _skip(url, f"HTTP {r.status_code}")
    if r.status_code >= 400:
        return _skip(url, f"HTTP {r.status_code}")
    soup = BeautifulSoup(r.text, "html.parser")
    repos: list[str] = []
    seen: set[str] = set()
    # Repo cards on a GitHub topic page live inside <h3>; feature/nav links don't.
    # Scoping to <h3> filters /features/*, /resources/*, header/footer noise.
    # Each repo h3 contains two <a> tags (owner profile + full repo); _REPO_PATH_RE's
    # two-segment requirement filters out the owner-only link automatically.
    for h3 in soup.find_all("h3"):
        for a in h3.find_all("a", href=True):
            href = a["href"]
            if "?" in href or "#" in href:
                continue
            if _REPO_PATH_RE.match(href):
                path = href
            elif _REPO_ABS_RE.match(href):
                path = href[len("https://github.com"):]
            else:
                continue
            if path.startswith(_TOPIC_REJECT_PREFIXES):
                continue
            full = f"https://github.com{path}"
            if full not in seen:
                seen.add(full)
                repos.append(full)
    return _ok(url, "\n".join(repos[:30]))


def _fetch_html(url: str) -> FetchResult:
    r = _get(url)
    if "application/pdf" in r.headers.get("Content-Type", ""):
        return _skip(url, "PDF skipped (v1)")
    if r.status_code in _SKIP_HTTP:
        return _skip(url, f"HTTP {r.status_code}")
    if r.status_code >= 400:
        return _skip(url, f"HTTP {r.status_code}")
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "aside", "header"]):
        tag.decompose()
    return _ok(url, soup.get_text(separator="\n", strip=True))
