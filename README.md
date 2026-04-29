# Value Betting System

A sports betting scanner that finds bets where a bookmaker is offering better odds than the market consensus suggests. Covers 6 European football leagues with a secondary CatBoost model layer that scores how strongly it agrees with each bet.

---

## How it works

The system combines two independent signals:

### 1. Kaunitz Consensus (the core strategy)

Based on a 2017 academic paper by Kaunitz, Zhong & Kreiner that showed a +3.5% ROI over tens of thousands of bets.

The idea: if you average the implied probabilities across 30-40 bookmakers, you get a very accurate estimate of the true probability of each outcome — because the collective market is hard to beat. But occasionally, one bookmaker offers significantly better odds than the rest. That gap is the edge.

```
Consensus probability = average of (1/odds) across all 36 bookmakers
Edge = consensus probability − bookmaker implied probability

If edge ≥ 3% → flag as a Kaunitz bet
If edge ≥ 2% and CatBoost agrees → flag as a model-filtered bet
```

The scanner only flags bets at **UK-licensed bookmakers** (Betfair, Smarkets, William Hill, Sky Bet, etc.) since those are the ones you can actually use from the UK.

### 2. CatBoost Model (the filter)

A machine learning model trained on 10 years of historical match data. It independently estimates the probability of each outcome using:

- **Pi-ratings** — dynamic team strength ratings updated after every match (goals scored vs expected)
- **Rolling form** — goals for/against and points over the last 5 and 10 matches
- **Expected goals (xG)** — a better measure of underlying performance than raw goals (EPL, Bundesliga, Serie A, Ligue 1)

The model's output is shown as a signed percentage on each bet:
- **+18%** → model thinks this outcome is 18 percentage points more likely than the bookmaker does. Strong agreement.
- **+3%** → model barely agrees. Weak signal.
- **-8%** → model disagrees. The bookmaker may actually be right and this is noise.

**Key insight from backtesting:** The Kaunitz strategy alone gives ~+6% ROI at a 2% edge threshold. When filtered to only bets where the CatBoost model also agrees (≥2% model edge), ROI jumped to ~+26% — though with fewer bets and on a smaller sample. The model doesn't replace Kaunitz; it filters out noise.

---

## Leagues covered

| League | Data source | xG |
|---|---|---|
| Premier League (EPL) | football-data.co.uk + Understat | ✓ |
| Bundesliga | football-data.co.uk + Understat | ✓ |
| Serie A | football-data.co.uk + Understat | ✓ |
| Ligue 1 | football-data.co.uk + Understat | ✓ |
| EFL Championship | football-data.co.uk | — |
| Bundesliga 2 | football-data.co.uk | — |
| NBA | The Odds API (live only) | — |
| Tennis | The Odds API (live only) | — |

NBA and Tennis use the Kaunitz consensus only — no CatBoost model for those.

---

## Quick start

```bash
# 1. Set up environment
cp .env.example .env   # add your Odds API key

# 2. Generate CatBoost model signals (run once, then weekly)
python3 scripts/model_signals.py --download   # downloads data + trains models
python3 scripts/model_signals.py              # refresh signals (no re-download)

# 3. Scan for value bets
export $(cat .env) && python3 scripts/scan_odds.py

# 4. Open the dashboard
python3 app.py   # → http://localhost:5000
```

---

## The scanner (`scripts/scan_odds.py`)

Runs the full scan across all sports. For each fixture it:

