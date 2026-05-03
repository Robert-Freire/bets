# Plan — Per-Book Skill & Bias Tracking (2026-05)

## Goal

Build an empirical per-(book, league, market) **bias + skill** dataset from archived raw API snapshots + settled outcomes. The dataset is the deliverable — it becomes a substrate for multiple downstream strategies, not a single change to consensus weighting.

Downstream uses (non-exhaustive, all paper-only until graduated):

- Bias-fade variants (e.g. fade home-biased books on home favourites).
- Sharp-weighted consensus (rewire `J_sharp_weighted` to use empirical weights).
- Filter: drop a book from consensus on a (league, market) where its skill is materially worse than peers.
- Diagnostic: detect drift when a previously-sharp book degrades.

## Sample-size constraint (read first)

Per-book skill ranking needs enough fixtures per (book, league, market) for Brier confidence intervals to separate. With ~10 h2h fixtures/league/week on the active 6 leagues, an 8-week window = ~80 fixtures per (book, league). For 36 books that is **below the threshold** where Brier-vs-outcome ranks reliably — variance dominates skill at this n.

Two consequences shape the plan:

1. **Bias signals converge much faster than skill signals.** Fav-longshot slope and home/draw tilt are per-bucket aggregates over all a book's lines, so they stabilise at hundreds of fixtures, not thousands. Bias is the higher-confidence output in v1.
2. **Brier-vs-Pinnacle-close is the primary skill metric**, not Brier-vs-outcome. The proxy has far higher signal at small n; outcome-Brier is a slow-converging validator stored alongside, not a ranking input until the archive is deeper.

If after backfill the per-book Brier CIs overlap heavily across the ranking, **skill-based variants do not ship** and downstream work leans on bias signals only. This is the explicit abort criterion for B.4a.

## Phases

### B.0 — Schema (½ day)

- New table `book_skill` keyed `(book, league, market, window_end)`:
  - **Sample size:** `n_fixtures`
  - **Skill (B.2 — sample-gated):** `brier_vs_close, brier_vs_outcome, log_loss`
  - **Bias (B.1):** `fav_longshot_slope, home_bias, draw_bias`
  - **Free-tier signals (B.0.5 — new):** `flag_rate, mean_flag_edge, edge_vs_consensus, edge_vs_pinnacle, divergence`
- Add to `src/storage/schema.sql` + sqlite mirror; migration via existing runner.
- All trend reading falls out of multiple `window_end` rows per (book, league, market). No separate timeseries store.
- **No CSV outputs.** Mid A.4/A.8 cutover — SQL is the source of truth; weekly post-mortem and dashboard read directly.

### B.0.5 — Free-tier consensus-divergence signals (½ day) — new, ship first

Available immediately for all scanned books on all scanned leagues. Zero API cost. Catches the "coordinated soft-pack drift" crack that consensus-anchored flag rate alone would miss.

- Read `bets` + `paper_bets` + scan-time blob archive over rolling 8w window.
- Per (book, league, market, window_end), write:
  - `flag_rate` — flags / fixtures observed.
  - `mean_flag_edge` — average edge % at scan time on flagged bets.
  - `edge_vs_consensus` — mean (book devigged prob − Shin consensus) at scan time, all observations (not just flags).
  - `edge_vs_pinnacle` — same but vs Pinnacle's scan-time devigged prob.
  - `divergence` — `edge_vs_pinnacle − edge_vs_consensus`. Reading: both shrink together → genuine retune; consensus-edge flat while Pinnacle-edge shrinks → soft-pack-coordinated drift; both flat → bias intact.
- Pinnacle is the truth anchor on EPL/Bundesliga/Serie A/Ligue 1 (Brier-validated, `docs/DISPERSION_SHAPES_2026-05.md`); on Championship + Bundesliga 2 use Bet365/Bwin sharp-only consensus instead.

### B.0.6 — Brier-vs-results trend for FDCO 6 (¼ day) — new, ship first

Same script, FDCO-covered books only (Pinnacle, Bet365, Bwin, William Hill, BetVictor, Betfair Exchange).

