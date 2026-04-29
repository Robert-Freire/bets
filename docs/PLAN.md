# Improvement Implementation Plan

*Drafted: 2026-04-29 — derived from `docs/REVIEW.md`.*

This is a phased plan to roll the recommendations from the review into the system without breaking the daily scan in the meantime. Phases are ordered so each one delivers measurable value on its own; you can stop after any phase and still be better off than today.

---

## Guiding principles

- **Don't break the cron.** The scanner runs 6×/day. Every change ships behind a feature flag or in a way that the existing flow keeps working until verified.
- **Measure before optimising.** CLV logging (Phase 3) is the diagnostic that tells you whether subsequent changes actually help. Build it early.
- **One PR-equivalent per task.** Small, reversible commits. Tag them so you can roll back individual changes.
- **Backtest then live-shadow.** Each strategy change runs in shadow mode (logged but not flagged) for at least one weekend before becoming the default.
- **Snapshot `bets.csv` before each phase.** Never let a refactor lose historical data.

---

## Roadmap at a glance

| Phase | Theme | Estimated effort | Risk |
|---|---|---|---|
| 0 | Hygiene & safety net | ~3h | Low |
| 1 | Theory fix: de-vig + Pinnacle anchor | ~5h | Medium |
| 2 | Risk management | ~3h | Low |
| 3 | Diagnostics: CLV + drift | ~5h | Low |
| 4 | Filters: dispersion, outliers, dedup | ~4h | Low |
| 4.5 | Test scaffolding (pytest, devig/risk/keys) | ~2h | Low |
| 5 | New markets: totals, BTTS | ~3h | Low |
| 5.5 | Paper portfolios (8 strategy variants, shadow A/B) | ✅ Done | Low |
| 5.6 | Phase 5.5 bugfix sweep (P0/P1 from code review) | ✅ Done | Low |
| 5.7 | Commission-aware edges (per-book commission collection) | ✅ Done | Low |
| 5.8 | Post-5.7 review fixes (schema reset, per-row CLV, impl_raw, tennis throttle) | ✅ Done | Low |
| 6 | Storage: SQLite + UUIDs + `sport_key` (closes tennis CLV gap) | ~5h | Medium |
| 7 | Model overhaul: calibration, daily refresh, ensemble | ~8h | Medium |
| 8 | Auto-placement: Betfair API + dry-run | ~12h | High |
| 9 | Infrastructure: Pi/Azure migration | ~6h | Medium |
| 10 | Long-term: syndicate, multi-account | open | High |

Total active development: ~50 hours (one focused week, or 4–6 evenings + weekends).

---

## Review findings (2026-04-29) — read this first

Post-implementation review of phases 0–3 (live) and the partial Phase 5 work that's already in `scan_odds.py`. Bugs and risks for the implementer to fix while completing remaining work.

### Doc/code status discrepancies
- **CLAUDE.md says Phase 4 Done**, but no `dispersion`, `outlier`, `stdev`, `trimmed`, or `notified.json` symbol exists in the repo. Phase 4 is **not** implemented; treat the doc as wrong, not the code.
- **CLAUDE.md says Phase 5 Pending**, but `scan_odds.py:247` already requests `markets=h2h,totals,btts` and `find_value_bets` processes all three. Backend is done; only the dashboard, drift-key plumbing, and a few edge-cases are left.

### Bugs to fix while completing Phases 4/5

1. **`app.py:89, 150` — drift key drops `market` and `line`.** Currently `(home, away, kickoff, side)`. Must match the `closing_line.py` key `(home, away, kickoff, side, market, line)` or totals/BTTS bets on the same fixture will collide on lookup.
2. **`scan_odds.py:522` — `--max-tennis` CLI default is `99`** but `build_sport_list`'s default is `8`. CLI invocation overrides to 99, breaking the Phase 0.5 cap. Set CLI default to `8`.
3. **`scan_odds.py:580–583` — categorisation lost after risk pipeline.** `kaunitz_bets` and `model_bets` are re-derived using only `edge >= MIN_EDGE`. The original "model agrees" condition for the 2–3% bucket is dropped. Rebuild by tagging each bet with its source bucket *before* the risk pipeline runs, then partition by tag afterwards.
4. **Notification dedupe never landed.** Same bet flagged across 6 daily scans buzzes 6 times. Implement `logs/notified.json` per Phase 4.4 below.
5. **Tennis bets produce no CLV/drift.** `closing_line.py:281` skips tennis because `LABEL_TO_KEY` is a static dict keyed on the human-readable `title` field, which doesn't include the dynamically-discovered tennis tournaments. Root cause: `bets.csv` stores `sport: "EPL"` (the label) instead of `sport_key: "soccer_epl"` — so the closing-line script has to round-trip back from label to key. **Fix in Phase 6** (schema 6.1.1): store `sport_key` directly. Until then, tennis is excluded from CLV; document in `CLAUDE.md`.
6. **`scan_odds.py:367, 410` — totals and BTTS rows always set `model_signal: "?"`.** The 2–3% model-filtered notification path can therefore never include a totals/BTTS bet. This is correct (no model exists for those markets) but the user-facing docs should say so explicitly.
7. **`risk.py:20` — `get_bankroll()` reads only `BANKROLL` env var.** CLAUDE.md mentions `config.json`, which doesn't exist. Either add `config.json` support or update CLAUDE.md.
8. **No stale-price check at close.** `closing_line.py` records Pinnacle close but uses the *originally flagged* `bet.odds` for CLV. If the price has shortened between scan and close, CLV is inflated. Add: at T-1, also re-fetch the flagged book's current price and log it as `your_book_close_odds` so `clv_pct` can later be recomputed against actually-tradable prices.
9. **`compute_raw_stake` cap of 5% dominates** at typical edges (3–8%). Worth a comment in `risk.py` so future readers don't think Kelly is doing the work — the cap is.
10. **Potential silent miss in `_drift_direction`** (`app.py:97`) — `int(t_minus_min)` will `KeyError`/`ValueError` if a drift row is missing the field. Defensive default needed.

### Things that are solid (don't refactor for sport)
- `devig.py` Shin implementation has a correct bisection, sensible fallback, and a tol-based exit. Don't touch.
- `closing_line.py` market+line keys are consistent across reads/writes.
- Atomic writes via `os.replace` and `fcntl.flock` are correct.
- Dedup-on-append in `scan_odds.py:611–630` correctly includes market+line.

### External risk watch — UK Remote Betting Duty hike (2027-04-01)
HM Treasury is raising UK Remote Betting Duty from 15% to 25% on **1 April 2027**. Punter winnings remain tax-free (so no direct math change), but operators will almost certainly pass the duty through as wider over-rounds at UK-licensed books. Expect average CLV against Pinnacle (non-UK) to decay through Q2 2027 even if the model is unchanged — *attribute decay carefully before declaring the strategy broken.* If it bites, two mitigations: (a) down-weight UK books and tilt toward exchanges (Smarkets, Betfair) and EU books; (b) re-examine the per-book commission map in Phase 5.7 to see if effective margin assumptions need refresh. Watchlist item only — no code change required pre-2027.

---

## Phase 0 — Hygiene & safety net (~3h)

Stop the bleeding before refactoring anything.

### 0.1 Snapshot and back up `bets.csv`

- Add `cron` task: nightly `cp logs/bets.csv logs/bets.csv.bak.$(date +%F)`, keep last 14 days.
- Acceptance: a snapshot exists for last night.

### 0.2 Dedupe scan output

- In `scripts/scan_odds.py`, before appending to `bets.csv`, load existing rows and skip writes where `(kickoff, home, away, side, book)` already appears with the same `scanned_at` date.
- Acceptance: re-running the scanner twice in 5 minutes does not duplicate rows.

### 0.3 Atomic CSV writes

- In `app.py:save_bets`, write to `bets.csv.tmp` then `os.replace`.
- Add `fcntl.flock` around `load_bets()` and `save_bets()` so the dashboard and scanner can't collide.
- Acceptance: stress test with `app.py` open and 3 concurrent `scan_odds.py` runs — no row loss.

### 0.4 Quiet the no-bets notification

- Skip the "no value today" ntfy push if the previous scan in the last 6 hours also had no bets.
- Acceptance: phone gets at most one no-bets notification per day.

### 0.5 Stop scanning unmapped sports leagues silently

- Hard-cap `max_tennis` to 8 most-liquid tournaments. Document why in code comment.
- Acceptance: `--sports tennis` runs ≤16 API calls (8 × 2 regions).

---

## Phase 1 — Theory fix: de-vig + Pinnacle anchor (~5h)

The single biggest correctness fix. Everything downstream becomes more honest.

### 1.1 Implement Shin's method de-vigging

- New module: `src/betting/devig.py` with three functions:
  - `proportional(probs)` — divide each by sum.
  - `shin(probs)` — iterative solution (Shin 1991/1993). Reference: CRAN `implied` package.
  - `power(probs, k)` — `p_i^k / sum(p_j^k)`.
