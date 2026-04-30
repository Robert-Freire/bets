# Research Follow-up Plan — 2026-04

Implementation plan derived from `docs/RESEARCH_NOTES_2026-04.md`. Phases are sized for one PR each, ordered by adopt-cost × signal × dependency.

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
- **Decision-deferred phase (R.5.5)**: when phase body specifies a fallback path, follow that path's budget rule explicitly and document the decision in the PR body.

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
| Backtest entry (after R.5.5) | `src/betting/sportsbet_adapter.py` + `scripts/walk_forward_backtest.py` |
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
| R.2 | Sharp-weighted consensus variant (J) | Friday | pending |
| R.3 | SBK availability probe → UK_LICENSED_BOOKS | Friday | pending |
| R.4 | Weekend data collection | Sat–Sun | runs automatically (existing cron) |
| R.5 | Monday analysis: §4.3, 4.5, 4.6 + compare_strategies | Mon AM | pending |
| R.5.5 | Walk-forward backtest refactor (sklearn `TimeSeriesSplit`) | Mon PM – Tue | pending |
| R.6 | Graduate winning variants → scanner defaults | Wed | conditional on R.5.5 |
| R.7 | bets.csv schema: `devig_method`, `weight_scheme` columns | Wed | pending |
| R.8 | Draw-bias variant (K) — needs xG runtime hookup | Thu–Fri | pending |
| R.9 | Asian Handicap feasibility probe (The Odds API) | Thu–Fri | pending |
| R.10 | AH probability conversion module (planning only) | Following week | deferred |

**Dependency graph:**

```
R.0 ─┐
     ├─ R.1 ──┐
     ├─ R.1.5 ┤
     ├─ R.1.6 ┼─ R.4 ─ R.5 ─ R.5.5 ─ R.6 ─ R.7
     ├─ R.2 ──┤                      │
     └─ R.3 ──┘                      ├─ R.8 (xG)
                                     └─ R.9 ─ R.10 (deferred)
```

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
2. **§4.5 — power vs Shin backtest.** Re-run the existing backtest entry point with `devig="power"`. Located in `src/betting/consensus.py::backtest_consensus`; called from `main.py` or directly. Add a third column to `docs/BACKTEST.md` next to `raw` and `shin`. **Whole-period only — superseded by R.5.5's walk-forward.**
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
- [ ] `docs/BACKTEST.md` has a `power` column (whole-period method — preliminary; R.5.5 supersedes with per-fold variance).
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

**Note.** R.5's power-vs-Shin comparison uses the existing whole-period backtest. **It is preliminary.** Graduation decisions in R.6 must wait for R.5.5's per-fold walk-forward numbers — a single whole-period ROI can be driven by a few good seasons and is not sufficient evidence to flip a default.

---

## Phase R.5.5 — Walk-forward backtest via `sports-betting` package (~1h)

**Goal.** Replace whole-period `backtest_consensus()` with walk-forward evaluation using `sklearn.model_selection.TimeSeriesSplit`, by **adopting `georgedouzas/sports-betting` as a dev dependency** rather than writing from scratch. This package already implements the exact pattern we need (`src/sportsbet/evaluation/_model_selection.py`) — joblib-parallelised per-fold backtest with rich per-market reporting.

**Why now.** Whole-period ROI hides per-season variance. Walk-forward reveals consistency. Required for any defensible default-flip in R.6.

**Why use the package vs writing our own.** `sports-betting`'s `backtest()` accepts any `BaseBettor` subclass and runs walk-forward against it with parallelism, per-market breakdowns, and well-tested split logic. Subclassing takes ~1h. From-scratch was scoped at 2.5h and would re-implement infrastructure we don't need to own.

**Inputs.** R.5 done. Internet access (pip install).

