# Kaunitz, Zhong & Kreiner (2017) — Beating the Bookies With Their Own Numbers

**arXiv:** 1710.02824  
**Authors:** Lisandro Kaunitz, Shenjun Zhong, Javier Kreiner

## Core Idea

Don't build a competing prediction model. Instead, use the **consensus of bookmaker odds** as a proxy for true probability. When one bookmaker's odds deviate significantly from the market consensus, that bookmaker has mispriced the bet.

## Strategy

1. Collect closing odds from many bookmakers for each match outcome
2. Compute **average implied probability** across all bookmakers (= market consensus)
3. Find bookmakers offering odds significantly above the consensus (overpriced outcome)
4. Bet on those outcomes using **Kelly criterion** staking

## Results

- **Historical simulation (10 years, 479,440 matches, 818 leagues):** +3.5% ROI over 56,435 bets
- **Live real-money (5 months, 265 bets):** +8.5% ROI
- Statistically significant positive returns in both phases

## Critical Warning

Once researchers began winning real money, bookmakers **restricted or banned their accounts**. The paper documents that the industry compensates for market inefficiencies through discriminatory account management, not model improvement.

**Implication:** Any working strategy has a limited lifespan per account/bookmaker. Need multiple soft bookmakers, exchanges, or betting brokers (Betfair, Pinnacle for line reference only).

## Key Formula

```
# Strip bookmaker margin (vig) from raw odds
raw_implied_prob = 1 / odds
margin = sum(raw_implied_prob for all outcomes) - 1
fair_prob = raw_implied_prob / (1 + margin)

# Consensus = average fair_prob across bookmakers
consensus_prob = mean(fair_prob_across_books)

# Value = bookmaker offers better odds than consensus implies
value_bet = bookmaker_odds > 1 / consensus_prob
```

## Staking

- Half-Kelly (0.5x) was most robust
- Full Kelly too aggressive given model uncertainty
