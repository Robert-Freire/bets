# Data Acquisition Ideas

Living doc. Things we could do to get more / better data than today's stack (The Odds API + FDCO). Append as ideas come up; mark status as they get executed or ruled out. Sister doc to `PAID_DATA_WISHLIST.md` — that one is specifically about paying for Odds API historical; this one is broader.

## Today's data stack (baseline)

- **The Odds API** — live odds, ~36 books on UK+EU, h2h + totals, free 500 cr/mo. Primary scanner input.
- **football-data.co.uk** — free Pinnacle close CSVs, top-6 leagues, h2h + totals 2.5, ~24–48h delay. CLV source.
- **Understat** — weekly xG snapshots for 4 EPL/Bundesliga/Serie A/Ligue 1.
- **Azure Blob `raw-api-snapshots`** — every Odds API response archived since 2026-05.

## Provider landscape (researched 2026-05-02)

### Worth trialling

- **OddsPapi** (oddspapi.io) — 350+ books incl. Pinnacle, Singbet, SBOBet, Betfair Exchange. Free tier 250 req/mo with **timestamped historical/closing lines included**. $49/mo flat per-request. Could replace both The Odds API (primary) and FDCO (CLV) in one move if data quality holds. **Trial gate:** Pinnacle close agreement with FDCO within ~0.2% on 20+ fixtures.
- **SportsGameOdds** (sportsgameodds.com) — 80+ books incl. Pinnacle, explicit closing-odds endpoint, free tier. Useful as cross-check oracle against OddsPapi.
- **SharpAPI** (sharpapi.io) — built-in +EV-vs-Pinnacle and arb/middle alerts, free tier. They've built our consensus layer; trial as oracle to sanity-check our Kaunitz output, not as a primary feed.
- **Tennis-Data.co.uk** — FDCO's sibling, free CSVs of ATP/WTA fixed odds inc. Pinnacle close back to 2003. **Closes our tennis CLV gap at near-zero engineering cost.** Should integrate regardless of other decisions.

### Ruled out

- **odds-api.io** — explicitly dropped Pinnacle after the Jul-2025 shutdown. Defeats the purpose.
- **RapidAPI Pinnacle resellers** (`tipsters/pinnacle-odds` et al.) — fragile; either grandfathered reseller or frontend scraper of post-shutdown Pinnacle. Single-point-of-failure.
- **Unabated** — US sports only ($3k/mo).
- **OpticOdds, Sportradar, SportsDataIO** — enterprise/B2B, sales-gated.
- **Singbet/SBOBet/IBCBet brokers** (BetInAsia, VOdds) — deposit + contract gated, limiting risk on our account. Rely on OddsPapi's pipe instead.

## Ideas

### Steam-chase scraper — reactive, not generic

**The idea:** when Pinnacle moves sharply (steam), some soft books lag for seconds-to-minutes. During that window the soft book's price is stale relative to the "true" price Pinnacle just discovered. If the gap is large enough, that's a value bet that arrives *after* the steam, not *before* — different signal class from our consensus-vs-soft Kaunitz path.

**Why it's interesting:** independent edge source from our consensus model; doesn't need calibration, just speed.

**Why it's hard:**
- Needs Pinnacle live odds at sub-minute granularity (post-shutdown means OddsPapi or paid Odds API, not free).
- Needs soft-book live odds at sub-minute granularity for the *specific* books we'd bet at — The Odds API polls aren't fast enough; would need a scraper or a paid live feed per book.
- Detection threshold + dwell time are empirical — needs **a lot of paired (Pinnacle move, soft-book lag) observations** to learn what "big enough" means.
- Soft books rate-limit + bot-detect; scraping from our own residential IP risks the betting account.
- ToS exposure on both sides.

**Trigger to revisit:** OddsPapi trial proves out AND we have ≥30 settled bets with positive CLV from the consensus path AND we still want a second edge source. Until then this is a research idea, not a build.

**Minimum viable probe (cheap, no scraper):** during the OddsPapi trial, log T-60/T-30/T-15/T-5 Pinnacle snapshots for the same fixtures as our scanner pulls. Post-hoc, identify Pinnacle moves >X% in <Y minutes, then check whether *any* soft book in our existing Odds API snapshots was still showing the pre-move price at our scan time. If yes, the lag exists and is detectable in our current data — only then is the live scraper worth building.

### Tennis-Data.co.uk ingest

Free, ATP+WTA Pinnacle close CSVs back to 2003. Patterned exactly on `scripts/backfill_clv_from_fdco.py`. Closes the tennis CLV gap noted in `CLAUDE.md`. ~1-day engineering. Should ship next time tennis bets get flagged.

### Per-bookmaker scrapers (generic, not reactive)

Probably **not worth it** for the books we currently scan — The Odds API already covers them, ~$25/mo for paid tier is cheaper than maintenance time, and scraping a book from our own residential IP accelerates account limiting.

Narrow case where it could make sense: a specific small book missing from The Odds API AND consistently mispricing a niche league we want to add (e.g., Scandinavian/South American). Done from a separate residential IP with no account at that book. Only after CLV evidence shows the gap is real edge.

### Asian-broker pipe (BetInAsia / VOdds)

Real Singbet/SBOBet/IBCBet odds + actual placement at sharp Asian books. Bypasses the "your soft-book account gets limited" problem entirely. Cost: deposit + spread on commissions. Operationally very different from our current scanner (different account model, different liquidity dynamics, no UK tax treatment). Park as "Phase 10+ multi-account" territory.

### Twitter/Reddit signal scrape

Sharp tipsters and arbers post moves on Twitter/Reddit before retail catches them. Would need NLP + reputation tracking to filter signal from noise. Sample-size and false-positive risk both look bad. Low priority.

### Weather / lineup feeds

Lineups (already partially captured by the Fri-19:30 scan timing) and weather (rain/wind for totals) are real signal. Free APIs exist (OpenWeatherMap, BBC Sport scrapes). Only worth wiring up once we have a model that *uses* them — currently CatBoost doesn't, so this is gated on the Phase 7 model unfreeze.

### Public xG / shot-data sources beyond Understat

FBref, StatsBomb open data, WhoScored. More leagues than Understat. Engineering cost is real (each has its own scraping or download cadence). Worth investigating if/when we extend the model beyond the four leagues Understat covers.

## Status log

- 2026-05-02: doc created. Captures provider landscape sweep + steam-chase scraper idea.
