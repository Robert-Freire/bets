# Azure Migration Plan — 2026-05

Phased migration from local CSV storage to a SQL-Server-Express-backed Flask dashboard hosted on Azure. Supersedes the earlier `docs/PI_AZURE_SETUP.md` (deleted on 2026-05-01).

**Driving question.** Pi cron is in production (Phase 9a ✅ done 2026-05-01). The two open questions: (1) where does data live now that the system is no longer a single-host setup? (2) how does the user view/settle bets from a phone, anywhere? This plan answers both with one consistent stack: Azure SQL DB + Azure App Service.

**Bot execution protocol.** Follow the same "Picking up a phase / During implementation / Commit conventions / PR conventions / Verifier bot protocol / Failure handling / Branch hygiene" rules from `docs/PLAN_RESEARCH_2026-04.md` §"How to use this doc". Branches: `azure-A-X-<short-slug>`. Commit prefix: `A.X:`. Always link `Refs: docs/PLAN_AZURE_2026-05.md#phase-A-X`.

---

## Scope: dev-first migration; Pi UNTOUCHED throughout

> ⚠️ **Decision (2026-05-01):** This plan migrates **only the WSL/dev side** to Azure. The **Raspberry Pi production cron is NOT modified by any phase in this document.** Pi keeps writing to its own local `~/projects/bets/logs/*.csv` exactly as it does today. This means: if any Azure phase breaks anything, the worst-case blast radius is dev/test data on WSL — production data on Pi is fully isolated.

**What's IN scope (WSL/dev only):**
- Provision Azure resources (SQL DB, App Service, Key Vault, RG)
- Migrate WSL `logs/*.csv` data into Azure SQL
- WSL `scan_odds.py` writes both CSV and Azure SQL (dual-write, env-flag gated)
- Dashboard (`app.py`) reads from Azure SQL — sees **only WSL data** during this plan
- Public Azure URL with Google OIDC auth (allowlist: `robert.freire@gmail.com`)

**What's OUT of scope (deferred to future plan):**
- Pi `scan_odds.py` writing to Azure SQL — Pi stays on CSVs.
- Pi data appearing in the Azure dashboard — Pi data is not in the DB during this plan.
- Decommissioning Pi's local CSVs.
- Unifying WSL + Pi data — they remain two parallel streams (per `project_dev_prod_split` memory).

**Implications for "stop-after-here" thinking:**
- After Phase A.7 lands, you have a fully working public Azure dashboard backed by Azure SQL — but it shows only the WSL test stream's data. The Pi production stream is still canonical and visible only via Pi-side CSVs / direct ssh.
- Pi onboarding (Phase A.10 below) is a **separate future sprint** — only attempt after this plan has soaked for ≥1 week.

---

## Two stacks: `kaunitz-dev-rg` (this plan) vs `kaunitz-prod-rg` (A.10)

**Decision (2026-05-01):** dev and prod live in **separate resource groups**, mirroring the existing WSL-dev / Pi-prod split (`project_dev_prod_split` memory). Each stack has its own SQL server, DB, Key Vault, App Service plan, and web app. The two stacks never share a DB.

| Aspect | `kaunitz-dev-rg` (A.0–A.9, now) | `kaunitz-prod-rg` (A.10, deferred) |
|---|---|---|
| Writer | WSL cron (dev API key) | Pi cron (prod API key) |
| Always-on? | **No — freely stoppable to save credits.** | **Yes — soak target, never stopped.** |
| SQL DB compute | Serverless `GP_S_Gen5_2`, `--auto-pause-delay 60` | Free offer (`--use-free-limit`) if available, else serverless with longer pause delay |
| App Service | F1 free; `az webapp stop` when not in use | F1 free initially; B1 if cold-starts hurt |
| Free SQL offer (one per subscription) | Goes to **prod**, not dev | ✅ Reserved for here |
| Dashboard URL identity | `kaunitz-dev-dashboard-<rand>.azurewebsites.net` | `kaunitz-prod-dashboard-<rand>.azurewebsites.net` |
| Blast radius if broken | Dev test data only; prod and Pi cron unaffected | Real CLV stream; mirror dev's stability before promoting |

**Why two RGs and not one shared DB with a `source` column?**
- Stronger isolation: a botched dev migration cannot corrupt prod data.
- Mirrors the architecturally-decided dev/prod split already in place at the cron + API key level.
- One-click teardown of either env via `az group delete`.
- Ops cost (~£0–£5/mo extra for dev) is well within the £150/mo Reply VSE credit.

---

## Architecture target

```
┌──────────────────────────  THIS PLAN (A.0–A.9)  ──────────────────────────┐

WSL (home network — dev cron, dev API key)
  └── scan_odds.py, closing_line.py (cron)
        ├── writes to logs/*.csv (existing path, always on)
        └── writes to Azure SQL via pyodbc (NEW, gated by BETS_DB_WRITE=1 env flag)
                                                    │
                                                    ▼
                                    kaunitz-dev-rg (UK South)
                                    ├── kaunitz-dev-sql-uksouth-<rand>
                                    │     └── DB: kaunitz (serverless, auto-pause 60min)
                                    │           schema: bets, fixtures, books,
                                    │                   closing_lines, drift,
                                    │                   paper_bets, strategies
                                    ├── kaunitz-dev-kv-<rand> (secrets)
                                    └── kaunitz-dev-plan / kaunitz-dev-dashboard-<rand>
                                          (F1, stoppable; Easy Auth Google OIDC,
                                           allowlist robert.freire@gmail.com)
                                          Shows: WSL-source data only.

└────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────  FUTURE: A.10 prod stack  ──────────────────────┐

Raspberry Pi (home network — UNCHANGED in A.0–A.9)
  └── scan_odds.py, closing_line.py, refresh_xg.py, research_scan.py (cron)
        └── writes to ~/projects/bets/logs/*.csv ONLY (until A.10)
                                                    │
                                                    ▼ (A.10)
                                    kaunitz-prod-rg (UK South — NOT created in this plan)
                                    ├── kaunitz-prod-sql-uksouth-<rand>  (free offer if avail)
                                    │     └── DB: kaunitz (always-on or long auto-pause)
                                    ├── kaunitz-prod-kv-<rand>
                                    └── kaunitz-prod-plan / kaunitz-prod-dashboard-<rand>

└────────────────────────────────────────────────────────────────────────────┘
```

