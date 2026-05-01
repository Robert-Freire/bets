# Azure Migration Plan — 2026-05

Phased migration from local CSV-on-Pi to a SQL-Server-Express-backed Flask dashboard hosted on Azure. Supersedes the earlier `docs/PI_AZURE_SETUP.md` (deleted on 2026-05-01) which assumed Blob-storage-only and a static read-only dashboard.

**Driving question.** Pi cron is in production (Phase 9a ✅ done 2026-05-01). The two open questions: (1) where does data live now that the system is no longer a single-host setup? (2) how does the user view/settle bets from a phone, anywhere? This plan answers both with one consistent stack: Azure SQL DB + Azure App Service.

**Bot execution protocol.** Follow the same "Picking up a phase / During implementation / Commit conventions / PR conventions / Verifier bot protocol / Failure handling / Branch hygiene" rules from `docs/PLAN_RESEARCH_2026-04.md` §"How to use this doc". Branches: `azure-A-X-<short-slug>`. Commit prefix: `A.X:`. Always link `Refs: docs/PLAN_AZURE_2026-05.md#phase-A-X`.

---

## Architecture target

```
Raspberry Pi (home network — production cron only)
  └── scan_odds.py, closing_line.py, refresh_xg.py, research_scan.py (cron)
        └── writes via pyodbc to →
                                    Azure SQL Database (single source of truth)
                                    schema: bets, fixtures, books, closing_lines,
                                            drift, paper_bets, strategies
                                            ↑
                                    reads ↑                    ↓ writes (settle bet)
                                    Azure App Service (F1 free tier)
                                    app.py — Flask dashboard
                                    Easy Auth (Google) for public URL
```

WSL stays as the dev environment. Local manual scans either (a) write to the same Azure SQL DB or (b) use a local SQLite scratch DB (decision deferred to A.4).

---

## Architecture decisions (fixed up-front to avoid bikeshedding mid-execution)

| Question | Decision | Why |
|---|---|---|
| Where does the DB live? | **Azure SQL Database Free tier** (fallback: Basic ~£5/mo if Free quota exhausted) | Managed, automated backups, scales if needed, no patching. Self-hosted SQL Express on a VM saves £0 but adds ops burden. |
| What flavour of SQL? | **Azure SQL DB** (T-SQL, MSSQL-flavoured) — *not* SQL Server Express on a VM | Same engine family as the original "SQL Server Express" intent; user gets the cloud benefits. Phase-6 doc copy still says "SQL Server Express" for continuity. |
| Where does the web app live? | **Azure App Service F1 (free)** | Always-on Linux Python runtime; deploy via `az webapp up`. If F1 cold starts hurt UX, escalate to B1 (~£10/mo) in A.7. |
| Auth on public dashboard? | **App Service Easy Auth with Google OIDC** (one allowed email: `robert.freire@gmail.com`) | One-click in portal; no auth code in app.py. Falls back to HTTP Basic Auth (1 LOC) if Easy Auth setup blocks. |
| Pi → Azure SQL transport | TCP 1433 outbound, TLS required (Azure default), SQL auth (username/password from Azure Key Vault → env var on Pi) | TLS+SQL auth is the simplest path. AAD auth from a Pi is awkward (no AAD identity). |
| Region | **UK South** | Lowest-latency UK region for the Pi; matches the user's location and bookmaker fixture timezones. |
| Migration style | **Dual-write transition** (A.4–A.8): scanner writes both CSV and DB; dashboard reads DB-first with CSV fallback. Cut over once 1 week of clean DB-only operation. | Lets us roll back cheaply if DB writes fail. |
| Schema primary key | **`uniqueidentifier` (UUID)** for `bets.id`, `paper_bets.id` | Closes the historical "Phase 6: SQLite + UUIDs" intent without needing app-side coordination of integer sequences. |
| Historical CSV data | Backfilled in A.3 (one-shot importer); CSVs archived to `logs/csv-archive/` after A.8 cutover; deleted in A.9 | Preserves the ~6 months of accumulated bets/closing-lines/drift/paper data. |

