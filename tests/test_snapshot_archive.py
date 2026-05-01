"""Tests for src/storage/snapshots.py SnapshotArchive (Phase A.5.5).

Coverage:
- Pi safety contract: no env flags → no `azure.storage.blob` import,
  archive call is a no-op, no local buffer file written.
- Activated path: env flag + fake conn string → one blob upload with the
  expected key shape, payload, redacted api_key, allowlisted headers.
- API key never appears in archived blob body (gzipped or not).
- Failure isolation: blob client raises → archive degrades to local buffer
  under logs/snapshots/, scan continues without the exception escaping.
- Buffer drain: pre-seeded logs/snapshots/ files upload on next successful
  archive, and are deleted only after confirmed upload.
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def fresh_env(monkeypatch):
    """Strip every BLOB_* / AZURE_BLOB_* env var; tests opt back in explicitly."""
    for k in list(os.environ):
        if k.startswith("BLOB_") or k.startswith("AZURE_BLOB_"):
            monkeypatch.delenv(k, raising=False)
    return monkeypatch


@pytest.fixture
def isolated_module(monkeypatch, tmp_path):
    """Reload snapshots with logs/snapshots/ pointed at tmp_path."""
    sys.modules.pop("src.storage.snapshots", None)
    sys.modules.pop("azure.storage.blob", None)

    import src.storage.snapshots as snap
    monkeypatch.setattr(snap, "_LOCAL_BUFFER_DIR", tmp_path / "snapshots")
    snap.reset_archive_for_tests()
    return snap


class _FakeBlobClient:
    def __init__(self, sink: list, fail: bool = False):
        self._sink = sink
        self._fail = fail

    def upload_blob(self, data, overwrite=False, content_type=None):
        if self._fail:
            raise RuntimeError("boom")
        self._sink.append({"data": bytes(data), "content_type": content_type, "overwrite": overwrite})


class _FakeBlobServiceClient:
    def __init__(self, sink: list, fail: bool = False):
        self._sink = sink
        self._fail = fail
        self.requested = []  # (container, blob)

    def get_blob_client(self, container, blob):
        self.requested.append((container, blob))
        return _FakeBlobClient(self._sink, fail=self._fail)


def _install_fake_azure(monkeypatch, sink: list, fail: bool = False) -> _FakeBlobServiceClient:
    """Inject a fake azure.storage.blob module so the lazy import resolves."""
    fake_service = _FakeBlobServiceClient(sink, fail=fail)

    fake_pkg = types.ModuleType("azure")
    fake_storage = types.ModuleType("azure.storage")
    fake_blob = types.ModuleType("azure.storage.blob")

    class _BSC:
        @classmethod
        def from_connection_string(cls, conn_string: str):
            return fake_service

    fake_blob.BlobServiceClient = _BSC
    fake_pkg.storage = fake_storage
    fake_storage.blob = fake_blob

    monkeypatch.setitem(sys.modules, "azure", fake_pkg)
    monkeypatch.setitem(sys.modules, "azure.storage", fake_storage)
    monkeypatch.setitem(sys.modules, "azure.storage.blob", fake_blob)
    return fake_service


# ---- Pi safety -------------------------------------------------------------

def test_pi_safety_no_env_means_no_azure_import(fresh_env, isolated_module, tmp_path):
    """Without env flags, archive() is a no-op and azure.storage.blob is never imported."""
    snap = isolated_module
    sys.modules.pop("azure.storage.blob", None)

    archive = snap.SnapshotArchive()
    archive.archive(
        source="odds_api", endpoint="/sports/", params={"all": "false", "apiKey": "SECRET"},
        status=200, headers={}, body=b"[]", sport_key="",
    )

    assert "azure.storage.blob" not in sys.modules, (
        "azure.storage.blob was imported even though BLOB_ARCHIVE was unset — "
        "Pi safety contract violated."
    )
    assert archive.enabled is False
    # No buffer file should have been written either — disabled means truly off.
    assert list((tmp_path / "snapshots").rglob("*.json.gz")) == []


# ---- Activated path --------------------------------------------------------

def test_activated_path_writes_one_blob_with_redaction(fresh_env, isolated_module,
                                                       monkeypatch):
    """BLOB_ARCHIVE=1 + AZURE_BLOB_CONN → one upload, api_key redacted."""
    snap = isolated_module
    sink: list = []
    fake = _install_fake_azure(monkeypatch, sink)

    fresh_env.setenv("BLOB_ARCHIVE", "1")
    fresh_env.setenv("AZURE_BLOB_CONN", "DefaultEndpointsProtocol=https;AccountName=fake;")

    archive = snap.SnapshotArchive()
    archive.archive(
        source="odds_api", endpoint="/sports/soccer_epl/odds/",
        params={"regions": "uk,eu", "apiKey": "REAL_KEY_aaa111"},
        status=200,
        headers={"x-requests-remaining": "499", "Set-Cookie": "should-be-dropped",
                 "content-type": "application/json"},
        body=b'[{"id":"abc"}]',
        sport_key="soccer_epl",
    )

    assert archive.enabled is True
    assert len(sink) == 1
    assert len(fake.requested) == 1
    container, blob_key = fake.requested[0]
    assert container == "raw-api-snapshots"
    # Key shape: odds_api/sports_soccer_epl_odds/YYYY/MM/DD/<iso>_soccer_epl.json.gz
    assert blob_key.startswith("odds_api/sports_soccer_epl_odds/")
    assert blob_key.endswith("_soccer_epl.json.gz")
    parts = blob_key.split("/")
    assert len(parts) == 6  # source, endpoint, yyyy, mm, dd, file

    # Blob payload — gzipped JSON; decompress and check.
    decoded = json.loads(gzip.decompress(sink[0]["data"]).decode("utf-8"))
    assert decoded["source"] == "odds_api"
    assert decoded["endpoint"] == "/sports/soccer_epl/odds/"
    assert decoded["status"] == 200
    assert decoded["params"] == {"regions": "uk,eu", "apiKey": "<redacted>"}
    assert decoded["headers"] == {"x-requests-remaining": "499",
                                  "content-type": "application/json"}
    assert decoded["body_raw"] == '[{"id":"abc"}]'
    assert sink[0]["content_type"] == "application/gzip"
    assert sink[0]["overwrite"] is False  # collision = loud-fail, not silent clobber


def test_api_key_never_in_archived_blob(fresh_env, isolated_module, monkeypatch):
    """Reviewer focus: real api_key string must never end up in the gzipped blob body."""
    snap = isolated_module
    sink: list = []
    _install_fake_azure(monkeypatch, sink)

    fresh_env.setenv("BLOB_ARCHIVE", "1")
    fresh_env.setenv("AZURE_BLOB_CONN", "fake-conn")

    real_key = "abc123_definitely_a_real_api_key"
    snap.SnapshotArchive().archive(
        source="odds_api", endpoint="/sports/",
        params={"apiKey": real_key, "api_key": real_key, "all": "false"},
        status=200, headers={}, body=b"[]",
    )

    assert len(sink) == 1
    raw = gzip.decompress(sink[0]["data"])
    assert real_key.encode() not in raw
    assert raw.count(b"<redacted>") == 2  # both casings redacted


# ---- Failure isolation -----------------------------------------------------

def test_blob_failure_buffers_locally(fresh_env, isolated_module, monkeypatch, tmp_path):
    """Upload raises → archive buffers under logs/snapshots/; scan does not abort."""
    snap = isolated_module
    sink: list = []
    _install_fake_azure(monkeypatch, sink, fail=True)

    fresh_env.setenv("BLOB_ARCHIVE", "1")
    fresh_env.setenv("AZURE_BLOB_CONN", "fake-conn")

    snap.SnapshotArchive().archive(
        source="odds_api", endpoint="/sports/",
        params={"apiKey": "SECRET"}, status=200, headers={}, body=b"[]",
    )

    assert sink == []  # nothing uploaded
    buffered = list((tmp_path / "snapshots").rglob("*.json.gz"))
    assert len(buffered) == 1, f"expected one buffered file, got {buffered}"


# ---- Buffer drain ---------------------------------------------------------

def test_buffer_drains_on_next_successful_upload(fresh_env, isolated_module,
                                                  monkeypatch, tmp_path):
    """Pre-seeded buffer files upload + delete on next successful archive."""
    snap = isolated_module
    sink: list = []
    _install_fake_azure(monkeypatch, sink, fail=False)

    fresh_env.setenv("BLOB_ARCHIVE", "1")
    fresh_env.setenv("AZURE_BLOB_CONN", "fake-conn")

    # Pre-seed two stale buffer files at the canonical key shape.
    buffer_root = tmp_path / "snapshots"
    pre_a = buffer_root / "odds_api/sports_/2026/04/30/old_a.json.gz"
    pre_b = buffer_root / "odds_api/sports_/2026/04/30/old_b.json.gz"
    for p, payload in ((pre_a, b"a-body"), (pre_b, b"b-body")):
        p.parent.mkdir(parents=True, exist_ok=True)
        import gzip as _gz
        p.write_bytes(_gz.compress(payload))

    snap.SnapshotArchive().archive(
        source="odds_api", endpoint="/sports/",
        params={"apiKey": "SECRET"}, status=200, headers={}, body=b"[]",
    )

    # 1 fresh + 2 drained = 3 uploads total
    assert len(sink) == 3
    # Old files deleted after successful upload
    assert not pre_a.exists()
    assert not pre_b.exists()


def test_buffer_persists_when_drain_fails(fresh_env, isolated_module,
                                          monkeypatch, tmp_path):
    """If the drain upload fails, the buffer file is NOT deleted."""
    snap = isolated_module
    sink: list = []
    _install_fake_azure(monkeypatch, sink, fail=True)

    fresh_env.setenv("BLOB_ARCHIVE", "1")
    fresh_env.setenv("AZURE_BLOB_CONN", "fake-conn")

    buffer_root = tmp_path / "snapshots"
    seed = buffer_root / "odds_api/sports_/2026/04/30/old_a.json.gz"
    seed.parent.mkdir(parents=True, exist_ok=True)
    import gzip as _gz
    seed.write_bytes(_gz.compress(b"a-body"))

    snap.SnapshotArchive().archive(
        source="odds_api", endpoint="/sports/",
        params={"apiKey": "SECRET"}, status=200, headers={}, body=b"[]",
    )

    assert sink == []
    # Both the new attempt and the existing seed remain.
    assert seed.exists()
    leftover = list(buffer_root.rglob("*.json.gz"))
    assert len(leftover) == 2  # seed + freshly buffered current request
