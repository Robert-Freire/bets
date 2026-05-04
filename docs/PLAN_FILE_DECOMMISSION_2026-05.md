# File Decommission Plan — 2026-05

End-state: the only files in the repo are source code, tests, static config (`config.json`, `.env*`, `requirements.txt`), and static docs. **All system data lives in Azure SQL. All telemetry lives in Application Insights.** The `logs/` directory is deleted.

**Scope: dev (WSL + Azure) only.** Pi follows in A.10. This plan assumes S.1–S.4 (`PLAN_RESULT_BACKFILL_2026-05.md`) and A.9 (scanner CSV cutover) have landed first — they remove the largest CSV writers. Without those, F.2/F.3 below have nothing to migrate.

---

## How to use this doc — bot execution protocol

Same rules as `docs/PLAN_RESEARCH_2026-04.md` §"How to use this doc". Branches: `feat/decom-F-X-<short-slug>`. Commit prefix: `F.X:`. Always link `Refs: docs/PLAN_FILE_DECOMMISSION_2026-05.md#phase-f-X`.

- Each phase is independently shippable; system remains operational after each merge.
- `pytest -q` from repo root must pass.
- Update `CLAUDE.md` to remove every `logs/<file>` reference the phase decommissions.
- Pi-safety contract preserved: each new env var must be DB/AppInsights-gated and no-op when unset.

---

## Phase status tracker

| Phase | Title | Status | Branch |
|---|---|---|---|
| F.1 | App Insights logging shim | pending | — |
| F.2 | State JSONs → DB | pending | — |
| F.3 | Derived caches (model_signals, team_xg) → DB | pending | — |
| F.4 | `data/raw/` → blob with lazy local cache | pending | — |
| F.5 | Delete backups, legacy CSVs, frozen logs | pending | — |
| F.6 | Generated reports off git → dashboard | pending | — |
| F.7 | Final sweep: delete `logs/`, remove fallbacks | pending | — |

---

## Inventory (snapshot at plan time)

```
logs/
  bets.csv                           → DB (A.9)               *out of scope here, prerequisite*
  paper/*.csv                        → DB (A.9)               *prerequisite*
  bets_legacy.csv                    → delete (F.5)
  bets.csv.bak.YYYY-MM-DD            → delete (F.5)
  closing_line.log                   → delete (F.5; frozen)
  closing_lines.csv, drift.csv       → delete (F.5; frozen)
  scan.log                           → App Insights (F.1)
  backfill_clv.log                   → App Insights (F.1)
  book_skill.log                     → App Insights (F.1)
  research.log                       → App Insights (F.1)
  sports.log                         → App Insights (F.1)
  app.log                            → App Insights (F.1)
  ingest_fixtures.log                → App Insights (F.1)
  notified.json                      → DB table notification_seen (F.2)
  bankroll.json                      → DB table bankroll_state (F.2)
  scan_state.json                    → DB table kv_state (F.2)
  research_seen.json                 → DB table research_seen (F.2)
  sports_cache.json                  → DB table kv_state (F.2)
  team_xg.json                       → DB table team_xg (F.3)
  model_signals.json                 → DB table model_signals (F.3)
  model_signals_calibrated.json      → DB table model_signals (F.3, devig_method col)
  model_eval/                        → delete (F.6; regenerate via dashboard)
  paper/                             → delete dir (F.7, after A.9 + F.2/F.3)
  snapshots/                         → keep (transient buffer for blob retry)

data/raw/*.csv                       → blob raw-fdco/ + lazy local cache (F.4)
docs/STRATEGY_COMPARISON.md          → dashboard-rendered (F.6)
docs/RESEARCH_FEED.md                → dashboard-rendered (F.6)
```

---

## Phase F.1 — App Insights logging shim (~2h)

**Goal.** Replace every `print()` and per-script `.log` file with structured logging to Application Insights. Stderr fallback when `APPINSIGHTS_CONNECTION_STRING` is unset (Pi-safety + offline dev).

### Inputs

- `scripts/scan_odds.py`, `scripts/backfill_clv_from_fdco.py`, `scripts/refresh_xg.py`, `scripts/research_scan.py`, `scripts/check_sports.py`, `scripts/ingest_fixtures.py`, `scripts/compute_book_skill.py`, `app.py` — current emitters.
- All files writing under `logs/*.log` (cron `>>` redirects in `crontab -l`).

### Outputs

