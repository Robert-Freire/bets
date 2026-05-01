# Market & League Coverage Plan — 2026-05

Goal: free up Odds API budget by dropping the unused `totals` market, then use the freed credits to (a) probe new leagues for value, (b) start an Asian Handicap probe in dev. Production stays minimal (h2h-only); dev becomes the exploration sandbox.

> **Why now.** Per `logs/scan.log` (2026-05-01) and CLAUDE.md API-budget block: each league fetch costs `regions × markets` = `uk,eu × h2h,totals` = 4 credits. Of 847 paper-flagged bets across 16 variants, **0 are from totals or BTTS** — 100% are h2h. Dropping `totals` halves per-call cost (4 → 2 cr), freeing ~260 cr/month per key. That headroom funds league expansion and an AH probe without paying for paid tier.

This doc is self-contained for asynchronous bot execution. Follow the same protocol as `docs/PLAN_RESEARCH_2026-04.md` (branch naming, commit format, PR template, verifier checks). Phases are sized for one PR each.

---

## Phase status tracker

| Phase | Title | Status |
|---|---|---|
| M.0 | Drop `totals` market + remove dead BTTS try-block | pending |
| M.1 | League-coverage probe script + run | pending |
| M.2 | Move league list to `config.json`; env-overridable per host | pending (depends on M.0) |
| M.3 | Add passing leagues to prod config | pending (depends on M.1, M.2) |
| M.4 | Add `spreads` market + same leagues to dev config | pending (depends on M.2, M.3) |
| M.5 | Doc + memory updates; delete this plan | pending (depends on M.4) |

**Dependency graph**

```
M.0 ─┬─ M.2 ─┬─ M.3 ─┬─ M.4 ─ M.5
M.1 ─┘       │       │
             └───────┘
```

---

## Phase M.0 — Drop `totals` market

**Goal.** Cut per-call cost from 4 → 2 credits by requesting only `h2h`. Remove the BTTS try-block, which 422s silently on free tier and writes no useful data.

**Inputs.** None.

**Outputs.**
- `scripts/scan_odds.py` requests `markets=h2h` only.
- BTTS code path deleted, not commented out.
- Test added confirming the request URL contains only `h2h`.

**Tasks.**
1. In `scripts/scan_odds.py:249-277` (`fetch_odds`), change the markets param from `"h2h,totals"` to `"h2h"`.
2. Delete the `try/except` block that fetches BTTS and merges into events.
3. Search for downstream references to totals/BTTS markets in the same file (e.g. consensus, paper-portfolio writers) — they should remain functional for any historical CSV rows but never fire on new scans. Do **not** strip totals/BTTS handling elsewhere; leave the code paths in place so backfill scripts that read past data still work.
4. Add `tests/test_scan_odds_markets.py` with a test that monkeypatches `api_get` and asserts the params dict contains `markets="h2h"` exactly.

**Acceptance.**
- [ ] `fetch_odds` issues exactly one HTTP call per league, with `markets=h2h`.
- [ ] No BTTS-related HTTP calls remain.
- [ ] `pytest -q` passes (existing 263 tests + new one).
- [ ] Manual scan in dev (`export $(cat .env.dev) && python3 scripts/scan_odds.py`) ends with quota delta ≈ 12 (6 leagues × 2 cr) — confirm via `grep "API quota remaining" logs/scan.log | tail -2`.

**Reviewer focus.**
- Confirm no totals/BTTS code path is silently re-introducing requests.
- Confirm `logs/paper/*.csv` writers still tolerate rows missing totals/BTTS columns (they always wrote `""` when absent — should be no-op).

**Verification commands.**
```bash
pytest -q tests/test_scan_odds_markets.py
grep -n "h2h,totals\|markets.*btts" scripts/scan_odds.py   # expect: no matches
export $(cat .env.dev) && python3 scripts/scan_odds.py
tail -3 logs/scan.log   # expect ≈12-credit delta on the API quota line
```

---

## Phase M.1 — League coverage probe

**Goal.** Empirically measure UK book coverage and Shin-dispersion for candidate new leagues, so prod/dev cron decisions are data-driven rather than guessed.

**Inputs.** Dev API key (`.env.dev`).

**Outputs.**
- `scripts/probe_league.py` — one-shot script that fetches a single league's odds and prints (n_fixtures, avg_books, max_dispersion, n_value_bets_at_3pct).
- `docs/LEAGUE_COVERAGE_2026-05.md` — coverage table populated from probe runs.

