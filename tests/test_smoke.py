"""Flask smoke test: GET / returns 200 with non-empty body on empty bets.csv."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_app_starts_and_renders_index(tmp_path, monkeypatch):
    # Point log files at temp dir so no real CSV needed
    empty_bets = tmp_path / "bets.csv"
    empty_bets.write_text("scanned_at,sport,market,line,home,away,kickoff,"
                          "side,book,odds,edge,consensus,pinnacle_cons,"
                          "n_books,confidence,model_signal,dispersion,outlier_z,"
                          "stake,result\n")
    empty_drift = tmp_path / "drift.csv"
    empty_drift.write_text("")
    empty_closing = tmp_path / "closing_lines.csv"
    empty_closing.write_text("")

    import app as _app
    monkeypatch.setattr(_app, "BETS_CSV", empty_bets)
    monkeypatch.setattr(_app, "DRIFT_CSV", empty_drift)

    _app.app.config["TESTING"] = True
    client = _app.app.test_client()
    response = client.get("/")
    assert response.status_code == 200
    assert len(response.data) > 0
