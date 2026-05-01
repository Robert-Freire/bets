# Model Evaluation — May 2026

## TL;DR

Isotonic calibration (Option A: temporal 80/20 split) did not improve model accuracy: EPL RPS gap worsened (0.0171→0.0182) and EPL Brier worsened (0.6083→0.6099). Calibration produced implausibly large probability shifts (mean 0.21–0.29 per fixture, max 0.73), indicating the 20%-slice calibrator is fitting noise. Do not flip; the model has no demonstrated edge over the market on any league with measurable odds.

## Numbers

| league | n_seasons_eval | mean_rps_gap_uncal | mean_rps_gap_cal | mean_brier_uncal | mean_brier_cal | calibration_verdict |
|---|---|---|---|---|---|---|
| soccer_epl | 9 | 0.01711 | 0.01821 | 0.60831 | 0.60990 | MISCALIBRATED |
| soccer_germany_bundesliga | 9 | n/a¹ | n/a¹ | 0.63601 | 0.63688 | MISCALIBRATED |
| soccer_italy_serie_a | 9 | n/a¹ | n/a¹ | 0.62363 | 0.61399 | MISCALIBRATED |
| soccer_efl_champ | 9 | n/a¹ | n/a¹ | 0.66787 | 0.65746 | MISCALIBRATED |
| soccer_france_ligue_one | 9 | n/a¹ | n/a¹ | 0.63859 | 0.63252 | MISCALIBRATED |
| soccer_germany_bundesliga2 | 9 | n/a¹ | n/a¹ | 0.68996 | 0.66594 | MISCALIBRATED |

¹ Non-EPL data files do not include `avg_odds_*` columns; bookmaker RPS cannot be computed. Edge comparison only possible for EPL.

Brier improved with calibration in 4 of 6 leagues (Serie A, Championship, Ligue 1, Bundesliga 2), but worsened for EPL and Bundesliga — the two leagues with the highest xG merge rates (91.9% and 71.1%). No league reached `WELL_CALIBRATED` status (max abs deviation ≥ 0.04 in every qualifying bin).

## Where the model has any edge

No (league × season) cell shows `has_edge=True` AND `n_matches ≥ 200` post-calibration.

For EPL (the only league where bookmaker RPS is measurable), every one of the 9 evaluated seasons shows the model RPS strictly above the bookmaker RPS — the market is consistently sharper.

## Recommendation

**STOP** — calibration made accuracy worse on EPL (the most data-rich league with xG), and produced implausibly large probability swings (mean shift 0.21–0.29 total abs per fixture, max 0.73). These magnitudes indicate the 20%-slice isotonic calibrators are fitting noise, not signal. Do not ship `model_signals_calibrated.json`.

Root-cause hypothesis: the calibration slice (~20% of 3 training seasons ≈ ~200 matches) is too small to reliably fit isotonic regression for 3 classes simultaneously. The Ligue 1/Championship/Bundesliga 2 Brier improvements are marginal (1–3%) and do not overcome EPL degradation.

Suggested follow-up before reconsidering calibration:
- Switch to Option B (`CalibratedClassifierCV` with `TimeSeriesSplit(n_splits=3)`) — requires more compute but uses more data for calibration fitting.
- Or increase the calibration window: fit isotonic on the last full season rather than the last 20% of training data.
- Add `avg_odds_*` columns to non-EPL league loaders so we can measure bookmaker RPS for all 6 leagues.

## What we did NOT touch

- `scripts/scan_odds.py` — no changes
- `scripts/model_signals.py` production path — `calibrate=False` default is unchanged; the `--calibrate` flag is off-by-default
- `logs/model_signals.json` — byte-identical (sha256: `6989aa590e8a802f8fbc457b4f2cd5e0b78c30dc2ae0d9edb2eb7760315ebfc2`)
- Crontab on WSL or Pi — untouched
- Azure SQL / Azure Blob — no writes
- `requirements.txt` — no new entries

Revert the entire sprint: `git checkout main`.

## Open questions / follow-ups

- Non-EPL odds coverage: `load_league()` does not compute `avg_odds_*` for non-EPL leagues; edge is unmeasurable for 5/6 leagues in this eval.
- Tennis + NBA are excluded — not in scope (CatBoost model is football-only).
- Phase 8 Betfair auto-placement should consume the **uncalibrated** `model_signals.json` from day one; do not use `_calibrated.json` until a future sprint validates calibration.
- CLV gate still applies: once ≥50 settled bets have `clv_pct` populated, revisit whether the 2–3% model-filtered notification path is adding value beyond pure Kaunitz consensus.
- The calibration code (Option A + Option B paths in `catboost_model.py`) is harmless to have in tree — it ships regardless of this STOP decision and will be the starting point for the next calibration attempt.
