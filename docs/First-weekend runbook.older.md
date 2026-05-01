# First-weekend runbook (day-by-day) — 2026-05-01 (Fri) → 2026-05-04 (Mon)

Day-by-day playbook for the first live weekend. Tells you what fires when, what to look at after each fire, and what "good" looks like by Monday morning.

The companion doc `FIRST_WEEKEND.md` holds the live evaluation log + the WSL/Pi divergence table; this one is the per-day "what to check" walk-through. **Read `FIRST_WEEKEND.md` first** for the snapshot of what's running where.

All times **UTC** with **BST** in brackets (BST = UTC+1).

---

## Pre-flight checks — do these now, before Friday's cron fires

These confirm the new infrastructure (R.0–R.3, R.7, R.8, R.11, A.0–A.7, A.5.5) is healthy. Each takes <30 s. **Don't skip.**

```bash
cd /home/rfreire/projects/bets

# 1. xG file healthy (feeds K_draw_bias). Expect ~76 teams across EPL/BL/SA/L1.
python3 -c "import json; d=json.load(open('logs/team_xg.json')); print(f\"{len(d['teams'])} teams, q25={d['xg_q25']}, updated={d['updated']}\")"

# 2. All 16 variants configured
python3 -c "from src.betting.strategies import STRATEGIES; print(f'{len(STRATEGIES)} variants:', [s.name for s in STRATEGIES])"
# expected: 16 names A_production through P_max_odds_shopping

# 3. Tests all green
python3 -m pytest -q
# expected: 263 passed

# 4. Cron entries installed (8 entries on both machines: 5 football scans + 1 backup + 1 FDCO backfill + 1 check_sports; Pi has 3 extra: xG refresh + research curated + research open)
crontab -l | grep -cE 'scan_odds|backfill_clv|refresh_xg|research_scan|backup|check_sports'
# expected: ≥8 (WSL); ssh robert@192.168.0.28 'crontab -l | grep -cE "scan_odds|backfill_clv|refresh_xg|research_scan|backup|check_sports"' should be ≥11

# 5. WSL Azure paths active (A.4 dual-write + A.5.5 blob archive)
export $(cat .env.dev) && python3 -c "
import os, sys; sys.path.insert(0, '.')
from src.storage.repo import BetRepo
from src.storage.snapshots import SnapshotArchive
print('A.4 dual-write enabled:', BetRepo().db_enabled)
print('A.5.5 blob archive enabled:', SnapshotArchive().enabled)
"
# expected: both True on WSL

# 6. Pi safety contract still holds (no Azure libs imported when env unset)
ssh robert@192.168.0.28 '.venv/bin/python3 -c "import pyodbc"' 2>&1 | grep -q "ModuleNotFoundError" && echo "Pi pyodbc absent: OK"
ssh robert@192.168.0.28 '.venv/bin/python3 -c "import azure.storage.blob"' 2>&1 | grep -q "ModuleNotFoundError" && echo "Pi azure-storage-blob absent: OK"

# 7. Public Azure dashboard reachable
curl -s https://kaunitz-dev-dashboard-rfk1.orangebush-7e5af054.uksouth.azurecontainerapps.io/health
# expected: {"db":"ok","csv":"ok"}

# 8. Smoke-run a scan manually to confirm all 16 paper variants write rows (uses 1 dev API call)
export $(cat .env.dev) && python3 scripts/scan_odds.py --sports football 2>&1 | tail -30
ls -la logs/paper/  # CSVs for variants that flagged

# 9. Comparison report regenerates
python3 scripts/compare_strategies.py 2>&1 | tail -3
```

**If anything fails, fix before Friday 19:30 UTC.** Common fixes:
- xG file missing: `python3 scripts/refresh_xg.py` (Pi-side; needs network)
- Tests fail: read failure; do not start the weekend with red tests
- A.4/A.5.5 not enabled on WSL: re-check `.env.dev` has `BETS_DB_WRITE=1` + `BLOB_ARCHIVE=1` + KV references
- Pi imports Azure libs: `git pull` on Pi missed something; A.4/A.5.5 must stay dormant
- Cron count off: re-add via `crontab -l | crontab -`

