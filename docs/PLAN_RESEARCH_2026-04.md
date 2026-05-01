# Research Follow-up Plan — 2026-04

Implementation plan derived from `docs/RESEARCH_NOTES_2026-04.md`. Phases are sized for one PR each, ordered by adopt-cost × signal × dependency.

> **Status (2026-05-01):** R.0–R.3, R.5.5a/b, R.7–R.9, R.11 ✅ done. **R.5 / R.5.5c / R.6 still pending.** R.10 blocked on CLV evidence. R.4 auto-runs (existing cron). Architectural shift since this plan was written: `scripts/closing_line.py` paused 2026-05-01 in favour of weekly football-data.co.uk Pinnacle close-odds backfill (`scripts/backfill_clv_from_fdco.py`, Mon 08:00 UTC). The `closing_line.py` references in this doc still describe the architecture they were written against; treat them as historical context, not as something to extend further. Any future CLV pipeline changes should live in the FDCO backfill path. CLAUDE.md is the authoritative current-state doc.

**Driving question.** Does the scanner itself need updating? **Yes — but staged.** The Phase 5.5 paper-portfolio infrastructure means new strategy variants don't touch the scanner; they're added in `src/betting/strategies.py` and run alongside production. The scanner only changes when a variant **graduates** from shadow (≥50 bets settled, positive CLV) to a default-flip — and when we ingest a genuinely new market (Asian Handicap).

---

## How to use this doc — bot execution protocol

This doc is designed for asynchronous execution by an implementation bot, with verification by a separate reviewer bot. Both should pick up phases from this file alone, with no chat-history context required.

### Picking up a phase
1. Read **Phase status tracker** below. Pick the lowest-numbered `pending` phase whose dependency phases (per the graph) are all `done`.
2. Read the phase's full body (Goal, Inputs, Outputs, Tasks, Acceptance, Reviewer focus, Verification).
3. If anything is unclear or contradictory, **stop and add a `BLOCKED: <reason>` note** in the phase status tracker — do not silently expand scope.
4. Create a branch: `research-r-X[.Y]-<short-slug>` (e.g. `research-r-1-cheap-variants`, `research-r-1-5-kaunitz-classic`).

### During implementation
- Keep changes minimal — match the phase's `Tasks` list exactly. No drive-by refactors.
- Write tests as part of the phase, not after.
- Run `pytest -q` from repo root before opening a PR. All existing tests must continue to pass.
- Update `README.md` and `CLAUDE.md` if the phase changes user-facing behaviour or default scanner config (per standing memory rule).

### Commit conventions
- One commit per phase (or per logical sub-step within a phase).
- Commit message format:
  ```
  R.X[.Y]: <one-line summary, imperative voice>
  
  <2–4 bullets describing what changed>
  
  Refs: docs/PLAN_RESEARCH_2026-04.md#phase-r-X
  ```
- Example: `R.1: add variants I, L, M, N to STRATEGIES`

### PR conventions
- Title: same as commit summary (no Phase prefix needed if single commit).
- Body template:
  ```markdown
  ## Phase
  R.X[.Y] — <title>

  ## What changed
  <bullets>

  ## Acceptance checklist
  <copy from phase's Acceptance block, with [x] where met>

  ## Verification commands
  <copy from phase's Verification block, with output stubs filled in after running>
  ```
- Always link back to this doc: `Refs: docs/PLAN_RESEARCH_2026-04.md`.

### Verifier bot protocol
For each PR, the reviewer bot:
1. Checks out the PR branch.
2. Runs each command in the phase's **Verification commands** block. Confirms output matches the expected pattern.
3. Confirms every Acceptance checkbox is genuinely met (re-checks rather than trusting the PR body).
4. Reads files mentioned in **Reviewer focus** for the specific concerns flagged there.
5. Approves on full pass; requests changes with explicit naming of the failed step otherwise.

### Failure handling
- **Pre-commit hook failure**: investigate root cause, fix, create a NEW commit. Never `--no-verify`.
- **Phase blocker mid-implementation**: stop, add `BLOCKED: <reason>` to the phase status tracker, comment in the PR.
- **Existing test breaks unexpectedly**: investigate before modifying it. Do NOT delete or `.skip` tests to make CI pass.
- **Decision-deferred phases**: when a phase body specifies a fallback path, follow that path's budget rule explicitly and document the decision in the PR body.

### Branch hygiene
- Rebase onto `main` before opening PR. No merge commits in feature branches.
- Squash-merge to main; squashed commit message = original phase commit message.

### File-path glossary

| Item | Path |
|---|---|
| Strategy variants | `src/betting/strategies.py` |
| Consensus computation | `src/betting/consensus.py` |
| Devig methods (shin/proportional/power) | `src/betting/devig.py` |
| Kelly | `src/betting/kelly.py` |
| Risk pipeline | `src/betting/risk.py` |
| Commission rates | `src/betting/commissions.py` |
| Scanner main | `scripts/scan_odds.py` |
| Closing line capture | `scripts/closing_line.py` |
| Strategy comparison | `scripts/compare_strategies.py` |
| Backtest entry (today) | `src/betting/consensus.py::backtest_consensus` |
| Walk-forward primitive (after R.5.5a) | `src/betting/walk_forward.py` |
| Backtest entry (after R.5.5c) | `scripts/walk_forward_backtest.py` |
| Tests | `tests/test_*.py` |
| Bets log | `logs/bets.csv` |
| Paper-portfolio logs | `logs/paper/<variant>.csv` |
| Scan log | `logs/scan.log` |
| Backtest doc | `docs/BACKTEST.md` |
| Strategy comparison doc | `docs/STRATEGY_COMPARISON.md` |
| Source notes (this round) | `docs/RESEARCH_NOTES_2026-04.md` |
| .env (gitignored) | `.env` (must run `export $(cat .env)` before scanner) |

### Common commands

```bash
# Run all tests
pytest -q

# Run only strategy tests
pytest -q tests/test_strategies.py

# Run scanner with env loaded
export $(cat .env) && python3 scripts/scan_odds.py

# Compare strategy variants (outputs docs/STRATEGY_COMPARISON.md)
python3 scripts/compare_strategies.py

# Tail scanner log
tail -f logs/scan.log

# Verify no hardcoded paths in scripts
grep -E '/(home|mnt)/' scripts/*.py src/**/*.py | grep -v test_  # should be empty
```

---

## Phase status tracker

| Phase | Title | Window | Status |
|---|---|---|---|
| R.0 | Stale doc fix (CLAUDE.md:7) | Now | done |
| R.1 | Add 4 cheap variants (I, L, M, N) | Friday | done |
| R.1.5 | Paper-faithful baseline variant (O_kaunitz_classic) | Friday | done |
| R.1.6 | Max-odds shopping variant (P_max_odds_shopping) — optional | Friday if time | done |
| R.2 | Sharp-weighted consensus variant (J) | Friday | done |
| R.3 | SBK availability probe → UK_LICENSED_BOOKS | Friday | done |
| R.4 | Weekend data collection | Sat–Sun | runs automatically (existing cron) |
| R.5 | Monday analysis: §4.3, 4.5, 4.6 + compare_strategies | Mon AM | pending |
| R.5.5a | Walk-forward scaffold: `TimeSeriesSplit`-based primitive + loader + tests | Thu–Fri (this week) | done |
| R.5.5b | Add 16 new leagues from football-data.co.uk to backtest data | Thu–Fri (this week) | done — 91k matches / 22 leagues; see docs/FDCO_INGEST_NOTES.md |
| R.5.5c | Walk-forward run + per-fold report → `docs/BACKTEST.md` | Mon PM – Tue | pending |
| R.6 | Graduate winning variants AND winning leagues → scanner defaults | Wed | conditional on R.5.5c |
| R.7 | bets.csv schema: `devig_method`, `weight_scheme` columns | Wed | done (commit a980efc) |
| R.8 | Draw-bias variant (K) — needs xG runtime hookup | Thu–Fri | done (`scripts/refresh_xg.py`; weekly Mon 06:00 cron; variant K shipped) |
| R.9 | Asian Handicap feasibility probe (The Odds API) | Thu–Fri | ✅ done (2026-04-30, `docs/AH_FEASIBILITY.md`) |
| R.10 | AH probability conversion module (planning only) | Following week | **blocked on CLV confirmation** (gate in §R.10 below) |
| R.11 | Eval-window provenance: `code_sha` + `strategy_config_hash` columns; `compare_strategies` filters to current config window | Now (pre-weekend) | done (2026-05-01) |

**Dependency graph:**

```
R.0 ─┐
     ├─ R.1 ──┐
     ├─ R.1.5 ┤
     ├─ R.1.6 ┼─ R.4 ─ R.5 ─────────────┐
     ├─ R.2 ──┤                          ├─ R.5.5c ─ R.6 ─ R.7
     ├─ R.3 ──┘                          │
     │                                   │
     └─ R.5.5a ─ R.5.5b (extra leagues) ┘
                                         ├─ R.8 (xG)
                                         └─ R.9 ─ R.10 (deferred)
```

R.5.5a (scaffold) and R.5.5b (extra leagues from football-data.co.uk) are independent of the weekend data chain and can be picked up immediately. R.5.5c joins the chain once R.5 (Monday analysis), R.5.5a (scaffold), and R.5.5b (extra leagues) are all merged. R.5.5b alphabetical order matches dependency order: a → b → c.

---

## Goal & non-goals

**Goal.** By Monday EOD, have 5 new paper variants with ≥48h of shadow data, an answer to whether favourite-longshot bias is measurable in our EPL h2h data, and a power-vs-Shin backtest comparison committed. By next Friday, graduate the winners into scanner defaults and have a written feasibility note on Asian Handicap integration.

**Non-goals.**
- No real-money bets on new variants.
- No refactor of `scan_odds.py` core pipeline. Only `consensus.py` weighting, `strategies.py` config, and (Phase R.6/R.7) integration of graduates.
- No exchange auto-placement work (Phase 8 territory).
- No xG real-time pipeline beyond what variant K minimally needs.
- No AH fetch implementation in this sprint — only feasibility note.

---

## Phase R.0 — Stale doc fix (~5 min)

**Goal.** Remove the stale "Phase 1.5 pending" note in `CLAUDE.md:7`. `BACKTEST.md` already has Shin-corrected numbers (generated 2026-04-29).

**Tasks.**
1. Open `CLAUDE.md`. Locate line 7 — currently:
   ```
   *Note: the legacy backtest (+6.1% ROI at 2% edge) was computed on raw implied probabilities, not de-vigged. A corrected backtest is pending (Plan phase 1.5).*
   ```
2. Replace with:
   ```
   *Backtest results — including Shin-corrected numbers (2% edge → 17.65% ROI, generated 2026-04-29) — are in `docs/BACKTEST.md`.*
   ```

**Acceptance.**
- [ ] `CLAUDE.md` no longer contains the substring "corrected backtest is pending".
- [ ] `CLAUDE.md` contains a pointer to `docs/BACKTEST.md` near line 7.

**Verification commands.**
```bash
# Should print 0
grep -c "corrected backtest is pending" CLAUDE.md

# Should print >= 1
grep -c "docs/BACKTEST.md" CLAUDE.md

# Should print >= 1
grep -c "Shin-corrected" CLAUDE.md
```

