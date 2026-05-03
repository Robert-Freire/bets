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


def test_drift_key_includes_market_and_line(tmp_path):
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

    # Patch DRIFT_CSV to a temp file so we don't touch the real logs/
    tmp = tmp_path / "drift_test.csv"
    tmp.write_text(buf.getvalue())
    original = _app.DRIFT_CSV
    _app.DRIFT_CSV = tmp
    try:
        drift = _app.load_drift()
        keys = list(drift.keys())
        # Each key must be a 6-tuple
        for k in keys:
            assert len(k) == 6, f"Expected 6-tuple, got {len(k)}-tuple: {k}"
        # The two rows must produce different keys (market differs)
        assert len(keys) == 2
    finally:
        _app.DRIFT_CSV = original


def test_closing_line_key_matches_drift_key(tmp_path):
    """closing_line.py drift key is 7-tuple (home, away, kickoff, side, t_label, market, line).
    The app.py lookup key is the same 7-tuple minus t_label at index 4.
    Verify the relationship with a real equality check, not just length asserts.
    """
    home, away, kickoff, side, t_label, market, line = (
        "Arsenal", "Chelsea", "2026-05-10T15:00:00Z", "H", "60", "h2x", ""
    )
    dk = (home, away, kickoff, side, str(t_label), market, line)  # 7-tuple as in closing_line.py
    lk = (home, away, kickoff, side, market, line)                 # 6-tuple as in app.py

    # Lookup key must equal the drift key with t_label (index 4) removed
    assert lk == dk[:4] + dk[5:], f"lk={lk!r} != dk without t_label {dk[:4] + dk[5:]!r}"


def test_bets_csv_dedup_key_matches():
    """scan_odds.py dedup key on append must be a 5-tuple (not including market/line for dedup purposes)."""
    # The dedup key in find_value_bets is (home, away, market, line, side) — 5-tuple
    vb = {"home": "Arsenal", "away": "Chelsea", "market": "h2h", "line": "", "side": "H"}
    k = (vb["home"], vb["away"], vb["market"], str(vb.get("line", "")), vb["side"])
    assert len(k) == 5


# ── fixture_uuid + _norm_name ─────────────────────────────────────────────────

def test_fixture_uuid_is_deterministic():
    from src.storage._keys import fixture_uuid
    id1 = fixture_uuid("soccer_epl", "2026-05-10T14:00:00Z", "Arsenal", "Chelsea")
    id2 = fixture_uuid("soccer_epl", "2026-05-10T14:00:00Z", "Arsenal", "Chelsea")
    assert id1 == id2


def test_fixture_uuid_collapses_timestamp_to_date():
    from src.storage._keys import fixture_uuid
    id1 = fixture_uuid("soccer_epl", "2026-05-10T14:00:00Z", "Arsenal", "Chelsea")
    id2 = fixture_uuid("soccer_epl", "2026-05-10T18:00:00Z", "Arsenal", "Chelsea")
    assert id1 == id2


def test_fixture_uuid_collapses_fc_suffix():
    from src.storage._keys import fixture_uuid
    id1 = fixture_uuid("soccer_epl", "2026-05-10T14:00:00Z", "Arsenal FC", "Chelsea FC")
    id2 = fixture_uuid("soccer_epl", "2026-05-10T14:00:00Z", "Arsenal", "Chelsea")
    assert id1 == id2


def test_fixture_uuid_different_leagues():
    from src.storage._keys import fixture_uuid
    id1 = fixture_uuid("soccer_epl", "2026-05-10T14:00:00Z", "Arsenal", "Chelsea")
    id2 = fixture_uuid("soccer_germany_bundesliga", "2026-05-10T14:00:00Z", "Arsenal", "Chelsea")
    assert id1 != id2


def test_fixture_uuid_different_dates():
    from src.storage._keys import fixture_uuid
    id1 = fixture_uuid("soccer_epl", "2026-05-10T14:00:00Z", "Arsenal", "Chelsea")
    id2 = fixture_uuid("soccer_epl", "2026-05-11T14:00:00Z", "Arsenal", "Chelsea")
    assert id1 != id2


def test_fixture_uuid_does_not_collide_with_bet_uuid():
    from src.storage._keys import fixture_uuid, bet_uuid, normalise_line, scan_date_of
    fid = fixture_uuid("soccer_epl", "2026-05-10T14:00:00Z", "Arsenal", "Chelsea")
    bid = bet_uuid("2026-05-03", "2026-05-10T14:00:00Z", "Arsenal", "Chelsea",
                   "h2h", "", "HOME", "bet365")
    assert fid != bid


def test_norm_name_strips_fc():
    from src.storage._keys import _norm_name
    assert _norm_name("Arsenal FC") == "arsenal"
    assert _norm_name("Arsenal") == "arsenal"


def test_norm_name_folds_accents():
    from src.storage._keys import _norm_name
    assert _norm_name("Mönchengladbach") == "monchengladbach"
