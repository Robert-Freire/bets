# Betting System — Thorough Review

*Reviewed: 2026-04-29*

## TL;DR

The architecture and choice of papers is solid, but the implementation has **one theoretically important bug**: the consensus is computed as the *raw* mean of `1/odds` instead of per-book margin-removed (de-vigged) probabilities — which is what Kaunitz et al. and the literature actually use. This systematically **inflates every reported edge by the average bookmaker margin (~3–6%)**, and as a consequence inflates Kelly stakes too. A "3% edge" in your logs is closer to a 0% edge in reality on a typical 1X2 market with average margin ~4–5%.

Beyond that: you're missing the most informative diagnostic in this whole field (closing-line value vs Pinnacle), you're targeting the bookmaker population that restricts the fastest (UK-licensed soft books), and a 2022 independent replication of the Kaunitz paper found the published edge had **disappeared** — soft books have closed the gap. So the realistic expectation isn't "+6% ROI", it's somewhere between mildly positive and break-even, with restriction risk capping bankroll growth.

The CatBoost layer is interesting but currently has no measurable edge (RPS 0.213 vs market 0.196), so its use as a "filter" is on shaky ground — the +26% backtest figure is probably small-sample noise.

The good news: every problem here is fixable, and a few of the fixes are 1-day jobs.

---

## 1. Theory review

### What's right
- **Choice of strategy is sensible.** Using market consensus as the ground-truth proxy is robust, model-free, and honest about how hard it is to beat the market.
- **Half-Kelly capped at 5%.** Standard, conservative, in line with both Kaunitz and Hubáček.
- **UK-licensed bookmaker filter.** Pragmatic — no point flagging bets you can't actually place.
- **Confidence tiers by book count.** Reasonable proxy for consensus reliability.
- **Walk-forward training in `model_signals.py` / `main.py`.** No future leakage in the time split.

### Where the theory deviates from the paper
1. **Raw vs margin-removed consensus.** Both `src/betting/consensus.py:60-67` (`np.mean(book_probs)` over raw `1/odds`) and `scripts/scan_odds.py:255-261` use raw implied probabilities. Your own paper summary at `docs/papers/kaunitz_2017_summary.md:32-38` describes the correct formula (per-book strip of margin via `fair = raw / (1+margin)`, *then* average). The code does the wrong thing. Empirically this means:

   - On a typical EPL 1X2 market with average margin ~5%, every reported edge is overstated by ~1.5–3 percentage points (margin/3 distributed across H/D/A).
   - Worse: **a sharp/exchange book with low margin will be flagged as "value" simply because it has lower margin than the average**, not because it's mispriced. Look at your `bets.csv` — `betfair_ex_uk`, `matchbook`, `smarkets`, `casumo`, `coral`, `betfred_uk` dominate. Those are exactly the lowest-margin books in the panel.

   Confirmed with a quick simulation: a Betfair-style 1% margin book flagged as "+3.3% edge raw" came out to **−0.5% edge** once consensus was properly de-vigged. The Kelly stake also collapses from 3.1% of bankroll to 0%.

2. **Use Pinnacle (or Betfair Exchange) as the anchor, not as one of N voters.** DataGolf's analysis suggests >95% of optimal forecast weight goes to Pinnacle alone; averaging in soft books actually *adds noise*. The paper from 2017 used many soft books because Pinnacle has US restrictions, but every modern variant of this strategy uses Pinnacle's de-vigged price as truth and only triggers on soft-book deviation from it.

3. **Live odds vs closing odds.** Kaunitz tested on closing odds. You're scanning live, often days before kickoff. Pre-market lines are noisier and closing lines incorporate lineups/injuries. Some of your "edge" is really "I'm faster than the soft book updates", which gets arbitraged away as kickoff approaches.

4. **Replication failure (2022).** The strategy in its naive form is **no longer profitable** in independent tests — soft books have largely closed the gap. The published 2017 numbers are best read as an upper bound for that era.

5. **Account restrictions.** UK-licensed soft books (William Hill, Sky Bet, Paddy Power, Coral, Ladbrokes, BetVictor, etc.) are the most aggressive at restricting winners. Within 50–200 winning bets they will limit you to pennies. Your only sustainable books from the current `UK_LICENSED_BOOKS` set are the exchanges (`betfair_ex_uk`, `smarkets`, `matchbook`) — but their commission (2–5%) eats most of a 3% edge, so you'd need to raise the threshold to ~5% for the exchange-only path to make sense.

