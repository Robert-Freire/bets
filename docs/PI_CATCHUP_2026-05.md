# Pi Catch-up + Azure Onboarding Runbook — post-weekend 2026-05

One-time runbook. Delete after execution.

**Timing:** do this Monday morning after the weekend scan data has landed, so Pi's historical CSVs are complete before the prod-DB import.

---

## What needs doing

| Item | Type | Source doc |
|---|---|---|
| `git pull` + verify edge formula fix | Code sync | PR #23 |
| B.3 cron (compute_book_skill) | Cron | PR #32 |
| Stand up `kaunitz-prod-rg` + prod SQL DB | Azure A.10 step 1 | `PLAN_AZURE_2026-05.md` §A.10 |
| Deploy prod dashboard (Container App) | Azure A.10 step 1 | same |
| Open Pi firewall on prod SQL | Azure A.10 step 2 | same |
| Set Pi env vars for DB write | Azure A.10 step 2 | same |
| Run schema migration against prod DB | Azure A.10 step 2 | same |
| Import Pi historical CSVs into prod DB | Azure A.10 step 2 | same |
| Verify Pi rows appear in prod dashboard | Verification | same |

---

## Part 1 — Code sync on Pi

```bash
cd ~/projects/bets
git pull
pip install -r requirements.txt   # no new packages; harmless re-run
```

