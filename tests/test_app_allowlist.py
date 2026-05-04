"""
A.7: tests for the dashboard email allowlist (`@app.before_request`).

Container Apps Easy Auth handles authentication; this test covers the
defense-in-depth allowlist guard inside `app.py` that 403s any signed-in
account whose email is not in `DASHBOARD_ALLOWED_EMAILS`.
"""
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _principal_header(claims_kv: dict[str, str]) -> str:
    """Encode a fake X-MS-CLIENT-PRINCIPAL header for a Google OIDC user."""
    payload = {"claims": [{"typ": k, "val": v} for k, v in claims_kv.items()]}
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def _setup(monkeypatch, tmp_path):
    import app as _app
    monkeypatch.setattr(_app, "RESEARCH_FEED_MD", tmp_path / "RESEARCH_FEED.md")
    return _app


def test_allowlist_disabled_when_env_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("DASHBOARD_ALLOWED_EMAILS", raising=False)
    _app = _setup(monkeypatch, tmp_path)
    with _app.app.test_client() as c:
        r = c.get("/health")
        assert r.status_code == 200


def test_allowlist_blocks_missing_principal(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_ALLOWED_EMAILS", "robert.freire@gmail.com")
    _app = _setup(monkeypatch, tmp_path)
    with _app.app.test_client() as c:
        r = c.get("/")
        assert r.status_code == 401


def test_allowlist_admits_listed_email_via_name_header(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_ALLOWED_EMAILS", "robert.freire@gmail.com")
    _app = _setup(monkeypatch, tmp_path)
    with _app.app.test_client() as c:
        r = c.get("/", headers={"X-MS-CLIENT-PRINCIPAL-NAME": "Robert.Freire@gmail.com"})
        assert r.status_code == 200


def test_allowlist_admits_listed_email_via_principal_blob(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_ALLOWED_EMAILS", "robert.freire@gmail.com")
    _app = _setup(monkeypatch, tmp_path)
    header = _principal_header({"emails": "robert.freire@gmail.com"})
    with _app.app.test_client() as c:
        r = c.get("/", headers={"X-MS-CLIENT-PRINCIPAL": header})
        assert r.status_code == 200


def test_allowlist_blocks_other_email(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_ALLOWED_EMAILS", "robert.freire@gmail.com")
    _app = _setup(monkeypatch, tmp_path)
    with _app.app.test_client() as c:
        r = c.get("/", headers={"X-MS-CLIENT-PRINCIPAL-NAME": "stranger@example.com"})
        assert r.status_code == 403


def test_health_bypasses_allowlist(monkeypatch, tmp_path):
    monkeypatch.setenv("DASHBOARD_ALLOWED_EMAILS", "robert.freire@gmail.com")
    _app = _setup(monkeypatch, tmp_path)
    with _app.app.test_client() as c:
        # No principal headers, but /health must still respond — Container
        # Apps excludes /health from auth, and our guard mirrors that.
        r = c.get("/health")
        assert r.status_code == 200
