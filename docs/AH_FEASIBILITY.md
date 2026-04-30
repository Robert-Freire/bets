# Asian Handicap Feasibility — The Odds API

**Date:** 2026-04-30  
**Phase:** R.9  
**Verdict:** Partially feasible — Pinnacle AH usable as a second anchor signal; betting market use requires paid tier upgrade.

---

## 1. Source found

**Market key:** `spreads` (The Odds API v4)  
**Endpoint:** `GET /v4/sports/{sport_key}/odds?markets=spreads&regions=...`  
**Status:** Returns 200 for `soccer_epl`. Verified live 2026-04-30.

**`alternate_spreads`:** Returns 422 (Unprocessable Entity) — not available on free tier.

### Books by region (EPL, 2026-04-30)

| Region | Books offering AH |
|--------|------------------|
| `uk`   | `matchbook` (1 book only) |
| `eu`   | `onexbet`, `mybookieag`, `pinnacle`, `betonlineag`, `matchbook`, `gtbets`, `coolbet` (7 books) |

Only **Matchbook** is in our `UK_LICENSED_BOOKS` set. The other 6 EU books are offshore (1xBet/Russia, MyBookie.ag/Curaçao, BetOnline.ag/Panama, GTbets/Curaçao, Coolbet/Estonia). None are UK Gambling Commission licensed.

### Sample response

See `docs/papers/sample_ah_response.json` (Leeds United vs Burnley, 2026-05-01).

Key structural features:
- Market key: `"spreads"`
- Each bookmaker's `outcomes` has two entries: `{ "name": team, "price": decimal_odds, "point": handicap_line }`
- **Pinnacle uses quarter-point lines** (e.g. ±1.25) — the most efficient AH format (eliminates draw as third outcome, price=2.05/1.86 vs vig-inflated integer lines)
- **Other books use half-ball lines** (e.g. ±1.5, price ~1.7–2.18) — different lines across books; cannot directly consensus-average without line normalisation

---

## 2. Cost — API tier and request budget

| | Now | With AH (eu only, 6 soccer sports) |
|---|---|---|
| Monthly calls used | ~474 / 500 | ~534–594 / 500 |
| Free tier OK? | Yes (barely) | **No — over limit** |
| Required upgrade | — | Starter plan: $79/month (10k calls) |

Breakdown: 6 soccer sports × ~15–20 scan cycles/month × 1 call/cycle (eu region only) ≈ +90–120 calls/month. This pushes total above 500.

Even limiting to **EPL only** costs +15–20 calls/month — still marginal on the current budget given the closing-line script's variable usage.

**API reset:** Resets on the 1st of each month. As of 2026-04-30: 459 used, 41 remaining.

---

## 3. Implementation sketch

### 3a. Where to add `fetch_ah_odds()` in `scan_odds.py`

The function would be a sibling to the existing `h2h+totals` call block (lines ~249–290):

```python
def fetch_ah_odds(sport_key: str) -> dict[str, dict]:
    """Returns {fixture_id: {home: str, away: str, pinnacle_ah: {line, home_price, away_price}}}"""
    params = {
        "apiKey": API_KEY,
        "regions": "eu",           # uk only has matchbook; eu has pinnacle
        "markets": "spreads",
        "oddsFormat": "decimal",
        "bookmakers": "pinnacle",  # anchor-only; saves API credits vs full eu fetch
    }
    # ... fetch + parse
```

Using `bookmakers=pinnacle` filter (if supported) would reduce response size and cost. The result feeds into the consensus module as a second anchor, not as a betting market.

### 3b. Probability conversion — Hegarty & Whelan (2023)

The paper's closed-form formulas (Eqs 6–28) convert an AH line + decimal price pair into a win probability:

**Quarter-ball line (e.g. −1.25):** The handicap splits into two equal half-size bets at the adjacent whole/half lines (−1.0 and −1.5). The published price is a blend. Back-solving for the implied probability requires iterative inversion or the closed-form expression in Eq 17 of the paper.

**New module:** `src/betting/asian_handicap.py`

```python
def ah_to_win_prob(line: float, price: float, side: str) -> float:
    """Convert AH line + decimal odds to win probability (Hegarty & Whelan Eqs 6–28).
    side: 'home' or 'away'
    """
    ...
```

This would replace or augment the current Pinnacle h2h devigged probability as the anchor for consensus computation in `src/betting/consensus.py`.

---

## 4. Estimated effort — `O_asian_handicap_anchor` variant

| Task | Effort |
|------|--------|
| Implement `ah_to_win_prob()` (Eqs 6–28, 4 line types: integer, half, quarter) | ~3h |
| Add `fetch_ah_odds()` to `scan_odds.py` | ~1h |
| Wire AH-derived prob as second anchor in `consensus.py` (average h2h Pinnacle + AH Pinnacle when both available) | ~2h |
| Add shadow variant `Q_ah_anchor` to `strategies.py` (reuses existing framework) | ~1h |
| Tests | ~2h |
| **Total** | **~9h** |

Upgrade to paid API tier required before this ships.

---

## 5. Key constraints

1. **UK books don't offer AH via this API.** Only Matchbook, and it's too thin (1 book) for a consensus target. AH is useful only as an anchor signal, not as a betting market.
2. **Different lines across books.** Pinnacle (±1.25) and offshore books (±1.5) offer different lines for the same fixture. You cannot directly average implied probs without normalising to a common line — this adds complexity to the conversion math.
3. **Pinnacle-only anchor is simpler and likely sufficient.** Since Pinnacle is the sharpest book, using its AH line as a second anchor (alongside h2h) is the main value here. A full multi-book AH consensus isn't needed.
4. **API tier blocker.** Can't add this on the free tier without dropping other sports. Upgrade decision required before implementation.

---

## 6. Recommendation

**Defer R.10 — block on CLV confirmation, not just API budget.** Pinnacle's AH line is the most efficient market-implied probability we can get — more informationally dense than h2h because quarter-ball lines eliminate the draw. But it is a *refinement* of an anchor (h2h Pinnacle Shin-devig) we already have, and the binding constraint on this system is operational cost, not anchor quality (`RESEARCH_NOTES_2026-04.md` §6).

**Explicit gate for revisiting R.10:**
1. R.6 graduations have landed (≥1 shadow variant flipped to default with walk-forward evidence), AND
2. Avg CLV across graduated variants is positive over ≥50 settled bets, AND
3. Either (a) ≥1 league surfaces in R.5.5c with h2h Pinnacle book count consistently <5 (the AH-as-anchor use case Hegarty & Whelan cite), OR (b) `J_sharp_weighted` shadow shows materially better CLV than `A_production` (evidence that "use the sharpest signal" pays off, making the AH upgrade more likely to as well).

If all three hold: $79/month Starter tier + ~9h implementation is justified. If CLV is flat/negative or R.6 produces no graduations, AH won't rescue it — every hour and dollar belongs on restriction-resilience (RESEARCH_NOTES §3.1, 3.3, 3.4) instead.

**Do not** upgrade the API tier speculatively. The budget math only fails because we'd be paying $948/year to refine a signal whose base utility isn't yet measured.
