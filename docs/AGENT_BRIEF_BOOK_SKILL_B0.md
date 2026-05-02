# Agent brief — implement B.0, B.0.5, B.0.6 of PLAN_BOOK_SKILL_2026-05

## Context (read first, in order)

1. `CLAUDE.md` — project overview, env split, A.4 dual-write architecture, Pi-safety contract.
2. `docs/PLAN_BOOK_SKILL_2026-05.md` — your spec. The whole doc.
3. `~/.claude/projects/-home-rfreire-projects-bets/memory/MEMORY.md` and the linked memories — especially `project_book_reliability_thread.md`, `project_azure_phase_plan.md`, `project_dev_prod_split.md`.
4. Source: `src/storage/schema.sql`, `src/storage/schema_sqlite.sql`, `src/storage/migrate.py`, `src/storage/repo.py`, `src/storage/snapshots.py`, `src/storage/_keys.py`.
5. Existing scan-time output paths so you know where to read flags from: `logs/bets.csv`, `logs/paper/*.csv`, Azure Blob `raw-api-snapshots` container.

Don't write code until you've read all of the above and can answer: how does `BetRepo` lazy-import pyodbc and gate on `BETS_DB_WRITE=1`? What's the deterministic-UUID scheme in `_keys.py`? How does the migration runner achieve idempotency?

## Scope

Implement these three phases only. Stop at the end of B.0.6. Do not start B.1, B.2, B.3, B.4, or any dashboard work.

### B.0 — Schema

Add a `book_skill` table to `src/storage/schema.sql` and `src/storage/schema_sqlite.sql`, keyed `(book, league, market, window_end)`, with these columns:

- `n_fixtures` (int)
- Skill (nullable for now): `brier_vs_close`, `brier_vs_outcome`, `log_loss`
- Bias (nullable for now): `fav_longshot_slope`, `home_bias`, `draw_bias`
- Free-tier signals: `flag_rate`, `mean_flag_edge`, `edge_vs_consensus`, `edge_vs_pinnacle`, `divergence`

Pick column types matching adjacent tables in the same schema files. Migration must be idempotent (re-runnable on a DB that already has the table).

Extend `BetRepo` in `src/storage/repo.py` with a `write_book_skill(rows)` method, gated on `BETS_DB_WRITE=1` exactly like existing writes, lazy-importing pyodbc the same way.

### B.0.5 — Consensus-divergence signals

New `scripts/compute_book_skill.py`. Per (book, league, market) over a rolling 8-week window ending at the most recent Sunday, compute:

- `flag_rate` = flags-per-fixture-observed.
- `mean_flag_edge` = average edge % at scan time on flagged bets.
- `edge_vs_consensus` = mean (book devigged prob − Shin consensus) at scan time, across **all observations** in the blob archive, not just flags.
- `edge_vs_pinnacle` = same but vs Pinnacle's scan-time devigged prob.
- `divergence` = `edge_vs_pinnacle − edge_vs_consensus`.

For Championship + Bundesliga 2 (where Pinnacle is sometimes absent or unreliable), substitute a sharp-only consensus across {Bet365, Bwin} as the truth anchor and document that explicitly in the row (add a `truth_anchor` column if needed, or note it in a comment in the script).

Inputs: scan-time blobs in Azure Blob `raw-api-snapshots` (use `SnapshotArchive` reading helpers; if none exist, write minimal helpers in the same module). Reuse Shin de-vig from `src/betting/devig.py` — do not re-derive.

Idempotent on `window_end`. Zero API cost — no fresh fetches.

### B.0.6 — Brier-vs-results trend for FDCO 6

Same script. After FDCO Mon-AM backfill has landed (you can assume it's run; respect the existing FDCO ingest paths in `scripts/backfill_clv_from_fdco.py` for shape), compute Brier-vs-outcome per book on the 6 FDCO-covered books (Pinnacle, Bet365, Bwin, William Hill, BetVictor, Betfair Exchange) for the 6 FDCO-covered leagues. Write to `book_skill.brier_vs_outcome`. Leave `brier_vs_close` null until B.2.

Idempotent on `window_end`. Zero API cost.

## Cross-cutting constraints

- **SQL only, no CSV outputs.** We're mid A.8 cutover. Don't add `logs/book_*.csv`. The weekly post-mortem and future dashboard read from SQL.
- **Pi-safety contract.** Without `BETS_DB_WRITE=1`, the new repo method must stay dormant and never import pyodbc. Without `BLOB_ARCHIVE=1`, the new script must skip cleanly — no crash. After `git pull` on Pi, behavior must be byte-identical to pre-PR.
- **Lazy imports.** Top-level imports in any new module must be Pi-safe. Only `pyodbc` / `azure-storage-blob` go inside the env-gated code paths.
- **Determinism.** Use the existing `_keys.py` UUID5 scheme for any FK references. Don't introduce a new key namespace.

## Tests

Extend the existing pytest suite (`tests/`). Mandatory:

- Schema migration runs cleanly against the SQLite mirror, twice, with no error on the second run (idempotency).
- Unit test on the divergence calculation against a synthetic 2-book fixture set with hand-computed expected values.
- Smoke test that `compute_book_skill.py` runs against a fixture-set fake of the blob archive (no network) and emits rows matching the schema.
- Pi-safety test: import the new module with `BETS_DB_WRITE` and `BLOB_ARCHIVE` unset; verify no pyodbc / azure import is attempted.

## Out of scope

- B.1, B.2, B.3 (cron), B.4* (downstream variants).
- Any dashboard tile or read-side query work.
- Auto-weighting in the production scanner.
- New CSV outputs anywhere.

## Definition of done

- All three phases implemented in one PR.
- Migration applied on WSL Azure SQL successfully (you don't need to apply it; the human runs the migration after review — but include the exact command in the PR description).
- Full pytest suite green, including the new tests.
- PR description includes: schema diff, sample query showing what populated rows look like for one (book, league) after a manual run on the existing blob archive, the migration command, and an explicit "Pi-safety verified" line listing what you tested.
