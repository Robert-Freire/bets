# Phase 7 — Model calibration + honest hold-out eval (2026-05)

**Status when this plan was written:** CLAUDE.md `Phase 7 (model overhaul: calibration, hold-out eval)` = **pending**. Current model RPS 0.2137 vs bookmaker 0.1957 — no edge. CatBoost is wired into the scanner via `logs/model_signals.json` and powers the 2–3% model-filtered notification path on h2h in EPL/Bundesliga/Serie A/Ligue 1.

**Goal of this sprint.** Answer one question with evidence: _does the CatBoost model, after proper calibration, beat the market on any well-defined slice — and if so, which?_ Output a go/no-go decision plus, if go, a calibrated `model_signals.json` that the live scanner can consume without code changes.

**Non-goal.** Building a new model architecture, scraping new features, or retraining on more leagues. This is an evaluation + calibration sprint, not an architecture sprint. Phase 8 (Betfair auto-placement) and Phase 10 (syndicate) are separately tracked.

---

## Hard guardrails (the bot MUST respect these)

These exist because live cron is running on both WSL and Pi for the first weekend (see `docs/FIRST_WEEKEND.md`):

1. **No network calls during the work.** Train and evaluate entirely off `data/raw/*.csv` and `logs/team_xg.json`. Do **not** call `download_league`, `download_xg`, or any Odds API endpoint. No `--download` flag use. The Mon 06:00 UTC xG refresh (Pi-only) is the only live data refresh allowed this weekend, and the bot does not run it.
2. **Do not modify `scripts/scan_odds.py` or `scripts/model_signals.py` production behavior.** The scanner reads `logs/model_signals.json` from disk. If you regenerate it, write to `logs/model_signals_calibrated.json` instead and leave the live file untouched until phase 7.4 says otherwise.
3. **Do not touch crontab on either machine.** Not even to add a one-shot evaluation run.
4. **Do not write to Azure SQL or Azure Blob.** Phase 7 is purely a modelling sprint and has no DB schema implications. `BETS_DB_WRITE`, `BLOB_ARCHIVE` env flags are not your business.
5. **Do not train Pi-side.** All work runs on WSL (`/home/rfreire/projects/bets/`, `.env.dev`). Pi has the prod cron and limited compute headroom.
6. **Do not change the `config.json` `min_books` thresholds, dispersion / outlier filters, or Kelly multipliers.** Out of scope.
7. **All commits go on a feature branch off `main`** (e.g. `phase-7-model-eval-2026-05`). Open a PR but do not merge — leave for human review. Pre-commit hooks must not be bypassed.
8. **`pytest -q` must end the sprint at green.** Adding new tests is fine, regressing existing tests is not.
9. **Pi-safety contract:** verify nothing you add will be imported by the Pi `scan_odds.py` path. New deps go in `requirements-dev.txt` (create if needed), never `requirements.txt`. No new lazy-imported Azure clients.

---

## Phase 7.0 — Sanity baseline (do this first; ~30 min)

**What.** Reproduce the headline numbers from CLAUDE.md (model RPS 0.2137 vs bookmaker 0.1957) on the current code so we have a known-good baseline before we change anything.

**How.**

1. Branch: `git checkout -b phase-7-model-eval-2026-05`.
2. Add `scripts/eval_model_baseline.py` that:
   - Loads the EPL CSVs via `src.data.loader.load_all(since="1415")`.
   - Builds rolling pi-ratings + features via `src.ratings.pi_ratings.build_rolling_ratings` + `src.data.features.build_feature_matrix` (xG via `src.data.understat.load_xg(league="epl", since_season=2014)` from local files only — no network).
   - Trains `MatchPredictor(backend="catboost", calibrate=False)` on seasons `[1920, 2021, 2122]` and evaluates on `2223` (matches the existing `scripts/model_signals.py` window pattern; no future leakage).
   - Calls `src.model.calibration.evaluate(model_probs, test_matches)` and prints the dict.
3. Acceptance: prints `model_rps`, `bookmaker_rps`, `rps_vs_bookmaker`, `model_accuracy`. Numbers don't have to match exactly — bookmaker RPS depends on which odds columns are populated per season — but `model_rps - bookmaker_rps` should be in the range `[0.005, 0.030]` (i.e. model still worse than the market). If wildly outside, **stop and flag**: something has shifted in the data pipeline since the headline number was logged and the rest of the plan needs to be re-grounded.

