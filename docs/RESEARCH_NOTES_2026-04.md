# Research Notes — April 2026

Manual deep-read of the 37 sources in `docs/research_sources.md` plus 4 IJF 2025 PDFs (Hegarty & Whelan, Clegg & Cartlidge, Hardy et al., MacLean/Thorp/Ziemba). Goal: surface ideas to adopt into the bets system, plus ideas worth pursuing outside its scope.

This is not a replacement for the automated `RESEARCH_FEED.md` — it's the deeper, judgement-laden pass that the cost-capped scanner can't do. Implementation of every adopt-able finding here is in **`docs/PLAN_RESEARCH_2026-04.md`**.

## TL;DR — six findings that drive everything else

1. **Favourite-longshot bias is empirically real in football h2h** (Hegarty & Whelan 2025, n=84,230). Decile 1 loses 17%, decile 10 loses 2%. → variant `M_min_prob_15` + scanner-default candidate.
2. **The Asian handicap market is efficient; H2H is not.** Sharps trade AH; soft books copy them with a margin. Closed-form AH→prob formulas published. → planning carryover (R.9 feasibility, R.10 deferred implementation).
3. **We've never benchmarked against the paper-faithful Kaunitz baseline** (α=0.05, raw consensus, max-odds shopping). → variant `O_kaunitz_classic` (R.1.5). Without it, our 17.65% Shin backtest ROI is uncalibrated.
4. **Single bad data row + Kelly = bankroll destruction** (Clegg & Cartlidge 2025: one Bet365 5.50 vs market 1.85 wiped a published 29% ROI study). Our outlier-z + per-fixture cap are the explicit guards. **Keep them strict.** Surviving signal in their data: "competitive matches only" → variant `N_competitive_only`.
5. **Sharpness-weighted consensus is a real gap** (datagolf: Pinnacle blind-return -3.31% vs DraftKings -7.08%, ~2× sharpness gap). We currently equal-weight all 36 books. → variant `J_sharp_weighted` (R.2).
6. **`georgedouzas/sports-betting` already has walk-forward backtest infrastructure** (`TimeSeriesSplit`, joblib parallel, per-market reporting). MIT licensed. → R.5.5 effort drops from 2.5h → 1h by subclassing `BaseBettor` instead of writing from scratch.

**Sober anchor**: Konstanzer's faithful Kaunitz reproduction returned ~$2/hr effective rate over 5 months including monitoring overhead. The HN 2024 consensus is "still works, but you get banned." Operational cost / restriction-resilience is the binding constraint, not modelling sophistication.

## Findings → plan phases (cross-reference)

| Finding | Implementing phase | Doc section |
|---|---|---|
| Min-prob filter (longshot guard) | R.1 (variant M) | §1.2, §6, §7.1 |
| Sharpness-weighted consensus | R.2 (variant J) | §1.1, §8.2 |
| Power devig variant | R.1 (variant I) | §2.1 |
| 0.4-Kelly variant | R.1 (variant L) | §2.4 |
| Competitive-match filter | R.1 (variant N) | §7.2 |
| Paper-faithful Kaunitz baseline | R.1.5 (variant O) | §9.1 |
| Max-odds shopping | R.1.6 (variant P, optional) | §9.5 |
| Draw-bias variant | R.8 (variant K) — deferred | §2.3 |
| SBK rotation | R.3 | §1.3 |
| Stale CLAUDE.md note | R.0 | §1.4 |
| Walk-forward backtest | R.5.5 | §3.7, §9.2 |
| Provenance columns in bets.csv | R.7 | §8.1 (implied) |
| AH feasibility note | R.9 | §7.1 |
| AH probability conversion | R.10 — deferred | §7.1 |

Items still in **Open carryovers** (no phase yet): restriction-detection logging, mug-bet camouflage cron, ELO prior variant, Zenodo 84k dataset, pybettor evaluation, dashboard pagination.

---

## Source coverage

| Cluster | Sources | Read | 403 / blocked |
|---|---|---|---|
| Kaunitz strategy | 6 | 4 | arXiv abstract (no detail page) + ScienceDirect 2024 paper |
| Devig methods | 6 | 4 | dratings.com + researchgate.net |
| CLV / drift | 4 | 3 | Pinnacle's own CLV article (JS-rendered) |
| Restrictions / exchanges | 3 | 3 | — |
| Bet sizing | 3 | 2 | Aldous PDF (needs `poppler-utils` locally) |
| Other (biases, outliers) | 3 | 3 | — |
| Tier B repos | 5 | 5 | — |
| **Total** | 30 | 24 | 6 partial / blocked |

