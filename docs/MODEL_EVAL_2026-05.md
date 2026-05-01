# Model Evaluation — May 2026

## TL;DR

Isotonic calibration (Option A: temporal 80/20 split) improved overall model accuracy across all 6 leagues: overall RPS gap fell from 0.01990 to 0.01803, overall Brier fell from 0.64406 to 0.63613. 4 of 6 leagues improved on both metrics; EPL and Bundesliga degraded slightly. No league has edge over the bookmaker yet. Do not flip now — wait for ≥50 settled bets with CLV before reconsidering.

## Numbers

| league | n_seasons_eval | mean_rps_gap_uncal | mean_rps_gap_cal | mean_brier_uncal | mean_brier_cal | calibration_verdict |
|---|---|---|---|---|---|---|
| soccer_epl | 9 | 0.01706 | 0.01821 | 0.60831 | 0.60990 | MISCALIBRATED |
| soccer_germany_bundesliga | 9 | 0.02244 | 0.02476 | 0.63602 | 0.63691 | MISCALIBRATED |
| soccer_italy_serie_a | 9 | 0.02013 | 0.01927 | 0.62358 | 0.61404 | MISCALIBRATED |
| soccer_efl_champ | 9 | 0.01806 | 0.01484 | 0.66785 | 0.65750 | MISCALIBRATED |
| soccer_france_ligue_one | 9 | 0.02006 | 0.01868 | 0.63856 | 0.63247 | MISCALIBRATED |
| soccer_germany_bundesliga2 | 9 | 0.02165 | 0.01242 | 0.69001 | 0.66593 | MISCALIBRATED |
| **OVERALL** | | **0.01990** | **0.01803** | **0.64406** | **0.63613** | |

Calibration improved RPS and Brier in 4/6 leagues (Serie A, Championship, Ligue 1, Bundesliga 2); EPL and Bundesliga degraded slightly. No league reached `WELL_CALIBRATED` status. All bookmaker_rps values are now populated for all 6 leagues — the avg_odds gap in `load_league` for non-EPL was fixed in this sprint.

## Where the model has any edge

No (league × season) cell shows `has_edge=True` AND `n_matches ≥ 200` post-calibration.

All 6 leagues show positive rps_gap in every evaluated season — the market is consistently sharper than the model. Calibration narrows the gap in 4/6 leagues but does not close it.

## Recommendation

**HOLD** — calibration improved overall probabilistic accuracy (RPS gap 0.01990→0.01803, Brier 0.64406→0.63613) but no league shows a clear edge over the market yet. EPL and Bundesliga (the two leagues with the most xG coverage) both degrade slightly with calibration, suggesting the 20% calibration slice is adding noise where xG features already do the work. The calibrated `model_signals_calibrated.json` is preferable on aggregate but not unambiguously so on the primary scanning leagues.

Wait for ≥50 settled bets with `clv_pct` populated, then re-evaluate. If avg CLV is positive across the model-filtered subset, flip.

To flip when ready (human-only):
```bash
mv logs/model_signals_calibrated.json logs/model_signals.json
```

## What we did NOT touch

- `scripts/scan_odds.py` — no changes
- `scripts/model_signals.py` production path — `calibrate=False` default is unchanged; the `--calibrate` flag is off-by-default
- `logs/model_signals.json` — byte-identical (sha256: `6989aa590e8a802f8fbc457b4f2cd5e0b78c30dc2ae0d9edb2eb7760315ebfc2`)
- Crontab on WSL or Pi — untouched
- Azure SQL / Azure Blob — no writes
- `requirements.txt` — no new entries

Revert the entire sprint: `git checkout main`.

## Open questions / follow-ups

- EPL and Bundesliga degrade with calibration: both have the best xG coverage (91.9% and 71.1%); calibration may add noise where xG features already constrain the probabilities well. Option B (`CalibratedClassifierCV` with `TimeSeriesSplit`) would use more data for calibration and is worth trying if HOLD persists after CLV review.
- Probability shift magnitudes (mean 0.21–0.29 per fixture, max 0.73) are large; inspect per-bin reliability curves before the flip decision.
- Tennis + NBA excluded — model is football-only.
- Phase 8 Betfair auto-placement: use the uncalibrated `model_signals.json` for now; revisit after CLV gate.
- CLV gate: ≥50 settled bets with `clv_pct` populated is the trigger to re-evaluate this HOLD decision.
