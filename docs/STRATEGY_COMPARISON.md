# Strategy Comparison

Sorted by average CLV descending. Only rows with a Pinnacle close prob contribute to CLV stats.
Run `python3 scripts/compare_strategies.py` to refresh.

> **Sample size note.** Variants with `<10` CLV bets in this report are indicative only. Per `RESEARCH_NOTES_2026-04.md` §6, graduation requires ≥30 CLV bets across ≥3 weekends with positive Avg CLV CI bracket.

> Variants with 0 bets this period are listed for completeness; if a variant you expect to fire shows 0, check its filter wiring (e.g. `K_draw_bias` requires `logs/team_xg.json` and an alias-resolved team name).

| Strategy | Bets | CLV bets | Avg CLV ± 95% CI | Med CLV | CLV >0 % | Avg Edge | Top books |
|---|---|---|---|---|---|---|---|
| [low n] A_production | 13 | 0 | — | — | — | 7.03% | — |
| [low n] C_loose | 37 | 0 | — | — | — | 3.90% | — |
| [low n] D_pinnacle_only | 22 | 0 | — | — | — | 5.70% | — |
| [low n] E_exchanges_only | 12 | 0 | — | — | — | 7.36% | — |
| [low n] F_model_primary | 100 | 0 | — | — | — | 0.59% | — |
| [low n] G_proportional | 18 | 0 | — | — | — | 5.11% | — |
| [low n] H_no_pinnacle | 13 | 0 | — | — | — | 7.03% | — |
| [low n] B_strict | 0 | — | — | — | — | — | — |
| [low n] I_power_devig | 0 | — | — | — | — | — | — |
| [low n] L_quarter_kelly | 0 | — | — | — | — | — | — |
| [low n] M_min_prob_15 | 0 | — | — | — | — | — | — |
| [low n] N_competitive_only | 0 | — | — | — | — | — | — |
| [low n] O_kaunitz_classic | 0 | — | — | — | — | — | — |
| [low n] P_max_odds_shopping | 0 | — | — | — | — | — | — |
| [low n] J_sharp_weighted | 0 | — | — | — | — | — | — |
| [low n] K_draw_bias | 0 | — | — | — | — | — | — |

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