- Default to Shin for soccer 1X2; fall back to proportional if Shin doesn't converge.
- Unit tests: a no-vig market (sum=1) returns inputs unchanged; a 5%-overround market returns probs summing to 1.

### 1.2 Refactor `consensus.py` to de-vig before averaging

- Change `compute_consensus` so each book's H/D/A is de-vigged first, then averaged.
- Add `consensus_method` param: `"raw"` (legacy) or `"shin"` (new default).
- Old behaviour reachable for backtest comparison.

### 1.3 Refactor `scan_odds.py` to use the new consensus

- `find_value_bets` calls into the new module.
- The bookmaker's own `1/odds` is still de-vigged before computing edge: `edge = fair_consensus[side] - book_fair_prob[side]`.
- Acceptance: re-run the scanner; expect ~50% fewer flagged bets, all of them more honest.

### 1.4 Add Pinnacle as a weighted anchor

- Add `pinnacle` to the regions/bookmakers fetched. (No extra credit — `pinnacle` is in the EU region.)
- New consensus mode: `"pinnacle_anchor"` → use Pinnacle's de-vigged probability *as* the truth, weight other books at 0.
- New consensus mode: `"weighted"` → weights from a config dict, default `{pinnacle: 5, smarkets: 3, matchbook: 3, betfair_ex_uk: 3, others: 1}`.
- Run all three modes in parallel for one week, log all three "edges" per bet, but flag using only the chosen default.

### 1.5 Re-run the historical backtest

- In `main.py` and `src/betting/consensus.py:backtest_consensus`, repeat the ROI table at edges 1–5% under each de-vig method.
- Document results in a new `docs/BACKTEST.md`. Expect the published "+6.1% ROI at 2%" to drop substantially.
- Acceptance: backtest table for `raw` matches old numbers; `shin` and `pinnacle_anchor` produce new (lower, more realistic) numbers.

### 1.6 Update Kelly to use de-vigged probability

- `kelly_fraction(prob, odds)` already takes a probability — pass de-vigged consensus, not raw.
- Acceptance: Kelly stake on a Betfair Exchange "edge" with no real value collapses to 0.

---

## Phase 2 — Risk management (~3h)

### 2.1 Stake rounding

- After Kelly, round to the nearest £5 (configurable `STAKE_ROUNDING = 5`).
- Below £5, drop to £0 — the bet is too small to bother and looks suspicious.
- Acceptance: every `stake` value in `bets.csv` is a £5 multiple.

### 2.2 Per-fixture exposure cap

- Aggregate Kelly fractions per `(home, away)` fixture. Cap total at one Kelly-equivalent (5% of bankroll).
- If two sides on the same fixture both flag, scale them proportionally.
- Acceptance: simulate Bayer Leverkusen vs anything with HOME 4% edge and DRAW 5% edge → total ≤5% of bankroll.

### 2.3 Per-scan portfolio cap

- Sum all proposed stakes from a scan. If > 15% of bankroll, scale uniformly.
- Acceptance: weekend scan with 30 flagged bets stakes ≤£150 of £1000 bankroll, not £750.

### 2.4 Drawdown brake

- Maintain `bankroll_high_water` in `logs/bankroll.json`, updated from `bets.csv` settled P&L.
- If current bankroll < high-water × 0.85, halve all stake suggestions until back at high water.
- Acceptance: simulated 20% drawdown halves subsequent suggested stakes.

### 2.5 Configurable bankroll

- Move `BANKROLL = 1000` out of `scan_odds.py` into `config.json` or `.env`.
- Read from one source of truth across scanner and dashboard.

---

## Phase 3 — Diagnostics: CLV + drift (~5h)

This is the most important diagnostic phase — it tells you whether you have edge.

### 3.1 Closing-line snapshot job

- New script: `scripts/closing_line.py`. Runs every 5 minutes via cron, finds bets in `bets.csv` whose kickoff is in the next 5 minutes, fetches Pinnacle (and Betfair Exchange) odds for that fixture, writes to a new `closing_lines.csv`.
- Stop after kickoff; no need to keep polling.
- Acceptance: each settled bet has a row in `closing_lines.csv` within 5 minutes of kickoff.

### 3.2 CLV computation

- New column in `bets.csv`: `pinnacle_close_prob`, `clv_pct`.
- `clv_pct = (your_odds / pinnacle_close_devig_odds) - 1`.
- Compute on settle, not on flag.
- Acceptance: each settled bet shows a CLV percentage.

### 3.3 Dashboard CLV stats

- In `app.py:summary_stats`, add `avg_clv`, `clv_pos_rate`, `bets_w_clv`.
- In `templates/index.html`, surface average CLV in the stats bar — green if > 0, red if < 0.
- Acceptance: dashboard shows running CLV after Phase 3 has been live for a week.

### 3.4 Drift snapshots

- For every flagged bet, schedule three follow-up odds fetches: T-60min, T-15min, T-1min before kickoff.
- Log to `drift.csv`: `bet_id, t_minus_min, your_book_odds, pinnacle_odds, n_books`.
- Quota: each fetch is one API call per fixture, but you only refetch fixtures you've actually flagged. Should fit in budget.
- Acceptance: a flagged bet has 3 drift rows by kickoff.

### 3.5 Drift summary in dashboard

- For each settled bet, show whether the line drifted toward (good) or away from (bad) your bet.
- Aggregate: "Drift toward you on X% of bets". Should be >50% if you're sharp.

---

## Phase 4 — Filters: dispersion, outliers, dedup (~4h) — **NOT YET IMPLEMENTED**

> Status correction: CLAUDE.md says this is done, but no related code exists. All four sub-tasks below remain TODO.

### 4.1 Cross-book dispersion filter

- In `scan_odds.py:find_value_bets`, after `cons` is computed for a market, also compute `dispersion = stdev(book_fair_probs[side])` across all books for that side.
- Reject the bet if `dispersion > MAX_DISPERSION` (start at `0.04`; expose as constant near the top of the file).
- Add `dispersion` to the `bets.csv` schema for both flagged and (optionally) rejected analysis.
- Apply for **all three markets** (h2h, totals, btts), not just h2h.
- Acceptance: a contrived event where books split 50/50 between two distant prices does not flag.

### 4.2 Outlier-book check

- Before flagging book B at price `p`, compute `z = (book_fair[side] − mean(other_books_fair[side])) / stdev(other_books_fair[side])`.
- If `|z| > 2.5`, skip. The flagged book is itself the outlier — likely stale or wrong.
- Add `outlier_z` to the bet dict so it's logged for debugging.
- Acceptance: insert a synthetic event where a single book quotes `10.0` while 30 others quote `3.0` and confirm no flag.

### 4.3 Trimmed mean / median consensus *(optional — defer until Phase 5.5 paper data shows it matters)*

- Add `trimmed_mean(values, trim_pct=0.1)` helper and a `consensus_method="trimmed"` option in `devig.py`.
- Run as a paper variant in Phase 5.5; only promote to default if it beats Shin+mean by avg CLV.

### 4.4 Notification dedupe

- Maintain `logs/notified.json`: `{(kickoff, home, away, side, book, market, line): {"first_notified_at": iso, "last_odds": float}}`.
- Skip the ntfy push for any bet whose key has been notified in the last 12 h **unless** the odds have improved by ≥2%.
- Update the entry on every notification (even skipped ones, so the timer resets only when a real notification fires).
- Acceptance: same Arsenal–Fulham AWAY at the same odds notified at 02:23 does not re-notify at 02:26.

---

## Phase 4.5 — Test scaffolding (~2h, do before Phase 5.5)

