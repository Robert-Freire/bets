# Bets — Multi-Sport Value Betting System

## What this is

A value betting scanner using the **Kaunitz consensus strategy**: compute the Shin-devigged fair probability across 30–40 bookmakers, then flag bets where a UK-licensed bookmaker's odds are significantly better than the consensus. CLV (closing-line value) against Pinnacle is the primary diagnostic for whether edge is real.

*Backtest: 2% edge → 17.65% ROI (Shin-corrected, 2026-04-29) — see `docs/BACKTEST.md`.*

## Quick start

```bash
# Run the scanner manually (WSL dev)
export $(cat .env.dev) && python3 scripts/scan_odds.py

# CLV backfill from football-data.co.uk Pinnacle close odds (Mon 08:00 cron)
# Requires BETS_DB_WRITE=1 — writes result/pnl/CLV to Azure SQL only (DB-only, no CSV writes)
export $(cat .env.dev) && python3 scripts/backfill_clv_from_fdco.py

# Local dashboard (track bets, log results, view CLV)
python3 app.py   # → http://localhost:5000

# Public dev dashboard (Azure Container Apps; Google OIDC, allowlist robert.freire@gmail.com)
# https://kaunitz-dev-dashboard-rfk1.orangebush-7e5af054.uksouth.azurecontainerapps.io

# Compare strategy variants (after a weekend of data)
# Requires BETS_DB_WRITE=1 — reads paper_bets from Azure SQL
export $(cat .env.dev) && python3 scripts/compare_strategies.py   # writes docs/STRATEGY_COMPARISON.md
```

## How the scanner works

1. **Pre-flight + canary** (free `/sports/?all=false` health log): the configured football league (`canary_league` in `config.json`, env `CANARY_LEAGUE`, default `soccer_epl`) is fetched **first** in the per-league loop. If it returns 0 events, remaining football leagues are skipped (saves ~20 credits) and a high-priority ntfy alert fires.
2. Fetches live odds from The Odds API (`uk,eu` regions, ~36 bookmakers per fixture).
3. **Shin-devigs** each book's implied probabilities before averaging.
4. Consensus = mean of Shin-fair probs across all books; Pinnacle's devigged prob logged as anchor.
5. **Filters**: rejects if cross-book stdev of fair probs > `MAX_DISPERSION=0.04`; rejects if the flagged book's z-score vs the rest exceeds `OUTLIER_Z_THRESHOLD=2.5`.
6. Flags bets where a **UK-licensed** bookmaker's devigged prob is ≥3% below consensus (Kaunitz), or ≥2% with CatBoost model agreement.
7. Sizes bets with half-Kelly + risk pipeline: £5 rounding, per-fixture 5% cap, 15% portfolio cap, drawdown brake.
8. Sends ntfy push (topic `robert-epl-bets-m4x9k`), deduped via `logs/notified.json` (12h per bet key).
9. Writes bets to Azure SQL via `BetRepo` (A.9: DB-only; no CSV writes) and archives raw API responses to Azure Blob via `SnapshotArchive` (A.5.5).

## Sports actively scanned

| Sport | Key | Min books |
|---|---|---|
| EPL | `soccer_epl` | 20 |
| Bundesliga | `soccer_germany_bundesliga` | 20 |
| Serie A | `soccer_italy_serie_a` | 20 |
| EFL Championship | `soccer_efl_champ` | 25 |
| Ligue 1 | `soccer_france_ligue_one` | 20 |
| Bundesliga 2 | `soccer_germany_bundesliga2` | 20 |

Cron-trimmed 2026-05-01 to fit within the 500/mo Odds API budget. NBA + tennis dropped from cron; the code paths still work for ad-hoc scans (`--sports nba`, `--sports tennis`). La Liga excluded — too noisy, not enough UK book coverage.

## Confidence levels

