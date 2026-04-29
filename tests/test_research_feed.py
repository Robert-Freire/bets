"""Tests for research_lib.feed — offline, no network."""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from research_lib.feed import _BANNER, _count_findings, write_findings

RUN_AT = datetime(2026, 4, 29, 10, 0, 0, tzinfo=timezone.utc)
MODE = "bootstrap"

SAMPLE_OUTPUT = """\
### https://example.com/a
- **STRATEGY** — use Kelly sizing. Adopt-ability: 4. Apply half-Kelly.
- **EVIDENCE** — positive CLV correlation. Affects: CLV filter.
- **RISK** — account restrictions. Affects: book selection.

### https://example.com/b
(no actionable findings)
"""


class TestCountFindings:
    def test_counts_all_types(self):
        assert _count_findings(SAMPLE_OUTPUT) == 3

    def test_empty_output(self):
        assert _count_findings("") == 0

    def test_no_findings_line(self):
        assert _count_findings("(no actionable findings)") == 0

    def test_does_not_match_partial(self):
        # lower-case should not match
        assert _count_findings("- **(strategy)** something") == 0

    def test_inline_not_counted(self):
        # only line-starts count (re.MULTILINE + ^)
        assert _count_findings("text - **(STRATEGY)** here") == 0


class TestWriteFindings:
    def test_creates_file_with_banner_when_missing(self, tmp_path):
        feed = tmp_path / "RESEARCH_FEED.md"
        count = write_findings(SAMPLE_OUTPUT, MODE, RUN_AT, feed_path=feed)
        text = feed.read_text()
        assert "# Research Feed" in text
        assert "Auto-generated" in text
        assert "## Run 2026-04-29 10:00 UTC (mode: bootstrap)" in text
        assert count == 3

    def test_banner_appears_once_after_two_writes(self, tmp_path):
        feed = tmp_path / "RESEARCH_FEED.md"
        write_findings(SAMPLE_OUTPUT, MODE, RUN_AT, feed_path=feed)
        run2 = datetime(2026, 5, 6, 10, 0, 0, tzinfo=timezone.utc)
        write_findings("- **STRATEGY** — new one. Adopt-ability: 3. How.", "curated", run2, feed_path=feed)
        text = feed.read_text()
        assert text.count("# Research Feed") == 1
        assert text.count("Auto-generated") == 1

    def test_newest_run_appears_before_older_run(self, tmp_path):
        feed = tmp_path / "RESEARCH_FEED.md"
        write_findings(SAMPLE_OUTPUT, MODE, RUN_AT, feed_path=feed)
        run2 = datetime(2026, 5, 6, 10, 0, 0, tzinfo=timezone.utc)
        write_findings("- **EVIDENCE** — something new.", "curated", run2, feed_path=feed)
        text = feed.read_text()
        pos_run1 = text.index("2026-04-29")
        pos_run2 = text.index("2026-05-06")
        assert pos_run2 < pos_run1, "newer run should appear first"

    def test_returned_count_matches_strategy_evidence_risk(self, tmp_path):
        feed = tmp_path / "RESEARCH_FEED.md"
        output = (
            "- **STRATEGY** — a. Adopt-ability: 5. b.\n"
            "- **EVIDENCE** — c. Affects: d.\n"
            "- **RISK** — e. Affects: f.\n"
        )
        count = write_findings(output, MODE, RUN_AT, feed_path=feed)
        assert count == 3

    def test_zero_findings_recorded(self, tmp_path):
        feed = tmp_path / "RESEARCH_FEED.md"
        count = write_findings("(no actionable findings)", MODE, RUN_AT, feed_path=feed)
        assert count == 0
        assert "— 0 findings" in feed.read_text()

    def test_atomic_write_uses_tmp_then_replace(self, tmp_path, monkeypatch):
        feed = tmp_path / "RESEARCH_FEED.md"
        replaced = []
        real_replace = os.replace

        def fake_replace(src, dst):
            replaced.append((src, dst))
            real_replace(src, dst)

        monkeypatch.setattr(os, "replace", fake_replace)
        write_findings(SAMPLE_OUTPUT, MODE, RUN_AT, feed_path=feed)
        assert len(replaced) == 1
        src, dst = replaced[0]
        assert str(dst) == str(feed)
        assert str(src).endswith(".tmp")

    def test_existing_file_with_prior_runs(self, tmp_path):
        feed = tmp_path / "RESEARCH_FEED.md"
        write_findings("- **STRATEGY** — old.", "curated", datetime(2026, 4, 1, 10, 0), feed_path=feed)
        write_findings("- **EVIDENCE** — newer.", MODE, RUN_AT, feed_path=feed)
        text = feed.read_text()
        # Both sections present
        assert "2026-04-01" in text
        assert "2026-04-29" in text

    def test_grep_strategy_lines_are_clean(self, tmp_path):
        feed = tmp_path / "RESEARCH_FEED.md"
        write_findings(SAMPLE_OUTPUT, MODE, RUN_AT, feed_path=feed)
        lines = [l for l in feed.read_text().splitlines() if "**STRATEGY**" in l]
        assert len(lines) == 1
        assert lines[0].startswith("- **STRATEGY**")
