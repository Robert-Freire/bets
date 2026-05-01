# Bets — Multi-Sport Value Betting System

## What this is

A value betting scanner using the **Kaunitz consensus strategy**: compute the Shin-devigged fair probability across 30–40 bookmakers, then flag bets where a UK-licensed bookmaker's odds are significantly better than the consensus. CLV (closing-line value) against Pinnacle is the primary diagnostic for whether edge is real.

*Backtest results — including Shin-corrected numbers (2% edge → 17.65% ROI, generated 2026-04-29) — are in [`docs/BACKTEST.md`](docs/BACKTEST.md).*

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

# Compare strategy variants (after a weekend of data)
python3 scripts/compare_strategies.py   # writes docs/STRATEGY_COMPARISON.md
```

## How the scanner works

1. Fetches live odds from The Odds API (`uk,eu` regions, ~36 bookmakers per fixture)
2. **Shin-devigs** each book's implied probabilities before averaging (Phase 1)
3. Consensus = mean of Shin-fair probs across all books; Pinnacle's devigged prob logged as anchor
4. Applies **Phase 4 filters**: rejects if cross-book stdev of fair probs > `MAX_DISPERSION=0.04`; rejects if the flagged book's z-score vs the rest exceeds `OUTLIER_Z_THRESHOLD=2.5`
5. Flags bets where a **UK-licensed** bookmaker's devigged prob is ≥3% below consensus (Kaunitz), or ≥2% with CatBoost model agreement
6. Sizes bets with half-Kelly, then applies **risk pipeline** (Phase 2): £5 rounding, per-fixture 5% cap, 15% portfolio cap, drawdown brake
7. Sends push notifications via ntfy.sh (topic: `robert-epl-bets-m4x9k`), deduped via `logs/notified.json` (12h per bet key)
8. Appends to `logs/bets.csv` (deduped by `(kickoff, home, away, side, book)` per scan date); includes `dispersion` and `outlier_z` columns

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

## Setup (fresh clone)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install --no-deps understat   # see requirements.txt for why this is separate
```

## Environment

Production cron runs on a Raspberry Pi 5 (`robert@192.168.0.28`, OS = Raspberry Pi OS Trixie / Python 3.13, project at `~/projects/bets`). WSL is the dev environment for manual scans and code changes. Each side has its own free Odds API account/key so manual testing never burns prod quota:

```bash
# Pi: ~/projects/bets/.env (gitignored) — PROD key, used by cron only
ODDS_API_KEY=<prod>
BANKROLL=1000   # optional override; falls back to config.json → default 1000

# WSL: /home/rfreire/projects/bets/.env.dev (gitignored) — DEV key, manual runs
ODDS_API_KEY=<dev>
```

Manual dev runs: `export $(cat .env.dev) && python3 scripts/scan_odds.py`. **Never** run manual scans on the Pi against the prod key (one exception: the post-cutover validation run on 2026-05-01).

Free tier: 500 requests/month per key. Production schedule uses ~474/month. Closing-line script adds ~6–10 calls on match days (zero on idle days). Each region (`uk`, `eu`) counts as a separate API call.

**Note:** The Odds API blocks requests from cloud/server IPs. The scanner runs on the Pi (production) or WSL (dev). Multi-account split is a stopgap; migrate to paid tier (~$25/mo for 100k credits) once CLV evidence justifies it.

## Cron schedule (Pi: `robert@192.168.0.28`, UTC)

Cutover from WSL → Pi on 2026-05-01. Pi crontab uses `SHELL=/bin/bash` and the `cd ~/projects/bets && export $(cat .env) && .venv/bin/python3 ...` pattern (no inline API keys). WSL crontab still has the bets entries commented as a restoration path if the Pi goes offline.

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

# xG snapshot (feeds K_draw_bias filter)
0  6  * * 1     Mon 06:00        — refresh logs/team_xg.json from Understat (last 5 matches/team)

