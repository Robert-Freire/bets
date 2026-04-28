# Dixon & Coles (1997) — Modelling Association Football Scores

**Journal:** Journal of the Royal Statistical Society: Series C, Vol. 46, Issue 2, pp. 265–280  
**Authors:** Mark J. Dixon, Stuart G. Coles

## Core Model

Goals scored by each team modelled as **independent Poisson distributions**:

```
Home goals ~ Poisson(λ_h)
Away goals ~ Poisson(λ_a)

λ_h = alpha_i * beta_j * gamma      (home attack * away defense * home advantage)
λ_a = alpha_j * beta_i              (away attack * home defense)
```

Where:
- `alpha_i` = attack strength of team i
- `beta_j` = defensive weakness of team j (higher = worse defense)
- `gamma` = home advantage multiplier (~1.3 for EPL)

## The ρ (Rho) Correction

Standard independent Poisson **underestimates** probability of low-score results. Correction:

```
P(0,0) = P_pois(0,0) * (1 - λ_h * λ_a * ρ)
P(1,0) = P_pois(1,0) * (1 + λ_a * ρ)
P(0,1) = P_pois(0,1) * (1 + λ_h * ρ)
P(1,1) = P_pois(1,1) * (1 - ρ)
```

Typical fitted value: `ρ ≈ -0.13`

## Computing Match Outcome Probabilities

Integrate over score matrix (0-10 goals each side):

```python
P(home win) = sum(P(i,j) for i > j)
P(draw)     = sum(P(i,i))
P(away win) = sum(P(i,j) for i < j)
```

## Time Decay

Weight older matches less:

```
w(t) = exp(-xi * (t_max - t))
```

Typical xi ≈ 0.0065 (half-life ~3 months)

## Parameter Estimation

Maximum likelihood on weighted match history. Each team gets attack + defense parameter. Solved with scipy.optimize.minimize (L-BFGS-B).

## Key Findings

- Model showed **positive expected return** against published bookmaker odds
- First peer-reviewed paper to formally demonstrate this for football
- ρ correction is essential — without it draw probabilities are systematically underestimated

## Practical Notes

- Use last 3 seasons of data with decay weighting
- Reset/re-estimate parameters at start of each season
- Score matrix capped at 10 goals per team captures >99.99% of probability mass