**Deliverable.** `scripts/eval_model_baseline.py` + a 5-line summary written to `docs/PLAN_PHASE_7_2026-05.md` under a new "## Baseline (7.0 result)" section showing the numbers on the current data.

---

## Phase 7.1 — Time-series-aware hold-out leaderboard

**What.** Build one CLI that trains, evaluates, and prints a per-league + per-season leaderboard so we can see _where_ the model is closest to / furthest from the market. Today we only have a single global average.

**Why.** A global RPS gap of ~0.018 could mean "uniformly bad" or "great in one league, dreadful in another." We need to know which.

**How.**

1. Add `src/model/holdout.py` with one function:
   ```python
   def rolling_holdout_eval(
       sport_key: str,
       *,
       train_window: int = 3,
       since: str = "1415",
   ) -> pd.DataFrame:
       """
       Walk-forward eval: for each season s with at least `train_window` prior
       seasons of data, train on the previous `train_window` seasons and evaluate
       on s. Returns one row per (sport_key, test_season) with: n_matches,
       model_rps, bookmaker_rps, rps_gap, model_accuracy, has_edge.

       No data is fetched — all data must already be on disk under data/raw/.
       Raises FileNotFoundError if that's not the case.
       """
   ```
   Internally reuse `load_league` / `build_rolling_ratings` / `build_feature_matrix` exactly as `scripts/model_signals.py` does today. Train with `MatchPredictor(backend="catboost", calibrate=False)` — calibration comes in 7.2; this phase produces the **uncalibrated** baseline at higher resolution.