---

## Phase status tracker

| Phase | Title | Status | Depends on |
|---|---|---|---|
| A.0 | Provision Azure account + resource group | pending | — |
| A.1 | Stand up Azure SQL Database (Free tier) | pending | A.0 |
| A.2 | Schema DDL + idempotent migrations runner | pending | A.1 |
| A.3 | CSV → DB importer (one-shot, idempotent) | pending | A.2 |
| A.4 | Storage layer + dual-write in scanner | pending | A.2 |
| A.5 | Dashboard reads DB-first with CSV fallback | pending | A.2, A.4 |
| A.6 | Provision App Service + deploy `app.py` | pending | A.5 |
| A.7 | Easy Auth (Google OIDC) on dashboard | pending | A.6 |
| A.8 | Cutover: DB-only, archive CSVs | pending | A.7 + 1 week stable A.4/A.5 |
| A.9 | Decommission CSV path entirely | pending | A.8 + 1 week stable |

---

## Cost estimate

| Service | Cost/month |
|---|---|
| Azure SQL DB (Free tier) | £0.00 |
| App Service F1 (free) | £0.00 |
| Outbound bandwidth (well within free) | £0.00 |
| Azure AD / Easy Auth | £0.00 |
| **Total during/after migration** | **£0.00** |
| Fallback if Free SQL quota tight | + ~£5/mo for Basic |
| Fallback if F1 cold-starts hurt | + ~£10/mo for B1 |

Azure free credit (~£150) covers any escalations for the first year.

---

## Phase A.0 — Provision Azure account + resource group

**Goal.** Single Azure resource group `bets-rg` in UK South, owned by `robert.freire@gmail.com`'s Azure account. All future resources land here for one-click teardown.

**Tasks.**
1. Sign up for Azure free account (£150 credit) at portal.azure.com if not already.
2. `az login` from WSL.
3. `az group create -n bets-rg -l uksouth`.

**Acceptance.**
- [ ] `az group show -n bets-rg` returns the group with `provisioningState: Succeeded`.
- [ ] Resource group visible at portal.azure.com under the user's subscription.

**Reviewer focus.** None (provisioning only).

**Verification commands.**
```bash
az account show --query "{name:name, user:user.name}" -o table  # confirm logged-in user
az group list -o table | grep bets-rg                            # confirm group exists
```

---

## Phase A.1 — Stand up Azure SQL Database (Free tier)

**Goal.** A working Azure SQL DB instance reachable from the Pi over TCP 1433+TLS.

**Tasks.**
1. `az sql server create -g bets-rg -n bets-sql-uksouth-<random> -l uksouth --admin-user betsadmin --admin-password <generated, store in Bitwarden>`.
2. `az sql db create -g bets-rg -s bets-sql-uksouth-<random> -n bets --tier GeneralPurpose --family Gen5 --capacity 2 --compute-model Serverless --auto-pause-delay 60 --backup-storage-redundancy Local` (or `--use-free-limit` if the free-tier flag is available in current az CLI; check `az sql db create --help`).
3. Firewall: `az sql server firewall-rule create` to allow (a) Pi's public IP, (b) WSL's public IP, (c) Azure services (`0.0.0.0` rule with name `AllowAzureServices`).
4. On Pi: install ODBC driver (`sudo apt install -y unixodbc-dev` + Microsoft repo for `msodbcsql18`).
5. Create `bets-rg`-scoped Azure Key Vault `bets-kv-<random>`; store SQL admin password as secret `sql-admin-password`. (Phase A.4 will pull from Key Vault into Pi env.)

**Acceptance.**
- [ ] From Pi: `python3 -c "import pyodbc; conn = pyodbc.connect(<conn_str>); print(conn.execute('SELECT 1').fetchone())"` returns `(1,)`.
- [ ] Key Vault secret `sql-admin-password` exists and is fetchable via `az keyvault secret show`.
- [ ] Bitwarden has a new entry `Azure SQL — bets DB` with admin user/password/connection string.

