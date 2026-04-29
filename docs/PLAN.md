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
| 5 | New markets: totals, BTTS | ~3h | Low |
| 6 | Storage: SQLite + UUIDs | ~4h | Medium |
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
5. **Tennis bets get no CLV/drift.** `closing_line.py:281` skips because `LABEL_TO_KEY` lacks tennis tournament keys. Either build a dynamic tennis label→key map (mirror what `scan_odds.py` does), or document that tennis is excluded from CLV until further notice.
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

## Phase 5 — New markets: totals, BTTS (~1h remaining)

> Status correction: scanner and closing-line backend are already done. Only dashboard polish + a couple of bug fixes remain.

### 5.1 ✅ Done — `markets=h2h,totals,btts` in `scan_odds.py:247` and `closing_line.py:76`

### 5.2 ✅ Done — `find_value_bets` handles all three markets (`scan_odds.py:266–411`)

### 5.3 ✅ Done — `bets.csv` schema has `market`, `line`, `pinnacle_cons`, `pinnacle_close_prob`, `clv_pct`. Old rows missing these columns are tolerated by `app.py:load_bets` defaults.

### 5.4 Dashboard polish — TODO

- `templates/index.html`: add CSS classes for `.side-OVER`, `.side-UNDER`, `.side-YES`, `.side-NO` so non-h2h sides have colour.
- For non-h2h rows, render a small market tag in front of the side, e.g. `<span class="market-tag">O/U {{ b.line }}</span> OVER` for totals, `<span class="market-tag">BTTS</span> YES` for BTTS.
- Apply the change to all three sections (placed-pending, not-placed-pending, settled).
- Acceptance: a totals OVER 2.5 bet shows `O/U 2.5 OVER` in yellow; a BTTS YES bet shows `BTTS YES` in green.

### 5.5 Drift key bugfix — TODO (covered by Review #1 above)

- In `app.py`, change `load_drift()` and `summary_stats()` drift-key tuples from `(home, away, kickoff, side)` to `(home, away, kickoff, side, market, line)`.
- Same change in `index()` route at line 185 where `_drift_dir` is attached to settled bets.
- Acceptance: a fixture flagged at both totals OVER 2.5 and h2h HOME shows independent drift directions.

### 5.6 Document the model-gate scope — TODO

- Add a note to `CLAUDE.md` and `README.md`: the CatBoost model only produces signals for h2h on the four leagues with xG (EPL, Bundesliga, Serie A, Ligue 1). Totals/BTTS bets and Championship/Bundesliga 2/NBA/tennis bets always show `model_signal=?`, so the 2–3% model-filtered path **only ever fires on h2h in those four leagues**.

---

## Phase 5.5 — Paper portfolios (shadow A/B test) (~4h)

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
- [ ] `docs/PLAN.md` Phase 5.5 marked Done; `CLAUDE.md` implementation table updated.
- [ ] All review-findings bugs (Review #1–#10) are either fixed in this PR or filed as separate issues with code-comment TODOs.

---

## Phase 6 — Storage: SQLite + UUIDs (~4h)

### 6.1 Migrate `bets.csv` to SQLite

- New file: `logs/bets.db`. Tables: `bets`, `closing_lines`, `drift`, `bankroll_history`.
- Each bet has a `bet_uuid` PK, not a row index.
- Migrate existing CSV: read all rows, assign UUIDs, write to SQLite.

### 6.2 Refactor scanner and dashboard

- `scripts/scan_odds.py` writes via `INSERT OR IGNORE` keyed on a unique constraint `(kickoff, home, away, side, book, market)`.
- `app.py` queries SQLite. Routes use `bet_uuid` instead of int index.
- Add `csv_export` route for backwards compat — anyone who downloads a CSV still can.

### 6.3 Dashboard UUID-safe URLs

- `/update/<uuid>` instead of `/update/<int>`.
- Acceptance: scanner and dashboard concurrent run for an hour — no race, no duplicate, no lost edits.

### 6.4 Keep CSV export for analysis

- Nightly cron: dump SQLite to `logs/bets_export.csv` for ad-hoc analysis in Excel/pandas.

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
