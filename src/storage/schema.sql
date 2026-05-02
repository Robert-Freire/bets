-- Canonical MSSQL T-SQL schema for the kaunitz dev/prod DBs.
-- Idempotent: re-running this file on a populated DB is a no-op.
-- Sibling SQLite variant (smoke tests): src/storage/schema_sqlite.sql.
-- Keep the two files in sync when editing.

-- Reference tables --------------------------------------------------------

IF OBJECT_ID(N'fixtures', N'U') IS NULL
CREATE TABLE fixtures (
    id            uniqueidentifier NOT NULL PRIMARY KEY,
    sport_key     nvarchar(64)     NOT NULL,
    league        nvarchar(128)    NULL,
    home          nvarchar(128)    NOT NULL,
    away          nvarchar(128)    NOT NULL,
    kickoff_utc   datetime2(3)     NOT NULL,
    created_at    datetime2(3)     NOT NULL DEFAULT SYSUTCDATETIME()
);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_fixtures_kickoff_sport')
CREATE INDEX ix_fixtures_kickoff_sport ON fixtures (kickoff_utc, sport_key);

IF OBJECT_ID(N'books', N'U') IS NULL
CREATE TABLE books (
    id              int          NOT NULL IDENTITY(1,1) PRIMARY KEY,
    name            nvarchar(64) NOT NULL UNIQUE,
    region          nvarchar(8)  NULL,
    commission_rate decimal(6,4) NOT NULL DEFAULT 0
);

IF OBJECT_ID(N'strategies', N'U') IS NULL
CREATE TABLE strategies (
    id          int           NOT NULL IDENTITY(1,1) PRIMARY KEY,
    name        nvarchar(64)  NOT NULL UNIQUE,
    description nvarchar(512) NULL,
    active      bit           NOT NULL DEFAULT 1
);

-- Production bets (settled, real money) -----------------------------------

IF OBJECT_ID(N'bets', N'U') IS NULL
CREATE TABLE bets (
    id                  uniqueidentifier NOT NULL PRIMARY KEY,
    fixture_id          uniqueidentifier NOT NULL REFERENCES fixtures(id),
    book_id             int              NOT NULL REFERENCES books(id),
    scanned_at          datetime2(3)     NOT NULL,
    market              nvarchar(16)     NOT NULL,
    line                decimal(8,2)     NULL,
    side                nvarchar(32)     NOT NULL,
    odds                decimal(10,4)    NOT NULL,
    impl_raw            decimal(10,8)    NULL,
    impl_effective      decimal(10,8)    NULL,
    edge                decimal(10,8)    NULL,
    edge_gross          decimal(10,8)    NULL,
    effective_odds      decimal(10,4)    NULL,
    commission_rate     decimal(6,4)     NULL,
    consensus           decimal(10,8)    NULL,
    pinnacle_cons       decimal(10,8)    NULL,
    n_books             int              NULL,
    confidence          nvarchar(8)      NULL,
    model_signal        nvarchar(16)     NULL,
    dispersion          decimal(10,8)    NULL,
    outlier_z           decimal(10,4)    NULL,
    devig_method        nvarchar(16)     NULL,
    weight_scheme       nvarchar(32)     NULL,
    stake               decimal(10,2)    NULL,
    actual_stake        decimal(10,2)    NULL,
    result              nvarchar(16)     NOT NULL DEFAULT N'pending',
    settled_at          datetime2(3)     NULL,
    pnl                 decimal(10,2)    NULL,
    pinnacle_close_prob decimal(10,8)    NULL,
    clv_pct             decimal(10,6)    NULL,
    created_at          datetime2(3)     NOT NULL DEFAULT SYSUTCDATETIME()
);

-- A.5: settle handler writes actual_stake into the DB. Older deployments
-- (created in A.2 before A.5) need this column added in place.
IF COL_LENGTH(N'bets', N'actual_stake') IS NULL
ALTER TABLE bets ADD actual_stake decimal(10,2) NULL;

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_bets_scanned')
CREATE INDEX ix_bets_scanned ON bets (scanned_at);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_bets_fixture_lookup')
CREATE INDEX ix_bets_fixture_lookup ON bets (fixture_id, side, market, line);

-- Paper portfolios (shadow strategies; CLV-only, no real stake) -----------

