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

## Phase 4 — Filters: dispersion, outliers, dedup (~4h)

### 4.1 Cross-book dispersion filter

- Compute `std(1/odds across books)` for the flagged side.
- Reject bets where dispersion > threshold (start at 0.04, tune empirically).
- Add column `dispersion` to `bets.csv` so you can analyse rejected vs accepted.

### 4.2 Outlier-book check

- If the flagged book's `1/odds` is itself >2.5σ from the rest, suspect stale/bad data. Skip.
- Acceptance: a single book quoting 10.0 when others quote 3.0 doesn't trigger a "huge edge".

### 4.3 Trimmed mean / median consensus

- Add option in `devig.py`: drop top/bottom 10% of book probs before averaging.
- Compare to current performance in shadow mode for a week before switching default.

### 4.4 Notification dedupe

- Maintain `logs/notified.json` keyed on `(kickoff, home, away, side, book)`.
- Don't re-notify a bet already pushed in last 12h unless its odds have improved by >2%.
- Acceptance: same Arsenal-Fulham bet flagged at 02:23 and 02:26 only buzzes the phone once.

---

## Phase 5 — New markets: totals, BTTS (~3h)

### 5.1 Add markets to API call

- Change `fetch_odds` to request `markets=h2h,totals,btts`.
- Verify The Odds API quota cost is per-call, not per-market (it is).

### 5.2 Generalise `find_value_bets`

- Refactor to handle any market with a known set of outcomes.
- For totals (Over/Under 2.5), outcomes are `OVER` and `UNDER`. For BTTS, `YES` and `NO`.
- Apply de-vig per market separately.

### 5.3 Schema additions to `bets.csv`

- New columns: `market` (h2h, totals, btts), `line` (e.g. 2.5 for totals), `selection` (e.g. OVER).
- Backfill `market = "h2h"` for all existing rows.

### 5.4 Dashboard updates

- Group by market on the dashboard.
- Acceptance: BTTS YES on EPL Fulham–Crystal Palace shows up correctly.

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
