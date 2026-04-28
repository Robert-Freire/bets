# Premier League Value Betting System — Approach

## Goal

Build a soccer match outcome prediction model for the English Premier League that:
1. Estimates true win/draw/loss probabilities
2. Compares them against bookmaker odds to find value bets
3. Sizes bets using Kelly criterion

Realistic expected edge: **1–8% ROI** on value bets, assuming best-odds access across soft bookmakers.

---

## Research Foundation

### Core Papers

| Paper | Key Contribution |
|---|---|
| Dixon & Coles (1997) | Poisson model with ρ low-score correction. Still competitive. |
| Constantinou & Fenton (2013) | Pi-ratings: dynamic goal-difference-weighted team strength |
| Koopman & Lit (2015) | State-space dynamic Poisson on EPL; showed positive returns |
| Kaunitz, Zhong & Kreiner (2017) | Consensus odds as benchmark; bookmakers restrict winning accounts |
| Hubáček, Sourek & Železný (2022) | 40-year review: Berrar ratings + Weibull best overall |
| Yeung, Bunker et al. (2023) | CatBoost + pi-ratings competitive with deep learning |

### Key Research Findings

1. **xG beats raw goals** as team strength signal — corrects for shot quality and luck
2. **Time-varying parameters mandatory** — static ratings go stale within weeks
3. **Dixon-Coles ρ correction** — without it, draws and 0-0 are systematically underpriced
4. **Bookmaker closing line = benchmark** — Pinnacle closing odds are the hardest target
5. **Realistic ROI is 1–8%** — accounts get restricted, so target soft books
6. **Kelly 0.5x** — half-Kelly is more robust than full Kelly in practice

---

## Architecture

### Pipeline

```
Raw Data → Feature Engineering → Pi-Ratings → Dixon-Coles / XGBoost → Calibration → Value Filter → Kelly Sizing
```

### Step 1: Data Collection

**Sources:**
- `football-data.co.uk` — Match results + betting odds (CSV, free, EPL from 1993)
  - URL pattern: `https://www.football-data.co.uk/mmz4281/{season}/E0.csv`
  - Key columns: Date, HomeTeam, AwayTeam, FTHG, FTAG, FTR, B365H, B365D, B365A
- `understat.com` — xG data per match (EPL from 2014/15)
  - Export as JSON/CSV per season

**Seasons to use:** 2014/15 → present (xG data available)

### Step 2: Pi-Ratings (Team Strength)

Dynamic rating updated after every match based on goal difference.

**Formula:**
```
Expected goal diff = f(home_rating, away_rating, home_advantage)
Actual goal diff = FTHG - FTAG

Error = actual - expected

home_attack += lr * error
home_defense -= lr * error  
away_attack -= lr * error
away_defense += lr * error
```

Parameters: learning rate `lr ≈ 0.06`, decay per season, home advantage `≈ +0.4 goals`

**Output:** 4 values per team: attack_home, attack_away, defense_home, defense_away

### Step 3: Dixon-Coles Poisson Model

Model goals scored as Poisson distributions:
```
Home goals ~ Poisson(λ_h)  where  λ_h = home_attack * away_defense * home_advantage
Away goals ~ Poisson(λ_a)  where  λ_a = away_attack * home_defense
```

**ρ correction** on low-score outcomes (0-0, 1-0, 0-1, 1-1):
```
P(0,0) *= (1 - λ_h * λ_a * ρ)
P(1,0) *= (1 + λ_a * ρ)
P(0,1) *= (1 + λ_h * ρ)
P(1,1) *= (1 - ρ)
```

Fit `ρ ≈ -0.13` via maximum likelihood.

Integrate over score matrix (0–10 goals each) → P(home win), P(draw), P(away win)

### Step 4: XGBoost Layer (Optional Enhancement)

Features:
- Pi-rating differentials (attack, defense, home/away)
- Rolling xG for/against (last 5, 10 matches)
- Form (points per game last 5/10)
- Days rest
- Head-to-head record

Target: W/D/L outcome. Probabilities from `predict_proba`.

### Step 5: Calibration

- Compute **Ranked Probability Score (RPS)** vs bookmaker odds
- Apply **Platt scaling** or **isotonic regression** to calibrate output probabilities
- Benchmark: if our RPS ≥ Pinnacle's RPS, we have no edge

### Step 6: Value Bet Detection

```
value = our_probability - (1 / bookmaker_odds)
```

Place bet only when `value > threshold` (e.g., 0.03 = 3% edge minimum).

Use **best odds** across multiple bookmakers (line shopping).

### Step 7: Kelly Criterion Staking

```
kelly_fraction = (our_prob * bookmaker_odds - 1) / (bookmaker_odds - 1)
bet_size = 0.5 * kelly_fraction * bankroll   # half-Kelly for safety
```

---

## Evaluation Metrics

| Metric | Description |
|---|---|
| RPS (Ranked Probability Score) | Primary — lower is better. Compare vs bookmaker |
| Accuracy (W/D/L) | Secondary — ~54-56% is good |
| ROI % | On value bets only |
| CLV (Closing Line Value) | Did we beat the closing line? Best long-run indicator |

---

## Project Structure

```
bets/
├── docs/
│   ├── APPROACH.md          # This file
│   └── papers/              # Downloaded paper summaries
├── data/
│   ├── raw/                 # Downloaded CSVs from football-data.co.uk
│   └── processed/           # Cleaned + merged data
├── src/
│   ├── data/
│   │   ├── downloader.py    # Fetch CSVs from football-data.co.uk
│   │   └── loader.py        # Load + clean into DataFrames
│   ├── ratings/
│   │   └── pi_ratings.py    # Pi-ratings dynamic system
│   ├── model/
│   │   ├── dixon_coles.py   # Poisson model + ρ correction
│   │   ├── xgboost_model.py # XGBoost W/D/L classifier
│   │   └── calibration.py   # RPS, Platt scaling
│   └── betting/
│       ├── value.py         # Value bet detection
│       └── kelly.py         # Kelly criterion staking
├── notebooks/               # Exploration and analysis
└── main.py                  # End-to-end pipeline runner
```

---

## Implementation Order

1. `src/data/downloader.py` — Download raw EPL CSVs
2. `src/data/loader.py` — Clean and standardize data
3. `src/ratings/pi_ratings.py` — Build dynamic pi-ratings
4. `src/model/dixon_coles.py` — Poisson model + ρ
5. `src/model/calibration.py` — RPS evaluation
6. `src/betting/value.py` + `kelly.py` — Betting logic
7. `src/model/xgboost_model.py` — ML enhancement layer

---

## Data Sources

| Source | URL | Notes |
|---|---|---|
| football-data.co.uk | `https://www.football-data.co.uk/mmz4281/{SSSS}/E0.csv` | Free, CSV, EPL 1993-present |
| Understat | `https://understat.com/league/EPL` | xG data, EPL 2014-present, JSON/CSV |

## Season Code Format

`9394` = 1993/94, `0001` = 2000/01, `2526` = 2025/26
