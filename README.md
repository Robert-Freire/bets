# Value Betting System

A sports betting scanner that finds bets where a bookmaker is offering better odds than the market consensus suggests. Covers 6 European football leagues with a secondary CatBoost model layer that scores how strongly it agrees with each bet.

---

## How it works

The system combines two independent signals:

### 1. Kaunitz Consensus (the core strategy)

Based on a 2017 academic paper by Kaunitz, Zhong & Kreiner that showed a +3.5% ROI over tens of thousands of bets.

The idea: if you average the implied probabilities across 30–40 bookmakers, you get a very accurate estimate of the true probability of each outcome — because the collective market is hard to beat. But occasionally, one bookmaker offers significantly better odds than the rest. That gap is the edge.

Each book's raw implied probabilities are **Shin-devigged** before averaging, removing the bookmaker's overround so the consensus reflects true market probability rather than inflated implied probabilities.

```
Fair probability (per book) = Shin(1/odds_H, 1/odds_D, 1/odds_A)
Consensus probability       = mean of fair probs across all ~36 books
Edge = consensus probability − bookmaker's own Shin-fair probability

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

**Key insight from backtesting:** Earlier backtests showed +6% ROI at 2% edge and +26% ROI when filtered by the CatBoost model — but those were computed on raw implied probabilities, not Shin-devigged ones. A corrected backtest is pending. Treat these figures as upper bounds. The model's role is to filter noise, not replace Kaunitz.

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
2. **Shin-devigs** each book's implied probs, then averages them to form the consensus
3. Checks every UK-licensed bookmaker for gaps vs the consensus
4. Looks up the CatBoost model signal for football leagues
5. Outputs two groups:
   - **≥3% Kaunitz** — pure consensus bets, sent as HIGH/MED/LOW push notifications
   - **2–3% Model-filtered** — lower-edge bets where the model agrees, sent as a separate notification
6. Applies the **risk pipeline**: rounds stakes to nearest £5, caps exposure per fixture (5% of bankroll) and per scan (15%), and halves stakes if in a drawdown
7. Logs everything to `logs/bets.csv` (deduplicated within the same scan day)

Push notifications go to your phone via [ntfy.sh](https://ntfy.sh). Confidence tiers:
- **HIGH** — ≥30 bookmakers in consensus
- **MED** — 20–29 bookmakers
- **LOW** — <20 bookmakers

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

## Cron schedule (WSL, UTC)

```
# Scanner
30 7  * * 1,2   Mon+Tue 7:30        fresh weekly lines (football)
30 7  * * 5     Fri 7:30            injury news drops (football)
30 19 * * 5     Fri 19:30           lineup hints (football)
30 10 * * 6     Sat 10:30           before 12:30 kick-offs (football)
30 16 * * 6     Sat 16:30           between 15:00 and 17:30 games (football)
30 12 * * 0     Sun 12:30           before afternoon games (football)
0  9  * * 1,4   Mon+Thu 9:00        tennis (capped at 2 tournaments)
0  17 * * 1-5   Mon-Fri 17:00       NBA before evening tip-offs

# Closing line + drift snapshots
*/5 7-23 * * *  Every 5 min         T-60, T-15, T-1 drift + CLV at close

# Housekeeping
0  8  1,15 * *  Bi-weekly 8:00      sports discovery check
0  3  * * *     Daily 3:00          bets.csv backup (14-day retention)
```

---

## Environment

```bash
# .env
ODDS_API_KEY=your_key_here
```

Free tier: 500 requests/month. Scanner uses ~474/month; closing-line script adds ~6–10 calls on match days (zero on idle days).
Each scan of all football leagues uses ~14 API calls (2 regions × 7 sports).

Get a key at [the-odds-api.com](https://the-odds-api.com).

---

## Project structure

```
scripts/
  scan_odds.py          Daily scanner — finds value bets, sends notifications
  closing_line.py       Closing-line + drift snapshots (runs every 5 min via cron)
  model_signals.py      Trains CatBoost per league, caches predictions to JSON
  check_sports.py       Discovers new sports on The Odds API (run bi-weekly)

