# Strategy Comparison

Sorted by average CLV descending. Only rows with a Pinnacle close prob contribute to CLV stats.
Run `python3 scripts/compare_strategies.py` to refresh.

> **Eval-window filter:** showing CURRENT config window per variant only (rows whose `strategy_config_hash` matches the most recent scan). Pass `--all-history` to include older config windows / pre-R.11 rows.

> Variants with hidden older-window rows: `I_power_devig` (62/87), `J_sharp_weighted` (40/57), `L_quarter_kelly` (35/50), `M_min_prob_15` (29/42), `N_competitive_only` (19/29), `O_kaunitz_classic` (54/70), `P_max_odds_shopping` (8/9) — format: `current/total`.

> **Sample size note.** Variants with `<10` CLV bets in this report are indicative only. Per `RESEARCH_NOTES_2026-04.md` §6, graduation requires ≥30 CLV bets across ≥3 weekends with positive Avg CLV CI bracket.

> Variants with 0 bets this period are listed for completeness; if a variant you expect to fire shows 0, check its filter wiring (e.g. `K_draw_bias` requires `logs/team_xg.json` and an alias-resolved team name).

| Strategy | Bets | CLV bets | Avg CLV ± 95% CI | Med CLV | CLV >0 % | Drift→you % | Avg Edge | Top books |
|---|---|---|---|---|---|---|---|---|
| [low n] A_production | 1 | 0 | — | — | — | — | 14.14% | — |
| [low n] C_loose | 2 | 0 | — | — | — | — | 8.11% | — |
| [low n] D_pinnacle_only | 3 | 0 | — | — | — | — | 5.19% | — |
| [low n] F_model_primary | 144 | 0 | — | — | — | — | -0.97% | — |
| [low n] G_proportional | 2 | 0 | — | — | — | — | 7.43% | — |
| [low n] H_no_pinnacle | 1 | 0 | — | — | — | — | 14.51% | — |
| [low n] I_power_devig | 62 | 0 | — | — | — | — | -9.94% | — |
| [low n] J_sharp_weighted | 40 | 0 | — | — | — | — | -7.49% | — |
| [low n] L_quarter_kelly | 35 | 0 | — | — | — | — | -8.63% | — |
| [low n] M_min_prob_15 | 29 | 0 | — | — | — | — | -10.55% | — |
| [low n] N_competitive_only | 19 | 0 | — | — | — | — | -16.22% | — |
| [low n] O_kaunitz_classic | 54 | 0 | — | — | — | — | 6.11% | — |
| [low n] P_max_odds_shopping | 8 | 0 | — | — | — | — | 0.90% | — |
| [low n] B_strict | 0 | — | — | — | — | — | — | — |
| [low n] E_exchanges_only | 0 | — | — | — | — | — | — | — |
| [low n] K_draw_bias | 0 | — | — | — | — | — | — | — |

*95% CI is `±1.96·σ/√n`. A variant whose CI bracket includes 0 has not yet shown a statistically distinguishable signal.*

## CLV by consensus-prob bucket (favourite-longshot bias check)

Pooled across all paper strategies, deduped by `(kickoff, home, away, market, line, side, book)`. Bucketed by Shin-devigged consensus probability of the side bet on. Persistent negative CLV in a single bucket = favourite-longshot bias signal in our flow.
Sample: 0 unique bets with CLV.

| Bucket | Bets | Avg CLV | CLV >0 % |
|---|---|---|---|
| 0–20% (longshots) | 0 | — | — |
| 20–35% | 0 | — | — |
| 35–50% | 0 | — | — |
| 50–65% | 0 | — | — |
| 65–80% | 0 | — | — |
| 80%+ (favourites) | 0 | — | — |

*Generated: see `logs/paper/` and `logs/bets.csv`.*
