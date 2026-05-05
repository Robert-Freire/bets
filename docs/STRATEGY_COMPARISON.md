# Strategy Comparison

Sorted by average CLV descending. Only rows with a Pinnacle close prob contribute to CLV stats.
Run `python3 scripts/compare_strategies.py` to refresh.

> **Data source:** Azure SQL `paper_bets` table.

> **Sample size note.** Variants with `<10` CLV bets in this report are indicative only. Per `RESEARCH_NOTES_2026-04.md` §6, graduation requires ≥30 CLV bets across ≥3 weekends with positive Avg CLV CI bracket.

> Variants with 0 bets this period are listed for completeness; if a variant you expect to fire shows 0, check its filter wiring.

| Strategy | Bets | CLV bets | Avg CLV ± 95% CI | Med CLV | CLV >0 % | Avg Edge | Settled | Win % | ROI % |
|---|---|---|---|---|---|---|---|---|---|
| D_pinnacle_only ★ | 88 | 32 | 7.30% ± 24.45% | -5.09% | 34.38% | -6.80% | 17 | 47% | +44.0% |
| F_model_primary | 393 | 211 | -0.88% ± 4.07% | -2.10% | 36.97% | -0.87% | 190 | 38% | -85.6% |
| C_loose | 174 | 73 | -5.23% ± 3.77% | -2.61% | 38.36% | -4.87% | 50 | 44% | +30.5% |
| [low n] B_strict | 19 | 3 | -8.46% ± 11.02% | -6.80% | 33.33% | -14.96% | 2 | — | — |
| G_proportional | 65 | 27 | -10.50% ± 5.48% | -5.14% | 11.11% | -7.88% | 23 | 35% | — |
| [low n] N_competitive_only | 29 | 4 | -16.63% ± 2.27% | -16.73% | 0.00% | -17.87% | 4 | — | — |
| [low n] I_power_devig | 86 | 7 | -17.64% ± 19.12% | -16.73% | 28.57% | -12.07% | 7 | 57% | +48.6% |
| [low n] J_sharp_weighted | 56 | 7 | -19.69% ± 18.27% | -16.73% | 14.29% | -10.26% | 7 | 43% | +41.3% |
| [low n] A_production | 62 | 9 | -19.87% ± 13.45% | -18.92% | 11.11% | -9.78% | 8 | 25% | +21.8% |
| [low n] H_no_pinnacle | 61 | 9 | -19.87% ± 13.45% | -18.92% | 11.11% | -9.94% | 8 | 25% | +21.8% |
| [low n] E_exchanges_only | 47 | 6 | -22.49% ± 20.33% | -18.92% | 16.67% | -12.93% | 5 | 20% | +21.8% |
| [low n] L_quarter_kelly | 49 | 6 | -24.17% ± 18.96% | -16.73% | 0.00% | -11.75% | 6 | 33% | +21.8% |
| [low n] M_min_prob_15 | 41 | 6 | -24.17% ± 18.96% | -16.73% | 0.00% | -14.15% | 6 | 33% | +21.8% |
| [low n] P_max_odds_shopping | 8 | 2 | -44.22% ± 53.89% | -44.22% | 0.00% | -1.29% | 2 | — | — |
| [low n] O_kaunitz_classic | 61 | 1 | -71.72% | -71.72% | 0.00% | 5.99% | 1 | — | — |
| [low n] K_draw_bias | 0 | — | — | — | — | — | — | — | — |

*95% CI is `±1.96·σ/√n`. A variant whose CI bracket includes 0 has not yet shown a statistically distinguishable signal.*

## CLV by sport

A_production shown as baseline for any sport with ≥1 CLV bet; other variants shown only where n_with_clv ≥ 10.

| Sport | Variant | Bets | CLV bets | Avg CLV | CLV >0 % |
|---|---|---|---|---|---|
| EPL | F_model_primary | 106 | 52 | -6.44% | 26.92% |
| Bundesliga | F_model_primary | 57 | 31 | -1.82% | 22.58% |
| Bundesliga | C_loose | 32 | 11 | -6.27% | 18.18% |
| Bundesliga 2 | D_pinnacle_only | 15 | 15 | -4.35% | 33.33% |
| Bundesliga 2 | C_loose | 15 | 15 | -8.26% | 26.67% |
| Bundesliga 2 | A_production | 5 | 5 | -12.65% | 20.00% |
| Championship | F_model_primary | 34 | 32 | -0.20% | 43.75% |
| Championship | C_loose | 12 | 12 | -6.57% | 50.00% |
| Championship | A_production | 2 | 2 | -16.53% | 0.00% |
| La Liga | A_production | 14 | 1 | -71.72% | 0.00% |
| Ligue 1 | F_model_primary | 74 | 33 | -1.25% | 57.58% |
| Ligue 1 | C_loose | 37 | 12 | -2.76% | 58.33% |
| Serie A | F_model_primary | 121 | 63 | 4.03% | 38.10% |
| Serie A | C_loose | 28 | 12 | -4.80% | 50.00% |
| Serie A | A_production | 9 | 1 | -10.77% | 0.00% |