- New `src/obs/logging.py`: `get_logger(name, run_id=None) -> logging.Logger`.
- App Insights resource provisioned in `kaunitz-dev-rg`; connection string in Key Vault (`appinsights-connection-string`).
- All scripts emit via `get_logger(__name__)`.
- `crontab` redirects `>> logs/*.log` removed (App Insights captures everything).

### Tasks

**T1. Provision App Insights**

- Create workspace-based App Insights resource in `kaunitz-dev-rg` (UK South).
- Stash connection string in `kaunitz-dev-kv-rfk1` as `appinsights-connection-string`.
- Add to `.env.dev`:
  ```
  APPINSIGHTS_KV_VAULT=kaunitz-dev-kv-rfk1
  APPINSIGHTS_KV_SECRET=appinsights-connection-string
  ```
  (Or literal `APPINSIGHTS_CONNECTION_STRING=...` for the no-KV path.)

**T2. Logging shim**

`src/obs/logging.py`:

```python
import logging, os, sys, uuid

_INITIALISED = False

def _appinsights_handler():
    cs = os.environ.get("APPINSIGHTS_CONNECTION_STRING")
    if not cs:
        # Optional: resolve via Key Vault (lazy import — Pi-safety)
        vault, secret = (os.environ.get("APPINSIGHTS_KV_VAULT"),
                         os.environ.get("APPINSIGHTS_KV_SECRET"))
        if vault and secret:
            cs = _kv_fetch(vault, secret)
    if not cs:
        return None
    from azure.monitor.opentelemetry import configure_azure_monitor
    configure_azure_monitor(connection_string=cs)
    return True

def get_logger(name: str, run_id: str | None = None) -> logging.Logger:
    global _INITIALISED
    if not _INITIALISED:
        ok = _appinsights_handler()
        if not ok:
            logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                                format="%(asctime)s %(name)s %(levelname)s %(message)s")
        _INITIALISED = True
    log = logging.getLogger(name)
    if run_id:
        log = logging.LoggerAdapter(log, {"run_id": run_id})
    return log
```

- One shared `run_id` per script invocation (UUID4 generated at `main()` entry; passed via `extra=`).
- Sampling: configure at the SDK level — default 100% in dev, lower if quota issues appear.

**T3. Migrate scripts**

For each script, replace:
- `print("[label] ...")` → `log.info("...", extra={"label": "fdco"})`
- `with open("logs/x.log", "a")` patterns → `log.debug/info/warning/error`
- Tracebacks: `log.exception(msg)` instead of `print(traceback...)`.

Keep human-readable stdout for `--dry-run` and CLI tools; structured logs go to App Insights regardless.

**T4. Crontab cleanup**

`crontab -l | sed 's| >> .*/logs/[a-z_]*\.log 2>&1||g' | crontab -`

App Insights ingests stderr via OpenTelemetry; redirects are no longer needed. Keep `2>&1` only where a cron mailer is desired.

**T5. Add `requirements.txt` entry**

```
azure-monitor-opentelemetry>=1.4
```

### Acceptance

- [ ] Setting `APPINSIGHTS_CONNECTION_STRING` produces queryable traces in App Insights for one full scan + one backfill cycle.
- [ ] Unsetting it leaves scripts running normally with stderr output (offline dev).
- [ ] No script writes to `logs/*.log`. `find logs -name '*.log' -newer <plan-merge-date>` returns nothing after one weekend.
- [ ] `pytest -q` passes; tests use the stderr fallback (no AppInsights env in test runs).

### Reviewer focus

