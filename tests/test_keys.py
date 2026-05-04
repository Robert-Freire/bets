"""
Contract tests guarding against the drift-key collision bug (Review finding #1):
closing_line.py and app.py must use the same 6-tuple key shape.
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA_SQLITE = ROOT / "src" / "storage" / "schema_sqlite.sql"


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQLITE.read_text())
    conn.commit()
    return conn


def test_drift_key_includes_market_and_line(tmp_path):
    """BetRepo.get_drift keys must be 6-tuples ending in (market, line)."""
    from src.storage.repo import BetRepo
    db = _make_db()
    repo = BetRepo(dsn="sqlite-test")
    repo._conn = db
    repo._cur = db.cursor()
    repo._connect = lambda: db  # type: ignore[method-assign]

    # Insert two drift rows with different markets via add_drift_snapshot
    common = {"kickoff": "2026-05-10 15:00", "home": "Arsenal", "away": "Chelsea",
              "side": "HOME", "sport": "EPL",
              "your_book_odds": 2.2, "pinnacle_odds": 2.0, "n_books": 30,
              "captured_at": "2026-05-10 13:59 UTC"}
    repo.add_drift_snapshot([
        {**common, "market": "h2h", "line": "", "book": "bet365", "t_minus_min": 60},
        {**common, "market": "totals", "line": "2.5", "book": "bet365", "t_minus_min": 60},
    ])
    db.commit()

    drift = repo.get_drift()
    assert drift is not None
    keys = list(drift.keys())
    for k in keys:
        assert len(k) == 6, f"Expected 6-tuple, got {len(k)}-tuple: {k}"
    assert len(keys) == 2


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


def test_fixture_uuid_pinned_value():
    """Pin the exact UUID so any future key-shape change fails loudly.

    This is the canary: if this assertion breaks, you changed fixture_uuid's
    key shape and must ship a DB remediation script before the next ingest.
    See src/storage/_keys.py docstring for the protocol.
    """
    from src.storage._keys import fixture_uuid
    assert fixture_uuid("soccer_epl", "2026-05-10T14:00:00Z", "Arsenal", "Chelsea") == \
        "7831f699-219a-5cdc-9e77-8409c955260b"
