# Plan — Numerical Audit Invariants (2026-05)

## Goal

`scripts/audit_invariants.py` — ~15 named assertions over real DB + CSV state,
ntfy push on any failure, Mon 09:10 BST cron after FDCO backfill + book_skill.

Catches cross-system drift and silent computation errors that unit tests miss.

## Check groups

**Group 1 — Within-row arithmetic (bets + paper_bets)**
- I-1: P&L reconciliation — won/lost/void maths vs stake + odds
- I-2: Edge in `[-0.20, 0.20]`
- I-3: `stake % 5 == 0`, `stake >= 5` for all non-null stakes

**Group 2 — Cross-source / dashboard parity**
- I-4: DB `SUM(pnl)` matches dashboard P&L tile
- I-5: DB `SUM(actual_stake)` matches dashboard total staked
- I-6: No bets with `result = 'pending'` and kickoff > 7 days ago

**Group 3 — CLV pipeline**
- I-7: ≥70% of football settled bets (kickoff > 14d) have `clv_pct` on FDCO-covered leagues
- I-8: `clv_pct` in `[-0.50, 0.50]` (outside = likely join mismatch)
- I-9: Week-over-week avg CLV shift < 10pp (WARNING only — doesn't page)

**Group 4 — book_skill construction**
- I-10: `mean(ABS(edge_vs_consensus_loo))` > 0.0001 for latest window (LOO regression guard)
- I-11: `divergence == edge_vs_pinnacle - edge_vs_consensus_loo` within 1e-7
- I-12: Every `(book, league, market, window_end)` has exactly 2 rows (shin + multiplicative)
- I-13: No `n_fixtures <= 0` rows

**Group 5 — Idempotency**
- I-14: `compute_book_skill.py --dry-run` row count stable vs `logs/audit_state.json`

**Group 6 — Aggregate plausibility**
- I-15: Win-rate in `[0.25, 0.75]` for each strategy with ≥30 settled bets (WARNING only)

## Files

```
scripts/audit_invariants.py     new (~200 lines)
logs/audit_state.json           state for I-9, I-14, I-15 (gitignored)
logs/audit.log                  cron output
```

## Cron wiring

```
10 9 * * 1  cd /home/rfreire/projects/bets && ... python3 scripts/audit_invariants.py >> logs/audit.log 2>&1
```

Groups 1–4 ship first; groups 5–6 in a follow-up once book_skill rows are populated.

## Status

- 2026-05-03: plan created. Not started.
