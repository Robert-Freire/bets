# Plan — Strategy Comparison Pipeline Improvements (2026-04)

Companion to `docs/PLAN_RESEARCH_2026-04.md`. Scope: improvements identified in the
strategy-pipeline review on 2026-04-30, focused on `scripts/compare_strategies.py` and
`docs/STRATEGY_COMPARISON.md` so that Monday-morning analysis after the first
shadow-portfolio weekend (2026-05-04) is debuggable, statistically honest, and
sliceable along axes that actually matter for variant graduation decisions.

**Bot execution protocol**: same as `docs/PLAN_RESEARCH_2026-04.md` §"How to use this
doc — bot execution protocol". One PR per phase by default; phases marked "**batchable**"
may be combined into a single PR if all touch only `compare_strategies.py` and the
diff stays under ~150 lines.

---

## Already done (do not re-do)

These two fixes shipped during the review on 2026-04-30. Listed here so the bot
doesn't accidentally redo them.

| # | Change | Where |
|---|---|---|
| ✅ | Seeded `logs/team_xg.json` (76 teams, `xg_q25=1.198`) by running `python3 scripts/refresh_xg.py` once. Cron will keep it fresh from Mon 06:00 onwards. | `logs/team_xg.json` |
| ✅ | Added `UNDERSTAT_NAME_ALIAS` (19 mappings: 3 EPL, 9 Bundesliga, 5 Serie A, 2 Ligue 1) and applied it at both `_flag_bets` xG-lookup sites. Without this map, ~30% of fixtures across the four xG-supported leagues silently failed name matching, gating `K_draw_bias` to zero flags. | `src/betting/strategies.py:50-75, 360-365, 437-442` |

If the `UNDERSTAT_NAME_ALIAS` map needs to grow (new promoted club, new league), add
entries directly — no separate phase needed.

---

## Phase status tracker

| Phase | Title | Tier | Window | Status |
|---|---|---|---|---|
| C.1 | Show 0-bet variants in comparison report | High | Pre-weekend | ✅ done |
| C.2 | 95% CI / SE on Avg CLV | High | Pre-weekend | ✅ done |
| C.3 | Per-sport breakdown table | High | Pre-weekend | pending |
| C.4 | Drift-toward-you % per variant (wire `logs/drift.csv`) | Medium | Post first comparison | pending |
| C.5 | Per-confidence breakdown (HIGH / MED / LOW) | Medium | Post first comparison | pending |
| C.6 | Median CLV alongside mean | Medium | Pre-weekend (cheap) | ✅ done |
| C.7 | Per-market breakdown (h2h / totals / btts) | Low | Backlog | pending |
| C.8 | Model-signal stratification | Low | Backlog | pending |
| C.9 | Sample-size guardrail line in report | Low | Pre-weekend (cheap) | ✅ done |

**Batchable groups** (single-PR-friendly):
- **Group A** (`compare_strategies.py` only, all small): C.1 + C.2 + C.6 + C.9.
  Net diff likely <80 lines. Recommended bundle.
- **Group B**: C.3 alone (new section, ~40 lines).
- **Group C**: C.5 + C.7 + C.8 (all add a sliced table; same pattern). Could be one
  PR if landed together; the slicing helper from C.5 is reused.
- **Group D**: C.4 alone (new data source: `logs/drift.csv` parsing).

Recommended landing order: **A → B → D → C**. Group D before C because the drift
signal is more decision-relevant than further slicing.

---

## Goal & non-goals

**Goal.** By end of next week (~2026-05-09), `docs/STRATEGY_COMPARISON.md` shows
every configured variant (including 0-bet ones), CIs that distinguish signal from
noise, per-sport and per-confidence slices, and a drift-toward-you metric per
variant. The first multi-weekend graduation decision (R.6 in
`PLAN_RESEARCH_2026-04.md`) becomes defensible rather than driven by 5-sample point
estimates.

**Non-goals.**
- No new strategy variants. (That is `strategies.py` work, not comparison-pipeline work.)
- No real-money decisions driven by single-weekend data even after these improvements.
- No SQLite migration of `logs/paper/`. Stay on CSV until Phase 6 in `PLAN.md`.
- No dashboard (`app.py`) changes in this plan; the dashboard already exposes
  Avg CLV and Drift-toward-you tiles. This plan is about the Markdown report.

---

## Phase C.1 — Show 0-bet variants in comparison report (~20 min, batchable)

**Goal.** Make silent variant failures (e.g. `K_draw_bias` rejecting every match
due to a bad alias map) visible in the report instead of hidden.

**Inputs.** None.