- **HIGH** ≥30 books in consensus → high-priority ntfy notification
- **MED** 20–29 books → default priority
- **LOW** <20 books → low priority

## Setup (fresh clone)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install --no-deps understat   # see requirements.txt for why this is separate
```

## Environment

Production cron runs on a Raspberry Pi 5 (`robert@192.168.0.28`, Raspberry Pi OS Trixie / Python 3.13, project at `~/projects/bets`). WSL is the dev environment — manual scans, code changes, and a parallel test cron stream. Each side has its own free Odds API key so manual testing never burns prod quota.

```bash
# Pi: ~/projects/bets/.env (gitignored) — PROD key, used by cron only
ODDS_API_KEY=<prod>
BANKROLL=1000   # optional override; falls back to config.json → default 1000

# WSL: /home/rfreire/projects/bets/.env.dev (gitignored) — DEV key, manual + test cron
ODDS_API_KEY=<dev>
NTFY_TOPIC_OVERRIDE=   # empty = silence ntfy on test stream
```

**Never** run manual scans on the Pi against the prod key (one exception: the post-cutover validation run on 2026-05-01).

### Azure SQL DB writes — WSL only (Pi must NOT set these)

After A.9, these env vars are **required** for the scanner, FDCO backfill, dashboard, and `compare_strategies` — those scripts now exit with an error if `BETS_DB_WRITE` is unset.

```bash
BETS_DB_WRITE=1
# Either: literal pyodbc DSN with admin password embedded
AZURE_SQL_DSN="Driver={ODBC Driver 18 for SQL Server};Server=tcp:kaunitz-dev-sql-uksouth-rfk1.database.windows.net,1433;Database=kaunitz;Uid=kaunitzadmin;Pwd=...;Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;"
# Or: components + Key Vault refs (BetRepo fetches the password at boot via `az keyvault secret show`)
# AZURE_SQL_SERVER=kaunitz-dev-sql-uksouth-rfk1.database.windows.net
# AZURE_SQL_USER=kaunitzadmin
# AZURE_SQL_DATABASE=kaunitz
# AZURE_SQL_KV_VAULT=kaunitz-dev-kv-rfk1
# AZURE_SQL_KV_SECRET=sql-admin-password
```

### A.5.5 raw API blob archive — WSL only (Pi must NOT set these)

```bash
BLOB_ARCHIVE=1
# Either: literal blob storage connection string
AZURE_BLOB_CONN="DefaultEndpointsProtocol=https;AccountName=kaunitzdevstrfk1;..."
# Or: Key Vault refs
# AZURE_BLOB_KV_VAULT=kaunitz-dev-kv-rfk1
# AZURE_BLOB_KV_SECRET=blob-storage-connection-string
# Optional override (default "raw-api-snapshots")
# AZURE_BLOB_CONTAINER=raw-api-snapshots
```

### OddsPapi Pinnacle close-odds resolver — WSL only (Pi must NOT set this)

Used by `scripts/backfill_clv_from_oddspapi.py` (Mon+Wed 10:00 cron). FDCO stopped publishing Pinnacle close odds (`PSCH/PSCD/PSCA`, `PC>2.5/PC<2.5`) ~mid-Jan 2026 after Pinnacle closed their public API on 2025-07-23. OddsPapi (`api.oddspapi.io`) replaces that source for h2h Pinnacle close.

```bash
ODDSPAPI_KEY=...   # from https://oddspapi.io/en/account (free tier: 250 req/mo, no credit card)
```

Quirks:
- Historical-odds endpoint capped at the **last 3 months** — bets older than that can't be backfilled from OddsPapi (the gap from FDCO-cutoff 2026-01-15 to ~2026-02-05 is unrecoverable from this source).
- Free tier rate-limit is tight; script paces 5 s between calls and honours `Retry-After` on 429.
- Script caches every response under `logs/cache/oddspapi/` — re-runs over the same window cost zero requests.
- When `ODDSPAPI_KEY` is unset the script exits with a clear error rather than silently skipping. Pi-safe because Pi never sets the key.

**Pi-safety contract (post-A.9).** `BetRepo` and `SnapshotArchive` import lazily and stay dormant when env vars are unset. **However**, after A.9 the scanner (`scan_odds.main`) and the FDCO backfill (`backfill_clv_from_fdco`) **refuse to run** without `BETS_DB_WRITE=1` — silent CSV fallback is gone. If the Pi pulls main without env vars set, those scripts will exit with a clear error rather than drop bets silently. A.10 onboards Pi to its own DB; until then, do not pull main onto the Pi. Lifecycle on the blob container: tier-to-cool at 30d, **no auto-delete** (archive is the substrate for future data-quality rules).

API budget: free tier 500 credits/month per key. Cost per call = `regions × markets`; each league fetch is `uk,eu × h2h,totals` = 4 credits. Current schedule: 5 scans/wk × 6 leagues × 4 cr × 4.345 wk ≈ **~520/mo theoretical ceiling**, less in practice (canary skip on empty-fixture days saves ~20 cr/scan; off-season league windows reduce further). Bi-weekly sports check adds ~20 cr/mo. The BTTS follow-up call 422s on free tier without charging. Migrate to paid tier (~$25/mo for 100k credits) once CLV evidence justifies it.

## Cron schedule (UTC)

Both Pi and WSL run the same scanner cron. Pi is canonical production (24/7); WSL is a parallel test stream (gaps from laptop sleep are acceptable). WSL skips `research_scan.py` and `refresh_xg.py` (Pi-canonical, would conflict on git-tracked outputs).

```
# Football scans (both machines)
30 7  * * 2     Tue 07:30           — fresh weekly lines
30 19 * * 5     Fri 19:30           — lineup hints
30 10 * * 6     Sat 10:30           — before 12:30 kick-off
30 16 * * 6     Sat 16:30           — between 15:00 and 17:30 games
30 12 * * 0     Sun 12:30           — before afternoon games

