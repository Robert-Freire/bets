# Strategy Comparison

Sorted by average CLV descending. Only rows with a Pinnacle close prob contribute to CLV stats.
Run `python3 scripts/compare_strategies.py` to refresh.

| Strategy | Bets | CLV bets | Avg CLV | CLV >0 % | Avg Edge | Top books |
|---|---|---|---|---|---|---|
| A_production | 13 | 0 | — | — | 7.03% | — |
| B_strict | 5 | 0 | — | — | 12.05% | — |
| C_loose | 34 | 0 | — | — | 4.03% | — |
| D_pinnacle_only | 22 | 0 | — | — | 5.70% | — |
| E_exchanges_only | 12 | 0 | — | — | 7.36% | — |
| F_model_primary | 100 | 0 | — | — | 0.59% | — |
| G_proportional | 18 | 0 | — | — | 5.11% | — |
| H_no_pinnacle | 13 | 0 | — | — | 7.03% | — |

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