**Candidate league keys** (Odds API `sport_key` → FDCO code for future CLV):

| League | Odds API key | FDCO |
|---|---|---|
| La Liga | `soccer_spain_la_liga` | `SP1` |
| La Liga 2 | `soccer_spain_segunda_division` | `SP2` |
| Eredivisie | `soccer_netherlands_eredivisie` | `N1` |
| Primeira Liga | `soccer_portugal_primeira_liga` | `P1` |
| Ligue 2 | `soccer_france_ligue_two` | `F2` |

**Tasks.**
1. Write `scripts/probe_league.py` accepting `--sport <key>` and reusing `scan_odds.fetch_odds` + `scan_odds._build_consensus` to compute the dispersion and Kaunitz hits without writing to any CSV or sending ntfy. Output one summary line per fixture and a totals line.
2. Run probe against each candidate (`export $(cat .env.dev) && python3 scripts/probe_league.py --sport soccer_spain_la_liga` etc). Total cost: 5 leagues × 2 cr = 10 credits.
3. Write `docs/LEAGUE_COVERAGE_2026-05.md` with one row per league: `n_fixtures, avg_books, p95_dispersion, n_3pct_hits`.
4. Decision rule for promotion to prod cron (M.3): `avg_books >= 20 AND p95_dispersion <= 0.04`. To dev cron (M.4): `avg_books >= 15` (dev tolerates noisier sets to gather signal).

**Acceptance.**
- [ ] `scripts/probe_league.py` runs without modifying any persistent state (`logs/bets.csv`, `logs/paper/`, `logs/notified.json`, ntfy).
- [ ] `docs/LEAGUE_COVERAGE_2026-05.md` exists with the 5 candidates' numbers.
- [ ] Dev key quota delta ≈ 10 cr from a clean baseline.

**Reviewer focus.**
- Verify the probe script does not write to any of: `logs/bets.csv`, `logs/paper/*`, `logs/scan.log`, `logs/notified.json`, Azure SQL, blob archive, ntfy. It is read-only.

**Verification commands.**
```bash
git diff --name-only main..HEAD   # expect: scripts/probe_league.py, docs/LEAGUE_COVERAGE_2026-05.md
grep -E "csv|ntfy|BetRepo|SnapshotArchive|append" scripts/probe_league.py   # expect: no matches
export $(cat .env.dev) && python3 scripts/probe_league.py --sport soccer_spain_la_liga
```

---

## Phase M.2 — Externalise league list to `config.json`

**Goal.** Allow Pi (prod) and WSL (dev) to run different league sets and market sets without forking the script.

**Inputs.** None (depends on M.0 having landed so the markets param is dynamic, not hardcoded).

**Outputs.**
- `config.json` gains `leagues` array and `extra_markets` array.
- `scripts/scan_odds.py` reads `FIXED_SPORTS` from config; falls back to current hardcoded list if absent (back-compat for Pi until config rolls out).
- `LEAGUES_CONFIG` env var optionally points at an alternate config file (e.g. `config.dev.json`).

**Tasks.**
1. Update `config.json` schema:
   ```json
   {
     "canary_league": "soccer_epl",
     "leagues": [
       {"key": "soccer_epl", "label": "EPL", "min_books": 20},
       {"key": "soccer_germany_bundesliga", "label": "Bundesliga", "min_books": 20},
       {"key": "soccer_italy_serie_a", "label": "Serie A", "min_books": 20},
       {"key": "soccer_efl_champ", "label": "Championship", "min_books": 25},
       {"key": "soccer_france_ligue_one", "label": "Ligue 1", "min_books": 20},
       {"key": "soccer_germany_bundesliga2", "label": "Bundesliga 2", "min_books": 20}
     ],
     "extra_markets": []
   }
   ```