### CatBoost layer
- The system itself is well-built (pi-ratings → rolling form → xG features → CatBoost), but **the model RPS (0.2137) is worse than the bookmaker (0.1957)**. That's a meaningful gap — it means standalone the model has *negative* edge.
- Using a model with no edge as a "filter" can still help in theory if its errors are uncorrelated with consensus errors, but the +26% backtest in the README is from a small-sample combined test and almost certainly survivor-fitted. Don't bet real money on it being real.
- The model also can't predict newly promoted teams (no history), which is exactly when uncertainty is highest and bookmakers are most likely to mis-price. So you're filtered out of the bets that probably matter most.

---

## 2. Bugs & code-correctness issues

### Critical (affects every bet you flag)

1. **Edge inflated by average book margin.** Fix in `src/betting/consensus.py:55-66` and `scripts/scan_odds.py:254-256`: per-book, normalize `1/odds` so each book's probabilities sum to 1, then average. Use Shin's method or proportional de-vig. The CRAN `implied` package and `WagerBrain` are good references.

2. **Kelly stake uses raw consensus.** `scripts/scan_odds.py:303` and `:414`: `kelly = 0.5 * (cons * odds - 1) / (odds - 1)`. With raw `cons` containing margin, this is biased upward. Fix in lockstep with #1.

3. **Duplicate bet logging across scan runs.** Confirmed: `bets.csv` has the same Arsenal-Fulham AWAY at 02:23 and 02:26 the same night. The scanner appends without checking whether the same `(home, away, side, kickoff)` is already in the CSV. Either dedupe on append, or log to SQLite with a unique key on `(kickoff, home, away, side)`.

4. **Bet ID is the CSV row index** (`app.py:30, 105`). If the scanner appends new rows while the dashboard is open, an `/update/<id>` POST can land on the wrong row. Race-window is small but not zero. Use a UUID per bet.

5. **CSV write isn't atomic.** `app.py:48-51` rewrites the whole CSV; `scripts/scan_odds.py:431-458` appends. They can collide. Lock via `fcntl.flock` or move to SQLite.

### Medium

6. **`main.py:152-161` runs `min_kaunitz_edge` and `min_model_edge` against the same dataset that produced both signals.** The "+26% combined" number in the README is a backtest on a model whose RPS is worse than the market. Treat this number as exploratory until it's reproduced on a held-out period.

7. **Pi-rating home advantage is fixed at 0.4** (`src/ratings/pi_ratings.py:34`). Constantinou's original updates this dynamically — minor, but means the rating is weakly biased early in the data.

8. **Tennis scan with `max_tennis=99`** (`scripts/scan_odds.py:217, 360`). At 2 region credits per tournament, an active fortnight can blow your 500/month budget. Hard-cap to 5–10 most-liquid tournaments and rely on `min_books`.

9. **`_API_TO_FD` (`scan_odds.py:37-127`) and `_NAME_MAP` (`features.py:11-40`) are partial.** Any unmapped team silently returns `?` model signal. Add an assertion that every team in a fetched event is mappable, and notify on misses.

10. **`logs/scan.log` shows La Liga**, but `FIXED_SPORTS` and `CLAUDE.md` say it's excluded. The log is stale — fine — but the contradiction is confusing if you're debugging.

11. **No total-stake cap per scan.** With 30-50 flagged bets per weekend × 5% cap, you could be staking 100%+ of bankroll across one matchday. Add a portfolio-level cap (e.g. 15% of bankroll across all open positions).

12. **No correlated-bet handling.** Same fixture flagging HOME and DRAW counts as two independent Kelly bets. They aren't independent.

### Minor

