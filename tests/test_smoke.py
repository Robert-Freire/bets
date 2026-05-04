"""Flask smoke test: GET / returns 200 with non-empty body (DB disabled — empty list)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_app_starts_and_renders_index(tmp_path, monkeypatch):
    import app as _app
    monkeypatch.setattr(_app, "RESEARCH_FEED_MD", tmp_path / "RESEARCH_FEED.md")
    _app.app.config["TESTING"] = True
    client = _app.app.test_client()
    response = client.get("/")
    assert response.status_code == 200
    assert len(response.data) > 0
