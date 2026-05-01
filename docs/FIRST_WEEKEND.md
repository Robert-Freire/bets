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
| **League set** | **7 leagues — 6 prod + La Liga (M.4a, 2026-05-01)** | **6 leagues from `config.json`** |
| **Config file read** | `config.dev.json` via `LEAGUES_CONFIG` in `.env.dev` | `config.json` (no env var) |
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
2. **Dev/prod parity (scoped to shared leagues).** Both machines run the same scanner code on the 6 shared leagues (EPL, Bundesliga, Serie A, Championship, Ligue 1, Bundesliga 2). If WSL paper-bet counts on those 6 leagues diverge from Pi by >20% per variant, something is wrong. WSL's La Liga rows (added 2026-05-01 via M.4a) are dev-only by design and **must not** be included in the parity comparison — Pi has no La Liga data.
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

---

## Market-coverage rollout — Monday decisions (PR #17 follow-up)

PR #17 (`market-coverage-m0-m2-2026-05`) merged to main 2026-05-01 with M.0–M.2 of `docs/PLAN_MARKET_COVERAGE_2026-05.md`. Pi was intentionally **not pulled** to keep the eval window clean. The Monday post-mortem must close out the following decisions before any further market-coverage work resumes.

### D.1 — Pull PR #17 onto Pi?

**Default:** yes, pull immediately after the post-mortem signs off on the weekend.

**Conditions to hold:**
- Pi scan log shows any 401 / quota / paper-schema-loop / new-error pattern over the weekend → diagnose first; do not pull a config-loader change onto a wedged Pi.
- Post-mortem flags any divergence > 20% in WSL vs Pi paper-bet counts per variant → treat the same way; understand divergence first.

**Action if pulling:**
```bash
ssh robert@192.168.0.28 'cd ~/projects/bets && git fetch && git log --oneline HEAD..origin/main && git pull && .venv/bin/pytest -q'
# Verify markets=h2h is what Pi will request next
ssh robert@192.168.0.28 'grep -n "markets" ~/projects/bets/scripts/scan_odds.py | head -5'
# Confirm config.json leagues array loads
ssh robert@192.168.0.28 'cd ~/projects/bets && .venv/bin/python3 -c "import json; print(len(json.load(open(\"config.json\"))[\"leagues\"]))"'
```

**If holding:** add a one-line note in the **Live evaluation log** with the reason and a re-evaluate date.

### D.2 — Promote M.3 (add probe-passing leagues to prod `config.json`)?

**Default:** yes — La Liga 2, Eredivisie, Primeira Liga, Ligue 2 cleared the prod bar (`avg_books ≥ 20 AND p95_dispersion ≤ 0.04`) per `docs/LEAGUE_COVERAGE_2026-05.md`. La Liga **fails** (p95 dispersion 0.083) — exclude.

**Pre-condition:** D.1 done, Pi running PR #17 cleanly for at least one scan cycle.

**Budget verification before merging M.3:** `(6 + 4) × 2 cr × 5 scans/wk × 4.345 = 434/mo`, leaves 66cr buffer in the 500/mo cap. ✓ Re-run the M.3 phase verification command in the plan doc before merging.

**Action:** open a fresh PR off main with the four added entries in `config.json`, the updated `CLAUDE.md` "Sports actively scanned" table, and the M.3 acceptance checklist filled in. **Do not bundle with M.4.**

### D.3 — Promote M.4 (dev AH probe via `extra_markets=["spreads"]`)?

**Default:** yes if D.1 + D.2 both clean for one full scan cycle. AH is the highest-leverage edge probe in the plan.

**Pre-conditions:**
- D.2 merged and live on Pi.
- WSL crontab being trimmed 5 → 3 scans/wk (Tue 07:30 + Sat 16:30 + Sun 12:30) to fit the dev budget. Pi crontab unchanged.
- New `Q_asian_handicap` paper variant builds and writes a row on at least one synthetic spreads fixture in the test suite.