1. Fetches live odds from ~36 bookmakers via [The Odds API](https://the-odds-api.com)
2. Computes the consensus probability for each outcome (home/draw/away)
3. Checks every UK-licensed bookmaker for gaps vs the consensus
4. Looks up the CatBoost model signal for football leagues
5. Outputs two groups:
   - **≥3% Kaunitz** — pure consensus bets, sent as HIGH/MED/LOW push notifications
   - **2–3% Model-filtered** — lower-edge bets where the model agrees, sent as a separate notification
6. Logs everything to `logs/bets.csv`

Push notifications go to your phone via [ntfy.sh](https://ntfy.sh). Confidence tiers:
- **HIGH** — ≥30 bookmakers in consensus
- **MED** — 20–29 bookmakers
- **LOW** — <20 bookmakers

Bet sizing uses half-Kelly criterion capped at 5% of bankroll.

---

## The model signals (`scripts/model_signals.py`)

Trains one CatBoost model per league and pre-computes predictions for every possible fixture combination in the current season. Results are cached in `logs/model_signals.json`.

Run this:
- Once initially (`--download` to also fetch historical data)
- Weekly to refresh team form and ratings after new results

```bash
python3 scripts/model_signals.py                        # all leagues (~90 seconds)
python3 scripts/model_signals.py --league soccer_epl    # one league only
python3 scripts/model_signals.py --download             # also download missing data
```

Each league uses the last 3 completed seasons for training. The model signal shown on each bet is:

```
model edge = CatBoost probability − bookmaker implied probability
```

Positive = model agrees there is value. The larger the number, the stronger the agreement.

---

## The dashboard (`app.py`)

```bash
python3 app.py   # → http://localhost:5000
```

Three sections:

- **Suggested — not yet placed**: new scanner finds, with CatBoost signal, suggested stake, bookmaker link
- **Placed — awaiting result**: bets you've logged a stake for
- **Settled**: completed bets with P&L and ROI summary

To log a bet: enter the odds you got, your actual stake, and hit Save. Fill in the result (W/L/V) after the match settles.

---

## Cron schedule (WSL, London time)

```
30 7  * * 1,2   Mon+Tue 7:30am      fresh weekly lines
30 7  * * 5     Fri 7:30am          injury news drops
30 19 * * 5     Fri 7:30pm          lineup hints
30 10 * * 6     Sat 10:30am         before 12:30 kick-offs
30 16 * * 6     Sat 4:30pm          between 15:00 and 17:30 games
30 12 * * 0     Sun 12:30pm         before afternoon games
0  8  1,15 * *  Bi-weekly 8am       sports discovery check
```

---

## Environment

```bash
# .env
ODDS_API_KEY=your_key_here
```

Free tier: 500 requests/month. Current usage: ~474/month (within budget).
Each scan of all football leagues uses ~14 API calls (2 regions × 7 sports).

Get a key at [the-odds-api.com](https://the-odds-api.com).

---

## Project structure

```
scripts/
  scan_odds.py          Daily scanner — finds value bets, sends notifications
  model_signals.py      Trains CatBoost per league, caches predictions to JSON
  check_sports.py       Discovers new sports on The Odds API (run bi-weekly)

src/
  data/
    downloader.py       Downloads match CSVs from football-data.co.uk
    loader.py           Loads and cleans match data per league
    features.py         Feature engineering (rolling stats, xG merge, differentials)
    understat.py        Downloads xG data from Understat (EPL, Bundesliga, Serie A, Ligue 1)
  ratings/
    pi_ratings.py       Dynamic team strength ratings (Constantinou & Fenton 2013)
  model/
    catboost_model.py   CatBoost W/D/L classifier (falls back to XGBoost if unavailable)
    dixon_coles.py      Poisson goal model with low-score correction (not yet in production)
    calibration.py      Model evaluation (RPS score vs bookmaker)
  betting/
    consensus.py        Kaunitz consensus logic + combined backtest
    kelly.py            Half-Kelly bet sizing
    value.py            Value bet detection from model probabilities

app.py                  Flask dashboard
templates/index.html    Dashboard UI
logs/
  bets.csv              All suggested bets + results (your main log)
  model_signals.json    CatBoost predictions cache (regenerated weekly)
  scan.log              Scanner output log
data/
  raw/                  football-data.co.uk CSVs (10+ seasons per league)
  raw/xg/               Understat xG CSVs
docs/
  APPROACH.md           Full research-backed architecture notes
  papers/               Academic paper summaries
```

---

## Research behind this

| Paper | What it contributes |
|---|---|
| Kaunitz, Zhong & Kreiner (2017) | The consensus strategy — bet when one book deviates from the market |
| Constantinou & Fenton (2013) | Pi-ratings — the dynamic team strength model |
| Dixon & Coles (1997) | Poisson goal model with low-score correction |
| Hubáček et al. (2022) | 40-year review showing gradient boosted trees outperform most models |
| Yeung et al. (2023) | CatBoost + pi-ratings competitive with deep learning on match prediction |

---

## Known limitations

- The Kaunitz paper notes that bookmakers eventually **restrict accounts** that consistently win. Betfair Exchange and Smarkets are less likely to do this (you bet against other punters, not the house).
- The CatBoost model's RPS score (0.213) is still worse than the bookmaker market (0.196) in isolation — it adds value as a filter, not as a standalone predictor.
- Championship and Bundesliga 2 have no xG data, so the model uses goals + pi-ratings only.
- Model signals are pre-computed, not live — refresh weekly with `model_signals.py`.