The 6 misses are flagged as **Open questions** below — most have alternate routes.

---

## 1. Adopt-now changes

Concrete diffs sized for one PR each. Ranked by adopt-ability × signal strength.

### 1.1 Sharpness-weighted consensus
**File:** `src/betting/consensus.py` · **Adopt-ability: 4/5**

We currently equal-weight all ~36 books in consensus. Datagolf's measured "blind-return at close" (a CLV-based sharpness metric) shows wide variance:

| Book | Blind return | Implied weight (1/|return|) |
|---|---|---|
| Pinnacle | -3.31% | 0.30 |
| Betcris | ~-4% | 0.25 |
| Bet365 | -7.08% | 0.14 |
| DraftKings | -7.01% | 0.14 |

Soft books drag the consensus toward retail-pricing noise. Pinnacle remains excluded (we use it as anchor), but among the ~35 remaining we should weight sharper books more. **Concrete starter weights**: sharp tier (Bet365, Betfair Exchange) 1.5×; mid tier (Marathonbet, Sportingbet, Bwin, etc.) 1.0×; soft tier (most retail UK) 0.7×. Tier list seeded from datagolf + outlier.bet, refined empirically over CLV.

This is a meaningful methodological change — should ship as a paper variant first (variant J in `strategies.py`), measured against current equal-weight baseline for ≥2 weeks before flipping the default.

### 1.2 Min-probability filter (longshot guard)
**File:** `src/betting/strategies.py` · **Adopt-ability: 5/5**

Add `MIN_CONSENSUS_PROB = 0.10` (or 0.15) and reject bets whose consensus prob falls below it. Favourite-longshot bias is well-documented: longshots systematically underperform their implied odds. Even if our edge calc is correct in expectation, longshot bets have far higher variance and slower convergence to true ROI. One-line filter, no harm if bias is absent.

Also catches a class of false positives where a single soft book overprices a 4.0+ underdog and dispersion just barely passes the filter.

### 1.3 Add SBK to UK_BOOKS rotation
**File:** wherever `UK_BOOKS` is defined (likely `scripts/scan_odds.py`) · **Adopt-ability: 4/5**

Smart Sports Trader article: SBK explicitly markets "we don't limit." If The Odds API carries SBK in `uk` region, we should be flagging on it preferentially since its lifespan as a usable account is materially longer than Bet365's. Worth verifying SBK is in the API's bookmaker list first.

### 1.4 Update stale CLAUDE.md note
**File:** `CLAUDE.md:7` · **Adopt-ability: 5/5**

The "corrected backtest pending (Plan phase 1.5)" note is wrong — `docs/BACKTEST.md` shows Shin-corrected numbers (generated 2026-04-29). Replace with one-line pointer: "Backtest results in `docs/BACKTEST.md` (Shin-corrected, 2026-04)." Trivial fix already discussed in chat.

---

## 2. Paper-variant candidates (Phase 5.5-style)

For shadow A/B testing in `src/betting/strategies.py`, alongside variants A–H. Each is independent and could be added in one commit.

### 2.1 `I_power_devig` — power method instead of Shin
Bethero recommends power as the best general-purpose default for two-way and multi-way markets. Method differences are small at balanced markets (0–0.5pp on draws/coin-flip h2h) but grow to ~1.25pp on heavy favourites — exactly the regime where bet sizing is most sensitive. We already have `src/betting/devig.py` with power implemented; just need a strategy variant that calls it.

### 2.2 `J_sharp_weighted` — sharpness-weighted consensus (see §1.1)

### 2.3 `K_draw_bias` — Predictology's draw-value filter
Restrict draw flags to fixtures meeting:
- Draw odds in 3.20–3.60 range
- Both teams in low-xG quartile
- (Optional) Late-season or second-leg cup tie

Filter implementable on EPL where we have understat xG data. If it shows positive CLV after 50 bets, it's a confirmed bias to exploit.

