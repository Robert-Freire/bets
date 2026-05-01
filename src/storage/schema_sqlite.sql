-- SQLite mirror of src/storage/schema.sql for in-memory smoke tests.
-- SQLite uses dynamic typing — UUIDs as TEXT, datetime2 as TEXT (ISO8601),
-- decimal as REAL. Keep this file in sync with schema.sql when editing.

CREATE TABLE IF NOT EXISTS fixtures (
    id          TEXT    PRIMARY KEY,
    sport_key   TEXT    NOT NULL,
    league      TEXT,
    home        TEXT    NOT NULL,
    away        TEXT    NOT NULL,
    kickoff_utc TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS ix_fixtures_kickoff_sport ON fixtures (kickoff_utc, sport_key);

CREATE TABLE IF NOT EXISTS books (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL UNIQUE,
    region          TEXT,
    commission_rate REAL    NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS strategies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT,
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS bets (
    id                  TEXT    PRIMARY KEY,
    fixture_id          TEXT    NOT NULL REFERENCES fixtures(id),
    book_id             INTEGER NOT NULL REFERENCES books(id),
    scanned_at          TEXT    NOT NULL,
    market              TEXT    NOT NULL,
    line                REAL,
    side                TEXT    NOT NULL,
    odds                REAL    NOT NULL,
    impl_raw            REAL,
    impl_effective      REAL,
    edge                REAL,
    edge_gross          REAL,
    effective_odds      REAL,
    commission_rate     REAL,
    consensus           REAL,
    pinnacle_cons       REAL,
    n_books             INTEGER,
    confidence          TEXT,
    model_signal        TEXT,
    dispersion          REAL,
    outlier_z           REAL,
    devig_method        TEXT,
    weight_scheme       TEXT,
    stake               REAL,
    actual_stake        REAL,
    result              TEXT    NOT NULL DEFAULT 'pending',
    settled_at          TEXT,
    pnl                 REAL,
    pinnacle_close_prob REAL,
    clv_pct             REAL,
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS ix_bets_scanned ON bets (scanned_at);
CREATE INDEX IF NOT EXISTS ix_bets_fixture_lookup ON bets (fixture_id, side, market, line);

CREATE TABLE IF NOT EXISTS paper_bets (
    id                  TEXT    PRIMARY KEY,
    strategy_id         INTEGER NOT NULL REFERENCES strategies(id),
    fixture_id          TEXT    NOT NULL REFERENCES fixtures(id),
    book_id             INTEGER NOT NULL REFERENCES books(id),
    scanned_at          TEXT    NOT NULL,
    market              TEXT    NOT NULL,
    line                REAL,
    side                TEXT    NOT NULL,
    odds                REAL    NOT NULL,
    impl_raw            REAL,
    impl_effective      REAL,
    edge                REAL,
    edge_gross          REAL,
    effective_odds      REAL,
    commission_rate     REAL,
    consensus           REAL,
    pinnacle_cons       REAL,
    n_books             INTEGER,
    confidence          TEXT,
    model_signal        TEXT,
    dispersion          REAL,
    outlier_z           REAL,
    devig_method        TEXT,
    weight_scheme       TEXT,
    stake               REAL,
    actual_stake        REAL,
    result              TEXT    NOT NULL DEFAULT 'pending',
    settled_at          TEXT,
    pnl                 REAL,
    pinnacle_close_prob REAL,
    clv_pct             REAL,
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS ix_paper_bets_strategy_result ON paper_bets (strategy_id, result);
CREATE INDEX IF NOT EXISTS ix_paper_bets_fixture_lookup ON paper_bets (fixture_id, side, market, line);

CREATE TABLE IF NOT EXISTS closing_lines (
    fixture_id             TEXT    NOT NULL REFERENCES fixtures(id),
    side                   TEXT    NOT NULL,
    market                 TEXT    NOT NULL,
    line                   REAL    NOT NULL DEFAULT 0,
    book_id                INTEGER NOT NULL REFERENCES books(id),
    captured_at            TEXT    NOT NULL,
    pinnacle_close_prob    REAL,
    pinnacle_raw_odds      REAL,
    your_book_flagged_odds REAL,
    your_book_close_odds   REAL,
    clv_pct                REAL,
    PRIMARY KEY (fixture_id, side, market, line, book_id)
);

CREATE TABLE IF NOT EXISTS drift (
    fixture_id     TEXT    NOT NULL REFERENCES fixtures(id),
    side           TEXT    NOT NULL,
    market         TEXT    NOT NULL,
    line           REAL    NOT NULL DEFAULT 0,
    book_id        INTEGER NOT NULL REFERENCES books(id),
    t_minus_min    INTEGER NOT NULL,
    captured_at    TEXT    NOT NULL,
    your_book_odds REAL,
    pinnacle_odds  REAL,
    n_books        INTEGER,
    PRIMARY KEY (fixture_id, side, market, line, book_id, t_minus_min)
);
