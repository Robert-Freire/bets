# Market & League Coverage Plan — 2026-05

Goal: free up Odds API budget by dropping the unused `totals` market, then use the freed credits to (a) probe new leagues for value, (b) start an Asian Handicap probe in dev. Production stays minimal (h2h-only); dev becomes the exploration sandbox.

> **Why now.** Per `logs/scan.log` (2026-05-01) and CLAUDE.md API-budget block: each league fetch costs `regions × markets` = `uk,eu × h2h,totals` = 4 credits. Of 847 paper-flagged bets across 16 variants, **0 are from totals or BTTS** — 100% are h2h. Dropping `totals` halves per-call cost (4 → 2 cr), freeing ~260 cr/month per key. That headroom funds league expansion and an AH probe without paying for paid tier.

This doc is self-contained for asynchronous bot execution. Follow the same protocol as `docs/PLAN_RESEARCH_2026-04.md` (branch naming, commit format, PR template, verifier checks). Phases are sized for one PR each.

---

## Phase status tracker

| Phase | Title | Status |
|---|---|---|
| M.0 | Drop `totals` market + remove dead BTTS try-block | ✅ done (PR #17, 2026-05-01) |
| M.1 | League-coverage probe script + run | ✅ done (PR #17, 2026-05-01) |
| M.2 | Move league list to `config.json`; env-overridable per host | ✅ done (PR #17, 2026-05-01) |
| M.4a | La Liga early-add to dev (no AH, no per-league weights) | ✅ done (PR #18, 2026-05-01) |
| M.3 | Add passing leagues to prod config | pending (depends on M.1, M.2; gated on Mon 2026-05-04 post-mortem) |
| M.7 | Dispersion shape analysis per league | **partial** — `scripts/analyse_dispersion.py` shipped 2026-05-01; ran on La Liga (78.3% bimodal). Tests + per-league report across all 11 leagues still pending. |
| M.6 | Configurable book weights via `config.json` | pending (depends on M.7) |
| M.4 | Add `spreads` market + structured experiments to dev config | pending (depends on M.6, M.3, M.4a) |
| M.5 | Doc + memory updates; delete this plan | pending (depends on M.4) |

**Why M.7 before M.6.** Sharp-weighted consensus (`J_sharp_weighted`) only beats flat consensus (`A_production`) in leagues where book disagreement is **structured** — i.e., the same books consistently form a "sharp" cluster vs a "soft" cluster. M.7 measures whether that's true per league. Without that data, M.6's per-league weight overrides would be guesses. M.7 is also the diagnostic that tells us whether to add La Liga to dev as a sharp-weighted experiment or drop it permanently.

**Dependency graph**

```
M.0 ─┬─ M.2 ─┬─ M.3 ──────────────────┬─ M.4 ─ M.5
M.1 ─┴───────┴─ M.7 ─ M.6 ────────────┘
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

## Phase M.4a — La Liga early-add to dev (shipped)

**Goal.** Start collecting La Liga consensus data on dev key from the first eval weekend onward, without waiting for the full M.4 sandbox. Rationale: dev currently mirrors prod exactly (same `config.json`, same scans), so the dev key's headroom is being burned on duplicate fetches. Reallocating ~43 cr/mo to La Liga gets a one-week lead on M.7's analysis (real scan data instead of one-shot probe) and the first concrete signal on whether `J_sharp_weighted`'s existing hardcoded weights beat `A_production` on a high-dispersion league.

**Scope (intentionally minimal).**
- La Liga (`soccer_spain_la_liga`) added to dev league set only.
- No AH market, no `Q_asian_handicap` variant, no per-league `extra_markets` overrides, no per-league `book_weights`. All of those wait for M.4 proper.
- Pi crontab and `config.json` unchanged. Pi never sees La Liga in this phase.

**Outputs (shipped in PR #18).**
- `config.dev.json` — copy of `config.json` with `soccer_spain_la_liga` appended to the `leagues` array.
- `.env.dev` (gitignored) — `LEAGUES_CONFIG=config.dev.json` line added.
- `docs/FIRST_WEEKEND.md` — divergence-check rule scoped to shared leagues; La Liga dev-only noted in the schedule table.

**Mechanism.** WSL's `.env.dev` is loaded by the cron via `export $(cat .env.dev)` before running `scripts/scan_odds.py`. The scanner's `_load_config` (M.2) honours `LEAGUES_CONFIG` and reads `config.dev.json` instead of the default. Pi has no `LEAGUES_CONFIG` set, so it falls through to `config.json` exactly as before — Pi-safety contract holds without code changes.

**Cost.** La Liga = 2 cr × 5 scans/wk × 4.345 wk ≈ **43 cr/mo** added to dev key burn (~260 → ~303). Comfortable under the 500 ceiling.

**What this enables for M.7.** When M.7 runs Tuesday, it can analyse the actual weekend's La Liga scan output (not just a one-shot probe) — captures fixture-level dispersion at the moments the production scanner sees it, including pre-KO line moves. Cluster-persistence scoring is more meaningful with real time-of-scan data.

**What this enables for M.6.** The 16 paper variants run on La Liga immediately with their existing hardcoded configs. By Monday morning, `logs/paper/J_sharp_weighted.csv` and `logs/paper/A_production.csv` have parallel La Liga rows; even before CLV lands, the *count* and *odds spread* of bets between the two variants is informative.

**Acceptance.**
- [x] `config.dev.json` exists with La Liga + the 6 prod leagues.
- [x] `.env.dev` sets `LEAGUES_CONFIG=config.dev.json`.
- [x] Smoke test: `LEAGUES_CONFIG=config.dev.json ODDS_API_KEY=test python3 -c "import scripts.scan_odds as s; print([l['key'] for l in s._CONFIG['leagues']])"` includes `soccer_spain_la_liga`.
- [x] Pi unchanged (no env var set; `config.json` not modified).
- [x] FIRST_WEEKEND divergence check scoped to shared leagues.

**Reviewer focus.**
- Pi-safety: confirm the 7-league `config.dev.json` is **not** copied to Pi by any cron / sync mechanism. If a future automation rsyncs `/home/rfreire/projects/bets/` to Pi, this file becomes a hazard. Currently no such automation exists.
- The first WSL Sat scan after this PR merges should show 7 leagues in the per-scan summary (EPL, Bundesliga, Serie A, Championship, Ligue 1, Bundesliga 2, **La Liga**). Pi's same scan should show 6.

**Verification commands.**
```bash
# Dev config loads with La Liga
LEAGUES_CONFIG=config.dev.json ODDS_API_KEY=test python3 -c "
import scripts.scan_odds as s
print('leagues:', [l['key'] for l in s._CONFIG['leagues']])
"
# After Sat 10:30 dev scan — verify La Liga rows appear
grep -i "la liga" logs/scan.log | tail -5
grep -i "la liga\|spain" logs/paper/A_production.csv logs/paper/J_sharp_weighted.csv | tail -10
```

---

## Phase M.7 — Dispersion shape analysis per league

**Goal.** Characterise the *shape* of book disagreement per league, not just summary statistics. A league's `p95 = 0.083` could mean (a) wide unimodal noise — flat consensus is fine, (b) bimodal clusters of "sharp" and "soft" books — sharp-weighting can exploit it, or (c) no structure — drop the league. Without this diagnostic, M.6's per-league weight overrides would be guesses.

**Status (2026-05-01).** Scaffold shipped: `scripts/analyse_dispersion.py` exists and runs end-to-end. Tested on La Liga (single scan, 60 fixture×outcome rows): **78.3% bimodal** (well above the 30% threshold), Pinnacle/Marathonbet/Matchbook identified as sharp anchors. Pending: tests, per-league report covering all 11 leagues, persistence-across-scans calculation.

**Inputs.** Azure Blob `raw-api-snapshots/odds_api/` (preferred — no API cost) OR fresh dev-scan run (with `--fetch` flag).

**Outputs.**
- `scripts/analyse_dispersion.py` — read-only script that computes shape stats from a single odds snapshot. **Defaults to reading from a local gzipped blob (no API cost); fresh fetch requires `--fetch` flag.** Per memory `feedback_reuse_archived_data.md`.
- `docs/DISPERSION_SHAPES_2026-05.md` — table per league with shape distribution (% unimodal, % bimodal, % no-structure) + cluster persistence score across multiple scans + identity of recurring "low cluster" books.
- `docs/LEAGUE_COVERAGE_2026-05.md` updated with a "shape verdict" column per league.

**Tasks.** (M.7.0 = scaffold done; M.7.1 onward pending)

0. **[done]** Implement `scripts/analyse_dispersion.py` per the design above. Defaults to `--blob <path>`; `--fetch` is opt-in. Runs end-to-end on La Liga.
1. Add `tests/test_dispersion_shape.py`:
   - synthetic unimodal-tight input → `unimodal-tight` verdict.
   - synthetic two-cluster input → `bimodal` verdict.
   - per-book cluster tally on hand-rolled fixtures matches expected counts.
   - blob-loading path round-trips a `SnapshotArchive` envelope correctly.
2. Generate `docs/DISPERSION_SHAPES_2026-05.md` by running the script against the **already-archived blobs** for all 6 prod leagues + La Liga (M.4a) + 4 other M.1 candidates. **Use blob archive — no fresh API calls.** Compute cluster persistence across 3+ scans per league (Fri/Sat/Sun within the same weekend) using Spearman correlation between per-book bias scores across scans.
3. Add a "Shape verdict" column to `docs/LEAGUE_COVERAGE_2026-05.md`:
   - **Add to prod (sharp-weighted)** — bimodal-dominant + persistence ≥ 0.6.
   - **Add to prod (flat consensus fine)** — unimodal-dominant.
   - **Add to dev only** — bimodal-dominant + persistence 0.3–0.6.
   - **Drop** — no-structure dominant OR persistence < 0.3.
4. Update memory `project_market_coverage_2026_05.md` with shape verdicts per league + recommended `book_weights.by_league` seed values for M.6.

**Acceptance.**
- [ ] `scripts/analyse_dispersion.py` runs without writing to logs/CSV/DB/ntfy (read-only, like M.1).
- [ ] `docs/DISPERSION_SHAPES_2026-05.md` populated with all 11 leagues.
- [ ] `docs/LEAGUE_COVERAGE_2026-05.md` augmented with shape verdict per league.
- [ ] Dev key quota delta ≈ 22 cr from clean baseline.
- [ ] All tests pass.

**Reviewer focus.**
- Confirm the script does not write to `logs/bets.csv`, `logs/paper/*`, ntfy, BetRepo, SnapshotArchive. Read-only.
- Sanity check the EPL calibration constant — it should be derived from EPL's actual stdev distribution, not a magic number.
- Verify the "≥10 books" threshold filters out off-season fixtures with thin coverage (would otherwise generate spurious shape verdicts).
- Spot-check at least one fixture×outcome's classification by hand against the per-book printout.

**Verification commands.**
```bash
git diff --name-only main..HEAD   # expect: scripts/analyse_dispersion.py, docs/DISPERSION_SHAPES_2026-05.md, docs updates, tests
grep -E "csv|ntfy|BetRepo|SnapshotArchive|append" scripts/analyse_dispersion.py   # expect: no matches
pytest -q tests/test_dispersion_shape.py
export $(cat .env.dev) && python3 scripts/analyse_dispersion.py --sport soccer_spain_la_liga
```

---

## Phase M.6 — Configurable book weights via `config.json`

**Goal.** Make `J_sharp_weighted` data-driven, not code-driven. Per-book weights live in config, with optional overrides per market and per league. Enables M.4's structured experiment: run `J` (global weights) and `J2_sharp_weighted_per_league` (M.7-derived per-league weights) in parallel, compare CLV after 4–6 weekends, graduate the winner.

**Inputs.** M.7's per-book cluster-persistence data (informs the per-league weight defaults).

**Outputs.**
- `config.json` (and `config.dev.json` once M.4 lands) gain a `book_weights` block.
- `src/betting/strategies.py` — `J_sharp_weighted` reads weights from config via a new helper `book_weight(book_key, market, league)`.
- New variant `J2_sharp_weighted_per_league` — same code path, picks weights with per-league overrides applied.
- Tests covering resolution order (`by_league` → `by_market` → `default[book]` → `default["*"]`).

**Schema.**

```json
{
  "book_weights": {
    "default": {
      "pinnacle": 3.0,
      "betfair_ex_uk": 2.5,
      "smarkets": 2.0,
      "*": 1.0
    },
    "by_market": {
      "spreads": {
        "pinnacle": 4.0,
        "*": 1.0
      }
    },
    "by_league": {
      "soccer_spain_la_liga": {
        "pinnacle": 3.0,
        "betfair_ex_uk": 1.5,
        "*": 1.0
      }
    }
  }
}
```

**Resolution order** for `(book_key, market, league)`:
1. `book_weights.by_league[league][book_key]` if present.
2. else `book_weights.by_market[market][book_key]` if present.
3. else `book_weights.default[book_key]` if present.
4. else `book_weights.default["*"]`.
5. else `1.0` (treats absent config as flat-consensus equivalent for `J`; backwards-compatible).

**Tasks.**
1. Add the schema block to both `config.json` and `config.dev.json` (when M.4 lands; otherwise just `config.json`). Initial `default` block matches the current hardcoded weights inside `J_sharp_weighted` so behaviour doesn't change for `J` on day one.
2. Implement `src/betting/book_weights.py` (or extend `strategies.py`) with `book_weight(book, market, league) -> float` honouring the resolution order. Must tolerate absent config gracefully (fall back to `1.0`).
3. Refactor `J_sharp_weighted` to call `book_weight(book, market, league)` instead of its inline dict.
4. Add new variant `J2_sharp_weighted_per_league` — identical code path; the difference is purely that `config.dev.json`'s `by_league` block is populated from M.7 data (to be filled in by M.4 task).
5. Wire `J2` into `scripts/scan_odds.py` paper-portfolio dispatch.
6. Tests: `tests/test_book_weights.py` covers all four resolution branches + the absent-config fallback. Existing `test_strategies.py::test_J_sharp_weighted_*` still passes (backwards compat).

**Acceptance.**
- [ ] `J_sharp_weighted` produces the same paper-portfolio output before and after the refactor (golden-file test on a fixed fixture set).
- [ ] `J2_sharp_weighted_per_league` exists and produces output when `config.dev.json` has a `by_league` block.
- [ ] Absent `book_weights` block → all books weighted `1.0` → `J` ≡ flat consensus.
- [ ] Tests cover all four resolution branches.

**Reviewer focus.**
- Backwards compatibility: existing paper-portfolio CSVs must continue to work; row-by-row diff before vs after the refactor for `J`.
- Confirm the new variant's CSV header matches the existing variant CSV schema (`code_sha`, `strategy_config_hash`, etc.).
- Pi-safety: Pi reads `config.json` only — its `by_league` block stays empty/absent, so behaviour is unchanged on Pi.

**Verification commands.**
```bash
pytest -q tests/test_book_weights.py tests/test_strategies.py
# Golden-file diff for J before/after
git stash && python3 scripts/scan_odds.py --dry-run > /tmp/before.txt 2>&1
git stash pop && python3 scripts/scan_odds.py --dry-run > /tmp/after.txt 2>&1
diff /tmp/before.txt /tmp/after.txt | grep "J_sharp_weighted"   # expect: no behavioural diff
```

---

## Phase M.4 — Dev sandbox: AH probe + structured weighting experiments

**Goal.** Dev runs the prod league set plus (a) AH (`extra_markets=["spreads"]`) probe, (b) La Liga added to dev only as a structured sharp-weighted experiment, (c) `J2_sharp_weighted_per_league` populated from M.7's data. Generates the data needed to graduate variants.

**Inputs.** M.3 prod config; M.6 weight schema; M.7 dispersion shape verdicts.

**Outputs.**
- `config.dev.json` — extends `config.json` with:
  - `extra_markets: ["spreads"]`.
  - `leagues` array adds La Liga (and any other league flagged "Add to dev only" by M.7).
  - `book_weights.by_league` populated for La Liga + any league with cluster-persistence ≥ 0.6 from M.7.
- `.env.dev` updated to `LEAGUES_CONFIG=config.dev.json`.
- `src/betting/strategies.py` — new variant `Q_asian_handicap` (filters `market_type == "spreads"` rows).
- `scripts/scan_odds.py` — paper-portfolio writer wires Q variant alongside existing A–P + J2.

**Budget check.** Dev burn = `N_leagues × 2 cr × (1 + 1) markets × N_scans × 4.345`. With 11 leagues (10 from M.3 + La Liga) and `spreads` enabled = 4 cr/league:

| Scans/wk | Monthly burn |
|---|---|
| 5 (current) | 956/mo ✗ |
| 4 | 765/mo ✗ |
| **3** | **574/mo** ✗ marginal |
| 3 + drop 2 lowest-priority dev leagues to h2h-only | needs a per-league `extra_markets` override |

**Decision (override M.4 default).** Schema extension required: per-league `extra_markets` override in the `leagues` array, e.g. `{"key": "soccer_germany_bundesliga2", "label": "Bundesliga 2", "min_books": 20, "extra_markets": []}` to opt that league out of `spreads`. With `spreads` only on top-4 leagues (EPL + Bundes + Serie A + Ligue 1) and h2h-only on the rest, 3 scans/wk:

```
top-4: 4 × 4 cr × 3 × 4.345 = 209
rest:  7 × 2 cr × 3 × 4.345 = 183
total: 392/mo ✓ (within 500)
```

**Tasks.**
1. Extend `_load_config` (M.2) and the per-league entry schema to support optional `extra_markets` override at the league level. Default to global `extra_markets` if absent.
2. Create `config.dev.json` with:
   - `leagues` from `config.json` (M.3 set) + La Liga + any other M.7 "Add to dev only" leagues.
   - Top-4 leagues get `extra_markets: ["spreads"]`; the rest get `extra_markets: []` (or omit).
   - `book_weights.by_league` populated from M.7 cluster-persistence data.
3. Update `.env.dev` to set `LEAGUES_CONFIG=config.dev.json`.
4. Edit WSL crontab: 5 → 3 scans/wk (keep Tue 07:30 + Sat 16:30 + Sun 12:30; remove Fri 19:30 + Sat 10:30). Pi crontab **unchanged**.
5. Add `Q_asian_handicap` to `src/betting/strategies.py`. Filter: `market_type == "spreads"` and `edge_pct >= 0.03`. Use h2h's risk pipeline.
6. Wire `Q` into `scripts/scan_odds.py` paper-portfolio dispatch.
7. Tests: extend `tests/test_strategies.py` with a Q-variant fixture (synthetic spreads data). Extend `tests/test_config_loader.py` with per-league `extra_markets` override case.

**Acceptance.**
- [ ] Dev runs `markets=h2h,spreads` on top-4 leagues; `markets=h2h` on the rest; prod runs `markets=h2h` everywhere.
- [ ] Computed dev monthly burn ≤ 450 cr.
- [ ] WSL crontab shows 3 football scans/wk; Pi crontab unchanged (verify both).
- [ ] `logs/paper/Q_asian_handicap.csv` accumulates rows on next dev scan.
- [ ] `logs/paper/J2_sharp_weighted_per_league.csv` accumulates rows that diverge from `J_sharp_weighted` on La Liga (proving per-league weights are loaded).
- [ ] All tests pass.

**Reviewer focus.**
- Pi-safety: confirm `config.dev.json` is gitignored OR Pi has `LEAGUES_CONFIG` unset (falls through to `config.json`). Both must be true; don't assume.
- Confirm the FDCO backfill (`scripts/backfill_clv_from_fdco.py`) has no CLV path for `spreads` rows. Empty `clv_pct` for AH is expected; CLV for AH is a future R.10 sprint.
- No ntfy or DB-write side-effects from the Q or J2 variants on prod.
- Per-league `extra_markets` override has a clear precedence rule: league-level wins over global `extra_markets`. Document in the M.6/M.4 schema notes.

**Verification commands.**
```bash
# Dev path
LEAGUES_CONFIG=config.dev.json python3 -c "
import json,os
c=json.load(open(os.environ['LEAGUES_CONFIG']))
top4 = [l for l in c['leagues'] if l.get('extra_markets') == ['spreads']]
print('top-4 with spreads:', [l['key'] for l in top4])
print('book_weights.by_league:', list(c.get('book_weights',{}).get('by_league',{}).keys()))
"
# Crontab
crontab -l | grep -c "scan_odds"   # expect: 3 on WSL, 5 on Pi
ssh robert@192.168.0.28 'crontab -l | grep -c scan_odds'   # expect: 5
# Smoke test
export $(cat .env.dev) && python3 scripts/scan_odds.py
ls -la logs/paper/Q_asian_handicap.csv logs/paper/J2_sharp_weighted_per_league.csv
```

---

## Phase M.5 — Doc + memory updates; delete this plan

**Goal.** Reflect the new state in CLAUDE.md and memory; delete this one-time plan per the lean-docs principle. **Don't run M.5 until M.4's variants have ≥ 4 weekends of CLV data so we know whether `J`/`J2`/`Q` graduate.** If the answer is "graduate `J2`", the prod default flip is part of M.5; if the answer is "no edge", M.5 just records the negative result and reverts.

**Tasks.**
1. Update `CLAUDE.md`:
   - "Sports actively scanned" table → final prod set.
   - "API budget" paragraph → final prod number from M.3.
   - Add a paragraph under "Cron schedule" noting Pi runs 5 scans/wk + Pi config.json; WSL runs 3 scans/wk + config.dev.json with `spreads`.
   - "Variants in shadow" list updated to reflect M.6 additions (`J2`) and M.4 additions (`Q`); flip whichever variant graduated to "production".
2. Update memory:
   - `project_cron_schedule.md` — final prod & dev cadence and burn numbers.
   - `project_la_liga_excluded.md` — replace with the M.4 evaluation outcome (added to dev as structured experiment; record CLV outcome).
   - `project_market_coverage_2026_05.md` — record final outcome of the experiment cycle: which variant graduated, whether sharp-weighting beat flat consensus on La Liga, whether AH probe produced positive CLV.
   - `MEMORY.md` index — adjust the cron-schedule and market-coverage lines to point at the new numbers.
3. Delete `docs/PLAN_MARKET_COVERAGE_2026-05.md` (this file), `docs/LEAGUE_COVERAGE_2026-05.md` (M.1 output), and `docs/DISPERSION_SHAPES_2026-05.md` (M.7 output) — all transient, durable state lives in CLAUDE.md + memory.

**Acceptance.**
- [ ] `CLAUDE.md` reflects final state including any variant graduations.
- [ ] Memory files updated with experiment outcomes.
- [ ] All three plan/coverage/shape docs deleted from `docs/`.
- [ ] Final commit message: `M.5: finalise market-coverage rollout; delete plan`.

---

## Out of scope

- **Paid tier migration.** Defer until ≥50 settled bets with `clv_pct` populated demonstrate positive avg CLV. Tracked separately.
- **AH closing-line capture.** R.10 in the research plan; blocked on CLV evidence from this AH probe.
- **BTTS market.** Free tier rejects; revisit on paid tier.
- **`us` / `au` regions.** Books we can't actually bet at; not adding.
- **Increasing scan frequency on existing leagues.** Diminishing returns; not adding.
- **Per-league per-book sharpness rankings derived from historical CLV.** A genuinely data-driven `book_weights.by_league` (instead of M.7-cluster-derived) needs ≥ 50 settled bets per book per league with `clv_pct` populated. Future research sprint, not in this plan.
- **Bayesian / shrinkage estimators for cluster persistence.** M.7 uses a simple Spearman correlation; a hierarchical model would handle small-sample leagues better. Defer until M.7 produces evidence that the simple metric is too noisy.
- **Removing `K_draw_bias` xG dependency or other variant simplifications.** Out of scope; the 16 variants stay as-is unless graduation evidence forces a change.