### 2.4 `L_zero_four_kelly` — 0.4-Kelly when dispersion is high
Currently we sit at half-Kelly across the board. Downey + Aldous suggest Kelly should shrink when uncertainty is high. **Heuristic**: when cross-book stdev > 0.025 (half our reject threshold), use 0.4-Kelly instead of 0.5. Above 0.04 we already reject. Smooth ramp, not a step function.

### 2.5 `M_min_prob_15` — stronger longshot guard (paper variant of §1.2)
If §1.2 ships at 0.10, also test 0.15 in shadow as a higher-conviction-only variant.

---

## 3. Out-of-scope but interesting

Ideas worth a note for future phases or other projects.

### 3.1 Smarkets-first as primary book (Phase 8 alternative)
Smartsportstrader: Smarkets at 2% commission (0% for matched bettors, 3% above £25k profit/year) is the cleanest exchange. Better commission profile than Betfair (which has Premium Charge after £25k). Phase 8 was scoped around Betfair API auto-placement; Smarkets has an API too and the economics are friendlier. Worth comparing API maturity before committing.

### 3.2 Lay-arb on outliers (free money when liquidity allows)
When a soft UK book is far above consensus on outcome A: **back A on the soft book + lay A on Smarkets**. If the soft odds × (1 - smarkets_commission) > consensus odds × Smarkets implied, this is a guaranteed profit independent of model edge. Requires real-time Smarkets liquidity check; could be a separate scanner with its own crontab.

### 3.3 Restriction-detection tooling
Track per-bookie max-stake limits hit on placement (manual log via dashboard). Alert when our Bet365 max drops from £200 to £20 — early signal of restriction. Simple state in `logs/bookie_limits.json`. Would feed into a "rotate primary book" decision.

### 3.4 Mug-bet camouflage cron
RebelBetting + punter2pro both recommend mixing in recreational-looking bets. Schedule a small (£5–£10) accumulator on mainstream football every weekend, automatically. No actual scanning needed — just place a known-mug bet. Lightweight cron + Telegram notification. Useful only if we actually start hitting restrictions.

### 3.5 Open-source the scanner
`georgedouzas/sports-betting` is the most mature comparable project on GitHub (PyPI distribution, scikit-learn integration, GUI, CI). Our system is more sophisticated in places (Shin devig, multi-sport, CLV diagnostics) and could be released as a permissive-licensed Python package after Phase 7. Strategic question: open-sourcing would attract contributors but also accelerate market saturation. Likely answer is *yes, but only the framework, keep the live scanner private*.

### 3.6 Beard accounts
Third-party placement networks to mask patterns. Legal in UK but operational lift is real. Listed for completeness — not recommending.

### 3.7 Walk-forward backtest infrastructure
Our existing `scripts/compare_strategies.py` and `BACKTEST.md` use whole-period evaluation. `georgedouzas/sports-betting` uses scikit-learn's `TimeSeriesSplit` for forward-chaining cross-validation. **This is the right pattern for our Phase 7 honest hold-out eval** — adopt it in `scripts/backtest_consensus.py` (or create one) before any model-overhaul claims.

---

## 4. Open questions

Worth resolving before locking in the changes above.

### 4.1 ScienceDirect 2024 paper
`https://www.sciencedirect.com/science/article/pii/S0169207024000670` (International Journal of Forecasting) refused our request. The paper's existence in our source list implies it's relevant — probably a Kaunitz replication, refutation, or extension. **Action**: try via Sci-Hub mirror or institutional access; or search arXiv for the same authors in 2024.

### 4.2 Aldous Berkeley Kelly paper
PDF couldn't render — needs `apt-get install poppler-utils`. **Action**: install poppler, re-fetch, get the cited bad-Kelly properties (variance, drawdown, time-to-converge bounds). The Aldous paper is the academic foundation for the half-Kelly heuristic — worth doing properly.

### 4.3 Does sharpness-weighted consensus yield *fewer* bets at our existing thresholds?
Sharper books produce tighter consensus → fewer outliers exceed 3% deviation. Shadow-test variant J before committing. If bet count drops > 50%, threshold may need to drop to 2.5% to keep flow.

### 4.4 SBK availability via The Odds API
Need to grep `bookmaker_key` outputs from a recent scan to see if SBK is in our `uk` region response. If not, no point adding it to UK_BOOKS.