**Reviewer focus.** That the replacement preserves the surrounding markdown formatting (italics, position relative to `## What this is` heading).

---

## Phase R.1 — Cheap variants I, L, M, N (~80 min)

**Goal.** Add four variants to `STRATEGIES` in `src/betting/strategies.py` that require only new fields on `StrategyConfig`, no changes to consensus computation.

**Inputs.** R.0 done.

**Tasks.**
1. Extend `StrategyConfig` with:
   ```python
   min_consensus_prob:  float = 0.0          # M, N (longshot guard)
   max_consensus_prob:  float = 1.0          # N (competitive-only)
   kelly_fraction:      float = 0.5          # L (allows 0.4-Kelly variant)
   ```
2. In `_filter_candidate(...)` (or wherever the per-bet filter lives), add:
   ```python
   if cons[side] < strategy.min_consensus_prob: continue
   if cons[side] > strategy.max_consensus_prob: continue
   ```
3. Find where Kelly is applied (likely `src/betting/kelly.py` called from `scan_odds.py`). Plumb `kelly_fraction` from `StrategyConfig` through to the `kelly_fraction(...)` call.
4. Append four entries to `STRATEGIES`:

```python
StrategyConfig(
    name="I_power_devig",
    label="I: Power devig",
    description="Power devig instead of Shin; tests Bethero recommendation",
    devig="power",
),
StrategyConfig(
    name="L_quarter_kelly",
    label="L: 0.4-Kelly",
    description="Tighter Kelly fraction (0.4) — Aldous/Downey caution under uncertainty",
    kelly_fraction=0.4,
),
StrategyConfig(
    name="M_min_prob_15",
    label="M: Min-prob 15%",
    description="Reject bets with consensus prob < 15%; longshot-bias guard (Hegarty & Whelan 2025)",
    min_consensus_prob=0.15,
),
StrategyConfig(
    name="N_competitive_only",
    label="N: Competitive-only",
    description="Only flag matches where consensus prob ∈ [0.30, 0.70]; Clegg & Cartlidge 2025 surviving signal",
    min_consensus_prob=0.30,
    max_consensus_prob=0.70,
),
```

5. Tests in `tests/test_strategies.py`:
   - I: produces same bet count as G_proportional ± a small tolerance (both diverge from Shin similarly on heavy favourites).
   - L: produces identical bet count to A_production but stake column is 0.8× (0.4/0.5).
   - M: rejects all bets where `cons[side] < 0.15` (use a fabricated longshot fixture).
   - N: rejects bets where `cons[side] < 0.30` or `> 0.70`.

**Pre-flight checks.**
```bash
# Confirm current state passes
pytest -q tests/test_strategies.py

# Confirm new fields don't already exist
grep -E "^\s+(min_consensus_prob|max_consensus_prob|kelly_fraction):" src/betting/strategies.py  # should be empty

# Confirm Kelly source location (informational — find the function called from scan_odds.py)
grep -n "kelly" scripts/scan_odds.py src/betting/kelly.py
```

**Order of operations.**
1. Add fields to `StrategyConfig` first, with defaults that don't change behaviour (existing variants must work unchanged).
2. Plumb `kelly_fraction` through `kelly.py` and `scan_odds.py`. Run existing tests — they should all still pass.
3. Add `_filter_candidate` checks for `min_consensus_prob` / `max_consensus_prob`.
4. Append four `STRATEGIES` entries.
5. Write tests for the four new variants. Run pytest.
6. Smoke run scanner once.

**Acceptance.**
- [ ] `pytest -q tests/test_strategies.py` passes (existing + 4 new tests).
- [ ] `python3 scripts/scan_odds.py` (with `.env` loaded) writes rows to all 12 of `logs/paper/{A..N}_*.csv` after one scan that flagged ≥1 bet under `A_production`. (Variants will produce 0 rows on a quiet day — only required when production has ≥1 row.)
- [ ] `A_production` bet count for a synthetic fixture is unchanged (regression check).

**Verification commands.**
```bash
# Must pass
pytest -q tests/test_strategies.py

# Variants present and configured
python3 -c "
from src.betting.strategies import STRATEGIES
new = {'I_power_devig','L_quarter_kelly','M_min_prob_15','N_competitive_only'}
present = {s.name for s in STRATEGIES if s.name in new}
assert present == new, f'Missing: {new - present}'
print('OK: 4 new variants present')
"

# kelly_fraction is actually applied (not just stored)
python3 -c "
from src.betting.strategies import StrategyConfig
sc = StrategyConfig(name='X', label='X', description='x', kelly_fraction=0.4)
assert sc.kelly_fraction == 0.4
print('OK: kelly_fraction field accepts 0.4')
"

# Smoke: A_production output is structurally unchanged
test -d logs/paper && ls logs/paper/A_production*.csv >/dev/null && echo "OK: A_production paper log exists"
```

**Reviewer focus.** That `kelly_fraction` actually multiplies the stake — not just stored on the config. Verify by reading where Kelly is invoked from `scan_odds.py` and tracing the multiplication. Match WagerBrain's `kelly_size` API shape (already-validated parameter naming, see `RESEARCH_NOTES_2026-04.md` §9.3).

---

## Phase R.1.5 — Paper-faithful baseline variant `O_kaunitz_classic` (~45 min)

**Goal.** Add a strategy variant that implements the **exact Kaunitz et al. (2017) formula**, recovered from `konstanzer/online-sports-betting/odds_model.py` and `Lisandro79/BeatTheBookie/src/strategies/beatTheBookie.m`. **Without this baseline, we cannot say how much of our backtest ROI is attributable to our additions vs. the underlying market regime.**

**Inputs.** R.1 done.

**Tasks.**
1. Extend `StrategyConfig`:
   ```python
   raw_consensus:       bool  = False    # O: skip Shin/power devig, use mean of 1/odds
   kaunitz_alpha:       float = 0.0      # O: paper's α (commission adjustment); 0.0 disables
   max_odds_shopping:   bool  = False    # O: flag based on max(odds) across UK books, not specific book
   ```
2. In consensus computation:
   - When `raw_consensus=True`, skip `_apply_devig` and use `1 / book_odds[side]` directly per book, then arithmetic-mean across books.
3. In bet-flagging:
   - When `kaunitz_alpha > 0`, the bet condition becomes:
     ```python
     edge_kaunitz = (consensus_prob - kaunitz_alpha) * book_odds - 1
     # flag if edge_kaunitz > 0 (replaces the additive `cons - book_implied >= min_edge` rule)
     ```
   - When `max_odds_shopping=True`, evaluate against `max(uk_book_odds[side])` instead of per-book.
4. Append:
   ```python
   StrategyConfig(
       name="O_kaunitz_classic",
       label="O: Kaunitz classic (paper)",
       description="Paper-faithful Kaunitz: raw consensus, α=0.05, max-odds shopping, min 4 books",
       raw_consensus=True,
       kaunitz_alpha=0.05,
       max_odds_shopping=True,
       min_books=4,            # paper used 3-4
       max_dispersion=None,    # paper had no dispersion filter
       drop_outlier_book=False,# paper had no outlier filter
       markets=("h2h",),       # paper only studied h2h
   ),
   ```
5. Tests:
   - With α=0.05 and a fabricated fixture where `(1/avg_odds - 0.05) * max_odds > 1`, the variant flags.
   - With α=0.05 and a fabricated fixture where the same expression ≤ 1, the variant skips.
   - Bet count under `O_kaunitz_classic` should be **substantially higher** than `A_production` for the same data — the paper rule is more permissive.

**Acceptance.**
- [ ] Variant present in `STRATEGIES` and produces a non-empty `logs/paper/O_kaunitz_classic.csv` after one scan that flagged ≥1 bet under `A_production`.
- [ ] Tests pass.
- [ ] Documented in `docs/RESEARCH_NOTES_2026-04.md` §9.1 backreference (already done — verify cross-link).

**Verification commands.**
```bash
# Must pass
pytest -q tests/test_strategies.py

# Variant present
python3 -c "
from src.betting.strategies import STRATEGIES
o = next((s for s in STRATEGIES if s.name == 'O_kaunitz_classic'), None)
assert o is not None, 'O_kaunitz_classic not in STRATEGIES'
assert o.raw_consensus is True
assert o.kaunitz_alpha == 0.05
assert o.max_odds_shopping is True
assert o.min_books == 4
assert o.markets == ('h2h',)
print('OK: O_kaunitz_classic correctly configured')
"

# raw_consensus truly bypasses devig — instrument with a fabricated probe
python3 -c "
from src.betting.strategies import StrategyConfig, _apply_devig
# When raw_consensus=True path is taken, devig should NOT be called.
# Manual smoke: confirm code path branches on raw_consensus before _apply_devig.
import inspect
import src.betting.strategies as m
src = inspect.getsource(m)
assert 'raw_consensus' in src, 'raw_consensus flag not referenced in strategies.py'
print('OK: raw_consensus flag is referenced (manual code review still required)')
"
```

**Reviewer focus.** That `raw_consensus` truly bypasses devig (no lingering Shin call). Read the code path in `_compute_consensus` and confirm the `if strategy.raw_consensus` branch is taken **before** any devig logic. Confirm `max_odds_shopping` correctly considers only `UK_LICENSED_BOOKS` (not all 36 books — even Kaunitz had to bet through accessible bookies).

**Why this phase matters most for the weekend test.** The point of weekend testing is comparison. Without `O_kaunitz_classic` running alongside our system, every other variant's CLV is measured against `A_production` only — which is itself untethered from any external benchmark. With `O`, we can answer "are we getting positive value from Shin + dispersion + outlier-z, or is most of our edge just being-in-a-value-betting-market?"

---

## Phase R.1.6 — Max-odds shopping variant `P_max_odds_shopping` (~30 min, optional)

**Goal.** Test whether always taking the best-priced UK book on a flagged outcome (rather than the specific book that triggered the flag) improves CLV. Same Shin devig + filters as production, only the per-bet odds-source changes.

**Inputs.** R.1.5 done (provides the `max_odds_shopping` field).

**Tasks.**
1. Append:
   ```python
   StrategyConfig(
       name="P_max_odds_shopping",
       label="P: Max-odds shopping",
       description="Production logic, but bet at the best-priced UK book on flagged outcome",
       max_odds_shopping=True,
   ),
   ```
2. No additional code beyond R.1.5's `max_odds_shopping` plumbing.

**Acceptance.**
- [ ] Variant produces rows; `book` column reflects the max-odds book per row.
- [ ] No regression in `A_production`.

**Skip if R.1.5 takes longer than expected.** This is the lowest-priority weekend variant.

---

## Phase R.2 — Sharp-weighted consensus variant J (~1.5h)

**Goal.** Add `J_sharp_weighted` variant using datagolf-derived sharpness weights.

**Inputs.** R.1 done.

