# Paid-Data Wishlist — things unlocked by 1–2 months of Odds API historical

Living doc. Append new entries as they come up in conversation; do not delete executed ones, just mark status. Referenced from `CLAUDE.md` so future "we don't have enough data" discussions land here.

## Why this exists

Several investigations are blocked or sample-size-constrained because the free Odds API tier has no historical access and our blob archive only starts 2026-05. Paying for the historical endpoint (~$25–60 for 1–2 months of paid tier, 100k–500k credits) unlocks multi-season backfills in one shot.

What `/historical` returns: full live-odds payload frozen at a `date` param — every bookmaker's prices for every requested market at that timestamp. ~5–10min cadence, depth back to ~June 2020. Costs **10× live** per call.

## Budget shape (rough)

- 1 snapshot per fixture (close only): 6 leagues × 380 fixtures/season × 1 × 40 cr ≈ 90k cr → ~1 month paid tier ($25).
- 2 snapshots (open + close): ~180k cr → ~$40 (one tier up).
- 3 snapshots (open + T-60 + close) for drift work: ~270k cr → ~$60.

Always pull `uk,eu × h2h,spreads,totals` together — the cost is the same as pulling one market because billing is `regions × markets`, and a single fetch covers multiple wishlist items.

## Investigations unlocked

### 1. Per-book skill + bias rankings — **primary motivator**

Status: planned in `docs/PLAN_BOOK_SKILL_2026-05.md`, §B.2 explicitly gated on sample size.
Unlock: multi-season closing snapshots → thousands of fixtures per (book, league) → Brier-vs-close CIs that actually separate. B.4a (sharp-weighted consensus) and B.4c (book-drop filter) ship-or-don't-ship decision becomes possible immediately rather than after 8+ weeks of live archive.

### 2. Asian Handicap feasibility

Status: R.10 blocked on CLV evidence in production; AH probability conversion module not built. See `docs/AH_FEASIBILITY.md`.
Unlock: AH lines are in the historical payload (`spreads` market). Backfill lets us measure UK-book vs Pinnacle-close AH dispersion, see whether consensus-style edge exists on AH, and decide whether to build the conversion module before risking live credits on it.

### 3. Totals beyond 2.5 + BTTS sample

Status: production scanner currently h2h+totals-2.5 only; BTTS dropped (FDCO doesn't carry it; live sample was 0).
Unlock: historical totals payload includes all lines (1.5, 2.5, 3.5, AH-totals). Lets us check whether non-2.5 totals carry edge and whether BTTS dispersion is even worth re-enabling.

### 4. Opening-vs-closing line movement → sharp-book identification

Status: not attempted; `closing_line.py` paused 2026-05-01 to save credits.
Unlock: with open + close snapshots per fixture, identify which books move first ahead of the rest (steam) vs which lag. This is a more direct sharp-book signal than Brier-vs-close and doesn't need outcome data, so it converges fastest of any skill metric.

### 5. Multi-season walk-forward CV for paper variants

Status: 16 paper variants in shadow; graduation gate is ≥30 CLV bets + ≥3 weekends + CI lower bound > 0. R.5/R.5.5c/R.6 (walk-forward run + variant graduations) pending.
Unlock: walk-forward across 3–4 seasons per league instead of waiting for live CLV to accumulate. Lets the I/J/K/L/M/N/O/P variants either graduate or get culled in days rather than months.

### 6. La Liga re-evaluation

Status: excluded from active scanner — p95 dispersion 0.083 fails the 0.04 production filter (`memory/project_la_liga_excluded.md`). Probe ran on a single weekend.
Unlock: multi-season La Liga dispersion means + tails. If the 2026-05-01 probe was a tail event, La Liga adds a 7th league cheaply. If it's structural, we stop revisiting.

### 7. Drift / steam detection without burning live quota

Status: `logs/drift.csv` frozen since `closing_line.py` was paused.
Unlock: historical T-60/T-15/T-1 snapshots reconstruct drift retrospectively. Lets us validate whether drift was actually predictive of CLV before deciding to re-enable live polling on the paid tier.

### 8. Fav-longshot bias per league per book

Status: subset of item 1 but worth calling out separately — converges much faster than full skill ranking.
Unlock: with thousands of fixtures, per-book fav-longshot slope stabilises and a "fade longshot-biased books on longshots" variant becomes testable.

### 9. Live closing lines per book — replacing FDCO's Pinnacle-only feed

Status: today CLV is sourced from football-data.co.uk's free Mon-AM Pinnacle close, ~24–48h after kickoff, top-6 European leagues, h2h + totals 2.5 only, ~6 books published. See `docs/CLAUDE.md` CLV section.
Unlock: live T-5min capture against the Odds API gives the closing price for **every book we scan** (~36 vs FDCO's 6), every market we scan (not just h2h + totals 2.5), every sport we scan (not just football), with optional T-60/T-15/T-1 snapshots for drift.

**Bias monitoring is *not* a justification here.** That's covered free-tier by `PLAN_BOOK_SKILL_2026-05.md` B.0.5 (consensus-vs-Pinnacle scan-time divergence) + B.0.6 (Brier-vs-outcome trend on FDCO 6) + flag-rate trends. Coordinated soft-pack drift — the original gap — is caught by the divergence metric without paying.

What live per-book closing lines *specifically* enable that the free-tier signals can't:

- **B.2 ranking on FDCO-uncovered books.** Brier-vs-Pinnacle-close at scale needs Pinnacle's close *and* every other book's close at the same timestamp. Required for `PLAN_BOOK_SKILL_2026-05.md` §B.2 to ship beyond FDCO's 6 books — i.e., to cover Marathonbet, Smarkets, Matchbook, the soft UK long tail.
- **Coverage parity beyond FDCO scope.** The moment we re-enable totals ≠ 2.5, BTTS, tennis, NBA, or any non-top-6 league, FDCO returns NaN and live capture is the only path.
- **Drift / steam signal.** Multi-snapshot capture (T-60/T-15/T-1) tells us which books move first vs which lag — converges faster than Brier-vs-outcome and doesn't need outcome data. Cross-references item #4.
- **Scanner audit.** Verifies the prices the scanner logged were achievable, catching snapshot-vs-reality drift in our own pipeline. One-time use.

Cost shape: ~2 cr per fixture × ~60 fixtures/week × 4.345 ≈ **520 cr/month** for single T-5min snapshot per fixture. T-60 + T-1 doubles it. Lives on the paid tier; not viable on free.

Trigger conditions (any one):

- B.2 (per-book skill ranking) needs to ship on books FDCO doesn't cover.
- We expand scanner beyond h2h on top-6 leagues.
- Drift becomes a strategy variant.
- Real-time pause/resume of variants becomes a workflow.

Until then FDCO is sufficient for the graduation gate (≥30 CLV bets, ≥3 weekends, CI lower bound > 0).

## When to actually pull the trigger

Don't buy historical access speculatively. Buy it when:

- Two or more items above are simultaneously gated on it, **or**
- A specific decision (e.g., "do we ship B.4a?") is blocked and the answer changes how we spend the next month.

Pull all wishlist items in a single backfill pass — the marginal cost of additional markets in one fetch is zero.

## Status log

- 2026-05-02: doc created. No purchase yet.