# CLV + housekeeping (both machines)
0  2  * * 1     Mon 02:00           — fixture calendar ingest (fixtures table in Azure SQL)
0  9  * * 1,3   Mon+Wed 09:00       — football-data.co.uk CLV + result backfill (DB-only since S.4)
0 10  * * 1,3   Mon+Wed 10:00       — OddsPapi Pinnacle close-odds backfill (WSL only — fills the FDCO Pinnacle gap; covers last 7 days)
0 11  * * 1     Mon 11:00           — compare_strategies.py → docs/STRATEGY_COMPARISON.md (uses fresh CLV from above)
0  8  1,15 * *  Bi-weekly 8am       — sports discovery check

# Pi only (WSL would conflict on git-tracked outputs)
0  6  * * 1     Mon 06:00           — refresh logs/team_xg.json from Understat
0 10  * * 1     Mon 10:00           — research scanner — curated sources
0 10  1 * *     1st of month 10:00  — research scanner — open-search
```

`scripts/closing_line.py` is paused (cron entry removed; kept in tree for fast revert). CLV now backfills weekly from FDCO — see CLV section below.

## Key files

```
scripts/scan_odds.py        Main scanner
scripts/backfill_clv_from_fdco.py  Mon+Wed 09:00 CLV backfill from football-data.co.uk (h2h FTR/PSCH)
scripts/backfill_clv_from_oddspapi.py  Mon+Wed 10:00 OddsPapi Pinnacle-close resolver (WSL only); fills the FDCO Pinnacle gap (mid-Jan 2026 onward) for 6 prod leagues + La Liga; idempotent; caches every API response under logs/cache/oddspapi/; audit per-run under logs/backfill/oddspapi/
scripts/closing_line.py     (paused 2026-05-01; kept for revert)
scripts/refresh_xg.py       Weekly xG snapshot from Understat → logs/team_xg.json
scripts/check_sports.py     Sports discovery (bi-weekly)
scripts/model_signals.py    CatBoost signal cache generator
scripts/compare_strategies.py  Strategy comparison report → docs/STRATEGY_COMPARISON.md
scripts/archive/migrate_csv_to_db.py  One-shot CSV → DB importer (archived; used once for A.3 backfill)
scripts/compute_book_skill.py  Per-(book, league, market) skill + bias signals → book_skill table (B.0.5 + B.0.6)
scripts/ingest_fixtures.py  Mon 02:00 fixture calendar ingest → fixtures table via FixtureRepo (Pi-safe: no-op when DB env vars unset)
scripts/remediate_fixture_uuids.py  One-shot UUID remediation; reference for any future fixture-key shape change

