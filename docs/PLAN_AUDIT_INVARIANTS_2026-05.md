# Plan — Numerical Audit Invariants (2026-05)

## Goal

A single script (`scripts/audit_invariants.py`) that runs ~15 named assertions over real
DB + CSV state and emits a pass/fail report, posted to ntfy on any failure. Wired into
Mon 08:00 cron after the FDCO backfill and `compute_book_skill.py`.

This is not a unit-test suite. Unit tests catch formula bugs; these checks catch
cross-system drift, data-layer mismatches, and silent computation errors that only
show up when real data flows through the full pipeline.

## Invariants

### Group 1 — Within-row arithmetic (bets + paper_bets)

**I-1: P&L reconciliation**
For every settled bet (`result IN ('won','lost','void')`):
- won: `abs(pnl - (actual_stake or stake) * (odds - 1)) < 0.05`
- lost: `abs(pnl + (actual_stake or stake)) < 0.05`
- void: `abs(pnl) < 0.05`

Catches: rounding bugs in `src/betting/risk.py`, settle-handler mismatches.

**I-2: Edge in plausible range**
`edge` and `edge_gross` in `[-0.20, 0.20]` for all rows. Values outside this range
are not impossible but warrant a flag — most real edges are 2–8%.

**I-3: Stake rounding**
`stake % 5 == 0` for all non-null stakes (nearest-£5 contract in `risk.py`).
`stake >= 5` for all non-null stakes (bets < £5 are dropped before insertion).

### Group 2 — Cross-source / dashboard parity

**I-4: P&L sum parity**
`SUM(pnl) FROM bets WHERE result IN ('won','lost')` (DB) matches
dashboard "Total P&L" figure (query the same table; this is a self-consistency
check that the dashboard's aggregation query is correct, not a CSV vs DB diff).

**I-5: Total staked parity**
`SUM(COALESCE(actual_stake, stake)) FROM bets WHERE result != 'pending'`
matches dashboard "Total staked" tile. Tolerance: £0.01.

**I-6: Settled count sanity**
`COUNT(*) FROM bets WHERE result IN ('won','lost','void')` > 0 after the first
real weekend (skip this check if total count is 0 — still pre-first-result phase).
Separately: `COUNT(*) WHERE result = 'pending' AND kickoff_utc < NOW() - INTERVAL 7 DAYS` == 0
(bets more than 7 days past kickoff should be settled or marked void).

### Group 3 — CLV pipeline

**I-7: CLV coverage rate**
Of bets where `result IN ('won','lost')` AND `sport = 'football'` AND kickoff > 14 days ago:
the fraction with non-null `clv_pct` should be ≥ 0.70 for the 6 FDCO-covered leagues.
Failure indicates FDCO team-name normalisation breakage or a missing CSV download.

**I-8: CLV value bounds**
All non-null `clv_pct` in `[-0.50, 0.50]`. Outside this range → almost certainly a
join mismatch (wrong fixture matched, wrong market direction, odds inversion).

**I-9: CLV direction sanity**
Avg `clv_pct` over the trailing 4 weeks should not have moved by more than 10 pp
week-over-week (e.g. from +2% to +12%). A sudden jump is a join-logic regression,
not a real edge change. Log as WARNING, not ERROR — doesn't trigger ntfy but appears
in audit report.

### Group 4 — book_skill construction

**I-10: LOO non-zero**
Mean `ABS(edge_vs_consensus_loo)` across all `book_skill` rows for the latest
`window_end` is > 0.0001. If this is ≈ 0, the LOO fix has been reverted and the
self-contaminated formula is running again (see `compute_book_skill.py:119-127`).

**I-11: Divergence identity**
For every `book_skill` row where `edge_vs_pinnacle` and `edge_vs_consensus_loo`
are both non-null: `ABS(divergence - (edge_vs_pinnacle - edge_vs_consensus_loo)) < 1e-7`.
Catches any future refactor that redefines `divergence` inconsistently.

**I-12: Devig method pairs**
Every `(book, league, market, window_end)` tuple in `book_skill` appears exactly
twice — once with `devig_method='shin'` and once with `devig_method='multiplicative'`.
Orphaned single rows indicate a partial run or a write-path bug.

**I-13: n_fixtures positive**
No `book_skill` row with `n_fixtures <= 0`. A zero here is a divide-by-zero
sentinel that slipped past the write guard.

### Group 5 — Idempotency spot-check

**I-14: Compute idempotency**
Run `compute_book_skill.py --dry-run --leagues EPL`. Compare `len(rows)` and the
set of `(book, devig_method)` keys against the previous run's counts (persisted in
`logs/audit_state.json`). Row count drift of more than ±2 on the same window_end
triggers a warning.

### Group 6 — Aggregate plausibility

**I-15: Strategy win-rate bounds**
For each paper variant with ≥ 30 settled bets: win-rate in `[0.25, 0.75]`. Outside
this range the strategy result-attribution is likely broken (e.g. wrong side matched).
Logged as WARNING only — low volume can produce outliers legitimately.

## Output format

```
[audit] 2026-05-05 — 15 checks, 15 passed
  ✓ I-1  P&L reconciliation         (312 bets checked, 0 mismatches)
  ✓ I-2  Edge range                  (312 bets in [-0.20, 0.20])
  ...
  ✗ I-7  CLV coverage rate           (0.54 < 0.70 threshold — 3 leagues missing FDCO CSV)
```

On any ✗: ntfy push to `robert-epl-bets-m4x9k`, priority high, body = check name +
failure detail. Script exits non-zero so cron logs the failure.

State persisted in `logs/audit_state.json` for idempotency check (I-14) and
week-over-week drift checks (I-9, I-15).

## Files touched

```
scripts/audit_invariants.py     New script (~200 lines)
logs/audit_state.json           State file (gitignored)
logs/audit.log                  Cron output
```

No schema changes. No new tables.

## Cron wiring

```
# Mon 08:00 UTC block — after CLV backfill and book_skill compute:
0 9 * * 1   cd /home/rfreire/projects/bets && ... python3 scripts/backfill_clv_from_fdco.py >> logs/backfill_clv.log 2>&1
5 9 * * 1   cd /home/rfreire/projects/bets && ... python3 scripts/compute_book_skill.py >> logs/book_skill.log 2>&1
10 9 * * 1  cd /home/rfreire/projects/bets && ... python3 scripts/audit_invariants.py >> logs/audit.log 2>&1
```

Runs after both upstream scripts so it reads their freshest output.

## Implementation order

1. Skeleton + ntfy wiring + `logs/audit_state.json` persistence
2. Group 1 (P&L, edge, stake) — highest catch rate for bugs you've already seen
3. Group 2 (dashboard parity)
4. Group 3 (CLV coverage + bounds)
5. Group 4 (book_skill) — add after B.1+B.2+B.3 land
6. Group 5 (idempotency)
7. Group 6 (win-rate bounds)

Groups 1–4 ship in the first PR. Groups 5–6 bundled with the B.3 PR.

## Status log

- 2026-05-03: doc created. Not started.