- After FDCO Mon-AM backfill lands, compute per-book Brier-vs-outcome on the 6 leagues FDCO covers.
- Writes `brier_vs_outcome` + `n_fixtures` rows. `brier_vs_close` left null until B.2.
- Trend reading: superseded by CUSUM in B.0.7 (replaces the original "alert when Brier drifts > 0.01 over 4 weeks" rule, which was a lagged rolling-mean approach).

### B.0.7 — Methodology hardening (1 day) — bundle into B.0/B.0.5/B.0.6 PR

Synthesised from two independent expert reviews of `docs/BOOK_EVALUATION_QUESTION.md` (2026-05-02). Lands in the same PR as B.0.5/B.0.6 so methodology is correct *before* B.1/B.2 produce ranking-relevant outputs (avoids a follow-up PR that rewrites the schema and tests of the first).

**Why these fixes:** Both reviewers independently flagged that `edge_vs_consensus` collapses to ~0 by construction (the book under test is included in its own consensus → mathematical suction; documented at `compute_book_skill.py:119-127`). With sample sizes ~80 fixtures per (book, league), raw Brier SE (~0.015–0.020) swamps the differences we care about (~0.003–0.010). Threshold-on-rolling-mean drift is a lagged signal; CUSUM is sharper.

**Schema changes to `book_skill`:**

- **Drop** `edge_vs_consensus` (always-zero by construction).
- **Add** `edge_vs_consensus_loo` — Leave-One-Out consensus; book under test excluded from the mean before differencing.
- **Recompute** `divergence` as `edge_vs_pinnacle − edge_vs_consensus_loo` (load-bearing for the soft-pack-drift detector in the three-signal table below).
- **Add** `brier_paired_vs_pinnacle` — Δᵢ = Brierᵢ(book) − Brierᵢ(Pinnacle close), per-fixture mean.
- **Promote** `log_loss` from B.2-gated to B.0.6 — fill it now alongside Brier, not deferred.
- **Add** `brier_ci_low`, `brier_ci_high`, `log_loss_ci_low`, `log_loss_ci_high` — fixture-level bootstrap (1000 resamples).
- **Add** `devig_method` — track which method generated the row (`'shin'` | `'multiplicative'`); rows duplicated per method.

**Code changes in `scripts/compute_book_skill.py`:**

1. **LOO consensus.** In `_BookAccum.add_event`, exclude the book under test from the consensus mean before computing `edge_vs_consensus_loo`. Keep `edge_vs_pinnacle` as-is — Pinnacle is the sharpest single anchor for skill ranking; LOO is the soft-pack-drift detector. They answer different questions and their *disagreement* is itself the load-bearing signal.
2. **Paired Brier.** Rewrite `_compute_fdco_brier` to compute per-fixture Δ vs Pinnacle close for every other FDCO book, then mean+CI the Δ stream. This is the single biggest leverage point — cuts SE 3–5× at n≈80 because the two Briers are highly correlated within a fixture.
3. **Log loss.** Same loop, add `−Σ log(p_outcome)` per fixture. Penalises confident-and-wrong harder than Brier; informative on tail behaviour.
4. **De-vig robustness.** Run B.0.6 Brier loop under both `shin` and `multiplicative` from `src/betting/devig.py`. Emit two rows per (book, league, market, window_end) keyed on `devig_method`. A book whose ranking is stable across both methods is real; method-specific signal is artefact.
5. **Bootstrap CIs.** 1000 fixture-level resamples per metric; persist CI low/high. Block-bootstrap by gameweek deferred until autocorrelation is observed.

**Drift detection (revises B.0.6):**

- Replace "alert when Brier drifts > 0.01 over 4 weeks" with **CUSUM** (or Page-Hinkley — choice is stylistic) on the paired-Brier stream per (book, league, market). Catches sharp-onset model swaps in 2–3 fixtures instead of 4 weeks.
- Run twice per book: once on headline skill, once on the H/D/A bias profile. A model swap that leaves overall sharpness unchanged but flips home-bias sign would otherwise go undetected.

**Bayesian shrinkage (revises B.1):**

- For bias signals on cells with n<200 fixtures, shrink toward the global mean using empirical-Bayes (β-prior). Keeps small-cell noise from manifesting as spurious bias rankings.
- Hierarchical Bayesian partial pooling deferred to B.4 unless bias rankings warrant it.