src/
  betting/
    devig.py            Shin / proportional / power de-vigging
    risk.py             Stake rounding, fixture cap, portfolio cap, drawdown brake
    consensus.py        Kaunitz consensus logic + combined backtest
    kelly.py            Half-Kelly bet sizing
    value.py            Value bet detection from model probabilities
  data/
    downloader.py       Downloads match CSVs from football-data.co.uk
    loader.py           Loads and cleans match data per league
    features.py         Feature engineering (rolling stats, xG merge, differentials)
    understat.py        Downloads xG data from Understat (EPL, Bundesliga, Serie A, Ligue 1)
  ratings/
    pi_ratings.py       Dynamic team strength ratings (Constantinou & Fenton 2013)
  model/
    catboost_model.py   CatBoost W/D/L classifier
    dixon_coles.py      Poisson goal model with low-score correction (not yet in production)
    calibration.py      Model evaluation (RPS score vs bookmaker)

app.py                  Flask dashboard
templates/index.html    Dashboard UI
logs/
  bets.csv              All suggested bets + results + CLV (your main log)
  closing_lines.csv     Pinnacle closing devigged prob per bet at kick-off
  drift.csv             Pinnacle + book odds at T-60, T-15, T-1 before kick-off
  model_signals.json    CatBoost predictions cache (regenerated weekly)
  bankroll.json         High-water mark for drawdown brake
  scan.log              Scanner output log
  closing_line.log      Closing-line script output log
data/
  raw/                  football-data.co.uk CSVs (10+ seasons per league)
  raw/xg/               Understat xG CSVs
docs/
  PLAN.md               Phased improvement roadmap (Phases 0–10)
  APPROACH.md           Full research-backed architecture notes
  papers/               Academic paper summaries
```

---

## Research behind this

| Paper | What it contributes |
|---|---|
| Kaunitz, Zhong & Kreiner (2017) | The consensus strategy — bet when one book deviates from the market |
| Shin (1993) | Insider-trader model for removing bookmaker overround from implied probabilities |
| Constantinou & Fenton (2013) | Pi-ratings — the dynamic team strength model |
| Dixon & Coles (1997) | Poisson goal model with low-score correction |
| Hubáček et al. (2022) | 40-year review showing gradient boosted trees outperform most models |
| Yeung et al. (2023) | CatBoost + pi-ratings competitive with deep learning on match prediction |

---

## CLV diagnostics

Closing-line value (CLV) is the primary measure of whether the system has real edge. It answers: *did you get a better price than the market settled at?*

```
CLV % = your_odds × pinnacle_closing_fair_prob − 1
```

Positive CLV means you beat the close — the market subsequently agreed with your bet. Consistently positive CLV (over ≥50 bets) is evidence of genuine edge, regardless of short-term P&L variance.

`scripts/closing_line.py` captures Pinnacle odds at T-60, T-15, and T-1 minutes before kick-off, then writes `pinnacle_close_prob` and `clv_pct` back into `bets.csv`. The dashboard surfaces **avg CLV** and **% drift toward you** in the stats bar.

**If avg CLV is consistently negative over 50+ bets, stop the build-out** — Phases 5–10 add no value without an underlying edge.

---

## Known limitations

- The Kaunitz paper notes that bookmakers eventually **restrict accounts** that consistently win. Betfair Exchange and Smarkets are less likely to do this (you bet against other punters, not the house).
- The CatBoost model's RPS score (0.214) is still worse than the bookmaker market (0.196) in isolation — it adds value as a filter, not as a standalone predictor. A calibrated hold-out evaluation is planned (Phase 7).
- Championship and Bundesliga 2 have no xG data, so the model uses goals + pi-ratings only.
- Model signals are pre-computed, not live — refresh weekly with `model_signals.py`.