**Budget verification before merging M.4:** dev burn = `10 leagues × 4 cr (h2h+spreads) × 3 scans × 4.345 = 522/mo`. **Marginally over** 500/mo dev key — recompute against the Plan §M.4 mitigation table and pick option (a) or a tighter variant. Update the plan doc if the chosen option deviates.

### D.4 — Decide on paid Odds API tier ($25/mo, 100k credits)

Mostly an existing checklist item, but the M.0 totals-drop and M.4 AH-probe both interact with it. Re-evaluate using the actual weekend credit consumption from both keys, not the stale 497/500 figure.

**Trigger to flip paid:** ≥50 settled bets with `clv_pct` populated **AND** average CLV positive on at least one paper variant on the weekend cohort. Without that signal, free tier remains right.

### D.5 — La Liga revisit cadence

`project_la_liga_excluded.md` is now sourced from real probe numbers (p95 dispersion 0.083). **Don't re-probe in < 90 days** unless something materially changed (paid tier, new region added, dispersion threshold relaxed). Drop into dev only if D.3 lands with budget headroom remaining; never to prod on the current evidence.

### D.6 — Bot scope-creep follow-up

The PR #17 implementation bot added an unrelated WARNING ntfy notification outside the M.0/M.1/M.2 task list (it was the user's separate request — no harm, but a process miss). If we keep using a sub-agent for plan execution, tighten the bot-execution protocol in `docs/PLAN_RESEARCH_2026-04.md` (and reuse for future plans) to add an explicit "no drive-by changes; out-of-scope work goes to a follow-up PR" line.

---

### D.7 — La Liga early-add (M.4a) — assess after first weekend

Shipped 2026-05-01 alongside this doc update. Dev-only via `config.dev.json` + `LEAGUES_CONFIG=config.dev.json` in `.env.dev`. Pi unchanged.

**First scan (manual, 20:49 UTC Fri 2026-05-01):** ran cleanly. La Liga loaded with 20 fixtures and 32 avg books. **160 paper-portfolio rows** added across variants (vs typical ~30 per scan on the prod leagues). One Kaunitz bet logged — Girona vs Mallorca AWAY at betvictor 1.2, edge 4.5%, 27 books MED. Bet pushed to prod ntfy by mistake (`.env.dev` was missing `NTFY_TOPIC_OVERRIDE=` line; cron itself sets it inline so cron is unaffected; `.env.dev` now patched locally).

**Dispersion shape analysis ran offline against the archived blob (zero API cost):**
- **78.3% of fixture×outcome rows are bimodal** — far above the 30% threshold. La Liga's dispersion is structured, not noise.
- Sharp anchors confirmed: Pinnacle (89% centre rate), Marathonbet (88%), Matchbook (87%), Smarkets (78%).
- `J_sharp_weighted` hardcoded weights need La Liga override: keep Pinnacle 3.0; reduce Betfair Exchange 2.5 → 1.5 (not centre-dominant on La Liga); add Marathonbet + Matchbook at 2.5 (currently default 1.0).
- Soft UK books for edge-flagging: virginbet, livescorebet, paddypower, skybet, ladbrokes_uk, williamhill.
- Anomaly: winamax_fr/de show extreme structural bias — always low on Draws, always high on Aways. Different pricing model entirely.

→ **Run `scripts/analyse_dispersion.py --blob <path>` against Sat + Sun blobs to confirm cluster persistence across scans.** Persistent clusters = M.7 hypothesis validated; can move on M.6 weights.