**Tasks.**
1. In `scripts/compare_strategies.py`, import `STRATEGIES` from
   `src.betting.strategies`.
2. In `build_report`, after the existing `entries = ... PAPER_DIR.glob("*.csv")`
   loop, add the configured-but-empty variants:
   ```python
   from src.betting.strategies import STRATEGIES
   seen = {name for name, _ in entries}
   for s in STRATEGIES:
       if s.name not in seen:
           entries.append((s.name, []))  # empty rows → all "—" stats
   ```
3. Sort `results` so 0-bet variants appear at the bottom (after `None` avg_clv ones)
   — they should not displace meaningful comparisons. Suggest sort key:
   `(no_bets, avg_clv is None, -(avg_clv or 0))` where `no_bets = s["n_bets"] == 0`.
4. Adjust the table row to render `0` for `Bets`, `—` for everything else when n=0.
5. Add a short note above the table: *"Variants with 0 bets this period are listed
   for completeness; if a variant you expect to fire shows 0, check its filter
   wiring (e.g. `K_draw_bias` requires `logs/team_xg.json` and an alias-resolved
   team name)."*

**Acceptance.**
- [ ] Report includes one row per `STRATEGIES` entry (currently 16), regardless of
      whether the corresponding CSV exists.
- [ ] 0-bet rows render without raising on `_fmt(None)`.
- [ ] Existing variants with bets keep their current ranking.
- [ ] `pytest -q` passes.

**Verification commands.**
```bash
# Delete one paper CSV and confirm it still appears in the report
mv logs/paper/B_strict.csv /tmp/B_strict.csv.bak
python3 scripts/compare_strategies.py
grep -c "B_strict" docs/STRATEGY_COMPARISON.md  # expect ≥ 1
mv /tmp/B_strict.csv.bak logs/paper/B_strict.csv

# Confirm K_draw_bias appears (0 bets pre-weekend) without error
grep "K_draw_bias" docs/STRATEGY_COMPARISON.md
```

**Reviewer focus.** Did the bot wire the import path correctly? `compare_strategies.py`
currently sets up `sys.path` at the top — confirm the `STRATEGIES` import doesn't
break in cron context (where `cwd` differs).

---

## Phase C.2 — 95% CI / SE on Avg CLV (~30 min, batchable)

**Goal.** A point estimate of Avg CLV from 5 bets is noise. Surface SE so the
reader can tell signal from noise without having to mentally divide by `sqrt(n)`.

**Inputs.** None.

**Tasks.**
1. In `_stats()`, compute `clv_stdev = statistics.stdev(clv_values) if len >= 2
   else None`, `se = clv_stdev / sqrt(n)`, and `ci95_half = 1.96 * se`.
2. Add three keys to the returned dict: `clv_se`, `clv_ci95_lo`, `clv_ci95_hi`
   (both bounds are `avg_clv ± ci95_half`).
3. Replace the `Avg CLV` cell in the per-variant table with
   `{avg_clv:.2%} ± {ci95_half:.2%}` (e.g. `+1.45% ± 4.80%`). When `n < 2`, render
   the CI cell as `—`.
4. Add a one-line note under the table: *"95% CI is `±1.96·σ/√n`. A variant whose
   CI bracket includes 0 has not yet shown a statistically distinguishable signal."*

**Acceptance.**
- [ ] Existing column `Avg CLV` either widens to include `± x.xx%`, or a new
      column `95% CI` is added — bot's choice; whichever renders cleaner.
- [ ] Variants with `n_with_clv < 2` render the CI as `—`.
- [ ] `pytest -q` passes (existing tests should not regress; if new test added
      for SE math, must use a known-stdev fixture).

**Verification commands.**
```bash
# Manually verify CI math for one variant
python3 -c "
import csv, statistics, math
rows = list(csv.DictReader(open('logs/paper/A_production.csv')))
clvs = [float(r['clv_pct']) for r in rows if r.get('clv_pct')]
if len(clvs) >= 2:
    se = statistics.stdev(clvs) / math.sqrt(len(clvs))
    print(f'n={len(clvs)}, mean={statistics.mean(clvs):.4f}, SE={se:.4f}, ±1.96SE={1.96*se:.4f}')
"
python3 scripts/compare_strategies.py
grep -A1 "A_production" docs/STRATEGY_COMPARISON.md | head -3
```

**Reviewer focus.** Edge case: `clv_values` of length exactly 1 — `statistics.stdev`
raises. Bot must guard with `if len(clv_values) >= 2`.

---

## Phase C.3 — Per-sport breakdown table (~45 min)

