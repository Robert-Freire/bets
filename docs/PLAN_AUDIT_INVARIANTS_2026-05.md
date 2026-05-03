# Plan — Numerical Audit Invariants (2026-05)

## Goal

`scripts/audit_invariants.py` — ~15 named assertions over real DB state,
ntfy push on any failure, Mon 09:10 BST after FDCO backfill + book_skill.

Catches cross-system drift and silent computation errors that unit tests miss.

## Execution model

**GitHub Actions scheduled workflow** (not Pi/WSL cron).

Rationale: the audit is inbound monitoring (reads Azure SQL, sends ntfy push) — no
outbound action that cloud blocks. A scheduled workflow is more reliable than WSL
(no laptop-sleep gaps), visible in the GitHub run history, and the SQL server's
`AllowAzureServices` firewall rule already covers GitHub's Azure-hosted runners.

The Pi cron handles scanner + CLV backfill + book_skill because those touch external
APIs or git-tracked files. The audit touches only the DB and ntfy — cloud-safe.

```
.github/workflows/audit_invariants.yml   Mon 08:10 UTC = 09:10 BST (summer)
scripts/audit_invariants.py              the 13 checks
```

## Check groups

**Group 1 — Within-row arithmetic (bets + paper_bets)**
- I-1: P&L reconciliation — won/lost/void maths vs stake + odds
- I-2: Edge in `[-0.20, 0.20]`
- I-3: `stake % 5 == 0`, `stake >= 5` for all non-null stakes

**Group 2 — Cross-source / dashboard parity**
- I-4: DB `SUM(pnl)` matches Python-level aggregate (NULL/format consistency)
- I-5: DB `SUM(actual_stake)` matches Python-level aggregate
- I-6: No bets with `result = 'pending'` and kickoff > 7 days ago

**Group 3 — CLV pipeline**
- I-7: ≥70% of football settled bets (kickoff > 14d) have `clv_pct` on FDCO-covered leagues
- I-8: `clv_pct` in `[-0.50, 0.50]` (outside = likely join mismatch)
- I-9: Week-over-week avg CLV shift < 10pp (WARNING only — doesn't page; all-SQL: compares 0–7d vs 7–14d settled bets, no file state)

**Group 4 — book_skill construction**
- I-10: `mean(ABS(edge_vs_consensus_loo))` > 0.0001 for latest window (LOO regression guard)
- I-11: `divergence == edge_vs_pinnacle - edge_vs_consensus_loo` within 1e-7
- I-12: Every `(book, league, market, window_end)` has exactly 2 rows (shin + multiplicative)
- I-13: No `n_fixtures <= 0` rows

**Group 5 — Idempotency**
- I-14: `compute_book_skill.py --dry-run` row count stable (compare DB row count week-over-week)

**Group 6 — Aggregate plausibility**
- I-15: Win-rate in `[0.25, 0.75]` for each strategy with ≥30 settled bets (WARNING only)

## GitHub Actions secrets required

| Secret | Notes |
|---|---|
| `AZURE_CLIENT_ID` | Already present (OIDC deploy workflow) |
| `AZURE_TENANT_ID` | Already present |
| `AZURE_SUBSCRIPTION_ID` | Already present |

No new secrets needed. The workflow logs in via OIDC then uses
`AZURE_SQL_KV_VAULT` / `AZURE_SQL_KV_SECRET` (baked into the workflow YAML as
non-secret env vars) so BetRepo fetches the SQL password from Key Vault at
runtime via `az keyvault secret show`. `kaunitz-github-actions` SP has
`get + list` on `kaunitz-dev-kv-rfk1` (added 2026-05-03).

## Status

- 2026-05-03: plan created.
- 2026-05-03: Groups 1–4 (I-1..I-13) shipped in `scripts/audit_invariants.py`. WSL cron wired Mon 09:10 BST. Groups 5–6 pending.
- 2026-05-03: Moved from WSL cron to GitHub Actions. `audit_invariants.yml` workflow created. WSL cron entry removed. No new secrets required — OIDC + KV reuses existing `AZURE_CLIENT_ID/TENANT_ID/SUBSCRIPTION_ID` creds.
