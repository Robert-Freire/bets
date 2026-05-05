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
| Web tier | **Container Apps Consumption** (scale-to-zero; A.6 pivot — Reply VSE has 0 App Service VM quota); image in ACR Basic ~£4/mo | **Container Apps Consumption** with min replicas ≥1 if cold-starts hurt; same ACR Basic |
| Free SQL offer (one per subscription) | Goes to **prod**, not dev | ✅ Reserved for here |
| Dashboard URL identity | `kaunitz-dev-dashboard-rfk1.<env-id>.uksouth.azurecontainerapps.io` (live) | `kaunitz-prod-dashboard-rfk1.<env-id>.uksouth.azurecontainerapps.io` (A.10 will mirror) |
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
                                    ├── kaunitz-dev-st-<rand> (storage account)
                                    │     └── container: raw-api-snapshots
                                    │           (gzipped JSON of every external API
                                    │            response — Odds API now;
                                    │            Pinnacle/Betfair/etc. future.
                                    │            Lifecycle: hot 30d → cool 90d → delete.)
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
                                    ├── kaunitzprodacr<rand>  (ACR Basic, image registry)
                                    ├── kaunitz-prod-env  (Container Apps managed env)
                                    └── kaunitz-prod-dashboard-<rand>  (Container App, mirrors dev pivot)

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
| Where do raw API responses live? | **Azure Blob Storage** (`kaunitzdevstrfk1` storage account in `kaunitz-dev-rg`, container `raw-api-snapshots`). Phase A.5.5 stands this up; mirrors the BetRepo dual-writer contract from A.4 (env-gated, Pi-safe lazy import). Local `logs/snapshots/` only as transient offline buffer. Lifecycle rule: hot → cool at 30d, **no auto-delete** (the archive is the substrate for future data-quality rules — retention is indefinite). | We never persisted raw odds API output; on 2026-05-01 we found we couldn't retro-test proposed data-quality rules (Pinnacle overround sanity, book dropout, stale response). Blob = cheap, durable, queryable, and natural fit for the Azure migration already in flight. Volumes are tiny (~50 MB/month → ~3 GB after 5 years; pennies on cool tier) so retention cost is not a binding constraint. |

---

## Phase status tracker

All A.0–A.9 phases operate on `kaunitz-dev-rg`. A.10 is the only phase that creates `kaunitz-prod-rg` and touches Pi.

| Phase | Title | RG | Touches Pi? | Status | Depends on |
|---|---|---|---|---|---|
| A.0 | Provision Azure account + dev resource group | dev | no | ✅ Done 2026-05-01 | — |
| A.1 | Stand up dev Azure SQL Database (serverless, auto-pause) | dev | no | ✅ Done 2026-05-01 | A.0 |
| A.2 | Schema DDL + idempotent migrations runner | dev | no | ✅ Done 2026-05-01 | A.1 |
| A.3 | CSV → DB importer — **WSL CSVs only** | dev | no | ✅ Done 2026-05-01 | A.2 |
| A.4 | Storage layer + dual-write in scanner — **WSL only**, env-flag gated so Pi `git pull` is safe | dev | no (code is gated; Pi never sets the flag) | ✅ Done 2026-05-01 | A.2 |
| A.5 | Dashboard reads DB-first with CSV fallback — **shows WSL data only** | dev | no | ✅ Done 2026-05-01 | A.2, A.4 |
| A.5.5 | Blob archive for raw API responses (Odds API, future Pinnacle/Betfair) — **WSL only**, env-flag gated | dev | no (gated; Pi never sets the flag) | ✅ Done 2026-05-01 | A.4 |
| A.6 | Provision dev App Service + deploy `app.py` (**pivoted to Container Apps** — Reply VSE has 0 App Service VM quota) | dev | no | ✅ Done 2026-05-01 | A.5 |
| A.7 | Easy Auth (Google OIDC) on dev dashboard | dev | no | ✅ Done 2026-05-01 | A.6 |
| A.8 | Cutover: WSL DB-only, archive WSL CSVs | dev | no | ✅ Partial 2026-05-02 — dashboard reads DB-only; scanner still dual-writes; CSV archive pending | A.7 + 1 week stable A.4/A.5 |
| A.9 | Decommission WSL CSV path entirely (scanner stops writing CSV, remove CSV code paths) | dev | no | ✅ Done 2026-05-04 | A.8 full + 1 week stable |
| **A.10** | **Stand up `kaunitz-prod-rg` + onboard Pi + prod dashboard** | **prod (new)** | **yes** | ✅ Done 2026-05-05 — Pi on `main` writing to prod DB; prod dashboard live (`kaunitz-prod-dashboard-rfk1.agreeablemoss-0a74374c.uksouth.azurecontainerapps.io`, Google OIDC) | A.9 ✅ |