# Research scanner
0 10 * * 1      Mon 10:00        — curated sources (Tier A change-watch + Tier B)
0 10 1 * *      1st of month 10:00 — open-search (7 queries × 4 backends)
```

## Key files

```
scripts/scan_odds.py        Main scanner
scripts/refresh_xg.py       Weekly xG snapshot from Understat → logs/team_xg.json
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
logs/team_xg.json           Per-team avg scoring xG + q25 threshold (weekly; feeds K_draw_bias)
logs/bankroll.json          High-water mark for drawdown brake
logs/notified.json          Notification dedupe state (12h per bet key)
tests/                      pytest suite (126 tests across 14 files; run with `pytest`)
src/betting/devig.py        Shin / proportional / power de-vigging
src/betting/risk.py         Stake rounding, fixture cap, portfolio cap, drawdown
src/betting/strategies.py   16 paper strategy variants (A–P; A_production live, B–P shadow) + evaluate_strategy() entry point
src/betting/consensus.py    Consensus computation helpers
src/betting/kelly.py        Kelly criterion
src/betting/walk_forward.py  Walk-forward backtest primitive (TimeSeriesSplit; Phase R.5.5a)
logs/paper/                 Paper strategy CSVs (one per variant, same schema as bets.csv + strategy col)
scripts/compare_strategies.py  Strategy comparison report → docs/STRATEGY_COMPARISON.md
docs/STRATEGY_COMPARISON.md   Latest CLV comparison across all paper-portfolio strategy variants
docs/PLAN.md                Phased improvement roadmap (Phases 0–10)
docs/APPROACH.md            Full research-backed architecture
docs/BACKTEST.md            Shin-corrected backtest (2026-04-29): raw vs shin tables + interpretation
docs/RESEARCH_NOTES_2026-04.md  Manual deep-read findings (April 2026); TL;DR at top
docs/PLAN_RESEARCH_2026-04.md   Implementation plan from above; bot-executable + bot-verifiable
docs/RESEARCH_SCANNER.md    Automated scanner spec (Phases 11.0–11.9 shipped) + post-2026-04 improvements
docs/RESEARCH_FEED.md       Auto-generated weekly findings (newest first)
src/                        Statistical models (Dixon-Coles, pi-ratings, CatBoost)
data/raw/                   Football-data.co.uk CSVs + Understat xG
```

## Dashboard

```bash
python3 app.py
# Open http://localhost:5000
```

Stat tiles: Bets placed · Won/Lost/Void · Total staked · P&L · ROI · **Avg CLV** (green if >0, only shown once any bets have CLV) · **Drift toward you %** (only shown once drift data exists; should be >50% if sharp) · **Research** (latest run count + mode · date from `docs/RESEARCH_FEED.md`).

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
| Bankroll source | `BANKROLL` env var → `config.json` → default £1000 |

## CLV diagnostics (Phase 3)

`scripts/closing_line.py` runs every 5 minutes and:
- Captures Pinnacle odds at T-60, T-15, and T-1 before kick-off → `logs/drift.csv`
- At kick-off (0–6 min window): saves Pinnacle closing devigged prob, computes `clv_pct = your_odds × pinnacle_close_prob − 1`
- Backfills `bets.csv` with `pinnacle_close_prob` and `clv_pct` columns
- Dashboard aggregates: avg CLV and % of bets where line drifted toward you

**CLV is the gate**: if avg CLV stays negative over ~50 bets, the system has no real edge and further build-out (Phases 5–10) is pointless.

**CLV scope limitations:**
- **Tennis bets produce no CLV/drift.** `closing_line.py` skips tennis because sport labels (API `title` field) are dynamic and not in the fixed `LABEL_TO_KEY` map. Will be fixed in Phase 6 when `sport_key` is stored in `bets.csv`.
- **Totals and BTTS bets always show `model_signal=?`.** The CatBoost model only produces signals for h2h on EPL, Bundesliga, Serie A, and Ligue 1. The 2–3% model-filtered notification path therefore only ever fires on h2h bets in those four leagues — never on totals, BTTS, Championship, Bundesliga 2, NBA, or tennis.

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
| 4 | Filters: dispersion, outlier-book check + notification dedupe + test scaffolding | ✅ Done (dispersion/outlier via `strategies.py`; notification dedupe via `logs/notified.json` in `scan_odds.py`) |
| 5 | New markets: totals, BTTS | ✅ Done |
| 5.5 | Paper portfolios (initial 8 strategy variants A–H, shadow A/B; expanded to 16 in R.0–R.3 + R.8) | ✅ Done |
| 5.6 | Phase 5.5 bugfix sweep (P0/P1) | ✅ Done |
| 5.7 | Commission-aware edges (per-book commission, net Kelly) | ✅ Done |
| 5.8 | Post-5.7 review fixes (schema reset, per-row CLV, impl_raw rename, tennis throttle) | ✅ Done |
| 6 | Storage migration: SQL Server Express + UUIDs (was: SQLite — superseded by Azure direction below) | Pending |
| 7 | Model overhaul: calibration, hold-out eval | Pending |
| 8 | Betfair API auto-placement | Pending |
| 9 | Infrastructure: **9a Pi cron ✅ Done 2026-05-01** · 9b–9d Azure migration (App Service + SQL DB, replaces CSVs) Pending — see `docs/PLAN_AZURE_2026-05.md` |
| 11 | Research scanner (11.0–11.9: source scan → Claude → `docs/RESEARCH_FEED.md` → dashboard tile → cron). Spec: `docs/RESEARCH_SCANNER.md` | ✅ Done |
| R.0–R.3 | Stale doc fix + 7 new shadow variants (I/L/M/N/O/P/J) + SBK probe. Spec: `docs/PLAN_RESEARCH_2026-04.md` | ✅ Done |
| R.5.5a | Walk-forward backtest scaffold (`src/betting/walk_forward.py`, `TimeSeriesSplit(5)` primitive) | ✅ Done |
| R.5.5b | 16 new leagues from football-data.co.uk (91,492 matches / 22 leagues; see `docs/FDCO_INGEST_NOTES.md`). Pivot rationale: `docs/ZENODO_INGEST_NOTES.md` | ✅ Done |
| R.4 | Weekend data collection (Sat–Sun, runs via existing cron) | Auto-runs |
| R.5 | Monday analysis: §4.3, 4.5, 4.6 + compare_strategies output | Pending |
| R.5.5c | Walk-forward run + 3-view per-fold report (all-22 / production-6 / per-league × 16); adds `consensus_mode` axis (mean vs pinnacle_only) | Pending |
| R.6 | Graduate winning **variants** (production-6 evidence) AND winning **leagues** (per-league evidence) → scanner defaults | Pending (conditional on R.5.5c) |
| R.7 | bets.csv schema: `devig_method`, `weight_scheme` columns | ✅ Done |
| R.8 | Draw-bias variant K (xG from Understat; `scripts/refresh_xg.py` weekly cron) | ✅ Done |
| R.9 | Asian Handicap feasibility probe (The Odds API) | ✅ Done (`docs/AH_FEASIBILITY.md`; AH fetchable via `spreads` key; UK books too thin; Pinnacle anchor viable post-upgrade) |
| R.10 | AH probability conversion module (planning only) | Blocked on CLV confirmation (gate: R.6 graduations + avg CLV>0 over ≥50 bets + sharp-weighted shadow signal; see `docs/AH_FEASIBILITY.md` §6) |

Full roadmap: `docs/PLAN.md`. 2026-04 sprint: `docs/PLAN_RESEARCH_2026-04.md`.

**Variants in shadow (paper portfolio only — not flipped as defaults):** I_power_devig, J_sharp_weighted, K_draw_bias, L_quarter_kelly, M_min_prob_15, N_competitive_only, O_kaunitz_classic, P_max_odds_shopping. Production scanner still uses A_production logic.

## Research cycle (manual deep-read → variants → graduations)

The automated scanner (`docs/RESEARCH_SCANNER.md`) writes shallow findings weekly to `docs/RESEARCH_FEED.md`. Quarterly (or signal-triggered), do a manual deep-read pass that produces:

1. `docs/RESEARCH_NOTES_<YYYY-MM>.md` — judgement-laden findings; reads actual code from comparable repos and PDFs in full.
2. `docs/PLAN_RESEARCH_<YYYY-MM>.md` — phased implementation plan derived from the notes; each phase = one PR, with explicit Acceptance, Verification, and Reviewer-focus blocks (bot-implementable, bot-verifiable).
3. PRs landing variants in `src/betting/strategies.py` as Phase 5.5 paper portfolio.
4. After ≥50 settled bets per variant + walk-forward backtest evidence, graduations flip scanner defaults.

Latest cycle: **2026-04** — see `docs/RESEARCH_NOTES_2026-04.md` (TL;DR at top) and `docs/PLAN_RESEARCH_2026-04.md` (phases R.0 → R.10).

## Research foundation

| Paper | Key finding |
|---|---|
| Dixon & Coles (1997) | Poisson model with ρ low-score correction |
| Constantinou & Fenton (2013) | Pi-ratings: dynamic goal-difference ratings |
| Kaunitz, Zhong & Kreiner (2017) | Consensus strategy: +3.5% ROI, accounts get restricted |
| Shin (1993) | Insider-trader model for de-vigging bookmaker overround |
| Hubáček et al. (2022) | 40-year review: Berrar ratings + XGBoost best |
| Yeung et al. (2023) | CatBoost + pi-ratings competitive with deep learning |
