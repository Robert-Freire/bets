# Bets — Multi-Sport Value Betting System

## What this is

A value betting scanner using the **Kaunitz consensus strategy**: compute the Shin-devigged fair probability across 30–40 bookmakers, then flag bets where a UK-licensed bookmaker's odds are significantly better than the consensus. CLV (closing-line value) against Pinnacle is the primary diagnostic for whether edge is real.

*Note: the legacy backtest (+6.1% ROI at 2% edge) was computed on raw implied probabilities, not de-vigged. A corrected backtest is pending (Plan phase 1.5).*

## Quick start

```bash
# Run the scanner manually
export $(cat .env) && python3 scripts/scan_odds.py

# Capture closing lines + drift (normally runs via cron every 5 min)
export $(cat .env) && python3 scripts/closing_line.py

# Open the dashboard (track bets, log results, view CLV)
python3 app.py   # → http://localhost:5000

# Check for new sports worth adding (bi-weekly)
export $(cat .env) && python3 scripts/check_sports.py
```

## How the scanner works

1. Fetches live odds from The Odds API (`uk,eu` regions, ~36 bookmakers per fixture)
2. **Shin-devigs** each book's implied probabilities before averaging (Phase 1)
3. Consensus = mean of Shin-fair probs across all books; Pinnacle's devigged prob logged as anchor
4. Flags bets where a **UK-licensed** bookmaker's devigged prob is ≥3% below consensus (Kaunitz), or ≥2% with CatBoost model agreement
5. Sizes bets with half-Kelly, then applies **risk pipeline** (Phase 2): £5 rounding, per-fixture 5% cap, 15% portfolio cap, drawdown brake
6. Sends push notifications via ntfy.sh (topic: `robert-epl-bets-m4x9k`)
7. Appends to `logs/bets.csv` (deduped by `(kickoff, home, away, side, book)` per scan date)

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
| Tennis | auto-detected from active tournaments (capped at 2) | 15 |

La Liga excluded — too noisy, not enough UK bookmaker coverage yet.

## Confidence levels

- **HIGH** — ≥30 books in consensus → high priority ntfy notification
- **MED** — 20–29 books → default priority
- **LOW** — <20 books → low priority

## Environment

```bash
# .env (gitignored)
ODDS_API_KEY=...
BANKROLL=1000   # optional override; default in config
```

Free tier: 500 requests/month. Scanner uses ~474/month. Closing-line script adds ~6–10 calls on match days (zero on idle days).
Each region (`uk`, `eu`) counts as a separate API call.

**Note:** The Odds API blocks requests from cloud/server IPs. The scanner must run locally (WSL or Pi).

## Cron schedule (WSL, UTC)

```
# Scanner
30 7  * * 1,2   Mon+Tue 7:30     — fresh weekly lines
30 7  * * 5     Fri 7:30         — injury news drops
30 19 * * 5     Fri 19:30        — lineup hints
30 10 * * 6     Sat 10:30        — before 12:30 kick-off
30 16 * * 6     Sat 16:30        — between 15:00 and 17:30 games
30 12 * * 0     Sun 12:30        — before afternoon games
0  9  * * 1,4   Mon+Thu 9:00     — tennis (capped at 2 tournaments)
0  17 * * 1-5   Mon-Fri 17:00    — NBA before evening tip-offs

# Closing line + drift snapshots
*/5 7-23 * * *  Every 5 min      — T-60, T-15, T-1 drift + CLV at close

# Housekeeping
0  8  1,15 * *  Bi-weekly 8am    — sports discovery check
0  3  * * *     3am daily        — bets.csv backup (14-day retention)
```

## Key files