**Out of scope for B.0.7 (deferred to B.4+ or beyond):**

- Cross-book price-movement correlation ("follower-ness" via cross-correlation across the 5–6 scans per fixture).
- Pinnacle + Betfair Exchange dual-benchmark (LOO already provides robustness at v1).
- Block-bootstrap by gameweek (only if fixture-level autocorrelation is observed).
- Hierarchical Bayesian model with partial pooling (book × stratum).

### B.1 — Bias backfill (1 day) — primary deliverable

- `scripts/compute_book_skill.py`: reads Azure Blob `raw-api-snapshots` + FDCO results, computes per (book, league):
  - Fav-longshot slope: regress implied prob vs realised freq across prob buckets.
  - Home / draw bias: mean signed deviation from consensus on home / draw outcomes.
- Writes to `book_skill` (skill columns nullable for now).
- Idempotent on `window_end`. Zero API cost.

### B.2 — Skill backfill (½ day) — secondary, sample-size gated

- Same script, adds Brier-vs-close (primary) + Brier-vs-outcome (validator) over rolling 8w window.
- Output includes per-book Brier CI; surfaced in B.1's report so abort criterion is visible.

### B.3 — Weekly cron (¼ day)

- Add to Mon 08:00 cron after FDCO backfill. Single job covers B.0.5 + B.0.6 + B.1 + (B.2 once unblocked). No dashboard work yet.

## Three-signal complementarity

Each signal catches something the others miss. All write to the same `book_skill` table.

| Signal | Source | Books covered today | Catches |
| --- | --- | --- | --- |
| `flag_rate` / `mean_flag_edge` | scan-time pipeline | all scanned | Independent retune (consensus self-corrects) |
| `divergence` (`edge_vs_pinnacle − edge_vs_consensus_loo`, B.0.7) | scan-time pipeline | all scanned | Coordinated soft-pack drift (Pinnacle anchor moves vs LOO consensus stays put → soft pack drifted together) |
| `brier_paired_vs_pinnacle` + CUSUM | FDCO Pinnacle close + results | FDCO 6 today; all books after B.2 | Predictive-accuracy decay vs ground truth, sharp onset detected in 2–3 fixtures |

### B.4+ — Downstream consumers (separate sprints, gated)

Each ships as paper variant; graduation gate unchanged (≥30 CLV bets, ≥3 weekends, CI lower bound > 0).

- B.4a: sharp-weighted consensus. **Blocked on B.2 abort criterion passing.**
- B.4b: bias-fade variant. Unblocked by B.1 alone.
- B.4c: book-drop filter on poor-Brier (book, league) pairs. Blocked on B.2.

## Open questions

1. Window length — 8 weeks vs full-season? Lean 8w with 200-fixture floor for skill columns.
2. Per-market or pooled? h2h only in v1 (totals/BTTS too sparse).
3. ~~Consensus baseline for bias — Shin-devigged consensus or Pinnacle-close-devigged? Probably both.~~ **Resolved by B.0.7:** Pinnacle anchor (`edge_vs_pinnacle`) for skill, LOO Shin-devigged consensus (`edge_vs_consensus_loo`) for soft-pack-drift detection; both run under Shin and multiplicative de-vig.

## Implementation scope — B.1 + B.2 + B.3

### B.1 — Bias backfill

**Files:** `scripts/compute_book_skill.py` only.

Two new functions, both following the dual-devig pattern already in place:

**`_bias_from_rows(rows, since, until, devig_fn)`**  
Computes fav-longshot slope + home/draw bias from FDCO data (closing odds = ground truth).

- **Fav-longshot slope**: for each fixture, devig the book's odds → get p_home, p_draw, p_away.
  Bucket all outcome probabilities into 10 equal-width bins [0,0.1) … [0.9,1.0].
  Per bucket: `realised_freq = outcomes_that_happened / outcomes_in_bucket`.
  OLS slope of `realised_freq ~ bucket_midpoint` → stored as `fav_longshot_slope`.
  Slope ≈ 1.0 = calibrated; > 1 = favourite-biased; < 1 = longshot-biased.
  Requires ≥ 3 non-empty buckets; else NULL.