---

## Schedule overview

| UTC | BST | What | Days | Pi | WSL |
|---|---|---|---|---|---|
| 07:30 | 08:30 | Football scan (fresh weekly lines) | Tue | ✅ | ✅ |
| 19:30 | 20:30 | Football scan (Fri lineup hints) | Fri | ✅ | ✅ |
| 10:30 | 11:30 | Football scan (pre-12:30 KO) | Sat | ✅ | ✅ |
| 16:30 | 17:30 | Football scan (between 15:00 and 17:30 KOs) | Sat | ✅ | ✅ |
| 12:30 | 13:30 | Football scan (pre-Sun afternoon) | Sun | ✅ | ✅ |
| 03:00 | 04:00 | `bets.csv` snapshot to `bets.csv.bak.<date>` (14d retention on the **snapshots**; live file never touched) | every day | ✅ | ✅ |
| 08:00 | 09:00 | FDCO CLV backfill | Mon | ✅ | ✅ |
| 08:00 | 09:00 | `check_sports.py` (sports discovery) | 1st & 15th | ✅ | ✅ |
| 06:00 | 07:00 | xG refresh (Understat → `logs/team_xg.json`) | Mon | ✅ | ❌ |
| 10:00 | 11:00 | Research scanner — curated | Mon | ✅ | ❌ |
| 10:00 | 11:00 | Research scanner — open-search | 1st of month | ✅ | ❌ |