**Why now**: 4 of the 10 bugs surfaced in this review (Review #1, #3, #10, plus the missing dispersion/outlier work in Phase 4) would have been caught by tests. Phase 5.5 introduces 8 strategy variants with overlapping logic — without tests, subtle differences between variants will only surface on Sunday night when results come in. Phases 6 (SQLite migration) and 7 (model overhaul) are high-risk refactors that need a regression net.

**Scope**: pure-function tests only. No Odds API mocking, no ntfy, no Flask. The cost of mocking external systems outweighs the bug-catching value at this stage.

### 4.5.1 Test infrastructure

- Add `pytest` to project deps (no `requirements.txt` exists yet — create one with `pytest` and the existing imports `flask`, `pandas`, `numpy`, `scipy`, `aiohttp`, `understat`, `catboost`).
- Create `tests/` directory with:
  - `tests/__init__.py` (empty)
  - `tests/conftest.py` — shared fixtures.
  - `tests/fixtures/sample_event.json` — one realistic Odds API event with ~30 bookmakers covering h2h+totals+btts. Capture this from a real API response and scrub the API key.
- Add `pytest.ini` at repo root: `testpaths = tests`, `pythonpath = .`.
- Acceptance: `pytest` from repo root runs and finds zero tests initially without errors.

### 4.5.2 `tests/test_devig.py` — pure math, ~6 tests

- `test_shin_no_overround_returns_unchanged`: input probs that already sum to 1.0 → identical output (within `1e-9`).
- `test_shin_5pct_overround_2way`: e.g. probs `[0.55, 0.50]` (sum 1.05) → fair output sums to 1.0 within `1e-6`.
- `test_shin_5pct_overround_3way`: 1X2 example → output sums to 1.0, ordering preserved (favourite stays the favourite).
- `test_shin_falls_back_to_proportional_on_pathological_input`: extreme overround case where bisection fails → returns proportional result, doesn't raise.
- `test_proportional_normalises_sum`: any positive-sum input → output sums to 1.0.
- `test_power_two_way_convergence`: 2-way market with known margin → power method converges within `max_iter`.

### 4.5.3 `tests/test_risk.py` — pipeline composition, ~5 tests

- `test_round_stake_below_half_rounding_drops`: stake `£2` with rounding `5` → returns `0.0`.
- `test_round_stake_to_nearest_5`: `£12.50` → `£10` or `£15` (specify which the implementation chose, lock it down).
- `test_fixture_cap_scales_within_fixture`: two bets on same `(home, away)` summing to 8% bankroll → scaled to ≤5%; bets on *different* fixtures unaffected.
- `test_portfolio_cap_scales_uniformly`: 30 bets summing to 30% bankroll → scaled to ≤15%; relative ratios preserved.
- `test_drawdown_multiplier_halves_when_15pct_below_high_water`: bankroll `£850`, high-water `£1000` → multiplier `0.5`; bankroll `£900` → `1.0`.
- `test_pipeline_order`: drawdown applied **before** caps, rounding **last**. (Property check via constructed inputs.)

### 4.5.4 `tests/test_strategies.py` — variant behaviour, ~10 tests (write alongside Phase 5.5)

Use `tests/fixtures/sample_event.json`. For each strategy variant A–H, assert:

- `test_variant_A_matches_production_h2h_count`: variant A on the sample event produces ≤1 bet difference vs. the legacy `find_value_bets` output. (Variant A is the regression check on the abstraction.)
- `test_variant_C_loose_finds_more_bets_than_A`: same fixture, lower edge threshold → equal-or-more bets.
- `test_variant_E_exchanges_only_no_williamhill`: no flagged bet has `book == "williamhill"`.
- `test_variant_D_pinnacle_only_uses_pinnacle_devig`: edge is computed against Pinnacle's de-vigged prob, not market mean. Verify by running with a synthetic event where Pinnacle has a deliberately weird price.
- `test_variant_F_model_primary_skips_totals_btts`: only h2h bets flagged.
- `test_variant_F_requires_positive_model_edge`: with `_MODEL_SIGNALS = {}`, variant F flags nothing.
- `test_variant_H_excludes_pinnacle_from_consensus`: with a synthetic event where dropping Pinnacle changes the mean, variant H's consensus differs from variant A's by the expected amount.
- `test_dispersion_filter_blocks_high_dispersion`: synthetic event where book probs split 50/50 → variant B (max_dispersion 0.04) flags 0 bets, variant A flags ≥1.
- `test_outlier_book_filter_blocks_outlier`: synthetic event with one rogue book at z>2.5 → no bet flagged AT that book.
- `test_strategy_count_is_8`: `len(STRATEGIES) == 8` and all `name` values are unique.

### 4.5.5 `tests/test_keys.py` — contract tests, ~3 tests

These guard against the Review #1 bug (drift-key dropping market/line).

- `test_drift_key_includes_market_and_line`: `app.py:load_drift` keys are 6-tuples ending in `(market, line)`. Implementer should expose a `_drift_key(row)` helper to make this testable.
- `test_closing_line_key_matches_drift_key`: same key shape used by `closing_line.py:load_existing_closing_keys` and `app.py:load_drift`.
- `test_bets_csv_dedup_key_matches`: `scan_odds.py`'s dedup key on append is the same shape.

### 4.5.6 `tests/test_smoke.py` — one integration check

- `test_app_starts_and_renders_index`: Flask test client GETs `/` with empty `bets.csv` → 200 + non-empty body. Catches template rendering errors after the Phase 5 dashboard polish.

### 4.5.7 CI hook (optional but recommended)

Add a GitHub Action (or local pre-commit) that runs `pytest` on push. If GitHub isn't being used, add `make test` and document running it before each PR.

### 4.5.8 Acceptance checklist

- [ ] `pytest` runs from repo root with zero warnings.
- [ ] All listed tests pass against current code (excluding strategy tests, which land with Phase 5.5).
- [ ] At least one test would have caught Review-finding #1 (drift-key collision).
- [ ] `requirements.txt` exists and lists `pytest`.
- [ ] `pytest.ini` configures testpaths and pythonpath.

---

## Phase 5 — New markets: totals, BTTS (~1h remaining)

> Status correction: scanner and closing-line backend are already done. Only dashboard polish + a couple of bug fixes remain.

### 5.1 ✅ Done — `markets=h2h,totals,btts` in `scan_odds.py:247` and `closing_line.py:76`

### 5.2 ✅ Done — `find_value_bets` handles all three markets (`scan_odds.py:266–411`)

### 5.3 ✅ Done — `bets.csv` schema has `market`, `line`, `pinnacle_cons`, `pinnacle_close_prob`, `clv_pct`. Old rows missing these columns are tolerated by `app.py:load_bets` defaults.

### 5.4 ✅ Done — Dashboard polish

- `.side-OVER`, `.side-UNDER`, `.side-YES`, `.side-NO` CSS added.
- `market_badge` macro renders `O/U 2.5` / `BTTS` tags in all three sections.

### 5.5 ✅ Done — Drift key bugfix

- `load_drift()`, `summary_stats()`, and `index()` all key on `(home, away, kickoff, side, market, line)`.

### 5.6 ✅ Done — Model-gate scope documented

- Note in `CLAUDE.md` CLV Scope Limitations section.

---

## Phase 5.5 — Paper portfolios (shadow A/B test) (~4h) ✅ Done

**Goal**: run 7 alternative strategy variants alongside production every scan, log their would-be bets, and let CLV after this weekend's matches tell us which configuration extracts the most edge. No real money — pure data.

**Why before Phase 6**: today is Wednesday; matches start Saturday. Building paper portfolios on flat files now gives one weekend of data. Phase 6 (SQLite) is plumbing that can wait until next week — it would eat Thursday and miss the deadline.

**Deliverables**:
1. `src/betting/strategies.py` — strategy config + a single `evaluate_strategy(events, sport_key, strategy)` entry point.
2. `scripts/scan_odds.py` — extended to run all paper strategies on the same events fetched for production (no extra API calls).
3. `logs/paper/<strategy_name>.csv` — one CSV per strategy, same schema as `bets.csv` plus a `strategy` column.
4. `scripts/closing_line.py` — extended to update CLV columns in every paper CSV that has matching keys.
5. `scripts/compare_strategies.py` — Markdown report writer.
6. CLAUDE.md + PLAN.md status updates.

### 5.5.1 `src/betting/strategies.py`

Define the dataclass and 8 variants. The implementer should follow this schema exactly so the comparison script can introspect:

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class StrategyConfig:
    name:                str          # "A_production", "B_strict", etc.
    label:               str          # human-readable
    description:         str
    devig:               str = "shin"           # "shin" | "proportional" | "power"
    consensus_mode:      str = "mean"           # "mean" | "weighted" | "pinnacle_only"
    pinnacle_weight:     float = 1.0            # used when consensus_mode == "weighted"
    exclude_pinnacle:    bool = False           # H variant — don't include Pinnacle in consensus
    book_filter:         str = "uk_licensed"    # "uk_licensed" | "exchanges_only" | "all"
    min_edge:            float = 0.03
    min_books:           int = 20
    max_dispersion:      float | None = None    # std-dev cap; None = off
    drop_outlier_book:   bool = False           # |z| > 2.5 outlier-flag-skip
    require_model_agree: bool = False           # F variant — model must agree
    model_min_edge:      float = 0.0
    markets:             tuple = ("h2h", "totals", "btts")
```

**The 8 variants** (define as a `STRATEGIES: list[StrategyConfig]` at module level):

| Code | name | Differences vs production |
|---|---|---|
| **A** | `A_production` | Mirrors current production exactly: `shin`, `mean`, all UK-licensed, 3% edge, no model gate, no dispersion, h2h+totals+btts |
| **B** | `B_strict` | `consensus_mode="weighted"`, `pinnacle_weight=5.0`, `min_edge=0.05`, `max_dispersion=0.04` |
| **C** | `C_loose` | `min_edge=0.02`, otherwise like A |
| **D** | `D_pinnacle_only` | `consensus_mode="pinnacle_only"`, `min_edge=0.03` (edge measured vs Pinnacle's de-vigged prob) |
| **E** | `E_exchanges_only` | `book_filter="exchanges_only"` (Betfair Ex, Smarkets, Matchbook), `min_edge=0.04` to compensate for commission |
| **F** | `F_model_primary` | `require_model_agree=True`, `model_min_edge=0.03`, `min_edge=0.0` (model-only — bets where model edge over book ≥3% regardless of consensus) |
| **G** | `G_proportional` | `devig="proportional"`, otherwise like A — tests whether Shin actually beats proportional in practice |
| **H** | `H_no_pinnacle` | `exclude_pinnacle=True`, otherwise like A — isolates Pinnacle's contribution |

`evaluate_strategy(events, sport_key, strategy) -> list[bet_dict]` should:
- Build per-book de-vigged fair probs using `strategy.devig`.
- For `consensus_mode="weighted"`, use `pinnacle_weight` for Pinnacle, 1.0 elsewhere; for `pinnacle_only`, ignore everything except Pinnacle's row.
- For `exchanges_only`, filter both consensus and flag candidates to `{betfair_ex_uk, smarkets, matchbook}`.
- For `exclude_pinnacle`, drop Pinnacle from consensus computation (but the bet target can still be a UK book).
- For `require_model_agree`, only flag h2h bets where `_model_signal(...)` returns a positive numeric value ≥ `model_min_edge`. Skip totals/btts entirely for variants with this flag.
- Apply `max_dispersion` and `drop_outlier_book` checks (write helpers in `strategies.py`, not `scan_odds.py`).

Acceptance: `python3 -c "from src.betting.strategies import STRATEGIES; print(len(STRATEGIES))"` prints 8.

### 5.5.2 Wire into `scan_odds.py`

After the production `find_value_bets` loop completes for each sport, call `evaluate_strategy(events, sport_key, strategy)` for every strategy in `STRATEGIES`. Append results to `logs/paper/<strategy.name>.csv` with the same dedup/atomic-write logic that `bets.csv` already uses. Schema = `bets.csv` schema + a `strategy` column.

Important: **do not re-fetch odds.** Reuse the `events` list returned by `fetch_odds`. No extra API quota cost.

Acceptance: after one scan, `ls logs/paper/` shows 8 CSVs; `bets.csv` is unchanged in row count vs. previous behaviour; `wc -l logs/paper/A_production.csv` is roughly equal to `wc -l` on the new rows in `bets.csv` for that scan (variant A should match production within a row or two).

### 5.5.3 Extend `closing_line.py`

Currently iterates `bets.csv`. Modify so the active-bet collection loop also iterates every `logs/paper/*.csv`, and `update_bets_csv_clv()` becomes `update_csv_clv(path, updates)` called once per CSV.

Pinnacle close prob lookup happens once per `(home, away, kickoff, side, market, line)` regardless of how many CSVs include that key — cache it in a dict during the loop.

Acceptance: after a closing-line run, every paper CSV has `pinnacle_close_prob` and `clv_pct` populated for any T-1-window matching rows.

### 5.5.4 `scripts/compare_strategies.py`

Reads `logs/paper/*.csv` and `logs/bets.csv`. Prints a Markdown table to stdout and writes `docs/STRATEGY_COMPARISON.md`.

Per strategy, compute (only over rows with non-empty `clv_pct`):
- `n_bets` — total flagged
- `n_with_clv` — settled-by-Pinnacle-close
- `avg_clv_pct`
- `pos_clv_pct` — % of bets where CLV > 0
- `avg_edge` — mean reported edge at flag time
- `book_dist` — top 3 books by share

Sort by `avg_clv_pct` descending. Highlight the row with the most positive CLV.

Acceptance: `python3 scripts/compare_strategies.py` prints a Markdown table and writes the doc; running before any matches close shows zeros across the board (expected).

### 5.5.5 Cron entry

Add to `crontab -e`:

```
# Paper portfolios run as part of scan_odds.py automatically — no extra cron needed.
# Comparison report (manual or weekly):
0 9 * * 1   cd /home/rfreire/projects/bets && python3 scripts/compare_strategies.py >> logs/strategy_compare.log
```

Closing-line cron is unchanged; it now updates paper CSVs in addition to `bets.csv`.

### 5.5.6 What NOT to touch

- **Don't refactor `scan_odds.py:find_value_bets`** to use the new strategies module. Keep production untouched until at least 2 weekends of paper data prove the abstraction works. Variant A is a parallel implementation that should produce nearly-identical output — divergence is itself a useful debug signal.
- **Don't gate production bets on paper results.** Paper is read-only: it logs, doesn't influence ntfy or `bets.csv`.
- **Don't add results / W-L tracking to paper CSVs in this phase.** CLV against Pinnacle close is the metric; ROI requires manual settlement and can be backfilled later.

### 5.5.7 Acceptance checklist for the implementer

- [ ] `src/betting/strategies.py` exists with 8 `StrategyConfig` entries.
- [ ] `python3 scripts/scan_odds.py --sports football` produces 8 new paper CSVs (or appends to existing) without errors.
- [ ] `bets.csv` row count for that scan is identical to pre-change behaviour.
- [ ] No extra API calls beyond what production already makes (verify by inspecting `X-Requests-Remaining` header before/after).
- [ ] `python3 scripts/closing_line.py` (manually triggered) writes `pinnacle_close_prob` to paper CSVs at T-1.
- [ ] `python3 scripts/compare_strategies.py` runs without error and produces a Markdown table.
- [ ] **`tests/test_strategies.py` from Phase 4.5 passes** — all 10 variant tests green. Phase 5.5 cannot be merged with failing tests.
- [ ] `docs/PLAN.md` Phase 5.5 marked Done; `CLAUDE.md` implementation table updated.
- [ ] All review-findings bugs (Review #1–#10) are either fixed in this PR or filed as separate issues with code-comment TODOs.

---

## Phase 5.6 — Phase 5.5 bugfix sweep (~3h, must finish by Fri 18:00 BST)

Code review of the Phase 5.5 implementation (2026-04-29) surfaced 15 issues. Group by priority — only P0 + P1 are blockers for the weekend.

### P0 — must fix before the weekend smoke test

**5.6.1 Add `tests/test_strategies.py` with the full 10-test suite** specified in Phase 4.5.4 (skipped during 5.5 implementation). All 10 are required — no minimum-subset escape hatch — since we have the time and the variants will only multiply in later phases.

The 10 required tests (verbatim from 4.5.4 — do not abridge):

1. `test_variant_A_matches_production_h2h_count` — variant A on the sample event produces ≤1 bet difference vs the legacy `find_value_bets` output. Variant A is the regression check on the abstraction itself.
2. `test_variant_C_loose_finds_more_bets_than_A` — same fixture, lower edge threshold → equal-or-more bets.
3. `test_variant_E_exchanges_only_no_williamhill` — no flagged bet has `book == "williamhill"` (or any non-exchange book).
4. `test_variant_D_pinnacle_only_uses_pinnacle_devig` — edge computed against Pinnacle's de-vigged prob, not market mean. Verify with a synthetic event where Pinnacle has a deliberately weird price.
5. `test_variant_F_model_primary_skips_totals_btts` — only h2h bets flagged.
6. `test_variant_F_requires_positive_model_edge` — with `_MODEL_SIGNALS = {}`, variant F flags nothing.
7. `test_variant_H_excludes_pinnacle_from_consensus` — synthetic event where dropping Pinnacle changes the mean; variant H's consensus differs from variant A's by the expected amount.
8. `test_dispersion_filter_blocks_high_dispersion` — synthetic event where book probs split 50/50 → variant B (`max_dispersion=0.04`) flags 0 bets, variant A flags ≥1.
9. `test_outlier_book_filter_blocks_outlier` — synthetic event with one rogue book at z>2.5 → no bet flagged AT that book (other books still flag-eligible).
10. `test_strategy_count_is_8` — `len(STRATEGIES) == 8` and all `name` values are unique.

- Acceptance: `pytest tests/test_strategies.py -v` shows **10 passed, 0 skipped, 0 xfailed**. CI must fail if any test is `@pytest.mark.skip`'d or removed.
- Implementation note for the bot: tests 4, 7, 8, 9 need *synthetic events* (not just the captured sample). Build a `synthetic_event(prices_per_book)` helper in `tests/conftest.py` so each test constructs the exact market state it needs.

**5.6.2 Run a smoke scan and verify all 8 paper CSVs populate.**

- `mkdir -p logs/paper && python3 scripts/scan_odds.py --sports football`
- Acceptance: `ls logs/paper/` shows ≥1 CSV per non-empty variant; total row count > 0; `python3 scripts/compare_strategies.py` runs without error.
- If any variant produces zero rows on a normal Friday-evening scan, debug the strategy config (likely `min_books`, `max_dispersion`, or `consensus_mode` filtering everything).

**5.6.3 Add `stake` column to paper CSVs** (`scripts/scan_odds.py:_PAPER_FIELDNAMES`).

- Compute via `risk.compute_raw_stake(vb["cons"], vb["odds"], BANKROLL)` — half-Kelly capped at 5%.
- **Do not apply the full risk pipeline** to paper bets (no fixture cap, no portfolio cap, no drawdown brake — paper is per-bet hypothetical, not a portfolio).
- Add `stake` between `model_signal` and `pinnacle_close_prob` in the field order.
- Acceptance: every paper CSV row has a non-empty `stake` value.

**5.6.4 Fix the post-risk-pipeline categorisation bug** (Review #3, `scan_odds.py:580–583`).

- Tag each bet with its source bucket (`"kaunitz"` or `"model"`) **before** `_apply_risk_pipeline`.
- After the pipeline, partition by tag, not by `edge >= MIN_EDGE`.
- Acceptance: a 2.5%-edge model-agree bet that survives the risk pipeline is sent in the `MODEL` notification, not silently dropped or mis-categorised.

### P1 — should fix this week

**5.6.5 Fix variant F's `min_edge` so it's truly model-primary.**

- Currently `min_edge=0.0` still gates on `edge >= 0` — bets where the model agrees but consensus disagrees are dropped.
- Change to `min_edge=-1.0` (effectively off) in `STRATEGIES`, and add a comment explaining why. Acceptance criterion: a synthetic event where consensus is mildly negative on a side but model edge is +5% gets flagged by F.

**5.6.6 Stop double-counting in `compare_strategies.py`.**

- Drop the `bets.csv` ("production") entry from the report; `paper/A_production.csv` is the proxy. Or merge them under one "production" label and dedupe by bet key.
- Acceptance: report no longer shows both rows for the same bets.

**5.6.7 Strengthen `tests/test_keys.py:test_closing_line_key_matches_drift_key`.**

- Replace the per-key length asserts with a real equality check: `assert lk == dk[:4] + dk[5:]` — the lookup key must be the drift key minus `t_label`.
- Use `tmp_path` fixture instead of writing `logs/_test_drift_tmp.csv`.

**5.6.8 Fix `compare_strategies.py:_stats:avg_edge` fallback.**

- Drop the `or r.get("consensus", 0)` fallback (line ~50). Edge and consensus aren't interchangeable. If `edge` is missing, skip the row.

**5.6.9 Add a comment to Strategy E about consensus dilution.**

- E uses `mean` consensus over all UK-licensed soft books. Document in the `description` that this dilutes the exchange-only signal; a future refinement is to anchor E on Pinnacle.

**5.6.10 Tennis "skip" log spam.**

- `closing_line.py` prints a "tennis excluded" warning per bet. Throttle to one warning per scan with the count of skipped tennis bets.

### P2 — backlog (do with Phase 6 next week)

- 5.6.11: Test for `_append_paper_csv` (would have caught the missing stake column).
- 5.6.12: Update `PLAN.md` roadmap line "Phase 5.5 ✅ Done" to also tick the sub-item checklist inside the phase body.
- 5.6.13: Update `CLAUDE.md` implementation table — Phase 4 should be "Pending" until dispersion/outlier filters land via Phase 5.5's strategies.py (now landed inside `_flag_bets`); Phase 5 + 5.5 should be marked Done.
- 5.6.14: Make the `1.2 <= odds <= 15.0` band a `StrategyConfig` field (`min_odds`, `max_odds`) so variant C_loose can capture short-favourite value at 1.10–1.20.
- 5.6.15: Apply notification dedupe (`logs/notified.json`) — Phase 4.4 still pending.

### Acceptance for Phase 5.6 as a whole

- [ ] `pytest` passes (incl. the new `test_strategies.py`).
- [ ] One scan run produces 8 paper CSVs with `stake` column populated.
- [ ] `compare_strategies.py` shows no double-counting.
- [ ] No P0 or P1 item open.
- [ ] CLAUDE.md and PLAN.md status tables match reality.

---

## Phase 5.7 — Commission-aware edges (~3h, ship with 5.6) ✅ Done

Replaces "Strategy E uses min_edge=0.04 to compensate for commission" with a global commission table, so **every strategy reports honest net edges** and Kelly stakes correctly account for what each book actually pays out.

### 5.7.1 New module `src/betting/commissions.py`

```python
"""
Commission rates per bookmaker on the Odds API.
Type: 'winnings' = % of net winnings (exchanges); 'none' = baked into odds (sportsbooks).
"""

# ─── Commission collection ──────────────────────────────────────────────────
# Source: each book's published commission policy, verified 2026-04-29.
# See docs/COMMISSIONS.md for citations.
BOOK_COMMISSIONS: dict[str, dict] = {
    # ── Exchanges (commission on net winnings) ─────────────────────────────
    "betfair_ex_uk":   {"type": "winnings", "rate": 0.05, "label": "Betfair Exchange (UK MBR)"},
    "smarkets":        {"type": "winnings", "rate": 0.02, "label": "Smarkets"},
    "matchbook":       {"type": "winnings", "rate": 0.02, "label": "Matchbook"},

    # ── Sportsbooks (no commission; margin built into odds) ────────────────
    "pinnacle":        {"type": "none",     "rate": 0.0,  "label": "Pinnacle (low-margin sportsbook)"},
    "betfair_sb_uk":   {"type": "none",     "rate": 0.0,  "label": "Betfair Sportsbook"},
    "betfred_uk":      {"type": "none",     "rate": 0.0,  "label": "Betfred"},
    "williamhill":     {"type": "none",     "rate": 0.0,  "label": "William Hill"},
    "coral":           {"type": "none",     "rate": 0.0,  "label": "Coral"},
    "ladbrokes_uk":    {"type": "none",     "rate": 0.0,  "label": "Ladbrokes"},
    "skybet":          {"type": "none",     "rate": 0.0,  "label": "Sky Bet"},
    "paddypower":      {"type": "none",     "rate": 0.0,  "label": "Paddy Power"},
    "boylesports":     {"type": "none",     "rate": 0.0,  "label": "BoyleSports"},
    "betvictor":       {"type": "none",     "rate": 0.0,  "label": "BetVictor"},
    "betway":          {"type": "none",     "rate": 0.0,  "label": "Betway"},
    "leovegas":        {"type": "none",     "rate": 0.0,  "label": "LeoVegas"},
    "casumo":          {"type": "none",     "rate": 0.0,  "label": "Casumo"},
    "virginbet":       {"type": "none",     "rate": 0.0,  "label": "Virgin Bet"},
    "livescorebet":    {"type": "none",     "rate": 0.0,  "label": "LiveScore Bet"},
    "sport888":        {"type": "none",     "rate": 0.0,  "label": "888Sport"},
    "grosvenor":       {"type": "none",     "rate": 0.0,  "label": "Grosvenor"},
}

# Default for any book not in the table (assume no commission)
DEFAULT_COMMISSION = {"type": "none", "rate": 0.0, "label": "unknown"}


def commission_rate(book: str) -> float:
    """Commission as a fraction of net winnings. 0.0 for sportsbooks."""
    entry = BOOK_COMMISSIONS.get(book, DEFAULT_COMMISSION)
    return entry["rate"] if entry["type"] == "winnings" else 0.0


def effective_odds(odds: float, book: str) -> float:
    """Decimal odds after commission deducted from net winnings."""
    c = commission_rate(book)
    if c == 0.0:
        return odds
    return 1.0 + (odds - 1.0) * (1.0 - c)


def effective_implied_prob(odds: float, book: str) -> float:
    """1 / effective_odds — the implied prob you actually pay for."""
    return 1.0 / effective_odds(odds, book)
```

### 5.7.2 Wire commission into edge & Kelly

**`src/betting/strategies.py:_flag_bets`** — replace the gross edge with net edge:

```python
from src.betting.commissions import effective_implied_prob, commission_rate

# ... inside the per-side loop:
fair_side = b["fair"].get(side, 1.0 / odds)
gross_edge = cons[side] - fair_side
# Commission shrinks effective odds → raises effective implied prob → reduces edge
net_edge = cons[side] - effective_implied_prob(odds, b["book"])
if net_edge < strategy.min_edge:
    continue
```

Store both `gross_edge` and `net_edge` (or rename to `edge` for net and keep `edge_gross` for the old number) on each bet dict so the comparison can still see what was lost to commission.

**`src/betting/risk.py:compute_raw_stake`** — Kelly uses effective odds:

```python
from src.betting.commissions import effective_odds

def compute_raw_stake(cons: float, odds: float, bankroll: float, book: str = "") -> float:
    eff = effective_odds(odds, book) if book else odds
    kelly = max(0.0, min(0.5 * (cons * eff - 1) / (eff - 1), 0.05))
    return kelly * bankroll
```

**`scripts/closing_line.py:clv_pct`** — CLV uses effective odds:

```python
close_odds = your_book_odds if your_book_odds else flagged_odds
eff = effective_odds(close_odds, book) if close_odds else 0
clv = round(eff * pin_prob - 1, 6) if eff else ""
```

### 5.7.3 Schema additions

Add to `bets.csv`, paper CSVs, and `closing_lines.csv`:

- `commission_rate` (float, e.g. 0.05 for Betfair Ex)
- `edge_gross` (float — old "edge" before commission)
- `edge` (now means net of commission)
- `effective_odds` (float)

### 5.7.4 Strategy E reformulation

Now that commissions apply globally, `E_exchanges_only` no longer needs `min_edge=0.04` to compensate. Change:

```python
StrategyConfig(
    name="E_exchanges_only",
    label="E: Exchanges only",
    description="Restrict to Betfair Ex / Smarkets / Matchbook; commission auto-applied via commissions.py",
    book_filter="exchanges_only",
    min_edge=0.03,  # same as production now that commission shrinks edge fairly
),
```

**Verify what this changes**: Smarkets at 4% gross becomes ~3.2% net (still passes 3% threshold). Betfair at 4% gross becomes ~3% net (borderline). Betfair at 3% gross becomes ~2.4% net (fails). So variant E will skip Betfair-only marginal bets — which is the intended outcome.

### 5.7.5 Documentation

Create `docs/COMMISSIONS.md` listing each book's commission rate with a citation URL. Pin the verification date so future drift is visible.

### 5.7.6 Tests `tests/test_commissions.py`

- `test_sportsbook_effective_odds_unchanged`: `williamhill` at 2.5 → 2.5.
- `test_betfair_5pct_winnings`: 2.5 → `1 + 1.5*0.95 = 2.425`.
- `test_smarkets_2pct`: 2.5 → `1 + 1.5*0.98 = 2.47`.
- `test_unknown_book_defaults_to_zero`: an unmapped key returns input odds unchanged.
- `test_kelly_uses_effective_odds`: same gross edge, smaller stake on Betfair than Smarkets.

### 5.7.7 Acceptance

- [ ] `BOOK_COMMISSIONS` covers every book in `UK_LICENSED_BOOKS` plus Pinnacle.
- [ ] `pytest tests/test_commissions.py` passes.
- [ ] After re-running a scan, paper CSVs show `commission_rate=0.05` for Betfair rows, `0.02` for Smarkets/Matchbook, `0` for sportsbooks.
- [ ] `edge` in CSV is now net edge; sanity-check that exchange bets show `edge_gross > edge` and sportsbook bets show `edge_gross == edge`.
- [ ] `compare_strategies.py` reflects net edges; the `Avg Edge` column should now be lower for variants that flag exchange bets heavily.

---

## Phase 5.8 — Post-5.7 review fixes (~2h) ✅ Done

Code review of 5.6 + 5.7 (2026-04-29 evening, all 31 tests pass) surfaced 7 follow-ups. Items 5.8.1 and 5.8.3 are P0 — must land before the weekend smoke scan.

### 5.8.1 — Reset `bets.csv` schema while preserving legacy bets in the dashboard (P0)

The current `logs/bets.csv` header is the pre-5.7 schema (17 columns). The scanner now writes 21 columns (`edge_gross`, `effective_odds`, `commission_rate`, `market`, `line`, `pinnacle_cons`). Appending without a header rewrite produces a malformed file: `csv.DictReader` keys against the old header and silently drops the new columns.

**Approach**: don't migrate-in-place. Archive the legacy file, start fresh, and teach the dashboard to read both.

Steps for the bot:

1. **Archive existing data** (one shell op):
   ```bash
   mv logs/bets.csv logs/bets_legacy.csv
   ```
   Bets the user has placed manually live in here — must be preserved for ROI tracking on the dashboard.

2. **Start a new `bets.csv` with the full new schema header** — happens automatically on the next scan since `scan_odds.py:905` writes the header when the file doesn't exist. No code change needed for that.

3. **Update `app.py:load_bets()` to read both files** and normalise legacy rows. Concrete:
   ```python
   BETS_CSV        = Path(__file__).parent / "logs" / "bets.csv"
   BETS_LEGACY_CSV = Path(__file__).parent / "logs" / "bets_legacy.csv"

   def load_bets() -> list[dict]:
       bets = []
       # Legacy first (older bets at the top after reverse(); user's settled bets stay visible)
       if BETS_LEGACY_CSV.exists():
           with open(BETS_LEGACY_CSV, newline="") as f:
               fcntl.flock(f, fcntl.LOCK_SH)
               for row in csv.DictReader(f):
                   _normalise_row(row, source="legacy")
                   bets.append(row)
       if BETS_CSV.exists():
           with open(BETS_CSV, newline="") as f:
               fcntl.flock(f, fcntl.LOCK_SH)
               for row in csv.DictReader(f):
                   _normalise_row(row, source="new")
                   bets.append(row)
       for i, row in enumerate(bets):
           row["id"] = i
       return bets

   def _normalise_row(row: dict, source: str) -> None:
       row["_source"] = source
       # Defaults for fields missing in legacy rows
       row.setdefault("market", "h2h")
       row.setdefault("line", "")
       row.setdefault("edge_gross", row.get("edge", ""))     # legacy edge was already gross
       row.setdefault("effective_odds", row.get("odds", ""))
       row.setdefault("commission_rate", "0")                # treat unknown legacy as no-commission
       row.setdefault("pinnacle_cons", "")
       row.setdefault("pinnacle_close_prob", "")
       row.setdefault("clv_pct", "")
       row.setdefault("actual_stake", "")
       row.setdefault("pnl", "")
       row.setdefault("model_signal", "?")
   ```

4. **`save_bets()` writes only to `BETS_CSV`** — never to legacy. Updates to legacy bets (manual stake/result edits in the dashboard) are stored back to the legacy file: in `update()` route, dispatch by `row.get("_source")` to the right CSV. Concrete: split into two save functions, `save_legacy(bets)` and `save_new(bets)`, each writing only the rows from its source file.

5. **Dashboard hint**: optionally render a small `(legacy)` badge next to bets where `b._source == "legacy"` so it's clear they're pre-5.7 data without commission/CLV columns.

**Acceptance**:
- `mv` archives the old CSV.
- `python3 scripts/scan_odds.py --sports football` produces a fresh `logs/bets.csv` with the new 21-column header.
- Dashboard at `localhost:5000` shows both legacy and new bets; legacy bets show empty CLV/dispersion columns gracefully (not "—" everywhere).
- Editing a legacy bet's result via the form persists to `bets_legacy.csv`, not `bets.csv`.
- New tests `tests/test_app_legacy.py`: legacy-only file → loads; legacy + new → loads both; updating a legacy row writes to `bets_legacy.csv`.

### 5.8.2 — Smoke scan validation (P0, do this last)

Per 5.6.2 acceptance: `logs/paper/` doesn't exist yet. After 5.8.1 + 5.8.3 land, run:

```bash
export $(cat .env)
mkdir -p logs/paper
python3 scripts/scan_odds.py --sports football
ls -la logs/paper/                        # expect 8 CSVs
head -1 logs/paper/A_production.csv       # confirm new 24-column header
python3 scripts/compare_strategies.py     # report runs, no double-count
pytest                                    # 31+ green
```

If any variant produces 0 rows on a Friday-evening scan, debug before Saturday — likely `min_books`, `max_dispersion`, or `consensus_mode` filtering everything out.

### 5.8.3 — Per-row CLV recomputation in `update_csv_clv` (P0)

Bug: `closing_line.py:230–260` writes the same `clv_pct` from `updates[key]` to every CSV row matching `(home, away, kickoff, side, market, line)`. Different paper variants may flag the same side at different books; different book → different commission → different effective odds → different CLV. Currently they all share the production bet's CLV — wrong.

**Fix in `closing_line.py:update_csv_clv`**:
```python
from src.betting.commissions import effective_odds

for row in rows:
    key = (row.get("home"), row.get("away"), row.get("kickoff"),
           row.get("side"), row.get("market", "h2h"), row.get("line", ""))
    if key in updates and not row.get("pinnacle_close_prob"):
        pin_prob = float(updates[key]["pinnacle_close_prob"])
        row_book = row.get("book", "")
        try:
            row_odds = float(row.get("odds") or 0)
        except ValueError:
            row_odds = 0.0
        eff = effective_odds(row_odds, row_book) if row_odds else 0.0
        row["pinnacle_close_prob"] = str(pin_prob)
        row["clv_pct"] = str(round(eff * pin_prob - 1, 6)) if eff else ""
        changed = True
```

`updates[key]` keeps `pinnacle_close_prob` (book-independent truth) but no longer carries a precomputed `clv_pct` — every consumer recomputes per-row.

**Acceptance**: write a test that populates two paper CSVs (`A_production.csv` flagging williamhill, `E_exchanges_only.csv` flagging betfair_ex_uk) for the same side and runs `update_csv_clv` with a single Pinnacle-close update; the two rows must have *different* `clv_pct` values, with E's smaller (commission shrinks effective odds).

### 5.8.4 — Model-signal baseline consistency (P1)

Production `scan_odds.py:_model_signal` compares model prob to **raw** `1/odds`. Paper `strategies.py:278` compares to **effective** implied prob. Same fixture/book/side gets a different `model_signal` value in production vs paper.

Decision: **model_signal should always be vs raw `1/odds`**. The model predicts a probability; commission is a payout adjustment, not a probability adjustment. The `edge` and Kelly stake are where commission belongs.

**Fix in `strategies.py:_flag_bets`** — separate the variable used for model-signal from the variable used for `edge`:
```python
fair_side = b["fair"].get(side, 1.0 / odds)
edge_gross = cons[side] - fair_side
edge = cons[side] - _effective_implied_prob(odds, b["book"])  # net of commission
raw_implied = round(1.0 / odds, 4)                            # for model_signal only
# ... in the model-signal branch:
ms_edge = sig.get(side, 0.0) - raw_implied
```

Drop the `ip = round(eff_implied, 4)` rebinding entirely. Store `impl_raw = round(1/odds, 4)` and `impl_effective = round(eff_implied, 4)` as separate fields on the bet dict.

### 5.8.5 — `impl` field rename (P1)

Currently the bet dict's `impl` field changed semantics in 5.7 from raw to effective. Anything reading `impl` downstream (compare_strategies, future analytics) silently sees a different number than before.

Rename: replace `impl` with two explicit fields, `impl_raw` and `impl_effective`. Update:
- `strategies.py:_flag_bets` (where the bet dict is built)
- `scan_odds.py:find_value_bets` (production path)
- `_PAPER_FIELDNAMES` and the bets.csv writer field list
- Any place in `app.py` or templates that reads `impl` (search-and-grep)

Tests: a regression test in `test_strategies.py` asserting `bet["impl_raw"] == round(1/bet["odds"], 4)` for every flagged bet.

### 5.8.6 — Tennis-skip log throttling (P2)

`closing_line.py:336` prints one "tennis excluded" line per tennis bet. Throttle:
```python
n_tennis_skipped = 0
for bet in all_active:
    sport_key = LABEL_TO_KEY.get(bet.get("sport", ""))
    if not sport_key:
        n_tennis_skipped += 1
        continue
    ...
if n_tennis_skipped:
    print(f"  [skip] {n_tennis_skipped} tennis bet(s) — no sport_key mapping (resolved in Phase 6)")
```

### 5.8.7 — `compare_strategies.py:_stats` `book_dist` scope (P2)

`book_dist` counts all bets; `avg_clv` counts only CLV-eligible. Either scope `book_dist` to CLV rows for consistency, or add a column header note: `Top books (all flagged)`. Either fix is fine.

### Acceptance checklist for 5.8 as a whole

- [ ] `logs/bets_legacy.csv` exists with the user's pre-reset bets; new `logs/bets.csv` has the new schema.
- [ ] Dashboard renders both legacy and new bets; result-edit form correctly routes to the right CSV.
- [ ] One smoke scan produces 8 paper CSVs, all 31+ tests pass.
- [ ] Per-row CLV test in `tests/test_clv.py` (new) shows different CLV per book on the same fixture.
- [ ] `model_signal` value is identical between production and variant A on the sample event.
- [ ] No occurrence of bare `impl` field in any CSV header or bet dict.

---

## Phase 6 — Storage: SQLite + UUIDs + sport_key column (~5h)

**Why this phase also closes the tennis CLV gap**: today `bets.csv` stores the human label (`sport: "EPL"`) and `closing_line.py` translates back to a sport key via a static dict (`LABEL_TO_KEY`) that excludes tennis tournaments because their titles are dynamic. By moving to a SQLite schema where `sport_key` is a first-class column populated at scan time (`soccer_epl`, `tennis_atp_madrid`, etc.), the closing-line script can iterate over distinct sport keys directly — and tennis bets get drift + CLV automatically.

**Note for the implementer**: this phase covers production `bets.csv` *and* the 8 paper CSVs from Phase 5.5. Do the migration once across all of them.

### 6.1 Migrate `bets.csv` to SQLite

- New file: `logs/bets.db`. Tables: `bets`, `closing_lines`, `drift`, `bankroll_history`.
- Each bet has a `bet_uuid` PK, not a row index.
- Migrate existing CSV: read all rows, assign UUIDs, write to SQLite.

### 6.1.1 Schema (the contract)

```sql
CREATE TABLE bets (
    bet_uuid             TEXT PRIMARY KEY,
    scanned_at           TEXT NOT NULL,        -- ISO UTC
    strategy             TEXT NOT NULL,        -- "production" or strategy name from Phase 5.5
    sport_key            TEXT NOT NULL,        -- "soccer_epl", "tennis_atp_madrid", "basketball_nba"
    sport_label          TEXT NOT NULL,        -- "EPL", "ATP Madrid Open" — preserved for display
    home                 TEXT NOT NULL,
    away                 TEXT NOT NULL,
    kickoff              TEXT NOT NULL,        -- ISO UTC
    market               TEXT NOT NULL,        -- "h2h" | "totals" | "btts"
    line                 REAL,                 -- NULL for h2h/btts; e.g. 2.5 for totals
    side                 TEXT NOT NULL,        -- "HOME" | "DRAW" | "AWAY" | "OVER" | "UNDER" | "YES" | "NO"
    book                 TEXT NOT NULL,
    odds                 REAL NOT NULL,
    edge                 REAL NOT NULL,
    consensus            REAL NOT NULL,
    pinnacle_cons        REAL,
    n_books              INTEGER NOT NULL,
    confidence           TEXT NOT NULL,
    model_signal         TEXT,
    dispersion           REAL,                 -- from Phase 4.1, NULL if not computed
    suggested_stake      REAL NOT NULL,
    actual_stake         REAL,                 -- NULL until placed
    result               TEXT,                 -- NULL | "W" | "L" | "V"
    pnl                  REAL,
    pinnacle_close_prob  REAL,
    your_book_close_odds REAL,                 -- new from Review #8
    clv_pct              REAL,
    UNIQUE (strategy, kickoff, home, away, side, book, market, line)
);

CREATE INDEX idx_bets_strategy ON bets (strategy);
CREATE INDEX idx_bets_sport_key ON bets (sport_key);
CREATE INDEX idx_bets_kickoff ON bets (kickoff);

CREATE TABLE drift (
    drift_uuid    TEXT PRIMARY KEY,
    bet_uuid      TEXT NOT NULL,               -- FK to bets
    captured_at   TEXT NOT NULL,
    t_minus_min   INTEGER NOT NULL,
    your_book_odds REAL,
    pinnacle_odds REAL,
    n_books       INTEGER,
    FOREIGN KEY (bet_uuid) REFERENCES bets (bet_uuid)
);

CREATE TABLE bankroll_history (
    snapshot_at  TEXT PRIMARY KEY,
    bankroll     REAL NOT NULL,
    high_water   REAL NOT NULL
);
```

### 6.1.2 Migration script `scripts/migrate_to_sqlite.py`

- Reads `logs/bets.csv` and every `logs/paper/*.csv`. Each row gets a freshly-generated `bet_uuid` (`uuid.uuid4().hex`).
- For production rows, set `strategy = "production"`. For paper rows, set `strategy = <filename stem>` (e.g. `"A_production"`, `"B_strict"`).
- Backfill `sport_key` by reverse-lookup from `sport_label` using `LABEL_TO_KEY` from `closing_line.py` extended to include the historical tennis labels found in the CSV. For unmappable rows, set `sport_key = sport_label.lower().replace(' ', '_')` and log a warning.
- Backfill `drift` table from `logs/drift.csv` by joining on `(home, away, kickoff, side, market, line)` to get `bet_uuid`.
- Run migration in a transaction; on any error, rollback and leave CSVs untouched.
- Acceptance: `SELECT COUNT(*) FROM bets WHERE strategy='production'` equals the row count of `bets.csv`. Same for each paper CSV.

### 6.2 Refactor scanner

- `scripts/scan_odds.py` and the strategies-loop append via `INSERT OR IGNORE` (the `UNIQUE` constraint above replaces the existing dedup logic).
- Both production and paper writes flow through one helper: `db.insert_bet(bet_dict, strategy)`.
- **Schema additions on the bet dict**: `sport_key` (always set from the API call's sport), `sport_label` (display name), `dispersion` (computed in Phase 4.1, `None` if not).

### 6.3 Refactor closing-line and dashboard

- `closing_line.py` queries `SELECT DISTINCT sport_key FROM bets WHERE pinnacle_close_prob IS NULL AND kickoff BETWEEN ? AND ?` — no more `LABEL_TO_KEY` map. **Tennis works automatically.**
- Closing-line writes `pinnacle_close_prob`, `your_book_close_odds`, `clv_pct` via `UPDATE` keyed on `bet_uuid` — replaces the per-CSV walk from Phase 5.5.3.
- `app.py`: `load_bets()` becomes `db.fetch_bets()`. Routes use `bet_uuid` (string) not int index. `/update/<bet_uuid>` replaces `/update/<int:bet_id>`.
- Dashboard adds a `strategy` filter dropdown (default: `production`).

### 6.4 Concurrency

- SQLite `journal_mode=WAL` set on first open so the dashboard and scanner can read+write concurrently without the existing fcntl locking.
- Drop the `fcntl.flock` calls — WAL handles it.
- Acceptance: scanner and dashboard concurrent run for an hour — no race, no duplicate, no lost edits.

### 6.5 CSV export for analysis

- Nightly cron: `python3 scripts/export_csv.py` dumps `bets`, `drift`, and per-strategy bet tables to `logs/exports/<date>/*.csv` for ad-hoc analysis. Keep last 30 days.
- The dashboard gets a "Download CSV" button that streams a snapshot.

### 6.6 Tennis CLV verification (the bonus deliverable)

- After migration + closing-line refactor, run an active tennis tournament through one full scan + closing-line cycle. Confirm Pinnacle close prob and CLV land in the `bets` table for tennis rows.
- Acceptance: `SELECT sport_key, COUNT(*), AVG(clv_pct) FROM bets WHERE sport_key LIKE 'tennis_%' AND clv_pct IS NOT NULL GROUP BY sport_key` returns rows.
- Update `CLAUDE.md` to remove the "tennis CLV gap" caveat.

### 6.7 Tests (extends Phase 4.5)

- `tests/test_db.py`: schema migration round-trip; `INSERT OR IGNORE` upsert behaviour; concurrent read+write under WAL.
- `tests/test_keys.py`: contract test now asserts SQLite UNIQUE constraint matches the in-memory dedup key shape.
- Acceptance: `pytest tests/test_db.py` passes.

### 6.8 Acceptance checklist

- [ ] `logs/bets.db` exists; `logs/bets.csv` and `logs/paper/*.csv` archived to `logs/archive/<date>/`.
- [ ] All historical bets reachable via SQL with correct `strategy` and `sport_key`.
- [ ] One full scan → closing-line → settle round-trip works end-to-end.
- [ ] Tennis bet from a live tournament has `clv_pct` populated within ~10 min of kickoff.
- [ ] `app.py` works against the DB; dashboard renders identically (visually) to the CSV-backed version.
- [ ] All Phase 4.5 tests still pass; new `tests/test_db.py` tests pass.
- [ ] CLAUDE.md updated: tennis caveat removed; storage section says SQLite.

---

## Phase 7 — Model overhaul (~8h)

### 7.1 Honest hold-out evaluation

- Pick the most recent full season as a strict hold-out. Train on everything before.
- Compute RPS on the hold-out, compare to bookmaker market RPS.
- If model RPS still > market RPS, the model has no edge and should not gate any bets.
- Acceptance: `docs/MODEL_EVAL.md` documents the result with confidence intervals.

### 7.2 Calibration

- Add isotonic / Platt calibration trained on a separate calibration fold.
- Re-evaluate hold-out RPS after calibration.

### 7.3 Daily refresh

- Move `model_signals.py` from weekly to daily cron, before the morning scan.
- Acceptance: model uses team form within last 24 hours.

### 7.4 Decide the model's role

Three honest options after 7.1–7.3:

1. Model has edge → use as primary signal, consensus as filter.
2. Model has no edge but uncorrelated errors → keep as filter for low-edge bets.
3. Model has no edge and correlated errors → remove from production.

Document the decision in `docs/MODEL_DECISION.md`.

### 7.5 Add Dixon-Coles as a third independent vote

- The Dixon-Coles model is built but unused. Generate its predictions alongside CatBoost.
- Use `vote_count = sum(model_agrees, dc_agrees, consensus_flags)` as a soft filter.
- Backtest before promoting to production.

### 7.6 Edge uncertainty shrinkage

- Estimate `σ_edge` from cross-book dispersion.
- Use `f* = max(0, μ_edge - σ²/μ) / (odds-1)` instead of pure Kelly.
- Naturally shrinks stakes on uncertain edges.

---

## Phase 8 — Auto-placement: Betfair API (~12h)

Already in `todo.md`. Sequence:

### 8.1 Betfair authentication module

- `src/exchange/betfair.py`. Cert-based login, session token caching.
- Store cert path + key in `.env`, never in git.

### 8.2 Market resolution

- For each flagged bet, find the Betfair market ID from team names + date.
- Cache `(home, away, kickoff) → market_id` in SQLite.
- Handle name mismatches with a manual override table.

### 8.3 Dry-run mode

- New flag in scanner: `--place=dry`. Logs what would be placed (price, size, market) to `dry_runs.csv`.
- Run for at least 2 weekends and verify everything looks correct.

### 8.4 Pre-flight checks before live

- Re-fetch the price; if it has shortened by >0.5%, skip.
- Check the order book has at least 3× your stake at the price.
- Check daily exposure cap not breached.
- Acceptance: dry runs show no surprises for two weekends straight.

### 8.5 Live mode with kill switch

- New flag: `--place=live`. Requires explicit confirmation via env var `I_AM_BETTING_REAL_MONEY=true`.
- Maximum daily stake hard-cap.
- Acceptance: first real bet placed for £5–10 only, settles correctly, P&L matches expectation.

### 8.6 Auto-log placed bets

- Successful bets log to SQLite with `placed_via=betfair_api`, including the actual matched price.

---

## Phase 9 — Infrastructure: Pi / Azure (~6h)

Already documented in `docs/PI_AZURE_SETUP.md`. Add:

### 9.1 Migrate scanner to Pi

- One-time: `scp` the project, install deps, set up cron on the Pi.
- Verify The Odds API isn't blocked from the Pi's IP.

### 9.2 Read-only Azure dashboard

- The dashboard is read-only on Azure; the Pi syncs `bets.db` to Azure Blob Storage every 5 minutes.
- Authenticate the dashboard with a single-user OAuth (GitHub login or magic link).

### 9.3 Tunnelling

- Use Tailscale or Cloudflare Tunnel between Pi and Azure. Don't open ports on the Pi.

### 9.4 Monitoring

- Pi sends a heartbeat to ntfy.sh every hour; alert if missed.
- Acceptance: power-cycle the Pi → notification within an hour.

---

## Phase 10 — Long-term (open-ended)

### 10.1 Multi-account / syndicate

- When account restrictions hit, splitting bankroll across 2–3 trusted accounts is the only way to scale.
- Build the wallet-management layer: per-account bankroll tracking, restriction state, geographic spread.

### 10.2 Live exchange depth feed

- For exchange bets, subscribe to the live ladder and place limit orders below the offered price; let them be filled.

### 10.3 Mug-bet scheduler

- Automatic weekly low-edge bet on Premier League favourites to balance betting patterns. Reduces restriction risk.

### 10.4 Live-feed model retraining

- Daily incremental updates instead of full retrain. Cheaper compute, more responsive.

### 10.5 Multi-sport expansion

- Cricket (IPL), MLB, NHL — once the pipeline is robust on football.

---

## Dependencies between phases

```
Phase 0  (Hygiene)
   ↓
Phase 1  (De-vig)  ────┐
   ↓                   │
Phase 2  (Risk)        │
   ↓                   ├── Phase 5 (New markets)
Phase 3  (CLV/drift)   │
   ↓                   │
Phase 4  (Filters) ────┘
   ↓
Phase 6  (SQLite)  ──── Phase 7 (Model)  ──── Phase 8 (Auto-place)
                                                       ↓
                                                Phase 9 (Pi/Azure)
                                                       ↓
                                                Phase 10 (Long-term)
```

- Phases 0–5 are linear and should be done in order.
- Phase 6 (SQLite) can start in parallel with Phase 5 if you have time.
- Phase 7 (Model) needs Phase 6 to land first (cleaner data layer).
- Phase 8 (Betfair) needs Phase 6 + Phase 3 (CLV proves edge first).
- Phase 9 (Pi/Azure) needs Phase 8 stable on local first.

---

## Success criteria per phase

| Phase | KPI | Target |
|---|---|---|
| 0 | Duplicate rows in `bets.csv` | 0 |
| 1 | Flagged bets with raw vs Shin discrepancy | All recomputed |
| 2 | Stakes outside [£0, £5×N] | 0 |
| 3 | Bets with logged CLV | ≥95% of settled |
| 4 | Notification fires per unique bet | 1 |
| 5 | Markets covered | h2h + totals + btts |
| 6 | Race condition incidents | 0 over 4 weeks |
| 7 | Model RPS vs market RPS on hold-out | Documented honestly |
| 8 | First successful live bet | £5–10 settles correctly |
| 9 | Pi uptime | >99% over a month |

---

## What to commit / PR boundaries

Suggested git tags so you can roll back individual phases:

```
v0.0  current state (baseline)
v0.1  phase 0 complete
v1.0  phase 1 complete (de-vig live)
v2.0  phase 2 complete
v3.0  phase 3 complete (CLV live)
…
```

Each phase = one feature branch, merged when its acceptance criteria are met.

---

## When to abandon a phase

If after Phase 3 (CLV logging) the running average CLV is consistently negative over ~50 bets, **stop the build-out** and reconsider:

- Maybe live odds aren't tradable in size (exchange depth issue).
- Maybe the market has fully closed the gap and there's no edge to extract.
- Phases 5–10 are wasted effort if there's no underlying signal.

CLV is the gate. Build the diagnostic, then let the data tell you whether to keep building.
