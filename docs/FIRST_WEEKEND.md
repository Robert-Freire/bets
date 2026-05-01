# First-weekend runbook — 2026-05-01 (Fri) → 2026-05-04 (Mon)

The first live weekend with Pi production cron + WSL parallel test cron + R.11 provenance shipped + dev-side Azure migration substantially complete (A.0–A.7 + A.5.5). **This is the canonical eval log** — every check, scan, CLV count, error, and Monday post-mortem gets recorded below in the **Live evaluation log** section.

All times **UTC** with **BST** in brackets (BST = UTC+1).

---

## System status (snapshot at handover into the weekend)

| Layer | State | Where |
|---|---|---|
| Pi production cron | ✅ active (cutover 2026-05-01) | `robert@192.168.0.28`, `~/projects/bets/`, `.env` (prod key) |
| WSL parallel-test cron | ✅ active (re-enabled 2026-05-01) | `/home/rfreire/projects/bets/`, `.env.dev` (dev key) |
| Pi notifications | enabled (`robert-epl-bets-m4x9k`) | scan_odds.py default topic |
| WSL notifications | silenced (`NTFY_TOPIC_OVERRIDE=""`) | data-collection only |
| R.11 provenance | live | `code_sha`, `strategy_config_hash` per paper row |
| **A.4 dual-write (Azure SQL)** | ✅ live on **WSL only** | `BETS_DB_WRITE=1` in `.env.dev`; DB `kaunitz` on `kaunitz-dev-sql-uksouth-rfk1` |
| **A.5.5 raw-API blob archive** | ✅ live on **WSL only** | `BLOB_ARCHIVE=1` in `.env.dev`; container `kaunitzdevstrfk1/raw-api-snapshots` |
| **Public Azure dashboard** | ✅ live (Google OIDC, allowlist `robert.freire@gmail.com`) | `https://kaunitz-dev-dashboard-rfk1.orangebush-7e5af054.uksouth.azurecontainerapps.io` |
| **CLV source** | football-data.co.uk PSC* odds via Mon 08:00 backfill (`closing_line.py` paused) | `scripts/backfill_clv_from_fdco.py` |
| Tests | 263 passing | `pytest -q` |

---

## WSL vs Pi divergence (READ THIS FIRST)

The two environments now diverge on more than just notifications. Don't apply Pi expectations to WSL or vice versa.

| Concern | WSL (dev) | Pi (prod) |
|---|---|---|
| API key | dev (separate 500/mo budget) | prod (separate 500/mo budget) |
| Notifications | silenced | live to ntfy `robert-epl-bets-m4x9k` |
| Football scans | 5/wk (Tue 07:30, Fri 19:30, Sat 10:30, Sat 16:30, Sun 12:30) | identical |
| FDCO CLV backfill | Mon 08:00 | Mon 08:00 (independent run; same source → identical writes back to its own CSV) |
| `bets.csv` backup | 03:00 daily, 14d retention | identical |
| `check_sports.py` | 1st & 15th 08:00 | identical |
| **xG refresh (Understat)** | ❌ not scheduled (would conflict on `logs/team_xg.json`) | Mon 06:00 |
| **Research scanner** | ❌ not scheduled (would conflict on `docs/RESEARCH_FEED.md`) | Mon 10:00 curated; 1st of month 10:00 open-search |
| **Azure SQL dual-write** | ✅ ON — every bet/paper row also lands in DB | ❌ OFF (no env flag; lazy `pyodbc` import never triggers) |
| **Raw-API blob archive** | ✅ ON — every Odds API response gzipped → `raw-api-snapshots` | ❌ OFF (deferred to post-weekend; Pi-safety contract: lazy `azure.storage.blob` import never triggers) |
| Dashboard data source | Pi data NOT visible in Azure dashboard until A.10 — only Pi-local `python3 app.py` shows it | Azure dashboard shows WSL data only |