- Confirm `azure-monitor-opentelemetry` is lazy-imported; absence shouldn't break `pytest`.
- Confirm sampling config is conservative (an empty-fixture canary day shouldn't cost £1).
- Confirm `run_id` propagates through subprocess boundaries if any (the scan calls `model_signals` only out-of-band; should be fine).

---

## Phase F.2 — State JSONs → DB (~3h)

**Goal.** Move every operational state file out of `logs/`. Five files; three new tables.

### Schema additions

`src/storage/schema.sql` (and `_sqlite.sql` mirror):

```sql
CREATE TABLE notification_seen (
    bet_key      nvarchar(256) NOT NULL PRIMARY KEY,
    notified_at  datetime2(3)  NOT NULL,
    expires_at   datetime2(3)  NOT NULL          -- 12h after notified_at
);
CREATE INDEX ix_notification_expires ON notification_seen (expires_at);

CREATE TABLE bankroll_state (
    id              int          NOT NULL PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    current         decimal(12,2) NOT NULL,
    high_water      decimal(12,2) NOT NULL,
    updated_at      datetime2(3)  NOT NULL DEFAULT SYSUTCDATETIME()
);

CREATE TABLE kv_state (
    namespace   nvarchar(64)   NOT NULL,    -- 'scan_state' | 'sports_cache'
    key_name    nvarchar(128)  NOT NULL,
    value_json  nvarchar(max)  NOT NULL,
    updated_at  datetime2(3)   NOT NULL DEFAULT SYSUTCDATETIME(),
    PRIMARY KEY (namespace, key_name)
);

CREATE TABLE research_seen (
    source_id   nvarchar(256) NOT NULL PRIMARY KEY,
    seen_at     datetime2(3)  NOT NULL DEFAULT SYSUTCDATETIME()
);
```

### Tasks

**T1. `notified.json` → `notification_seen`**

- New repo methods `is_notified(key) -> bool`, `mark_notified(key, ttl_hours=12)`.
- `scan_odds.py` line 567: replace `_load_notified()` / `_save_notified()` with the repo methods.
- TTL purge: `DELETE FROM notification_seen WHERE expires_at < SYSUTCDATETIME()` runs at the start of each scan (cheap; tiny table).

**T2. `bankroll.json` → `bankroll_state`**

- `src/betting/risk.py` line 15: replace file IO with `repo.read_bankroll() -> (current, high_water)` and `repo.write_bankroll(current, high_water)`.
- One-row table; UPSERT pattern (`MERGE` or `IF EXISTS UPDATE ELSE INSERT`).
- Migration: read current `logs/bankroll.json`, INSERT the row once, then delete the file in F.7.

**T3. `scan_state.json` + `sports_cache.json` → `kv_state`**

- `repo.kv_get(namespace, key) -> dict | None`, `repo.kv_set(namespace, key, value: dict)`.
- `scan_odds.py` line 806 and `check_sports.py` line 42 migrate to the namespace API.
- One-time migration in F.7 cleanup; no separate import script needed (small files, cron rebuilds them within a week).

**T4. `research_seen.json` → `research_seen`**

- `scripts/research_lib/state.py` line 10: `SEEN_PATH` removed; `add_seen(source_id)`/`is_seen(source_id)` route through repo.

**T5. Pi-safety**

Every method falls through to a no-op when `BETS_DB_WRITE` is unset. WSL flips to DB-only; Pi continues with file-based state until A.10. Means file paths must remain readable as a *fallback* until F.7 — but no new writes go to files when DB is enabled.

### Acceptance

- [ ] Two consecutive WSL scans produce zero writes to `logs/*.json`.
- [ ] Notification dedupe still works across scans (verified by re-running a scan within 12h and asserting no duplicate ntfy fire).
- [ ] Drawdown brake triggers correctly when `bankroll_state.current < 0.85 * high_water`.
- [ ] Pi (no DB env) keeps writing to JSON files; this is verified manually by inspecting one Pi run.
- [ ] `pytest -q` passes.

### Reviewer focus

- TTL purge is cheap *only* if `ix_notification_expires` exists. Confirm.
- `bankroll_state` row constraint (`id = 1`) prevents accidental multi-row state.
- Confirm `kv_state.value_json` round-trips dicts without lossy serialisation (datetime strings, etc.).

---

## Phase F.3 — Derived caches → DB (~2h)

**Goal.** Move computed-artifact caches into DB tables. Queryable, joinable, no JSON files to ship.

### Schema additions

```sql
CREATE TABLE team_xg (
    team        nvarchar(128) NOT NULL,
    avg_xg      decimal(8,4)  NOT NULL,
    refreshed_at datetime2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
    PRIMARY KEY (team)
);

CREATE TABLE team_xg_meta (
    id              int           NOT NULL PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    q25_threshold   decimal(8,4)  NOT NULL,
    refreshed_at    datetime2(3)  NOT NULL DEFAULT SYSUTCDATETIME()
);

CREATE TABLE model_signals (
    fixture_id   uniqueidentifier NOT NULL REFERENCES fixtures(id),
    side         nvarchar(32)     NOT NULL,
    devig_method nvarchar(16)     NOT NULL,    -- 'uncalibrated' | 'isotonic'
    signal       nvarchar(8)      NOT NULL,    -- 'agree' | 'disagree' | 'neutral'
    model_prob   decimal(10,8)    NOT NULL,
    refreshed_at datetime2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    PRIMARY KEY (fixture_id, side, devig_method)
);
```

### Tasks

**T1. `team_xg.json` → `team_xg` + `team_xg_meta`**

- `scripts/refresh_xg.py`: write per-team rows + meta in one transaction. Truncate table at the start of each refresh (weekly cron).
- `src/betting/strategies.py` line 14: read both tables on first use; cache in-process.

**T2. `model_signals*.json` → `model_signals`**

- `scripts/model_signals.py`: write rows keyed on `(fixture_id, side, devig_method)`. Calibrated and uncalibrated coexist.
- `scripts/scan_odds.py` line 85: SELECT the appropriate `devig_method` (default `uncalibrated`; flip via `MODEL_SIGNAL_DEVIG=isotonic` env var).
- `scripts/diff_model_signals.py`: rewrite as a SQL diff (`SELECT ... WHERE devig_method = ...` × 2). Drop `BASE`/`CALIB` constants.

**T3. Migration: deprecate the JSON output paths**

Once F.3 lands and the next scheduled refresh has run, the JSON files are stale. F.7 deletes them.

### Acceptance

- [ ] `scan_odds.py` produces the same model-agreement signal as before, sourced from DB.
- [ ] `K_draw_bias` continues to fire (depends on `team_xg`); manual spot-check on one EPL fixture.
- [ ] Calibrated/uncalibrated flip works via env var without code change.
- [ ] `pytest -q` passes.

### Reviewer focus

- Confirm `model_signals` row count after a fresh refresh ≈ Σ(fixtures × sides) for production-scanning leagues.
- Confirm FK to `fixtures(id)` doesn't block (xG/signals are computed *before* the fixture is flagged for a bet, but `ingest_fixtures` cron runs Mon 02:00 — model_signals refresh must run after that).

---

## Phase F.4 — `data/raw/` → blob with lazy local cache (~2h)

**Goal.** FDCO season CSVs live in Azure Blob (`raw-fdco/<season>/<league>.csv`). Local `data/raw/` becomes a gitignored cache populated lazily on first read.

### Tasks

**T1. One-shot upload**

`scripts/migrate_fdco_to_blob.py`: walks `data/raw/*.csv`, uploads each to `raw-fdco/<season>/<league>.csv` in the existing `kaunitzdevstrfk1` storage account. Idempotent (skip if blob exists with matching ETag).

**T2. Read path with cache**

`backfill_clv_from_fdco.py` `_refresh_csv` rewrite:

```
1. If local cache exists and < _STALE_DAYS old → use it.
2. Else: try blob. If blob exists → download to local cache.
3. Else: HTTP fetch from football-data.co.uk → upload to blob → write local cache.
```

The HTTP path becomes the cold-start fallback only. Once blob has the season, every dev box hits blob.

**T3. Gitignore**

```
echo "data/raw/" >> .gitignore
git rm -r --cached data/raw/
```

**T4. CLAUDE.md edit**

Update the `data/raw/` line in the file tree to note "(local cache; canonical copy in Azure Blob `raw-fdco/`)".

### Acceptance

- [ ] First run after delete-and-rerun on a clean checkout: local cache is empty, script populates from blob, FDCO HTTP path not hit.
- [ ] Second run: zero blob calls (local cache hit).
- [ ] `git status` is clean after a backfill (no `data/raw/` tracking).

### Reviewer focus

- Confirm container `raw-fdco` has the same lifecycle policy as `raw-api-snapshots` (cool at 30d, no auto-delete).
- HTTP fallback should still write to blob — otherwise blob diverges from upstream.

---

## Phase F.5 — Delete backups, legacy CSVs, frozen logs (~30min)

**Goal.** Pure deletion. No code changes.

### Tasks

**T1. Delete files**

```
rm logs/bets_legacy.csv
rm logs/bets.csv.bak.*
rm logs/closing_line.log
rm logs/closing_lines.csv logs/drift.csv
rm logs/sports.log logs/research.log logs/book_skill.log logs/scan.log \
   logs/backfill_clv.log logs/ingest_fixtures.log logs/app.log
   # …only after F.1 lands and one full week has passed.
```

**T2. Remove the daily snapshot cron**

Current entry: `0 3 * * * — daily 3am snapshot bets.csv.bak.<date>`. Delete it; DB is canonical.

**T3. CLAUDE.md edits**

Remove every reference to the deleted files. Update the cron table.

### Acceptance

- [ ] `ls logs/` after F.5 is significantly smaller (only paper/, snapshots/, the JSON state files pending F.2/F.3 cleanup).
- [ ] No script breaks (verified by running one full scan + one backfill).

### Reviewer focus

- `closing_line.py` is paused but kept for revert per CLAUDE.md. Confirm we're deleting only the *log files*, not the script. Same for `closing_lines.csv` / `drift.csv` (frozen *outputs*; the script is dormant).

---

## Phase F.6 — Generated reports off git → dashboard (~3h)

**Goal.** `STRATEGY_COMPARISON.md` and `RESEARCH_FEED.md` are no longer committed artifacts. They're rendered live from DB by the dashboard.

### Tasks

**T1. Dashboard route: `/strategies`**

`app.py` adds a route that runs the same DB-backed aggregation as `compare_strategies.py` (S.3) and renders Markdown → HTML in the page.

**T2. Dashboard route: `/research`**

Same pattern, sourcing from a new `research_findings` table (extension of F.2's `research_seen`, or separate — TBD when F.6 lands).

**T3. Strip generated docs from git**

```
git rm docs/STRATEGY_COMPARISON.md docs/RESEARCH_FEED.md
echo "docs/STRATEGY_COMPARISON.md" >> .gitignore   # in case scripts still write locally
echo "docs/RESEARCH_FEED.md" >> .gitignore
rm -rf logs/model_eval/
```

**T4. Update CLAUDE.md**

Remove docs/STRATEGY_COMPARISON.md from the Key Files block; add `/strategies` and `/research` to the Dashboard section.

### Acceptance

- [ ] Local + Azure dashboards render both views from DB.
- [ ] No `compare_strategies.py` invocation writes to `docs/`.
- [ ] `git status` clean after running both scripts.

### Reviewer focus

- Confirm Azure dashboard auth (Google OIDC + allowlist) covers the new routes — they're read-only views of paper data, but the allowlist still applies.

---

## Phase F.7 — Final sweep: delete `logs/`, remove fallbacks (~1h)

**Goal.** `logs/` directory ceases to exist. Every code path that *could* fall back to file IO is removed.

Pre-conditions: F.1–F.6 merged, A.9 merged, one weekend of clean operation.

### Tasks

**T1. Delete the directory**

```
rm -rf logs/
```

Add `logs/` to `.gitignore` (defensive — prevents accidental recreation by any straggling code path).

**T2. Remove file-IO fallbacks from F.2/F.3 code**

Methods like `read_bankroll()` no longer accept a "DB unavailable → read JSON" fallback. WSL is DB-only; tests use the in-memory SQLite fixture; Pi is on its own track (A.10).

Specifically:
- `src/betting/risk.py`: drop `_BANKROLL_STATE` Path constant + JSON load/save.
- `scripts/scan_odds.py`: drop `_NOTIFIED_PATH`, `_SCAN_STATE_PATH`, `_SIGNALS_PATH` constants and their loaders.
- `src/betting/strategies.py`: drop `_XG_FILE`.
- `scripts/research_lib/state.py`: drop `SEEN_PATH`.
- `scripts/check_sports.py`: drop `CACHE_FILE`.
- `scripts/model_signals.py`, `scripts/refresh_xg.py`: drop their JSON output writers entirely.

**T3. Document the new contract in CLAUDE.md**

Add a top-level "Storage layout" subsection:

> All system data and operational state lives in Azure SQL (`kaunitz` DB). All telemetry lives in Application Insights. The repository contains only source code, tests, static config (`config.json`, `.env*`, `requirements.txt`), and static docs. There is no `logs/` directory.

### Acceptance

- [ ] `find . -path ./.venv -prune -o -name 'logs' -print` returns nothing.
- [ ] `grep -rn 'logs/' --include='*.py' src/ scripts/` returns nothing (or only test fixtures).
- [ ] `pytest -q` passes.
- [ ] One full Tue/Sat scan cycle on WSL: zero file writes outside `data/raw/` (the FDCO cache).

### Reviewer focus

- The Pi cron path. F.7 is dev-only; Pi still uses files. Either Pi is gated off these scripts (preferred) or the Pi keeps a frozen pre-F.2 commit until A.10. Decide before merging F.7.
- `data/raw/` still exists as the FDCO local cache. That's the only on-disk data store remaining; documented in F.4.

---

## What this plan deliberately does NOT do

- **Doesn't migrate Pi.** Pi keeps writing files until A.10. F.2/F.3 keep file-IO fallbacks alive *only* until F.7; F.7 only flips after Pi is on DB.
- **Doesn't replace Application Insights with a self-hosted alternative.** The cost (~£3–5/mo dev) is below the threshold to justify Loki/Grafana.
- **Doesn't change `config.json` / `.env*` location.** Configuration is the only category of file that stays — that's the whole point.
- **Doesn't touch CSV ingestion sources.** External reference data (FDCO, Understat) is still file-shaped; it lives in blob (F.4) but the *format* is unchanged. This isn't our state, so the principle doesn't apply.
