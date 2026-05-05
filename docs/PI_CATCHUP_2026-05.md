# Pi Catch-up + Azure A.10 Onboarding — Runbook

**This file is a one-time runbook. Delete it after the final acceptance check passes.** Status tracked in CLAUDE.md "Implementation status" → A.10.

---

## Context — read first, do not modify

This section answers two questions the user explicitly raised before handing off:

1. *Which fixes are deployed where?*
2. *What data are we actually getting, and where does it live?*

### Topology today

| Surface | Code state | Data state |
|---|---|---|
| **WSL (dev)** at `/home/rfreire/projects/bets/`, branch `main` | Up-to-date with `main` (post-PR #38, #39). Scanner / FDCO backfill / dashboard / `compare_strategies` all **refuse to run without `BETS_DB_WRITE=1`**. CSV writes have been removed from BetRepo. | All bets / paper_bets / fixtures / book_skill rows live in **dev DB** `kaunitz-dev-sql-uksouth-rfk1.database.windows.net / kaunitz`. CSVs under `logs/` are gone for live data; only operational state JSONs remain (`bankroll.json`, `notified.json`, `team_xg.json`, `model_signals.json`). |
| **Pi (prod)** at `robert@192.168.0.28:~/projects/bets/` | **HEAD unknown** — Pi has not been pulled in this sprint cycle. Phase 0 captures it. Critically: **Pi has not pulled `main` since A.9**, so Pi still has the pre-A.9 dual-write code (CSV + DB). Pulling `main` onto Pi *now* would break the cron because the scanner would refuse to run without env vars. | All bets / paper_bets live in **Pi-local CSVs** at `~/projects/bets/logs/`. None of this has ever been imported into any DB. The dev DB does not contain Pi data. |
| **Dev dashboard** (Azure Container App `kaunitz-dev-dashboard-rfk1`) | Reads dev DB only. | Shows the WSL test stream only. Has never shown Pi production data. |
| **Prod dashboard** | Does not exist. To be provisioned in Phase 1. | n/a |

### Recently-merged changes that affect Pi when it eventually pulls

| PR | Change | Pi impact when it pulls |
|---|---|---|
| #34 / #35 | Fixture calendar — `fixtures` table, `scripts/ingest_fixtures.py`, `FixtureRepo`. Mon 02:00 cron on WSL. | Dormant on Pi until env vars set. After Phase 4, Pi reads from prod DB via `FixtureRepo` automatically; no Pi-side ingest cron needed. |
| #36 | Pyodbc connect retry with backoff (cold-start handling). | Active when DB writes are enabled. |
| #37 | (Same as #36 — duplicate merge.) | Same. |
| #38 (S.1–S.4) | DB-only result + CLV backfill. `backfill_clv_from_fdco.py` queries pending rows from DB and writes `result`/`pnl`/`settled_at`/`pinnacle_close_prob`/`clv_pct` back. | **Critical:** Pi's current cron runs the *old* CSV-walking version of this script. Once Pi pulls, it switches to DB-driven, which means Pi's CSV-only history needs to have been imported to DB first or the backfill won't see those rows. |
| #39 (A.9) | CSV path decommissioned. Scanner / backfill / dashboard / `compare_strategies` exit 1 without `BETS_DB_WRITE=1`. | **Critical:** Pi's cron breaks until env vars are set. This is why Phase 4 sets env vars *before* Phase 6 pulls main. |
| 2026-05-05 (no PR yet) | OddsPapi Pinnacle close-odds resolver — new script `scripts/backfill_clv_from_oddspapi.py`, Mon+Wed 10:00 UTC cron on WSL, fills the FDCO Pinnacle gap (FDCO PSCH dropped mid-Jan 2026 after Pinnacle closed their public API on 2025-07-23). Also covers La Liga (paper_bets only). FDCO backfill cron rescheduled Mon → Mon+Wed at 09:00 to align. | **New Pi-side requirement:** prod needs its own OddsPapi free-tier key (separate quota from WSL's dev key — same dev/prod key separation pattern as `ODDS_API_KEY`). Phase 4c adds `ODDSPAPI_KEY` to Pi's `.env`. Phase 6c installs the cron entry. Without the key, the script exits with a clear error rather than dropping CLV silently — same fail-fast pattern as `BETS_DB_WRITE`. |

### What's still uncertain until Phase 0 runs

- The exact commit SHA on Pi.
- Whether Pi has any uncommitted local edits (e.g. `config.json` divergence — historically Pi has had a hand-edited books array).
- Pi's last successful cron run timestamp.
- Whether the Pi has working `pyodbc` + ODBC Driver 18 (probably not — never installed because Pi has been CSV-only).

Phase 0 captures all of this and writes it to a snapshot before any change.

---

## Bot execution rules

- **Execute phases in order.** Each phase has pre-conditions, steps, acceptance criteria, and abort conditions. If a phase's acceptance fails, **stop and surface to the user** — do not proceed.
- **Never `git pull` on Pi before Phase 6.** A.9 will break Pi's cron without env vars set.
- **Never delete Pi CSVs.** They are the only copy of prod data until Phase 3 imports them. Phase 7 covers archiving.
- **Branches.** Any Azure-side changes (e.g. infra-as-code if you choose to script it) go on `azure-A-10-<slug>`. Commits prefixed `A.10:`. Doc edits to CLAUDE.md / PLAN_AZURE land in the same branch.
- **`BLOB_ARCHIVE=1` on Pi is OUT OF SCOPE.** This runbook only sets `BETS_DB_WRITE=1`. Pi blob archival is a separate sprint.

---

## Phase 0 — Pre-flight inventory (no changes) — ~15 min

**Goal.** Capture Pi state before doing anything, so post-execution diff is clear and rollback is possible.

### Steps

```bash
# From WSL
mkdir -p /tmp/pi-snapshot-$(date +%Y-%m-%d)
SNAP=/tmp/pi-snapshot-$(date +%Y-%m-%d)

# 1. Pi git state
ssh robert@192.168.0.28 'cd ~/projects/bets && git rev-parse HEAD' > "$SNAP/pi-head.txt"
ssh robert@192.168.0.28 'cd ~/projects/bets && git status --short' > "$SNAP/pi-git-status.txt"
ssh robert@192.168.0.28 'cd ~/projects/bets && git log --oneline -10' > "$SNAP/pi-git-log.txt"

# 2. Compare Pi HEAD against current main
PI_HEAD=$(cat "$SNAP/pi-head.txt")
git -C ~/projects/bets log --oneline "$PI_HEAD..origin/main" > "$SNAP/pi-behind-main.txt"
# This shows commits Pi will get when it eventually pulls. Sanity-check there are
# no surprises — should be the PRs listed in the Context section above.

# 3. Pi cron
ssh robert@192.168.0.28 'crontab -l' > "$SNAP/pi-crontab.txt"

# 4. Pi data
ssh robert@192.168.0.28 'cd ~/projects/bets/logs && ls -la' > "$SNAP/pi-logs-listing.txt"
ssh robert@192.168.0.28 'cd ~/projects/bets/logs && wc -l bets.csv paper/*.csv 2>/dev/null' > "$SNAP/pi-row-counts.txt"
ssh robert@192.168.0.28 'cd ~/projects/bets/logs && tail -1 scan.log 2>/dev/null' > "$SNAP/pi-last-scan.txt"

# 5. Pi runtime — pyodbc availability
ssh robert@192.168.0.28 'python3 -c "import pyodbc; print(pyodbc.drivers())"' \
  > "$SNAP/pi-pyodbc.txt" 2>&1 || echo "(pyodbc not installed — Phase 4a will install)" >> "$SNAP/pi-pyodbc.txt"
```

### Acceptance

- [ ] All seven snapshot files exist and are non-empty.
- [ ] `pi-behind-main.txt` lists at least the A.9 commit (`ed3cbf2`) and the S.1–S.4 commit (`30334aa`). If it doesn't, Pi may have been pulled mid-sprint — surface to user.
- [ ] `pi-row-counts.txt` shows non-zero `bets.csv` row count. If zero, **abort** — there is no data to migrate, and the user needs to investigate why.

### Abort conditions

- Pi unreachable over SSH → confirm laptop is on the right network.
- `git status` on Pi shows uncommitted changes → surface to user; don't proceed until those are committed or stashed by the user.

---

## Phase 1 — Provision `kaunitz-prod-rg` — ~45 min

**Goal.** Stand up the prod Azure stack: RG, SQL server + DB, Key Vault. Container App for the dashboard is **deferred to Phase 7** so we don't spend it on the critical path.

**Pre-conditions.**
- Phase 0 acceptance met.
- WSL has `az` CLI authenticated to the subscription.
- A fresh password generated for the prod SQL admin (save in Bitwarden as **"Azure SQL — kaunitz prod DB"** before running).

### 1a. Resource group

```bash
az group create -n kaunitz-prod-rg -l uksouth \
  --tags env=prod project=kaunitz owner=rfreire
```

### 1b. SQL server + DB

```bash
PROD_SQL_PASSWORD=<paste from Bitwarden>

az sql server create -g kaunitz-prod-rg \
  -n kaunitz-prod-sql-uksouth-rfk1 \
  -l uksouth \
  --admin-user kaunitzadmin \
  --admin-password "$PROD_SQL_PASSWORD"

# Try free tier first
az sql db create -g kaunitz-prod-rg \
  -s kaunitz-prod-sql-uksouth-rfk1 \
  -n kaunitz \
  --use-free-limit \
  --free-limit-exhaustion-behavior AutoPause
```

If `--use-free-limit` fails (quota already used by dev), fall back to serverless:

```bash
az sql db create -g kaunitz-prod-rg \
  -s kaunitz-prod-sql-uksouth-rfk1 \
  -n kaunitz \
  --tier GeneralPurpose --family Gen5 --capacity 2 \
  --compute-model Serverless --auto-pause-delay 360 \
  --backup-storage-redundancy Local
```

### 1c. Firewall rules

```bash
az sql server firewall-rule create -g kaunitz-prod-rg \
  -s kaunitz-prod-sql-uksouth-rfk1 \
  -n AllowAzureServices --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0

# WSL (for migration + admin)
WSL_IP=$(curl -s https://api.ipify.org)
az sql server firewall-rule create -g kaunitz-prod-rg \
  -s kaunitz-prod-sql-uksouth-rfk1 \
  -n AllowWSL --start-ip-address "$WSL_IP" --end-ip-address "$WSL_IP"

# Pi (needed in Phase 4)
PI_IP=$(ssh robert@192.168.0.28 'curl -s https://api.ipify.org')
az sql server firewall-rule create -g kaunitz-prod-rg \
  -s kaunitz-prod-sql-uksouth-rfk1 \
  -n AllowPi --start-ip-address "$PI_IP" --end-ip-address "$PI_IP"

echo "WSL_IP=$WSL_IP"; echo "PI_IP=$PI_IP"   # save these for the snapshot dir
```

### 1d. Key Vault + secrets

```bash
az keyvault create -g kaunitz-prod-rg \
  -n kaunitz-prod-kv-rfk1 \
  -l uksouth

az keyvault secret set --vault-name kaunitz-prod-kv-rfk1 \
  -n sql-admin-password --value "$PROD_SQL_PASSWORD"

PROD_DSN="Driver={ODBC Driver 18 for SQL Server};Server=tcp:kaunitz-prod-sql-uksouth-rfk1.database.windows.net,1433;Database=kaunitz;Uid=kaunitzadmin;Pwd=${PROD_SQL_PASSWORD};Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;"

az keyvault secret set --vault-name kaunitz-prod-kv-rfk1 \
  -n sql-dsn --value "$PROD_DSN"
```

### 1e. Schema migration

```bash
export AZURE_SQL_DSN="$PROD_DSN"
python3 src/storage/migrate.py --dsn "$AZURE_SQL_DSN"
# expected: "+N tables"

python3 src/storage/migrate.py --dsn "$AZURE_SQL_DSN"
# expected on second run: "no changes"
```

### Acceptance

- [ ] `az sql db show -g kaunitz-prod-rg -s kaunitz-prod-sql-uksouth-rfk1 -n kaunitz --query status` returns `Online` (or `Paused`, both fine).
- [ ] `python3 -c "import pyodbc; pyodbc.connect('$PROD_DSN').execute('SELECT 1')"` from WSL returns without error.
- [ ] `python3 -c "import pyodbc; print([r[0] for r in pyodbc.connect('$PROD_DSN').execute('SELECT name FROM sys.tables').fetchall()])"` lists at least: `bets`, `paper_bets`, `fixtures`, `books`, `strategies`, `closing_lines`, `drift`, `book_skill`.

### Abort

- SQL server provisioning fails → check subscription quota; user may need to provision in a different region.

---

## Phase 2 — Backup Pi data — ~10 min

**Goal.** Defensive snapshot of Pi CSVs to a place that survives anything in Phases 3–6.

```bash
SNAP=/tmp/pi-snapshot-$(date +%Y-%m-%d)
rsync -av robert@192.168.0.28:~/projects/bets/logs/ "$SNAP/pi-logs/"

# Tar it
tar -C "$SNAP" -czf "$SNAP/pi-logs.tgz" pi-logs/

# Upload to prod blob (create container first if needed)
az storage account create -g kaunitz-prod-rg \
  -n kaunitzprodstrfk1 -l uksouth --sku Standard_LRS

az storage container create --account-name kaunitzprodstrfk1 \
  -n pi-csv-archive --auth-mode login

az storage blob upload --account-name kaunitzprodstrfk1 \
  --container-name pi-csv-archive \
  -f "$SNAP/pi-logs.tgz" \
  -n "pi-logs-$(date +%Y-%m-%d).tgz" \
  --auth-mode login
```

### Acceptance

- [ ] `pi-logs.tgz` exists locally, is at least 100KB.
- [ ] Blob upload completes; `az storage blob list --account-name kaunitzprodstrfk1 -c pi-csv-archive --auth-mode login` shows the file.

---

## Phase 3 — Import Pi historical CSVs into prod DB — ~20 min

**Goal.** Every Pi CSV row becomes a row in prod DB before Pi starts writing live.

The migrator was archived in PR #39 to `scripts/archive/migrate_csv_to_db.py`. It still works — the move was about discoverability, not deprecation.

```bash
SNAP=/tmp/pi-snapshot-$(date +%Y-%m-%d)
export AZURE_SQL_DSN="$PROD_DSN"   # same DSN as Phase 1
export BETS_DB_WRITE=1

# Sanity: dry-run first to see what would be inserted
python3 scripts/archive/migrate_csv_to_db.py \
  --bets-csv "$SNAP/pi-logs/bets.csv" \
  --paper-dir "$SNAP/pi-logs/paper/" \
  --dsn "$AZURE_SQL_DSN" \
  --dry-run

# Real run
python3 scripts/archive/migrate_csv_to_db.py \
  --bets-csv "$SNAP/pi-logs/bets.csv" \
  --paper-dir "$SNAP/pi-logs/paper/" \
  --dsn "$AZURE_SQL_DSN"
```

### Acceptance

```bash
# Row counts: DB should match CSVs (within 1–2 of duplicates skipped)
python3 -c "
import pyodbc, csv
c = pyodbc.connect('$PROD_DSN')
db_bets = c.execute('SELECT COUNT(*) FROM bets').fetchone()[0]
db_paper = c.execute('SELECT COUNT(*) FROM paper_bets').fetchone()[0]
csv_bets = sum(1 for _ in open('$SNAP/pi-logs/bets.csv')) - 1
print(f'bets: csv={csv_bets} db={db_bets}')
print(f'paper_bets: db={db_paper}')
"
```

- [ ] DB `bets` count is within 5 of CSV `bets.csv` count (small delta acceptable from header / blank lines / dedup).
- [ ] DB `paper_bets` count > 0.
- [ ] `python3 scripts/archive/migrate_csv_to_db.py --dsn ... --dry-run` re-run reports zero new inserts (idempotency check).

### Abort

- DB count is dramatically smaller than CSV count → migrator may have hit an error mid-run; check stderr, fix, re-run (idempotent).

---

## Phase 4 — Set Pi env vars + open firewall — ~15 min

**Goal.** Pi starts dual-writing to prod DB **without changing Pi code yet**. Pi is on pre-A.9 code that still has dual-write paths, so simply setting the env vars activates DB writes alongside the existing CSV writes.

This phase is the riskiest — it's the moment Pi starts touching the network mid-cron. Pi continues writing CSVs, so worst-case rollback is just unsetting the env vars.

### 4a. Install ODBC Driver 18 on Pi if needed

If Phase 0 captured `pi-pyodbc.txt` showing `pyodbc not installed`:

```bash
ssh robert@192.168.0.28 << 'EOF'
sudo apt-get update
sudo apt-get install -y unixodbc-dev
# Microsoft repo for ODBC Driver 18 — Raspberry Pi OS Trixie / Debian 13:
curl https://packages.microsoft.com/keys/microsoft.asc | sudo gpg --dearmor -o /usr/share/keyrings/microsoft.gpg
echo "deb [arch=arm64 signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/13/prod trixie main" | sudo tee /etc/apt/sources.list.d/mssql-release.list
sudo apt-get update
sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18
pip install pyodbc
python3 -c "import pyodbc; print('OK', pyodbc.drivers())"
EOF
```

### 4b. Smoke-test Pi → prod DB connection

```bash
ssh robert@192.168.0.28 << EOF
python3 -c "
import pyodbc
c = pyodbc.connect('$PROD_DSN')
print('connected:', c.execute('SELECT 1').fetchone()[0])
"
EOF
```

If this fails: most likely the firewall rule from Phase 1c didn't pin the right Pi IP (e.g. ISP rotated). Re-run the Pi-IP detection and `firewall-rule create`.

### 4c. Add env vars to Pi `.env`

**Pre-step:** generate a **prod** OddsPapi key separate from WSL's dev key. Sign up a second account at https://oddspapi.io/en/sign-up under a prod-mailbox alias (e.g. `robert.freire+prod@gmail.com`); free tier 250 req/mo, no credit card. Copy the key from `/en/account` and use it as `<PROD_ODDSPAPI_KEY>` below. Same dev/prod separation pattern as `ODDS_API_KEY` — never reuse the WSL key on Pi (would split one quota across both machines and trip rate limits).

```bash
# Append to Pi's .env. Use a heredoc to avoid escape pain.
ssh robert@192.168.0.28 "cat >> ~/projects/bets/.env << 'EOF'

# A.10: Azure prod DB writes (added $(date +%Y-%m-%d))
BETS_DB_WRITE=1
AZURE_SQL_DSN=${PROD_DSN}
# OddsPapi Pinnacle close-odds resolver (Phase 6c installs the cron)
ODDSPAPI_KEY=<PROD_ODDSPAPI_KEY>
EOF"

# Verify (the OddsPapi key value should be redacted manually before pasting output anywhere)
ssh robert@192.168.0.28 'tail -7 ~/projects/bets/.env'
```

### 4d. Trigger ad-hoc scan to verify dual-write

```bash
ssh robert@192.168.0.28 'cd ~/projects/bets && export $(grep -v "^#" .env | xargs) && python3 scripts/scan_odds.py --sports football --dry-run' 2>&1 | tail -20
```

Expect:
- `[scan] Dual-write mode: CSV + Azure SQL` (Pi is still on pre-A.9 code).
- No `pyodbc` errors.

### Acceptance

- [ ] Pi's next scheduled cron (next slot in Phase 0's `pi-crontab.txt`) writes rows to **both** CSVs and prod DB.
- [ ] `python3 -c "import pyodbc; c = pyodbc.connect('$PROD_DSN'); print(c.execute('SELECT COUNT(*) FROM bets WHERE scanned_at >= CAST(SYSUTCDATETIME() AS DATE)').fetchone()[0])"` returns ≥1 after Pi's first post-Phase-4 cron.
- [ ] Pi CSVs continue growing (run `wc -l ~/projects/bets/logs/bets.csv` before and after a cron — count must increase).

### Rollback

- If anything goes wrong after Phase 4c: `ssh robert@192.168.0.28 'sed -i "/^BETS_DB_WRITE=/d; /^AZURE_SQL_DSN=/d" ~/projects/bets/.env'`. Pi reverts to CSV-only.

---

## Phase 5 — Soak (24–48 hours, no action) — wait

**Goal.** Watch two consecutive Pi crons land in DB cleanly before any code change.

Monitor at a glance:

```bash
# Run from WSL
python3 -c "
import pyodbc
c = pyodbc.connect('$PROD_DSN')
print('Pi rows by day:')
for r in c.execute('''
  SELECT CAST(scanned_at AS DATE) AS day, COUNT(*)
  FROM bets GROUP BY CAST(scanned_at AS DATE) ORDER BY day DESC
''').fetchmany(7):
  print(' ', r[0], r[1])
"
```

### Acceptance (do not proceed until ALL hold)

- [ ] At least 2 cron-driven scans on Pi have landed rows in DB.
- [ ] CSV row counts and DB row counts (filtered to those days) match within 5.
- [ ] Mon 08:10 UTC audit-invariants GH Actions workflow has run since Phase 4c and passed.

### Abort during soak

- DB rows missing for a Pi cron → check Pi's cron mail for pyodbc errors; verify firewall hasn't rotated; verify SQL DB hasn't auto-paused for too long.

---

## Phase 6 — Pull `main` onto Pi (A.9 alignment) — ~15 min

**Goal.** Pi runs identical code to WSL. After this, Pi is DB-only — CSVs are no longer written.

**Pre-conditions.**
- Phase 5 acceptance met.
- Pi env vars confirmed set (re-check with `ssh robert@192.168.0.28 'grep BETS_DB_WRITE ~/projects/bets/.env'`).
- A pre-pull SHA captured for rollback: `PRE_PULL_SHA=$(cat $SNAP/pi-head.txt)`.

### Steps

```bash
ssh robert@192.168.0.28 << EOF
cd ~/projects/bets
PRE_PULL_SHA=\$(git rev-parse HEAD)
echo "\$PRE_PULL_SHA" > .pre-A10-sha   # rollback marker

git pull
pip install -r requirements.txt   # in case Azure SDK pins changed

# Sanity: scanner refuses without env (proving A.9 guard works)
python3 scripts/scan_odds.py --sports football --dry-run 2>&1 | head -5
# expected: ERROR + sys.exit(1) IF env not loaded
# (the cron loads env explicitly, so this is informational only)

# With env: should run
export \$(grep -v "^#" .env | xargs)
python3 scripts/scan_odds.py --sports football --dry-run 2>&1 | tail -5
# expected: "[scan] DB mode: Azure SQL" — no errors
EOF
```

### 6a. Add Mon 02:00 fixture-calendar ingest cron — NO

After Phase 4c sets `BETS_DB_WRITE=1` on Pi, `FixtureRepo` is active. The Mon 02:00 ingest stays on **WSL only** (writes to dev DB) — Pi reads `fixtures` from prod DB but does not need to ingest. Verify:

```bash
ssh robert@192.168.0.28 "cd ~/projects/bets && export \$(grep -v '^#' .env | xargs) && python3 -c 'from src.data.fixture_calendar import calendar_available; print(calendar_available())'"
# expected: True (prod DB is reachable and has fixtures)
```

If this returns False, the fixtures table in prod DB is empty — Phase 1e ran the schema but no ingest. Run a one-shot WSL → prod ingest:

```bash
export AZURE_SQL_DSN="$PROD_DSN"
export BETS_DB_WRITE=1
python3 scripts/ingest_fixtures.py
unset AZURE_SQL_DSN BETS_DB_WRITE
```

### 6b. Add B.3 `compute_book_skill` cron on Pi (now active because BETS_DB_WRITE=1)

```bash
ssh robert@192.168.0.28 << 'EOF'
( crontab -l 2>/dev/null
  echo "# A.10: book_skill compute — Mon 09:05 UTC (after Mon 08:00 CLV backfill)"
  echo "5 9 * * 1 cd ~/projects/bets && set -a && . ./.env && set +a && python3 scripts/compute_book_skill.py 2>&1" ) | crontab -
crontab -l | grep book_skill
EOF
```

### 6c. Add OddsPapi Pinnacle close-odds resolver cron on Pi

WSL runs this Mon+Wed 10:00 UTC; Pi mirrors. Pre-condition: `ODDSPAPI_KEY` set on Pi (Phase 4c). The script defaults to "last 7 days" so no date flags needed. Idempotent — re-runs over the same window are no-ops. Audit CSVs land under `~/projects/bets/logs/backfill/oddspapi/<run_iso>/`.

```bash
ssh robert@192.168.0.28 << 'EOF'
# Idempotent install (skip if already present)
if crontab -l 2>/dev/null | grep -qF 'backfill_clv_from_oddspapi.py'; then
  echo "ALREADY_PRESENT"
else
  ( crontab -l 2>/dev/null
    echo "# A.10: OddsPapi Pinnacle close-odds backfill — Mon+Wed 10:00 UTC (1h after FDCO settlement)"
    echo "0 10 * * 1,3 cd ~/projects/bets && set -a && . ./.env && set +a && python3 scripts/backfill_clv_from_oddspapi.py --commit >> logs/backfill_clv_oddspapi.log 2>&1" ) | crontab -
  echo "INSTALLED"
fi
crontab -l | grep oddspapi
EOF
```

Smoke-test the key + script before relying on cron:

```bash
ssh robert@192.168.0.28 << 'EOF'
cd ~/projects/bets
set -a && . ./.env && set +a
# Cheap auth check — single tournaments call (1 request)
curl -s -o /dev/null -w "tournaments HTTP %{http_code}\n" \
  "https://api.oddspapi.io/v4/tournaments?sportId=10&apiKey=$ODDSPAPI_KEY"
# Dry-run on yesterday's window — no commit, just verify pipeline
python3 scripts/backfill_clv_from_oddspapi.py 2>&1 | tail -16
EOF
```

Expect: `HTTP 200` from the auth check, and a clean dry-run summary with `requests issued ≤ 20` and `no_fixture_match` either zero or only matching the OddsPapi 3-month historical cutoff (~3 months back from run date). If `HTTP 401/403`, the key is invalid — regenerate at https://oddspapi.io/en/account. If the dry-run reports many `no_fixture_match` rows, check team-name normalisation against the cached fixtures JSON in `logs/cache/oddspapi/fixtures/` (likely a new German/French club name we haven't aliased yet — extend `_ALIAS` in the script).

### Acceptance for 6c

- [ ] `ssh robert@192.168.0.28 'crontab -l | grep oddspapi'` shows the new entry.
- [ ] First Mon-after-Phase-6 cron run on Pi writes a `logs/backfill/oddspapi/<run_iso>/audit.csv` and `clv_pct` populates for that day's settled bets in prod DB.
- [ ] WSL and Pi both have OddsPapi entries in their respective crontabs (one quota each, never sharing the key).

### Acceptance

- [ ] `ssh ... 'cd ~/projects/bets && git rev-parse HEAD'` matches origin/main.
- [ ] First post-pull cron run on Pi writes to DB (`SELECT COUNT(*) FROM bets WHERE scanned_at >= ...` increments).
- [ ] No new `.csv` files appear in `~/projects/bets/logs/` after Phase 6 (CSV write paths are gone in A.9). Existing files frozen — that's fine.
- [ ] `pi-logs/scan.log` shows `[scan] DB mode: Azure SQL` line, no pyodbc errors.

### Rollback

```bash
ssh robert@192.168.0.28 << EOF
cd ~/projects/bets
git reset --hard \$(cat .pre-A10-sha)
EOF
# Pi reverts to pre-A.9 code; dual-write resumes; CSVs grow again.
```

---

## Phase 7 — Cleanup + (optional) prod dashboard — ~30 min

### 7a. Mark A.10 done

Edit `CLAUDE.md` "Implementation status" — replace the A.10 row with:

```
| Phase 9 / A.10 (`kaunitz-prod-rg` + Pi onboarding) | ✅ done <YYYY-MM-DD> — Pi writes to prod DB; identical code to WSL |
```

Edit `docs/PLAN_AZURE_2026-05.md` phase tracker similarly. Add the prod resource names to a "Prod stack" subsection if not already there.

### 7b. Delete this file

```bash
git rm docs/PI_CATCHUP_2026-05.md
```

Memory pointers (`memory/MEMORY.md`) referencing this doc need cleanup too — search for `PI_CATCHUP` and update.

### 7c. (Optional) Provision prod dashboard

Reuse the dev ACR image (`kaunitzdevacrrfk1.azurecr.io/dashboard:latest`) — there's nothing prod-specific in the image, the connection string is wired via env vars.

```bash
az containerapp env create -g kaunitz-prod-rg -n kaunitz-prod-env -l uksouth

az containerapp create -g kaunitz-prod-rg \
  -n kaunitz-prod-dashboard-rfk1 \
  --environment kaunitz-prod-env \
  --image kaunitzdevacrrfk1.azurecr.io/dashboard:latest \
  --registry-server kaunitzdevacrrfk1.azurecr.io \
  --ingress external --target-port 5000 \
  --min-replicas 1 --max-replicas 1 \
  --cpu 0.25 --memory 0.5Gi \
  --system-assigned

IDENTITY=$(az containerapp show -g kaunitz-prod-rg -n kaunitz-prod-dashboard-rfk1 \
  --query "identity.principalId" -o tsv)
ACR_ID=$(az acr show -n kaunitzdevacrrfk1 --query id -o tsv)

az role assignment create --assignee "$IDENTITY" --role AcrPull --scope "$ACR_ID"
az keyvault set-policy -n kaunitz-prod-kv-rfk1 \
  --object-id "$IDENTITY" --secret-permissions get list

DSN_SECRET_ID=$(az keyvault secret show --vault-name kaunitz-prod-kv-rfk1 -n sql-dsn --query id -o tsv)
az containerapp secret set -g kaunitz-prod-rg -n kaunitz-prod-dashboard-rfk1 \
  --secrets "sql-dsn=keyvaultref:${DSN_SECRET_ID},identityref:system"

az containerapp update -g kaunitz-prod-rg -n kaunitz-prod-dashboard-rfk1 \
  --set-env-vars "BETS_DB_WRITE=1" "AZURE_SQL_DSN=secretref:sql-dsn"
```

Wire Google OIDC the same way A.7 did for dev. **Memory note `project_container_apps_auth`** has the gotchas — `az containerapp auth update` has no `--allowed-principals` flag; allowlist is enforced at app level.

### 7d. (Optional, post-soak ≥1 week) Archive Pi CSVs and stop writing them

Pi is on A.9 code now; CSVs have stopped growing. The historical CSVs are already imported (Phase 3) and backed up to blob (Phase 2). Safe to delete Pi-side:

```bash
ssh robert@192.168.0.28 'cd ~/projects/bets/logs && tar -czf /tmp/pi-logs-final.tgz bets.csv paper/ closing_lines.csv drift.csv 2>/dev/null && rm -f bets.csv && rm -rf paper/ && rm -f closing_lines.csv drift.csv'
```

Don't do this in the same sprint as A.10 lands. Wait at least one full weekend cycle of clean DB writes.

---

## What this runbook deliberately does NOT do

- **Doesn't enable `BLOB_ARCHIVE=1` on Pi.** Raw API archival from Pi is a separate sprint (provision a Pi-accessible blob container, decide on lifecycle).
- **Doesn't migrate Pi state JSONs (`bankroll.json`, `notified.json`, etc.) to DB.** That's the F.* file-decommission sprint (`docs/PLAN_FILE_DECOMMISSION_2026-05.md`).
- **Doesn't unify the Pi cron with WSL cron.** Pi keeps its `refresh_xg.py` and `research_scan.py` slots; WSL keeps the `ingest_fixtures.py` slot. Cron divergence stays per `project_dev_prod_split` memory.
- **Doesn't touch dev DB.** The dev stack continues running independently as a parallel test stream.
