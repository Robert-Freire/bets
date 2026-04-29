"""Claude subprocess wrapper for research scanner."""

import logging
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOG = ROOT / "logs" / "research.log"

# Construct PATH at import time using $HOME so no hard-coded user directory.
_home = os.environ.get("HOME", "")
_local_bin = os.path.join(_home, ".local", "bin")

CLAUDE_CMD: list[str] = [
    "env", "-i",
    f"HOME={_home}",
    f"PATH=/usr/bin:/bin:{_local_bin}",
    "claude", "-p",
    "--model", "claude-opus-4-7",
    "--output-format", "text",
]

PROMPT_TEMPLATE: str = (
    "You are evaluating external content for a UK value-betting system. Our existing\n"
    "approach is summarised below. For each source in the input file, classify any\n"
    "findings as STRATEGY / EVIDENCE / RISK and score adopt-ability 1–5.\n"
    "\n"
    "Our system already does:\n"
    "- Shin de-vigging across ~36 books, Kaunitz consensus (≥3% UK-book deviation)\n"
    "- Cross-book stdev filter (≤4%), per-book outlier z-score (≤2.5)\n"
    "- Half-Kelly sizing, £5 rounding, 5% per-fixture, 15% per-portfolio, drawdown brake\n"
    "- Commission-aware net edges (Phase 5.7)\n"
    "- 8 paper-strategy variants A_production…H_no_pinnacle, shadow A/B\n"
    "- CLV vs Pinnacle as primary edge gauge\n"
    "\n"
    "For each source, output a section:\n"
    "\n"
    "### <source URL>\n"
    "- **STRATEGY** — <one-line description>. Adopt-ability: <1–5>. <one-line how>.\n"
    "- **EVIDENCE** — <claim>. Affects: <which strategy/filter>.\n"
    "- **RISK** — <what it suggests is broken in our flow>.\n"
    "\n"
    "If a source contains only generic \"ML for football\" content already covered by\n"
    "Dixon-Coles / Kaunitz / Yeung, write \"(no actionable findings)\" and move on.\n"
    "Be terse. Skip filler."
)

_logger = logging.getLogger(__name__)


def _ensure_log_handler() -> None:
    if not _logger.handlers:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(LOG, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        _logger.addHandler(handler)
        _logger.setLevel(logging.INFO)


class ClaudeCallError(RuntimeError):
    pass


def call_claude(pending_md: str, timeout: int = 300, mode: str = "single") -> str:
    _ensure_log_handler()
    full_prompt = PROMPT_TEMPLATE + "\n\n" + pending_md
    chars_in = len(full_prompt)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            CLAUDE_CMD,
            input=full_prompt,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=True,
        )
        exit_code = proc.returncode
        wall = round(time.monotonic() - t0, 2)
        _logger.info("%s | chars_in=%d | wall_time_s=%s | exit_code=%d", mode, chars_in, wall, exit_code)
        return proc.stdout
    except subprocess.CalledProcessError as exc:
        wall = round(time.monotonic() - t0, 2)
        _logger.info("%s | chars_in=%d | wall_time_s=%s | exit_code=%d", mode, chars_in, wall, exc.returncode)
        raise ClaudeCallError(exc.stderr) from exc


def call_claude_batched(segments: list[str], timeout: int = 300) -> str:
    outputs = [call_claude(seg, timeout=timeout, mode="batched") for seg in segments]
    return "\n\n---\n\n".join(outputs)
