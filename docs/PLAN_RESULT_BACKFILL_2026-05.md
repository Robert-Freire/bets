# Result + CLV Backfill (DB-only) — 2026-05

Backfill `result`, `pnl`, `settled_at`, `pinnacle_close_prob`, `clv_pct` for `bets` and `paper_bets` in Azure SQL from football-data.co.uk FTR + PSC* data.

**Driving question.** All 88 past-kickoff paper bets in dev DB have `result = 'pending'`, NULL `pnl`, NULL `pinnacle_close_prob`, NULL `clv_pct`. FDCO already carries `FTR`/`FTHG`/`FTAG` (results) and `PSCH`/`PSCD`/`PSCA`/`PC>2.5`/`PC<2.5` (Pinnacle close odds) for every settled match. Today's `backfill_clv_from_fdco.py` extracts CLV but writes **only to CSVs** — its docstring is explicit: *"CSV-only writes. The Azure SQL closing_lines table is intentionally not populated here."* So even the CLV gate is invisible in DB.

**Scope: dev (WSL + Azure SQL) only.** Pi cron untouched. CSV writes are intentionally **dropped from this script** — A.8/A.9 is decommissioning the CSV path; we don't add new CSV writers on the way out. The scanner still appends to CSVs at scan time (until A.9) — this script just operates DB-side.

---

## How to use this doc — bot execution protocol

Same rules as `docs/PLAN_RESEARCH_2026-04.md` §"How to use this doc". Branches: `feat/result-backfill-S-X-<short-slug>`. Commit prefix: `S.X:`. Always link `Refs: docs/PLAN_RESULT_BACKFILL_2026-05.md#phase-s-X`.

- Keep changes minimal — match each phase's Tasks list exactly.
- `pytest -q` from repo root must pass before PR.
- Update `CLAUDE.md` if user-facing behaviour changes.
- Never `--no-verify`.

---

## Phase status tracker

| Phase | Title | Status | Branch |
|---|---|---|---|
| S.1 | DB-side settlement repo methods | ✅ done | feat/retry-on-cold-start |
| S.2 | FDCO backfill rewrite (DB-only) | ✅ done | feat/retry-on-cold-start |
| S.3 | compare_strategies.py reads DB | ✅ done | feat/retry-on-cold-start |
| S.4 | Tests | ✅ done | feat/retry-on-cold-start |

---

## Phase S.1 — DB-side settlement repo methods (~1h)

**Goal.** Add the UPDATE primitives to `BetRepo` so callers can settle a row and write CLV into `bets` / `paper_bets` without touching CSVs.