A.0–A.9 stand up only the dev stack. WSL is the sole writer; Pi continues writing local CSVs, untouched. A.10 stands up the prod stack and onboards Pi to it.

---

## Architecture decisions (fixed up-front to avoid bikeshedding mid-execution)

| Question | Decision | Why |
|---|---|---|
| Dev/prod split | **Two separate resource groups** (`kaunitz-dev-rg` now, `kaunitz-prod-rg` in A.10). No shared DB. Each stack has its own SQL server, KV, App Service plan, web app. | Mirrors existing WSL-dev / Pi-prod split; isolates dev mistakes from prod data; supports independent stop/start; one-click teardown per env. |
| Stop/start ergonomics | **Dev = stoppable** (`az webapp stop` for app; serverless DB auto-pauses after 60 min idle). **Prod = always-on** during match windows, never explicitly stopped. | User wants to stop dev cheaply between work sessions; prod runs the canonical CLV pipeline so any stop shows up as a closing-line gap. |
| Where does the DB live? | **Azure SQL Database** in the per-env RG. **Dev:** serverless `GP_S_Gen5_2` with `--auto-pause-delay 60` (cost £0 while paused, ~£5/mo if active 24/7). **Prod (A.10):** the once-per-subscription free offer (`--use-free-limit`) if still available — else Basic (~£5/mo) or longer-pause serverless. Fallback: paid Basic ~£5/mo. | Managed, auto-backups, no patching. Self-hosted SQL Express saves nothing and adds ops burden. The free offer (one per subscription) is reserved for prod because prod must be always-on. |
| What flavour of SQL? | **Azure SQL DB** (T-SQL, MSSQL-flavoured) — *not* SQL Server Express on a VM | Same engine family as the original "SQL Server Express" intent; user gets the cloud benefits. Phase-6 doc copy still says "SQL Server Express" for continuity. |
| Where does the web app live? | **Azure App Service F1 (free)**, separate plan per env. Dev plan stoppable via `az webapp stop`. | Always-on Linux Python runtime; deploy via `az webapp up`. If F1 cold starts hurt UX, escalate to B1 (~£10/mo) on prod first; dev can stay F1. |
| Auth on public dashboard? | **App Service Easy Auth with Google OIDC** (one allowed email: `robert.freire@gmail.com`). Decoupled from the Reply VSE subscription on purpose — the subscription owns the Azure resources, but the dashboard identity is the user's personal Google account. | One-click in portal; no auth code in app.py. Falls back to HTTP Basic Auth (1 LOC) if Easy Auth setup blocks. |
| Pi → Azure SQL transport | **N/A in this plan** — Pi is not touched. Future Phase A.10 will add this (TCP 1433 outbound, TLS, SQL auth from Key Vault). | Avoid touching production-critical Pi cron during a multi-phase Azure stand-up. Onboard Pi only after dev side has soaked for ≥1 week. |
| WSL → Azure SQL transport | TCP 1433 outbound, TLS required (Azure default), SQL auth (username/password from Azure Key Vault → env var on WSL) | TLS+SQL auth is the simplest path. WSL is on home network so firewall rule allows the WSL public IP. |
| Pi onboarding scope | **Deferred to Phase A.10** (separate future sprint, post-soak). Pi keeps writing CSVs only during A.0–A.9. | Pi is production; protecting it from Azure-related disruption is the entire point of the dev-first scope above. |
| Region | **UK South** | Lowest-latency UK region for the Pi; matches the user's location and bookmaker fixture timezones. |
| Migration style | **Dual-write transition on WSL only** (A.4–A.8): WSL scanner writes both CSV and DB; dashboard reads DB-first with CSV fallback. Cut over once 1 week of clean DB-only operation on the WSL side. Pi is NOT part of this transition — Pi stays CSV-only. | Lets us roll back cheaply if DB writes fail. Keeps Pi production isolated from any Azure-side breakage. |
| Schema primary key | **`uniqueidentifier` (UUID)** for `bets.id`, `paper_bets.id` | Closes the historical "Phase 6: SQLite + UUIDs" intent without needing app-side coordination of integer sequences. |
| Historical CSV data | **WSL CSVs only** backfilled in A.3 (one-shot importer); WSL CSVs archived to `logs/csv-archive/` after A.8 cutover; deleted in A.9. **Pi CSVs are NOT touched by any phase in this plan.** | Preserves the WSL test-stream data; Pi production data stays in its own CSVs untouched. |

---

## Phase status tracker

All A.0–A.9 phases operate on `kaunitz-dev-rg`. A.10 is the only phase that creates `kaunitz-prod-rg` and touches Pi.