### 4.5 Power devig vs Shin on EPL backtest
Cheap to test — we have `src/betting/devig.py` with both. Should produce a comparison table in `BACKTEST.md` before deciding which is the default. Bethero's recommendation is opinion; our own data is signal.

### 4.6 Is favourite-longshot bias measurable in EPL / Bundesliga at our odds range?
Most cited research is horse racing (where longshots are 50-100/1). Football longshots cap around 10/1 in main markets. Bias may be vestigial. Variant `M_min_prob_15` will tell us.

---

## 5. Notes on Tier B repos

| Repo | Take | Useful for |
|---|---|---|
| `Lisandro79/BeatTheBookie` | MATLAB; original paper code | Historical reference only |
| `georgedouzas/sports-betting` | Most mature; PyPI + scikit-learn + Reflex GUI; `TimeSeriesSplit` backtest | **Phase 7 backtesting structure**; possible OSS template |
| `konstanzer/online-sports-betting` | Faithful Python Kaunitz reproduction; "$2/hr effective rate" quote | Sobering reality check on hours-vs-profit |
| `sedemmler/WagerBrain` | Utility lib (Kelly, vig, ELO, parlay) | Reference only — we have better |
| `jacksebastian17/betting-algo` | Selenium + Twilio + pybettor | Mention of `pybettor` lib worth a 5-min look |

---

## 6. Sober anchors

Two findings to keep in mind whenever we're tempted by a new paper or repo:

1. **Konstanzer's "$2/hour" quote** — a faithful Kaunitz reproduction with real-money execution returned ~$2/hr effective rate over 5 months once you account for monitoring cost and account-restriction overhead. Edge is real but **operational cost is the binding constraint**, not modelling sophistication.
2. **HN 2024 consensus** — "the strategy still works, you just get banned." Every hour spent on edge sophistication beyond what we have is hours not spent on restriction-resilience (variants 3.1, 3.3, 3.4). Distribution of effort matters.
3. **Clegg & Cartlidge (2025) — "Not feeling the buzz"**: a single bad odds row (Bet365 listing 5.50 vs market 1.85 for Hercog/Doi 2019) generated >150% of a published 29.38% ROI study. When removed, ROI flipped to −7%. **Kelly + bad data = bankroll destruction**. Our outlier-z and per-fixture cap are explicit guards against this exact failure mode — keep them strict.

---

## 7. Reading list — IJF 2025 papers (read 2026-04)

User downloaded ~13 PDFs from `International Journal of Forecasting` Vol. 41 (2025). Three are directly relevant; full notes below.

### 7.1 Hegarty & Whelan (2025) — "Forecasting soccer matches with betting odds: A tale of two markets"

**Dataset**: 84,230 European soccer matches across 22 leagues (2011/12–2021/22) from football-data.co.uk. Open data: <https://zenodo.org/records/12673394>. R + Stata code published.

**Core findings:**

1. **The traditional home/away/draw market is empirically inefficient.** Strong favourite-longshot bias confirmed across the full dataset. Betting decile 1 (longshots) loses **17%** on average; decile 10 (favourites) loses 2%. Bias is steeply nonlinear, sharpest at the longshot end. Equally-weighted portfolio of all home/away/draw bets loses 7.8% — significantly more than the 6.5% loss rate the bookmaker's overround would predict under efficient markets.
2. **The Asian handicap (AH) market is empirically efficient.** No detectable favourite-longshot bias. Average loss rate 3.6% — half the H2H market — and matches the predicted ex ante rate within confidence intervals.
3. **Soft books dominate H2H, sharp books dominate AH.** Pinnacle (sharp) actually sells forecast probabilities to soft books for a fee. Sharp books accept informed bettors and use those bets to refine their lines. Soft books restrict winners and copy sharps with a margin.
4. **Methodology contribution**: closed-form formulas to convert AH odds → outcome probabilities (Eqs 6–28 in the paper), accounting for the four handicap types (integer, .25, .5, .75) and refund probabilities estimated empirically per handicap type.

**Implications for our system:**