2. Refactor `scripts/scan_odds.py:154-159` to load this list at startup. Resolve config path: `LEAGUES_CONFIG` env var → `config.json`. Validate that each entry has the three required keys.
3. In `fetch_odds`, build the `markets` param from `["h2h"] + config.extra_markets` joined by comma. So a config with `extra_markets=["spreads"]` issues `markets=h2h,spreads` (cost = 4 cr).
4. Update `src/storage/_keys.py:SPORT_LABEL_MAP` and `src/data/downloader.py:DOWNLOAD_MAP` to include all candidate leagues (so adding to config later doesn't require code changes). FDCO codes from the M.1 table.
5. Tests: `tests/test_config_loader.py` covers (a) default load, (b) missing config falls back to hardcoded list, (c) `extra_markets` reaches the request.

**Acceptance.**
- [ ] `config.json` includes `leagues` and `extra_markets` arrays.
- [ ] `scripts/scan_odds.py` reads from config; passes existing 263 tests + new ones.
- [ ] `LEAGUES_CONFIG=/tmp/empty.json python3 scripts/scan_odds.py` raises a clear error (missing required keys), not a silent default.
- [ ] `SPORT_LABEL_MAP` and `DOWNLOAD_MAP` cover all 5 candidate leagues from M.1.

**Reviewer focus.**
- Pi safety: if `git pull` lands this on Pi before the new config exists, behaviour must be byte-identical to today. Verify by removing `leagues` key from local `config.json` and confirming the scanner still runs the original 6 leagues.
- No leaked env vars between dev/prod paths.

**Verification commands.**
```bash
pytest -q tests/test_config_loader.py
python3 -c "import json; c=json.load(open('config.json')); assert 'leagues' in c and len(c['leagues'])==6"
export $(cat .env.dev) && python3 scripts/scan_odds.py   # baseline still works
```

---

## Phase M.3 — Add probe-passing leagues to **prod** config

**Goal.** Expand prod cron to include leagues that passed M.1's promotion bar (`avg_books >= 20 AND p95_dispersion <= 0.04`).

**Inputs.** `docs/LEAGUE_COVERAGE_2026-05.md` (from M.1).

**Outputs.**
- `config.json` `leagues` array extended with the passing leagues.
- `min_books` per league set from probe data: `floor(avg_books × 0.7)` rounded to nearest 5.

**Tasks.**
1. From the coverage doc, identify leagues that meet the prod bar.
2. Add each to the `leagues` array in `config.json`. Use the FDCO code already mapped in M.2.
3. Verify the new prod monthly budget: `(6 + N_added) × 2 cr × 5 scans/wk × 4.345 wk` ≤ **450 cr/mo** (leaves 50 cr buffer for canary/sports check). If over, drop the lowest-priority league(s) until it fits.
4. Update `CLAUDE.md` "Sports actively scanned" table.

**Acceptance.**
- [ ] Only leagues passing the bar are added.
- [ ] Computed monthly burn ≤ 450 cr.
- [ ] `CLAUDE.md` table reflects the new set.
- [ ] Pi will pull this and run identically (no schema changes since M.2).

**Reviewer focus.**
- Recompute the budget yourself; don't trust the PR body.
- Confirm no league with `avg_books < 20` slipped in.

**Verification commands.**
```bash
python3 -c "
import json
c = json.load(open('config.json'))
n = len(c['leagues'])
markets_per_call = 1 + len(c.get('extra_markets', []))
budget = n * 2 * markets_per_call * 5 * 4.345
print(f'leagues={n}  markets={markets_per_call}  monthly={budget:.0f}')
assert budget <= 450, f'over budget: {budget}'
"
```

---

## Phase M.4 — Add `spreads` + same leagues to **dev** config

**Goal.** Dev runs the prod league set plus the AH (Asian Handicap) probe via `extra_markets=["spreads"]`. Generates AH bet candidates in `logs/paper/Q_asian_handicap.csv` (new variant scaffold).

**Inputs.** Prod config from M.3.

**Outputs.**
- `config.dev.json` — copy of `config.json` with `extra_markets: ["spreads"]`.
- `.env.dev` updated to `LEAGUES_CONFIG=config.dev.json`.
- `src/betting/strategies.py` — new variant `Q_asian_handicap` that filters to `market_type == "spreads"` rows.
- `scripts/scan_odds.py` — paper-portfolio writer wires Q variant alongside existing A–P.

**Budget check.** Dev burn = `N_leagues × 2 cr × (1 + 1) markets × 5 × 4.345`. With 9 leagues (6 baseline + 3 added in M.3) and `spreads` = 4 cr/league = `9 × 4 × 5 × 4.345` = **782/mo**. Over 500.

**Mitigations** (pick one based on M.3 outcome):
- (a) Reduce dev cron to 3 scans/wk (Tue+Sat 16:30+Sun): `9 × 4 × 3 × 4.345` = **469/mo** ✓
- (b) Apply `spreads` only to top-4 leagues (EPL+Bundes+Serie A+Ligue 1) and h2h-only on the rest. Requires per-league `extra_markets` override (extends M.2 schema). `4×4×5×4.345 + 5×2×5×4.345` = 348 + 217 = **565/mo** ✗
- (c) (a) + drop two lower-priority leagues from dev: `7 × 4 × 5 × 4.345` = **608/mo** ✗

**Decision rule:** default to (a). Re-evaluate cadence after one month of AH data.

**Tasks.**
1. Create `config.dev.json` with `extra_markets: ["spreads"]`.
2. Update `.env.dev` to set `LEAGUES_CONFIG=config.dev.json`.
3. Edit WSL crontab (`crontab -l | crontab -`) to drop dev scans down to 3/wk: keep Tue 07:30 + Sat 16:30 + Sun 12:30; remove Fri 19:30 + Sat 10:30. Pi crontab **unchanged**.
4. Add `Q_asian_handicap` to `src/betting/strategies.py` STRATEGIES list. Filter rule: `market_type == "spreads"` and `edge_pct >= 0.03`. Use h2h's risk-pipeline configuration.
5. Wire the new variant into `scripts/scan_odds.py` paper-portfolio dispatch.
6. Tests: extend `tests/test_strategies.py` with a Q-variant fixture (synthetic spreads market data).

**Acceptance.**
- [ ] Dev runs `markets=h2h,spreads`; prod still runs `markets=h2h`.
- [ ] Computed dev monthly burn ≤ 480 cr.
- [ ] WSL crontab shows 3 football scans/wk; Pi crontab unchanged (verify both).
- [ ] `logs/paper/Q_asian_handicap.csv` accumulates rows on next dev scan.
- [ ] All tests pass.

**Reviewer focus.**
- Pi-safety: confirm `config.dev.json` is gitignored OR confirm Pi has `LEAGUES_CONFIG` unset so it falls through to `config.json`. Both must be true; don't assume.
- Confirm the FDCO backfill (`scripts/backfill_clv_from_fdco.py`) has no CLV path for `spreads` rows — they will show empty `clv_pct` indefinitely. This is expected; CLV for AH needs a separate sprint (R.10).
- No ntfy or DB-write side-effects from the Q variant on prod.

**Verification commands.**
```bash
# Dev path
LEAGUES_CONFIG=config.dev.json python3 -c "
import json,os
c=json.load(open(os.environ['LEAGUES_CONFIG']))
print('extra_markets:', c.get('extra_markets'))
"
# Crontab
crontab -l | grep -c "scan_odds"   # expect: 3 on WSL, 5 on Pi
ssh robert@192.168.0.28 'crontab -l | grep -c scan_odds'   # expect: 5
# Smoke test
export $(cat .env.dev) && python3 scripts/scan_odds.py
ls -la logs/paper/Q_asian_handicap.csv
```

---

## Phase M.5 — Doc + memory updates; delete this plan

**Goal.** Reflect the new state in CLAUDE.md and memory; delete this one-time plan per the lean-docs principle.

**Tasks.**
1. Update `CLAUDE.md`:
   - "Sports actively scanned" table → final prod set.
   - "API budget" paragraph → final prod number from M.3.
   - Add a paragraph under "Cron schedule" noting Pi runs 5 scans/wk + Pi config.json; WSL runs 3 scans/wk + config.dev.json with `spreads`.
2. Update memory:
   - `project_cron_schedule.md` — final prod & dev cadence and burn numbers.
   - `project_la_liga_excluded.md` — if La Liga ended up added, replace with a "La Liga added 2026-05" note linking to M.1 coverage data; if still excluded, update with the new evidence.
   - `MEMORY.md` index — adjust the cron-schedule line to point at the new numbers.
3. Delete `docs/PLAN_MARKET_COVERAGE_2026-05.md` (this file) and `docs/LEAGUE_COVERAGE_2026-05.md` (M.1 output, no longer needed once CLAUDE.md is authoritative).

**Acceptance.**
- [ ] `CLAUDE.md` reflects final state.
- [ ] Memory files updated.
- [ ] Both plan + coverage docs deleted from `docs/`.
- [ ] Final commit message: `M.5: finalise market-coverage rollout; delete plan`.

---

## Out of scope

- **Paid tier migration.** Defer until ≥50 settled bets with `clv_pct` populated demonstrate positive avg CLV. Tracked separately.
- **AH closing-line capture.** R.10 in the research plan; blocked on CLV evidence from this AH probe.
- **BTTS market.** Free tier rejects; revisit on paid tier.
- **`us` / `au` regions.** Books we can't actually bet at; not adding.
- **Increasing scan frequency on existing leagues.** Diminishing returns; not adding.
