"""Raw API response archive (Phase A.5.5).

Every external API call (Odds API today; Pinnacle/Betfair/etc. later) writes
its raw, unparsed response to Azure Blob Storage *before* parsing. New
data-quality rules can then be retro-tested against real history instead of
waiting on fresh data.

Activation rule (BOTH must hold for blob writes):
  - BLOB_ARCHIVE=1
  - AZURE_BLOB_CONN  set to a literal blob storage connection string
                     (or AZURE_BLOB_KV_VAULT + AZURE_BLOB_KV_SECRET, in which
                      case the connection string is fetched from Key Vault
                      once per process via `az keyvault secret show`).

Optional:
  - AZURE_BLOB_CONTAINER  (default "raw-api-snapshots")

Pi safety contract (mirrors A.4 BetRepo):
  - Module imports nothing beyond stdlib at top level. `azure.storage.blob`
    and the Azure CLI are touched only inside the activated branch.
    After `git pull` on the Pi (no env flags), the archive path stays
    dormant; behavior is byte-identical to pre-A.5.5.

Failure isolation:
  - Any blob-write error is caught; the response is buffered into
    `logs/snapshots/<same-key>` (gzipped). Buffered files are drained on the
    next successful archive call. The scanner never aborts because of an
    archive failure.

API key redaction:
  - The `params` dict is deep-copied and any key matching /apikey/i is
    replaced with the literal string "<redacted>" before it lands in the
    blob body. Reviewer focus item — there is a unit test asserting the
    real key never appears in a sample blob.
"""
from __future__ import annotations

import gzip
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Headers worth keeping for retro analysis. We intentionally drop everything
# else (cookies, Set-Cookie, server names) so we don't archive sensitive or
# noisy metadata.
_HEADER_ALLOWLIST = {
    "x-requests-remaining",
    "x-requests-used",
    "date",
    "content-type",
}

_LOCAL_BUFFER_DIR = Path(__file__).resolve().parent.parent.parent / "logs" / "snapshots"