| Phase | Title | RG | Touches Pi? | Status | Depends on |
|---|---|---|---|---|---|
| A.0 | Provision Azure account + dev resource group | dev | no | ✅ Done 2026-05-01 | — |
| A.1 | Stand up dev Azure SQL Database (serverless, auto-pause) | dev | no | ✅ Done 2026-05-01 | A.0 |
| A.2 | Schema DDL + idempotent migrations runner | dev | no | pending | A.1 |
| A.3 | CSV → DB importer — **WSL CSVs only** | dev | no | pending | A.2 |
| A.4 | Storage layer + dual-write in scanner — **WSL only**, env-flag gated so Pi `git pull` is safe | dev | no (code is gated; Pi never sets the flag) | pending | A.2 |
| A.5 | Dashboard reads DB-first with CSV fallback — **shows WSL data only** | dev | no | pending | A.2, A.4 |
| A.6 | Provision dev App Service + deploy `app.py` | dev | no | pending | A.5 |
| A.7 | Easy Auth (Google OIDC) on dev dashboard | dev | no | pending | A.6 |
| A.8 | Cutover: WSL DB-only, archive WSL CSVs | dev | no | pending | A.7 + 1 week stable A.4/A.5 |
| A.9 | Decommission WSL CSV path entirely | dev | no | pending | A.8 + 1 week stable |
| **A.10** | **Stand up `kaunitz-prod-rg` + onboard Pi** (future sprint — separate plan doc) | **prod (new)** | **yes** | **deferred** | A.9 + ≥1 week soak |

---

## Cost estimate

Two stacks costed separately. Reply VSE provides ~£150/month MSDN credit (recurring), so even worst-case is well-covered.

**During A.0–A.9 (dev stack only):**

| Service | Cost/month |
|---|---|
| Azure SQL DB — dev (serverless `GP_S_Gen5_2`, 60-min auto-pause) | £0–£5 (depending on weekend usage; £0 while paused) |
| App Service F1 — dev (`az webapp stop` between sessions) | £0 |
| Key Vault — dev (first 10k ops free) | £0 |
| Outbound bandwidth | £0 |
| **Dev subtotal during this plan** | **£0–£5/mo** |

**After A.10 (dev + prod stacks both running):**

| Service | Cost/month |
|---|---|
| Dev (as above) | £0–£5 |
| Azure SQL DB — prod (`--use-free-limit` if still available; else Basic) | £0 (free offer) or ~£5 (Basic) |
| App Service F1 — prod (always-on) | £0 |
| Key Vault — prod | £0 |
| **Total post-A.10** | **£0–£10/mo (most likely £0)** |
| Fallback if F1 cold-starts hurt prod UX | + ~£10/mo for B1 on prod plan |

The £150/month Reply VSE credit covers any escalation with ~10–15× headroom, so cost is not a binding constraint on architectural choices.

---

## Dev stop/start operations (cost control)

The dev stack is designed to be cheaply stoppable. Once A.1+A.6 land, use these commands.

**Stop dev (between sessions; saves ~£0.20/day at dev usage levels):**
```bash
# App Service: explicit stop (still costs £0 on F1; this only stops compute)
az webapp stop -g kaunitz-dev-rg -n kaunitz-dev-dashboard-<rand>
# SQL DB: auto-pauses after 60 min idle; force pause if desired:
az sql db pause -g kaunitz-dev-rg -s kaunitz-dev-sql-uksouth-<rand> -n kaunitz
```

**Start dev:**
```bash
az webapp start -g kaunitz-dev-rg -n kaunitz-dev-dashboard-<rand>
# SQL DB: any query auto-resumes; or explicitly:
az sql db resume -g kaunitz-dev-rg -s kaunitz-dev-sql-uksouth-<rand> -n kaunitz
```

**Nuclear option** (preserves nothing; recreate via the phase commands):
```bash
az group delete -n kaunitz-dev-rg --yes  # tear down entire dev stack
```

Prod (`kaunitz-prod-rg`, A.10) is **never** stopped during normal ops — closing-line scans run every 5 minutes during match windows and a paused DB introduces drift gaps.

---

## Phase A.0 — Provision dev resource group  ✅ Done 2026-05-01

**Goal.** Single Azure resource group `kaunitz-dev-rg` in UK South, under the Reply Visual Studio Enterprise Subscription (Azure account `r.freire@reply.eu`, tenant `reply.onmicrosoft.com`, subscription id `bab24bda-5316-4e9e-9565-056e5e57e64f`, ~£150/month MSDN credit). All A.0–A.9 resources land here for one-click teardown. The matching `kaunitz-prod-rg` is NOT created in this phase — it lands in A.10.

**Identity boundary** — *only the Azure subscription/admin uses the Reply identity.* Application-level identity (dashboard auth allowlist, OAuth client owner, contact email on alerts) is the user's personal `robert.freire@gmail.com`. Phases below should not conflate the two.

**Tasks.**
1. Confirm `az account show` reports the Reply VSE subscription as default. (Done 2026-05-01.)
2. `az group create -n kaunitz-dev-rg -l uksouth --tags env=dev project=kaunitz owner=rfreire`. (Done 2026-05-01.)

**Acceptance.**
- [x] `az group show -n kaunitz-dev-rg` returns the group with `provisioningState: Succeeded`.
- [x] Resource group visible at portal.azure.com under the user's subscription.
- [x] Tags `env=dev`, `project=kaunitz`, `owner=rfreire` set so future cost queries can filter by env.