**Why the WSL-only Azure flags?** Pi-safety contract from A.4/A.5.5. WSL is the test stream; Pi is canonical production. Activating Azure on Pi is **A.10** (deferred, post-weekend at earliest).

---

## What we're testing this weekend

1. **CLV pipeline produces non-zero data for the first time ever** — but **only after Mon 08:00 UTC FDCO backfill fires**. Prior to that, all `pinnacle_close_prob` cells stay empty even on settled bets. This is the new normal post-CLV-source-swap.
2. **Dev/prod parity.** Both machines run the same scanner code; if WSL paper-bet counts diverge significantly from Pi (>20% per variant), something is wrong.
3. **No quota collision.** Pi prod key + WSL dev key, separate 500/mo budgets.
4. **A.4 dual-write parity (WSL only).** Every WSL CSV row should also land in Azure SQL. Mismatch = repo wedge.
5. **A.5.5 blob archive coverage (WSL only).** Every WSL `api_get(...)` call should produce one gzipped blob. Gap = silent archive failure (and we should never have to wait for fresh data to retro-test data-quality rules).
6. **Public dashboard renders DB rows correctly.** Sign in as `robert.freire@gmail.com`; bet history table populated; `/health` returns `{db: ok, csv: ok}`.
7. **No silent failures.** Scan logs clean on both machines; backfill log clean on Monday.

---

## Schedule overview (live, both machines unless flagged)

| UTC | BST | What | Days | Pi | WSL |
|---|---|---|---|---|---|
| 07:30 | 08:30 | Football scan | Tue | ✅ | ✅ |
| 19:30 | 20:30 | Football scan (Fri lineup hints) | Fri | ✅ | ✅ |
| 10:30 | 11:30 | Football scan (pre-12:30 KO) | Sat | ✅ | ✅ |
| 16:30 | 17:30 | Football scan (between 15:00 and 17:30 KOs) | Sat | ✅ | ✅ |
| 12:30 | 13:30 | Football scan (pre-Sun afternoon) | Sun | ✅ | ✅ |
| 03:00 | 04:00 | `bets.csv` snapshot to `bets.csv.bak.<date>` (14d retention on the **snapshots**; live file never touched) | every day | ✅ | ✅ |
| 08:00 | 09:00 | FDCO CLV backfill (writes `pinnacle_close_prob` + `clv_pct` to bets/paper rows) | Mon | ✅ | ✅ |
| 08:00 | 09:00 | `check_sports.py` (sports discovery) | 1st & 15th | ✅ | ✅ |
| 06:00 | 07:00 | xG refresh (Understat) | Mon | ✅ | ❌ |
| 10:00 | 11:00 | Research scanner — curated | Mon | ✅ | ❌ |
| 10:00 | 11:00 | Research scanner — open-search | 1st of month | ✅ | ❌ |

**Removed from the original plan (per 2026-05-01 trim + CLV source swap):**
- Closing-line + drift snapshot every 5 min — `closing_line.py` paused; CLV backfilled from FDCO on Monday instead.
- NBA scans (Mon–Fri 17:00).
- Tennis scans (Mon, Thu 09:00).
- Mon + Fri 07:30 football scans (kept Tue 07:30 only — fresh weekly lines after weekend).

---

## CLV source change — what to expect this weekend

`closing_line.py` is **paused** (not deleted; revert path documented in memory `project_clv_source_swap_2026_05`). CLV now comes from football-data.co.uk's free Pinnacle closing odds (`PSCH/PSCD/PSCA` for h2h, `PC>2.5/PC<2.5` for totals 2.5) via `scripts/backfill_clv_from_fdco.py` on Mondays at 08:00 UTC.