**Monday checks specific to M.4a:**
- [ ] WSL Sat/Sun scans show La Liga in the per-scan summary (7 leagues vs Pi's 6).
- [ ] `logs/paper/A_production.csv` and `logs/paper/J_sharp_weighted.csv` have La Liga rows; counts diverge between the two variants (proves the variants are filtering La Liga differently — informative even before CLV).
- [ ] WSL dev key consumption in line with expected: ~7 leagues × 2 cr × 3 weekend scans = ~42 cr added vs the 6-league baseline.
- [ ] No scan-log errors specific to La Liga (parse failures, FDCO mapping issues, etc.).
- [ ] Run `scripts/analyse_dispersion.py --blob` on Sat 10:30 + Sun 12:30 La Liga blobs. Confirm shape distribution + sharp/soft books match Friday's findings.

**Soft signals worth noting in the post-mortem write-up:**
- Count of La Liga value-bet flags per variant on Sat/Sun, broken down by Kaunitz vs Model-filtered.
- Spread of edge percentages on La Liga flags vs the 6 prod leagues' flags.
- Whether `J_sharp_weighted` and `A_production` produce systematically different La Liga bet sets (overlap %), as a leading indicator before CLV lands.

**Decision rule on M.4a continuation:** if La Liga produces obvious data-quality issues (parse errors, missing teams, mapping failures) → revert by removing La Liga from `config.dev.json`. If it runs cleanly + cluster persistence holds → keep it through M.7 + M.4 to maximise the data window.

### Reuse-archived-data principle (lesson from this session)

When doing analysis on already-collected data, **always prefer Azure Blob `raw-api-snapshots` over a fresh API call**. Each Odds API call costs 2cr against a 500/mo budget; the blob archive (A.5.5, live on WSL) holds every Odds API response. The dispersion-shape analysis in this section was done at zero API cost by parsing the archived blob from the 20:49 scan. `scripts/analyse_dispersion.py --blob <path>` is the canonical pattern. Memory note: `feedback_reuse_archived_data.md`.

### All-leagues dispersion analysis (2026-05-01) — captured for reference

Ran `scripts/analyse_dispersion.py` against blobs for all 10 currently-archived leagues. Results in `docs/DISPERSION_SHAPES_2026-05.md`. Three findings worth surfacing here:

1. **Bimodality is universal** (75–93% across every league). The original M.7 threshold was useless. Replaced with **cluster amplitude** as the differentiator (mean distance between low and high cluster medians).
2. **Ligue 1 has the highest cluster amplitude (0.0518)** — even higher than La Liga (0.0441). Already in prod. Either there's unrealised edge or amplitude alone doesn't translate to extractable edge. Investigate against existing CLV data once it lands.
3. **Sharp identity shifts per league.** Marathonbet is a near-universal sharp (8 of 10 leagues) but currently weighted 1.0 in `J_sharp_weighted`. Pinnacle's sharpness varies — top sharp on La Liga + Championship, mid-pack on EPL/Bundesliga/Serie A/Ligue 1. Hardcoded weights are league-blind and leave signal on the table.

**Methodology limitation flagged.** "Sharp = high centre rate" is a proxy that can mislabel a real sharp as soft when many UK books cluster together. M.7 should add Pinnacle-anchored deviation (and eventually closing-line deviation, post-CLV) as a more robust metric. See `docs/DISPERSION_SHAPES_2026-05.md` § "Methodology limitation".

### D.8 — Weekly post-mortem book-sharpness analysis (standing item, two scripts)

Added 2026-05-01 as a recurring Monday post-mortem step. **Zero API cost** — runs entirely against archived blobs and on-disk FDCO CSVs.

**Two-step standing procedure:**

**Step 1 — Dispersion shape analysis (`scripts/analyse_dispersion.py`).** Centre rate per book per league, from archived Odds API blobs.
- Catches all 36 books from the Odds API (incl. niche specialists FDCO doesn't cover).
- Caveat: centre-rate is a proxy that can mislabel sharps when soft UK books cluster.

**Step 2 — Book Brier vs results (`scripts/eval_books_vs_results.py`).** Gold-standard sharpness from realized outcomes, on FDCO data.
- Catches only ~7 books FDCO covers (Bet365, Bwin, Pinnacle, BetVictor, William Hill, Interwetten, Betfair Exchange).
- This is the truth signal — use it to **cross-validate Step 1**. If a book Step 1 flagged as sharp is in FDCO and shows poor Brier, distrust Step 1's verdict for that book. If FDCO doesn't cover the book, treat Step 1 as hypothesis only.

**What to look for week-on-week:**
- Brier rankings shift between consecutive weeks (small samples are noisy; trust 4-week trends over single weeks).
- Centre-rate sharps that fail Brier validation → demote in `book_weights`.
- Centre-rate sharps NOT in FDCO → flag as "unvalidated, watch with each weekly run."
- Soft books drifting toward centre → could be tightening their lines.

**If drift persists ≥ 2 weeks**, update `book_weights` in `config.json` / `config.dev.json`. Memory: `project_weekly_postmortem_cadence.md`.

**Don't run with `--fetch`.** That'd burn 22cr/week on data we already have. The whole point is to avoid that.

**Initial 2025-26 Brier findings** (already captured in `docs/DISPERSION_SHAPES_2026-05.md`):
- Pinnacle is the universal sharp by Brier (top-2 on 6 leagues), validating canonical wisdom and **contradicting** the centre-rate analysis on EPL/Bundesliga/Serie A/Ligue 1.
- Bet365 and Bwin are broadly sharp (top-3 on 3 leagues each); currently weighted 1.0 in `J_sharp_weighted` — should be 2.0 / 1.5.
- Centre-rate is unreliable for ranking sharpness on densely-priced markets. Demoted to secondary signal for FDCO-covered books; remains primary for books FDCO doesn't cover (with weak-evidence label).

---

## Strategic direction for next week (decided 2026-05-01)

Beyond the immediate D.1–D.6 decisions, the conversation that produced this section also clarified the *shape* of the next week. Capturing it here so the post-mortem doesn't reconstruct context.

### Core insight: dispersion is opportunity, *if* the disagreement is structured

Previously the dispersion filter (`MAX_DISPERSION = 0.04`) was treated as a binary "trust this market or skip it." That framing rejects La Liga (p95 = 0.083). But high dispersion can mean three different things:

| Shape | Books look like | Implication |
|---|---|---|
| Unimodal-tight | Bell curve, narrow | Flat consensus is fine |
| **Bimodal** | **Two distinct peaks** | **Sharp-weighted consensus wins big — the soft cluster is visibly wrong** |
| No-structure | Wide flat distribution | Avoid; no method recovers signal |

La Liga's 0.083 dispersion could be any of these and we don't currently know which. **If it's bimodal with a persistent sharp/soft split, La Liga becomes the league where sharp-weighting has its biggest *relative* advantage** — exactly because the flat-consensus method (which currently filters it out) is the wrong tool for it.

The 16-paper-variant infrastructure already encodes the sharp-weighted hypothesis: `D_pinnacle_only`, `J_sharp_weighted`, `E_exchanges_only`, `H_no_pinnacle`. They've been running in shadow but lack CLV data to validate. The dispersion-shape diagnostic gives us an *a priori* prediction of which leagues these variants should outperform on, before we have settled bets.

### Weekly cadence (Mon → Sun)

Driven by the strategic insight above. M.7 (the diagnostic) lands first; M.6 (the mechanism for per-league weight overrides) reads M.7's output; M.4 (dev sandbox) ties them together with the AH probe.

| Day | PR / Action | What |
|---|---|---|
| **Mon 2026-05-04** | Post-mortem + PR #17 → Pi (D.1) | Confirm weekend was clean; pull PR #17 onto Pi. |
| **Mon afternoon** | M.3 PR | Add La Liga 2 + Eredivisie + Primeira + Ligue 2 to prod `config.json` (D.2). Keep prod boring. |
| **Tue** | M.7 PR | `scripts/analyse_dispersion.py` + `docs/DISPERSION_SHAPES_2026-05.md`. Run on all 11 leagues. ~22 cr cost. **Output dictates the shape verdict per league** — drives everything downstream. |
| **Wed** | M.6 PR | `book_weights` schema in `config.json`. Refactor `J_sharp_weighted` to read from config. Add `J2_sharp_weighted_per_league` (same code, different config). Backwards-compatible: absent block → flat consensus. |
| **Thu** | M.4 PR | `config.dev.json` with `extra_markets=["spreads"]` on top-4 leagues, La Liga added to dev set with M.7-derived weights, `Q_asian_handicap` variant, WSL cron 5 → 3 scans/wk. Per-league `extra_markets` override extends M.2 schema. |
| **Fri** | Soak | Don't touch. Let dev cron run Fri evening. |
| **Sat–Sun** | First experimental weekend | All four variants (`A`, `J`, `J2`, `D`) run on La Liga + AH on top-4 in parallel. |

This sequence lets prod stay frozen on the M.0 + M.3 baseline while dev becomes a structured laboratory. The four variants' CLV on La Liga answers two questions at once: (a) is sharp-weighting the right method for high-dispersion leagues, (b) does La Liga belong in prod under any method.

### What "configurable book weights" means concretely

Today's `J_sharp_weighted` has hardcoded weights inside `src/betting/strategies.py`. M.6 moves them to `config.json` with this resolution order:

1. `book_weights.by_league[league][book]` if present
2. else `book_weights.by_market[market][book]` if present
3. else `book_weights.default[book]` if present
4. else `book_weights.default["*"]`
5. else `1.0` (flat-consensus fallback)

This makes Pi (`config.json`) and WSL (`config.dev.json`) able to run the same `J_sharp_weighted` code with different weighting policies. M.4's dev experiment uses the per-league overrides; Pi keeps the simpler global weights until evidence forces a flip in M.5.

### Dispersion shape — what M.7 produces

For each fixture × outcome with ≥10 books:
- Classify each book into `low_cluster` / `centre` / `high_cluster` using median ± 1.5·MAD.
- Determine shape (unimodal-tight / unimodal-fat / bimodal / no-structure).

For each league:
- Distribution of shapes (% of fixtures × outcomes).
- Per-book cluster-membership tally across all fixtures.
- **Cluster persistence score** (Spearman correlation): are the same books always in the "low" cluster?

Decision rule from the persistence score:
- ≥ 0.6 → structural sharp/soft split → **add with sharp-weighting** (prod or dev based on other criteria)
- 0.3–0.6 → some structure, marginal → **dev only**
- < 0.3 → noise → **drop the league**

### What this changes about La Liga specifically

La Liga today is excluded from prod for p95 dispersion 0.083. The right test is **not** "is dispersion below 0.04" but **does La Liga's dispersion form a stable sharp/soft cluster, and does sharp-weighted consensus produce positive CLV on it.**

M.7 answers question 1 with no further bets needed. M.4 starts answering question 2 by running La Liga in dev with `J2_sharp_weighted_per_league` for 4–6 weekends. M.5 makes the graduation call.

If M.7 says "no structure" for La Liga, M.4 drops it from dev and the question is settled negatively. If M.7 says "structured," La Liga becomes the canonical use case for the sharp-weighted variants.

### Out of scope for next week

- **Per-league per-book sharpness from historical CLV.** Needs ≥ 50 settled bets per book per league. Won't have that data for months.
- **Hierarchical / Bayesian shape models.** M.7 starts simple (Spearman); upgrade only if the simple metric is too noisy.
- **Production AH-CLV pipeline.** R.10 territory; blocked on the dev probe producing initial CLV signal.
- **Paid Odds API tier.** Unchanged trigger: ≥50 settled bets with positive average CLV on at least one paper variant.

### The hierarchy of "out of scope" if this week slips

If only some phases land, prioritise in this order:
1. **D.1 (Pi pull)** — must happen unless something is genuinely broken.
2. **M.3 (prod leagues)** — small, low-risk, immediate budget realisation.
3. **M.7 (dispersion shape)** — diagnostic; cheap; informs everything downstream.
4. **M.6 (configurable weights)** — refactor; backwards-compatible; can land independently.
5. **M.4 (dev sandbox)** — biggest piece; can slip a week without losing the thread.

If D.1 and M.3 land but the rest slips, that's still a successful week — prod is in a better place and dev can wait.
