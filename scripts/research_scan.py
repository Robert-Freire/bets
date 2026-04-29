"""Research scanner — top-level CLI entry point.

Usage:
  python3 scripts/research_scan.py --mode bootstrap   # Tier A, force re-fetch
  python3 scripts/research_scan.py --mode curated     # Tier A change-watch + Tier B
  python3 scripts/research_scan.py --mode open        # open-search queries (stub)
  python3 scripts/research_scan.py --mode all         # curated + open

Kill switch: RESEARCH_SCAN_ENABLE must be set to "1" (or any non-zero string).
"""

import argparse
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "logs" / "research.log"
SOURCES_PATH = ROOT / "docs" / "research_sources.md"

NTFY_URL = "https://ntfy.sh/robert-epl-bets-m4x9k"

logging.basicConfig(
    filename=str(LOG),
    level=logging.INFO,
    format="%(asctime)s %(message)s",
)
_log = logging.getLogger("research_scan")


def _ntfy(title: str, message: str, priority: str = "low") -> None:
    try:
        req = urllib.request.Request(
            NTFY_URL,
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": priority},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except urllib.error.URLError as exc:
        _log.warning("ntfy failed: %s", exc)


def _parse_sources(path: Path) -> tuple[list[str], list[str]]:
    """Return (tier_a_urls, tier_b_urls) parsed from research_sources.md."""
    tier_a: list[str] = []
    tier_b: list[str] = []
    current: list[str] | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## Tier A"):
            current = tier_a
        elif line.startswith("## Tier B"):
            current = tier_b
        elif line.startswith("- http") and current is not None:
            current.append(line[2:].strip())
    return tier_a, tier_b


def _run(
    urls: list[str],
    mode: str,
    seen: dict,
    force: bool,
    dry_run: bool,
    max_sources: int | None,
) -> tuple[list, int]:
    """Fetch URLs, filter by change, return (changed_results, total_bytes)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from research_lib.fetch import fetch
    from research_lib.state import is_changed

    if max_sources is not None:
        urls = urls[:max_sources]

    results = []
    total_bytes = 0
    for url in urls:
        r = fetch(url)
        total_bytes += len(r.body_text.encode("utf-8"))
        if force or is_changed(url, r.body_hash, seen):
            results.append(r)
            _log.info("changed url=%s status=%s bytes=%d", url, r.status, len(r.body_text))
        else:
            _log.info("unchanged url=%s", url)
        if dry_run:
            changed_marker = "[CHANGED]" if (force or is_changed(url, r.body_hash, seen)) else "[skip]"
            print(f"  {changed_marker} {url}  ({len(r.body_text)} bytes)")

    return results, total_bytes


def main() -> None:
    parser = argparse.ArgumentParser(description="Research scanner")
    parser.add_argument("--mode", choices=["bootstrap", "curated", "open", "all"], required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-sources", type=int, default=None)
    args = parser.parse_args()

    enable = os.environ.get("RESEARCH_SCAN_ENABLE", "0")
    if enable not in ("1", "true", "yes"):
        _log.info("RESEARCH_SCAN_ENABLE=%r — exiting without scan", enable)
        print("RESEARCH_SCAN_ENABLE not set to 1 — skipping scan.")
        sys.exit(0)

    sys.path.insert(0, str(ROOT / "scripts"))
    from research_lib.claude_call import call_claude_batched
    from research_lib.feed import write_findings
    from research_lib.state import assemble_pending, load_seen, save_seen, update_seen

    tier_a, tier_b = _parse_sources(SOURCES_PATH)

    if args.mode == "bootstrap":
        urls = tier_a
        force = True
    elif args.mode == "curated":
        urls = tier_a + tier_b
        force = False
    elif args.mode == "open":
        print("open-search backend not yet implemented (Phase 11.7) — nothing to do.")
        _log.info("mode=open stub — no-op")
        sys.exit(0)
    else:  # all
        urls = tier_a + tier_b
        force = False

    seen = load_seen()

    if args.dry_run:
        print(f"Dry-run mode={args.mode} force={force} sources={len(urls)}")
        _, total_bytes = _run(urls, args.mode, seen, force=force, dry_run=True, max_sources=args.max_sources)
        print(f"\nTotal estimated bytes (after 20 KB/source cap): {total_bytes:,}")
        return

    try:
        run_at = datetime.now(tz=timezone.utc)
        results, _ = _run(urls, args.mode, seen, force=force, dry_run=False, max_sources=args.max_sources)

        ok_results = [r for r in results if r.status == "ok"]
        for r in results:
            update_seen(seen, r)
        save_seen(seen)

        if not ok_results:
            _log.info("mode=%s — no changed sources, nothing to send to Claude", args.mode)
            print("No changed sources — feed unchanged.")
            _ntfy("Research scan", "0 new findings", priority="low")
            return

        segments = assemble_pending(ok_results)
        claude_output = call_claude_batched(segments)
        count = write_findings(claude_output, args.mode, run_at)

        _log.info("mode=%s — %d findings written", args.mode, count)
        print(f"Done: {count} findings written to docs/RESEARCH_FEED.md")
        _ntfy("Research scan", f"{count} new findings ({args.mode})", priority="low")

    except Exception as exc:
        _log.exception("research scan failed: %s", exc)
        _ntfy("Research scan FAILED", str(exc), priority="high")
        raise


if __name__ == "__main__":
    main()