13. **`/update/<bet_id>` accepts any odds value** without server-side bounds (only client-side `min=1`). Easy to break the CSV with a non-numeric in `result` (currently fine because POST goes through `result.upper()` and unknown maps to no PnL — but the column will hold `XYZ`).
14. **Templates use `onclick="…('{{ b.home }}')"`** in JS string contexts (`templates/index.html:159, 161, 214`). Jinja's HTML autoescape doesn't fix JS-string-context injection. Team name "O'Brien FC" or any input with `'` could break the JS. Use `tojson` filter or a `data-*` attribute + `addEventListener`.
15. **`logs/sports_cache.json` 14-day skip-rule** (`scripts/check_sports.py:147-150`) is implicit — no `if (now - checked_at) > 14 days` actually exists. The current code skips forever once a sport has been seen with `< MIN_AVG_BOOKS`. Either remove the comment or implement the freshness check.
16. **Unused `xgboost_model.py` deleted** but `catboost_model.py` still has the XGBoost fallback path with `use_label_encoder=False` (deprecated in modern xgboost; will warn).
17. **`scripts/scan_odds.py:399`** sends a "no bets" notification on every empty scan. With 6 scans/day on a quiet weekday, your phone will buzz frequently. Consider only notifying on a daily summary cadence.

---

## 3. Missed opportunities (improvements)

These come from the literature review and from comparable open-source projects (`Lisandro79/BeatTheBookie`, `georgedouzas/sports-betting`, `konstanzer/online-sports-betting`, `WagerBrain`).

### High value, low effort

- **Closing-line value (CLV) tracking.** The single most informative diagnostic for whether you're sharp. At kickoff (or T-5min), re-fetch the Pinnacle/Betfair closing odds and log them next to every bet. After ~100 bets, average CLV vs close tells you whether you have edge — much faster than waiting for ROI to converge.
- **Pinnacle as anchor.** Free in The Odds API (`pinnacle` key). Either weight it 3–5× in the consensus, or use its de-vigged price *as* the truth and trigger on soft-book deviation only.
- **Shin's method for per-book de-vigging.** ~30 lines of code; biggest single improvement to edge accuracy.
- **Dispersion filter.** Reject bets where the std-dev of `1/odds` across books is above some threshold (the BeatTheBookie repo uses this). High dispersion = books genuinely disagree = consensus is unreliable.
- **Trimmed mean / median consensus** instead of plain mean. Robust to a single rogue book.
- **Outlier check on the flagged book itself.** If the UK book's price is itself >2σ from the rest, it's probably a stale or erroneous quote — skip.
- **Drift snapshots.** Capture odds at T-60min, T-15min, T-1min. If the market moves *toward* your bet, you're sharp on it; if *away*, you grabbed an old price. Lightweight version of CLV using existing API calls.
- **Stake rounding.** Round half-Kelly to the nearest £5. Fractional precision (£12.58, £15.99) screams "value bettor" to risk teams.
- **Drawdown brake.** Cut stake size when bankroll is ≥15% below peak; restore at new peak. Standard practice; dramatically reduces blow-up risk.
- **Multi-market scan.** `markets=h2h,totals,btts` on the Odds API costs the same credit per call. Totals (over/under 2.5) and BTTS often have lower margins and slower-moving lines than 1X2.
- **Per-fixture exposure cap.** Cap total stake per fixture at one Kelly-equivalent regardless of how many sides you flagged.

### Medium value, medium effort

- **Bookmaker tiering / weighted consensus.** Pinnacle, SBOBet, Matchbook, Smarkets are sharp; weight them higher. Bet365, William Hill etc. weight lower.
- **Exchange liquidity check.** Before treating a Smarkets/Matchbook/Betfair "value bet" as placeable, query the order book depth — exchange listed odds often don't have meaningful size at the displayed price.
- **Per-bet UUID + SQLite.** Replace the CSV+row-index pattern. Solves bug #3, #4, #5 simultaneously.
- **Live model retraining.** Currently `model_signals.py` runs weekly; team form moves daily. Run it daily from cron, before each scan.
- **Reduce model staleness via in-scan feature build.** Instead of pre-computing N×N pairs, pull the actual fixture list and only compute features for the matches that are about to be played.
- **Calibration on the CatBoost output.** With RPS worse than the market, isotonic or Platt calibration on a held-out fold may close some of the gap.
- **Log Pinnacle's de-vigged closing prob into `bets.csv`** as `pinnacle_close_prob`, then auto-compute CLV.
- **Bayesian / shrinkage edge.** Penalise the edge by its uncertainty (`f* = (μ − σ²/μ) / (odds − 1)`). Naturally shrinks stakes on noisy signals.

