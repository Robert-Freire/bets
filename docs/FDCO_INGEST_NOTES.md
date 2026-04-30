# Football-Data.co.uk Ingest Notes — Phase R.5.5b

**Status: complete**  
**Date:** 2026-04-30

---

## Summary

Added 16 new league codes from football-data.co.uk to `data/raw/`, increasing loader coverage from **27,149 matches / 6 leagues** to **91,492 matches / 22 leagues**.

New leagues: `B1, E2, E3, EC, F2, G1, I2, N1, P1, SC0, SC1, SC2, SC3, SP1, SP2, T1`

Files are committed to git (same precedent as the existing 72 league files). Download helper: `scripts/refresh_fdco_data.py`.

---

## Baseline (pre-R.5.5b)

```
Total: 27,149 matches across 6 divisions
Existing divisions: ['D1', 'D2', 'E0', 'E1', 'F1', 'I1']
D1: 3,645   D2: 3,645   E0: 4,519   E1: 6,612   F1: 4,208   I1: 4,520
```

---

## Leagues added

| League | Name | Files | Rows | Seasons |
|---|---|---|---|---|
| B1 | Belgian First Division A | 12 | 3,252 | 2014/15–2025/26 |
| E2 | English League One | 12 | 6,461 | 2014/15–2025/26 |
| E3 | English League Two | 12 | 6,501 | 2014/15–2025/26 |
| EC | English Conference (National) | 12 | 6,409 | 2014/15–2025/26 |
| F2 | French Ligue 2 | 12 | 4,292 | 2014/15–2025/26 |
| G1 | Greek Super League | 11 | 2,610 | 2015/16–2025/26 |
| I2 | Italian Serie B | 12 | 4,832 | 2014/15–2025/26 |
| N1 | Dutch Eredivisie | 12 | 3,571 | 2014/15–2025/26 |
| P1 | Portuguese Primeira Liga | 12 | 3,646 | 2014/15–2025/26 |
| SC0 | Scottish Premier League | 12 | 2,663 | 2014/15–2025/26 |
| SC1 | Scottish Championship | 12 | 2,069 | 2014/15–2025/26 |
| SC2 | Scottish League One | 12 | 2,044 | 2014/15–2025/26 |
| SC3 | Scottish League Two | 12 | 2,042 | 2014/15–2025/26 |
| SP1 | Spanish La Liga | 12 | 4,510 | 2014/15–2025/26 |
| SP2 | Spanish Segunda División | 12 | 5,489 | 2014/15–2025/26 |
| T1 | Turkish Süper Lig | 12 | 3,985 | 2014/15–2025/26 |
| **TOTAL** | | **191** | **64,376** | |

---

## Season-league combos dropped

| File | Reason |
|---|---|
| `G1_1415.csv` | 78.4% of rows have ≥3 bookmaker columns — below 80% threshold. 2014/15 Greek Super League had insufficient bookmaker coverage in this dataset. |

All other 191 files passed the ≥80% rows with ≥3 books check. Average ~6.5 bookmaker columns per file.

---

## Encoding observations

football-data.co.uk CSVs come in two encoding flavours:
- **UTF-8 with BOM** (`\xef\xbb\xbf`): recent seasons (varies by league, mostly 2024/25–2025/26 but some from 2021/22). If read with `latin1`, the BOM appears as `ï»¿Div` in the first column name — making the `Div` column invisible to the loader.
- **Latin-1**: older seasons with non-ASCII characters in team names (e.g. `ö` in German team names, `0xf6`).

**Loader fix**: `load_backtest_data()` now tries `utf-8-sig` first (strips BOM automatically), falling back to `latin1` on `UnicodeDecodeError`. This correctly handles both variants.

**Data quality fix**: One row in `G1_2526.csv` had `1XBH = '1xBet'` (a bookmaker name instead of a decimal odds value — a source data error). The loader now coerces all bookmaker columns to `float` with `errors='coerce'`, turning such values into NaN. `compute_consensus()` already skips NaN odds.

---

## After state (post-R.5.5b)

```
Total: 91,492 matches across 22 divisions
D1: 3,645   D2: 3,645   E0: 4,519   E1: 6,612   F1: 4,208   I1: 4,520  ← unchanged
```

Walk-forward 5-fold spot-check (`shin`, `min_edge=0.02`):

| Fold | Bets | ROI | Period |
|---|---|---|---|
| 0 | 5,074 | +14.3% | May 2016 – Apr 2018 |
| 1 | 1,796 | +7.5% | Apr 2018 – Sep 2020 |
| 2 | 2,141 | +19.0% | Sep 2020 – May 2022 |
| 3 | 1,318 | +2.7% | May 2022 – May 2024 |
| 4 | 5,162 | −4.6% | May 2024 – Apr 2026 |

*(These are raw spot-check numbers, not the definitive R.5.5c report.)*

---

## Why football-data.co.uk (not Zenodo)

The originally-targeted Zenodo 84k-match dataset (Hegarty & Whelan 2024, DOI 10.5281/zenodo.12673394) was inspected and rejected — it ships only **aggregated odds** (`maxhome`, `avghome`, ...), not per-bookmaker triplets, which our consensus strategy cannot use. Full rationale: `docs/ZENODO_INGEST_NOTES.md`.