**Tasks.**
1. Add to `src/betting/consensus.py` (top-level constant):
   ```python
   # Sharpness weights — seeded from datagolf.com/how-sharp-are-bookmakers.
   # Books not listed default to 1.0. Refine empirically after 4 weeks of CLV.
   SHARPNESS_WEIGHTS: dict[str, float] = {
       # Sharp tier
       "betfair_ex_uk": 1.5, "smarkets": 1.5, "matchbook": 1.5,
       # Mid tier
       "marathonbet": 1.0, "sportingbet": 1.0, "bwin": 1.0,
       "betvictor": 1.0, "williamhill": 1.0, "betfair_sb_uk": 1.0,
       # Soft tier (retail UK)
       "betfred_uk": 0.7, "coral": 0.7, "ladbrokes_uk": 0.7,
       "skybet": 0.7, "paddypower": 0.7, "boylesports": 0.7,
       "leovegas": 0.7, "casumo": 0.7, "virginbet": 0.7,
       "livescorebet": 0.7, "sport888": 0.7, "grosvenor": 0.7,
       "betway": 0.7,
   }
   ```
2. Extend `StrategyConfig`:
   ```python
   sharpness_weights: dict | None = None   # J: book → weight; None = uniform
   ```
3. Modify `_compute_consensus` to apply per-book weights when `strategy.sharpness_weights` is set. Use weighted mean of fair probs, with `weights[book] = strategy.sharpness_weights.get(book, 1.0)`. Stdev computation unchanged (still uniform — dispersion filter is about disagreement, not sharpness).
4. Append to `STRATEGIES`:
   ```python
   StrategyConfig(
       name="J_sharp_weighted",
       label="J: Sharp-weighted",
       description="Sharpness-weighted consensus per datagolf blind-return ranking",
       sharpness_weights=SHARPNESS_WEIGHTS,
   ),
   ```
5. Tests:
   - With `sharpness_weights=None`, output identical to `A_production` (no regression).
   - With weights set, sharper books pull the consensus toward their fair probs (verify with a synthetic 3-book fixture: 2 sharp aligned, 1 soft outlier).

**Acceptance.**
- [ ] `pytest -q tests/test_strategies.py` passes including J test.
- [ ] Manual smoke: run one scan, confirm `logs/paper/J_sharp_weighted.csv` rows have plausible consensus values (not wildly different from A_production for the same fixtures).
- [ ] Bet-count drop vs A_production logged in scan output (anticipated effect from §4.3).

**Verification commands.**
```bash
# Must pass
pytest -q tests/test_strategies.py

# Variant present and configured
python3 -c "
from src.betting.strategies import STRATEGIES
from src.betting.consensus import SHARPNESS_WEIGHTS
j = next((s for s in STRATEGIES if s.name == 'J_sharp_weighted'), None)
assert j is not None
assert j.sharpness_weights is not None
assert j.sharpness_weights == SHARPNESS_WEIGHTS
print('OK: J_sharp_weighted wired to SHARPNESS_WEIGHTS')
"

# Default weight is 1.0 for unknown books (regression — sanity)
python3 -c "
from src.betting.consensus import SHARPNESS_WEIGHTS
unknown = SHARPNESS_WEIGHTS.get('unknown_book_xyz', 1.0)
assert unknown == 1.0
print('OK: unknown books default to weight 1.0')
"

# Equal weights produce same consensus as unweighted (fabricated test)
python3 -c "
from src.betting.strategies import StrategyConfig
# When sharpness_weights=None, behaviour must match A_production exactly.
sc_a = StrategyConfig(name='A', label='A', description='x')
sc_j_offmode = StrategyConfig(name='J', label='J', description='x', sharpness_weights=None)
assert sc_a.sharpness_weights is None
assert sc_j_offmode.sharpness_weights is None
print('OK: weights=None preserves A_production behaviour')
"
```

**Reviewer focus.** Weighted-mean math correctness in `_compute_consensus`. Specifically: (a) books not in `SHARPNESS_WEIGHTS` default to 1.0; (b) the weighted mean falls back to arithmetic mean when `sharpness_weights is None`; (c) stdev (used by dispersion filter) is still computed unweighted — dispersion measures disagreement, not consensus quality.

---

## Phase R.3 — SBK availability probe (~15 min)

**Goal.** Verify whether SBK is in The Odds API `uk` region. If yes, add to `UK_LICENSED_BOOKS`.

**Inputs.** None.

**Tasks.**
1. Grep recent `logs/scan.log` for `"sbk"` (case-insensitive). If present, note the exact bookmaker_key.
2. If not in logs, fetch one fixture's odds via The Odds API with `regions=uk` and inspect `bookmakers[].key` field.
3. If SBK present: add the key to `UK_LICENSED_BOOKS` set in `src/betting/strategies.py` (and `scripts/scan_odds.py` if duplicated).
4. If not present: log a TODO line in this doc under "Open carryovers" — the API may surface it later, or we may need premium tier.

**Acceptance.**
- [ ] Either: SBK key confirmed and added to `UK_LICENSED_BOOKS`, OR documented as not-currently-available.
- [ ] No silent absence — explicitly recorded either way.

**Verification commands.**
```bash
# Check scan log for any reference to SBK
grep -in 'sbk' logs/scan.log | head -5

# Check API response (requires .env loaded). Save sample to /tmp for inspection.
export $(cat .env)
curl -s "https://api.the-odds-api.com/v4/sports/soccer_epl/odds/?apiKey=${ODDS_API_KEY}&regions=uk&markets=h2h" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); keys=set(); [keys.update(b['key'] for b in m['bookmakers']) for m in d]; print('All UK book keys:', sorted(keys)); print('SBK present:', any('sbk' in k.lower() for k in keys))"

# If SBK present, confirm it's added to UK_LICENSED_BOOKS:
python3 -c "
from src.betting.strategies import UK_LICENSED_BOOKS
present = any('sbk' in b.lower() for b in UK_LICENSED_BOOKS)
print('SBK in UK_LICENSED_BOOKS:', present)
"

# If SBK NOT present in API: confirm Open carryovers section of THIS doc has been updated with the not-available note.
grep -A1 "SBK" docs/PLAN_RESEARCH_2026-04.md | head -10
```

---

## Phase R.4 — Weekend data collection (passive, Sat–Sun)

**Goal.** Existing crontab fires Sat 10:30 + Sat 16:30 + Sun 12:30 EPL/league scans plus tennis/NBA scans. New variants record paper bets to `logs/paper/{I,J,L,M,N}_*.csv`.

**Inputs.** R.1, R.2, R.3 deployed by Friday EOD.

**Tasks.**
- Nothing manual. Verify Friday evening that variants are firing by tailing `logs/scan.log`.

**Acceptance.**
- [ ] By Sunday EOD, ≥1 row per variant in respective `logs/paper/*.csv` (assumes weekend has flagged events). Tennis-heavy weekends will produce more rows.

---

## Phase R.5 — Monday analysis (~2h)

**Goal.** Generate the four empirical answers from §4 of `RESEARCH_NOTES_2026-04.md`.

**Inputs.** R.4 done — `logs/paper/*.csv` populated.

**Tasks.**
1. **§4.3 — bet-count comparison.** Run `python3 scripts/compare_strategies.py`. Note J's bet-count delta vs A in the output table. If > 50% drop, document under the "Findings — 2026-04 weekend" subsection (see task 5) the recommendation to ramp J's `min_edge` to 2.5% next sprint.
2. **§4.5 — power vs Shin backtest.** Re-run the existing backtest entry point with `devig="power"`. Located in `src/betting/consensus.py::backtest_consensus`; called from `main.py` or directly. Add a third column to `docs/BACKTEST.md` next to `raw` and `shin`. **Whole-period only — superseded by R.5.5c's walk-forward.**
3. **§4.6 — favourite-longshot bias on EPL h2h.** Create `scripts/analyze_longshot_bias.py`:
   ```python
   #!/usr/bin/env python3
   """Bin settled EPL h2h bets by consensus-prob decile, compute payout per decile.
   Detects favourite-longshot bias per Hegarty & Whelan (2025).

   Output: prints table, exits 0 if bias detected (decile 1 underperforms decile 10
   by ≥ 5pp), exits 1 otherwise. Writes summary to docs/LONGSHOT_BIAS_2026-04.md.
   """
   from pathlib import Path
   import pandas as pd
   import sys

   REPO = Path(__file__).resolve().parent.parent
   BETS = REPO / "logs" / "bets.csv"
   OUT  = REPO / "docs" / "LONGSHOT_BIAS_2026-04.md"

   df = pd.read_csv(BETS)
   # Filter EPL h2h settled rows
   df = df[(df["sport_key"] == "soccer_epl") &
           (df["market"]    == "h2h") &
           (df["result"].notna())]
   if len(df) < 100:
       print(f"Insufficient data: {len(df)} settled EPL h2h bets (need ≥100). Skipping.")
       sys.exit(2)

   # Decile bin by consensus prob; compute payout per row (1+stake*odds if win else -stake/stake = -1)
   df["payout"] = df.apply(
       lambda r: (r["odds"] - 1) if r["result"] == "won"
                 else (0 if r["result"] == "void" else -1),
       axis=1,
   )
   df["decile"] = pd.qcut(df["consensus_prob"], 10, labels=False, duplicates="drop") + 1

   table = df.groupby("decile").agg(
       n=("payout", "count"),
       avg_prob=("consensus_prob", "mean"),
       avg_payout=("payout", "mean"),
       loss_rate=("payout", lambda s: -s.mean()),  # negative payout = loss
   ).round(3)
   print(table.to_markdown())

   # Bias check: decile 1 (longshots) underperforms decile 10 (favourites) by ≥ 5pp
   d1 = table.loc[1, "avg_payout"]
   d10 = table.loc[10, "avg_payout"]
   bias_pp = (d10 - d1) * 100
   verdict = f"Bias = {bias_pp:.1f}pp. " + ("DETECTED" if bias_pp >= 5 else "NOT detected")

   OUT.write_text(f"# Longshot Bias — EPL h2h (2026-04)\n\n{table.to_markdown()}\n\n**{verdict}**\n")
   print(verdict)
   sys.exit(0 if bias_pp >= 5 else 1)
   ```
4. Update `docs/STRATEGY_COMPARISON.md` with the new variants' first-weekend numbers — auto-handled by `compare_strategies.py`. Verify it picks the new variants up by `grep -c '^| [I-N]_' docs/STRATEGY_COMPARISON.md` (should be ≥4).
5. Append a "**Findings — 2026-04 weekend**" subsection to `docs/RESEARCH_NOTES_2026-04.md` §8 with explicit one-line answers to questions §4.3, §4.5, §4.6 (and §4.4 if not done in R.3).

**Acceptance.**
- [ ] `docs/BACKTEST.md` has a `power` column (whole-period method — preliminary; R.5.5c supersedes with per-fold variance).
- [ ] `scripts/analyze_longshot_bias.py` exists, runs cleanly, exits 0 (bias detected) or 1 (not detected) — never crashes.
- [ ] `docs/LONGSHOT_BIAS_2026-04.md` written by the script, committed.
- [ ] `docs/STRATEGY_COMPARISON.md` includes new variants.
- [ ] §4 questions all have explicit yes/no/inconclusive answers in `RESEARCH_NOTES_2026-04.md` §8 "Findings" subsection.