### Lower priority but worth doing eventually

- **Switch backtests to walk-forward across all historical bookmaker odds in football-data.co.uk** rather than a single `since=1415` window. The current `main.py` does walk-forward for the model; consensus backtests use the whole period at once.
- **Track and analyse "flagged but not placed" bets.** This is your control group — without it you can't tell whether the bets you skipped were the right ones to skip.
- **Add longshot debias.** Flag away/draw bets at odds >5.0 with extra scrutiny (favourite-longshot bias is well-documented and the un-devigged consensus is most wrong here).
- **Notification dedupe.** Cron runs 6×/day; same bet appears in multiple scans. Deduplicate notifications via a small `notified.json` keyed on `(kickoff, home, away, side, book)` until kickoff.

---

## 4. Future additions

The `todo.md` and `CLAUDE.md` already mention several of these. Order of value:

1. **Betfair API auto-placement.** Already planned. Note: with exchange commission (2–5%) eating into the edge, your minimum edge threshold should rise to ~5% on exchanges, not stay at 3%. Build dry-run mode first; require a 24-hour shadow period before turning on real money.
2. **Pinnacle as anchor + CLV.** Bigger win than auto-placement.
3. **Migration to Raspberry Pi / Azure read-only dashboard.** Already documented in `docs/PI_AZURE_SETUP.md`. Be aware Azure dashboards talking to a Pi need a public-facing endpoint; use Tailscale or Cloudflare Tunnel to avoid opening the Pi to the internet.
4. **Multiple markets (totals, BTTS, AH).** Free credit-wise.
5. **Replace CSV with SQLite.** Schema + UUIDs solve many small bugs at once.
6. **Pre-flight liquidity / freshness check.** Before flagging, re-fetch the single fixture and confirm the price is still there.
7. **Bayesian / weighted ensemble of consensus + CatBoost + Dixon-Coles.** Currently you have all three but only consensus is in production. The Dixon-Coles model in `src/model/dixon_coles.py` is unused — including it as a third independent vote would be more justifiable than the current CatBoost-as-filter approach.
8. **Bet syndicate / multi-account support.** When restrictions hit, bankroll splits across accounts is the only sustainable answer.

---

## 5. Concrete priority list (rank by ROI per hour of work)

1. **Fix the consensus de-vig** (`consensus.py`, `scan_odds.py`). Per-book proportional de-vig at minimum; Shin's method ideally. *2–3 hours.* Highest expected impact — will probably halve the number of "value bets" flagged, but the surviving ones will be real.
2. **Add Pinnacle as anchor and CLV logging.** Add `pinnacle` to The Odds API call, log `pinnacle_close_prob` and `clv_pct` to `bets.csv`. *3–4 hours.* The diagnostic that tells you whether the system is actually sharp.
3. **Dedupe across scan runs + UUIDs.** *2 hours.* Eliminates bug #3, #4, #5.
4. **Dispersion + outlier-book filters.** *1–2 hours.* Removes a category of false positives.
5. **Stake rounding + per-fixture cap + drawdown brake.** *2 hours.* Risk management hygiene.
6. **Multi-market scan (totals, BTTS).** *2–3 hours.* Doubles addressable market without doubling API budget.
7. **Daily `model_signals.py` cron + drift snapshots.** *1 hour cron + ~3 hours drift logic.*
8. **Switch to SQLite.** *3–4 hours.* Pays for itself the first time you avoid a CSV race.
9. **Decide on the CatBoost layer.** Either properly calibrate it and hold out a season for honest validation, or remove it from the user-facing flag set until it has demonstrated edge. Right now it's a story without numbers.
10. **Re-evaluate UK-soft-book targets.** If you're serious, restrict the flagged set to exchanges only (Betfair / Smarkets / Matchbook) and raise `MIN_EDGE` to 5–6% to compensate for commission. The other UK books will restrict you long before you accumulate a meaningful sample.

---

## 6. Two things to reconsider strategically

- **Be honest about expected ROI.** The 2017 paper's +3.5% in-sample / +8.5% live is the high-water mark. Modern replications fail. With proper de-vigging, exchange-only commission, and account caps, a realistic target is **0% to +2%** — barely profitable but a great proving ground for the technical pipeline. Don't size up bankroll based on the optimistic numbers.
- **Closing-line value, not P&L, is your North Star.** It's how you'll know whether the system has edge in 50 bets instead of 1000. If you build only one improvement from this review, build CLV logging.