def _kv_fetch(vault: str, secret: str) -> str | None:
    """Run `az keyvault secret show` once and return the secret value."""
    try:
        out = subprocess.run(
            ["az", "keyvault", "secret", "show",
             "--vault-name", vault, "--name", secret,
             "--query", "value", "-o", "tsv"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        print(f"[snapshots] WARN: Key Vault fetch failed ({vault}/{secret}): {e}",
              file=sys.stderr)
        return None


def _resolve_conn_string() -> str | None:
    """Return a blob connection string, or None if archive is disabled."""
    if os.environ.get("BLOB_ARCHIVE", "").strip() != "1":
        return None
    direct = os.environ.get("AZURE_BLOB_CONN", "").strip()
    if direct:
        return direct
    vault = os.environ.get("AZURE_BLOB_KV_VAULT", "").strip()
    secret = os.environ.get("AZURE_BLOB_KV_SECRET", "").strip()
    if vault and secret:
        return _kv_fetch(vault, secret)
    print("[snapshots] WARN: BLOB_ARCHIVE=1 but no connection inputs; archive disabled. "
          "Set AZURE_BLOB_CONN or AZURE_BLOB_KV_VAULT+AZURE_BLOB_KV_SECRET.",
          file=sys.stderr)
    return None


def _sanitise_endpoint(endpoint: str) -> str:
    """Convert '/v4/sports/soccer_epl/odds/' → 'v4_sports_soccer_epl_odds'."""
    return endpoint.strip("/").replace("/", "_") or "root"


def _redact(params: dict | None) -> dict:
    """Return a shallow copy of params with any apiKey-like field redacted."""
    if not params:
        return {}
    out = {}
    for k, v in params.items():
        if k.lower() in {"apikey", "api_key"}:
            out[k] = "<redacted>"
        else:
            out[k] = v
    return out


def _filter_headers(headers: dict | None) -> dict:
    if not headers:
        return {}
    return {k: v for k, v in headers.items() if k.lower() in _HEADER_ALLOWLIST}


def _build_blob_key(*, source: str, endpoint: str, captured_at: datetime,
                    sport_key: str) -> str:
    iso = captured_at.strftime("%Y-%m-%dT%H-%M-%S-%f")  # ms-resolution; colon-free
    sport = sport_key or "unknown"
    yyyy = captured_at.strftime("%Y")
    mm = captured_at.strftime("%m")
    dd = captured_at.strftime("%d")
    return f"{source}/{_sanitise_endpoint(endpoint)}/{yyyy}/{mm}/{dd}/{iso}_{sport}.json.gz"


def _gzip_payload(payload: dict) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(raw)
    return buf.getvalue()


class SnapshotArchive:
    """Captures raw external API responses to Azure Blob Storage.

    Construction is cheap; nothing happens until `archive()` is called and
    the env flags activate the path. A single `BlobServiceClient` is reused
    for the lifetime of the instance.
    """

    def __init__(self) -> None:
        self._conn_string: str | None = None
        self._service = None  # type: ignore[var-annotated]
        self._container_name: str = os.environ.get("AZURE_BLOB_CONTAINER", "raw-api-snapshots")
        self._initialised = False
        self._enabled = False
        self._buffer_drained = False

    def _init(self) -> None:
        if self._initialised:
            return
        self._initialised = True
        self._conn_string = _resolve_conn_string()
        if not self._conn_string:
            self._enabled = False
            return
        try:
            from azure.storage.blob import BlobServiceClient  # lazy: pi-safe
            self._service = BlobServiceClient.from_connection_string(self._conn_string)
            self._enabled = True
        except Exception as e:
            print(f"[snapshots] WARN: BlobServiceClient init failed: {e}", file=sys.stderr)
            self._enabled = False

    @property
    def enabled(self) -> bool:
        self._init()
        return self._enabled

    def archive(self, *, source: str, endpoint: str, params: dict | None,
                status: int, headers: dict | None, body: bytes,
                sport_key: str = "") -> None:
        """Persist one HTTP response. Never raises; degrades to local buffer."""
        self._init()
        if not (self._enabled or os.environ.get("BLOB_ARCHIVE", "").strip() == "1"):
            return  # archive disabled — fast path, no buffering either

        captured_at = datetime.now(timezone.utc)
        key = _build_blob_key(
            source=source, endpoint=endpoint,
            captured_at=captured_at, sport_key=sport_key,
        )
        payload = {
            "captured_at": captured_at.isoformat(),
            "source": source,
            "endpoint": endpoint,
            "params": _redact(params),
            "status": status,
            "headers": _filter_headers(headers),
            "body_raw": body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) else body,
        }
        gz_bytes = _gzip_payload(payload)

        if self._enabled and self._upload(key, gz_bytes):
            # Successful upload — opportunistically drain the local buffer
            # exactly once per process so retried scans don't re-upload.
            if not self._buffer_drained:
                self._drain_buffer()
                self._buffer_drained = True
            return

        # Either disabled-but-flag-set (means client init failed) or upload
        # threw: buffer locally so the next run can retry.
        self._buffer_locally(key, gz_bytes)

    def _upload(self, key: str, gz_bytes: bytes) -> bool:
        try:
            client = self._service.get_blob_client(  # type: ignore[union-attr]
                container=self._container_name, blob=key,
            )
            # overwrite=False ensures we never silently clobber an existing
            # blob if a key collision ever happens (the ms-resolution timestamp
            # makes this very unlikely, but loud-fail is the right call).
            client.upload_blob(gz_bytes, overwrite=False, content_type="application/gzip")
            return True
        except Exception as e:
            print(f"[snapshots] WARN: blob upload failed for {key}: {e}", file=sys.stderr)
            return False

    def _buffer_locally(self, key: str, gz_bytes: bytes) -> None:
        path = _LOCAL_BUFFER_DIR / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gz_bytes)

    # --- reading helpers (B.0.5 analysis) ------------------------------------

    def list_blob_keys(self, prefix: str = "") -> list[str]:
        """List blob keys under *prefix*. Returns [] if archive disabled or on error."""
        self._init()
        if not self._enabled:
            return []
        try:
            container_client = self._service.get_container_client(  # type: ignore[union-attr]
                self._container_name
            )
            return [b.name for b in container_client.list_blobs(name_starts_with=prefix)]
        except Exception as e:
            print(f"[snapshots] WARN: list_blob_keys prefix={prefix!r}: {e}",
                  file=sys.stderr)
            return []

    def download_blob(self, key: str) -> bytes | None:
        """Download raw (still gzipped) bytes for *key*. Returns None on error."""
        self._init()
        if not self._enabled:
            return None
        try:
            blob_client = self._service.get_blob_client(  # type: ignore[union-attr]
                container=self._container_name, blob=key
            )
            return blob_client.download_blob().readall()
        except Exception as e:
            print(f"[snapshots] WARN: download_blob key={key!r}: {e}", file=sys.stderr)
            return None

    def _drain_buffer(self) -> None:
        """Upload every file under logs/snapshots/. Delete on success."""
        if not _LOCAL_BUFFER_DIR.exists():
            return
        for path in sorted(_LOCAL_BUFFER_DIR.rglob("*.json.gz")):
            rel_key = path.relative_to(_LOCAL_BUFFER_DIR).as_posix()
            try:
                gz_bytes = path.read_bytes()
            except OSError as e:
                print(f"[snapshots] WARN: could not read buffered {path}: {e}", file=sys.stderr)
                continue
            if self._upload(rel_key, gz_bytes):
                try:
                    path.unlink()
                except OSError as e:
                    print(f"[snapshots] WARN: drained {rel_key} but could not delete buffer file: {e}",
                          file=sys.stderr)
            # On failure leave it for the next run.


def load_snapshot_envelope(gz_bytes: bytes) -> dict | None:
    """Parse a gzipped SnapshotArchive envelope. Returns None on failure."""
    try:
        return json.loads(gzip.decompress(gz_bytes))
    except Exception as e:
        print(f"[snapshots] WARN: failed to parse snapshot envelope: {e}",
              file=sys.stderr)
        return None


def extract_events(envelope: dict) -> list:
    """Return the list of API events from a parsed envelope, or []."""
    body_raw = envelope.get("body_raw")
    if isinstance(body_raw, str):
        try:
            result = json.loads(body_raw)
            return result if isinstance(result, list) else []
        except Exception as e:
            print(f"[snapshots] WARN: extract_events failed to parse body_raw: {e}",
                  file=sys.stderr)
            return []
    if not isinstance(body_raw, list):
        print(f"[snapshots] WARN: extract_events: unexpected body_raw type "
              f"{type(body_raw).__name__}", file=sys.stderr)
        return []
    return body_raw


# Module-level singleton: scan_odds.py imports and uses one instance.
_DEFAULT: SnapshotArchive | None = None


def get_archive() -> SnapshotArchive:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = SnapshotArchive()
    return _DEFAULT


def reset_archive_for_tests() -> None:
    """Test-only: clears the module singleton so env-var changes take effect."""
    global _DEFAULT
    _DEFAULT = None
