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
- Trend reading: alert when a book's Brier on (league, market) drifts > 0.01 over 4 weeks. Threshold to be calibrated after first 4 windows land.

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
| `divergence` (consensus vs Pinnacle scan-time) | scan-time pipeline | all scanned | Coordinated soft-pack drift |
| `brier_vs_outcome` trend | FDCO Pinnacle close + results | FDCO 6 today; all books after B.2 | Predictive-accuracy decay vs ground truth |

### B.4+ — Downstream consumers (separate sprints, gated)

Each ships as paper variant; graduation gate unchanged (≥30 CLV bets, ≥3 weekends, CI lower bound > 0).

- B.4a: sharp-weighted consensus. **Blocked on B.2 abort criterion passing.**
- B.4b: bias-fade variant. Unblocked by B.1 alone.
- B.4c: book-drop filter on poor-Brier (book, league) pairs. Blocked on B.2.

## Open questions

1. Window length — 8 weeks vs full-season? Lean 8w with 200-fixture floor for skill columns.
2. Per-market or pooled? h2h only in v1 (totals/BTTS too sparse).
3. Consensus baseline for bias — Shin-devigged consensus or Pinnacle-close-devigged? Probably both.

## Not in scope (v1)

- Dashboard tile — premature; defer until B.1 produces something worth looking at.
- CatBoost feature integration — model on HOLD; revisit when production model is unblocked.
- Auto-weighting in the production scanner — all consumers are paper variants until graduated.
- Multi-season backfill — start from whatever is in blob archive.
- Per-book commission interaction — already covered by `docs/COMMISSIONS.md`.