**Reviewer focus.** None (provisioning only).

**Verification commands.**
```bash
az account show --query "{name:name, user:user.name}" -o table  # confirm logged-in user
az group list -o table | grep kaunitz-dev-rg                            # confirm group exists
az group show -n kaunitz-dev-rg --query "{name:name,location:location,tags:tags}" -o json
```

---

## Phase A.1 — Stand up dev Azure SQL Database (serverless, auto-pause)  ✅ Done 2026-05-01

**Provisioned values (suffix `rfk1`):**
- SQL server: `kaunitz-dev-sql-uksouth-rfk1.database.windows.net`
- DB: `kaunitz` (`GP_S_Gen5_2`, autoPauseDelay 60, maxSize 32 GB, status Online)
- Key Vault: `kaunitz-dev-kv-rfk1` (vault URI `https://kaunitz-dev-kv-rfk1.vault.azure.net/`); secret `sql-admin-password` set
- SQL admin: `kaunitzadmin`; password in Key Vault + Bitwarden entry `Azure SQL — kaunitz dev DB`
- Firewall: `AllowAzureServices` (0.0.0.0) + `AllowWSL` (80.1.254.176). No Pi rule.

**Goal.** A working Azure SQL DB instance in `kaunitz-dev-rg`, reachable from **WSL only** over TCP 1433+TLS, on serverless `GP_S_Gen5_2` with 60-min auto-pause for cost control. Pi is NOT given access in this phase — that happens in A.10 against `kaunitz-prod-rg`'s SQL server.