**Verification commands.**
```bash
# Power column exists in backtest doc
grep -c "power" docs/BACKTEST.md  # >= 1

# Longshot script exists and is executable
test -x scripts/analyze_longshot_bias.py || test -f scripts/analyze_longshot_bias.py
python3 scripts/analyze_longshot_bias.py; rc=$?
test "$rc" = 0 -o "$rc" = 1 -o "$rc" = 2 || (echo "Script crashed unexpectedly: rc=$rc"; exit 1)

# Output doc exists (only if rc was 0 or 1, not 2 = insufficient data)
test -f docs/LONGSHOT_BIAS_2026-04.md && echo "OK: longshot output written"

# New variants in comparison doc
grep -cE "^\| [IJLMN]_" docs/STRATEGY_COMPARISON.md  # >= 4

# Findings subsection added
grep -c "Findings — 2026-04 weekend" docs/RESEARCH_NOTES_2026-04.md  # >= 1
```

**Note.** R.5's power-vs-Shin comparison uses the existing whole-period backtest. **It is preliminary.** Graduation decisions in R.6 must wait for R.5.5c's per-fold walk-forward numbers — a single whole-period ROI can be driven by a few good seasons and is not sufficient evidence to flip a default.

---

## Phase R.5.5a — Walk-forward scaffold: primitive + loader (~2h)

**Goal.** Land a self-contained walk-forward backtest primitive built directly on `sklearn.model_selection.TimeSeriesSplit` (already an indirect dependency via CatBoost — no new packages). Verify it with focused tests, but **do not run the full 15-combo backtest yet** — that's R.5.5c.

**Why no third-party walk-forward package.** `georgedouzas/sports-betting` was considered and rejected. The walk-forward primitive is ~30 lines of fold iteration on top of `TimeSeriesSplit` — not enough surface to justify a runtime dependency on a single-maintainer package on the hot path of graduation evidence. Bugs there (or unmaintained drift on Python/sklearn upgrades) would silently corrupt the per-fold ROI numbers we're using to defend default-flips. At our scale (~4,500 EPL matches × 15 `(devig, min_edge)` combos = 75 backtest runs), serial execution is seconds-to-minutes; we don't need joblib parallelism. Owning the loop also makes determinism + edge-case tests trivial to write.

**Why split this off from R.5.5c.** R.5.5a is independent of the weekend data chain (R.4 → R.5) and can be picked up immediately. With the primitive landed, R.5.5c on Mon/Tue collapses to "loop over `(devig, min_edge)` combos, write the report."

**Inputs.** R.0–R.3 done (already on `main`). No internet/install steps needed — `sklearn.model_selection.TimeSeriesSplit` is already pulled in by CatBoost (`python3 -c "from sklearn.model_selection import TimeSeriesSplit"` should already succeed).

**Outputs.**
- `src/betting/walk_forward.py` — new module containing:
  - `load_backtest_data() -> pd.DataFrame` — reads existing `data/raw/*.csv` and returns a single time-ordered DataFrame in the same row-per-match shape `backtest_consensus()` already accepts (no triplet conversion needed).
  - `walk_forward_backtest(matches: pd.DataFrame, *, consensus_method: str, min_edge: float, n_splits: int = 5, **kwargs) -> pd.DataFrame` — splits `matches` by time using `TimeSeriesSplit(n_splits)`, runs `backtest_consensus()` on each *test* fold (the train half is unused — consensus betting has no training step), returns one row per fold with columns: `fold_idx`, `n_bets`, `n_won`, `total_staked`, `total_pnl`, `roi`, `start_date`, `end_date`. `**kwargs` forwarded to `backtest_consensus` (bankroll, kelly_multiplier, min_books).
- `tests/test_walk_forward.py` — primitive tests (5):
  1. Loader returns ≥1000 rows from existing data, time-ordered (`Date` monotonically increasing).
  2. `walk_forward_backtest(..., n_splits=5)` returns exactly 5 rows; `fold_idx` is `[0,1,2,3,4]`.
  3. Folds are temporally ordered: each fold's `end_date` ≤ next fold's `start_date`.
  4. Determinism: two identical calls return DataFrames with identical numeric columns (use `pd.testing.assert_frame_equal`).
  5. Sanity: on a tiny fabricated DataFrame where no fixture clears `min_edge` (e.g. `min_edge=0.99`), every fold reports `n_bets=0` and `roi=0`.

**Out of scope (deferred to R.5.5c).**
- `scripts/walk_forward_backtest.py` (the entry script that loops over the 15 combos).
- The 95% CI aggregation across folds.
- Any changes to `docs/BACKTEST.md`.

**Tasks.**
1. **Sanity-check sklearn.** `python3 -c "from sklearn.model_selection import TimeSeriesSplit; print('OK')"`. Should already work via CatBoost.
2. **Loader.** Implement `load_backtest_data()`. Reads existing `data/raw/*.csv` (currently the football-data.co.uk EPL CSV plus any siblings), concatenates if multiple, sorts by `Date`, returns the DataFrame in the exact shape `backtest_consensus()` already consumes.
3. **Walk-forward function.** Iterate over `TimeSeriesSplit(n_splits).split(matches)`. For each `(train_idx, test_idx)`: ignore `train_idx` (no training step in consensus betting — leave a one-line comment in the code so a future reader doesn't think this is a bug); call `backtest_consensus(matches.iloc[test_idx], min_edge=min_edge, consensus_method=consensus_method, **kwargs)`; capture the returned dict; assemble one DataFrame row per fold. ROI per fold = `total_pnl / total_staked` (recompute from raw counters, don't average per-bet ROIs — variable stake sizes would skew that).
4. **Tests.** Five tests above in `tests/test_walk_forward.py`.

**Pre-flight checks.**
```bash
# sklearn already available
python3 -c "from sklearn.model_selection import TimeSeriesSplit; print('OK')"

# backtest_consensus signature stable
grep -nA 8 "def backtest_consensus" src/betting/consensus.py | head -12

# Module file doesn't exist yet
test ! -f src/betting/walk_forward.py && echo "OK: clean slate"

# Existing tests green
pytest -q
```

**Order of operations.**
1. Confirm sklearn import. Note version in PR body.
2. Implement `load_backtest_data()`. Verify it returns a non-empty time-ordered DataFrame at the REPL.
3. Implement `walk_forward_backtest()` skeleton; verify `n_splits=5` gives 5 rows on a small synthetic input. Use `TimeSeriesSplit` (NOT `KFold` — temporal ordering is the whole point).
4. Wire the real `backtest_consensus` call inside the loop. Forward `**kwargs`.
5. Write the five tests. Run pytest.
6. Commit.

**Acceptance.**
- [ ] `src/betting/walk_forward.py` exists with `load_backtest_data` + `walk_forward_backtest`.
- [ ] `tests/test_walk_forward.py` passes (5 tests).
- [ ] `pytest -q` overall still passes — no regressions.
- [ ] **No new entries in `requirements*.txt`** — sklearn is already an indirect dep via CatBoost.
- [ ] PR body notes the sklearn version observed (so R.5.5c inherits the same constraint).

**Verification commands.**
```bash
# Module imports cleanly
python3 -c "
from src.betting.walk_forward import load_backtest_data, walk_forward_backtest
print('OK: walk_forward imports')
"

# Loader returns time-ordered DataFrame
python3 -c "
import pandas as pd
from src.betting.walk_forward import load_backtest_data
m = load_backtest_data()
assert len(m) >= 1000, f'Expected >=1000 matches, got {len(m)}'
dates = pd.to_datetime(m['Date'])
assert dates.is_monotonic_increasing, 'matches must be time-ordered'
print(f'OK: loader returned {len(m)} matches, time-ordered')
"

# Walk-forward returns 5 folds for n_splits=5 with expected columns
python3 -c "
from src.betting.walk_forward import load_backtest_data, walk_forward_backtest
m = load_backtest_data()
result = walk_forward_backtest(m, consensus_method='shin', min_edge=0.02, n_splits=5)
assert len(result) == 5
required = {'fold_idx', 'n_bets', 'roi', 'start_date', 'end_date'}
missing = required - set(result.columns)
assert not missing, f'Missing columns: {missing}'
print(result.to_string())
"

# No new runtime dep
! grep -i 'sports-betting\|sportsbet' requirements*.txt 2>/dev/null && echo 'OK: no sports-betting dep added'

# Tests pass
pytest -q tests/test_walk_forward.py

# No regressions
pytest -q
```

**Reviewer focus.**
- Folds use `TimeSeriesSplit` (not `KFold` or random splitting) — temporal ordering is the whole point.
- The walk-forward function explicitly ignores `train_idx` with a one-line code comment — consensus has no training step; without the comment a future reader will think this is a bug.
- ROI per fold computed as `total_pnl / total_staked` (recomputed from raw counters), NOT mean-of-per-bet-ROI — the latter would be wrong because stakes are variable (Kelly).
- Determinism: no `random_state` knobs, no nondeterministic dict iteration in the output. The test `pd.testing.assert_frame_equal` over two identical calls catches accidental nondeterminism.
- No new entries in `requirements*.txt`. The point of this phase is to NOT take on a third-party walk-forward dep — verify by greppping.

**Carryover.** R.5.5b adds 16 new league CSVs from football-data.co.uk (Zenodo was investigated first but rejected — see R.5.5b body for rationale); R.5.5c then imports `walk_forward_backtest` and loops it over `(consensus_method, min_edge)` combos to produce the per-fold report + 95% CI aggregation in `docs/BACKTEST.md`.

---

## Phase R.5.5b — Add 16 new leagues from football-data.co.uk (~1h)

**Goal.** Augment `data/raw/` with 16 new league codes from football-data.co.uk that we **don't currently have** (`B1, E2, E3, EC, F2, G1, I2, N1, P1, SC0, SC1, SC2, SC3, SP1, SP2, T1`), increasing the loader's coverage from ~27k matches / 6 leagues to ~55k+ matches / ~22 leagues. Existing 6 leagues (`D1, D2, E0, E1, F1, I1`) remain untouched — no overlap, no dedup, no schema mapping, no loader changes.

**Why now — dual purpose.** (1) **Strategy generalisability.** R.5.5c's per-fold 95% CI tightens with more data, and cross-league diversity tests whether a strategy variant works beyond the 6 leagues we currently scan. (2) **Scanner-expansion feasibility study.** Several of the 16 candidate leagues (notably La Liga / SP1, Serie B / I2, Ligue 2 / F2, Eredivisie / N1) are major football leagues we **don't currently scan in production** — La Liga is explicitly excluded per CLAUDE.md ("too noisy, not enough UK bookmaker coverage yet"), but that call was made before Shin devigging, dispersion filters, and paper portfolios. R.5.5b's data ingestion + R.5.5c's per-league walk-forward results give us the empirical basis to **re-evaluate which excluded leagues should be added to the production scanner** in R.6 — alongside the strategy-variant graduation question.

**Why football-data.co.uk** (and not Zenodo, which the original phase plan targeted). The Zenodo 84k-match dataset (Hegarty & Whelan 2025) was investigated first because it's the canonical broader European dataset cited in the literature, but it ships only **aggregated odds** (`maxhome`, `avghome`, ...) — not per-bookmaker triplets — which our consensus strategy cannot use (it needs cross-book dispersion). Full schema comparison + rationale: `docs/ZENODO_INGEST_NOTES.md`. football-data.co.uk has all 16 candidate leagues with the **identical schema** as our existing 6 (`B365H/D/A`, `BWH/D/A`, `IWH/D/A`, `PSH/D/A`, `WHH/D/A`, `VCH/D/A`, plus `Date`, `Div`, `HomeTeam`, `AwayTeam`, `FTR`). A spike against `SP1_2324`, `G1_2324`, `SC3_2324` confirmed:
- 6/12 books from `BOOKMAKER_GROUPS` present per row.
- Avg ~5.4–5.5 books per match across all three sample leagues.
- 100% of rows clear `min_books=3` even in the smallest sample (Scottish 3rd tier).