---

## Sources

### Kaunitz strategy
- [arXiv 1710.02824 — Kaunitz, Zhong, Kreiner (2017)](https://arxiv.org/abs/1710.02824)
- [GitHub: Lisandro79/BeatTheBookie (official code)](https://github.com/Lisandro79/BeatTheBookie)
- [MIT Technology Review coverage](https://www.technologyreview.com/2017/10/19/67760/the-secret-betting-strategy-that-beats-online-bookmakers/)
- [Sportshandle: Sportsbooks vs Academics critique](https://sportshandle.com/sportsbooks-vs-academics-one-wins-battle/)
- [Hacker News: 2022 replication discussion](https://news.ycombinator.com/item?id=42112855)
- [Forecasting soccer matches with betting odds (2024 review)](https://www.sciencedirect.com/science/article/pii/S0169207024000670)

### Devig methods, sharp vs soft books
- [DataGolf: How sharp are bookmakers? (Pinnacle weighting)](https://datagolf.com/how-sharp-are-bookmakers)
- [DRatings devig method comparison](https://www.dratings.com/a-summary-of-different-no-vig-methods/)
- [implied R package vignette (Shin's method)](https://cran.r-project.org/web/packages/implied/vignettes/introduction.html)
- [Clarke et al. 2017 — Adjusting Bookmaker's Odds for Overround](https://www.researchgate.net/publication/326510904_Adjusting_Bookmaker's_Odds_to_Allow_for_Overround)
- [Devigging methods explained (Shin / proportional / power)](https://betherosports.com/blog/devigging-methods-explained)
- [Outlier — sharp vs soft books](https://help.outlier.bet/en/articles/9922960-how-sportsbooks-set-odds-soft-vs-sharp-books)

### Closing line value, drift, drawdown
- [Pinnacle: What is Closing Line Value (CLV)](https://www.pinnacle.com/betting-resources/en/educational/what-is-closing-line-value-clv-in-sports-betting)
- [TheLines — CLV explained](https://www.thelines.com/betting/closing-line-value/)
- [Pinnacle Odds Dropper — CLV blog](https://www.pinnacleoddsdropper.com/blog/closing-line-value)
- [Punter2Pro: Beating the closing line](https://punter2pro.com/punters-guide-beating-the-sp/)

### Account restrictions, exchange options
- [Punter2Pro: avoid restrictions](https://punter2pro.com/prevent-betting-accounts-restricted-closed/)
- [RebelBetting: avoid bookmaker limitations](https://www.rebelbetting.com/blog/how-to-avoid-bookmaker-limitations)
- [Smart Sports Trader: exchanges that don't limit](https://smartsportstrader.com/sports-betting-bookmakers-exchanges-dont-limit-uk-customers/)

### Bet sizing
- [Matthew Downey — fractional Kelly simulations](https://matthewdowney.github.io/uncertainty-kelly-criterion-optimal-bet-size.html)
- [Aldous — Good and Bad Properties of Kelly](https://www.stat.berkeley.edu/~aldous/157/Papers/Good_Bad_Kelly.pdf)
- [Wikipedia: Kelly criterion](https://en.wikipedia.org/wiki/Kelly_criterion)

### Comparable open-source projects
- [georgedouzas/sports-betting](https://github.com/georgedouzas/sports-betting)
- [konstanzer/online-sports-betting](https://github.com/konstanzer/online-sports-betting)
- [sedemmler/WagerBrain](https://github.com/sedemmler/WagerBrain)
- [jacksebastian17/betting-algo](https://github.com/jacksebastian17/betting-algo)

### Other
- [Predictology: The draw underbetting bias](https://www.predictology.co/blog/the-psychology-of-the-draw-why-market-bias-often-creates-massive-hidden-value-in-the-x-outcome/)
- [Wikipedia: Favourite-longshot bias](https://en.wikipedia.org/wiki/Favourite-longshot_bias)
- [BettorEdge — outlier identification](https://www.bettoredge.com/post/identifying-outliers-in-sports-betting-data)
