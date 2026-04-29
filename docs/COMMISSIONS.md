# Commission Rates by Bookmaker

Used by `src/betting/commissions.py` to compute net edges and commission-adjusted Kelly stakes.

**Verification date:** 2026-04-29

## Exchanges (commission on net winnings)

| Book (API key) | Commission | Source |
|---|---|---|
| `betfair_ex_uk` | 5% (UK Market Base Rate) | https://www.betfair.com/aboutUs/Betfair.Charges/ |
| `smarkets` | 2% | https://smarkets.com/betting-exchange/commission |
| `matchbook` | 2% | https://www.matchbook.com/aboutus/commission |

## Sportsbooks (commission baked into odds; margin already removed by Shin de-vigging)

| Book (API key) | Commission | Notes |
|---|---|---|
| `pinnacle` | 0% | Low-margin book; used as consensus anchor |
| `betfair_sb_uk` | 0% | Fixed-odds sportsbook, distinct from exchange |
| `betfred_uk` | 0% | |
| `williamhill` | 0% | |
| `coral` | 0% | |
| `ladbrokes_uk` | 0% | |
| `skybet` | 0% | |
| `paddypower` | 0% | |
| `boylesports` | 0% | |
| `betvictor` | 0% | |
| `betway` | 0% | |
| `leovegas` | 0% | |
| `casumo` | 0% | |
| `virginbet` | 0% | |
| `livescorebet` | 0% | |
| `sport888` | 0% | 888Sport |
| `grosvenor` | 0% | |

## How commission affects edge and stakes

For exchange bets, the effective decimal odds after commission are:

```
effective_odds = 1 + (decimal_odds - 1) * (1 - commission_rate)
```

Example: Betfair at 3.00 → effective odds = 1 + 2.0 × 0.95 = 2.90

The net edge (what the scanner reports as `edge`) is:

```
net_edge = consensus_prob - 1 / effective_odds
```

The `edge_gross` column stores the pre-commission edge for comparison.

Kelly stakes are computed on effective odds, so Betfair bets will have smaller suggested stakes than equivalent Smarkets bets at the same gross odds.

## Notes

- Betfair's UK MBR (Market Base Rate) is 5% as of 2026. Premium charges apply for highly profitable accounts but are excluded here.
- Commission rates can change; re-verify this table quarterly.