- §1.2 (min-prob filter) is now **strongly evidence-backed** for football, not just a horse-racing extrapolation. Decile-1 17% loss is dispositive.
- The H2H market we currently scan is the *less efficient* market. Our edge depends on outliers, not on baseline efficiency.
- **AH market is a major opportunity** for a future variant — sharp books, lower margin (3.6%), and the methodology to compute fair probs from quoted odds is now public. Out-of-scope for the weekend test (we'd need to fetch AH odds — The Odds API does not currently include them in our regions), but high-priority for the planning doc.
- The 84k-match Zenodo dataset is also a candidate to replace/augment our football-data.co.uk EPL backtest.

### 7.2 Clegg & Cartlidge (2025) — "Not feeling the buzz: Correction study"

**Premise**: replicates Ramirez et al. (2023) "WikiBuzz" tennis betting study and finds 90%+ of reported 29.38% ROI came from **one bad data row** (Bet365 odds 5.50 vs market 1.85 for Hercog/Doi 2019, Kelly placed 39.3% of bankroll on the bad odds). After correction, only the "competitive matches" filter (`p ∈ [0.4, 0.6]`) retained positive ROI (12.44%).

**Implications:**

- Validates §3 sober anchor about Kelly + data quality. Our outlier-z filter would catch this exact case (z >> 2.5 for 5.50 against a 40-book mean of 1.85).
- **The competitive-match filter is empirically defensible**: when the original paper's sole survivor is "only bet on matches where consensus prob is in [0.4, 0.6]", that's a signal worth testing as a paper variant (`N_competitive_only`).
- Extension to 2020–2023 found the WikiBuzz signal disappeared. Markets become more efficient; backtest dates matter.

### 7.3 Hardy, Zhang, Hullman, Hofman, Goldstein (2025) — "Improving out-of-population prediction"

**Premise**: model-assisted judgmental bootstrapping for forecasting in domains without outcome data (Covid-19 example).

**Implications:**

- Tangentially relevant — applies if we want to forecast a league/sport where we have no historical data (e.g. extending from EPL to a new league). Method: combine the existing model's output with expert (= our calibrated-CLV-tracked) judgment to bootstrap a new model.
- Lower priority. Note for Phase 7 only.

### 7.4 MacLean, Thorp, Ziemba (2010) — "Good and bad properties of the Kelly criterion"

(Aldous-hosted PDF turned out to be the MacLean/Thorp/Ziemba paper, not Aldous himself.)

**Key findings:**

1. **Errors in *means* dominate errors in variances by 20:2:1**. So an error in our edge estimate (the probability) costs 20× what an error in our variance estimate costs. **Implication**: invest in better edge estimation (sharpness-weighted consensus, power devig, AH-derived anchor) before refining variance models.
2. **Never bet more than full Kelly.** Betting 2× Kelly produces growth = risk-free rate; beyond that, growth is negative. Half-Kelly is firmly inside the safe regime.
3. **Buffett ≈ full Kelly, Keynes ≈ 80% Kelly.** Half-Kelly is *conservative* by historical standards — there's room to increase if our edge confidence justifies it.
4. **Blackjack at 2% edge example**: half-Kelly P(double-before-half) = 0.89 with 0.75 relative growth; full Kelly = 0.99 P + 1.0 growth. Real cost of half-Kelly is ~25% growth in exchange for substantial drawdown protection.

---

## 8. Next steps — weekend test plan

**Goal**: have as many strategy variants live in the Phase 5.5 paper portfolio by Friday EOD as possible, so Saturday & Sunday scans collect shadow bets we can evaluate with `scripts/compare_strategies.py` on Monday.

The infrastructure already exists — `STRATEGIES: list[StrategyConfig]` in `src/betting/strategies.py`. New variants are added by appending entries; new behaviours need new fields on `StrategyConfig`.

### 8.1 Variants to ship (highest signal × lowest implementation cost)

Ordered by adopt-effort:

| ID | Variant | Mechanism | Files touched | Effort |
|---|---|---|---|---|
| **I** | `I_power_devig` | `devig="power"` | `strategies.py` (1 entry) | 10 min — already supported in `_apply_devig` |
| **L** | `L_quarter_kelly` | Kelly fraction = 0.4 | `strategies.py` + `kelly.py` (add `kelly_fraction` to `StrategyConfig`, plumb through) | 30 min |
| **M** | `M_min_prob_15` | `min_consensus_prob=0.15` | `strategies.py` (new field + filter) | 20 min |
| **N** | `N_competitive_only` | Reject if max-side prob > 0.70 (only "competitive" matches) | `strategies.py` (new field + filter) | 20 min |
| **J** | `J_sharp_weighted` | Per-book sharpness weights from datagolf table | `consensus.py` (extend weighted mode) + `strategies.py` (new `sharpness_weights` param + 1 entry) | 1.5h |
| **K** | `K_draw_bias` | Restrict draws to odds ∈ [3.20, 3.60] + low-xG fixtures | `strategies.py` + xG hookup | 2–3h (xG not currently in scanner runtime — defer if tight) |

Additions to `StrategyConfig`:
```python
min_consensus_prob:  float = 0.0          # M, N (longshot guard)
max_consensus_prob:  float = 1.0          # N (competitive-only)
kelly_fraction:      float = 0.5          # L (quarter-Kelly variant)
sharpness_weights:   dict | None = None   # J (book → weight, default None = uniform)
draw_odds_band:      tuple | None = None  # K (e.g. (3.20, 3.60))
```

### 8.2 Datagolf sharpness weights — initial table

Empirical seed for variant `J`. Refine based on our own CLV after 4 weeks.

```python
# Sharpness weights: higher = more trusted in consensus.
# Seeded from datagolf.com/how-sharp-are-bookmakers (blind-return at close).
# Books not listed default to 1.0.
SHARPNESS_WEIGHTS: dict[str, float] = {
    # Sharp tier
    "pinnacle":           2.0,    # excluded from consensus by default; weight applies if included
    "betfair_ex_uk":      1.5,
    "smarkets":           1.5,
    "matchbook":          1.5,
    # Mid tier (sharps' soft customers, decent calibration)
    "marathonbet":        1.0,
    "sportingbet":        1.0,
    "bwin":               1.0,
    "betvictor":          1.0,
    "williamhill":        1.0,
    "betfair_sb_uk":      1.0,
    # Soft tier (retail UK)
    "betfred_uk":         0.7,
    "coral":              0.7,
    "ladbrokes_uk":       0.7,
    "skybet":             0.7,
    "paddypower":         0.7,
    "boylesports":        0.7,
    "leovegas":           0.7,
    "casumo":             0.7,
    "virginbet":          0.7,
    "livescorebet":       0.7,
    "sport888":           0.7,
    "grosvenor":          0.7,
    "betway":             0.7,
}
```

### 8.3 Empirical-question tests (§4 open questions)

Run alongside variant testing — most are derivable from logs after the weekend.

| ID | Question | Test method |
|---|---|---|
| 4.3 | Does sharp-weighted consensus produce **fewer** flags? | After Sat scan: count `J_sharp_weighted` rows vs `A_production` in `logs/paper/*.csv`. If `J` count > 50% lower, drop variant edge to 2.5%. |
| 4.4 | Is **SBK** in The Odds API `uk` region? | Friday: grep `bookmaker_key` over the last 3 days of `logs/scan.log` for `"sbk"`. If present, add to `UK_LICENSED_BOOKS` next week. |
| 4.5 | **Power vs Shin** — backtest comparison | Re-run `scripts/backtest_consensus.py` (or main.py backtest) with `devig="power"` flag; produce a third column in `docs/BACKTEST.md` next to `raw` and `shin`. |
| 4.6 | Does **favourite-longshot bias** show up in our EPL data? | After Sun: bin settled bets in `logs/bets.csv` by consensus-prob decile, compute payout per decile. If decile 1 < decile 10 by ≥ 5pp, bias is present and `M_min_prob_15` is justified to graduate to default. |

### 8.4 Concrete weekend timeline

- **Friday (today/tomorrow)**:
  - Implement variants I, L, M, N (~80 min total). Tests pass.
  - Implement variant J + sharpness weights (~1.5h).
  - Skip K unless time allows (xG hookup is the bottleneck — schedule for next sprint).
  - Run `pytest` — verify no regressions in `A_production`.
  - Verify SBK presence (§4.4).
  - One smoke `scan_odds.py` run, eyeball `logs/paper/*.csv` to confirm all variants produce rows.

- **Sat 10:30 + 16:30 + Sun 12:30 scans (existing crontab)** collect weekend data automatically.

- **Monday morning**:
  - `python3 scripts/compare_strategies.py` → `docs/STRATEGY_COMPARISON.md` updated.
  - Run §4.6 favourite-longshot decile analysis (one-off SQL/pandas script).
  - Re-run backtest with power devig (§4.5), update `docs/BACKTEST.md`.
  - Decide which variants graduate from shadow to candidates for default-flip.

### 8.5 Out-of-weekend scope (planning carryover)

Logged here so they don't get forgotten — none are weekend-blocking:

- **Asian handicap data integration** (per §7.1): The Odds API doesn't surface AH in our `uk,eu` regions. Investigate whether premium-tier API or a separate AH source is feasible. If yes: implement Hegarty & Whelan's closed-form prob conversion (Eqs 6–28) → use AH-derived prob as a *second* anchor alongside Pinnacle.
- **Zenodo 84k-match dataset** (Hegarty & Whelan): replace our football-data.co.uk EPL CSV backtest with the broader European dataset. Likely a one-day project for Phase 7.
- **Walk-forward backtesting** (`georgedouzas/sports-betting`'s `TimeSeriesSplit` pattern): refactor `scripts/backtest_consensus.py` for honest hold-out evaluation. Phase 7 prereq.
- **CLAUDE.md:7 stale Phase 1.5 note**: trivial doc fix already discussed in chat.

### 8.6 Non-goals for the weekend

To keep scope honest:

- **No** real-money bets on new variants — paper portfolio only until ≥50 bets per variant settled.
- **No** refactor of the existing scanner. New variants extend `StrategyConfig`; they don't reshape the pipeline.
- **No** xG integration (defer K).
- **No** AH integration (out of scope, planning only).
- **No** Phase 8 / exchange auto-placement work.

---

## 9. Repo deep-dive — actual code patterns (added on review)

My initial Tier B pass was too shallow — I read READMEs, not code. On review, the comparable repos contain things I should have flagged the first time. Documenting in full here.

### 9.1 The Kaunitz formula we never had — recovered from `konstanzer` + `BeatTheBookie`

I never extracted the exact Kaunitz threshold rule from the paper (arXiv blocked us; PDF was binary). Both `konstanzer/online-sports-betting/odds_model.py` and `Lisandro79/BeatTheBookie/src/strategies/beatTheBookie.m` implement it identically and unambiguously:

```python
# Per outcome, where i ∈ {home_win, away_win, draw}:
earn_margin[i] = ((1 / avg_odds[i]) - alpha) * max_odds[i] - 1

# Place a bet on outcome with highest earn_margin if any earn_margin > 0,
# AND only if at least nValidOdds (3 in MATLAB, 4 in Python) bookies quoted it.
```

with `alpha = 0.05` (the paper's commission/margin adjustment).

**Unpacking**:
- `1 / avg_odds[i]` is the **raw average implied probability across all bookmakers** — *not* Shin-devigged. The α=0.05 implicitly compensates for the vig.
- `max_odds[i]` is the **best price across all books** — i.e. you bet at whichever bookie is offering the highest odds. This is "max-odds shopping," fundamentally different from our flag-when-this-specific-book-deviates approach.
- The threshold is **multiplicative on prob** rather than additive (3% gap on consensus).
- The minimum-book bar is **3–4 quoted bookies**, not our 20+.

**Implication.** Our system is *already a stricter, more sophisticated cousin* of Kaunitz — Shin-devig is more rigorous than the α=0.05 hack, dispersion + outlier-z filters add discipline, 20+ books is much higher signal. **But we've never benchmarked our system against the paper-faithful baseline.** We don't know how much of our 17.65% backtest ROI (Shin, 2% edge) is attributable to our additions vs. just being in a value-betting regime.

**Action.** Add a paper-faithful baseline variant `O_kaunitz_classic` (α=0.05, raw consensus, max-odds shopping, min 4 books) for backtest comparison. New phase R.1.5 below in PLAN.

### 9.2 `georgedouzas/sports-betting` has Kaunitz built-in — `OddsComparisonBettor`

The `evaluation/_rules.py` file has `OddsComparisonBettor` — a **production Python implementation of Kaunitz**, with `alpha` parameter, scikit-learn API, default `alpha=0.05`. The example in the docstring uses `alpha=0.03`.

Beyond the bettor itself, `evaluation/_model_selection.py` contains `backtest(bettor, X, Y, O, cv=TimeSeriesSplit(), n_jobs=-1)` returning a DataFrame with columns:
- `Training start`, `Training end`, `Testing start`, `Testing end`
- `Number of betting days`, `Number of bets`
- **`Yield percentage per bet`**, **`ROI percentage`**
- `Final cash`
- Per-market breakdown (e.g. `Number of bets (over_2.5__full_time_goals)`)

**Implication for R.5.5.** I had R.5.5 scoped as a from-scratch refactor. It doesn't need to be. **Better path**: install `sports-betting` as a dev dependency, subclass `BaseBettor` to wrap our Shin-devigged + dispersion-filtered consensus, and let georgedouzas' walk-forward backtest do the work. Effort drops from ~2.5h to ~1h, and we inherit the parallelism, per-market breakdown, and well-tested split logic for free. **Updated R.5.5 below.**

### 9.3 `WagerBrain` patterns worth borrowing (not the math — the API shape)

`bankroll.py:basic_kelly_criterion(prob, odds, kelly_size=1)` — the `kelly_size` parameter is **exactly the API shape** we want for variant `L_quarter_kelly`. Pass `kelly_size=0.4` from `StrategyConfig.kelly_fraction` straight through to `kelly()`. Don't reinvent.

(WagerBrain's actual formula has a sign error — `(b*q - p)/b` instead of `(b*p - q)/b` — so don't *copy* the math, just the shape.)

`probs.py:elo_prob(elo_diff)` — ELO-based prior probability. Could be a future strategy variant `Q_elo_prior` that flags when consensus aligns with ELO — model-agreement filter without CatBoost dependency.

### 9.4 `pybettor` (active fork: `ian-shepherd/pybettor`, last update 2025-11)

PyPI: `pip install pybettor`. Provides `implied_prob(odds, category)` for US/decimal/fractional, plus more (the README cuts off — full function list TBD). Worth a 30-min skim before we re-implement any odds math.

**Decision pending:** add as a runtime dep, or just reference for our own implementation? Default to "just reference" until we know what they have that we don't.

### 9.5 The "max-odds shopping" idea (entirely missing from our system)

Both Kaunitz reproductions use `max_odds` (best price across all books). We currently flag when a *specific* UK book's odds exceed consensus by 3%. These are different signals:

- **Our approach**: "Bet365 is offering 2.50 vs consensus 2.40 — flag Bet365."
- **Kaunitz approach**: "Across 36 books, the best price is 2.50 (somewhere) vs consensus 2.40 — flag, place at the best book."

Why does this matter? In our system, if Skybet offers 2.55 (better than Bet365's 2.50) but isn't a UK_LICENSED_BOOKS member or fails some other filter, we miss the bet. Kaunitz takes whichever UK book has the best price — strictly better economics if the bookie list is the same.

**Action.** Optional follow-up variant `P_max_odds_shopping` — flag when `max(odds_over_uk_books) > 1 / (consensus_prob - α)`. Phase R.1.6 below, sized as low-priority.

### 9.6 Things in the repos NOT worth copying

For completeness, things I dug into that don't add value:

- **WagerBrain Fibonacci / Labouchere bankroll** — gambler's-fallacy progression systems, not value betting.
- **WagerBrain US-odds conversion** — we don't bet US markets.
- **BeatTheBookie's MATLAB code** — Python equivalents exist in `konstanzer`. No reason to read MATLAB.
- **`georgedouzas` GUI (Reflex)** — our Flask dashboard is sufficient. Don't add another runtime.
- **`jacksebastian17/betting-algo` Selenium scraper** — fragile, unnecessary; we use The Odds API.

### 9.7 Summary of what changed in my recommendations after this review

| Before review | After review |
|---|---|
| 5 paper variants planned (I, J, L, M, N) + K (xG-deferred) | **Add O_kaunitz_classic (paper-faithful baseline) and P_max_odds_shopping (optional)** |
| R.5.5 = 2.5h from-scratch walk-forward refactor | R.5.5 ≈ 1h: subclass `BaseBettor` from `sports-betting` package |
| `kelly_fraction` invented field on StrategyConfig | Match WagerBrain's `kelly_size` API shape exactly |
| No baseline-vs-our-system comparison ever | **R.1.5 establishes paper-faithful baseline so we can measure how much our additions actually help** |
| Asian Handicap as the only "expand markets" idea | Plus: ELO prior, max-odds shopping, fractional-odds support are now on the radar |

The most consequential of these is **R.1.5 + having a paper-faithful baseline**. Without it, our 17.65% Shin backtest result is uncalibrated against the academic prior.