**Reviewer focus.**
- Server name must include random suffix (DNS-globally-unique requirement).
- Firewall rules — confirm Pi IP rule is present and not overly permissive (no `0.0.0.0–255.255.255.255` for client IPs).
- Free tier confirmation: `az sql db show ... --query "currentSku"` should reflect free-tier pricing or auto-paused serverless.

**Verification commands.**
```bash
az sql db show -g bets-rg -s bets-sql-uksouth-<random> -n bets --query "{name:name, status:status, sku:currentSku}" -o json
az sql server firewall-rule list -g bets-rg -s bets-sql-uksouth-<random> -o table
ssh robert@192.168.0.28 'python3 -c "import pyodbc; print(pyodbc.drivers())"'  # expect msodbcsql18 in list
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

## Phase A.3 — CSV → DB importer (one-shot, idempotent)

**Goal.** Backfill all historical data from `logs/*.csv` and `logs/paper/*.csv` into the new DB.

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

## Phase A.4 — Storage layer + dual-write in scanner

**Goal.** Production scanner writes to both CSV (existing) and DB (new) on every scan. Env flag `BETS_DB_WRITE` gates the new path.

**Tasks.**
1. New module `src/storage/repo.py` with `BetRepo` class: `add_bet(...)`, `add_paper_bet(...)`, `add_closing_line(...)`, `add_drift_snapshot(...)`. CSV writer and DB writer behind a common interface.
2. Wire into `scripts/scan_odds.py` and `scripts/closing_line.py` — both now call `repo.add_*` instead of writing CSVs directly.
3. Default config: CSV write always on; DB write controlled by `BETS_DB_WRITE=1` env var. Pi `.env` adds the flag + `AZURE_SQL_DSN`.
4. Connection pulled from Azure Key Vault via `az keyvault secret show` at boot (cache for process lifetime).
5. Add `tests/test_repo_dual_write.py` — confirms a single `add_bet` writes one CSV row and one DB row, both with same UUID.

**Acceptance.**
- [ ] Smoke scan on Pi (with `BETS_DB_WRITE=1`) appends a row to `logs/bets.csv` AND inserts a row into `bets` table; UUIDs match.
- [ ] With `BETS_DB_WRITE=0`, only CSV is written (current behaviour preserved).
- [ ] No double-writes on cron retries (covered by upsert in repo layer).

**Reviewer focus.**
- Failure isolation: if DB insert fails, scanner still writes CSV and logs the error. Don't block the scan on DB outage.
- Connection re-use: open one pyodbc connection per scan run, not per row.
- Key Vault token caching: don't re-authenticate per scan (slow + costly).

**Verification commands.**
```bash
ssh robert@192.168.0.28 'cd ~/projects/bets && export $(cat .env) && BETS_DB_WRITE=1 .venv/bin/python3 scripts/scan_odds.py --sports football 2>&1 | tail -20'
# Expect: scan completes; check both CSV and DB for new rows with same kickoff/home/away/side/book.
ssh robert@192.168.0.28 'tail -5 ~/projects/bets/logs/bets.csv | cut -d, -f1-6'
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
1. `az appservice plan create -g bets-rg -n bets-plan --sku F1 --is-linux`.
2. `az webapp create -g bets-rg -p bets-plan -n bets-dashboard-<random> --runtime "PYTHON:3.11"`.
3. Configure app settings: `AZURE_SQL_DSN` (referencing Key Vault secret), `AZURE_MODE=true`.
4. Deploy: `az webapp up -n bets-dashboard-<random> -g bets-rg --runtime "PYTHON:3.11"`.
5. Confirm Application Settings → Identity → System-assigned managed identity ON; grant it `get` on the Key Vault secret.
6. Smoke-test the public URL; check cold-start latency. If F1 cold starts > 10s, escalate decision to B1 in A.7.

**Acceptance.**
- [ ] `curl https://bets-dashboard-<random>.azurewebsites.net/health` returns `{"db":"ok"}`.
- [ ] Dashboard renders bet history from DB (matches Pi-side data).
- [ ] App Service log stream shows no startup errors.

**Reviewer focus.**
- Managed identity (not connection-string-in-app-setting) must be the auth path to Key Vault.
- App must NOT have ODDS_API_KEY (only DB DSN — odds fetching stays on Pi).
- Confirm public dashboard does not expose any settle/admin endpoints without auth (next phase).

**Verification commands.**
```bash
az webapp show -g bets-rg -n bets-dashboard-<random> --query "{state:state, defaultHostName:defaultHostName}" -o json
curl -s https://bets-dashboard-<random>.azurewebsites.net/health
az webapp log tail -g bets-rg -n bets-dashboard-<random>  # interactive — confirm no errors
```

---

## Phase A.7 — Easy Auth (Google OIDC) on dashboard

**Goal.** Public URL requires Google sign-in; only `robert.freire@gmail.com` is authorized.

**Tasks.**
1. Portal: App Service → Authentication → Add identity provider → Google.
2. Set up Google OAuth client at console.cloud.google.com (OAuth 2.0 Client ID, Web app, redirect URI `https://bets-dashboard-<random>.azurewebsites.net/.auth/login/google/callback`).
3. Configure App Service: "Require authentication" + "Allowed identities" → restrict to user's email.
4. **Decision branch (document in PR body):** if Easy Auth setup hits a snag (Google verification, SP issues), fall back to HTTP Basic Auth: 1 LOC in `app.py` checking `request.authorization.username == 'robert' and request.authorization.password == os.environ['BASIC_AUTH_PASS']`. Store `BASIC_AUTH_PASS` in Bitwarden + App Settings.

**Acceptance.**
- [ ] `curl -i https://bets-dashboard-<random>.azurewebsites.net/` returns 302 to Google login (or 401 with Basic Auth fallback).
- [ ] Browser test: sign in as authorized email → dashboard renders. Sign in as different account → 403.
- [ ] Pi → App Service link still works (Pi calls public URL; should be allowed via internal allowlist, OR the dashboard doesn't need this).

**Reviewer focus.**
- Allowlist must be email-exact; "anyone with a Google account" is unacceptable.
- /health endpoint should remain unauth (for monitoring).
- Confirm no auth bypass via direct DB/blob access from internet.

**Verification commands.**
```bash
curl -i https://bets-dashboard-<random>.azurewebsites.net/ | head -1  # expect 302 or 401
curl -s https://bets-dashboard-<random>.azurewebsites.net/health     # should still return 200
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
az sql db show-deleted -g bets-rg -s bets-sql-uksouth-<random> 2>/dev/null  # confirm restore-from-deleted available
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

## Out of scope

- **Multi-tenant auth** — only one user (`robert.freire@gmail.com`) for the foreseeable future.
- **Real-time streaming** — Pi scans on cron, dashboard polls; no WebSockets.
- **DB-level row encryption** — Azure SQL is encrypted at rest by default; no PII in the schema beyond bet history.
- **Multi-region failover** — single UK South region is enough.
- **Betfair API auto-placement** — that's Phase 8 in `CLAUDE.md`, runs in parallel with Azure but not part of this plan.

---

## Cross-cutting risks

1. **Pi → Azure SQL connectivity outage** mid-scan → handled by A.4's failure-isolation (CSV stays as fallback during dual-write; alert via ntfy if DB writes fail >5 consecutive scans).
2. **Free-tier SQL quota exhaustion** mid-month → escalate to Basic (~£5/mo) per A.1's decision table.
3. **Easy Auth + Google verification delays** → A.7's documented Basic Auth fallback covers this.
4. **CSV → DB importer non-deterministic UUIDs** → A.3's deterministic-UUID rule prevents reimport duplication; verified in Acceptance.
5. **Dashboard cold-start UX** → measured in A.6; B1 escalation path exists.