app.py                      Flask dashboard
templates/index.html        Dashboard UI

src/config.py               Shared league config loader (load_config(), load_leagues()); respects LEAGUES_CONFIG env var; enriches entries with fdco_code from downloader.LEAGUES
src/storage/schema.sql      Canonical MSSQL schema (8 tables: fixtures, books, strategies, bets, paper_bets, closing_lines, drift, book_skill)
src/storage/schema_sqlite.sql  SQLite mirror for in-memory smoke tests
src/storage/migrate.py      Idempotent migration runner
src/storage/_keys.py        Deterministic UUID5 + sport-label helpers (don't change the namespace)
src/storage/repo.py         BetRepo DB writer (A.9: DB-only) + FixtureRepo calendar read/write; lazy pyodbc import
src/storage/snapshots.py    SnapshotArchive: gzipped raw API responses → Azure Blob (A.5.5; lazy azure-storage-blob import; logs/snapshots/ buffer on failure)
src/data/fixture_calendar.py  Forward fixture lookup from fixtures table via FixtureRepo (Pi-safe: no-op when DB env vars unset)
src/betting/devig.py        Shin / proportional / power de-vigging
src/betting/risk.py         Stake rounding, fixture cap, portfolio cap, drawdown
src/betting/strategies.py   16 paper variants (A–P; A_production live, B–P shadow) + evaluate_strategy()
src/betting/walk_forward.py  Walk-forward backtest primitive (TimeSeriesSplit)

logs/team_xg.json           Per-team avg xG + q25 threshold (weekly; feeds K_draw_bias)
logs/bankroll.json          High-water mark for drawdown brake
logs/notified.json          Notification dedupe state
logs/scan.log               Scanner output
logs/backfill_clv.log       FDCO backfill output
logs/ingest_fixtures.log    Fixture ingest output
logs/closing_line.log       (frozen; closing_line.py paused — historical only)

tests/                      pytest suite (469 tests across 37 files; run with `pytest`)

docs/PLAN.md                Phased improvement roadmap (Phases 0–10, foundation — historical for done phases)
docs/PLAN_AZURE_2026-05.md  Azure migration plan (A.0–A.10)
docs/PLAN_RESEARCH_2026-04.md  Research sprint plan (R.0–R.11)
docs/RESEARCH_NOTES_2026-04.md  Manual deep-read findings
docs/BACKTEST.md            Shin-corrected backtest
docs/STRATEGY_COMPARISON.md  Latest CLV comparison across paper variants
docs/FIRST_WEEKEND.md       Live eval log + WSL/Pi divergence checklist
docs/RESEARCH_SCANNER.md    Automated research scanner spec
docs/RESEARCH_FEED.md       Auto-generated weekly findings (newest first)
docs/APPROACH.md            Full research-backed architecture
docs/REVIEW.md              Foundational review (2026-04-29; historical)
docs/FDCO_INGEST_NOTES.md   Football-data.co.uk ingest details
docs/AH_FEASIBILITY.md      Asian Handicap feasibility probe (R.9)
docs/COMMISSIONS.md         Per-book commission rates
docs/PAID_DATA_WISHLIST.md  Living list of investigations unlocked by paying for Odds API historical access — consult & append whenever a "we don't have enough data" question comes up
docs/DATA_ACQUISITION_IDEAS.md  Living catalogue of data-source ideas beyond today's stack (provider landscape, steam-chase scraper, Tennis-Data ingest, etc.) — consult & append whenever a "could we get data from X?" question comes up
docs/CONFIG_BOUNDARY.md     Static reference data vs tuning vs observed signals — read before adding constants/tables/config keys
data/raw/                   Football-data.co.uk CSVs + Understat xG (external reference, not system state)
```

## Dashboard

```bash
python3 app.py    # → http://localhost:5000
```

Stat tiles: Bets placed · Won/Lost/Void · Total staked · P&L · ROI · **Avg CLV** (green if >0; only shown once any bets have CLV) · **Research** (latest run count + mode + date from `docs/RESEARCH_FEED.md`).

Three bet sections: **Placed — awaiting result** · **Suggested — not yet placed** · **Settled** (with P&L + CLV%).

Both the public Azure dashboard and the local `python3 app.py` read exclusively from Azure SQL (A.9: DB-only, no CSV fallback). Pi data is not visible in either dashboard until A.10 onboards Pi to its own DB.

## Data state — where things live (post-A.9, pre-A.10)

| Surface | Code | Data |
|---|---|---|
| WSL (`main`) | DB-only after A.9. Scanner / FDCO backfill / dashboard / `compare_strategies` exit 1 without `BETS_DB_WRITE=1`. | All bets live in dev DB `kaunitz-dev-sql-uksouth-rfk1.database.windows.net / kaunitz`. CSV files under `logs/` no longer track live data — only operational state JSONs (`bankroll.json`, `notified.json`, `team_xg.json`, `model_signals.json`). |
| Pi (`robert@192.168.0.28`) | **Behind `main`** until A.10 lands. Still on pre-A.9 code with the dual-write paths. Pulling main onto Pi *now* would break cron — see PI_CATCHUP runbook. | Pi-local CSVs at `~/projects/bets/logs/` are the **only** copy of prod data. Not yet imported into any DB. |
| Dev dashboard (`kaunitz-dev-dashboard-rfk1.orangebush-...`) | Reads dev DB. | Shows WSL test stream only — has never seen Pi production data. |
| Prod dashboard | Does not exist yet (Phase A.10). | n/a |

**What this means in practice.** When you look at `STRATEGY_COMPARISON.md` or the dashboard, you are seeing the WSL test stream, not the canonical Pi-side production data. Pi data merges into a single picture only after A.10 (`docs/PI_CATCHUP_2026-05.md`).

## Risk management

Configured in `src/betting/risk.py` and `logs/bankroll.json`:

| Control | Value |
|---|---|
| Stake rounding | Nearest £5 (bets < £5 dropped) |
| Per-fixture cap | Max 5% of bankroll across all sides of one game |
| Portfolio cap | Max 15% of bankroll per scan |
| Drawdown brake | If bankroll < 85% of high-water → stakes halved |
| Bankroll source | `BANKROLL` env var → `config.json` → default £1000 |

## CLV diagnostics

CLV uses two stacked sources:

1. **FDCO** (`scripts/backfill_clv_from_fdco.py`, Mon+Wed 09:00 UTC) — football-data.co.uk free CSVs supply `result`/`pnl` (FTR/FTHG/FTAG) for all 6 production leagues. Until ~mid-Jan 2026 the same CSVs also supplied Pinnacle close (`PSCH/PSCD/PSCA`); after Pinnacle closed their public API on 2025-07-23, FDCO's Pinnacle columns went dark, so this script now mainly settles results.
2. **OddsPapi** (`scripts/backfill_clv_from_oddspapi.py`, Mon+Wed 10:00 UTC, WSL only) — fills the Pinnacle close gap. Reads pending rows from Azure SQL, matches DB fixtures to OddsPapi `fixtureId` via name normalisation + alias table, fetches `historical-odds?bookmakers=pinnacle` per fixture (cached under `logs/cache/oddspapi/`), picks the latest snapshot where `active=True AND createdAt ≤ kickoff`, Shin-devigs, writes `pinnacle_close_prob` + `clv_pct`. Both scripts require `BETS_DB_WRITE=1`. Idempotent. Audit CSV per run under `logs/backfill/oddspapi/<run_iso>/`.

Coverage: 6 production leagues + La Liga (in paper_bets only). OddsPapi historical-odds capped at last 3 months — bets settled 2026-01-15 → 2026-02-05 are unrecoverable from this source.

**Source-swap rationale (2026-05-01):** the every-5-min Odds API polling in `closing_line.py` was projected at ~700–1000 credits/month forward and risked the 500/mo free quota. FDCO is free and accurate enough for CLV signal evaluation.

**Trade-offs vs the previous closing_line.py path:**
- No drift (T-60/T-15/T-1 snapshots disabled; drift.csv removed in A.9).
- Top-6 leagues only: EPL, Bundesliga, Serie A, Ligue 1, Championship, Bundesliga 2.
- Totals only on the 2.5 line; no BTTS (FDCO doesn't carry it; system has 0 BTTS bets anyway).
- ≥1-day delay vs at-close capture — fine for weekly review, useless for live tracking.

**CLV scope limitations:**
- Tennis + NBA + BTTS bets produce no CLV (FDCO is football-only).
- Totals + BTTS bets always show `model_signal=?` — CatBoost only produces signals for h2h on EPL/Bundesliga/Serie A/Ligue 1, so the 2–3% model-filtered notification path only ever fires on h2h bets in those four leagues.

**CLV is the gate.** If avg CLV stays negative over ~50 bets, the system has no real edge and further build-out is pointless.

## Statistical model (built, not yet in production)

Pipeline in `src/`: `pi_ratings.py` (Constantinou 2013) → `dixon_coles.py` Poisson → `catboost_model.py`. xG from Understat (`src/data/understat.py`, 4,180 EPL matches 2014–2024).

Current status: model RPS 0.2137 vs bookmaker 0.1957 — no edge yet. Phase 7 shipped 2026-05-01: hold-out eval across all 6 leagues + isotonic calibration scaffold (`src/model/holdout.py`, `src/model/reliability.py`, `--calibrate` flag on `model_signals.py`). Calibrated cache lives at `logs/model_signals_calibrated.json`; production scanner still consumes the uncalibrated `logs/model_signals.json`. Decision: **HOLD** — calibration improves aggregate RPS+Brier but EPL+Bundesliga (the production-scanning leagues) degrade. Re-evaluate when ≥50 settled bets have `clv_pct` populated; flip with `mv logs/model_signals_calibrated.json logs/model_signals.json` only if model-filtered CLV is positive. See `docs/MODEL_EVAL_2026-05.md`.

## Implementation status

| Group | Status |
|---|---|
| Phases 0–5.8 (hygiene, devig, risk, CLV, filters, markets, paper portfolio, commission-aware) | ✅ all done |
| Phase 6 (storage migration: SQLite + UUIDs) | superseded by Phase 9 Azure direction |
| Phase 7 (model overhaul: calibration, hold-out eval) | ✅ scaffolding done 2026-05-01; HOLD on flip pending ≥50 CLV bets (`docs/MODEL_EVAL_2026-05.md`) |
| Phase 8 (Betfair API auto-placement) | pending |
| Phase 9a (Pi cron cutover) | ✅ done 2026-05-01 |
| Phase 9b–9d (Azure dev migration A.0–A.7 + A.5.5: SQL DB + KV + 7-table schema + importer + dual-writer + dashboard DB-first reads + Container Apps dashboard with Google OIDC + raw-API blob archive) | ✅ done 2026-05-01 |
| B.0 + B.0.5 + B.0.6 + B.0.7 (book_skill table + LOO consensus + paired Brier + CIs + dual devig) | ✅ done 2026-05-02 |
| B.1 (bias backfill: fav-longshot slope + home/draw bias + empirical-Bayes shrinkage) | ✅ done 2026-05-03 |
| B.3 (cron: WSL ✅ 2026-05-03; Pi pending) | partial |
| B.2 (Brier-vs-close decision gate), B.4* (downstream variants) | pending |
| Audit invariants I-1..I-13 (groups 1–4: P&L arithmetic, dashboard parity, CLV pipeline, book_skill) | ✅ done 2026-05-03; GitHub Actions workflow Mon 08:10 UTC (OIDC + KV, no new secrets); groups 5–6 pending |
| Phase 9 / A.8 (cutover: WSL DB-only, archive CSVs) | ✅ done 2026-05-02 (PRs #27 + #28) |
| Phase 9 / A.9 (decommission CSV path) | ✅ done 2026-05-04 (PR #39) — DB is sole source of truth; scanner / backfill / dashboard refuse to run without `BETS_DB_WRITE=1` |
| S.1–S.4 (DB-only result + CLV backfill) | ✅ done 2026-05-04 (PR #38) — `backfill_clv_from_fdco` reads pending rows from DB, writes `result`/`pnl`/`settled_at`/`pinnacle_close_prob`/`clv_pct` back; `compare_strategies` reads from DB |
| Phase 9 / A.10 (`kaunitz-prod-rg` + Pi onboarding) | pending — runbook in `docs/PI_CATCHUP_2026-05.md` |
| Phase 10 (long-term: syndicate, multi-account) | open |
| Phase 11 (research scanner) | ✅ done |
| R.0–R.3 + R.5.5a/b + R.7–R.9 + R.11 (2026-04 research sprint) | ✅ done |
| R.5 / R.5.5c / R.6 (Mon analysis + walk-forward run + variant graduations) | pending |
| R.10 (AH probability conversion module) | blocked on CLV evidence |

Detail in `docs/PLAN.md`, `docs/PLAN_AZURE_2026-05.md`, `docs/PLAN_RESEARCH_2026-04.md`.

**Variants in shadow** (paper portfolio only, not flipped as defaults): I_power_devig, J_sharp_weighted, K_draw_bias, L_quarter_kelly, M_min_prob_15, N_competitive_only, O_kaunitz_classic, P_max_odds_shopping. Production scanner uses A_production logic.

## Research cycle

Three-stage: automated scanner (`docs/RESEARCH_SCANNER.md`) → quarterly manual deep-read producing `docs/RESEARCH_NOTES_<YYYY-MM>.md` + `docs/PLAN_RESEARCH_<YYYY-MM>.md` → PRs landing variants in `src/betting/strategies.py`. After ≥50 settled bets per variant + walk-forward backtest evidence, graduations flip scanner defaults.

Latest cycle: **2026-04** — see `docs/RESEARCH_NOTES_2026-04.md` (TL;DR at top) and `docs/PLAN_RESEARCH_2026-04.md` (phases R.0 → R.10).

## Research foundation

| Paper | Key finding |
|---|---|
| Dixon & Coles (1997) | Poisson model with ρ low-score correction |
| Constantinou & Fenton (2013) | Pi-ratings: dynamic goal-difference ratings |
| Kaunitz, Zhong & Kreiner (2017) | Consensus strategy: +3.5% ROI, accounts get restricted |
| Shin (1993) | Insider-trader model for de-vigging bookmaker overround |
| Hubáček et al. (2022) | 40-year review: Berrar ratings + XGBoost best |
| Yeung et al. (2023) | CatBoost + pi-ratings competitive with deep learning |
