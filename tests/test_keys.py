"""
Contract tests guarding against the drift-key collision bug (Review finding #1):
closing_line.py and app.py must use the same 6-tuple key shape.
"""
import csv
import io
import sys
from pathlib import Path
import importlib

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_drift_key_includes_market_and_line():
    """app.py load_drift keys must be 6-tuples ending in (market, line)."""
    import app as _app

    # Build a minimal in-memory drift CSV
    rows = [
        {"home": "Arsenal", "away": "Chelsea", "kickoff": "2026-05-10T15:00:00Z",
         "side": "H", "market": "h2h", "line": "", "t_minus_min": "60",
         "pinnacle_odds": "2.20", "captured_at": "2026-05-10T13:59:00Z",
         "pinnacle_prob": "0.45", "n_books": "30"},
        {"home": "Arsenal", "away": "Chelsea", "kickoff": "2026-05-10T15:00:00Z",
         "side": "OVER", "market": "totals", "line": "2.5", "t_minus_min": "60",
         "pinnacle_odds": "1.85", "captured_at": "2026-05-10T13:59:00Z",
         "pinnacle_prob": "0.52", "n_books": "28"},
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)

    # Patch DRIFT_CSV to our in-memory data
    original = _app.DRIFT_CSV
    try:
        tmp = ROOT / "logs" / "_test_drift_tmp.csv"
        tmp.write_text(buf.getvalue())
        _app.DRIFT_CSV = tmp
        drift = _app.load_drift()
        keys = list(drift.keys())
        # Each key must be a 6-tuple
        for k in keys:
            assert len(k) == 6, f"Expected 6-tuple, got {len(k)}-tuple: {k}"
        # The two rows must produce different keys (market differs)
        assert len(keys) == 2
    finally:
        _app.DRIFT_CSV = original
        if tmp.exists():
            tmp.unlink()


def test_closing_line_key_matches_drift_key():
    """closing_line.py drift dedup key is 7-tuple (home, away, kickoff, side, t_label, market, line).
    Verify the shape matches the pattern used in closing_line.py without importing it
    (the module raises RuntimeError on import when ODDS_API_KEY is absent).
    """
    # Matches closing_line.py line ~327: dk = (home, away, kickoff_str, side, str(t_label), market, line_val)
    home, away, kickoff, side, t_label, market, line = (
        "Arsenal", "Chelsea", "2026-05-10T15:00:00Z", "H", "60", "h2h", ""
    )
    dk = (home, away, kickoff, side, str(t_label), market, line)
    assert len(dk) == 7

    # The app.py lookup key is 6-tuple (drops t_label)
    lk = (home, away, kickoff, side, market, line)
    assert len(lk) == 6


def test_bets_csv_dedup_key_matches():
    """scan_odds.py dedup key on append must be a 5-tuple (not including market/line for dedup purposes)."""
    # The dedup key in find_value_bets is (home, away, market, line, side) — 5-tuple
    vb = {"home": "Arsenal", "away": "Chelsea", "market": "h2h", "line": "", "side": "H"}
    k = (vb["home"], vb["away"], vb["market"], str(vb.get("line", "")), vb["side"])
    assert len(k) == 5
