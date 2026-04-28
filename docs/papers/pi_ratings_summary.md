# Constantinou & Fenton (2013) — Pi-Ratings

**Paper:** "Determining the Level of Ability of Football Teams by Dynamic Ratings Based on the Relative Discrepancies in Scores Between Adversaries"  
**Authors:** Anthony Constantinou, Norman Fenton (Queen Mary University London)

## Core Idea

Dynamic rating system updated after each match using **goal-score discrepancy**. Unlike Elo (win/loss only), pi-ratings incorporate **margin of victory** — more information per match.

## Rating Structure

Each team has **two ratings**:
- `h` = home rating
- `a` = away rating

## Update Formula

After a match where team i plays home vs team j:

```
# Expected goal difference (from home team's perspective)
expected_diff = h_i - a_j   (in rating units, converted to goals)

# Actual goal difference
actual_diff = home_goals - away_goals

# Error
error = actual_diff - expected_diff

# Update ratings
h_i += lr * error
a_i += lr * error * 0.5     (away rating updated less aggressively)

a_j -= lr * error
h_j -= lr * error * 0.5
```

**Learning rate:** `lr ≈ 0.06` (tuned empirically)

## Converting Ratings to Probabilities

Rating difference → expected goal difference → Poisson probabilities (feed into Dixon-Coles).

Or use logistic regression on rating difference directly:
```
P(home win) = sigmoid(k * (h_i - a_j))
```

## Key Properties

- Ratings converge after ~5-6 matches per team per season
- Handles promoted/relegated teams by initializing at league average
- Outperforms Elo significantly because margin of victory is more informative
- Validated over **5 EPL seasons (2007-08 to 2011-12)** showing positive ROI vs bookmakers

## Practical Notes

- Initialize new teams at 0 (league average)
- Run a warm-up period of 1 season before using for betting
- Separate home/away ratings capture the structural home advantage better than a single parameter
- Can be used directly as features in XGBoost/CatBoost