**Practical implications for this weekend's eval:**
- **No live drift tracking.** `logs/drift.csv` is frozen as of 2026-05-01. T-60 / T-15 / T-1 capture is gone.
- **CLV won't be visible Sat/Sun.** First populated `pinnacle_close_prob` cells appear ~08:30 UTC Monday after the FDCO backfill cron fires. Don't pre-judge "no edge" from Sunday's empty CLV column.
- **CLV scope is football top-6 only.** EPL, Bundesliga, Serie A, Ligue 1, Championship, Bundesliga 2. Anything outside (NBA, tennis — already none scanned this weekend; BTTS — already 0 bets) gets no CLV ever. We don't bet outside that scope right now anyway.
- **Totals: 2.5 line only.** FDCO doesn't publish other totals. Already aligned with our market mix.
- **+1 day delay vs at-close.** Fine for weekly review, useless for live monitoring. We accepted this tradeoff to stay under the 500/mo Odds API budget.

**Manual smoke for the FDCO backfill** (if you want to fire it before Monday cron, e.g. on Sunday evening):

```bash
# WSL — dry run, no mutation
export $(cat .env.dev) && python3 scripts/backfill_clv_from_fdco.py --dry-run | head -20
# Pi — real run
ssh robert@192.168.0.28 'cd ~/projects/bets && export $(cat .env) && .venv/bin/python3 scripts/backfill_clv_from_fdco.py 2>&1 | tail -20'
```

---

## What "good" looks like by Monday morning (post-08:00 FDCO backfill)

### Both machines
- [ ] **CLV bets per variant > 0** for at least 5 of the active variants (A, C, D, F, G, H typically fire most). Variants with 0 bets this weekend won't have CLV — that's normal, not a failure.
- [ ] **`pinnacle_close_prob` populated** for every settled top-6-football h2h or totals-2.5 bet kicked off Sat/Sun.
- [ ] **No `[backfill_clv]` errors** in `logs/backfill_clv.log` other than expected "no FDCO row found" warnings for fixtures FDCO hasn't published yet.
- [ ] **No 401/quota errors** in either scan log.
- [ ] **No `[paper:schema]` migration loops** — schema migration should run once per CSV, then be silent.
- [ ] **Dev key remaining ≥ 250/500** at end of weekend (started 499/500 Friday).
- [ ] **Prod key remaining ≥ 250/500** at end of weekend.

### WSL-specific
- [ ] **Azure SQL row count matches CSV row count** for `bets` and each `paper_<variant>` table.
- [ ] **Blob coverage:** at least 5 football scans × ≥ 2 blobs each (h2h+totals + canary `/sports/`) ≈ ≥ 10 blobs in `kaunitzdevstrfk1/raw-api-snapshots/` for the weekend.
- [ ] **No leakage of dev API key** in any sample blob body (random sample 1–2 blobs and grep).
- [ ] **`/health` on the public dashboard returns `{db: ok, csv: ok}`** and the bet history table renders WSL data after Google sign-in.
- [ ] **WSL scan log shows `[ntfy] Disabled`** entries (proves override works).
- [ ] **No `[snapshots] WARN`** entries in WSL scan log (would mean blob writes are degrading to local buffer — investigate before Monday).