### Inputs
- `src/storage/repo.py` — `BetRepo`
- `src/storage/_keys.py` — UUID derivation (don't change shape)
- `src/storage/schema.sql` — target columns already exist

### Outputs
- Two new methods on `BetRepo`:
  - `settle_bet(fixture_id, side, market, line, book, *, result, pnl, pin_prob, clv_pct) -> bool`
  - `settle_paper_bet(strategy_name, fixture_id, side, market, line, book, *, result, pnl, pin_prob, clv_pct) -> bool`

### Tasks

**T1. `settle_bet`**

- `result`: `"W"` | `"L"` | `"void"` | `None` (None means "CLV-only update, leave result alone").
- `pnl`: float or None. None = don't touch.
- `pin_prob`: float or None.
- `clv_pct`: float or None.
- Build the UPDATE dynamically — only set columns whose argument is not None. `settled_at = SYSUTCDATETIME()` only when `result` is not None.
- Match by natural key: `WHERE fixture_id = ? AND side = ? AND market = ? AND line = ? AND book_id = ? AND result = 'pending'` (production bets are settled at most once; the `result = 'pending'` guard makes re-runs idempotent for the result write while still allowing CLV-only updates if the WHERE drops the result guard — see T3).
- Return rows-affected > 0.

**T2. `settle_paper_bet`**

- Same as T1 but on `paper_bets` and additionally scoped by `strategy_id`.

**T3. Idempotency split**

The result write is one-shot (`pending` → `W`/`L`). The CLV write should be allowed to refresh (FDCO can correct a row). Implement as two UPDATEs in the same method:

1. If `result is not None`: `UPDATE ... SET result=?, pnl=?, settled_at=SYSUTCDATETIME() WHERE ... AND result='pending'`.
2. If `pin_prob is not None`: `UPDATE ... SET pinnacle_close_prob=?, clv_pct=? WHERE ...` (no `pending` guard — overwrite is fine; FDCO is authoritative).

Return True if either UPDATE affected rows.

**T4. No new pyodbc imports at module level**

Methods route through the existing `_db_section()` context manager. When `db_enabled is False`, return False immediately.

### Acceptance
- [ ] Both methods land on `BetRepo`; `pytest -q` passes.
- [ ] When `BETS_DB_WRITE` unset, methods return False without importing pyodbc.
- [ ] Re-running settlement on an already-settled row returns False (no-op) for the result write but still refreshes CLV if `pin_prob` provided.

### Reviewer focus
- Confirm the natural-key match uses `book_id` (FK), not the raw book string. Use `_book_id(book)` to resolve.
- Confirm `line` normalisation goes through `normalise_line()` so the WHERE matches `add_paper_bets` insert-time form.
- Confirm `settled_at` stays naive UTC (MSSQL `datetime2` convention used elsewhere in repo.py).

---

## Phase S.2 — FDCO backfill rewrite (DB-only) (~2h)

**Goal.** Rewrite `scripts/backfill_clv_from_fdco.py` so it reads pending rows from DB, matches FDCO, and writes back to DB only. No CSV mutation.

### Inputs
- `scripts/backfill_clv_from_fdco.py` (current; CSV-driven)
- FDCO CSVs in `data/raw/<league>_2526.csv` (external reference data — kept as files; that's fine, they're not *our* data)
- `BetRepo.settle_bet` / `settle_paper_bet` (S.1)

### Outputs
- Rewritten script that produces a structured run summary.
- DB tables `bets` and `paper_bets` get `result`, `pnl` (paper only — see T4), `settled_at`, `pinnacle_close_prob`, `clv_pct` populated.

### Tasks

**T1. Pull pending rows from DB**

Add a method `BetRepo.iter_unsettled_or_no_clv()` that yields, for both `bets` and `paper_bets`, the columns needed for matching + writing:

```sql
SELECT b.id, b.market, b.line, b.side, b.book_id, bk.name AS book,
       b.odds, b.effective_odds, b.stake, b.actual_stake,
       b.result, b.pinnacle_close_prob,
       f.id AS fixture_id, f.sport_key, f.kickoff_utc, f.home, f.away,
       NULL AS strategy_id, NULL AS strategy_name        -- bets
FROM bets b
JOIN fixtures f ON f.id = b.fixture_id
JOIN books bk   ON bk.id = b.book_id
WHERE f.kickoff_utc < SYSUTCDATETIME()
  AND (b.result = 'pending' OR b.pinnacle_close_prob IS NULL)

UNION ALL

SELECT p.id, p.market, p.line, p.side, p.book_id, bk.name AS book,
       p.odds, p.effective_odds, p.stake, p.actual_stake,
       p.result, p.pinnacle_close_prob,
       f.id, f.sport_key, f.kickoff_utc, f.home, f.away,
       p.strategy_id, s.name
FROM paper_bets p
JOIN fixtures f   ON f.id = p.fixture_id
JOIN books bk     ON bk.id = p.book_id
JOIN strategies s ON s.id = p.strategy_id
WHERE f.kickoff_utc < SYSUTCDATETIME()
  AND (p.result = 'pending' OR p.pinnacle_close_prob IS NULL);
```

Yield as dicts. The `strategy_name` discriminator tells the caller which `settle_*` method to invoke.

**T2. Match against FDCO**

Reuse the existing FDCO loaders (`_refresh_csv`, `_load_fdco_index`, `_h2h_pin_prob`, `_totals_pin_prob`, `_parse_fdco_date`). Move the sport-key → FDCO-code map to use `sport_key` (Odds-API key) directly instead of CSV `sport` label, since DB stores `sport_key`.

`_FDCO_BY_SPORT_KEY` built from `load_leagues()` exactly as today, but keyed on `sport_key` (already in each entry) rather than `label`.

**T3. Result + pnl extraction**

New private helpers (replace the proposed `_result_from_fdco_row` + `_compute_pnl` from the prior plan, now operating on DB-shaped rows):

```python
def _settle_from_fdco(fdco_row: dict, market: str, side: str) -> str | None:
    if market == "h2h":
        ftr = (fdco_row.get("FTR") or "").strip()
        if ftr not in ("H", "D", "A"):
            return None
        return "W" if {"HOME":"H","DRAW":"D","AWAY":"A"}.get(side) == ftr else "L"
    if market == "totals":
        try:
            tot = float(fdco_row["FTHG"]) + float(fdco_row["FTAG"])
        except (KeyError, ValueError, TypeError):
            return None
        if side == "OVER":  return "W" if tot > 2.5 else "L"
        if side == "UNDER": return "W" if tot < 2.5 else "L"
    return None


def _pnl(stake: float | None, eff_odds: float | None, result: str) -> float | None:
    if stake is None or eff_odds is None or stake <= 0 or eff_odds <= 1:
        return None
    if result == "W":    return round(stake * (eff_odds - 1), 2)
    if result == "L":    return round(-stake, 2)
    if result == "void": return 0.0
    return None
```

**T4. Pnl scoping**

- Paper bets: compute pnl from `stake` * `effective_odds` (or fall back to `odds`). Always populated when result is W/L.
- Production bets: only compute pnl from `actual_stake` if non-NULL. If `actual_stake` is NULL, set `result` and `settled_at` but leave `pnl` NULL — manual placement may differ from suggested stake. (Keeps current "pnl is manual for real bets" semantics, but in DB.)

**T5. Driver loop**

```python
for row in repo.iter_unsettled_or_no_clv():
    fdco_row = _lookup_fdco(row, fdco_by_sport)
    if fdco_row is None:
        no_match += 1; continue

    pin_prob, clv_pct = _clv_from_fdco(row, fdco_row)   # may be (None, None)
    new_result = None
    new_pnl    = None
    if row["result"] == "pending":
        new_result = _settle_from_fdco(fdco_row, row["market"], row["side"])
        if new_result is not None:
            stake_basis = (row["actual_stake"] if row["strategy_name"] is None
                           else row["stake"])
            new_pnl = _pnl(stake_basis,
                           row["effective_odds"] or row["odds"],
                           new_result)

    if new_result is None and pin_prob is None:
        continue

    ok = (repo.settle_paper_bet if row["strategy_name"]
          else repo.settle_bet)(... )
    counters[...] += 1
```

**T6. Summary output**

```
[fdco] settled: bets W/L/void = X/Y/Z | paper W/L/void = X/Y/Z
       clv backfilled: bets=X paper=Y
       no FDCO match: X | already complete: Y
```

**T7. Drop CSV writes entirely**

Remove `_process_csv` and the path glob over `logs/paper/*.csv` + `logs/bets.csv`. The script no longer touches CSVs.

**T8. CLI args preserved**

`--dry-run`, `--leagues`, `--since` keep their semantics. Dry-run logs intended UPDATEs without executing them.

**T9. Refuse to run without DB**

If `BETS_DB_WRITE` is unset, exit with a clear error: *"Result/CLV backfill writes to Azure SQL only. Set BETS_DB_WRITE=1 + AZURE_SQL_* env vars (see CLAUDE.md A.4)."* No silent fallback.

### Acceptance
- [ ] Script with `BETS_DB_WRITE=1` populates `result`, `pnl`, `settled_at`, `pinnacle_close_prob`, `clv_pct` for past-kickoff rows in `bets` and `paper_bets`.
- [ ] No mutation of any file under `logs/`.
- [ ] Script with `BETS_DB_WRITE` unset exits non-zero with the stated error.
- [ ] Re-running is idempotent: zero new settlement writes, zero new CLV writes (no `result` flip, CLV columns already populated → UPDATE matches but writes identical values).
- [ ] `--dry-run` prints the would-be UPDATE counts and exits 0 with no DB writes.
- [ ] `pytest -q` passes.

### Reviewer focus
- Confirm `result = 'pending'` filter prevents already-settled rows from being re-settled even if FDCO data shifts.
- Confirm production-bet pnl stays NULL when `actual_stake` is NULL.
- Confirm sport-key map uses `sport_key` from `load_leagues()`, not the human label — DB has the canonical key.
- Confirm `--dry-run` is verifiable without DB write permissions (still hits SELECT, but no UPDATE).

### Verification
```bash
export $(cat .env.dev) && python3 scripts/backfill_clv_from_fdco.py --dry-run

# After a real run:
export $(cat .env.dev) && python3 -c "
import sys; sys.path.insert(0,'.')
from src.storage.repo import BetRepo
r = BetRepo()
cur = r._conn.cursor()
for tbl in ('bets','paper_bets'):
    cur.execute(f\"SELECT result, COUNT(*) FROM {tbl} GROUP BY result\")
    print(tbl, list(cur.fetchall()))
    cur.execute(f\"SELECT COUNT(*) FROM {tbl} WHERE pinnacle_close_prob IS NOT NULL\")
    print(tbl, 'with CLV:', cur.fetchone()[0])
"
```

---

## Phase S.3 — `compare_strategies.py` reads DB (~1h)

**Goal.** Strategy comparison report sourced from `paper_bets` in DB. CSVs are never read.

### Inputs
- `scripts/compare_strategies.py` (currently CSV-driven)
- `BetRepo` SELECT helpers
- `paper_bets` populated via S.1/S.2 (settlement + CLV) and via `add_paper_bets` (insert-time)

### Outputs
- Rewritten script. Same Markdown table shape, with new P&L columns.
- `docs/STRATEGY_COMPARISON.md` regenerated.

### Tasks

**T1. New repo helper**

`BetRepo.fetch_paper_bets_for_compare()` returns rows with `strategy_name`, `result`, `stake`, `pnl`, `clv_pct`, `edge`, `n_books`, `confidence` for all paper bets. One round-trip; aggregation in Python.

**T2. Aggregate**

Per strategy:
- `n_total`, `n_with_clv` (clv_pct NOT NULL), `avg_clv_pct`, `median_clv_pct`
- `settled` = rows where `result IN ('W','L','void')`
- `wins`, `win_pct = wins / settled`
- `total_pnl = sum(pnl)`, `roi_pct = total_pnl / sum(stake) * 100`
- `avg_edge`

**T3. Markdown columns**

After `Avg Edge`, add `Settled | Win % | ROI %`. Format:
- `Settled`: int or `—` if 0.
- `Win %`: `{x:.0%}` if settled ≥ 5, else `—`.
- `ROI %`: `{x:+.1f}%` if settled ≥ 5, else `—`.
- Sort key unchanged (avg CLV desc).

**T4. Refuse without DB**

Same env-var gate as S.2. No CSV fallback.

### Acceptance
- [ ] `python3 scripts/compare_strategies.py` produces a report from DB.
- [ ] Pre-settlement (no W/L rows): `Settled = —`, no division-by-zero.
- [ ] Post-settlement: numeric values match a hand-computed paper P&L for at least one strategy.
- [ ] `docs/STRATEGY_COMPARISON.md` regenerated with new columns.
- [ ] No `csv.DictReader` reads of `logs/paper/` or `logs/bets.csv`.
- [ ] `pytest -q` passes.

### Reviewer focus
- Guard against `sum(stake) == 0` and `settled == 0`.
- Confirm the script no longer imports from `logs/` paths at all.

---

## Phase S.4 — Tests (~1h)

**Goal.** Cover the new logic via the SQLite in-memory `BetRepo` fixture used elsewhere in the suite.

### Tasks

**T1. `tests/test_settlement.py` — pure functions**

`_settle_from_fdco`:
- h2h H/D/A × HOME/DRAW/AWAY → expected W/L matrix.
- totals 2.5: (2,1)/OVER→W, (1,1)/OVER→L, (1,1)/UNDER→W, (2,1)/UNDER→L.
- FTR blank, FTHG/FTAG blank/NaN → None.
- Unknown market → None.

`_pnl`:
- W: stake*(odds-1). L: -stake. void: 0.0. Pending/None: None.
- Edge cases: stake=0 → None; odds<=1 → None.

**T2. `tests/test_repo.py` — `settle_bet` + `settle_paper_bet`**

Using the existing in-memory SQLite fixture (`tests/conftest.py`):
- Insert a `bets` row with `result='pending'`, then `settle_bet(... result='W', pnl=10.5, pin_prob=0.42, clv_pct=0.03)`. Read back: result, pnl, settled_at, pinnacle_close_prob, clv_pct populated. Returns True.
- Re-call same args. Result-write is no-op (still True for CLV refresh, but `settled_at` unchanged — capture it before the second call).
- Same scenarios for `settle_paper_bet`, including strategy scoping (two strategies, same fixture/side/book; only the targeted row is updated).

**T3. `tests/test_backfill_clv.py` — driver integration**

- Stub `_refresh_csv` to a fixture file; insert a paper_bet for that fixture; run main; assert DB row settled.
- Negative: future-kickoff fixture is skipped.
- Negative: BTTS market → never settled (no FDCO column).

**T4. No CSV writes**

In every integration test, snapshot mtime of `logs/paper/`/`logs/bets.csv` (or use a tmp_path-pinned `_PAPER_DIR`) and assert no mutations.

### Acceptance
- [ ] `pytest tests/test_settlement.py tests/test_backfill_clv.py tests/test_repo.py -v` all pass.
- [ ] `pytest -q` passes overall.
- [ ] No test reads or writes real `logs/` files.

---

## What this plan deliberately does NOT do

- **Doesn't touch the scanner.** `add_bets` / `add_paper_bets` keep dual-writing to CSV until A.9 — out of scope here.
- **Doesn't backfill `closing_lines` table.** That table is frozen alongside `closing_line.py`; CLV in `bets`/`paper_bets` columns is the live signal. Re-introduce only if drift snapshots come back.
- **Doesn't change cron.** Mon 08:00 UTC backfill is sufficient for weekend football; FDCO doesn't publish faster. Add a Wed slot only if a future midweek-fixture cycle warrants it.
- **Doesn't migrate Pi.** Pi still runs the current CSV-only backfill; A.10 sprint covers Pi onboarding to DB.