---

## Cost estimate

Two stacks costed separately. Reply VSE provides ~£150/month MSDN credit (recurring), so even worst-case is well-covered.

**During A.0–A.9 (dev stack only):**

| Service | Cost/month |
|---|---|
| Azure SQL DB — dev (serverless `GP_S_Gen5_2`, 60-min auto-pause) | £0–£5 (depending on weekend usage; £0 while paused) |
| App Service F1 — dev (`az webapp stop` between sessions) | £0 |
| Key Vault — dev (first 10k ops free) | £0 |
| Storage account — dev (LRS hot, ~50 MB/month raw snapshots, lifecycle to cool/delete) | £0–£1 |
| Outbound bandwidth | £0 |
| **Dev subtotal during this plan** | **£0–£6/mo** |

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

Prod (`kaunitz-prod-rg`, A.10) is **never** stopped during normal ops — Pi cron writes can fire at any cron-configured time and a paused DB during a scan would lose that scan's writes. (Originally this section also cited every-5-min `closing_line.py` polling; that script was paused 2026-05-01 in favour of the Mon 08:00 FDCO backfill, but the always-on-prod stance still holds because the scanner cron itself runs at fixed times that must succeed first try.)

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

## Phase A.2 — Schema DDL + idempotent migrations runner  ✅ Done 2026-05-01

**Tables created:** `fixtures`, `books`, `strategies`, `bets`, `paper_bets`, `closing_lines`, `drift`. All FKs through `fixtures(id)` / `books(id)` / `strategies(id)`.

**Goal.** A version-controlled SQL schema that can be applied (and re-applied) safely.

**Tasks.**
1. Create `src/storage/schema.sql` (canonical MSSQL T-SQL) with `IF OBJECT_ID(...) IS NULL` guards and `src/storage/schema_sqlite.sql` (sibling for in-memory SQLite tests) covering:
   - `fixtures` (id uuid PK, sport_key, league, home, away, kickoff_utc, created_at)
   - `books` (id int PK, name, region, commission_rate)
   - `bets` (id uuid PK, fixture_id FK, book_id FK, scanned_at, market, line, side, odds, impl_raw, impl_effective, edge, edge_gross, effective_odds, commission_rate, consensus, pinnacle_cons, n_books, confidence, model_signal, dispersion, outlier_z, devig_method, weight_scheme, stake, result, settled_at, pnl, pinnacle_close_prob, clv_pct, created_at)
   - `closing_lines` (fixture_id FK + side/market/line/book_id composite PK; pinnacle_close_prob, pinnacle_raw_odds, your_book_flagged_odds, your_book_close_odds, clv_pct, captured_at). Note: keyed by (fixture, market, side, line, book) not bet_id, so a single closing data row covers production + every paper variant for the same identity.
   - `drift` (fixture_id FK + side/market/line/book_id/t_minus_min composite PK; captured_at, your_book_odds, pinnacle_odds, n_books)
   - `strategies` (id int PK, name UNIQUE, description, active)
   - `paper_bets` (id uuid PK, strategy_id FK, fixture_id FK, book_id FK, ... same fields as bets)
