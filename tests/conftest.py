import json
from pathlib import Path
import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_event():
    with open(FIXTURES_DIR / "sample_event.json") as f:
        return json.load(f)


# ── Synthetic event builder ────────────────────────────────────────────────────

def synthetic_event(
    home="Arsenal",
    away="Chelsea",
    kickoff="2026-05-10T15:00:00Z",
    h2h_prices=None,
    totals_prices=None,
    btts_prices=None,
    sport_key="soccer_epl",
):
    """Build a synthetic Odds API event dict with controlled per-book prices.

    h2h_prices:    {book_key: (home_odds, draw_odds, away_odds)}
    totals_prices: {book_key: (point, over_odds, under_odds)}
    btts_prices:   {book_key: (yes_odds, no_odds)}
    """
    all_books = (
        set(h2h_prices or {}) | set(totals_prices or {}) | set(btts_prices or {})
    )
    bookmakers = []
    for book in sorted(all_books):
        markets = []
        if h2h_prices and book in h2h_prices:
            ho, dr, aw = h2h_prices[book]
            markets.append({
                "key": "h2h",
                "outcomes": [
                    {"name": home, "price": ho},
                    {"name": "Draw", "price": dr},
                    {"name": away, "price": aw},
                ],
            })
        if totals_prices and book in totals_prices:
            pt, ov, un = totals_prices[book]
            markets.append({
                "key": "totals",
                "outcomes": [
                    {"name": "Over", "point": pt, "price": ov},
                    {"name": "Under", "point": pt, "price": un},
                ],
            })
        if btts_prices and book in btts_prices:
            yes, no = btts_prices[book]
            markets.append({
                "key": "btts",
                "outcomes": [
                    {"name": "Yes", "price": yes},
                    {"name": "No", "price": no},
                ],
            })
        if markets:
            bookmakers.append({"key": book, "title": book, "markets": markets})

    return {
        "id": "synthetic",
        "sport_key": sport_key,
        "sport_title": "EPL",
        "commence_time": kickoff,
        "home_team": home,
        "away_team": away,
        "bookmakers": bookmakers,
    }