**Inputs.** R.5.5a done (loader exists; we don't need to change it). Internet access. Disk space ~50MB.

**Outputs.**
- New CSV files in `data/raw/`: 16 league codes × ~10 seasons each ≈ 130–160 files. Same naming convention as existing data: `{LEAGUE}_{YYMM}.csv` (e.g. `SP1_2324.csv`, `SC3_1819.csv`). **Committed to git** — same precedent as the existing 72 league files in `data/raw/`.
- `scripts/refresh_fdco_data.py` — small bulk-download helper (loops the URL pattern). Useful for keeping data fresh going forward; not required to be elegant.
- `docs/FDCO_INGEST_NOTES.md` — short doc summarising: leagues added, season ranges per league, per-league match counts, the spike result, any seasons dropped (e.g. if a league lacks ≥3 books in older seasons).

**Out of scope.**
- **No changes to `src/betting/walk_forward.py`.** The existing `load_backtest_data()` already globs `data/raw/*.csv` — new CSVs get picked up automatically.
- **No changes to `backtest_consensus()`.** Schema is identical to our existing data.
- No replacement of existing `D1/D2/E0/E1/F1/I1/*.csv` files.
- Only one new test in `tests/test_walk_forward.py` confirming the size/league increase.

**Tasks.**
1. **Bulk download.** Write `scripts/refresh_fdco_data.py` that loops the 16 league codes × season range `1415..2526` (or whichever range each league has) over the URL pattern `https://www.football-data.co.uk/mmz4281/{YYMM}/{LEAGUE}.csv`. Save into `data/raw/{LEAGUE}_{YYMM}.csv`. Skip 404s gracefully (some lower-tier leagues won't have all seasons).
2. **Sanity-check each downloaded file.** For each new file: confirm at least 3 of `BOOKMAKER_GROUPS`'s `*H` columns are present and that ≥80% of rows have `n_books >= 3`. Files failing this check should be deleted and logged in `docs/FDCO_INGEST_NOTES.md`. (Older seasons of small leagues may have insufficient book coverage.)
3. **Run the loader.** Confirm `load_backtest_data()` now returns ≥50k rows and ≥15 unique `Div` values. Verify existing 6 leagues' row counts are unchanged from the pre-R.5.5b baseline.
4. **Sanity test.** Add one test to `tests/test_walk_forward.py`: `test_loader_includes_extra_leagues` — asserts `len(load_backtest_data()) >= 50000` and `m["Div"].nunique() >= 15`.
5. **Ingest notes doc.** Write `docs/FDCO_INGEST_NOTES.md`: leagues added (16 codes + per-league match counts), season ranges, any season-league combos dropped due to <3-books coverage, link to the spike rationale.

**Pre-flight checks.**
```bash
# Baseline for the "existing leagues unchanged" check
python3 -c "
from src.betting.walk_forward import load_backtest_data
m = load_backtest_data()
print(f'Baseline: {len(m)} matches across {m[\"Div\"].nunique()} divisions')
print('Per-existing-league counts:')
for div in sorted(m['Div'].unique()):
    print(f'  {div}: {(m[\"Div\"] == div).sum()}')
"

# Confirm existing leagues file naming convention
ls data/raw/E0_*.csv | head -3
```

**Order of operations.**
1. Capture the baseline output (above) — paste into `docs/FDCO_INGEST_NOTES.md` as the "before" state.
2. Write `scripts/refresh_fdco_data.py`. Test it on one league first (e.g. SP1) — confirm files land in `data/raw/` and load cleanly.
3. Run the full bulk-download loop.
4. Run the per-file sanity check; delete files failing the ≥3 books bar; log deletions in the ingest notes.
5. Run `load_backtest_data()` and confirm the totals match the acceptance bars.
6. Add the sanity test. Run `pytest -q`.
7. Capture the "after" state in the ingest notes (per-league counts).
8. Commit the new CSVs + helper script + notes + test in one commit.

**Acceptance.**
- [ ] 16 new league codes present in `data/raw/` (verify with `ls data/raw/ | sed 's/_.*//' | sort -u`).
- [ ] `load_backtest_data()` returns ≥ 50k rows (was ~27k) and ≥ 15 unique `Div` values (was 6).
- [ ] **Existing 6 leagues' match counts unchanged** — `D1/D2/E0/E1/F1/I1` row counts identical to pre-R.5.5b baseline.
- [ ] All R.5.5a tests pass; full `pytest -q` passes (now 127 tests).
- [ ] `docs/FDCO_INGEST_NOTES.md` exists with: leagues + counts + season ranges, any season-league combos dropped and why, and a one-line link back to `docs/ZENODO_INGEST_NOTES.md` for the rejected-Zenodo rationale.
- [ ] No new entries in `requirements*.txt`.

**Verification commands.**
```bash
# 16 new leagues present
NEW_LEAGUES="B1 E2 E3 EC F2 G1 I2 N1 P1 SC0 SC1 SC2 SC3 SP1 SP2 T1"
for L in $NEW_LEAGUES; do
  count=$(ls data/raw/${L}_*.csv 2>/dev/null | wc -l)
  echo "${L}: ${count} season files"
done
# Each line should be ≥1.

# Match count + division count meet bar
python3 -c "
from src.betting.walk_forward import load_backtest_data
m = load_backtest_data()
print(f'Total: {len(m)} matches; {m[\"Div\"].nunique()} divisions')
assert len(m) >= 50000, f'Expected >=50k, got {len(m)}'
assert m['Div'].nunique() >= 15, f'Expected >=15 divisions, got {m[\"Div\"].nunique()}'
print('OK')
"

# Existing leagues unchanged from baseline (compare against pre-flight output)
python3 -c "
from src.betting.walk_forward import load_backtest_data
m = load_backtest_data()
for div in ['D1', 'D2', 'E0', 'E1', 'F1', 'I1']:
    print(f'{div}: {(m[\"Div\"] == div).sum()}')
"

# Walk-forward end-to-end on the now-broader dataset
python3 -c "
from src.betting.walk_forward import load_backtest_data, walk_forward_backtest
m = load_backtest_data()
result = walk_forward_backtest(m, consensus_method='shin', min_edge=0.02, n_splits=5)
print(result.to_string())
assert len(result) == 5
print('OK: 5-fold walk-forward succeeds on combined dataset')
"

# Tests pass
pytest -q tests/test_walk_forward.py
pytest -q

# Ingest notes exist
test -f docs/FDCO_INGEST_NOTES.md && grep -q "leagues added" docs/FDCO_INGEST_NOTES.md && echo OK

# Helper script exists and is runnable
test -f scripts/refresh_fdco_data.py && python3 -c "import scripts.refresh_fdco_data" 2>/dev/null && echo "OK: helper imports"
```

**Reviewer focus.**
- **Existing leagues' counts unchanged.** The `D1/D2/E0/E1/F1/I1` row counts must match the baseline exactly. Any drift means we accidentally re-downloaded an existing league or polluted an existing file.
- **Per-file ≥3 books gate.** Older seasons of smaller leagues (e.g. SC3 in 2014/15) may not have full bookmaker coverage. The implementer should drop those files, not silently include them. Verify by sampling 2–3 of the smallest leagues' oldest files.
- **Schema sanity.** Open one new file and one existing file side by side. Column lists should look the same — no `Data.Date`-style prefixes (that would mean someone accidentally pulled Zenodo data).
- **Helper script is idempotent.** Re-running `scripts/refresh_fdco_data.py` should be safe (it skips existing files OR re-downloads them; either is fine, but it must not error out).

**Decision deferred to phase execution.** If a specific league's recent seasons lack ≥3 books per row (i.e. football-data.co.uk's lower-tier coverage degraded relative to the 2023-24 spike), drop just those season-league combos. Document each drop in `docs/FDCO_INGEST_NOTES.md`. If more than 4 of the 16 new leagues fail this check entirely, raise a concern in the PR — that would invalidate the spike's "100% pass" finding and warrant a deeper coverage audit before R.5.5c uses the data.

**Carryover.** R.5.5c's walk-forward output now covers ~22 leagues × 5 folds. R.6's graduation criteria interpret per-fold consistency as **cross-league consistency** — a stricter bar than EPL-only would have been.

---

## Phase R.5.5c — Walk-forward run + per-fold report (~1–1.5h)

**Goal.** Use the R.5.5a scaffold to run a walk-forward backtest with `TimeSeriesSplit(5)` over **30 combos**: `consensus_method ∈ {raw, shin, power}` × `consensus_mode ∈ {mean, pinnacle_only}` × `min_edge ∈ {0.01, 0.02, 0.03, 0.04, 0.05}`. Output **three views** to `docs/BACKTEST.md`: (i) all 22 leagues aggregated, (ii) production-6 leagues only (`{D1, D2, E0, E1, F1, I1}`) — the subset our scanner currently runs on, the right basis for variant graduations, (iii) per-league walk-forward ROI for each of the 16 currently-excluded leagues — the basis for R.6's scanner-expansion decision. Flag any combo whose CI crosses zero. The `consensus_mode` axis directly tests **"does the consensus-of-many-books complexity earn its keep?"**; the per-league view answers **"which currently-excluded leagues should we add to the production scanner?"**

**Why now.** Whole-period ROI hides per-season variance; walk-forward reveals consistency. The two-view structure separates two distinct decisions R.6 has to make: which **strategy variant** to graduate (decided on production-6 walk-forward to match what we'd actually act on), and which **leagues** to add to the production scanner (decided per-league on the 16 currently-excluded ones). Without the production-6 view, variant-graduation evidence would be polluted by leagues we never bet on; without the per-league view, league-graduation has no empirical basis.

**Inputs.** R.5 done (whole-period `power` column already in `docs/BACKTEST.md`). R.5.5a done (`src/betting/walk_forward.py` merged on `main`). R.5.5b done (16 new leagues from football-data.co.uk added to `data/raw/`; loader returns ≥50k matches across ≥15 leagues).

**Outputs.**
- Extension of `src/betting/consensus.py`'s three public functions (`compute_consensus`, `find_consensus_bets`, `backtest_consensus`) to accept a new `consensus_mode: str = "mean"` parameter with values `"mean"` (current behaviour, default) and `"pinnacle_only"`.
- `scripts/walk_forward_backtest.py` — entry script: loads data via `load_backtest_data()`, loops `walk_forward_backtest()` over the **30 combos**, aggregates per-fold ROI + 95% CI per combo, writes results.
- `docs/BACKTEST.md` — new "Walk-forward (5 folds)" section appended; legacy whole-period tables retained.
- `tests/test_consensus_pinnacle_only.py` (or extension to existing test file) covering the new `consensus_mode="pinnacle_only"` branch.

**Tasks.**
1. **Extend `compute_consensus(matches, consensus_method, consensus_mode="mean")`.** When `consensus_mode="pinnacle_only"`: for each row, use only Pinnacle's columns (`PSH/PSD/PSA`) — devig with the requested `consensus_method`, return `consensus_prob_{H,D,A}` from Pinnacle's row directly. If Pinnacle's columns are missing for a row, set `consensus_prob_*` to NaN and `n_books_used = 0`. Otherwise `n_books_used = 1`.
2. **Extend `find_consensus_bets(..., consensus_mode="mean")`.** Forward `consensus_mode` to `compute_consensus`. When `consensus_mode="pinnacle_only"`: **skip Pinnacle when iterating bet candidates** — Pinnacle is the anchor, not a book to bet at. Also: relax the `min_books` check (Pinnacle-only inherently has `n_books_used = 1`, so the default `min_books=3` would reject everything; either set `min_books=1` automatically when mode is pinnacle_only, OR document that the caller must override).
3. **Extend `backtest_consensus(..., consensus_mode="mean")`.** Forward to `find_consensus_bets`. No other changes — same returned dict shape.
4. **Tests** in `tests/test_consensus_pinnacle_only.py`:
   - On a fabricated 1-row fixture with `PSH=2.50, PSD=3.30, PSA=2.90` and a UK book at `B365H=2.80`, `consensus_mode="pinnacle_only"` flags a HOME bet at B365 (consensus prob from Pinnacle = ~0.40, B365 implied ~0.36, edge ≈ 4%).
   - Same fixture with `B365H=2.50` — no flag (no edge).
   - Fixture missing all `PS*` columns → `n_books_used=0`, no bet flagged.
   - Smoke: `consensus_mode="pinnacle_only"` does NOT flag a bet at Pinnacle itself even when its own price is theoretically beatable.
5. **Write `scripts/walk_forward_backtest.py`.** Three nested loops:
   - **View 1 — all 22 leagues**: loop the 30 combos on the full dataset.
   - **View 2 — production-6 only**: loop the 30 combos on `matches[matches["Div"].isin({"D1","D2","E0","E1","F1","I1"})]`. **This is the variant-graduation evidence basis** — what R.6 reads to decide which strategy variant flips the default.
   - **View 3 — per-league for the 16 candidates**: for each currently-excluded league code (`B1, E2, E3, EC, F2, G1, I2, N1, P1, SC0, SC1, SC2, SC3, SP1, SP2, T1`), run the **leading combo only** (best (`devig`, `mode`, `min_edge`) from view 2) on `matches[matches["Div"] == league]`. **This is the league-graduation evidence basis** — what R.6 reads to decide which excluded leagues to add to production.
   - For each: call `walk_forward_backtest(filtered_matches, consensus_method=..., consensus_mode=..., min_edge=..., n_splits=5)` (R.5.5a's primitive forwards kwargs already); capture the 5-row per-fold DataFrame.
6. **Aggregate.** For each combo (and each per-league run): mean ROI ± 95% CI (t-distribution, n=5: `scipy.stats.t.ppf(0.975, 4) ≈ 2.776`).
7. **Append walk-forward section to `docs/BACKTEST.md`.** Three sub-sections:
   - **Walk-forward — all 22 leagues**: two parallel 3×5 tables (one per `consensus_mode`), each cell `ROI% ± CI / n_bets`.
   - **Walk-forward — production-6 only**: same shape; this is the **graduation-relevant** view, called out clearly.
   - **Walk-forward — excluded-league candidates**: one row per excluded league code, columns `n_matches, n_bets, mean_roi, ci_low, ci_high, ci_crosses_zero`. Sort by `ci_low` descending — leagues at the top are the strongest expansion candidates.
   - Plus head-to-head: per `(devig × edge)` cell, which `consensus_mode` wins on production-6 aggregate ROI, and whether the difference's CI crosses zero.
8. **Interpretation notes** (two explicit yes/no answers in the doc):
   - **Strategy variant**: "Does `pinnacle_only` match or beat `mean` at any `(devig, edge)` combo on production-6 with non-overlapping CI?" — R.6 reads this to decide on `D_pinnacle_only` graduation.
   - **Excluded leagues**: "Which of the 16 currently-excluded leagues show positive walk-forward ROI on the leading combo with CI not crossing zero?" — R.6 reads this to decide which leagues to promote into the production scanner.

**Acceptance.**
- [ ] `compute_consensus` / `find_consensus_bets` / `backtest_consensus` accept `consensus_mode` kwarg with default `"mean"` (preserves current behaviour for all existing callers).
- [ ] `tests/test_consensus_pinnacle_only.py` passes (4 tests).
- [ ] `scripts/walk_forward_backtest.py` runs end-to-end and produces all three views (all-22, production-6, per-league × 16).
- [ ] `docs/BACKTEST.md` walk-forward section exists with the three sub-sections explicitly named: "all 22 leagues", "production-6 only", "excluded-league candidates".
- [ ] Per-fold variance reported. Aggregate mean ± 95% CI annotated.
- [ ] CI-crosses-zero combos explicitly flagged.
- [ ] Two explicit yes/no answers present: (a) does `pinnacle_only` beat `mean` on production-6? (b) which excluded leagues qualify for production-scanner promotion?
- [ ] No regressions in `pytest -q` — the default-arg change must not break any existing test.

**Verification commands.**
```bash
# Backtest script runs end-to-end
python3 scripts/walk_forward_backtest.py 2>&1 | tail -20

# BACKTEST.md has walk-forward section
grep -c "Walk-forward" docs/BACKTEST.md  # >= 1
grep -cE "(95% CI|confidence interval|fold)" docs/BACKTEST.md  # >= 1

# Three views present
grep -cE "all 22 leagues|production-6|excluded-league candidates" docs/BACKTEST.md  # >= 3

# Both consensus modes present
grep -A100 "Walk-forward" docs/BACKTEST.md | grep -cE "(mean|pinnacle_only)"  # >= 2

# All 3 consensus methods present
grep -A100 "Walk-forward" docs/BACKTEST.md | grep -cE "(raw|shin|power)"  # >= 3

# Both yes/no answers present
grep -iE "(does .* earn|complexity|pinnacle.*beat|mean.*beat)" docs/BACKTEST.md | head -3
grep -iE "(qualify for|excluded.*league|league.*promot|expand.*scanner)" docs/BACKTEST.md | head -3

# Backwards compatibility — existing callers without consensus_mode still work
python3 -c "
from src.betting.consensus import backtest_consensus
import pandas as pd
# Smoke: existing default-arg call still works (no consensus_mode kwarg)
help(backtest_consensus)  # should show consensus_mode in signature with default 'mean'
"

# Tests pass (including the new pinnacle_only suite)
pytest -q tests/test_consensus_pinnacle_only.py
pytest -q  # no regressions
```

**Reviewer focus.**
- **CI uses the right t-quantile** for n=5 (`scipy.stats.t.ppf(0.975, 4) ≈ 2.776`), NOT 1.96 (normal-approximation; too narrow for n=5).
- **Pinnacle excluded from bet candidates in `pinnacle_only` mode.** Re-read the iteration in `find_consensus_bets` — confirm `if consensus_mode == "pinnacle_only" and book == "PS": continue`. Without this, the strategy could "find an edge" against itself.
- **`min_books` handling under `pinnacle_only`.** The current default `min_books=3` would reject every Pinnacle-only candidate (since `n_books_used = 1`). The implementer must either auto-override to 1 inside the function or update the script's call to pass `min_books=1` for that mode.
- **CI-crosses-zero combos correctly flagged** and prominently called out — those cannot defend an R.6 default-flip.
- **Production-6 filter uses the canonical set** `{"D1","D2","E0","E1","F1","I1"}`. This is what the live scanner currently runs on per CLAUDE.md "Sports scanned"; using a different set would mismatch graduation evidence vs. production reality.
- **Per-league CI is honest with low n.** Some excluded leagues (smaller ones) may produce <50 bets in 5 folds. The CI for those will be very wide. Don't use `ci_crosses_zero=False` alone as a graduation gate without also checking `n_bets >= 50` per league.
- **Default-arg change is backwards compatible.** Existing callers of all 3 functions in `consensus.py` (e.g. `main.py`, R.5's whole-period analysis) must continue to work without changes — the `consensus_mode="mean"` default preserves current behaviour.
- **Script is a thin loop**, not a re-implementation of walk-forward logic — it should call `walk_forward_backtest()` from R.5.5a.

**Carryover.** Once this lands, `compare_strategies.py` and any future model-overhaul work (Phase 7) inherits both the walk-forward primitive and the `consensus_mode` axis. R.6 has direct walk-forward evidence on `D_pinnacle_only` graduation — no longer dependent solely on slow-to-accumulate CLV data.

---

## Phase R.6 — Graduate winning variants AND winning leagues → scanner defaults (~2h, conditional)

**Goal.** Two parallel decisions, both based on R.5.5c's walk-forward evidence:
1. **Variant graduation** — promote a strategy variant from shadow portfolio to scanner default if it beats production on the **production-6 walk-forward** view.
2. **League graduation** — promote one or more currently-excluded leagues into the production scanner if R.5.5c's per-league walk-forward shows positive ROI with non-overlapping CI.

These are independent decisions and can land in the same PR or be split.

**Bar for variant graduation.**
- Variant has shadow data ≥ 50 settled bets across the existing portfolio (won't be reached this weekend — most will need 4–6 weeks). For *immediate* graduation candidates from R.5/R.5.5c, we apply a softer bar:
  - **M_min_prob_15**: graduates immediately if §4.6 shows decile-1 underperformance ≥ 5pp on existing settled history. Bias is empirical fact, not a strategy hypothesis.
  - **I_power_devig**: graduates only if R.5.5c's **production-6 walk-forward** shows `power` ≥ `shin` ROI at 2–3% edges in **≥ 4 of 5 folds** AND the aggregate 95% CI does not cross Shin's mean. Whole-period dominance from R.5 alone is **insufficient**.
  - **D_pinnacle_only**: graduates only if R.5.5c's **production-6 walk-forward** head-to-head shows `pinnacle_only` ≥ `mean` ROI at the leading `(devig, edge)` combo with non-overlapping CI. If the difference is statistically a wash, do NOT graduate — the production complexity earns its keep on the "do no harm" bar.
  - **J_sharp_weighted**, **L_quarter_kelly**, **N_competitive_only**: stay in shadow until ≥ 50 settled bets *and* their inclusion in the walk-forward backtest (follow-up PR) shows positive aggregate ROI.

**Bar for league graduation.**
- Per excluded league (R.5.5c's view 3): graduate if **all three** hold:
  1. **Walk-forward CI does not cross zero** at the leading `(devig, mode, edge)` combo.
  2. `n_bets >= 50` over the 5 folds (otherwise CI is vacuously narrow due to sample-size limits).
  3. Avg `n_books_used >= 3` per match in the most-recent 2 seasons (confirms book coverage holds today, not just historically).
- "La Liga is excluded" was specifically called out in CLAUDE.md as "too noisy, not enough UK bookmaker coverage yet." If SP1 graduates, the commit message must explicitly retire that justification with the new evidence.

**Tasks (conditional on R.5.5c results).**
1. **Variant graduation** (if any qualify):
   - If M graduates: add `MIN_CONSENSUS_PROB = 0.15` to `scripts/scan_odds.py` and apply pre-flag.
   - If I graduates: change default `devig` from `shin` to `power` (or add a runtime flag).
   - If D graduates: change `consensus_mode` default; remove `D_pinnacle_only` from STRATEGIES (now production) and consider re-adding `A_consensus_legacy` as a shadow check.
2. **League graduation** (if any qualify):
   - Add the league code to the production scanner's league-list config in `scripts/scan_odds.py` (look for existing league configuration; copy a 6th-league entry as the template).
   - Set `min_books` per the new league per its R.5.5c sample (most likely 20, matching existing leagues).
   - Add a row to CLAUDE.md "Sports scanned" table.
   - **Important**: any league that graduates also flips on for paper-portfolio shadow runs of variants I/J/L/M/N/O/P automatically — verify the strategies.py `markets` filter doesn't exclude the new league code.
3. **Documentation**:
   - Update `CLAUDE.md` "How the scanner works" section.
   - Update `docs/PLAN.md` Phase 1 table.
   - If La Liga (SP1) graduated: explicitly remove the "La Liga excluded — too noisy, not enough UK bookmaker coverage yet" line from CLAUDE.md and replace with a one-line note pointing at the R.5.5c evidence.

**Acceptance.**
- [ ] Each graduating **variant** has a one-paragraph promotion note in the PR body citing production-6 walk-forward evidence (fold counts, CI bounds).
- [ ] Each graduating **league** has a one-paragraph promotion note citing per-league walk-forward evidence (CI bounds, n_bets, recent-seasons book coverage).
- [ ] No graduation happens silently — even immediate ones get explicit sign-off in commit message.
- [ ] CLAUDE.md "How the scanner works" + "Sports scanned" sections updated.
- [ ] If La Liga graduates: the "too noisy" justification is explicitly retired in CLAUDE.md.
- [ ] If nothing graduates: explicit `## No graduation this week` section in PR body explaining why (citing CI breadth and/or n_bets shortfalls from R.5.5c).

**Verification commands.**
```bash
# If M graduated, scanner has the constant
grep -c "MIN_CONSENSUS_PROB\s*=\s*0\.15" scripts/scan_odds.py  # 0 if not graduated, >=1 if graduated

# If I graduated, default devig changed
grep -E "devig.*=.*[\"']power[\"']" scripts/scan_odds.py | head -3

# CLAUDE.md updated to match
grep -c "min consensus prob\|MIN_CONSENSUS_PROB\|power devig\|power de-vig" CLAUDE.md

# No silent regressions
pytest -q
```

**If nothing graduates this week**: that's fine. The scanner is unchanged; we wait for more shadow data. Phase R.6 just rolls forward to the next sprint.

---

## Phase R.7 — bets.csv schema: provenance columns (~1h)

**Status: ✅ done** — landed in commit `a980efc` (2026-04). Both columns populated on new scans; backfill script idempotent on existing rows.

**Goal.** Add `devig_method` and `weight_scheme` columns to `logs/bets.csv` so future CLV analyses can attribute results by method.

**Inputs.** R.6 done (or skipped — independent).

**Tasks.**
1. Modify the bets-row dict in `scripts/scan_odds.py` to include `devig_method` (e.g. "shin" or "power") and `weight_scheme` ("uniform" or "sharp_v1").
2. Update CSV header writer to include the new columns.
3. Backfill existing rows: assume `shin` + `uniform` (the only mode pre-2026-04).
4. Update `closing_line.py` to preserve these columns when re-writing.
5. Update dashboard (`app.py`) — show breakdown of CLV by `devig_method` if both methods have ≥ 20 bets.

**Acceptance.**
- [ ] New scans write rows with both new columns populated.
- [ ] `pytest -q` passes — column-count regression caught.
- [ ] Dashboard renders without breakage; new breakdown only appears when threshold met.
- [ ] Backfill is idempotent: re-running it produces no diff on a backfilled file.

**Verification commands.**
```bash
# Both columns present in CSV header
head -1 logs/bets.csv | tr ',' '\n' | grep -cE "^(devig_method|weight_scheme)$"  # 2

# All non-header rows have both columns populated (no empty values)
python3 -c "
import csv
from pathlib import Path
with open('logs/bets.csv') as f:
    reader = csv.DictReader(f)
    rows = list(reader)
empty = [i for i, r in enumerate(rows) if not r.get('devig_method') or not r.get('weight_scheme')]
assert not empty, f'Empty values in rows: {empty[:5]}'
print(f'OK: all {len(rows)} rows have devig_method + weight_scheme populated')
"

# Backfill idempotent — running it twice yields no change
python3 scripts/backfill_provenance.py  # or wherever the backfill lives
md5_1=$(md5sum logs/bets.csv | cut -d' ' -f1)
python3 scripts/backfill_provenance.py
md5_2=$(md5sum logs/bets.csv | cut -d' ' -f1)
test "$md5_1" = "$md5_2" && echo "OK: backfill is idempotent"

# Dashboard still loads
python3 -c "
import importlib
m = importlib.import_module('app')
print('OK: app.py imports without error')
"

# Tests pass
pytest -q
```

**Reviewer focus.** Schema migration of `bets.csv` is the most fragile part of the system. Backfill must be idempotent (safe to re-run). The Verification block above includes an explicit md5 check for idempotency — the reviewer must run it.

---

## Phase R.8 — Draw-bias variant K (~2–3h)

**Status: ✅ done** — `scripts/refresh_xg.py` writes `logs/team_xg.json` (last 5 matches/team from Understat); weekly Mon 06:00 cron installed; `K_draw_bias` variant shipped in `src/betting/strategies.py` with `draw_odds_band=(3.20, 3.60)` + low-xG gate.

**Goal.** Add `K_draw_bias` variant restricting draw flags to fixtures meeting Predictology's filter (low-xG matchups, draw odds ∈ [3.20, 3.60]).

**Inputs.** R.1 done. xG data accessible at scan time.

**Tasks.**
1. Investigate xG runtime ingestion. Options:
   - Cache from `data/raw/understat/` keyed by team — fast but stale (last season's avg).
   - On-the-fly fetch from Understat — slow, fragile.
   - Pre-computed weekly xG snapshot in `logs/team_xg.json` updated by a new bi-weekly cron.
2. Recommended path: option (c). New `scripts/refresh_xg.py` that pulls last 5 matches per team, writes `logs/team_xg.json`. Cron weekly (Mondays 06:00).
3. Extend `StrategyConfig`:
   ```python
   draw_odds_band: tuple[float, float] | None = None  # K: e.g. (3.20, 3.60)
   require_low_xg: bool = False                       # K: both teams in bottom xG quartile
   ```
4. Add filter in `_filter_candidate` that applies only to draw bets.
5. Append `K_draw_bias` to STRATEGIES.
6. Tests with synthetic high-xG and low-xG fixtures.

**Acceptance.**
- [ ] `logs/team_xg.json` populated; tests verify reading from it.
- [ ] `K_draw_bias` produces only draw bets, only on filtered fixtures.
- [ ] `pytest` passes.

**Reviewer focus.** xG pipeline robustness — what happens when Understat is down on the cron run? Stale-file fallback acceptable.

---

## Phase R.9 — Asian Handicap feasibility probe (~1.5h)

**Goal.** Determine whether The Odds API surfaces Asian Handicap markets in our regions and pricing tier. Write a feasibility note — **no implementation**.

**Inputs.** None.

**Tasks.**
1. Check The Odds API docs for AH market support (`market_key=spreads` or similar). Note whether `regions=uk,eu` carries it.
2. If yes: pick one EPL fixture, fetch AH odds, save sample JSON to `docs/papers/sample_ah_response.json`.
3. If no: note required tier upgrade or alternative source (oddsportal, betbrain — but both 403'd us in this research pass; Pinnacle directly via brokers like asianbookie.com is another route).
4. Write `docs/AH_FEASIBILITY.md`:
   - Source(s) found.
   - Cost (API tier, request budget impact).
   - Implementation sketch (where to add `fetch_ah_odds()` in `scan_odds.py`).
   - Hegarty & Whelan probability conversion plan: new module `src/betting/asian_handicap.py` with closed-form formulas (Eqs 6–28 from the paper).
   - Estimated effort to ship a `O_asian_handicap_anchor` variant.

**Acceptance.**
- [ ] `docs/AH_FEASIBILITY.md` exists, all sections answered (yes/no, cost, sketch, effort).
- [ ] No code changes in this phase.

---

## Phase R.11 — Eval-window provenance (~1h)

**Goal.** Add per-row provenance to paper CSVs so strategy evaluation can be filtered to "current config window" and code-change noise is excluded from CLV stats. Closes the structural gap surfaced during the 2026-05-01 evaluation review.

**Inputs.** Two sources of row-to-row variance currently pollute paper CSVs:
1. Code/strategy changes (noise) — what we want to filter out.
2. Market state changes (signal) — what we actually want to measure.

R.7 added `devig_method` + `weight_scheme` per row — partial provenance only (two config dimensions). Variant *thresholds* (edge cutoff, dispersion cap, min books, etc.) are NOT versioned per row, so tweaking `MAX_DISPERSION` in `B_strict` makes every prior row falsely look like it came from the new threshold. `compare_strategies.py` pools every row regardless of generation time.

**Tasks.**
1. `src/betting/strategies.py`:
   - Add `StrategyConfig.config_hash() -> str`: deterministic 12-char SHA-256 of all behavior fields (every field except `name`/`label`/`description`). Uses `json.dumps(asdict(self), sort_keys=True, default=str)` for stable serialization.
2. `scripts/scan_odds.py`:
   - Add `_git_sha() -> str` helper (cached for process lifetime; graceful empty fallback if git unavailable).
   - Add `code_sha` and `strategy_config_hash` to `_PAPER_FIELDNAMES` (between `weight_scheme` and `stake`).
   - Populate both columns in `_append_paper_csv` row-build.
   - Add `_ensure_paper_schema(log_file)`: detects header mismatch on existing CSVs; rewrites with new header + null-padded existing rows. Idempotent. Logs `[paper:schema] migrating ...` on first call.
3. `scripts/compare_strategies.py`:
   - Add `_filter_to_current_window(rows)`: groups by `strategy_config_hash`, keeps only rows matching the most-recent hash (by `scanned_at`). Empty hash (pre-R.11 rows) collapse into one "pre-R.11" window.
   - Add `--all-history` CLI flag (default off): when set, skip the filter.
   - Always print "X bets in current config window, Y total in CSV" per variant in the report header — transparency without forcing a flag for the common-case eval.
4. Tests:
   - `tests/test_strategies.py`: same config → same hash; tweaking any single field → different hash; `name`/`label`/`description` excluded from hash.
   - `tests/test_compare_strategies.py`: filter picks most recent hash; mixed pre-R.11 + post-R.11 rows handled; `--all-history` includes everything.

**Acceptance.**
- [ ] Two consecutive scans without code/config change produce rows with identical `(code_sha, strategy_config_hash)`.
- [ ] Tweaking any `StrategyConfig` field (e.g., `B_strict.min_edge`) changes only that variant's hash; others unchanged.
- [ ] `compare_strategies.py` default output reports `current window: X bets, all history: Y bets` per variant.
- [ ] `compare_strategies.py --all-history` shows pooled stats (matches pre-R.11 behavior).
- [ ] Existing paper CSVs auto-migrate to new schema on next scan; old rows retain empty `code_sha`/`strategy_config_hash`.
- [ ] Full pytest suite passes (no regressions).

**Reviewer focus.**
- **Hash determinism.** `json.dumps(..., sort_keys=True)` guarantees key order; verify `default=str` handles tuple `markets` field consistently across Python versions. Hash is short SHA (12 char) — collision-safe within ~16 variants.
- **Schema migration idempotency.** `_ensure_paper_schema` running twice on the same file must produce byte-identical output. Lock-file pattern (`fcntl.LOCK_EX`) needed because cron may overlap.
- **Empty-hash handling.** Pre-R.11 rows have `""` hash. Filter must NOT crash on them; should treat them as a separate epoch from any non-empty hash.
- **Production scanner unaffected.** `bets.csv` (production) write path is not touched by this phase — only paper CSVs. R.7's provenance columns on `bets.csv` already cover production needs.

**Verification.**
```bash
# 1. Hash determinism
pytest -q tests/test_strategies.py::test_config_hash_deterministic
pytest -q tests/test_strategies.py::test_config_hash_excludes_identity_fields

# 2. End-to-end smoke (uses dev key, doesn't burn prod quota)
export $(cat .env.dev) && python3 scripts/scan_odds.py --sports football 2>&1 | tail -5
head -1 logs/paper/A_production.csv | tr ',' '\n' | grep -E 'code_sha|strategy_config_hash'  # both present

# 3. Two scans → identical hash on rows with same (kickoff, home, away, side, book)
export $(cat .env.dev) && python3 scripts/scan_odds.py --sports football
export $(cat .env.dev) && python3 scripts/scan_odds.py --sports football
awk -F, 'NR>1 {print $(NF-3), $(NF-4)}' logs/paper/A_production.csv | sort -u | wc -l
# expect: small number (one unique pair per code/config window)

# 4. Comparison filter
python3 scripts/compare_strategies.py 2>&1 | head -20  # current-window default
python3 scripts/compare_strategies.py --all-history 2>&1 | head -20  # all-history flag

# 5. Full test suite
pytest -q
```

**Note for this weekend's eval.** Pre-R.11 paper rows (everything in WSL `logs/paper/*.csv`) will have empty `strategy_config_hash` after the schema migration. They form their own "pre-R.11" window. The `current window: X / all history: Y` line in the comparison report makes this transparent so test-data inclusion is explicit, not silent.

---

## Phase R.10 — AH probability conversion module (blocked on CLV confirmation)

**Goal.** Implement Hegarty & Whelan's closed-form AH→prob conversion. Adds AH-derived prob as a *second anchor* alongside Pinnacle h2h.

**Inputs.** R.9 confirmed AH is fetchable via The Odds API `spreads` market key, but only Pinnacle (in `eu` region) is usable as an anchor; UK books are too thin (`docs/AH_FEASIBILITY.md`). Adds ~90–120 calls/month → forces $79/month Starter tier upgrade.

**Status.** **Blocked on CLV confirmation**, not merely deferred. Per `RESEARCH_NOTES_2026-04.md` §6, operational cost is the binding constraint on this system, not anchor quality. Spending $948/year + ~9h to refine a signal whose base utility we haven't yet measured is the wrong priority order.

**Explicit gate to unblock** (all three must hold):
1. R.6 has graduated ≥1 shadow variant to default with walk-forward evidence.
2. Avg CLV across graduated variants is positive over ≥50 settled bets.
3. Either (a) R.5.5c surfaces ≥1 league with h2h Pinnacle book count consistently <5, OR (b) `J_sharp_weighted` shadow shows materially better CLV than `A_production`.

If gate fails: AH won't rescue an unconfirmed edge; reallocate effort to restriction-resilience (RESEARCH_NOTES §3.1, 3.3, 3.4).

**Tasks.** (Detailed plan to be written when the gate clears, not before.)

---

## Open carryovers (not phased yet)

- **SBK not in Odds API uk region** (R.3, checked 2026-04-30): `regions=uk&markets=h2h` returns 20 UK books; SBK key is absent. Note: `unibet_uk` IS present in the API but not currently in `UK_LICENSED_BOOKS` — low-priority addition for a future PR once we confirm it's properly licensed and odds quality is acceptable.
- **Restriction-detection logging** (RESEARCH_NOTES §3.3): per-bookie max-stake limits hit on placement, manual log via dashboard. Lightweight but needs UI work.
- **Mug-bet camouflage cron** (RESEARCH_NOTES §3.4): scheduled small bets to mask account profile. Only relevant once we hit a real restriction.
- **Migrate `compare_strategies.py` to walk-forward** (follow-up to R.5.5c): once `walk_forward_backtest()` lands, port shadow-portfolio comparison to call it directly for fold-aware CLV variance reporting.
- ~~**Zenodo 84k-match dataset**~~ — investigated under R.5.5b, rejected: schema ships only aggregated odds (`maxhome`/`avghome`), no per-bookmaker triplets — incompatible with our consensus strategy. Full rationale in `docs/ZENODO_INGEST_NOTES.md`. R.5.5b pivoted to football-data.co.uk for the same 16 new leagues.
- **`pybettor` evaluation** (RESEARCH_NOTES §9.4): 30-min skim of `ian-shepherd/pybettor` to determine if any utilities replace what we currently maintain. Decision: dep or reference.
- **ELO prior variant `Q_elo_prior`** (RESEARCH_NOTES §9.3): WagerBrain's `elo_prob(elo_diff)` is a cheap model-agreement signal. Could substitute for CatBoost on leagues we don't have CatBoost coverage for (Championship, Bundesliga 2, NBA, tennis). Phase 7-adjacent.
- **Asian Handicap as second anchor** (RESEARCH_NOTES §7.1, R.9 done 2026-04-30): R.9 confirmed AH is fetchable but only Pinnacle (`eu` region) is usable; pushes API over free-tier budget. R.10 (implementation) is now **blocked on CLV confirmation** — see Phase R.10 above for the explicit unblock gate. Not a free carryover; it's a $948/year + ~9h investment that needs base-edge validation first.
- **Dashboard pagination/filter for variants** (RESEARCH_NOTES §9 implications): with R.1 + R.1.5 + R.1.6 + R.2 we go from 8 to 13–14 strategy variants. The current dashboard's "Three bet sections" list will get crowded. Add a strategy filter dropdown.

---

## Risks / things to watch

- **R.1 Kelly plumbing.** If `kelly_fraction` is read from a global constant rather than `StrategyConfig`, variant L becomes a no-op silently. Verify by inspecting `logs/paper/L_quarter_kelly.csv` stakes — should be exactly 0.8× of `A_production` for matched rows.
- **R.1.5 — silently wrong baseline.** If `raw_consensus=True` accidentally still calls Shin somewhere, the "paper-faithful" comparison would show our system being worse than itself. Smoke-test by computing one fixture by hand and comparing. The α=0.05 multiplicative threshold is **fundamentally different** from our additive 3% — bet count should be visibly higher (probably 5–10x more flags than `A_production`).
- **R.2 weighted-mean numerical stability.** When all books in a fixture happen to be soft-tier (weight 0.7), the weighted mean equals the unweighted mean — verify this is the intended behaviour, not a bug.
- **R.5 backtest regression.** The corrected-shin numbers in `docs/BACKTEST.md` (2% edge → 17.65% ROI) were generated 2026-04-29. Re-running with `power` should produce numbers in the same order of magnitude. If `power` returns wildly different numbers (e.g. 50% ROI), suspect a bug before celebrating.
- **R.5.5a sklearn drift.** `TimeSeriesSplit` is stable, but if a future sklearn release changes its API, our walk-forward primitive needs updating. Low risk (this is core sklearn, not a niche package), but worth knowing. Earlier draft considered `sports-betting` as a runtime dep — rejected: too thin a third-party surface to host a function that produces graduation evidence.
- **R.6 silent graduation.** Easy to flip a default and forget to update CLAUDE.md / dashboard / tests. Use the PR template's "docs updated" checkbox.

---

## Definition of done — by next Friday

- [ ] R.0–R.5 merged.
- [ ] **R.5.5a scaffold merged** — primitive + loader in `src/betting/walk_forward.py`, 5 tests pass, no new third-party deps.
- [x] **R.5.5b extra-leagues adoption merged** — 191 files, 91k matches / 22 leagues; existing 6 leagues unchanged; `docs/FDCO_INGEST_NOTES.md` written; loader encoding + odds-coercion fixes in `walk_forward.py`.
- [ ] **R.5.5c walk-forward run + report merged** — `docs/BACKTEST.md` reports per-fold ROI + 95% CI for `raw` / `shin` / `power`.
- [ ] At least one variant graduated (R.6) with explicit walk-forward evidence, OR explicit "no graduation this week" note citing CI breadth.
- [x] R.7 schema migration done (independent of graduation; commit `a980efc`).
- [x] R.9 AH feasibility note written (`docs/AH_FEASIBILITY.md`, 2026-04-30).
- [x] R.8 draw-bias variant in shadow — `scripts/refresh_xg.py` + `K_draw_bias` shipped.
- [ ] CLAUDE.md and README.md reflect any default changes.

---

## Cross-phase verifier checklist (final review)

Once all phases targeted for this sprint are merged, the verifier bot runs this end-to-end check:

```bash
# 1. Working tree clean
git status --porcelain  # empty

# 2. All tests pass
pytest -q

# 3. Scanner imports without error
python3 -c "import scripts.scan_odds; import src.betting.strategies; import src.betting.consensus"

# 4. All new variants present in STRATEGIES
python3 -c "
from src.betting.strategies import STRATEGIES
required = {'A_production', 'I_power_devig', 'J_sharp_weighted', 'L_quarter_kelly',
            'M_min_prob_15', 'N_competitive_only', 'O_kaunitz_classic'}
present = {s.name for s in STRATEGIES}
missing = required - present
assert not missing, f'Missing variants: {missing}'
print(f'OK: {len(required)} required variants present (out of {len(present)} total)')
"

# 5. No hardcoded paths
grep -RE '/(home|mnt)/[a-z]' scripts/ src/ 2>/dev/null | grep -v test_ | grep -v ".git" || echo "OK: no hardcoded paths"

# 6. Documentation cross-references intact
grep -c "RESEARCH_NOTES_2026-04" docs/PLAN_RESEARCH_2026-04.md  # >= 1
grep -c "PLAN_RESEARCH_2026-04" docs/RESEARCH_NOTES_2026-04.md  # >= 1
grep -c "BACKTEST.md" CLAUDE.md  # >= 1

# 7. Walk-forward backtest output present
grep -c "Walk-forward" docs/BACKTEST.md  # >= 1

# 8. Smoke run scanner (must not crash; rows-written check is best-effort)
export $(cat .env)
timeout 120 python3 scripts/scan_odds.py 2>&1 | tail -20
echo "Exit: $?"  # 0 expected
```

**Sign-off**: verifier creates a single PR comment summarising:
- Number of phases merged this sprint
- Any failed verification commands (none expected)
- Any phases that fell back to alternative paths
- Recommended graduations for next sprint (if R.6 didn't graduate anything this sprint)