**Goal.** A tennis-heavy weekend currently swamps the EPL/Bundesliga signal.
Stratifying by `sport` makes per-league variant-fitness visible.

**Inputs.** None. Paper CSVs already include a `sport` column.

**Tasks.**
1. Add `_per_sport_stats(rows)` helper that groups rows by `r["sport"]` then calls
   the existing `_stats()` per group.
2. In `build_report`, after the per-variant table, emit a new section
   `## CLV by sport` showing **only the production-A baseline plus variants with
   ≥10 CLV bets in that sport** (to avoid 64 cells of noise). Format:

   | Sport | Variant | Bets | CLV bets | Avg CLV | CLV >0 % |
   |---|---|---|---|---|---|

3. Sort within each sport by Avg CLV descending; sort sports alphabetically (EPL
   first by convention since it's the highest-volume).

**Acceptance.**
- [ ] New section `## CLV by sport` renders for sports with at least one
      qualifying row.
- [ ] Variants with `n_with_clv < 10` per sport are excluded from per-sport rows
      (but still appear in the main per-variant table).
- [ ] If no per-sport row qualifies, the section is omitted entirely (don't emit
      an empty header).

**Verification commands.**
```bash
python3 scripts/compare_strategies.py
grep -A30 "CLV by sport" docs/STRATEGY_COMPARISON.md
# Sanity: the rows under each sport should sum to ≤ that variant's overall CLV bets
```

**Reviewer focus.** What does `sport` actually contain in paper CSVs? Confirm it's
the human label (`"EPL"`, `"Bundesliga"`) not the sport_key (`"soccer_epl"`).
Look at `logs/paper/A_production.csv` head before coding.

---

## Phase C.4 — Drift-toward-you % per variant (~1.5h)

**Goal.** Add a second sharpness signal beyond CLV: did Pinnacle's line move toward
the variant's flagged side between T-60 and kick-off?

**Inputs.** `logs/drift.csv` populated (the cron has been running every 5 min).

**Tasks.**
1. Inspect `logs/drift.csv` schema: confirm columns include
   `(kickoff, home, away, side, pinnacle_prob_t60, pinnacle_prob_close)` or
   equivalent. Adapt below to actual schema.
2. Add `_load_drift_index()` returning a dict keyed by
   `(kickoff, home, away, market, line, side)` → `(prob_t60, prob_close)`.
3. Add to `_stats(rows, drift_index=None)`:
   ```python
   moved_toward = 0
   total_with_drift = 0
   for r in rows:
       k = (r['kickoff'], r['home'], r['away'], r['market'],
            str(r.get('line', '')), r['side'])
       if k in drift_index:
           t60, close = drift_index[k]
           total_with_drift += 1
           # Variant flagged a side at price X (so cons[side] > impl_at_flag).
           # If close > t60 for that side, the market moved toward the variant.
           if close > t60:
               moved_toward += 1
   drift_pct = moved_toward / total_with_drift if total_with_drift else None
   ```
4. New column `Drift→you %` in the per-variant table.

**Acceptance.**
- [ ] `Drift→you %` column shows for variants with ≥1 drift-matched row; otherwise
      `—`.
- [ ] Sane sanity rule: A_production's drift% should fall in the 40–70% range
      (50% = noise floor, materially above = sharp). If any variant's drift% is
      either 0% or 100% with n>=10, flag it in the report (likely a sign-error in
      the bot's "moved toward" comparison).
- [ ] `pytest -q` passes; new test asserts the `_load_drift_index` schema parser
      accepts the current `logs/drift.csv` first row without raising.

**Verification commands.**
```bash
head -2 logs/drift.csv  # confirm schema before coding
python3 scripts/compare_strategies.py
grep -E "Drift" docs/STRATEGY_COMPARISON.md
# Monotonic-direction sanity: plot or eyeball that a higher Avg CLV correlates
# loosely with a higher Drift→you %. They measure related but not identical things.
```

**Reviewer focus.** The "moved toward you" definition is sign-sensitive. If the
bet was on `OVER` at price 2.10 and the close moved to 2.05 (price down → implied
prob up), that's market-toward-you for the OVER side because the market caught
up. Confirm the bot computed direction with the right sign per market type. The
safest implementation compares devigged Pinnacle probs at T-60 vs close and asks
"did the side's prob rise?" — independent of market type.

---

## Phase C.5 — Per-confidence breakdown (HIGH / MED / LOW) (~30 min)

**Goal.** Show whether variants are CLV-positive only at HIGH confidence (≥30
books) or hold up at LOW. `O_kaunitz_classic` with `min_books=4` will produce many
LOW rows; the slice tells whether that's free signal or noise.

**Inputs.** None.

**Tasks.**
1. Reuse the slicing pattern introduced in C.3 (or similar). Group rows by
   `r["confidence"]` ∈ `{HIGH, MED, LOW}`.
2. Emit `## CLV by confidence` table with one row per (variant, confidence) where
   `n_with_clv >= 5`.
3. Same sort: variant alphabetical, confidence in `[HIGH, MED, LOW]` order.

**Acceptance.**
- [ ] Section renders only for variants with at least one qualifying confidence row.
- [ ] Tier order is HIGH→MED→LOW (don't sort alphabetically, that puts HIGH last).

**Verification.** `grep -A20 "CLV by confidence" docs/STRATEGY_COMPARISON.md`.

**Reviewer focus.** None — straightforward grouping.

---

## Phase C.6 — Median CLV alongside mean (~10 min, batchable)

**Goal.** A few large-magnitude outliers can shift Avg CLV by ±5pp on small
samples. Median is the tail-robust check.

**Inputs.** None.

**Tasks.**
1. In `_stats`, add `median_clv = statistics.median(clv_values) if clv_values else None`.
2. Add a `Med CLV` column adjacent to `Avg CLV`. Format `.2%` like the mean.

**Acceptance.**
- [ ] New column populated for any variant with `n_with_clv >= 1` (median is
      defined for n=1, unlike stdev).
- [ ] `pytest -q` passes.

**Verification.** `grep "Med CLV" docs/STRATEGY_COMPARISON.md`.

**Reviewer focus.** None.

---

## Phase C.7 — Per-market breakdown (h2h / totals / btts) (~30 min)

**Goal.** h2h, totals, and btts have materially different noise profiles. A
variant might be CLV+ on h2h but CLV− on totals.

**Inputs.** None.

**Tasks.**
1. Group rows by `r["market"]` ∈ `{h2h, totals, btts}`.
2. `## CLV by market` table — same shape as C.3.
3. Threshold: include rows with `n_with_clv >= 5` per (variant, market).

**Acceptance.**
- [ ] Section renders only for markets with at least one qualifying row.
- [ ] `F_model_primary` (h2h-only by definition) appears only in the h2h row.

**Verification.** `grep -A20 "CLV by market" docs/STRATEGY_COMPARISON.md`.

**Reviewer focus.** None.

---

## Phase C.8 — Model-signal stratification (~45 min)

**Goal.** The `model_signal` column is captured but unused. Stratify CLV by
"model agrees" vs "model disagrees" vs "no model signal" to confirm that the
2–3% model-filtered notification path (`F_model_primary` and the production model
gate) actually produces better CLV.

**Inputs.** None.

**Tasks.**
1. Add a helper `_model_bucket(signal)`:
   ```python
   if signal in ("?", "", None): return "no_signal"
   try:
       return "agrees" if float(signal.lstrip("+")) > 0 else "disagrees"
   except (ValueError, TypeError):
       return "no_signal"
   ```
2. Group rows by this bucket.
3. `## CLV by model signal` table — same shape as C.3. Include all three buckets
   per variant where `n_with_clv >= 5`.

**Acceptance.**
- [ ] Section renders for variants with at least one qualifying bucket.
- [ ] `agrees` rows for `F_model_primary` should be the bulk of its rows
      (sanity check: `F` requires model agreement).

**Verification.** `grep -A30 "CLV by model signal" docs/STRATEGY_COMPARISON.md`.

**Reviewer focus.** Model-signal column is `"+0.123"` / `"-0.045"` / `"?"`.
Confirm the bot handles the leading `+` and the `?` sentinel.

---

## Phase C.9 — Sample-size guardrail line (~15 min, batchable)

**Goal.** Make it impossible to misread a 5-bet "best variant" as a graduation
candidate.

**Inputs.** None.

**Tasks.**
1. Above the per-variant table, emit a one-line warning:
   ```
   > **Sample size note.** Variants with `<10` CLV bets in this report are
   > indicative only. Per `RESEARCH_NOTES_2026-04.md` §6, graduation requires
   > ≥30 CLV bets across ≥3 weekends with positive Avg CLV CI bracket.
   ```
2. In the per-variant table, prefix variants with `n_with_clv < 10` with `⚠️ ` (or
   the literal text `[low n] `) to make the visual cue obvious.

**Acceptance.**
- [ ] Warning line present once at the top of the report.
- [ ] Low-sample variants visually marked.

**Verification.** `head -20 docs/STRATEGY_COMPARISON.md`.

**Reviewer focus.** None.

---

## Cross-phase verifier checklist (final review)

After all targeted phases merge, the verifier bot runs:

```bash
# 1. Tests pass
pytest -q

# 2. Report regenerates without error
python3 scripts/compare_strategies.py

# 3. All 16 variants appear (C.1)
python3 -c "
from src.betting.strategies import STRATEGIES
report = open('docs/STRATEGY_COMPARISON.md').read()
missing = [s.name for s in STRATEGIES if s.name not in report]
assert not missing, f'Variants missing from report: {missing}'
print(f'OK: all {len(STRATEGIES)} variants present')
"

# 4. CI rendering present (C.2)
grep -E '±\s*[0-9]' docs/STRATEGY_COMPARISON.md | head -1

# 5. Per-sport section present (C.3)
grep -q "## CLV by sport" docs/STRATEGY_COMPARISON.md && echo "OK: per-sport section present"

# 6. Drift column present (C.4)
grep -q "Drift" docs/STRATEGY_COMPARISON.md && echo "OK: drift column present"

# 7. Median column present (C.6)
grep -q "Med CLV" docs/STRATEGY_COMPARISON.md && echo "OK: median column present"

# 8. Sample-size warning present (C.9)
grep -q "Sample size note" docs/STRATEGY_COMPARISON.md && echo "OK: warning present"
```

---

## Risks / things to watch

- **Drift schema drift (C.4).** `logs/drift.csv` is owned by `closing_line.py`.
  If its schema changes between when this plan is written and the bot picks up
  C.4, the parser will silently drop rows. Always re-read `head -2 logs/drift.csv`
  before coding C.4.
- **Cron context for `STRATEGIES` import (C.1).** `compare_strategies.py` is
  invoked manually post-weekend; not from cron. But the standard `sys.path`
  insertion at the top of the file already handles this — bot must verify it
  still works after the import addition.
- **Backwards compatibility of the report.** The dashboard (`app.py`) reads only
  the "Research" mode/date and doesn't parse `STRATEGY_COMPARISON.md`. Format
  changes here are safe with respect to the dashboard.
- **Over-stratification.** With 16 variants × 8 sports × 3 confidence × 3 market
  × 3 model-signal buckets, the worst case has 1,152 cells. The `n>=5` /
  `n>=10` thresholds are deliberate to keep the report scannable. Bot must keep
  these thresholds; do not lower them in pursuit of completeness.
- **CLV stdev non-stationarity.** Late-season fixtures may have systematically
  different CLV variance (lower book counts, dead-rubber matches). The CI math is
  correct as a within-period summary, not a forecast. The sample-size note in
  C.9 is the mitigation.

---

## Definition of done — by 2026-05-09

- [ ] C.1 + C.2 + C.6 + C.9 (Group A) merged before Sat 2026-05-09 morning, so
      Monday's first comparison round benefits.
- [ ] C.3 (per-sport) merged after Group A — needed before R.6 graduation
      decisions in `PLAN_RESEARCH_2026-04.md`.
- [ ] C.4 (drift) merged before any single-variant graduation is proposed — drift
      is the second-witness signal that protects against CLV gaming.
- [ ] C.5 + C.7 + C.8 (Group C) merged or explicitly deferred to next sprint with
      reasoning.
- [ ] `docs/STRATEGY_COMPARISON.md` regenerated and committed with the new shape.
- [ ] `CLAUDE.md` updated only if any default behaviour changed (none expected
      from this plan).

---

## Out of scope (do not pull into this plan)

These are real follow-ups but belong to other plans:

- **R.5.5c walk-forward run + per-fold report** (`PLAN_RESEARCH_2026-04.md`).
  That plan owns the historical-backtest dimension; this plan owns the live
  shadow-portfolio dimension. The two stay separate.
- **Dashboard enhancements** for shadow comparison. Dashboard is `app.py`;
  changes there go in a separate UI-focused PR after the Markdown report is
  trustworthy.
- **SQLite migration of `logs/paper/`** — Phase 6 in `PLAN.md`.
- **CSV-schema additions to `logs/paper/*.csv`**. The current schema (with
  `devig_method`, `weight_scheme`, `kelly_fraction`-aware `stake`) is sufficient
  for every phase here. New columns belong with the variant they support, not
  with this analysis-pipeline plan.

## Grouping

- Tier markers (High / Medium / Low) and batchable groups:                                                                                                             
    - Group A (C.1 + C.2 + C.6 + C.9) — single small PR, must-have before next weekend
    - Group B (C.3) — per-sport breakdown
    - Group D (C.4) — drift wiring (heaviest)
    - Group C (C.5 + C.7 + C.8) — shared slicing pattern