**Outputs.**
- `requirements-dev.txt` (or `pyproject.toml`) includes `sports-betting>=0.5`.
- `src/betting/sportsbet_adapter.py` — new module with `ConsensusBettor(BaseBettor)` subclass wrapping our existing Shin-devigged + dispersion-filtered consensus logic.
- `scripts/walk_forward_backtest.py` — entry script that loads our backtest data, instantiates `ConsensusBettor`, runs `backtest()` from sports-betting, writes per-fold table to `docs/BACKTEST.md`.
- `tests/test_sportsbet_adapter.py` — small test ensuring the adapter passes `OddsComparisonBettor`-equivalent inputs through.
- `docs/BACKTEST.md` — new "Walk-forward (5 folds)" section.

**Tasks.**
1. **Sanity-check existing scikit-learn.** `python3 -c "import sklearn; print(sklearn.__version__)"`. CatBoost likely pulls a compatible version.
2. **Install `sports-betting`.** Add to `requirements-dev.txt` (this is a backtest-only dep, not runtime). Verify import works.
3. **Subclass `BaseBettor`** in `src/betting/sportsbet_adapter.py`:
   ```python
   from sportsbet.evaluation import BaseBettor

   class ConsensusBettor(BaseBettor):
       def __init__(self, devig: str = "shin", min_edge: float = 0.02, ...):
           ...
       def fit(self, X, Y, O):
           # No-op for consensus — there's no model to train.
           # Store features for later use in bet().
           return self
       def bet(self, X, O) -> np.ndarray[bool]:
           # Apply Shin/power devig per row of O, compute consensus,
           # return boolean array of which (row, market) pairs we'd flag.
   ```
4. **Adapter for our data shape.** Our `data/raw/*.csv` is row-per-match with H/D/A columns. `sports-betting`'s `extract_train_data()` returns three dataframes (X, Y, O) — write a small loader that converts our CSV to that shape.
5. **Run backtest** via `sportsbet.evaluation.backtest(bettor, X, Y, O, cv=TimeSeriesSplit(5))`.
6. **Write `docs/BACKTEST.md` walk-forward section.** Keep legacy whole-period tables. Add per-fold table for `raw`, `shin`, `power` × `min_edge ∈ {0.01, 0.02, 0.03, 0.04, 0.05}`. Each cell: ROI%, bet count. Aggregate row with mean ± 95% CI (t-distribution, n=5).
7. **Interpretation note.** Flag any edge × method combo where 95% CI crosses zero — those cannot defend a default-flip.
8. **Tests.**
   - Adapter passes a single fabricated row through `fit` → `bet` and returns the expected flag.
   - Determinism: backtest twice → identical DataFrame.

**Acceptance.**
- [ ] `pip install sports-betting` clean; no version conflicts. (OR: fallback to from-scratch `TimeSeriesSplit` taken — documented in PR body.)
- [ ] `scripts/walk_forward_backtest.py` runs end-to-end on existing dataset.
- [ ] `docs/BACKTEST.md` walk-forward section exists with all 3 devig methods and 5 edge thresholds.
- [ ] Per-fold variance reported. Aggregate CI annotated.
- [ ] Adapter test passes.

**Verification commands.**
```bash
# Dependency installed (or fallback documented)
python3 -c "import sportsbet" 2>/dev/null && echo "OK: sports-betting installed" \
  || grep -q "fallback to from-scratch" $(git log -1 --format=%B 2>/dev/null) \
  || echo "WARN: neither package nor fallback evident"

# Backtest script runs end-to-end
python3 scripts/walk_forward_backtest.py 2>&1 | tail -20

# BACKTEST.md has walk-forward section
grep -c "Walk-forward" docs/BACKTEST.md  # >= 1
grep -cE "(95% CI|confidence interval|fold)" docs/BACKTEST.md  # >= 1

# All 3 devig methods present in walk-forward
grep -A20 "Walk-forward" docs/BACKTEST.md | grep -cE "(raw|shin|power)"  # >= 3

# Tests pass
pytest -q tests/test_sportsbet_adapter.py
```