**Tasks.**
1. `az sql server create -g kaunitz-dev-rg -n kaunitz-dev-sql-uksouth-<random> -l uksouth --admin-user kaunitzadmin --admin-password <generated, store in Bitwarden>`.
2. `az sql db create -g kaunitz-dev-rg -s kaunitz-dev-sql-uksouth-<random> -n kaunitz --tier GeneralPurpose --family Gen5 --capacity 2 --compute-model Serverless --auto-pause-delay 60 --backup-storage-redundancy Local`. Do **not** use `--use-free-limit` — that one-per-subscription free offer is reserved for `kaunitz-prod-rg` (Phase A.10).
3. Firewall: `az sql server firewall-rule create` to allow (a) WSL's public IP, (b) Azure services (`0.0.0.0` rule with name `AllowAzureServices`). **Pi's IP is NOT added in this phase** (Pi has no business connecting to dev DB).
4. Verify ODBC Driver 18 for SQL Server is installed on WSL (`python3 -c "import pyodbc; print(pyodbc.drivers())"` includes `ODBC Driver 18 for SQL Server`). If missing, install Microsoft repo + `msodbcsql18` per the [official docs](https://learn.microsoft.com/sql/connect/odbc/linux-mac/installing-the-microsoft-odbc-driver-for-sql-server).
5. Create `kaunitz-dev-rg`-scoped Azure Key Vault `kaunitz-dev-kv-<random>`; store SQL admin password as secret `sql-admin-password`. (Phase A.4 will pull from Key Vault into WSL env at scan-time.)

**Acceptance.**
- [x] From WSL: `pyodbc` SELECT 1 returns `(1,)` against `kaunitz-dev-sql-uksouth-rfk1`.
- [x] Key Vault secret `sql-admin-password` exists and is fetchable via `az keyvault secret show` (verified by KV-roundtrip vs local copy).
- [ ] Bitwarden has a new entry `Azure SQL — kaunitz dev DB` (user action — password displayed once during A.1).
- [x] Firewall rule list = `AllowAzureServices` + `AllowWSL` only; no Pi IP rule.

**Reviewer focus.**
- Server name must include random suffix (DNS-globally-unique requirement).
- Firewall rules — confirm WSL IP rule is present and not overly permissive (no `0.0.0.0–255.255.255.255` for client IPs). Confirm no Pi IP rule.
- Confirm serverless/auto-pause: `az sql db show ... --query "currentSku"` shows `GP_S_Gen5` and `--query "autoPauseDelay"` shows `60`.

**Verification commands.**
```bash
az sql db show -g kaunitz-dev-rg -s kaunitz-dev-sql-uksouth-<random> -n kaunitz --query "{name:name, status:status, sku:currentSku, autoPauseDelay:autoPauseDelay}" -o json
az sql server firewall-rule list -g kaunitz-dev-rg -s kaunitz-dev-sql-uksouth-<random> -o table
python3 -c "import pyodbc; print(pyodbc.drivers())"  # expect msodbcsql18 in list (on WSL)
```

---

## Phase A.2 — Schema DDL + idempotent migrations runner

**Goal.** A version-controlled SQL schema that can be applied (and re-applied) safely.

**Tasks.**
1. Create `src/storage/schema.sql` with `IF NOT EXISTS` patterns (or `IF OBJECT_ID(...) IS NULL`) covering:
   - `fixtures` (id uuid PK, sport_key, league, home, away, kickoff_utc, created_at)
   - `books` (id int PK, name, region, commission_rate)
   - `bets` (id uuid PK, fixture_id FK, side, market, book_id FK, odds, stake, edge_pct, consensus_prob, dispersion, outlier_z, model_signal, devig_method, weight_scheme, status, settled_at, won, pnl, created_at)
   - `closing_lines` (bet_id FK PK, pinnacle_close_prob, captured_at, clv_pct)
   - `drift` (bet_id FK, t_minus_minutes, pinnacle_prob, captured_at; PK = (bet_id, t_minus_minutes))
   - `strategies` (id int PK, name, description, active)
   - `paper_bets` (id uuid PK, strategy_id FK, fixture_id FK, ... same fields as bets)
2. Create `src/storage/migrate.py` — reads `schema.sql`, executes via pyodbc; logs "no changes" if idempotent rerun.
3. Add `tests/test_schema.py` — uses local SQLite (in-memory) as a smoke test; full MSSQL run requires `AZURE_SQL_TEST_DSN` env var.

**Acceptance.**
- [ ] `python3 src/storage/migrate.py` against an empty Azure SQL DB creates all tables; second run is a no-op.
- [ ] `pytest tests/test_schema.py` passes (in-memory SQLite path).
- [ ] All FK constraints present and indices on `(kickoff_utc, sport_key)`, `(strategy_id, status)`.

**Reviewer focus.**
- Idempotency: re-running `migrate.py` must not error or duplicate rows.
- Datetime handling: all timestamps stored as UTC-aware (`datetime2(3)` in MSSQL).
- UUID generation: app-side (`uuid.uuid4()`) not DB-side, to keep import scripts portable.

**Verification commands.**
```bash
python3 src/storage/migrate.py --dsn "$AZURE_SQL_DSN"
python3 src/storage/migrate.py --dsn "$AZURE_SQL_DSN"   # second run, expect "no changes"
pytest -q tests/test_schema.py
```

---

## Phase A.3 — CSV → DB importer (one-shot, idempotent) — WSL CSVs only

**Goal.** Backfill historical data from **WSL's** `logs/*.csv` and `logs/paper/*.csv` into the new DB. **Pi's CSVs are NOT imported** (Pi onboarding is Phase A.10, deferred).

**Tasks.**
1. Create `scripts/migrate_csv_to_db.py` reading:
   - `logs/bets.csv` → `bets` (and infer `fixtures` rows by unique kickoff/home/away)
   - `logs/closing_lines.csv` → `closing_lines`
   - `logs/drift.csv` → `drift`
   - `logs/paper/<variant>.csv` → `paper_bets` (one row per CSV row, FK to `strategies` row created from variant name)
2. Use natural-key upsert (`MERGE` in T-SQL or insert-with-not-exists) so re-running is a no-op.
3. Generate UUIDs deterministically from `(kickoff, home, away, side, book)` so two imports produce the same IDs (avoid duplicate rows).
4. Print row counts at end: `bets: X imported, 0 skipped (already present)`.

**Acceptance.**
- [ ] `wc -l logs/bets.csv` ≈ `SELECT COUNT(*) FROM bets` (off-by-1 for header).
- [ ] Per-variant: `wc -l logs/paper/<v>.csv` ≈ `SELECT COUNT(*) FROM paper_bets WHERE strategy_id = (SELECT id FROM strategies WHERE name = '<v>')`.
- [ ] Re-running the importer produces zero new rows.

**Reviewer focus.**
- Deterministic UUIDs: same input row → same UUID across runs (use `uuid.uuid5(NAMESPACE, key)`).
- NULL handling for late-added columns (`devig_method`, `weight_scheme`, `clv_pct`) on older CSV rows.
- Memory: stream CSV rows, don't load all into a DataFrame (some CSVs are large).

**Verification commands.**
```bash
export $(cat .env.dev) && python3 scripts/migrate_csv_to_db.py --dsn "$AZURE_SQL_DSN"
python3 scripts/migrate_csv_to_db.py --dsn "$AZURE_SQL_DSN"  # re-run, expect 0 new
python3 -c "import pyodbc; c = pyodbc.connect('$AZURE_SQL_DSN'); print('bets:', c.execute('SELECT COUNT(*) FROM bets').fetchone()[0])"
```

---

## Phase A.4 — Storage layer + dual-write in scanner — WSL only

**Goal.** **WSL's** `scan_odds.py` writes to both CSV (existing) and Azure SQL (new) on every scan. Env flag `BETS_DB_WRITE=1` gates the new path. **Pi's `scan_odds.py` is NOT modified beyond what `git pull` brings in — Pi never sets `BETS_DB_WRITE`, so the dual-write code path stays dormant on Pi.**

**Pi safety contract.** The `BetRepo` module must be import-safe even when `pyodbc` / `AZURE_SQL_DSN` / `BETS_DB_WRITE` are absent. Pi's `git pull` brings in the new code; on next cron fire, Pi runs the scanner with no env flag → the DB write path is short-circuited → Pi behavior is byte-identical to pre-A.4. Verify this in tests.

**Tasks.**
1. New module `src/storage/repo.py` with `BetRepo` class: `add_bet(...)`, `add_paper_bet(...)`, `add_closing_line(...)`, `add_drift_snapshot(...)`. CSV writer always-on; DB writer activated only when `BETS_DB_WRITE=1` AND `AZURE_SQL_DSN` is set.
2. Wire into `scripts/scan_odds.py` and `scripts/closing_line.py` — both call `repo.add_*` instead of writing CSVs directly.
3. **WSL `.env.dev`** adds `BETS_DB_WRITE=1` + `AZURE_SQL_DSN`. Pi `.env` does NOT — Pi stays CSV-only.
4. Connection pulled from Azure Key Vault via `az keyvault secret show` at boot on WSL (cache for process lifetime).
5. Add `tests/test_repo_dual_write.py` — confirms (a) a single `add_bet` writes one CSV row and one DB row when flag is on; (b) **no DB import or connection attempt occurs when `BETS_DB_WRITE` is unset** (the Pi-safety case).

**Acceptance.**
- [ ] Smoke scan on **WSL** (with `BETS_DB_WRITE=1`) appends a row to `logs/bets.csv` AND inserts a row into `bets` table; UUIDs match.
- [ ] With `BETS_DB_WRITE` unset (Pi case), only CSV is written; no pyodbc import attempted; no errors logged.
- [ ] After `git pull` on Pi, next scheduled cron runs unchanged (verify by tailing Pi's `logs/scan.log` — same line count growth as before A.4).
- [ ] No double-writes on cron retries (covered by upsert in repo layer).

**Reviewer focus.**
- **Pi safety:** import path must not require pyodbc/azure-* libs unless `BETS_DB_WRITE=1`. Lazy import inside the DB writer code path. Verify Pi can run scanner without those libs installed.
- Failure isolation: if DB insert fails on WSL, scanner still writes CSV and logs the error. Don't block the scan on DB outage.
- Connection re-use: open one pyodbc connection per scan run, not per row.
- Key Vault token caching: don't re-authenticate per scan.

**Verification commands.**
```bash
# WSL — dual-write should fire
export $(cat .env.dev) && BETS_DB_WRITE=1 python3 scripts/scan_odds.py --sports football 2>&1 | tail -20
# Expect: scan completes; row count grows in both logs/bets.csv AND Azure SQL bets table.

# Pi — dormant path, behavior unchanged
ssh robert@192.168.0.28 'cd ~/projects/bets && git pull && export $(cat .env) && .venv/bin/python3 scripts/scan_odds.py --sports football 2>&1 | tail -20'
# Expect: scan completes normally; no pyodbc errors; no Azure connection attempted.

python3 -c "import pyodbc; c = pyodbc.connect('$AZURE_SQL_DSN'); print(c.execute('SELECT TOP 5 created_at, side FROM bets ORDER BY created_at DESC').fetchall())"
```

---

## Phase A.5 — Dashboard reads DB-first with CSV fallback

**Goal.** `app.py` queries Azure SQL by default; falls back to local CSVs if DB unreachable. Renders identical UI either way.

**Tasks.**
1. Refactor data-loading functions in `app.py` behind `get_bets()`, `get_paper_bets()`, etc. — backed by the same `BetRepo` from A.4.
2. Add `/health` endpoint reporting `{db: "ok"|"down", csv: "ok"}`.
3. Settle-bet POST handler writes to DB if available, queues to CSV otherwise (later sync).
4. Add UI banner ("Using cached CSV data — DB unreachable") when in fallback.

**Acceptance.**
- [ ] Dashboard renders with full bet history when DB up.
- [ ] Stopping DB connectivity (kill firewall rule temporarily) → dashboard still renders from CSV; banner shows.
- [ ] Settle action with DB up writes to DB; with DB down writes to CSV.
- [ ] Visual diff (screenshot) between DB-mode and CSV-mode is identical (modulo banner).

**Reviewer focus.**
- Cache invalidation: each request shouldn't hit the DB N times — load once per request.
- Error-mode banner must not be visible when DB is healthy.
- Settle write path must be idempotent (refresh-after-submit shouldn't double-settle).

**Verification commands.**
```bash
python3 app.py &  # local WSL
curl -s localhost:5000/health
curl -s localhost:5000/ | grep -c "<tr"  # row count sanity check
```

---

## Phase A.6 — Provision App Service + deploy `app.py`

**Goal.** Public Azure URL serves the dashboard, reads from the same Azure SQL DB the Pi writes to.

**Tasks.**
1. `az appservice plan create -g kaunitz-dev-rg -n kaunitz-dev-plan --sku F1 --is-linux`.
2. `az webapp create -g kaunitz-dev-rg -p kaunitz-dev-plan -n kaunitz-dev-dashboard-<random> --runtime "PYTHON:3.11"`.
3. Configure app settings: `AZURE_SQL_DSN` (referencing Key Vault secret), `AZURE_MODE=true`.
4. Deploy: `az webapp up -n kaunitz-dev-dashboard-<random> -g kaunitz-dev-rg --runtime "PYTHON:3.11"`.
5. Confirm Application Settings → Identity → System-assigned managed identity ON; grant it `get` on the Key Vault secret.
6. Smoke-test the public URL; check cold-start latency. If F1 cold starts > 10s, escalate decision to B1 in A.7.

**Acceptance.**
- [ ] `curl https://kaunitz-dev-dashboard-<random>.azurewebsites.net/health` returns `{"db":"ok"}`.
- [ ] Dashboard renders bet history from DB (matches Pi-side data).
- [ ] App Service log stream shows no startup errors.

**Reviewer focus.**
- Managed identity (not connection-string-in-app-setting) must be the auth path to Key Vault.
- App must NOT have ODDS_API_KEY (only DB DSN — odds fetching stays on Pi).
- Confirm public dashboard does not expose any settle/admin endpoints without auth (next phase).

**Verification commands.**
```bash
az webapp show -g kaunitz-dev-rg -n kaunitz-dev-dashboard-<random> --query "{state:state, defaultHostName:defaultHostName}" -o json
curl -s https://kaunitz-dev-dashboard-<random>.azurewebsites.net/health
az webapp log tail -g kaunitz-dev-rg -n kaunitz-dev-dashboard-<random>  # interactive — confirm no errors
```

---

## Phase A.7 — Easy Auth (Google OIDC) on dashboard

**Goal.** Public URL requires Google sign-in; only `robert.freire@gmail.com` is authorized. (The Azure resources sit in the Reply VSE subscription, but the *application* identity layer is intentionally decoupled — see Phase A.0 identity boundary note.)

**Tasks.**
1. Portal: App Service → Authentication → Add identity provider → Google.
2. Set up Google OAuth client at console.cloud.google.com (OAuth 2.0 Client ID, Web app, redirect URI `https://kaunitz-dev-dashboard-<random>.azurewebsites.net/.auth/login/google/callback`). Owner of this OAuth client is the user's personal Google account, not the Reply identity.
3. Configure App Service: "Require authentication" + "Allowed identities" → restrict to `robert.freire@gmail.com`.
4. **Decision branch (document in PR body):** if Easy Auth setup hits a snag (Google verification, SP issues, Reply tenant blocking the redirect), fall back to HTTP Basic Auth — 1 LOC in `app.py` checking `request.authorization.username == 'robert' and request.authorization.password == os.environ['BASIC_AUTH_PASS']`. Store `BASIC_AUTH_PASS` in Bitwarden + App Settings.

**Acceptance.**
- [ ] `curl -i https://kaunitz-dev-dashboard-<random>.azurewebsites.net/` returns 302 to Google login (or 401 with Basic Auth fallback).
- [ ] Browser test: sign in as `robert.freire@gmail.com` → dashboard renders. Sign in as different account → 403.
- [ ] Pi → App Service link still works (Pi calls public URL; should be allowed via internal allowlist, OR the dashboard doesn't need this).

**Reviewer focus.**
- Allowlist must be email-exact; "anyone with a Google account" is unacceptable.
- /health endpoint should remain unauth (for monitoring).
- Confirm no auth bypass via direct DB/blob access from internet.

**Verification commands.**
```bash
curl -i https://kaunitz-dev-dashboard-<random>.azurewebsites.net/ | head -1  # expect 302 or 401
curl -s https://kaunitz-dev-dashboard-<random>.azurewebsites.net/health     # should still return 200
```

---

## Phase A.8 — Cutover: DB-only writes, archive CSVs

**Goal.** Stop writing to CSVs from the scanner; DB is sole source of truth. CSVs preserved for rollback.

**Pre-condition.** ≥1 calendar week of clean dual-write operation (A.4) and dashboard reads (A.5) with no DB errors logged.

**Tasks.**
1. Flip default in `BetRepo`: `BETS_DB_WRITE=1` and add `BETS_CSV_WRITE` defaulting `0`.
2. Move existing CSVs: `mv logs/bets.csv logs/csv-archive/bets.csv.pre-cutover-2026-MM-DD` etc.
3. Update CLAUDE.md "Key files" section to remove CSV references; point to the DB.
4. Pi cron unchanged (env var change only).
5. Update `.gitignore` to add `logs/csv-archive/`.

**Acceptance.**
- [ ] After cutover, run a Pi scan → no new rows in any CSV file (`stat logs/bets.csv` mtime unchanged).
- [ ] DB row count grows by expected amount.
- [ ] Dashboard renders correctly with no CSV files present (force fallback by renaming archive away).

**Reviewer focus.**
- Backup strategy: confirm Azure SQL automated backups are enabled (default for Free tier — verify via `az sql db show`).
- Document the rollback procedure in CLAUDE.md ("if DB is wedged for >24h: copy CSVs from `logs/csv-archive/`, set `BETS_CSV_WRITE=1`, set `BETS_DB_WRITE=0`").

**Verification commands.**
```bash
ssh robert@192.168.0.28 'cd ~/projects/bets && export $(cat .env) && .venv/bin/python3 scripts/scan_odds.py --sports football && stat -c "%y %n" logs/bets.csv'
# mtime should match the archive copy, NOT the current scan time.
az sql db show-deleted -g kaunitz-dev-rg -s kaunitz-dev-sql-uksouth-<random> 2>/dev/null  # confirm restore-from-deleted available
```

---

## Phase A.9 — Decommission CSV path entirely

**Goal.** Remove the dual-write code; the DB is the only storage layer in the codebase.

**Pre-condition.** ≥1 calendar week of clean DB-only operation (A.8).

**Tasks.**
1. Delete CSV write paths from `BetRepo` and all callers.
2. Remove `BETS_CSV_WRITE` env flag handling.
3. Move `scripts/migrate_csv_to_db.py` → `scripts/archive/migrate_csv_to_db.py` (kept as historical reference; not loaded).
4. Delete `logs/*.csv` from working tree (NOT the archive).
5. Update `tests/` — remove CSV-roundtrip tests; add DB-roundtrip tests if missing.
6. Update CLAUDE.md to reflect DB-only architecture.
7. Final commit closes Phase 9 in CLAUDE.md.

**Acceptance.**
- [ ] `grep -rn "to_csv\|read_csv\|csv\.writer\|csv\.reader" src/ scripts/ app.py | grep -v archive/` returns ≤2 hits (only data-ingestion scripts that read external CSVs like football-data.co.uk).
- [ ] Full pytest suite green.
- [ ] CLAUDE.md Phase 9 marked ✅ Done with date.

**Reviewer focus.**
- Confirm no implicit dependency on CSVs in compare_strategies.py (it currently globs `logs/paper/*.csv` — needs DB rewrite).
- Confirm research_scan.py / refresh_xg.py do not need CSV outputs.
- One final manual end-to-end smoke: scan on Pi → row in DB → visible in Azure dashboard within 1 minute.

**Verification commands.**
```bash
grep -rn "to_csv\|read_csv\|csv\.writer\|csv\.reader" src/ scripts/ app.py | grep -v archive/
pytest -q
ssh robert@192.168.0.28 'cd ~/projects/bets && ls logs/*.csv 2>&1'  # expect "No such file"
```

---

## Phase A.10 — Stand up `kaunitz-prod-rg` + onboard Pi (deferred — separate sprint)

**Status.** Deferred. Not part of this plan. Listed here so future sessions know it's the natural follow-up.

**Scope (substantially larger than the original "Pi onboarding"):** A.10 mirrors the entire dev stack into a brand-new prod RG, then connects Pi to it. Dev keeps running independently (different DB, different dashboard URL).

**Trigger gate (all must hold before unblocking A.10):**
1. A.0–A.9 fully done on the dev stack.
2. ≥1 calendar week of clean WSL DB-only operation (no rollbacks, no data corruption).
3. WSL data in the dev dashboard matches WSL CSVs by spot-check.
4. Pi production cron has not regressed during the dev Azure rollout (verified by tailing `~/projects/bets/logs/scan.log` on Pi — line counts grew normally, no errors).

**When unblocked, A.10 will cover (sketch — to be detailed in a fresh `PLAN_PI_AZURE_<YYYY-MM>.md` doc when the gate clears):**

**Step 1 — Stand up the prod stack (mirror of A.0–A.7 but in `kaunitz-prod-rg`):**
- `az group create -n kaunitz-prod-rg -l uksouth --tags env=prod project=kaunitz owner=rfreire`.
- Provision `kaunitz-prod-sql-uksouth-<rand>` with `--use-free-limit` if available (the once-per-subscription free offer is reserved for here), else serverless with longer auto-pause delay (e.g. 6 hours) to keep prod warm during match windows. Admin user `kaunitzadmin` (separate password from dev — store distinct Bitwarden entry).
- `kaunitz-prod-kv-<rand>` with the prod SQL admin password.
- `kaunitz-prod-plan` (F1) + `kaunitz-prod-dashboard-<rand>`. Deploy current `app.py`. Configure Easy Auth (Google OIDC, allowlist `robert.freire@gmail.com`) — same identity as dev, but separate web app so the URLs are distinct.
- Run `src/storage/migrate.py` against the prod DB to apply schema.

**Step 2 — Onboard Pi:**
- Pi `.env` adds `BETS_DB_WRITE=1` + `AZURE_SQL_DSN` (pointing at `kaunitz-prod-sql-uksouth-<rand>`) + Key Vault secret access for Pi's identity.
- Prod Azure SQL firewall opens to Pi's public IP.
- Pi `git pull` picks up the dual-write code (already deployed to Pi as dormant since A.4).
- One-shot import of Pi historical CSVs (`scripts/migrate_csv_to_db.py` re-run pointed at Pi data + prod DSN).
- Verify Pi rows appear in the prod dashboard.

**Step 3 — Soak + decommission:**
- ≥1 week soak with Pi writing to prod and WSL writing to dev — no cross-pollination.
- Eventually: Pi CSV decommission (mirror of A.8/A.9 but for Pi, against prod DB).

**Why deferred:** Pi is production. Touching it during a fresh Azure stand-up risks breaking the canonical data stream that we depend on for CLV evaluation. Better to debug Azure on dev data first, then onboard Pi from a known-good base. The two-stack split also means dev experiments can keep running through any prod incidents.

---

## Out of scope (in this plan)

- **Pi onboarding to Azure SQL** — see Phase A.10 above; deferred to separate future sprint.
- **Multi-tenant auth** — only one user (`robert.freire@gmail.com`) for the foreseeable future.
- **Real-time streaming** — scans on cron, dashboard polls; no WebSockets.
- **DB-level row encryption** — Azure SQL is encrypted at rest by default; no PII in the schema beyond bet history.
- **Multi-region failover** — single UK South region is enough.
- **Betfair API auto-placement** — that's Phase 8 in `CLAUDE.md`, runs in parallel with Azure but not part of this plan.

---

## Cross-cutting risks

1. **Pi safety contract violation** (the new top risk in this dev-first scope) → if any phase accidentally touches Pi cron behavior (e.g., A.4 module imports pyodbc unconditionally and Pi doesn't have it installed), Pi production silently breaks. Mitigated by A.4's lazy-import requirement + the `git pull on Pi` smoke test in A.4 Acceptance. **Reviewer must explicitly verify Pi-side smoke after every code-touching phase.**
2. **WSL → Azure SQL connectivity outage** mid-scan → handled by A.4's failure-isolation (CSV stays as fallback during dual-write; alert via ntfy if DB writes fail >5 consecutive scans on WSL).
3. **Free-tier SQL quota exhaustion** mid-month → escalate to Basic (~£5/mo) per A.1's decision table.
4. **Easy Auth + Google verification delays** → A.7's documented Basic Auth fallback covers this.
5. **CSV → DB importer non-deterministic UUIDs** → A.3's deterministic-UUID rule prevents reimport duplication; verified in Acceptance.
6. **Dashboard cold-start UX** → measured in A.6; B1 escalation path exists.
7. **Forgetting Phase A.10** — easy to ship A.0–A.9 and forget Pi is still on CSVs. Mitigation: the dashboard banner "Showing WSL data only — Pi still on CSVs" should appear in A.5 until A.10 ships.
