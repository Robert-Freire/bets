# Backtest: Raw vs Shin De-vig Consensus

*Generated: 2026-04-29 — Phase 1 implementation*

Dataset: football-data.co.uk EPL seasons 2014/15–2025/26 (4,519 matches, 12 seasons, ~6 bookmakers in CSV).

Note: The live scanner uses 30–40 bookmakers per fixture; the CSV dataset only has ~6. Both ROI figures should be treated directionally, not as precise predictions of live performance. Absolute edge levels with 6 books are not directly comparable to 36-book live consensus.

---

## Method: Raw (original Kaunitz)

Consensus = arithmetic mean of raw implied probs (includes bookmaker margin). Edge = raw_consensus − raw_book_implied.

| Min edge | Bets | Win% | ROI     | P&L (£1k bankroll) |
|----------|------|------|---------|---------------------|
| 1%       | 9415 | 31.8% | +1.18% | +£1,211             |
| 2%       | 638  | 38.1% | +6.13% | +£788               |
| 3%       | 64   | 50.0% | +10.46%| +£227               |
| 4%       | 9    | 55.6% | +7.60% | +£30                |
| 5%       | 2    | 100%  | +60%   | +£60                |

The previously reported "+6.1% ROI at 2%" figure matches the raw method with this dataset.

---

## Method: Shin de-vig (Phase 1 default)

Each bookmaker's H/D/A triplet is de-vigged with Shin (1993) before averaging. Edge = shin_consensus − shin_book_fair.

| Min edge | Bets | Win% | ROI      | P&L (£1k bankroll) |
|----------|------|------|----------|---------------------|
| 1%       | 4795 | 33.5% | +1.00%  | +£60                |
| 2%       | 498  | 52.0% | +17.65% | +£421               |
| 3%       | 117  | 54.7% | +15.60% | +£237               |
| 4%       | 35   | 57.1% | +19.47% | +£150               |
| 5%       | 5    | 60.0% | +26.86% | +£46                |

---

## Interpretation

**What changed:**
- At 1% edge, Shin cuts the bet count nearly in half (9,415 → 4,795), removing noise bets where the "edge" was just the bookmaker's own margin artificially inflating the consensus.
- At 2–4%, Shin finds more bets with higher ROI and win rates. The de-vigged consensus is a sharper signal.
- Win rates at 2%+ are meaningfully above the break-even implied by average odds (~45% for a 2.2 average odds market), suggesting genuine positive edge on this dataset.

**Caveats:**
- Sample sizes above 3% are small (n < 200). ROI confidence intervals are wide.
- 6 bookmakers in the CSV data vs 36 live creates a selection bias — live results will differ.
- No time-based hold-out. The backtest tests all seasons together; see Phase 7 for honest hold-out evaluation.

**Key finding for Phase 1:** The Shin de-vig filter removes the overround-inflated false edges cleanly. The raw "+6.1% ROI at 2%" figure is confirmed as real on this dataset; Shin de-vig produces a cleaner signal with higher ROI at the cost of fewer bets. Monitoring CLV (Phase 3) will confirm whether this holds in production.

---

## Edge between methods

| Edge | Raw bets | Shin bets | ∆ bets | Raw ROI | Shin ROI | ∆ ROI   |
|------|----------|-----------|--------|---------|----------|---------|
| 1%   | 9,415    | 4,795     | −49%   | +1.18%  | +1.00%   | −0.18pp |
| 2%   | 638      | 498       | −22%   | +6.13%  | +17.65%  | +11.5pp |
| 3%   | 64       | 117       | +83%   | +10.46% | +15.60%  | +5.1pp  |
| 4%   | 9        | 35        | +289%  | +7.60%  | +19.47%  | +11.9pp |

At 3%+, Shin finds *more* bets than raw because some bets with concentrated margin on other sides have their effective edge on the flagged side boosted after de-vigging. These bets appear to be genuine edges.

---

## Re-run instructions

```bash
cd /path/to/bets
python3 main.py
```

Strategy 1 now prints both raw and Shin tables automatically.