- **Home / draw bias**: per fixture, compute book's devigged p_home and p_draw, then
  subtract the LOO consensus (same logic as `_BookAccum.add_event` but per-outcome).
  Mean over all fixtures in window → `home_bias`, `draw_bias`.
  Positive = book overprices that outcome vs market.

- **Empirical-Bayes shrinkage** for n < 200: shrink home/draw bias toward 0 using
  Beta-prior with α = β = `n_fixtures * global_mean / (1 - global_mean)`.
  Fav-longshot slope: shrink slope toward 1.0 proportional to `n/(n+50)`.
  Global means computed over all books in the same (league, market, window_end).

**`_bias_from_blob(accum_by_method)`**  
Reuse the existing `_BookAccum` result but extract per-outcome means.  
Current `edge_vs_consensus_loo` list interleaves [home_diff, draw_diff, away_diff] per
fixture (3 values per fixture, appended in order). Extract:
- `home_bias` = mean of `loo_list[0::3]`
- `draw_bias` = mean of `loo_list[1::3]`

Shrinkage applied as above. Fav-longshot slope is not computable from blob data
(no outcomes in blob archive — that's FDCO's job).

**Integration in `compute()`**: populate `fav_longshot_slope`, `home_bias`, `draw_bias`
in the row-building loop. FDCO books get all three; blob-only books get home/draw bias
only (slope = NULL).

### B.2 — Skill CIs + abort decision

B.2 is not a new feature — it's a **decision gate**. After B.1 runs for 2–4 weeks and
the blob archive grows, run:

```bash
python3 scripts/compute_book_skill.py --dry-run | grep 'paired='
```

Inspect the `brier_paired_ci_low` / `brier_paired_ci_high` columns already populated
by B.0.7. If the CIs of Pinnacle vs the next-sharpest book (Bet365/Bwin) **do not
overlap**, skill ranking is real → B.4a (sharp-weighted consensus) proceeds.
If CIs overlap heavily across all books, **B.4a does not ship** and we lean on bias
signals only (B.4b, gated on B.1 alone).

`brier_vs_close` (the column, currently always NULL) is deferred until we have closing
odds for non-FDCO books — that requires the paid-data wishlist purchase. Leave it NULL;
`brier_paired_vs_pinnacle` is the primary ranking metric per B.0.7.

### B.3 — Cron wiring

Add two entries to the Mon 08:00 UTC block in crontab (both WSL and Pi):

```bash
# 09:05 BST — book skill compute (after FDCO backfill)
5 9 * * 1  cd /home/rfreire/projects/bets && set -a && . ./.env.dev && set +a && python3 scripts/compute_book_skill.py >> logs/book_skill.log 2>&1

# 09:10 BST — audit invariants (after book_skill)
10 9 * * 1 cd /home/rfreire/projects/bets && set -a && . ./.env.dev && set +a && python3 scripts/audit_invariants.py >> logs/audit.log 2>&1
```

Pi uses `.env` (prod key, `BETS_DB_WRITE=1`, `BLOB_ARCHIVE=1`).
WSL uses `.env.dev` (dev key, same env flags).

`logs/book_skill.log` and `logs/audit.log` are gitignored alongside other log files.

### PR structure

**PR 1 — B.1**: bias functions + shrinkage + integration into `compute()`.
Includes tests for `_bias_from_rows` with synthetic FDCO rows.

**PR 2 — B.3 + audit**: cron wiring (`crontab -l | ... | crontab -`) + 
`scripts/audit_invariants.py` groups 1–4 (arithmetic, parity, CLV, book_skill).
B.2 is not a PR — it's a manual decision after 2–4 weeks of data.

## Not in scope (v1)

- Dashboard tile — premature; defer until B.1 produces something worth looking at.
- CatBoost feature integration — model on HOLD; revisit when production model is unblocked.
- Auto-weighting in the production scanner — all consumers are paper variants until graduated.
- Multi-season backfill — start from whatever is in blob archive.
- Per-book commission interaction — already covered by `docs/COMMISSIONS.md`.