2. Create `src/storage/migrate.py` — dialect-aware (`--dsn` for MSSQL/pyodbc, `--sqlite` for SQLite), splits the schema file into statements, reports table-count delta after each run.
3. Add `tests/test_schema.py` — applies `schema_sqlite.sql` to in-memory SQLite, verifies tables/columns/FKs/indices; subprocess test for the runner itself; sanity check that `schema.sql` has `IF OBJECT_ID` guards.

**Acceptance.**
- [x] `python3 -m src.storage.migrate --dsn "$AZURE_SQL_DSN"` against the empty dev DB created 7 tables (`+7`); second run reports `no changes`.
- [x] `pytest tests/test_schema.py` — 10/10 passing on the SQLite path.
- [x] FK constraints present (bets→{fixtures,books}, paper_bets→{fixtures,books,strategies}, closing_lines/drift→{fixtures,books}); indices on `(kickoff_utc, sport_key)` and `(strategy_id, result)` confirmed via `sys.indexes` query.

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

## Phase A.3 — CSV → DB importer (one-shot, idempotent) — WSL CSVs only  ✅ Done 2026-05-01

**First-run results against `kaunitz-dev-sql-uksouth-rfk1`:** fixtures=90, books=17, strategies=15, bets=0 (production CSV header-only), paper_bets=543, closing_lines=0, drift=0 (CSVs absent — appear in A.4 onward). Per-variant counts match `wc -l logs/paper/*.csv` minus headers exactly. Second run reports 0 inserts across all tables.

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
- [x] `wc -l logs/bets.csv` ≈ `SELECT COUNT(*) FROM bets` (off-by-1 for header). (CSV is header-only → 0 imported, 0 in DB.)
- [x] Per-variant: `wc -l logs/paper/<v>.csv` ≈ `SELECT COUNT(*) FROM paper_bets WHERE strategy_id = (SELECT id FROM strategies WHERE name = '<v>')`. Verified for all 15 variants.
- [x] Re-running the importer produces zero new rows.

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

## Phase A.4 — Storage layer + dual-write in scanner — WSL only  ✅ Done 2026-05-01

**Capability shipped:** `src/storage/repo.py` (BetRepo) + `src/storage/_keys.py` (shared deterministic-UUID + label helpers, used by both the live writer and the A.3 importer). `scripts/scan_odds.py` and `scripts/closing_line.py` now write through the repo. CSV format/dedup behavior unchanged. DB-write activation requires both `BETS_DB_WRITE=1` and `AZURE_SQL_DSN` (or the `AZURE_SQL_SERVER/USER/DATABASE/KV_VAULT/KV_SECRET` quintet that builds the DSN from Key Vault at boot).

**WSL smoke run:** scan_odds against `kaunitz-dev-sql-uksouth-rfk1` produced 9 paper bets across 5 strategies; DB `paper_bets` count grew 543 → 552 with deterministic UUIDs matching the CSV rows byte-for-byte (verified by recomputing `paper_bet_uuid()` from the CSV and looking up by id in the DB).

**Pi smoke run:** scan_odds with no env flags printed `[scan] CSV-only mode (BETS_DB_WRITE not set)`, `pyodbc` was never imported (verified via `'pyodbc' in sys.modules`), CSV writes succeeded, and behavior matched pre-A.4 byte-for-byte. **Enabling dual-write on the WSL cron is a separate user-driven step** — it is not enabled by default, so until you add the env vars to `.env.dev`/the cron environment, WSL keeps writing CSV-only too.

**Goal.** **WSL's** `scan_odds.py` writes to both CSV (existing) and Azure SQL (new) on every scan. Env flag `BETS_DB_WRITE=1` gates the new path. **Pi's `scan_odds.py` is NOT modified beyond what `git pull` brings in — Pi never sets `BETS_DB_WRITE`, so the dual-write code path stays dormant on Pi.**

**Pi safety contract.** The `BetRepo` module must be import-safe even when `pyodbc` / `AZURE_SQL_DSN` / `BETS_DB_WRITE` are absent. Pi's `git pull` brings in the new code; on next cron fire, Pi runs the scanner with no env flag → the DB write path is short-circuited → Pi behavior is byte-identical to pre-A.4. Verify this in tests.

