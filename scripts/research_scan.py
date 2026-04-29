"""Research scanner — top-level CLI entry point.

Usage:
  python3 scripts/research_scan.py --mode bootstrap   # Tier A, force re-fetch
  python3 scripts/research_scan.py --mode curated     # Tier A change-watch + Tier B
  python3 scripts/research_scan.py --mode open        # open-search queries
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
QUERIES_PATH = ROOT / "docs" / "research_queries.md"

# Add scripts/ to path once at module level so research_lib imports work everywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent))

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


def _load_queries(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _run_curated(
    urls: list[str],
    seen: dict,
    force: bool,
    dry_run: bool,
) -> tuple[list, int]:
    """Fetch all URLs; return (all_results, total_bytes)."""
    from research_lib.fetch import fetch
    from research_lib.state import is_changed

    all_results = []
    total_bytes = 0
    for url in urls:
        r = fetch(url)
        total_bytes += len(r.body_text.encode("utf-8"))
        changed = force or is_changed(url, r.body_hash, seen)
        all_results.append(r)
        _log.info(
            "%s url=%s status=%s bytes=%d",
            "changed" if changed else "unchanged",
            url, r.status, len(r.body_text),
        )
        if dry_run:
            print(f"  {'[CHANGED]' if changed else '[skip]'} {url}  ({len(r.body_text)} bytes)")

    return all_results, total_bytes


def _run_open_search(
    seen: dict,
    dry_run: bool,
    max_sources: int | None,
) -> tuple[list, dict[str, str]]:
    """Run all open-search queries. Returns (ok_results, url_to_backend_tag)."""
    from research_lib.fetch import fetch
    from research_lib.search import BACKENDS, search

    queries = _load_queries(QUERIES_PATH)
    url_backend: dict[str, str] = {}  # url -> first backend that found it

    for query in queries:
        found_total = 0
        query_new: dict[str, str] = {}
        for backend in BACKENDS:
            urls = search(query, backend)
            found_total += len(urls)
            for url in urls:
                if url not in url_backend and url not in seen and url not in query_new:
                    query_new[url] = backend

        capped = dict(list(query_new.items())[:5])
        url_backend.update(capped)

        fetch_count = len(capped)
        dedup_rate = (found_total - fetch_count) / max(found_total, 1) * 100
        _log.info(
            "open-search query=%r backends_found=%d new_urls=%d dedup_rate=%.0f%%",
            query, found_total, fetch_count, dedup_rate,
        )

    if max_sources is not None:
        url_backend = dict(list(url_backend.items())[:max_sources])

    if dry_run:
        print(
            f"Open-search: {len(url_backend)} new URLs across"
            f" {len(queries)} queries × {len(BACKENDS)} backends"
        )
        for url, backend in url_backend.items():
            print(f"  [backend:{backend}] {url}")
        return [], {}

    results = []
    for url, backend in url_backend.items():
        r = fetch(url)
        _log.info(
            "open-fetch url=%s backend=%s status=%s bytes=%d",
            url, backend, r.status, len(r.body_text),
        )
        results.append(r)

    ok = [r for r in results if r.status == "ok"]
    return ok, url_backend


def main() -> None:
    parser = argparse.ArgumentParser(description="Research scanner")
    parser.add_argument("--mode", choices=["bootstrap", "curated", "open", "all"], required=True)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch URLs and report byte counts, but skip Claude and feed write.",
    )
    parser.add_argument("--max-sources", type=int, default=None)
    args = parser.parse_args()

    enable = os.environ.get("RESEARCH_SCAN_ENABLE", "0")
    if enable not in ("1", "true", "yes"):
        _log.info("RESEARCH_SCAN_ENABLE=%r — exiting without scan", enable)
        print("RESEARCH_SCAN_ENABLE not set to 1 — skipping scan.")
        sys.exit(0)

    from research_lib.claude_call import call_claude_batched
    from research_lib.feed import write_findings
    from research_lib.state import assemble_pending, is_changed, load_seen, save_seen, update_seen

    tier_a, tier_b = _parse_sources(SOURCES_PATH)
    seen = load_seen()

    # ── dry-run path ──────────────────────────────────────────────────────────
    if args.dry_run:
        if args.mode in ("bootstrap", "curated", "all"):
            urls = tier_a if args.mode == "bootstrap" else tier_a + tier_b
            if args.max_sources is not None:
                urls = urls[:args.max_sources]
            force = args.mode == "bootstrap"
            print(f"Dry-run mode={args.mode} force={force} sources={len(urls)}")
            _, total_bytes = _run_curated(urls, seen, force=force, dry_run=True)
            print(f"\nTotal estimated bytes (after 20 KB/source cap): {total_bytes:,}")
        if args.mode in ("open", "all"):
            _run_open_search(seen, dry_run=True, max_sources=args.max_sources)
        return

    # ── live run ──────────────────────────────────────────────────────────────
    try:
        run_at = datetime.now(tz=timezone.utc)
        total_count = 0

        if args.mode in ("bootstrap", "curated", "all"):
            urls = tier_a if args.mode == "bootstrap" else tier_a + tier_b
            if args.max_sources is not None:
                urls = urls[:args.max_sources]
            force = args.mode == "bootstrap"

            all_results, _ = _run_curated(urls, seen, force=force, dry_run=False)
            changed = [r for r in all_results if force or is_changed(r.url, r.body_hash, seen)]
            ok_changed = [r for r in changed if r.status == "ok"]
            for r in all_results:
                update_seen(seen, r)

            if ok_changed:
                segments = assemble_pending(ok_changed)
                claude_out = call_claude_batched(segments)
                count = write_findings(claude_out, args.mode, run_at)
                total_count += count
                _log.info("mode=%s curated — %d findings written", args.mode, count)
            else:
                _log.info("mode=%s — no curated changes", args.mode)
                print("No changed sources.")

        if args.mode in ("open", "all"):
            ok_results, tags = _run_open_search(seen, dry_run=False, max_sources=args.max_sources)
            for r in ok_results:
                update_seen(seen, r)
            if ok_results:
                segments = assemble_pending(ok_results, tags=tags)
                claude_out = call_claude_batched(segments)
                open_label = "open" if args.mode == "open" else "all:open"
                count = write_findings(claude_out, open_label, run_at)
                total_count += count
                _log.info("mode=%s open-search — %d findings written", args.mode, count)
            else:
                _log.info("mode=%s — no open-search results", args.mode)

        save_seen(seen)

        print(f"Done: {total_count} findings written to docs/RESEARCH_FEED.md")
        _ntfy("Research scan", f"{total_count} new findings ({args.mode})", priority="low")

    except Exception as exc:
        _log.exception("research scan failed: %s", exc)
        _ntfy("Research scan FAILED", str(exc), priority="high")
        raise


if __name__ == "__main__":
    main()