## CLV by confidence

Rows where n_with_clv ≥ 5 per (variant, confidence) tier.

| Confidence | Variant | Bets | CLV bets | Avg CLV | CLV >0 % |
|---|---|---|---|---|---|
| HIGH | A_production | 38 | 8 | -13.38% | 12.50% |
| HIGH | C_loose | 126 | 71 | -5.61% | 38.03% |
| HIGH | D_pinnacle_only | 67 | 31 | -4.99% | 32.26% |
| HIGH | E_exchanges_only | 30 | 5 | -12.65% | 20.00% |
| HIGH | F_model_primary | 329 | 210 | -2.73% | 36.67% |
| HIGH | G_proportional | 48 | 26 | -8.13% | 11.54% |
| HIGH | H_no_pinnacle | 37 | 8 | -13.38% | 12.50% |
| HIGH | I_power_devig | 49 | 6 | -8.63% | 33.33% |
| HIGH | J_sharp_weighted | 31 | 6 | -11.02% | 16.67% |
| HIGH | L_quarter_kelly | 27 | 5 | -14.66% | 0.00% |
| HIGH | M_min_prob_15 | 22 | 5 | -14.66% | 0.00% |

## CLV by market

Rows where n_with_clv ≥ 5 per (variant, market).

| Market | Variant | Bets | CLV bets | Avg CLV | CLV >0 % |
|---|---|---|---|---|---|
| h2h | A_production | 62 | 9 | -19.87% | 11.11% |
| h2h | C_loose | 174 | 73 | -5.23% | 38.36% |
| h2h | D_pinnacle_only | 88 | 32 | 7.30% | 34.38% |
| h2h | E_exchanges_only | 47 | 6 | -22.49% | 16.67% |
| h2h | F_model_primary | 393 | 211 | -0.88% | 36.97% |
| h2h | G_proportional | 65 | 27 | -10.50% | 11.11% |
| h2h | H_no_pinnacle | 61 | 9 | -19.87% | 11.11% |
| h2h | I_power_devig | 86 | 7 | -17.64% | 28.57% |
| h2h | J_sharp_weighted | 56 | 7 | -19.69% | 14.29% |
| h2h | L_quarter_kelly | 49 | 6 | -24.17% | 0.00% |
| h2h | M_min_prob_15 | 41 | 6 | -24.17% | 0.00% |

## CLV by model signal

Rows where n_with_clv ≥ 5 per (variant, model-signal bucket). `agrees` = model edge > 0; `disagrees` = model edge ≤ 0; `no_signal` = `?` or missing.

| Signal | Variant | Bets | CLV bets | Avg CLV | CLV >0 % |
|---|---|---|---|---|---|
| disagrees | A_production | 26 | 5 | -16.25% | 0.00% |
| agrees | C_loose | 40 | 11 | -8.26% | 36.36% |
| disagrees | C_loose | 80 | 37 | -5.07% | 35.14% |
| no_signal | C_loose | 54 | 25 | -4.15% | 44.00% |
| agrees | D_pinnacle_only | 22 | 5 | 80.95% | 60.00% |
| disagrees | D_pinnacle_only | 36 | 11 | -10.76% | 27.27% |
| no_signal | D_pinnacle_only | 30 | 16 | -3.30% | 31.25% |
| agrees | F_model_primary | 393 | 211 | -0.88% | 36.97% |
| disagrees | G_proportional | 35 | 20 | -8.93% | 10.00% |
| no_signal | G_proportional | 20 | 5 | -15.78% | 20.00% |
| disagrees | H_no_pinnacle | 24 | 5 | -16.25% | 0.00% |

## CLV by consensus-prob bucket (favourite-longshot bias check)

Pooled across all paper strategies, deduped by `(kickoff, home, away, market, line, side, book)`. Bucketed by Shin-devigged consensus probability of the side bet on. Persistent negative CLV in a single bucket = favourite-longshot bias signal in our flow.
Sample: 96 unique bets with CLV.

| Bucket | Bets | Avg CLV | CLV >0 % |
|---|---|---|---|
| 0–20% (longshots) | 23 | 16.34% | 39.13% |
| 20–35% | 34 | -2.24% | 26.47% |
| 35–50% | 18 | -7.83% | 38.89% |
| 50–65% | 11 | -4.98% | 36.36% |
| 65–80% | 9 | -0.15% | 66.67% |
| 80%+ (favourites) | 1 | -71.72% | 0.00% |

*Generated from Azure SQL `paper_bets` table.*