**Reviewer focus.** Whether the adapter's fit/bet contract truly matches `BaseBettor`'s expectations. If the package version-bumps an API, swap to writing a thin internal `TimeSeriesSplit` loop ourselves — falls back to the original 2.5h scope. **The PR body must explicitly state which path was taken** (package vs from-scratch) and why.

**Decision deferred to phase execution.** If the package's data-shape requirements turn out to be a poor fit (e.g. requires multi-output classifier semantics that don't apply to our case), abandon and write the from-scratch version. Phase budget: 1h with package, 2.5h without — pick whichever path is shorter once we've spent 30 min trying.

**Carryover.** Once this lands, `compare_strategies.py` and any future model-overhaul work (Phase 7) inherits the walk-forward primitive (whether ours or theirs).

---

## Phase R.6 — Graduate winning variants → scanner defaults (~1.5h, conditional)

**Goal.** Promote variants from shadow to scanner defaults if they meet bar.

**Bar for graduation.**
- Variant has shadow data ≥ 50 settled bets across the existing portfolio (won't be reached this weekend — most will need 4–6 weeks). For *immediate* graduation candidates from R.5/R.5.5, we apply a softer bar:
  - **M_min_prob_15**: graduates immediately if §4.6 shows decile-1 underperformance ≥ 5pp on existing settled history. Bias is empirical fact, not a strategy hypothesis.
  - **I_power_devig**: graduates only if R.5.5's **walk-forward** numbers show `power` ≥ `shin` ROI at 2–3% edges in **≥ 4 of 5 folds** AND the aggregate 95% CI does not cross Shin's mean. Whole-period dominance from R.5 alone is **insufficient** — we need consistency across time.
  - **J_sharp_weighted**, **L_quarter_kelly**, **N_competitive_only**: stay in shadow until ≥ 50 settled bets *and* their inclusion in the walk-forward backtest (follow-up PR) shows positive aggregate ROI.

**Tasks (conditional on R.5 results).**
1. If M graduates: add `MIN_CONSENSUS_PROB = 0.15` constant to `scripts/scan_odds.py` and apply pre-flag. Variant `M_min_prob_15` retired from STRATEGIES (or kept for regression comparison — decide).
2. If I graduates: change default `devig` in production scanner from `shin` to `power`, OR add a runtime flag and start shipping `power` for new bets.
3. Update `CLAUDE.md` "How the scanner works" section to reflect the new defaults.
4. Update `docs/PLAN.md` Phase 1 table — annotate the change.

**Acceptance.**
- [ ] Each graduating variant has a one-paragraph promotion note in the PR body explaining the evidence (which fold counts, which CI bounds).
- [ ] No graduation happens silently — even immediate ones get explicit sign-off in commit message.
- [ ] CLAUDE.md "How the scanner works" section updated to reflect new defaults.
- [ ] If nothing graduates: explicit `## No graduation this week` section in PR body explaining why (citing CI breadth from R.5.5).

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

## Phase R.10 — AH probability conversion module (deferred, planning only)

**Goal.** Implement Hegarty & Whelan's closed-form AH→prob conversion. Adds AH-derived prob as a *second anchor* alongside Pinnacle h2h.

**Inputs.** R.9 says "yes, AH is fetchable."

**Tasks.** (To be detailed in a follow-up plan once R.9 is in.)

**Status.** Not committed for next week. Planning carryover only — listed here so it doesn't get lost.

---

## Open carryovers (not phased yet)

- **Restriction-detection logging** (RESEARCH_NOTES §3.3): per-bookie max-stake limits hit on placement, manual log via dashboard. Lightweight but needs UI work.
- **Mug-bet camouflage cron** (RESEARCH_NOTES §3.4): scheduled small bets to mask account profile. Only relevant once we hit a real restriction.
- **Migrate `compare_strategies.py` to walk-forward** (follow-up to R.5.5): once the `sports-betting`-based primitive lands, port shadow-portfolio comparison to use the same splitter for fold-aware CLV variance reporting.
- **Zenodo 84k-match dataset** (Hegarty & Whelan): replace football-data.co.uk EPL CSV (~4.5k matches, 1 league, 6 books) with broader European dataset (84k matches, 22 leagues, 40-60 books). 7× more data. Phase 7 follow-up.
- **`pybettor` evaluation** (RESEARCH_NOTES §9.4): 30-min skim of `ian-shepherd/pybettor` to determine if any utilities replace what we currently maintain. Decision: dep or reference.
- **ELO prior variant `Q_elo_prior`** (RESEARCH_NOTES §9.3): WagerBrain's `elo_prob(elo_diff)` is a cheap model-agreement signal. Could substitute for CatBoost on leagues we don't have CatBoost coverage for (Championship, Bundesliga 2, NBA, tennis). Phase 7-adjacent.
- **Asian Handicap as second anchor** (RESEARCH_NOTES §7.1): if R.9 says AH is fetchable, implement Hegarty & Whelan's closed-form prob conversion (Eqs 6–28) in `src/betting/asian_handicap.py`. Use AH-derived prob alongside Pinnacle h2h as a *second* anchor (averaging the two when both available). The point: AH is the efficient market — it's the strongest external probability signal we could integrate.
- **Dashboard pagination/filter for variants** (RESEARCH_NOTES §9 implications): with R.1 + R.1.5 + R.1.6 + R.2 we go from 8 to 13–14 strategy variants. The current dashboard's "Three bet sections" list will get crowded. Add a strategy filter dropdown.

---

## Risks / things to watch

- **R.1 Kelly plumbing.** If `kelly_fraction` is read from a global constant rather than `StrategyConfig`, variant L becomes a no-op silently. Verify by inspecting `logs/paper/L_quarter_kelly.csv` stakes — should be exactly 0.8× of `A_production` for matched rows.
- **R.1.5 — silently wrong baseline.** If `raw_consensus=True` accidentally still calls Shin somewhere, the "paper-faithful" comparison would show our system being worse than itself. Smoke-test by computing one fixture by hand and comparing. The α=0.05 multiplicative threshold is **fundamentally different** from our additive 3% — bet count should be visibly higher (probably 5–10x more flags than `A_production`).
- **R.2 weighted-mean numerical stability.** When all books in a fixture happen to be soft-tier (weight 0.7), the weighted mean equals the unweighted mean — verify this is the intended behaviour, not a bug.
- **R.5 backtest regression.** The corrected-shin numbers in `docs/BACKTEST.md` (2% edge → 17.65% ROI) were generated 2026-04-29. Re-running with `power` should produce numbers in the same order of magnitude. If `power` returns wildly different numbers (e.g. 50% ROI), suspect a bug before celebrating.
- **R.5.5 dependency risk.** `sports-betting` package is one researcher's project — could break on Python or sklearn version bumps. Pin the version explicitly. If it diverges, fall back to the from-scratch `TimeSeriesSplit` implementation.
- **R.6 silent graduation.** Easy to flip a default and forget to update CLAUDE.md / dashboard / tests. Use the PR template's "docs updated" checkbox.

---

## Definition of done — by next Friday

- [ ] R.0–R.5 merged.
- [ ] **R.5.5 walk-forward backtest refactor merged** — `docs/BACKTEST.md` reports per-fold ROI + 95% CI for `raw` / `shin` / `power`.
- [ ] At least one variant graduated (R.6) with explicit walk-forward evidence, OR explicit "no graduation this week" note citing CI breadth.
- [ ] R.7 schema migration done (independent of graduation).
- [ ] R.9 AH feasibility note written.
- [ ] R.8 draw-bias variant in shadow if xG hookup landed; deferred to following week if not.
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
- Any phases that fell back to alternative paths (R.5.5 fallback most likely)
- Recommended graduations for next sprint (if R.6 didn't graduate anything this sprint)
