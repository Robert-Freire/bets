"""Dedup state and pending-file builder for research scanner."""

import json
import os
from pathlib import Path

from research_lib.fetch import FetchResult

ROOT = Path(__file__).resolve().parent.parent.parent
SEEN_PATH = ROOT / "logs" / "research_seen.json"


def load_seen(path: Path = SEEN_PATH) -> dict[str, dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def save_seen(d: dict, path: Path = SEEN_PATH) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def is_changed(url: str, new_hash: str, seen: dict) -> bool:
    entry = seen.get(url)
    return entry is None or entry.get("hash") != new_hash


def update_seen(seen: dict, result: FetchResult) -> None:
    prev = seen.get(result.url)
    if prev is None or prev.get("hash") != result.body_hash:
        last_changed = result.fetched_at
    else:
        last_changed = prev.get("last_changed_at", result.fetched_at)
    seen[result.url] = {
        "hash": result.body_hash,
        "fetched_at": result.fetched_at,
        "status": result.status,
        "last_changed_at": last_changed,
    }


def assemble_pending(results: list[FetchResult], cap_bytes: int = 200_000) -> list[str]:
    segments: list[str] = []
    current_parts: list[str] = []
    current_size = 0

    for r in results:
        if not r.body_text:
            continue
        block = (
            f"## Source: {r.url}\n"
            f"{r.fetched_at} — status: {r.status}\n\n"
            f"{r.body_text}\n\n"
            f"---\n\n"
        )
        block_size = len(block.encode("utf-8"))

        if block_size > cap_bytes:
            if current_parts:
                segments.append("".join(current_parts))
                current_parts = []
                current_size = 0
            segments.append(f"# WARNING: oversized\n\n{block}")
        elif current_size + block_size > cap_bytes:
            segments.append("".join(current_parts))
            current_parts = [block]
            current_size = block_size
        else:
            current_parts.append(block)
            current_size += block_size

    if current_parts:
        segments.append("".join(current_parts))

    return segments