2. Add `scripts/eval_model_holdout.py` that calls it for every league in `LEAGUES` (skipping the ones with no data or <200 completed matches, matching `process_league`'s existing skip rules), concatenates the results, and:
   - Writes `logs/model_eval/holdout_uncalibrated.csv` (one row per (league, season)).
   - Writes `logs/model_eval/holdout_uncalibrated_summary.md` — a markdown table grouped by league with seasonal RPS gaps + the count of seasons where `has_edge=True`.
3. Add `tests/test_holdout.py` with one test that calls `rolling_holdout_eval("soccer_epl", train_window=3, since="2021")` on a small slice and asserts the returned DataFrame has at least one row and the expected columns.

**Acceptance.**
- `pytest -q tests/test_holdout.py` green.
- `python3 scripts/eval_model_holdout.py` prints a summary and exits 0 in <5 minutes on WSL.
- `logs/model_eval/holdout_uncalibrated_summary.md` exists, has at least 4 leagues × 2 seasons of rows, and **no row claims an edge unless the seasonal n_matches ≥ 100** (small-sample wins are noise; the bot must filter them in the summary).

**Stop condition.** If the leaderboard shows zero (league × season) cells with `has_edge=True` _and_ `n_matches ≥ 200`, log that fact and proceed — calibration can still help reliability even when point RPS is worse. Do not abort the sprint.

---

## Phase 7.2 — Fix the calibration bug + add isotonic calibration for CatBoost

**What.** `src/model/catboost_model.py:113` reads `if self.calibrate and not CATBOOST_AVAILABLE:` — meaning the `calibrate=True` flag is **silently ignored when CatBoost is the active backend**. Production today runs CatBoost with `calibrate=False` anyway (`scripts/model_signals.py:141`), so neither path is calibrating. Fix this and ship a calibrated path.

**How.**

1. In `src/model/catboost_model.py`, change `MatchPredictor.fit` to (when `self.calibrate=True` and backend is CatBoost) **either**:
   - **Option A (preferred):** split the training data into a fit slice (first 80% by `Date` order) and a calibration slice (last 20% by `Date` order, so calibration happens on the most recent data — closest to the production use case). Fit raw CatBoost on the fit slice; then fit `sklearn.isotonic.IsotonicRegression` per class on the calibration slice (one-vs-rest), storing three calibrators on the predictor. `predict_proba` applies them and re-normalises so the row sums to 1.
   - **Option B (fallback if Option A flattens probabilities too aggressively):** use `sklearn.calibration.CalibratedClassifierCV(estimator=catboost_model, cv=TimeSeriesSplit(n_splits=3), method="isotonic")`. Time-series CV is critical — never use plain `KFold` on football matches.
   Pick A first, fall back to B only if A's reliability diagram is worse on the held-out test (see 7.3 acceptance).
2. Persist calibrators alongside the model when used. (No need to disk-serialise — the calibrated `MatchPredictor` is rebuilt per league in `scripts/model_signals.py`. We just need it to round-trip in memory.)
3. Add `tests/test_calibration_fit.py`:
   - Builds a tiny synthetic 200-row dataset with known feature/outcome relationship.
   - Fits `MatchPredictor(backend="catboost", calibrate=True)`.
   - Asserts `predict_proba` rows still sum to 1 ± 1e-6.
   - Asserts that on this synthetic data, calibrated probabilities are **at least as accurate (Brier score) as uncalibrated** on a held-out 20% slice — within a tolerance of `+0.01` to allow for variance.
   - This test must skip cleanly if CatBoost is not installed (use `pytest.importorskip("catboost")`).

**Acceptance.**
- `pytest -q tests/test_calibration_fit.py` green.
- The grep `grep -n "calibrate and not CATBOOST_AVAILABLE" src/model/catboost_model.py` returns no matches.
- All other existing tests still pass: `pytest -q`.

---

## Phase 7.3 — Reliability diagrams + sliced eval

**What.** Visualise where the (now-calibrated) model is well-calibrated vs miscalibrated, by league and by predicted-probability bin.

**How.**

1. Add `src/model/reliability.py` with:
   - `reliability_curve(probs: pd.Series, outcomes: pd.Series, n_bins: int = 10) -> pd.DataFrame` — returns columns `bin_lo, bin_hi, mean_pred, empirical_freq, count`.
   - `brier_score(probs_df: pd.DataFrame, outcomes: pd.Series) -> float` — multiclass Brier (sum of squared error across the three outcome columns).
2. Extend `scripts/eval_model_holdout.py` (or add a sibling `eval_model_holdout_calibrated.py` — bot's choice, single file is fine) to also produce, for the **calibrated** model:
   - `logs/model_eval/holdout_calibrated.csv` (same shape as 7.1 but with `brier` column added).
   - `logs/model_eval/reliability_<sport_key>.csv` — one per league, `class, bin_lo, bin_hi, mean_pred, empirical_freq, count`.
   - `logs/model_eval/reliability.md` — a markdown table per league + per outcome class, plus a one-line verdict per league: `WELL_CALIBRATED` (max abs deviation < 0.04 in any bin with count ≥ 30), `MISCALIBRATED`, or `INSUFFICIENT_DATA`.
3. Add `tests/test_reliability.py` with one synthetic test asserting that a perfectly-calibrated input (probs = empirical frequency) produces near-zero deviation, and a deliberately-miscalibrated input produces deviation ≥ 0.1.

**Acceptance.**
- Calibrated eval's average `rps_gap` across all (league × season) cells with `n_matches ≥ 200` is **≤ uncalibrated average + 0.001** — i.e. calibration didn't make point accuracy materially worse.
- Calibrated eval's average `brier` is **≤ uncalibrated average** by any margin — i.e. calibration improved probabilistic accuracy somewhere.
- At least one league shows `WELL_CALIBRATED` post-calibration in `reliability.md`. If none do, that's a real signal — log it and proceed to 7.5; do not silently retry until something passes.

---

## Phase 7.4 — Regenerate `model_signals.json` (calibrated, side-by-side)

**What.** Produce a calibrated signal cache the live scanner _could_ consume, but don't flip it on yet.

**How.**

1. Patch `scripts/model_signals.py` to add a `--calibrate` flag (default off — the production cron path stays byte-identical). With `--calibrate`, set `MatchPredictor(backend="catboost", calibrate=True)` and write to `logs/model_signals_calibrated.json` (not the live `logs/model_signals.json`).
2. Run `python3 scripts/model_signals.py --calibrate` once on WSL. **No new data fetches** — the bot must skip leagues whose data isn't already on disk and log which were skipped.
3. Add `scripts/diff_model_signals.py` that loads both files and prints, per league:
   - n_signals in each.
   - Mean absolute prob shift across the three outcomes per matched key.
   - Top 10 fixtures by total absolute prob shift.
   - Histogram of shift magnitudes (just text bins, no plotting libs).

**Acceptance.**
- `logs/model_signals_calibrated.json` exists.
- `python3 scripts/diff_model_signals.py` runs in <30 s and prints a non-empty diff for at least 4 leagues.
- `logs/model_signals.json` is **byte-identical to its pre-sprint state** (capture with `sha256sum logs/model_signals.json` at the start of phase 7.0 and re-check at the end of 7.4 — the sprint must not have touched it).

---

## Phase 7.5 — Decision report (the deliverable)

**What.** A single short doc the human reads on Monday after the FDCO CLV backfill fires, deciding go/no-go on flipping the calibrated signals into production.

**How.** Write `docs/MODEL_EVAL_2026-05.md` with these sections, in this order, no preamble:

1. **TL;DR** (3 sentences max). Did calibration improve RPS / Brier / reliability? Should we flip? On what evidence?
2. **Numbers** — single table from `holdout_calibrated.csv` summarised: rows = league, columns = `n_seasons_evaluated, mean_rps_gap_uncal, mean_rps_gap_cal, mean_brier_uncal, mean_brier_cal, calibration_verdict`.
3. **Where the model has any edge** — bullet list of (league × season) cells where `has_edge=True` AND `n_matches ≥ 200`, post-calibration. If empty: say so and stop.
4. **Recommendation.** One of:
   - **FLIP** — replace `logs/model_signals.json` with `_calibrated.json` and document the mv command in the report. Justified by ≥1 league showing improved Brier without RPS degradation. Human runs the `mv` themselves; the bot does not.
   - **HOLD** — calibration helped reliability but no league shows a clear edge in raw accuracy yet; wait for ≥50 settled bets with CLV before reconsidering.
   - **STOP** — calibration made things worse on every metric. Don't ship; investigate (likely Option A vs B from 7.2 was the wrong call, or training window is too short on smaller leagues).
5. **What we did NOT touch.** Reaffirm: scanner code, cron, Azure paths, live `model_signals.json`. The reviewer should be able to revert the entire sprint with `git checkout main`.
6. **Open questions / follow-ups.** Bullet list — e.g. "Tennis + NBA aren't in this eval because we don't bet them," "Phase 8 Betfair auto-placement should consume `_calibrated.json` from day one regardless of the flip decision."

**Acceptance.**
- `docs/MODEL_EVAL_2026-05.md` exists, ≤ 200 lines, ends with a Recommendation that's one of FLIP / HOLD / STOP — no fence-sitting.
- A PR is open against `main` from `phase-7-model-eval-2026-05`. The PR description quotes the TL;DR.

---

## Baseline (7.0 result)

Ran `scripts/eval_model_baseline.py` on 2026-05-01. Train: seasons 1920/2021/2122 (1140 matches). Test: 2223 (380 matches).

| metric | value |
|---|---|
| n_matches | 380 |
| model_rps | 0.21911 |
| bookmaker_rps | 0.19902 |
| rps_vs_bookmaker | 0.02009 |
| model_accuracy | 0.5158 |
| has_edge | False |

Gap of 0.02009 is within expected range [0.005, 0.030] — baseline confirmed, data pipeline unchanged.

---

## Bot kickoff checklist

Before opening a single file, the bot should verify:

- [ ] `pytest -q` is green on `main` (your starting baseline).
- [ ] WSL-only: `pwd` ends in `/home/rfreire/projects/bets`.
- [ ] `git status` is clean and `git rev-parse --abbrev-ref HEAD` is **not** `main` — create the feature branch first.
- [ ] `ls data/raw/*.csv | wc -l` returns ≥ 80 (otherwise data dir is incomplete; do **not** `--download`, raise a clear error and stop).
- [ ] `ls logs/team_xg.json` exists.
- [ ] `python3 -c "import catboost"` succeeds. If not, `pip install catboost` (in `.venv` only, never system-wide).
- [ ] `sha256sum logs/model_signals.json` recorded somewhere — checked again at the end of 7.4.

## Bot exit checklist

- [ ] All five phases complete.
- [ ] `pytest -q` green (including the three new test files).
- [ ] PR open against `main`, not merged.
- [ ] `logs/model_signals.json` byte-identical to start.
- [ ] `crontab -l` byte-identical to start (`diff` it).
- [ ] No new entries in `requirements.txt`. New dev deps (if any) in `requirements-dev.txt`.
- [ ] `docs/MODEL_EVAL_2026-05.md` ends with a single explicit Recommendation.

---

## After the sprint (human-only — bot does not do these)

- Read `docs/MODEL_EVAL_2026-05.md`.
- Decide whether to merge the PR (calibration logic + eval scripts ship regardless of the FLIP/HOLD/STOP call — they're harmless to have).
- If FLIP: `mv logs/model_signals_calibrated.json logs/model_signals.json` on **WSL only** first, observe one weekend, then decide on Pi.
- Re-evaluate after 50 settled bets with `clv_pct` populated — that's when this sprint's recommendation gets validated against money. CLV is the gate (per CLAUDE.md), not RPS.