**Verify config.json books array landed (PR #25):**

```bash
python3 -c "from src.config import load_config; c = load_config(); print(len(c.get('books', [])), 'books')"
# expect: 20 books
```

If 0, Pi has a locally-edited `config.json` that predates PR #25 — copy the `books` array from WSL.

**Verify edge formula fix (PR #23) — critical:**

Pi has been filtering/sizing bets with the wrong edge formula since before the cutover.
Dry-run a scan to confirm flagged bets show edge values in the expected 2–8% range:

```bash
export $(cat .env) && python3 scripts/scan_odds.py --sports football --dry-run 2>&1 | grep -E "edge=|flagged|value bet"
```

No data to fix retroactively — past bets are recorded as-is. Going forward the formula is correct.

---

## Part 2 — B.3 cron on Pi

Without `BETS_DB_WRITE=1` + `BLOB_ARCHIVE=1`, `compute_book_skill.py` prints rows but doesn't persist.
Add it now so it runs after A.10 activates DB write on Pi — it will auto-persist once the env vars are set.

```bash
# run on Pi:
( crontab -l; echo "# book_skill compute — Monday 09:05 BST (after CLV backfill)"; echo "5 9 * * 1 cd ~/projects/bets && set -a && . ./.env && set +a && python3 scripts/compute_book_skill.py >> logs/book_skill.log 2>&1" ) | crontab -
crontab -l | grep book_skill   # verify
```

---

## Part 3 — Stand up `kaunitz-prod-rg` (Azure A.10 step 1)

Run from WSL (the `az` CLI is there). All naming follows the dev pattern with `prod` substituted.

### 3a. Resource group

```bash
az group create -n kaunitz-prod-rg -l uksouth \
  --tags env=prod project=kaunitz owner=rfreire
```

### 3b. SQL server + DB

Use `--use-free-limit` here — this is the once-per-subscription free offer, reserved for prod per the architecture decision in `PLAN_AZURE_2026-05.md`.

```bash
# Generate a password, save it in Bitwarden as "Azure SQL — kaunitz prod DB" before running
PROD_SQL_PASSWORD=<generate>

az sql server create -g kaunitz-prod-rg \
  -n kaunitz-prod-sql-uksouth-rfk1 \
  -l uksouth \
  --admin-user kaunitzadmin \
  --admin-password "$PROD_SQL_PASSWORD"

az sql db create -g kaunitz-prod-rg \
  -s kaunitz-prod-sql-uksouth-rfk1 \
  -n kaunitz \
  --use-free-limit \
  --free-limit-exhaustion-behavior AutoPause
```

If `--use-free-limit` is unavailable (quota gone), fall back to serverless with a longer pause delay:

```bash
# Fallback only:
az sql db create -g kaunitz-prod-rg \
  -s kaunitz-prod-sql-uksouth-rfk1 \
  -n kaunitz \
  --tier GeneralPurpose --family Gen5 --capacity 2 \
  --compute-model Serverless --auto-pause-delay 360 \
  --backup-storage-redundancy Local
```

### 3c. Firewall rules

```bash
# Azure services
az sql server firewall-rule create -g kaunitz-prod-rg \
  -s kaunitz-prod-sql-uksouth-rfk1 \
  -n AllowAzureServices --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0

# WSL (for migrations + admin queries)
MY_IP=$(curl -s https://api.ipify.org)
az sql server firewall-rule create -g kaunitz-prod-rg \
  -s kaunitz-prod-sql-uksouth-rfk1 \
  -n AllowWSL --start-ip-address "$MY_IP" --end-ip-address "$MY_IP"

# Pi public IP (needed for dual-write) — find it first:
PI_IP=$(ssh robert@192.168.0.28 'curl -s https://api.ipify.org')
az sql server firewall-rule create -g kaunitz-prod-rg \
  -s kaunitz-prod-sql-uksouth-rfk1 \
  -n AllowPi --start-ip-address "$PI_IP" --end-ip-address "$PI_IP"
```

### 3d. Key Vault + secrets

```bash
az keyvault create -g kaunitz-prod-rg \
  -n kaunitz-prod-kv-rfk1 \
  -l uksouth

az keyvault secret set \
  --vault-name kaunitz-prod-kv-rfk1 \
  -n sql-admin-password \
  --value "$PROD_SQL_PASSWORD"

# Full pyodbc DSN for app container and Pi:
PROD_DSN="Driver={ODBC Driver 18 for SQL Server};Server=tcp:kaunitz-prod-sql-uksouth-rfk1.database.windows.net,1433;Database=kaunitz;Uid=kaunitzadmin;Pwd=${PROD_SQL_PASSWORD};Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;"
az keyvault secret set \
  --vault-name kaunitz-prod-kv-rfk1 \
  -n sql-dsn \
  --value "$PROD_DSN"
```

### 3e. Schema migration

```bash
export AZURE_SQL_DSN="$PROD_DSN"
python3 src/storage/migrate.py --dsn "$AZURE_SQL_DSN"
# expect: "+N tables"; second run: "no changes"
python3 src/storage/migrate.py --dsn "$AZURE_SQL_DSN"
```

### 3f. Prod dashboard (Container App)

Mirror the dev setup. Reuse the same ACR image (`kaunitzdevacrrfk1`) — rebuild only if app.py changed significantly.

```bash
# Container Apps environment
az containerapp env create \
  -g kaunitz-prod-rg \
  -n kaunitz-prod-env \
  -l uksouth

# Container app (system-assigned identity for KV access)
az containerapp create \
  -g kaunitz-prod-rg \
  -n kaunitz-prod-dashboard-rfk1 \
  --environment kaunitz-prod-env \
  --image kaunitzdevacrrfk1.azurecr.io/dashboard:latest \
  --registry-server kaunitzdevacrrfk1.azurecr.io \
  --ingress external --target-port 5000 \
  --min-replicas 1 --max-replicas 1 \
  --cpu 0.25 --memory 0.5Gi \
  --system-assigned

# Grant identity access to ACR + KV
IDENTITY=$(az containerapp show -g kaunitz-prod-rg -n kaunitz-prod-dashboard-rfk1 \
  --query "identity.principalId" -o tsv)
ACR_ID=$(az acr show -n kaunitzdevacrrfk1 --query id -o tsv)
KV_ID=$(az keyvault show -n kaunitz-prod-kv-rfk1 --query id -o tsv)

az role assignment create --assignee "$IDENTITY" --role AcrPull --scope "$ACR_ID"
az keyvault set-policy -n kaunitz-prod-kv-rfk1 \
  --object-id "$IDENTITY" --secret-permissions get list

# Wire KV secret into container env
az containerapp secret set -g kaunitz-prod-rg -n kaunitz-prod-dashboard-rfk1 \
  --secrets "sql-dsn=keyvaultref:$(az keyvault secret show --vault-name kaunitz-prod-kv-rfk1 -n sql-dsn --query id -o tsv),identityref:system"

az containerapp update -g kaunitz-prod-rg -n kaunitz-prod-dashboard-rfk1 \
  --set-env-vars "BETS_DB_WRITE=1" "AZURE_SQL_DSN=secretref:sql-dsn"

# Auth (reuse same Google OAuth client — just add new redirect URI in Google Cloud Console first)
az containerapp auth google update \
  -g kaunitz-prod-rg -n kaunitz-prod-dashboard-rfk1 \
  --client-id <same-google-client-id-as-dev> \
  --client-secret-name sql-dsn   # temp placeholder — update after wiring google secret

# TODO: wire google-client-secret into KV and container the same way as dev (A.7)

PROD_FQDN=$(az containerapp show -g kaunitz-prod-rg -n kaunitz-prod-dashboard-rfk1 \
  --query "properties.configuration.ingress.fqdn" -o tsv)
echo "Prod dashboard: https://$PROD_FQDN"
```

---

## Part 4 — Onboard Pi to prod DB (Azure A.10 step 2)

### 4a. Verify ODBC Driver 18 on Pi

```bash
ssh robert@192.168.0.28 'python3 -c "import pyodbc; print(pyodbc.drivers())"'
# expect: ODBC Driver 18 for SQL Server in list
# if not: install from Microsoft repo
```

### 4b. Import Pi historical CSVs into prod DB

Run from WSL, pointing at Pi's CSV files over SSH or after a local rsync:

```bash
# Option A: rsync Pi CSVs to a temp dir, then import
rsync -av robert@192.168.0.28:~/projects/bets/logs/ /tmp/pi-logs/

export AZURE_SQL_DSN="$PROD_DSN"
python3 scripts/migrate_csv_to_db.py \
  --bets-csv /tmp/pi-logs/bets.csv \
  --paper-dir /tmp/pi-logs/paper/ \
  --dsn "$AZURE_SQL_DSN"
# Idempotent — safe to re-run; uses deterministic UUIDs
```

### 4c. Add env vars to Pi's `.env`

```bash
ssh robert@192.168.0.28 'cat >> ~/projects/bets/.env << EOF

# A.10: Azure prod DB dual-write (added 2026-05-XX)
BETS_DB_WRITE=1
AZURE_SQL_DSN=Driver={ODBC Driver 18 for SQL Server};Server=tcp:kaunitz-prod-sql-uksouth-rfk1.database.windows.net,1433;Database=kaunitz;Uid=kaunitzadmin;Pwd=REPLACE_WITH_PASSWORD;Encrypt=yes;TrustServerCertificate=no;Connection Timeout=60;
EOF'
# Then edit the file on Pi to replace REPLACE_WITH_PASSWORD with the actual password from Bitwarden
```

**Pi-safety contract: do NOT set `BLOB_ARCHIVE=1` or any `AZURE_BLOB_*` vars on Pi yet** — blob archival for Pi is separate (A.10 extension, deferred).

### 4d. Smoke-test Pi dual-write

```bash
ssh robert@192.168.0.28 'cd ~/projects/bets && export $(cat .env) && python3 scripts/scan_odds.py --sports football --dry-run 2>&1 | tail -10'
# expect: no pyodbc / DB errors; "[scan] DB write active" or similar confirmation line
```

---

## Part 5 — Verification

```bash
# Pi rows visible in prod DB
python3 -c "
import pyodbc
c = pyodbc.connect('$PROD_DSN')
print('bets:', c.execute('SELECT COUNT(*) FROM bets').fetchone()[0])
print('paper_bets:', c.execute('SELECT COUNT(*) FROM paper_bets').fetchone()[0])
"

# Prod dashboard renders Pi data
curl -s "https://$PROD_FQDN/health"   # expect 200 {"db":"ok","csv":"ok"}
# Browser: sign in → confirm bet rows appear
```

---

## What stays deferred (do NOT do this week)

- **A.9** (decommission Pi CSV writes) — do after ≥1 week of clean dual-write on Pi
- **Blob archive on Pi** (`BLOB_ARCHIVE=1`) — separate step after A.10 is stable
- **`migrate_csv_to_db.py` pointing at `--check-sports` output** — not urgent
- **compute_book_skill.py persistence on Pi** — will auto-activate once `BETS_DB_WRITE=1` is in `.env` (Part 4c above) and `BLOB_ARCHIVE` is later set
- **Fixture calendar on Pi** — after A.10 onboards Pi to prod SQL, no Pi-side ingest is needed.
  Pi reads `fixtures` from prod SQL automatically via `FixtureRepo` (activated by the same `BETS_DB_WRITE=1` + DSN env vars set in Part 4c).
  Verify post-A.10 with:
  ```
  python3 -c 'from src.data.fixture_calendar import calendar_available; print(calendar_available())'
  ```
  The Mon 02:00 ingest cron on Pi is not needed — WSL remains the sole ingester until A.10; after A.10 the same WSL cron writes to prod SQL which Pi reads.

## After completing

Delete this file. Update CLAUDE.md A.10 status to ✅ Done with date and the prod resource names.
Update `PLAN_AZURE_2026-05.md` phase tracker for A.10.
