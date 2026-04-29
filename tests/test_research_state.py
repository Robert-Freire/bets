"""Offline tests for scripts/research_lib/state.py."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from research_lib.fetch import FetchResult
from research_lib.state import (
    assemble_pending,
    is_changed,
    load_seen,
    save_seen,
    update_seen,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _result(url="https://example.com", status="ok", body="hello world", hash_="abc123"):
    return FetchResult(
        url=url,
        status=status,
        body_text=body,
        body_hash=hash_,
        fetched_at="2026-04-29T10:00:00Z",
    )


def _seen_entry(hash_="abc123", last_changed="2026-04-29T10:00:00Z"):
    return {
        "hash": hash_,
        "fetched_at": "2026-04-29T10:00:00Z",
        "status": "ok",
        "last_changed_at": last_changed,
    }


# ── load_seen / save_seen ────────────────────────────────────────────────────

def test_load_seen_missing_file(tmp_path):
    result = load_seen(path=tmp_path / "nonexistent.json")
    assert result == {}


def test_load_seen_existing_file(tmp_path):
    data = {"https://x.com": _seen_entry()}
    p = tmp_path / "seen.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    assert load_seen(path=p) == data


def test_save_seen_writes_correct_json(tmp_path):
    p = tmp_path / "seen.json"
    data = {"https://x.com": _seen_entry()}
    save_seen(data, path=p)
    assert json.loads(p.read_text()) == data


def test_save_seen_atomic_tmp_file_removed(tmp_path):
    p = tmp_path / "seen.json"
    save_seen({"https://x.com": _seen_entry()}, path=p)
    assert not (tmp_path / "seen.tmp").exists()
    assert p.exists()


def test_save_seen_atomic_no_corrupt_on_replace_failure(tmp_path):
    """If os.replace fails the original file is untouched."""
    p = tmp_path / "seen.json"
    original = {"https://original.com": _seen_entry("oldhash")}
    p.write_text(json.dumps(original), encoding="utf-8")

    new_data = {"https://new.com": _seen_entry("newhash")}
    with patch("research_lib.state.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            save_seen(new_data, path=p)

    # Original file must be untouched
    assert json.loads(p.read_text()) == original


# ── is_changed ────────────────────────────────────────────────────────────────

def test_is_changed_new_url():
    assert is_changed("https://new.com", "abc", {}) is True


def test_is_changed_same_hash():
    seen = {"https://x.com": _seen_entry("abc123")}
    assert is_changed("https://x.com", "abc123", seen) is False


def test_is_changed_different_hash():
    seen = {"https://x.com": _seen_entry("oldhash")}
    assert is_changed("https://x.com", "newhash", seen) is True


# ── update_seen ───────────────────────────────────────────────────────────────

def test_update_seen_new_entry():
    seen: dict = {}
    r = _result("https://x.com", hash_="abc123")
    update_seen(seen, r)
    assert seen["https://x.com"]["hash"] == "abc123"
    assert seen["https://x.com"]["last_changed_at"] == "2026-04-29T10:00:00Z"


def test_update_seen_unchanged_hash_preserves_last_changed():
    seen = {"https://x.com": _seen_entry("abc123", last_changed="2026-01-01T00:00:00Z")}
    r = _result("https://x.com", hash_="abc123")
    r = FetchResult(
        url="https://x.com",
        status="ok",
        body_text="hello",
        body_hash="abc123",
        fetched_at="2026-04-29T10:00:00Z",
    )
    update_seen(seen, r)
    # Hash unchanged → last_changed_at must stay the original value
    assert seen["https://x.com"]["last_changed_at"] == "2026-01-01T00:00:00Z"


def test_update_seen_changed_hash_updates_last_changed():
    seen = {"https://x.com": _seen_entry("oldhash", last_changed="2026-01-01T00:00:00Z")}
    r = FetchResult(
        url="https://x.com",
        status="ok",
        body_text="new content",
        body_hash="newhash",
        fetched_at="2026-04-29T10:00:00Z",
    )
    update_seen(seen, r)
    assert seen["https://x.com"]["last_changed_at"] == "2026-04-29T10:00:00Z"


# ── assemble_pending ──────────────────────────────────────────────────────────

def test_assemble_pending_empty():
    assert assemble_pending([]) == []


def test_assemble_pending_skip_empty_body():
    r = _result(status="skip", body="", hash_="")
    assert assemble_pending([r]) == []


def test_assemble_pending_single_result_format():
    r = _result("https://x.com", body="some text", hash_="h1")
    segments = assemble_pending([r])
    assert len(segments) == 1
    seg = segments[0]
    assert "## Source: https://x.com" in seg
    assert "2026-04-29T10:00:00Z — status: ok" in seg
    assert "some text" in seg
    assert seg.strip().endswith("---")


def test_assemble_pending_multiple_fit_in_one_segment():
    results = [_result(f"https://x{i}.com", body="a" * 100, hash_=str(i)) for i in range(5)]
    segments = assemble_pending(results, cap_bytes=200_000)
    assert len(segments) == 1


def test_assemble_pending_200kb_cap_splits_into_multiple_segments():
    # Each result body is ~100 KB; two results → ~200 KB → should split at boundary
    body = "x" * 100_000
    results = [_result(f"https://x{i}.com", body=body, hash_=str(i)) for i in range(4)]
    segments = assemble_pending(results, cap_bytes=200_000)
    assert len(segments) >= 2
    total_bytes = sum(len(s.encode("utf-8")) for s in segments)
    # All content preserved across segments (modulo WARNING headers)
    for i in range(4):
        url = f"https://x{i}.com"
        found = any(url in seg for seg in segments)
        assert found, f"{url} missing from segments"


def test_assemble_pending_oversized_single_source():
    # Single result larger than cap_bytes
    body = "x" * 300_000
    r = _result("https://huge.com", body=body, hash_="big")
    segments = assemble_pending([r], cap_bytes=200_000)
    assert len(segments) == 1
    assert "# WARNING: oversized" in segments[0]
    assert "https://huge.com" in segments[0]


def test_assemble_pending_oversized_does_not_merge_with_others():
    """An oversized entry gets its own segment; surrounding results go in separate segments."""
    small_body = "a" * 100
    big_body = "x" * 300_000
    results = [
        _result("https://before.com", body=small_body, hash_="h1"),
        _result("https://huge.com", body=big_body, hash_="h2"),
        _result("https://after.com", body=small_body, hash_="h3"),
    ]
    segments = assemble_pending(results, cap_bytes=200_000)
    oversized = [s for s in segments if "WARNING: oversized" in s]
    assert len(oversized) == 1
    assert "https://huge.com" in oversized[0]
    # before and after must appear in other segments
    all_text = "\n".join(segments)
    assert "https://before.com" in all_text
    assert "https://after.com" in all_text


def test_assemble_pending_350kb_produces_two_or_more_segments():
    """350 KB of content must produce ≥2 segments under a 200 KB cap."""
    body = "y" * 90_000   # ~90 KB each; 4 × 90 KB = ~360 KB > 200 KB
    results = [_result(f"https://s{i}.com", body=body, hash_=str(i)) for i in range(4)]
    segments = assemble_pending(results, cap_bytes=200_000)
    assert len(segments) >= 2