**Tasks.**
1. New module `src/storage/repo.py` with `BetRepo` class: `add_bet(...)`, `add_paper_bet(...)`, `add_closing_line(...)`, `add_drift_snapshot(...)`. CSV writer always-on; DB writer activated only when `BETS_DB_WRITE=1` AND `AZURE_SQL_DSN` is set.
2. Wire into `scripts/scan_odds.py` and `scripts/closing_line.py` — both call `repo.add_*` instead of writing CSVs directly.
3. **WSL `.env.dev`** adds `BETS_DB_WRITE=1` + `AZURE_SQL_DSN`. Pi `.env` does NOT — Pi stays CSV-only.
4. Connection pulled from Azure Key Vault via `az keyvault secret show` at boot on WSL (cache for process lifetime).
5. Add `tests/test_repo_dual_write.py` — confirms (a) a single `add_bet` writes one CSV row and one DB row when flag is on; (b) **no DB import or connection attempt occurs when `BETS_DB_WRITE` is unset** (the Pi-safety case).

**Acceptance.**
- [x] Smoke scan on **WSL** (with `BETS_DB_WRITE=1`) appends a row to `logs/bets.csv` AND inserts a row into `bets` table; UUIDs match. (Verified for `paper_bets`: 9 new rows, deterministic UUIDs spot-checked.)
- [x] With `BETS_DB_WRITE` unset (Pi case), only CSV is written; no pyodbc import attempted; no errors logged.
- [ ] After `git pull` on Pi, next scheduled cron runs unchanged (verify by tailing Pi's `logs/scan.log` — same line count growth as before A.4). *Pending Pi `git pull` after merge — capability is in place; tested on WSL with env flags stripped.*
- [x] No double-writes on cron retries (covered by upsert in repo layer; covered by `test_dual_write_idempotent_on_retry`).

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

## Phase A.5 — Dashboard reads DB-first with CSV fallback  ✅ Done 2026-05-01

**Capability shipped.** `BetRepo` got a read API: `get_bets()`, `get_drift()`, `db_status()`, `update_bet_settle()`. Schema gained `actual_stake decimal(10,2) NULL` on `bets` + `paper_bets` (idempotent ALTER guards on the live DB; in-line column on fresh CREATE). `app.py` now constructs a per-request `BetRepo`, prefers the DB when reachable, falls back to CSV otherwise. `/health` reports `{db, csv}` (200 when db ≠ down; 503 when db=down). The settle handler still writes CSV (canonical until A.8), and additionally `UPDATE`s the DB row when dual-write is on.

**Three-mode smoke test:**

| Mode | /health | Banner | Rendered rows |
|---|---|---|---|
| Dual-write env (DB ok)        | 200 `{db:"ok", csv:"ok"}`        | hidden  | 52 |
| No env (CSV-only)             | 200 `{db:"disabled", csv:"ok"}`  | hidden  | 52 |
| DB env set, bad DSN (db down) | **503** `{db:"down", csv:"ok"}`  | **shown** | 52 |

Identical row counts in DB and CSV mode after a one-shot importer re-run synced the WSL CSV rows that pre-dated dual-write activation.

**Goal.** `app.py` queries Azure SQL by default; falls back to local CSVs if DB unreachable. Renders identical UI either way.

**Tasks.**
1. Refactor data-loading functions in `app.py` behind `get_bets()`, `get_paper_bets()`, etc. — backed by the same `BetRepo` from A.4.
2. Add `/health` endpoint reporting `{db: "ok"|"down", csv: "ok"}`.
3. Settle-bet POST handler writes to DB if available, queues to CSV otherwise (later sync).
4. Add UI banner ("Using cached CSV data — DB unreachable") when in fallback.

**Acceptance.**
- [x] Dashboard renders with full bet history when DB up.
- [x] Stopping DB connectivity (simulated via bad DSN) → dashboard still renders from CSV; banner shows.
- [x] Settle action with DB up writes to DB; with DB down writes to CSV. (CSV is also written in DB-up mode for now — A.8 cuts CSV writes off; until then, dual-write keeps both in lockstep and `repo.update_bet_settle` keeps the DB row in sync.)
- [x] Visual diff (row count) between DB-mode and CSV-mode is identical (modulo banner). After a one-shot `migrate_csv_to_db.py` resync, both modes render 52 `<tr>` elements on the dashboard.

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

## Phase A.5.5 — Blob archive for raw API responses (WSL only, env-flag gated)  ✅ Done 2026-05-01

**Goal.** Every external API request — Odds API today, Pinnacle/Betfair/etc. in the future — persists its raw, unparsed response to Azure Blob Storage **before** any parsing/devigging/filtering, so future data-quality rules (Pinnacle overround sanity, book dropout, stale response, per-book deviation) can be retro-tested against real history. Mirrors A.4's BetRepo dual-writer contract: WSL only, env-flag gated, Pi-safe lazy import.

**Lifecycle decision (corrected mid-execution).** Original plan was tier-to-cool at 30d, **delete at 120d**. Dropped the delete: the archive's whole purpose is retro-testing future data-quality rules, and 120d is shorter than the time horizon for many of those checks. Volumes are tiny (~50 MB/month → ~3 GB after 5 years; pennies on cool tier), so retention cost is not a binding constraint. Active rule: tier-to-cool at 30d, **no auto-delete**.

**What landed.**
- Storage account `kaunitzdevstrfk1` (Standard_LRS, hot, public-access disabled, TLS 1.2 minimum) in `kaunitz-dev-rg`.
- Container `raw-api-snapshots` (private).
- Lifecycle rule `raw-api-snapshots-tier`: hot → cool at 30d, no delete.
- Key Vault secret `blob-storage-connection-string` in `kaunitz-dev-kv-rfk1`.
- New module `src/storage/snapshots.py` with `SnapshotArchive` class:
  - Lazy `from azure.storage.blob import BlobServiceClient` only inside the activated branch.
  - Activated only when `BLOB_ARCHIVE=1` AND (`AZURE_BLOB_CONN` set OR `AZURE_BLOB_KV_VAULT`+`AZURE_BLOB_KV_SECRET` set, mirroring A.4's KV resolution).
  - Blob key: `<source>/<endpoint_sanitised>/<YYYY>/<MM>/<DD>/<scan_iso_with_microseconds>_<sport_key>.json.gz`. Microsecond resolution + `overwrite=False` upload eliminates collision risk.
  - Blob body: gzipped JSON `{captured_at, source, endpoint, params (apiKey/api_key redacted to "<redacted>"), status, headers (allowlist: x-requests-remaining, x-requests-used, date, content-type), body_raw}`.
  - On any blob-write error: log + fall back to `logs/snapshots/<same-key>` local buffer. Buffer drained on next successful archive call (once per process), files deleted only after confirmed upload.
- Wired into `scripts/scan_odds.py`'s `api_get(...)` — single archive call per HTTP response, before parse, sport_key parsed from path so every endpoint is covered automatically.
- `tests/test_snapshot_archive.py` (6 cases): Pi-safety no-import; activated path with redaction + key shape + header allowlist; api_key never in archived body; failure isolation → local buffer; buffer drain on success; buffer persists on drain failure.
- `logs/snapshots/` added to `.gitignore`.
- **No backfill** — coverage starts at deploy time.

**Acceptance.**
- [x] `pytest tests/test_snapshot_archive.py` — 6/6 pass; full suite 263/263.
- [x] Lifecycle rule visible via `az storage account management-policy show` (`tierToCool@30d`, no delete action).
- [x] `--allow-blob-public-access false` on the storage account.
- [ ] **Manual smoke pending user `.env.dev` flip** — set `BLOB_ARCHIVE=1` + `AZURE_BLOB_KV_VAULT=kaunitz-dev-kv-rfk1` + `AZURE_BLOB_KV_SECRET=blob-storage-connection-string`, run a scan, verify a sample blob lands and contains `<redacted>` instead of the real key.

**Reviewer focus (resolved).**
- **Pi safety:** module-level imports are stdlib only; `azure.storage.blob` is imported only inside the activated branch. Verified by `test_pi_safety_no_env_means_no_azure_import`.
- **API key redaction:** keys matching `apikey`/`api_key` (case-insensitive) replaced with `<redacted>`. `test_api_key_never_in_archived_blob` asserts the literal key string is absent from the gzipped body.
- **Container access:** `--allow-blob-public-access false` set at storage-account level.
- **Connection re-use:** single `BlobServiceClient` per `SnapshotArchive` instance; one instance per process via the module singleton.
- **Blob key collisions:** microsecond timestamps + `upload_blob(..., overwrite=False)` so collisions fail loud rather than clobber.
- **Cost guard:** lifecycle rule confirmed. Tier-to-cool only; no delete.
- **Scope creep:** this phase only stands up the archive. Data-quality rules on top of it are deferred (label A.5.6 if/when we build them).

**Verification commands.**
```bash
# Lifecycle rule sanity
az storage account management-policy show --account-name kaunitzdevstrfk1 -g kaunitz-dev-rg -o json

# WSL — once .env.dev is flipped, blob path active
export $(cat .env.dev) && python3 scripts/scan_odds.py
az storage blob list --account-name kaunitzdevstrfk1 -c raw-api-snapshots --prefix odds_api/ --num-results 5 -o table --auth-mode login
az storage blob download --account-name kaunitzdevstrfk1 -c raw-api-snapshots -n <one-blob-key> -f /tmp/sample.json.gz --auth-mode login
zcat /tmp/sample.json.gz | jq '{captured_at, source, endpoint, status, params}'
zcat /tmp/sample.json.gz | grep -c "$ODDS_API_KEY"   # expect 0

# Pi — dormant path (already enforced by tests; this is the env-level smoke)
ssh robert@192.168.0.28 'cd ~/projects/bets && git pull && export $(cat .env) && .venv/bin/python3 -c "from src.storage.snapshots import SnapshotArchive; SnapshotArchive().archive(source=\"odds_api\", endpoint=\"/sports/\", params={\"apiKey\":\"x\"}, status=200, headers={}, body=b\"[]\"); import sys; assert \"azure.storage.blob\" not in sys.modules; print(\"pi-safe: ok\")"'
```

---

## Phase A.6 — Provision App Service + deploy `app.py` — pivoted to Container Apps  ✅ Done 2026-05-01

**Pivot.** Reply VSE subscription has **0 App Service VM quota** across every SKU (Free/Basic/Standard/Premium) in UK South — discovered when `az appservice plan create --sku F1 --is-linux` returned `Current Limit (Free VMs): 0`. Same for B1 / S1 / P0v3. Quota is a subscription-level cap, not regional. Rather than wait on a quota request, pivoted to **Azure Container Apps** (different compute family — explicitly registered + provisionable in Reply VSE) which gives us scale-to-zero Consumption pricing and a public HTTPS URL.

**What landed:**
- `Dockerfile` (`python:3.11-slim` + `msodbcsql18` from the Microsoft repo + Flask + gunicorn).
- `requirements-app.txt` (minimal: `flask`, `gunicorn`, `pyodbc` — no pandas/catboost; image stays ~57MB).
- `.dockerignore` (drops tests, scripts, paper CSVs, model JSON, virtualenv).
- `kaunitzdevacrrfk1` (Basic ACR; ~£4/mo; built via `az acr build`).
- `kaunitz-dev-env` (Container Apps managed environment).
- `kaunitz-dev-dashboard-rfk1` container app:
  - 0.25 vCPU / 0.5Gi RAM, min replicas 0 (scale-to-zero), max 1.
  - System-assigned managed identity → `AcrPull` on ACR + KV `get/list secrets` on `kaunitz-dev-kv-rfk1`.
  - Container Apps secret `sql-dsn` resolved via Key Vault reference (`keyvaultref:.../secrets/sql-dsn,identityref:system`); env `AZURE_SQL_DSN=secretref:sql-dsn` injects the resolved DSN at boot. **No password on disk anywhere.**
  - Env: `BETS_DB_WRITE=1`, `AZURE_SQL_DSN=secretref:sql-dsn`. No `ODDS_API_KEY` (odds fetching stays on Pi/WSL cron).
- New KV secret `sql-dsn` containing the full pyodbc DSN (server + user + KV-fetched password).

**Public URL:** `https://kaunitz-dev-dashboard-rfk1.orangebush-7e5af054.uksouth.azurecontainerapps.io` (no auth yet — A.7).

**Smoke test results:**

| Probe | Result |
|---|---|
| `/health` | `200 {"csv":"ok","db":"ok"}` |
| `/` row count | 52 (matches local DB-mode) |
| Banner | hidden (db=ok) |
| Warm `/health` latency | ~150ms |

**Cost.** ACR Basic ~£4/mo, Container Apps consumption (~£0 idle, fractions of a penny per request), SQL DB serverless auto-pauses → £0 idle. Whole dev stack ≈ £4–10/mo.

**Acceptance.**
- [x] `curl https://kaunitz-dev-dashboard-rfk1.orangebush-7e5af054.uksouth.azurecontainerapps.io/health` returns `200 {"csv":"ok","db":"ok"}`. (Hostname differs from planned `*.azurewebsites.net` because of the App Service → Container Apps pivot.)
- [x] Dashboard renders bet history from DB (matches WSL-side data — Pi data is out of scope until A.10).
- [x] Container Apps log stream shows no startup errors after the v1→v2 Dockerfile fix (initial v1 had `apt-get purge --auto-remove` which silently removed `msodbcsql18` deps; v2 keeps them).

**Reviewer focus.**
- Managed identity (not connection-string-in-env-setting) is the auth path to Key Vault. Container Apps `keyvaultref:...,identityref:system` resolves at boot using the system-assigned identity. ✓
- App container must NOT have `ODDS_API_KEY` (only DB DSN — odds fetching stays on Pi/WSL). ✓
- Confirm public dashboard does not expose any settle/admin endpoints without auth — A.7 covers this. *Currently the dashboard is fully open to the public internet; Easy Auth lands next.*

**Verification commands.**
```bash
APP=kaunitz-dev-dashboard-rfk1
RG=kaunitz-dev-rg
FQDN=$(az containerapp show -g $RG -n $APP --query "properties.configuration.ingress.fqdn" -o tsv)
curl -sS "https://$FQDN/health"
az containerapp logs show -g $RG -n $APP --tail 50 --type console     # python stdout/stderr
az containerapp logs show -g $RG -n $APP --tail 50 --type system      # platform events
```

---

## Phase A.7 — Container Apps auth (Google OIDC) on dashboard  ✅ Done 2026-05-01

**What landed.**

- Google OAuth 2.0 Web client `kaunitz-dashboard` created in personal Google Cloud project (owner = `robert.freire@gmail.com`, not Reply). Consent screen left in **Testing** mode with `robert.freire@gmail.com` as the only test user — Google itself rejects sign-ins from other accounts before they ever reach the proxy.
- Authorized redirect URI: `https://kaunitz-dev-dashboard-rfk1.orangebush-7e5af054.uksouth.azurecontainerapps.io/.auth/login/google/callback`.
- Client secret stored in `kaunitz-dev-kv-rfk1` as `dashboard-google-client-secret`. Container app secret `google-client-secret` references it via system-assigned managed identity (mirrors the `sql-dsn` binding from A.6) — no plaintext anywhere on disk.
- Container app auth wired: `az containerapp auth google update --client-id ... --client-secret-name google-client-secret`, then `az containerapp auth update --enabled true --action RedirectToLoginPage --redirect-provider google --excluded-paths /health --require-https true`.
- **Defense-in-depth allowlist** (`app.py` `before_request` hook) added because Container Apps `auth update` doesn't expose a per-user `--allowed-principals` flag like App Service does. The hook reads `X-MS-CLIENT-PRINCIPAL-NAME` (or decodes `X-MS-CLIENT-PRINCIPAL`) and 403s any signed-in user not in `DASHBOARD_ALLOWED_EMAILS`. `DASHBOARD_ALLOWED_EMAILS=robert.freire@gmail.com` set on the container app.
- Image rebuilt as `kaunitzdevacrrfk1.azurecr.io/dashboard:v4`, container revision rolled to `kaunitz-dev-dashboard-rfk1--0000005`. (v3 used a module-level `ALLOWED_EMAILS` constant that leaked env state across pytest files; v4 reads env per-request — same runtime behaviour.)
- `tests/test_app_allowlist.py` — 6 cases pass: env unset → no enforcement, missing principal → 401, name-header email → admit, principal-blob email → admit, wrong email → 403, `/health` always open.

**Smoke results:**

| Probe | Result |
|---|---|
| `curl -i https://.../` (no UA) | `401` + `WWW-Authenticate: Bearer` (API-style — no accidental redirect for non-browser callers) |
| `curl -i -H "User-Agent: Mozilla/5.0..."` against `/` | **302** → `https://accounts.google.com/o/oauth2/v2/auth?...client_id=...&redirect_uri=.../.auth/login/google/callback&scope=openid+profile+email&state=...` |
| `curl -s https://.../health` | `200 {"db":"ok","csv":"ok"}` (excluded path) |
| `curl -s https://.../.auth/login/google` | `302` to Google's OAuth endpoint with our client_id |

**Acceptance.**
- [x] Browser User-Agent → 302 to Google login. API-style request → 401 (intentional — Container Apps suppresses login redirect for non-browser callers).
- [ ] Manual browser test: sign in as `robert.freire@gmail.com` → dashboard renders; different Google account is rejected at Google's consent screen (Testing mode allowlist) and additionally would 403 at the app guard if it ever got through. *Pending user check — capability is in place; the OAuth handshake is wired correctly per the smoke output above.*
- [x] `/health` stays open and reports `{"db":"ok","csv":"ok"}`.

**Reviewer focus (resolved).**
- Allowlist email-exact: enforced both at Google (Testing mode) and at app layer (`DASHBOARD_ALLOWED_EMAILS`). Belt and braces.
- `/health` excluded from auth proxy so monitoring still works.
- Container app secret references the KV item via managed identity — no plaintext secret in the container revision spec.

**Verification commands.**
```bash
FQDN=kaunitz-dev-dashboard-rfk1.orangebush-7e5af054.uksouth.azurecontainerapps.io
curl -i "https://$FQDN/" | head -1                                 # expect 401 (API) ...
curl -i -H "User-Agent: Mozilla/5.0" "https://$FQDN/" | head -3    # ... or 302 with browser UA
curl -s "https://$FQDN/health"                                     # 200 JSON
az containerapp auth show -g kaunitz-dev-rg -n kaunitz-dev-dashboard-rfk1 \
  --query "properties.{action:globalValidation.unauthenticatedClientAction, redirectTo:globalValidation.redirectToProvider, excluded:globalValidation.excludedPaths, google:identityProviders.google.registration}" -o json
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
8. **Forgotten raw-archive coverage when adding a new API source** — A.5.5 covers Odds API; if Pinnacle/Betfair/scraper integrations later bypass the `SnapshotArchive` wrapper, raw history quietly stops being captured. Mitigation: add a unit test that asserts every external HTTP call site for known sources goes through `SnapshotArchive`; document this contract in CLAUDE.md alongside A.4's Pi-safety contract.
9. **API key leakage in archived blobs** — if the redaction logic regresses, every archived snapshot leaks the API key. Mitigation: A.5.5 ships a unit test asserting the literal API key never appears in any sample blob body; reviewer focus item explicit on this.
