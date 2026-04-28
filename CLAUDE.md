# Bets — Multi-Sport Value Betting System

## What this is

A value betting scanner using the **Kaunitz consensus strategy**: compute the average implied probability across 30-40 bookmakers, then flag bets where a UK-licensed bookmaker's odds are significantly better than the consensus. Historical backtest shows +6.1% ROI at 2% edge, +10.5% at 3% edge.

## Quick start

```bash
# Run the scanner manually
export $(cat .env) && python3 scripts/scan_odds.py

# Open the dashboard (track bets, log results)
python3 app.py   # → http://localhost:5000

# Check for new sports worth adding (bi-weekly)
export $(cat .env) && python3 scripts/check_sports.py
```

## How the scanner works

1. Fetches live odds from The Odds API (`uk,eu` regions, ~36 bookmakers per fixture)
2. Computes consensus = average implied probability across all books
3. Flags bets where a **UK-licensed** bookmaker offers odds > 3% above consensus
4. Sizes bets using half-Kelly criterion (capped at 5% of bankroll)
5. Sends push notifications via ntfy.sh (topic: `robert-epl-bets-m4x9k`)
6. Appends all bets to `logs/bets.csv`

## Sports scanned

| Sport | Key | Min books |
|---|---|---|
| EPL | `soccer_epl` | 20 |
| Bundesliga | `soccer_germany_bundesliga` | 20 |
| Serie A | `soccer_italy_serie_a` | 20 |
| EFL Championship | `soccer_efl_champ` | 25 |
| Ligue 1 | `soccer_france_ligue_one` | 20 |
| Bundesliga 2 | `soccer_germany_bundesliga2` | 20 |
| NBA | `basketball_nba` | 20 |
| Tennis | auto-detected from active tournaments | 15 |

La Liga excluded — too noisy, not enough UK bookmaker coverage yet.

## Confidence levels

- **HIGH** — ≥30 books in consensus → high priority ntfy notification
- **MED** — 20–29 books → default priority
- **LOW** — <20 books → low priority

## Environment

```bash
# .env (gitignored)
ODDS_API_KEY=e450dc2a3eb22ced005f1bb823fe1f1e
```

Free tier: 500 requests/month. Current usage: ~474/month (safe).
Each region (`uk`, `eu`) counts as a separate API call.

**Note:** The Odds API blocks requests from cloud/server IPs (CCR sandbox). The scanner must run locally.

## Cron schedule (WSL, London time / BST)

```
30 7  * * 1,2   Mon+Tue 7:30am   — fresh weekly lines
30 7  * * 5     Fri 7:30am       — injury news drops
30 19 * * 5     Fri 7:30pm       — lineup hints
30 10 * * 6     Sat 10:30am      — before 12:30 kick-off
30 16 * * 6     Sat 4:30pm       — between 15:00 and 17:30 games
30 12 * * 0     Sun 12:30pm      — before afternoon games
0  8  1,15 * *  Bi-weekly 8am    — sports discovery check
```

## Key files

```
scripts/scan_odds.py        Main scanner
scripts/check_sports.py     Sports discovery (bi-weekly)
app.py                      Flask dashboard
templates/index.html        Dashboard UI
logs/bets.csv               All suggested bets + results
logs/scan.log               Scanner output
logs/sports_cache.json      Sports already evaluated
docs/APPROACH.md            Full research-backed architecture
docs/papers/                Academic paper summaries
src/                        Statistical models (Dixon-Coles, pi-ratings, XGBoost)
data/raw/                   Football-data.co.uk CSVs + Understat xG
```

## Dashboard

```bash
python3 app.py
# Open http://localhost:5000
```

Three sections:
- **Placed — awaiting result**: bets you've logged a stake for, waiting to settle
- **Suggested — not yet placed**: new scanner suggestions
- **Settled**: completed bets with P&L and ROI summary

## Statistical model (built, not yet in production)

Full pipeline in `src/` and `main.py`:
- `src/ratings/pi_ratings.py` — dynamic team strength (Constantinou 2013)
- `src/model/dixon_coles.py` — Poisson model with ρ low-score correction
- `src/model/xgboost_model.py` — CatBoost on pi-ratings + rolling xG features
- `src/data/understat.py` — xG data from Understat (4,180 EPL matches 2014–2024)

Current status: model RPS 0.2137 vs bookmaker 0.1957 — no edge yet. Needs better calibration.

## Next: Betfair API automation

Plan: automatically place bets on Betfair Exchange when scanner finds value.

Requirements:
- `betfairlightweight` Python library
- API key from Betfair account → Account → API Access
- SSL certificate (generated once via Betfair's key generation tool)

Build plan:
1. Authentication module
2. Market search (match team names + date to Betfair market IDs)
3. Dry-run mode first (logs what would be placed, no real bets)
4. Live mode with: max stake cap, price tolerance (skip if odds moved >2%)
5. Auto-log to `bets.csv`

## Research foundation

| Paper | Key finding |
|---|---|
| Dixon & Coles (1997) | Poisson model with ρ low-score correction |
| Constantinou & Fenton (2013) | Pi-ratings: dynamic goal-difference ratings |
| Kaunitz, Zhong & Kreiner (2017) | Consensus strategy: +3.5% ROI, accounts get restricted |
| Hubáček et al. (2022) | 40-year review: Berrar ratings + XGBoost best |
| Yeung et al. (2023) | CatBoost + pi-ratings competitive with deep learning |
