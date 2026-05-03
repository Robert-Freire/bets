# Exploratory Plan — Fixture Calendar

Living doc. Captures **what becomes possible** if we build a fixture calendar, beyond the original outage-detection ask in issue #7. Each downstream piece is its own gated decision; the calendar itself is the shared substrate.

## Origin

GitHub issue [#7](https://github.com/Robert-Freire/bets/issues/7) — distinguishing API outages from legitimate empty weeks. That's the narrowest motivating use case. The broader question this doc explores: *if we know in advance which fixtures are scheduled and when, what does that unlock?*

## What "the calendar" means

The `fixtures` table in Azure SQL, populated weekly by `scripts/ingest_fixtures.py` via `FixtureRepo`. Read by `src/data/fixture_calendar.py` (Pi-safe: no-op when DB env vars unset). Covers at least:

- Active 6 prod leagues + 5 dev candidates wired in M.2.
- Forward window: rolling 8 weeks (covers international breaks + season-end).
- Backward window: kept indefinitely (becomes the historical baseline).

Source candidates (cheapest first):

1. **football-data.co.uk fixtures CSVs** — free, current-season fixture list per league, refreshed by the publisher; we already ingest results from the same domain.
2. **Odds API `/sports/?all=false`** — 1cr/call, gives `active` flag per sport (binary, week-level — useful but coarse).
3. **api.football-data.org** — free tier covers EPL/Bundesliga/Serie A/Ligue 1 fixture lists with kickoff times.
4. **openligadb** — free, Bundesliga + Bundesliga 2 specifically, with kickoff times.
5. **League iCal feeds** — manual; brittle. Fallback only.

Recommended primary: FDCO fixtures CSV + api.football-data.org for kickoff times. Both free.

## Benefits — ordered by leverage

### 1. Cron tailored to actual kickoffs (not "one size fits all")

Current cron is fixed: Sat 10:30, Sat 16:30, Sun 12:30 UTC (+ Tue 07:30, Fri 19:30). It hits every league regardless of whether that league has games that day.

With a calendar:

- Schedule each scan at T-90min before a *cluster* of kickoffs in scanned leagues.
- A weekend with EPL 12:30 + Serie A 14:00 + Ligue 1 16:00 wants three scans at 11:00 / 12:30 / 14:30, not the current two arbitrary slots.
- Midweek European competition weeks (currently invisible) get their own pattern.
- Save credits on weekends where one of the 6 leagues has no games — skip that league specifically.

This is the user-flagged primary motivator. Estimated saving: **15–25%** of monthly credits, plus better proximity-to-close on every flagged bet.

### 2. Outage vs empty-week disambiguation (issue #7's core ask)

Canary returning 0 + calendar saying "no fixtures expected" → silent skip.
Canary returning 0 + calendar saying "fixtures expected" → confirmed outage, ntfy alert.

Eliminates the false-positive outage alerts during international breaks.

### 3. Closing-line proximity without re-enabling polling

`closing_line.py` was paused because per-fixture every-5-min polling was projected at 700–1000 cr/month. With per-fixture kickoff times in the calendar, a much cheaper alternative becomes possible: **single T-5min snapshot per fixture**, scheduled exactly. ~2 cr per fixture × ~60 fixtures/week = ~520 cr/month — still over budget on free tier, but feasible on the paid tier and gives genuine closing-line CLV without burning the live scanner's quota.

Cross-references `docs/PAID_DATA_WISHLIST.md` items #4 and #7.

### 4. Bet-to-result matching becomes deterministic

Currently `bets.csv` ↔ FDCO results match relies on fuzzy team-name normalisation (accented chars, "FC" suffixes, abbreviations). The calendar gives a deterministic `(league, date, home, away)` key per fixture, populated *before* odds are fetched. Eliminates a class of join bugs in `backfill_clv_from_fdco.py`.

### 5. Forward-load projection for the per-book skill plan

`docs/PLAN_BOOK_SKILL_2026-05.md` §B.2 is sample-size-gated. Forward fixture density per (league, week) tells us *when* each (book, league) cell will have enough fixtures for Brier CIs to separate — directly informing the timing of the paid-data wishlist purchase. Without the calendar this is guesswork.

### 6. Historical baseline for filter-coverage analysis

Today we can answer "of the bets we flagged, what was their CLV?" — but not "of the fixtures we *should* have scanned, how many produced any flag at all?" The calendar gives a clean denominator. Lets us measure filter false-negative rate (e.g. dispersion-filter rejects that, in hindsight, would have been profitable).

### 7. Notification context

"5 EPL fixtures this weekend, 0 flagged" reads differently from "0 EPL fixtures (international break)". The notification dedup logic could include the expected-fixture count to avoid silent confusion when a quiet weekend looks like a broken pipeline.

### 8. Cup / European competition windows surface automatically

FA Cup, DFB Pokal, UCL, UEL, Conference — none of these are scanned. The calendar makes their windows + book-coverage visible, which feeds the "should we add this competition?" decision with hard numbers instead of intuition.

### 9. Festive schedule + Boxing Day handling

EPL Boxing Day / winter schedule is abnormally dense and currently caught by chance. With the calendar, we know in advance and can pre-schedule extra scan slots without manual cron-edits.

### 10. Postponement / replay detection

If the calendar said "5 EPL fixtures Sat" and the API returns 4, that's a postponement signal worth surfacing — not just for our own sanity but because postponed-then-replayed fixtures are a known source of stale-line edge.

## Build-cost vs lifetime-leverage

The calendar itself is ~1–2 days of work (FDCO fixtures ingest + weekly refresh job + simple lookup API). Each downstream item above is a separate gated decision.

The calendar's value-per-dependent grows monotonically — once it exists, every new "we don't know what's scheduled" question is solved at zero marginal cost.

## What this doc is not

- A commitment to ship #1–#10. They're motivating use cases.
- A schema spec — that lives in the implementation PR if/when we proceed.
- A replacement for issue #7. Issue #7 is the minimum viable scope (#2 above); this doc is the wider benefits map.

## Status log

- 2026-05-02: doc created from conversation. No build started.
- 2026-05-03: initial build shipped (PR). Implemented: schema migration (source/status on fixtures),
  `scripts/ingest_fixtures.py` (FDCO fixtures.csv primary + api.football-data.org optional),
  `src/data/fixture_calendar.py` (has_fixtures / get_fixtures / canary_verdict),
  canary integration in scan_odds.py (issue #7 — silent skip when calendar confirms no fixtures;
  confirmed-outage alert when fixtures expected), Mon 02:00 UTC cron entry.
  Deferred to follow-up: cron tailoring (#1), closing-line proximity (#3).
- 2026-05-03: JSON-to-SQL migration shipped. `logs/fixture_calendar.json` dropped.
  `FixtureRepo` writes/reads `fixtures` table. `fixture_uuid` now keys on
  sport_key + UTC date + normalised team names (_norm_name). `ingested_at` column
  added for staleness detection. `src/data/fixture_calendar.py` now reads from DB
  (Pi-safe fallback: returns unknown/empty when DB unset).
