"""Open-search backends for research scanner (Phase 11.7)."""

import logging
import os
import urllib.parse
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

BACKENDS = ["arxiv", "hn", "github", "ddg"]

_USER_AGENT = "bets-research-scanner/0.1"
_TIMEOUT = 10
_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
_log = logging.getLogger("research_scan.search")


def search(query: str, backend: str) -> list[str]:
    """Return discovered URLs for *query* from *backend*. Always returns a list."""
    try:
        if backend == "arxiv":
            return _arxiv(query)
        if backend == "hn":
            return _hn(query)
        if backend == "github":
            return _github(query)
        if backend == "ddg":
            return _ddg(query)
        _log.warning("unknown search backend: %r", backend)
        return []
    except Exception as exc:
        _log.warning("search backend=%s query=%r failed: %s", backend, query, exc)
        return []


# ── internal helpers ──────────────────────────────────────────────────────────

def _get(url: str, extra_headers: dict | None = None) -> requests.Response:
    headers = {"User-Agent": _USER_AGENT}
    if extra_headers:
        headers.update(extra_headers)
    return requests.get(url, headers=headers, timeout=_TIMEOUT)


def _arxiv(query: str) -> list[str]:
    q = urllib.parse.quote(f"all:{query}")
    url = f"https://export.arxiv.org/api/query?search_query={q}&sortBy=submittedDate&max_results=10"
    r = _get(url)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    urls = []
    for entry in root.findall("a:entry", _ATOM_NS):
        id_text = (entry.findtext("a:id", "", _ATOM_NS) or "").strip()
        if id_text:
            urls.append(id_text)
    return urls


def _hn(query: str) -> list[str]:
    q = urllib.parse.quote(query)
    url = f"https://hn.algolia.com/api/v1/search_by_date?query={q}&hitsPerPage=10"
    r = _get(url)
    r.raise_for_status()
    return [hit["url"] for hit in r.json().get("hits", []) if hit.get("url")]


def _github(query: str) -> list[str]:
    q = urllib.parse.quote(query)
    url = f"https://api.github.com/search/repositories?q={q}&sort=updated&per_page=10"
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = _get(url, headers)
    r.raise_for_status()
    return [item["html_url"] for item in r.json().get("items", []) if item.get("html_url")]


def _ddg(query: str) -> list[str]:
    try:
        q = urllib.parse.quote(query)
        url = f"https://duckduckgo.com/html/?q={q}"
        r = _get(url)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        return [
            a["href"]
            for a in soup.select(".result__a")
            if a.get("href", "").startswith("http")
        ]
    except Exception as exc:
        _log.warning("ddg search query=%r failed: %s", query, exc)
        return []
