"""Offline tests for scripts/research_lib/claude_call.py — uses a fake claude shim."""

import os
import stat
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import research_lib.claude_call as cc
from research_lib.claude_call import ClaudeCallError, call_claude, call_claude_batched


# ── shim factory ──────────────────────────────────────────────────────────────

def _make_shim(tmp_path: Path, body: str, exit_code: int = 0) -> list[str]:
    """Write a tiny Python shim that reads stdin and exits with exit_code."""
    shim = tmp_path / "claude"
    shim.write_text(
        textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        _ = sys.stdin.read()  # exercise stdin piping
        sys.stdout.write({body!r})
        sys.exit({exit_code})
        """),
        encoding="utf-8",
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return [str(shim)]


# ── call_claude ───────────────────────────────────────────────────────────────

def test_call_claude_returns_shim_output(tmp_path):
    cmd = _make_shim(tmp_path, "FINDINGS_RESPONSE")
    with patch.object(cc, "CLAUDE_CMD", cmd):
        result = call_claude("## Source: https://example.com\nsome text")
    assert result == "FINDINGS_RESPONSE"


def test_call_claude_pipes_via_stdin(tmp_path):
    """Shim reads stdin and echoes char count; verifies stdin is wired."""
    shim = tmp_path / "claude"
    shim.write_text(
        textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        data = sys.stdin.read()
        sys.stdout.write(f"chars:{len(data)}")
        """),
        encoding="utf-8",
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    with patch.object(cc, "CLAUDE_CMD", [str(shim)]):
        result = call_claude("hello")
    # full_prompt = PROMPT_TEMPLATE + "\n\n" + "hello"
    expected_chars = len(cc.PROMPT_TEMPLATE + "\n\n" + "hello")
    assert result == f"chars:{expected_chars}"


def test_call_claude_nonzero_exit_raises(tmp_path):
    cmd = _make_shim(tmp_path, "error detail", exit_code=1)
    with patch.object(cc, "CLAUDE_CMD", cmd):
        with pytest.raises(ClaudeCallError):
            call_claude("some input")


def test_call_claude_logs_on_success(tmp_path):
    cmd = _make_shim(tmp_path, "OK")
    log_path = tmp_path / "research.log"
    with patch.object(cc, "CLAUDE_CMD", cmd), patch.object(cc, "LOG", log_path):
        # Reset handlers so _ensure_log_handler picks up the patched LOG path
        cc._logger.handlers.clear()
        call_claude("test input", mode="test")
        cc._logger.handlers.clear()
    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    assert "test" in lines[0]
    assert "chars_in=" in lines[0]
    assert "wall_time_s=" in lines[0]
    assert "exit_code=0" in lines[0]


def test_call_claude_logs_on_failure(tmp_path):
    cmd = _make_shim(tmp_path, "err", exit_code=1)
    log_path = tmp_path / "research.log"
    with patch.object(cc, "CLAUDE_CMD", cmd), patch.object(cc, "LOG", log_path):
        cc._logger.handlers.clear()
        with pytest.raises(ClaudeCallError):
            call_claude("test input", mode="test")
        cc._logger.handlers.clear()
    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    assert "exit_code=1" in lines[0]


# ── call_claude_batched ───────────────────────────────────────────────────────

def test_call_claude_batched_three_segments(tmp_path):
    """3 segments → 3 shim invocations → joined with separator."""
    call_count = {"n": 0}

    def _fake_call_claude(pending_md, timeout=300, mode="single"):
        call_count["n"] += 1
        return f"RESP{call_count['n']}"

    segments = ["seg1", "seg2", "seg3"]
    with patch.object(cc, "call_claude", _fake_call_claude):
        result = call_claude_batched(segments)

    assert call_count["n"] == 3
    assert result == "RESP1\n\n---\n\nRESP2\n\n---\n\nRESP3"


def test_call_claude_batched_single_segment(tmp_path):
    cmd = _make_shim(tmp_path, "SINGLE")
    with patch.object(cc, "CLAUDE_CMD", cmd):
        result = call_claude_batched(["one segment"])
    assert result == "SINGLE"


def test_call_claude_batched_propagates_error(tmp_path):
    cmd = _make_shim(tmp_path, "boom", exit_code=1)
    with patch.object(cc, "CLAUDE_CMD", cmd):
        with pytest.raises(ClaudeCallError):
            call_claude_batched(["seg1", "seg2"])