```
scripts/scan_odds.py        Main scanner
scripts/closing_line.py     Closing-line + drift snapshot (runs every 5 min)
scripts/check_sports.py     Sports discovery (bi-weekly)
scripts/model_signals.py    CatBoost signal cache generator
app.py                      Flask dashboard
templates/index.html        Dashboard UI
logs/bets.csv               All suggested bets + results + CLV
logs/closing_lines.csv      Pinnacle closing prob per bet at kick-off
logs/drift.csv              Odds drift at T-60, T-15, T-1 before kick-off
logs/scan.log               Scanner output
logs/closing_line.log       Closing-line script output
logs/bankroll.json          High-water mark for drawdown brake
src/betting/devig.py        Shin / proportional / power de-vigging
src/betting/risk.py         Stake rounding, fixture cap, portfolio cap, drawdown
src/betting/consensus.py    Consensus computation helpers
src/betting/kelly.py        Kelly criterion
docs/PLAN.md                Phased improvement roadmap (Phases 0–10)
docs/APPROACH.md            Full research-backed architecture
src/                        Statistical models (Dixon-Coles, pi-ratings, CatBoost)
data/raw/                   Football-data.co.uk CSVs + Understat xG
```

## Dashboard

```bash
python3 app.py
# Open http://localhost:5000
```

Four stat panels: Bets placed · Won/Lost/Void · Total staked · P&L · ROI · **Avg CLV** (green if >0) · **Drift toward you %** (should be >50% if sharp).

Three bet sections:
- **Placed — awaiting result**: logged stake, waiting to settle
- **Suggested — not yet placed**: new scanner output
- **Settled**: P&L, CLV%, drift direction per bet

## Risk management (Phase 2)

Configured in `src/betting/risk.py` and `logs/bankroll.json`:

| Control | Value |
|---|---|
| Stake rounding | Nearest £5 (bets < £5 dropped) |
| Per-fixture cap | Max 5% of bankroll across all sides of one game |
| Portfolio cap | Max 15% of bankroll per scan |
| Drawdown brake | If bankroll < 85% of high-water → stakes halved |
| Bankroll source | `BANKROLL` env var or `config.json` |

## CLV diagnostics (Phase 3)

`scripts/closing_line.py` runs every 5 minutes and:
- Captures Pinnacle odds at T-60, T-15, and T-1 before kick-off → `logs/drift.csv`
- At kick-off (0–6 min window): saves Pinnacle closing devigged prob, computes `clv_pct = your_odds × pinnacle_close_prob − 1`
- Backfills `bets.csv` with `pinnacle_close_prob` and `clv_pct` columns
- Dashboard aggregates: avg CLV and % of bets where line drifted toward you

**CLV is the gate**: if avg CLV stays negative over ~50 bets, the system has no real edge and further build-out (Phases 5–10) is pointless.

## Statistical model (built, not yet in production)

Full pipeline in `src/` and `main.py`:
- `src/ratings/pi_ratings.py` — dynamic team strength (Constantinou 2013)
- `src/model/dixon_coles.py` — Poisson model with ρ low-score correction
- `src/model/catboost_model.py` — CatBoost on pi-ratings + rolling xG features
- `src/data/understat.py` — xG data from Understat (4,180 EPL matches 2014–2024)

Current status: model RPS 0.2137 vs bookmaker 0.1957 — no edge yet. Honest hold-out eval + calibration planned (Phase 7).

## Implementation status

| Phase | Description | Status |
|---|---|---|
| 0 | Hygiene: dedup, atomic writes, backups, no-bets throttle | ✅ Done |
| 1 | Shin de-vigging + Pinnacle anchor | ✅ Done |
| 2 | Risk management (rounding, caps, drawdown) | ✅ Done |
| 3 | CLV + drift diagnostics | ✅ Done |
| 4 | Filters: dispersion, outlier-book check | Pending |
| 5 | New markets: totals, BTTS | Pending |
| 6 | SQLite + UUIDs | Pending |
| 7 | Model overhaul: calibration, hold-out eval | Pending |
| 8 | Betfair API auto-placement | Pending |
| 9 | Pi / Azure infrastructure | Pending |

Full roadmap: `docs/PLAN.md`.

## Research foundation

| Paper | Key finding |
|---|---|
| Dixon & Coles (1997) | Poisson model with ρ low-score correction |
| Constantinou & Fenton (2013) | Pi-ratings: dynamic goal-difference ratings |
| Kaunitz, Zhong & Kreiner (2017) | Consensus strategy: +3.5% ROI, accounts get restricted |
| Shin (1993) | Insider-trader model for de-vigging bookmaker overround |
| Hubáček et al. (2022) | 40-year review: Berrar ratings + XGBoost best |
| Yeung et al. (2023) | CatBoost + pi-ratings competitive with deep learning |