**Removed (don't expect them this weekend):**
- Closing-line + drift snapshot every 5 min — `closing_line.py` paused 2026-05-01; CLV now from FDCO backfill on Monday only.
- NBA scans (Mon–Fri 17:00) — dropped to free API budget.
- Tennis scans (Mon, Thu 09:00) — dropped to free API budget.
- Mon + Fri 07:30 football scans — dropped to free API budget.

**Why some Pi-only.** `research_scan.py` writes to git-tracked `docs/RESEARCH_FEED.md` (would conflict if both ran). `refresh_xg.py` writes to local `logs/team_xg.json` — Pi-canonical.

---

## Friday 2026-05-01

| UTC | BST | Job | Where | What to check |
|---|---|---|---|---|
| 03:00 | 04:00 | Daily backup | both | `ls logs/bets.csv.bak.2026-05-01` after 04:05 (and same on Pi). |
| 19:30 | 20:30 | Football scan (lineup hints) | both | `tail -100 logs/scan.log`. Look for `Flagged N value bets` line. Compare API call count — should be ~7–10 (paper variants share the API fetch). Pi may push to phone for any HIGH-confidence bet; WSL stays silent. |

**Throughout the day:**
- After Friday's scan: any newly-flagged bets in the Azure dashboard's **Suggested** section? Decide stakes for the ones you'll actually back.

**End-of-day check:**
- `wc -l logs/bets.csv` on both machines — Pi rows from the validation scan should already be there.
- WSL only: `az storage blob list --account-name kaunitzdevstrfk1 -c raw-api-snapshots --auth-mode key --num-results 5 -o table` — should show 1–2 blobs from the 19:30 scan.

---

## Saturday 2026-05-02

| UTC | BST | Job | Where | What to check |
|---|---|---|---|---|
| 03:00 | 04:00 | Daily backup | both | `ls logs/bets.csv.bak.2026-05-02`. |
| 10:30 | 11:30 | Football scan (pre-12:30 KO) | both | The big one. `tail -150 logs/scan.log` on Pi (notifications fire). On WSL, `tail -150 logs/scan.log` should show `[ntfy] Disabled` lines. This is where weekend volume usually lands. |
| 16:30 | 17:30 | Football scan (pre-evening KOs) | both | Catches late-moving lines on the 17:30 BST kickoffs. |

**Throughout the day (per kickoff window):**
- After each EPL kickoff (12:30, 15:00, 17:30 BST): peek at the Azure dashboard. New bets logged? Any actual stakes you've placed should be marked via the dashboard *before* the match starts so they end up in **Placed — awaiting result**.
- After each match settles: log W/L/V on the dashboard. P&L appears immediately. **CLV stays empty until Monday morning** — that's by design now.

**Variant-specific expectations** (so you know what's a bug vs by-design 0):
- **`K_draw_bias`**: very narrow filter (draws ∈ [3.20, 3.60] AND both teams below `xg_q25=1.198`, EPL/Bundesliga/Serie A/Ligue 1 only). Expect **0–2 flags across the whole weekend**. 0 is not a bug.
- **`O_kaunitz_classic`**: paper-faithful baseline (α=0.05, raw consensus, max-odds shopping, min 4 books). Should flag **materially more** than A_production. If `O` CSV is empty, *that's* suspicious.
- **`M_min_prob_15` / `N_competitive_only`**: subsets of A's flagging logic with extra prob filters. Bet count should be **≤ A_production's**. If they flag more, filter direction is wrong.
- **`L_quarter_kelly`**: identical flag count to A; only the `stake` column differs (0.4× instead of 0.5×).

**End-of-day check:**
- `awk -F, 'NR>1 && $1==strftime("%Y-%m-%d")' logs/bets.csv | wc -l` — count of bets flagged today on both machines.
- `ls -la logs/paper/` — count of variant CSVs that fired today. By Sunday EOD, expect ≥10 of the 16 to have at least one row.
- WSL only: blob archive growth — `az storage blob list --account-name kaunitzdevstrfk1 -c raw-api-snapshots --auth-mode key --num-results 50 -o table | tail -20`. After two Saturday scans, expect ≥4 blobs (each scan produces 1–2 calls: one h2h+totals + maybe one `/sports/` canary).
- WSL only: dual-write parity — `wc -l logs/bets.csv` vs the Azure SQL `bets` row count (commands in `FIRST_WEEKEND.md` Quick reference).

---

## Sunday 2026-05-03

| UTC | BST | Job | Where | What to check |
|---|---|---|---|---|
| 03:00 | 04:00 | Daily backup | both | `ls logs/bets.csv.bak.2026-05-03`. |
| 12:30 | 13:30 | Football scan (pre-afternoon games) | both | `tail -150 logs/scan.log`. Last football scan of the weekend. |

**Throughout the day:** same as Saturday — log actual stakes before kickoff, log results after settlement. CLV column stays empty (Mon backfill territory).

**End-of-day weekend wrap-up:**
- Dashboard: how many bets placed? How many won/lost?
- **Avg CLV / Drift→you tiles:** should still be empty Sunday night — both backfill Monday morning.
- Optional: if you can't wait for Monday cron, run the FDCO backfill manually:
  ```bash
  # Dry-run first
  export $(cat .env.dev) && python3 scripts/backfill_clv_from_fdco.py --dry-run | head -20
  # Real run
  ssh robert@192.168.0.28 'cd ~/projects/bets && export $(cat .env) && .venv/bin/python3 scripts/backfill_clv_from_fdco.py 2>&1 | tail -20'
  ```
  Note: FDCO publication is typically ~24h after match settlement, so Sunday-evening fixtures may not have FDCO rows yet even on Monday morning. Don't read into a "0 rows updated" message for the most recent fixtures.

---

## Monday 2026-05-04

The "did the system survive the weekend" day, plus the **first scheduled FDCO CLV backfill** and the first Monday research scanner fire.

| UTC | BST | Job | Where | What to check |
|---|---|---|---|---|
| 03:00 | 04:00 | Daily backup | both | `ls logs/bets.csv.bak.2026-05-04`. |
| 06:00 | 07:00 | xG refresh | Pi only | `tail -30 logs/refresh_xg.log` after 07:05 BST. Confirm: 4 leagues fetched, ~76 teams written, no `understat`/`aiohttp` import error. `python3 -c "import json; d=json.load(open('logs/team_xg.json')); print(d['updated'])"` should show today's date. |
| 07:30 | 08:30 | Football scan (fresh weekly lines) | both | `tail -100 logs/scan.log`. |
| 08:00 | 09:00 | **FDCO CLV backfill (first scheduled fire)** | both | `tail -50 logs/backfill_clv.log` after 09:05 BST. Expect entries like `[backfill_clv] Updated N rows in <file>` for `bets.csv` and each `paper/<variant>.csv`. **This is the eval gate** — populated `pinnacle_close_prob` cells appear from this point onward. Run on both machines independently; same source, similar updates. |
| 10:00 | 11:00 | Research scanner — curated | Pi only | `tail -50 logs/research.log` after 11:05 BST. Confirm: `claude` on PATH (no `command not found`), exit 0. New `## Run 2026-05-04 …` heading at the top of `docs/RESEARCH_FEED.md`. Dashboard's Research tile should show today's date. |

**Monday review (the real point of the weekend):**
- Dashboard P&L for the weekend.
- **Avg CLV** with sample size — even 5–10 settled bets is a useful signal of direction.
- Run `python3 scripts/compare_strategies.py` (both machines) and read the per-variant table.
- Any HIGH-confidence bets that lost? Worth eyeballing — not all losers are bad if CLV was positive.
- `wc -l logs/scan.log logs/backfill_clv.log logs/research.log` — sanity check log sizes.
- **Pi A.5.5 / A.4 activation decision:** per user, defer to post-weekend. Once decided, follow the Pi-side smoke commands in `FIRST_WEEKEND.md`.

---

## Paper-strategy A/B test (the real point of this weekend)

**Sixteen** strategy variants run in shadow alongside production. Every football scan evaluates *all sixteen* and appends to `logs/paper/{A..P}_*.csv`. **Only A_production fires real notifications and lands in `logs/bets.csv`** — the other fifteen are paper-only.

The original eight (A–H) were the Phase 5.5 set; the new eight (I, J, K, L, M, N, O, P) shipped in the R.0–R.3 + R.8 sprint. Pre-weekend bet counts are tracked in `docs/STRATEGY_COMPARISON.md`; current counts come from `python3 scripts/compare_strategies.py`.

| Variant | What it tests vs A |
|---|---|
| **A_production** | Mirrors live: Shin, mean consensus, all UK books, 3% edge, no model gate |
| **B_strict** | Pinnacle-weighted 5×, 5% edge, dispersion≤0.04 |
| **C_loose** | 2% edge instead of 3% |
| **D_pinnacle_only** | Edge measured vs Pinnacle alone, no consensus |
| **E_exchanges_only** | Restrict to Betfair Ex / Smarkets / Matchbook |
| **F_model_primary** | Model edge ≥3% on h2h only; consensus gate off |
| **G_proportional** | Proportional de-vig instead of Shin |
| **H_no_pinnacle** | Drop Pinnacle from consensus |
| **I_power_devig** | Power devig instead of Shin (R.1) |
| **J_sharp_weighted** | Sharpness-weighted consensus (datagolf seed; R.2) |
| **K_draw_bias** | Draws only on low-xG fixtures, odds ∈ [3.20, 3.60] (R.8) |
| **L_quarter_kelly** | Kelly fraction = 0.4 instead of 0.5 (R.1) |
| **M_min_prob_15** | Reject sides with consensus prob < 0.15 — longshot guard (R.1) |
| **N_competitive_only** | Only fixtures with consensus ∈ [0.30, 0.70] (R.1) |
| **O_kaunitz_classic** | Paper-faithful baseline: α=0.05, raw consensus, max-odds shopping (R.1.5) |
| **P_max_odds_shopping** | A_production logic + best-priced UK book per side (R.1.6) |

### What to do over the weekend

- **Don't change variant configs.** They're frozen; mid-weekend changes invalidate the A/B sample.
- **Don't act on B–P signals.** They're informational. If you bet something *only* B flagged but A didn't, you pollute both samples.
- **Do bet A's signals as you'd normally.** A is the production line.

### Monday morning: run the comparison

After the FDCO backfill fires at 08:00 UTC and populates Pinnacle close probs:

```bash
cd /home/rfreire/projects/bets
python3 scripts/compare_strategies.py
cat docs/STRATEGY_COMPARISON.md
```

What you're looking for in the refreshed report:

**Main per-variant table:**
1. **All 16 variants present** — even 0-bet ones. If any is missing, the `STRATEGIES` import broke.
2. **`[low n]` prefix** on variants with `<10` CLV bets — most rows this first weekend. Don't read into rankings of `[low n]` rows; the CI bracket is the honest signal.
3. **`Avg CLV ± 95% CI`** — *the headline*. A variant whose CI bracket includes 0 has not yet shown a statistically distinguishable signal.
4. **`Med CLV`** — robust to outliers. If `Avg CLV` and `Med CLV` differ by more than 2pp, trust the median on small samples.
5. **`CLV >0 %`** — breadth check. High avg but only 30% positive-CLV rate = tail-driven skew.
6. **`Avg Edge`** — F_model_primary's tends to be lower because it ignores consensus edge. Expected.
7. **`Top books`** — bookmaker concentration.

**Note:** **Drift columns and "Drift→you %" tiles will be EMPTY** this weekend — `closing_line.py` is paused so no T-60/T-15/T-1 captures occurred. The drift comparison framework in `compare_strategies.py` will silently render 0 rows. This is by design until/unless we revive closing-line capture or build a drift-from-FDCO replacement.

**Per-sport / per-confidence / per-market sections** — render only when at least one (variant, X) pair has ≥10 CLV bets, except A_production which appears with ≥1.

**Per-model-signal section** (`agrees / disagrees / no_signal`): F_model_primary should be almost entirely in `agrees`. If not, the model-signal column isn't being captured at write time.

### Sanity rule before promoting any variant

**Do not promote on one weekend.** Minimum bar:

- **≥30 CLV bets** in the candidate variant.
- **Avg CLV CI bracket excludes 0** on the high side (`Avg CLV − 1.96·SE > 0`). Point estimate without CI is not enough.
- **Avg CLV beats A_production's CI upper bound** — not just A's point estimate.
- **CLV >0 rate ≥ 50%**.
- **Holds across at least three weekends.**

Anything else is noise.

---

## What "good" looks like by Monday evening

- Daily backups exist on both machines: `logs/bets.csv.bak.{2026-05-01..04}`.
- `logs/bets.csv` and `logs/paper/*.csv` grew on both machines this weekend.
- `logs/backfill_clv.log` shows successful FDCO updates for top-6 league bets (EPL, Bundesliga, Serie A, Ligue 1, Championship, Bundesliga 2). Rows for fixtures published in FDCO since Friday should have populated `pinnacle_close_prob` + `clv_pct` cells.
- **Avg CLV tile** appears on the dashboard once Monday's backfill finishes.
- **Drift→you tile** stays absent (drift capture is paused — expected).
- `docs/STRATEGY_COMPARISON.md` shows non-zero CLV bets for at least A, C, D, F, G, H. New I–P variants enter with 0 — any non-zero count on those is the new signal to watch.
- Research scanner fired Monday 10:00 UTC (Pi only) and `docs/RESEARCH_FEED.md` has a new `## Run 2026-05-04` section.
- **WSL Azure SQL row counts match WSL CSV row counts.**
- **Blob archive ≥ 10 blobs** for the weekend (5 scans × ~2 calls each).
- **No `[snapshots] WARN`** entries in the WSL scan log.
- **No `pyodbc` or `azure.storage.blob` imports on Pi** (lazy-import contracts intact).
- Phone got a sensible number of pushes from Pi — not so many you ignored them, not so few that something's broken.

---

## What to do if something looks wrong

| Symptom | First check |
|---|---|
| No bets all weekend | `tail -200 logs/scan.log` for an HTTP error (Odds API blocked?) or "min books" rejection lines. |
| Phone got no pushes (Pi) | `curl -d "test" ntfy.sh/robert-epl-bets-m4x9k` — if it arrives, issue is in `scan_odds.py` notification code, not ntfy. |
| `pinnacle_close_prob` empty Monday after backfill cron | `tail -200 logs/backfill_clv.log`. Common causes: (1) FDCO hasn't published the fixture yet (24h delay typical, especially for Sun-evening matches); (2) team-name mismatch — check `src/betting/team_names.py` mapping; (3) market type isn't h2h or totals 2.5 (FDCO carries no others). |
| Drift columns empty in `STRATEGY_COMPARISON.md` | **Expected.** `closing_line.py` paused → no drift capture. Empty drift is by design until/unless we replace it. |
| Research scanner failed Monday | `tail -50 logs/research.log` on Pi — most likely cron-env doesn't have `claude` on PATH; `which claude` and add `PATH=…` to crontab. |
| `K_draw_bias` flagged 0 all weekend | Probably correct (very narrow filter). Confirm: `python3 -c "import json; d=json.load(open('logs/team_xg.json')); print(len(d['teams']), d['xg_q25'])"` should show ~76 teams, q25 ~1.2. If file missing, K silently rejects every match — re-run `python3 scripts/refresh_xg.py` on Pi. |
| `STRATEGY_COMPARISON.md` missing variants | `python3 -c "from src.betting.strategies import STRATEGIES; print(len(STRATEGIES))"` should print 16. Import errors → report falls back to glob-only and silently drops 0-bet variants. |
| Mon `logs/refresh_xg.log` shows `understat`/`aiohttp` ImportError | Pi: `pip install understat aiohttp` (in the correct venv). Script gracefully keeps existing `team_xg.json` if deps are missing, so K won't break — but the snapshot will be stale. |
| **WSL Azure SQL row count drifts from CSV row count** | `tail -200 logs/scan.log` for `[repo] WARN: DB insert failed`. The dual-write design isolates failures (CSV still appends), but persistent DB insert failures must be debugged before A.8 cutover. Likely: (1) firewall changed your IP; (2) serverless DB exceeded auto-pause delay during the scan; (3) KV secret rotated. |
| **WSL `[snapshots] WARN` entries in scan log** | Blob upload failures → `logs/snapshots/` accumulating local files. Check: (1) storage account reachable (`az storage blob list ...`); (2) KV secret valid; (3) the local buffer drains on next successful run. If buffer keeps growing, investigate before Pi gets activated post-weekend. |
| **Pi imports `pyodbc` or `azure.storage.blob`** | Pi-safety contract violated. `git pull` on Pi must NOT install Azure libs. Likely cause: `pip install -r requirements.txt` ran with Azure deps — those should be optional / dev-only. Investigate and remove. |
| Public dashboard returns 500 | `az containerapp logs show -g kaunitz-dev-rg -n kaunitz-dev-dashboard-rfk1 --tail 50 --type console`. Common: serverless DB still resuming (first hit after 60-min idle takes 5–15 s). Retry. |
| Public dashboard returns 401 with browser UA | OAuth flow broken or Google Testing-mode allowlist changed. `az containerapp auth show -g kaunitz-dev-rg -n kaunitz-dev-dashboard-rfk1` — verify `enabled: true`, allowed providers, and `DASHBOARD_ALLOWED_EMAILS` env var. |