### Pi-specific
- [ ] **`logs/snapshots/` directory does NOT exist on Pi** (proves A.5.5 lazy import contract holds — no buffering of Pi's calls).
- [ ] **`pyodbc` not installed** on Pi (proves A.4 stays dormant). `ssh robert@192.168.0.28 '.venv/bin/python3 -c "import pyodbc"'` should fail with `ModuleNotFoundError`.
- [ ] **No `[ntfy] Failed`** entries in Pi scan log.
- [ ] **xG refresh ran cleanly Mon 06:00** — `logs/team_xg.json` mtime shows Monday update.
- [ ] **Research scan ran cleanly Mon 10:00** — `docs/RESEARCH_FEED.md` updated.

---

## Live evaluation log

> Append a new entry every time you (or I) check the system. Newest at the top.
> Each entry: **timestamp, what was checked, finding, action taken (if any).**

### 2026-05-01 ~13:30 UTC — Pre-weekend setup complete (post-A.5.5 update)
- A.0–A.7 + A.5.5 ✅ shipped + merged. PR #14 opened for A.5.5 (final phase before the weekend).
- WSL `.env.dev` flipped: `BETS_DB_WRITE=1` + `BLOB_ARCHIVE=1` + KV references. Both Azure paths live; Pi untouched (post-weekend item per user).
- Closing-line cron removed; FDCO backfill installed at Mon 08:00 on both machines.
- R.11 provenance live: all post-R.11 paper rows tagged with `code_sha` + `strategy_config_hash`.
- ZERO CLV captures historically. Mon 08:00 FDCO backfill is the first real test.
- Dev key remaining: 499/500 (one curl spent 2026-05-01 morning).
- **Action:** none — let cron run. Next scheduled fire: **Fri 19:30 UTC football scan** on both machines.

---

## Quick commands reference

```bash
# === Pi state ===
ssh robert@192.168.0.28 'tail -30 ~/projects/bets/logs/scan.log'
ssh robert@192.168.0.28 'wc -l ~/projects/bets/logs/paper/*.csv ~/projects/bets/logs/bets.csv'
ssh robert@192.168.0.28 'cd ~/projects/bets && .venv/bin/python3 scripts/compare_strategies.py 2>&1 | head -30'
# Pi-safety smoke (must NOT install Azure libs)
ssh robert@192.168.0.28 '.venv/bin/python3 -c "import pyodbc"'                # expect ModuleNotFoundError
ssh robert@192.168.0.28 '.venv/bin/python3 -c "import azure.storage.blob"'    # expect ModuleNotFoundError
ssh robert@192.168.0.28 'ls ~/projects/bets/logs/snapshots/ 2>&1'             # expect "No such file"

# === WSL state ===
tail -30 logs/scan.log
wc -l logs/paper/*.csv logs/bets.csv
python3 scripts/compare_strategies.py 2>&1 | head -30
python3 scripts/compare_strategies.py --all-history 2>&1 | head -30  # include pre-R.11 test data

# === A.4 dual-write parity (WSL only) ===
# Compare CSV row count vs Azure SQL row count
export $(cat .env.dev) && python3 -c "
import os, sys
sys.path.insert(0, '.')
from src.storage.repo import BetRepo
r = BetRepo()
print('db_enabled:', r.db_enabled)
" 
# Then via az sql query (read-only count)
DSN=$(az keyvault secret show --vault-name kaunitz-dev-kv-rfk1 --name sql-dsn --query value -o tsv)
python3 -c "import pyodbc, os; c=pyodbc.connect(os.environ['DSN']); cur=c.cursor(); cur.execute('SELECT COUNT(*) FROM bets'); print('DB bets:', cur.fetchone()[0]); cur.execute('SELECT COUNT(*) FROM paper_bets'); print('DB paper:', cur.fetchone()[0])" DSN="$DSN"
echo "CSV bets: $(($(wc -l < logs/bets.csv) - 1))"
echo "CSV paper: $(($(cat logs/paper/*.csv | wc -l) - $(ls logs/paper/*.csv | wc -l)))"

# === A.5.5 blob archive coverage (WSL only) ===
az storage blob list --account-name kaunitzdevstrfk1 -c raw-api-snapshots --auth-mode key --num-results 50 --query "[].{name:name, size:properties.contentLength}" -o table | tail -50
# Spot-check redaction on one blob
LATEST=$(az storage blob list --account-name kaunitzdevstrfk1 -c raw-api-snapshots --auth-mode key --query "sort_by([], &properties.lastModified)[-1].name" -o tsv)
az storage blob download --account-name kaunitzdevstrfk1 -c raw-api-snapshots --auth-mode key -n "$LATEST" -f /tmp/sample.json.gz --no-progress
zcat /tmp/sample.json.gz | jq '{captured_at, source, endpoint, status, params, headers}'
zcat /tmp/sample.json.gz | grep -c "$ODDS_API_KEY"   # expect 0

# === Public dashboard health ===
curl -s https://kaunitz-dev-dashboard-rfk1.orangebush-7e5af054.uksouth.azurecontainerapps.io/health
# (Open the root URL in a browser to verify the bet history table renders.)

# === FDCO CLV backfill (manual; cron fires Mon 08:00) ===
export $(cat .env.dev) && python3 scripts/backfill_clv_from_fdco.py --dry-run | head -20
ssh robert@192.168.0.28 'cd ~/projects/bets && export $(cat .env) && .venv/bin/python3 scripts/backfill_clv_from_fdco.py 2>&1 | tail -20'

# === Quota check (uses 1 call each) ===
export $(cat .env.dev) && curl -s -D - "https://api.the-odds-api.com/v4/sports/soccer_epl/odds/?apiKey=$ODDS_API_KEY&regions=uk&markets=h2h" -o /dev/null | grep -i x-requests
ssh robert@192.168.0.28 'export $(cat ~/projects/bets/.env) && curl -s -D - "https://api.the-odds-api.com/v4/sports/soccer_epl/odds/?apiKey=$ODDS_API_KEY&regions=uk&markets=h2h" -o /dev/null | grep -i x-requests'

# === Cron sanity (both machines) ===
crontab -l | grep -cE 'scan_odds|backfill_clv'                                     # WSL: expect 6 (5 scans + 1 backfill)
ssh robert@192.168.0.28 'crontab -l | grep -cE "scan_odds|backfill_clv"'           # Pi:  expect 6
```

---

## Known limitations (don't interpret as bugs)

- **CLV is delayed by 1 day** — populated by Mon 08:00 FDCO backfill, not at-close. Don't expect Sat/Sun CLV.
- **`logs/drift.csv` is frozen** — closing-line script paused; T-60/T-15/T-1 captures will not grow this weekend. Existing rows are historical only.
- **No CLV outside top-6 football leagues** — NBA + tennis already not scanned this weekend; BTTS bets historically 0.
- **WSL gaps when laptop sleeps** — acceptable; Pi covers production reliability.
- **Pre-R.11 paper rows have empty `strategy_config_hash`** — own "pre-R.11 / WSL-test" eval window. `compare_strategies.py` default filters them out; pass `--all-history` to include.
- **Pi data not visible in the Azure dashboard yet** — A.10 (Pi onboarding) handles that; runs in its own future sprint.
- **Azure SQL serverless auto-pause = 60 min** — first dashboard hit after idle takes ~5–15 s while the DB resumes. Expected.

---

## Post-weekend cleanup checklist

- [ ] **Monday morning:** write up post-mortem in **Live evaluation log** above with CLV stats per variant (after FDCO backfill fires at 08:00 UTC).
- [ ] **Pi A.5.5 activation:** flip Pi `.env` to add `BLOB_ARCHIVE=1` + KV references (was deferred per user 2026-05-01). Then run the Pi-safety smoke commands above and confirm blobs from Pi land in `raw-api-snapshots/odds_api/...` distinguishable from WSL blobs by IP/timestamp.
- [ ] **Pi A.4 activation:** parallel decision — flip Pi `.env` to add `BETS_DB_WRITE=1` so Pi rows start dual-writing into Azure SQL. Gate: ≥1 weekend of clean WSL dual-write data first. (Strictly part of A.10, but feasible to start in advance.)
- [ ] **A.8 cutover eligibility:** ≥1 calendar week of clean WSL dual-write soak (clock started 2026-05-01) → eligible ~2026-05-08.
- [ ] **Decide on paid Odds API tier** ($25/mo, 100k credits) based on CLV evidence after the first 50 settled bets with `clv_pct` populated.
- [ ] **Delete this doc** if R.11 + Azure + FDCO all worked cleanly — it's a transient runbook, the durable state lives in CLAUDE.md + memory. Don't delete without asking the user first.
