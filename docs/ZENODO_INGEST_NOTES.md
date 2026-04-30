# Zenodo Dataset Ingest Notes — Phase R.5.5b

**Status: BLOCKED**

**Dataset:** Hegarty & Whelan (2024), "Supplementary materials for 'Forecasting Soccer Matches With Betting Odds: A Tale of Two Markets'"  
**Zenodo DOI:** 10.5281/zenodo.12673394  
**File inspected:** `Raw to Tidy Data/raw_data1.0.csv` (131,433 rows, ~16 MB)

---

## Schema comparison

| Aspect | football-data.co.uk | Zenodo (raw_data1.0.csv) |
|---|---|---|
| Column naming | `Div`, `Date`, `HomeTeam`, `AwayTeam`, `FTR` | `Data.Div`, `Data.Date`, `Data.HomeTeam`, `Data.AwayTeam`, `Data.FTR` |
| **Bookmaker odds** | **Per-bookmaker triplets: `B365H/B365D/B365A`, `BWH/BWD/BWA`, … (up to 12 books)** | **Aggregated only: `maxhome`, `avghome`, `maxdraw`, `avgdraw`, `maxaway`, `avgaway`** |
| Date format | `DD/MM/YYYY` | `DD/MM/YYYY` |
| FTR encoding | H / D / A | H / D / A |
| File encoding | UTF-8 | CP1252 / latin-1 |

## Why the phase is BLOCKED

`backtest_consensus()` (and `compute_consensus()`) iterate over `BOOKMAKER_GROUPS` — a dict of 12 per-bookmaker column triplets — to build a consensus across multiple books and find where a single book's odds beat that consensus.

The Zenodo dataset only provides **market aggregates** (`maxhome`, `avghome`, etc.), not individual bookmaker columns. This creates a fundamental data-granularity mismatch:

- With only 2 pseudo-bookmakers (avg + max), `n_books_used` is always 2, below the default `min_books=3` threshold → 0 bets flagged per match.
- Lowering `min_books` to 2 or 1 and treating avg/max as pseudo-books would produce a synthetic "edge" (max always beats avg) that fires on every single match — this is not the Kaunitz strategy and would produce meaningless ROI numbers in R.5.5c.

Column renaming (`Data.Date → Date`, etc.) is trivial. The odds granularity mismatch is not fixable at the loader boundary without changing the semantics of the backtest.

## Divisions in the Zenodo dataset

Total: 131,433 rows across 22 divisions (seasons 2005–2021).

| Div | Matches | In our existing data? |
|---|---|---|
| B1 | 4,438 | No — **new** |
| D1 | 5,202 | Yes (3,645 in our data, different seasons) |
| D2 | 5,202 | Yes |
| E0 | 6,446 | Yes |
| E1 | 9,384 | Yes |
| E2 | 9,232 | No — **new** |
| E3 | 9,272 | No — **new** |
| EC | 9,026 | No — **new** |
| F1 | 6,349 | Yes |
| F2 | 6,360 | No — **new** |
| G1 | 4,275 | No — **new** |
| I1 | 6,450 | Yes |
| I2 | 7,488 | No — **new** |
| N1 | 5,128 | No — **new** |
| P1 | 4,674 | No — **new** |
| SC0 | 3,827 | No — **new** |
| SC1 | 2,972 | No — **new** |
| SC2 | 2,949 | No — **new** |
| SC3 | 2,947 | No — **new** |
| SP1 | 6,450 | No — **new** |
| SP2 | 7,832 | No — **new** |
| T1 | 5,346 | No — **new** |

New leagues that could be added (if an alternative data source is found): B1, E2, E3, EC, F2, G1, I2, N1, P1, SC0–SC3, SP1, SP2, T1 (16 new codes).

## Files dropped / not used

- `tidy_data1.2.dta` — Stata binary format, not CSV-readable without a Stata reader
- `Raw to Tidy Data/Data_tidy1.2.do` — Stata script only
- `data/raw/zenodo/` directory was created (gitignored) but not populated

## Alternative path

If per-bookmaker historical odds for these 16+ leagues are needed in future, consider:

1. **football-data.co.uk** — already our existing source; covers E2, E3, EC, SP1, SP2, SC0–SC3, N1, P1, B1, G1, T1, I2, F2 at `https://www.football-data.co.uk/data.php` with per-bookmaker columns. Same schema as our existing CSVs — zero mapping work.
2. **Betfair historical data** — more detailed but requires paid access.

The simplest unblock: download the missing leagues from football-data.co.uk directly into `data/raw/` (same structure we already have). No loader changes needed.

## Impact on R.5.5c

Per the plan, R.5.5c proceeds on the existing **27,149 matches / 6 leagues**. The PR body for R.5.5c must note the smaller dataset. Walk-forward CI intervals will be wider than they would have been with 50k+ matches, but the results remain defensible for graduation decisions.
