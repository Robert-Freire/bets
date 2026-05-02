# Commission Rates by Bookmaker

**Source of truth:** the `books` section of `config.json`. `src/betting/commissions.py`
loads from there at import time. `UK_LICENSED_BOOKS` and `EXCHANGE_BOOKS` in
`src/betting/strategies.py` are derived from the same config at module load.

## Book types

- **Exchanges** (`type: exchange`) — commission charged on net winnings; rate stored in `config.json`.
- **Sportsbooks** (`type: sportsbook`) — commission baked into odds; Shin de-vigging removes it.

## Verification sources (exchanges only)

| Book (API key) | Source |
|---|---|
| `betfair_ex_uk` | https://www.betfair.com/aboutUs/Betfair.Charges/ |
| `smarkets` | https://smarkets.com/betting-exchange/commission |
| `matchbook` | https://www.matchbook.com/aboutus/commission |

**Verification date:** 2026-04-29. Re-verify quarterly — exchange rates can change.

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