IF OBJECT_ID(N'paper_bets', N'U') IS NULL
CREATE TABLE paper_bets (
    id                  uniqueidentifier NOT NULL PRIMARY KEY,
    strategy_id         int              NOT NULL REFERENCES strategies(id),
    fixture_id          uniqueidentifier NOT NULL REFERENCES fixtures(id),
    book_id             int              NOT NULL REFERENCES books(id),
    scanned_at          datetime2(3)     NOT NULL,
    market              nvarchar(16)     NOT NULL,
    line                decimal(8,2)     NULL,
    side                nvarchar(32)     NOT NULL,
    odds                decimal(10,4)    NOT NULL,
    impl_raw            decimal(10,8)    NULL,
    impl_effective      decimal(10,8)    NULL,
    edge                decimal(10,8)    NULL,
    edge_gross          decimal(10,8)    NULL,
    effective_odds      decimal(10,4)    NULL,
    commission_rate     decimal(6,4)     NULL,
    consensus           decimal(10,8)    NULL,
    pinnacle_cons       decimal(10,8)    NULL,
    n_books             int              NULL,
    confidence          nvarchar(8)      NULL,
    model_signal        nvarchar(16)     NULL,
    dispersion          decimal(10,8)    NULL,
    outlier_z           decimal(10,4)    NULL,
    devig_method        nvarchar(16)     NULL,
    weight_scheme       nvarchar(32)     NULL,
    stake               decimal(10,2)    NULL,
    actual_stake        decimal(10,2)    NULL,
    result              nvarchar(16)     NOT NULL DEFAULT N'pending',
    settled_at          datetime2(3)     NULL,
    pnl                 decimal(10,2)    NULL,
    pinnacle_close_prob decimal(10,8)    NULL,
    clv_pct             decimal(10,6)    NULL,
    created_at          datetime2(3)     NOT NULL DEFAULT SYSUTCDATETIME()
);

-- A.5: keep paper_bets symmetric with bets so the same SELECT works.
IF COL_LENGTH(N'paper_bets', N'actual_stake') IS NULL
ALTER TABLE paper_bets ADD actual_stake decimal(10,2) NULL;

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_paper_bets_strategy_result')
CREATE INDEX ix_paper_bets_strategy_result ON paper_bets (strategy_id, result);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'ix_paper_bets_fixture_lookup')
CREATE INDEX ix_paper_bets_fixture_lookup ON paper_bets (fixture_id, side, market, line);

-- CLV diagnostics ----------------------------------------------------------
-- closing_lines and drift use a 0 sentinel for `line` on h2h markets so
-- that the natural-key composite PK stays NOT NULL across all markets.

IF OBJECT_ID(N'closing_lines', N'U') IS NULL
CREATE TABLE closing_lines (
    fixture_id             uniqueidentifier NOT NULL REFERENCES fixtures(id),
    side                   nvarchar(32)     NOT NULL,
    market                 nvarchar(16)     NOT NULL,
    line                   decimal(8,2)     NOT NULL DEFAULT 0,
    book_id                int              NOT NULL REFERENCES books(id),
    captured_at            datetime2(3)     NOT NULL,
    pinnacle_close_prob    decimal(10,8)    NULL,
    pinnacle_raw_odds      decimal(10,4)    NULL,
    your_book_flagged_odds decimal(10,4)    NULL,
    your_book_close_odds   decimal(10,4)    NULL,
    clv_pct                decimal(10,6)    NULL,
    PRIMARY KEY (fixture_id, side, market, line, book_id)
);

IF OBJECT_ID(N'drift', N'U') IS NULL
CREATE TABLE drift (
    fixture_id     uniqueidentifier NOT NULL REFERENCES fixtures(id),
    side           nvarchar(32)     NOT NULL,
    market         nvarchar(16)     NOT NULL,
    line           decimal(8,2)     NOT NULL DEFAULT 0,
    book_id        int              NOT NULL REFERENCES books(id),
    t_minus_min    int              NOT NULL,
    captured_at    datetime2(3)     NOT NULL,
    your_book_odds decimal(10,4)    NULL,
    pinnacle_odds  decimal(10,4)    NULL,
    n_books        int              NULL,
    PRIMARY KEY (fixture_id, side, market, line, book_id, t_minus_min)
);

-- Per-(book, league, market) skill + bias signals (Phase B.0) ---------------
-- Keyed by (book, league, market, window_end): one row per rolling 8-week
-- window. Re-running compute_book_skill.py for the same window_end is safe
-- (delete + re-insert).

IF OBJECT_ID(N'book_skill', N'U') IS NULL
CREATE TABLE book_skill (
    book               nvarchar(64)  NOT NULL,
    league             nvarchar(128) NOT NULL,
    market             nvarchar(16)  NOT NULL,
    window_end         date          NOT NULL,
    n_fixtures         int           NOT NULL,
    -- Skill columns (B.2 gated — null until enough archive depth):
    brier_vs_close     decimal(10,8) NULL,
    brier_vs_outcome   decimal(10,8) NULL,
    log_loss           decimal(10,8) NULL,
    -- Bias columns (B.1 gated):
    fav_longshot_slope decimal(10,8) NULL,
    home_bias          decimal(10,8) NULL,
    draw_bias          decimal(10,8) NULL,
    -- Free-tier signals (B.0.5 + B.0.6):
    flag_rate          decimal(10,8) NULL,
    mean_flag_edge     decimal(10,8) NULL,
    edge_vs_consensus  decimal(10,8) NULL,
    edge_vs_pinnacle   decimal(10,8) NULL,
    divergence         decimal(10,8) NULL,
    -- truth_anchor: 'pinnacle' for EPL/BL/SA/L1; 'bet365+bwin' for Champ/BL2
    truth_anchor       nvarchar(32)  NULL,
    created_at         datetime2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),
    PRIMARY KEY (book, league, market, window_end)
);
