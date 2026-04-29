"""
Commission rates per bookmaker on the Odds API.
Type: 'winnings' = % of net winnings (exchanges); 'none' = baked into odds (sportsbooks).
"""

# Commission collection
# Source: each book's published commission policy, verified 2026-04-29.
# See docs/COMMISSIONS.md for citations.
BOOK_COMMISSIONS: dict[str, dict] = {
    # Exchanges (commission on net winnings)
    "betfair_ex_uk":   {"type": "winnings", "rate": 0.05, "label": "Betfair Exchange (UK MBR)"},
    "smarkets":        {"type": "winnings", "rate": 0.02, "label": "Smarkets"},
    "matchbook":       {"type": "winnings", "rate": 0.02, "label": "Matchbook"},

    # Sportsbooks (no commission; margin built into odds)
    "pinnacle":        {"type": "none",     "rate": 0.0,  "label": "Pinnacle (low-margin sportsbook)"},
    "betfair_sb_uk":   {"type": "none",     "rate": 0.0,  "label": "Betfair Sportsbook"},
    "betfred_uk":      {"type": "none",     "rate": 0.0,  "label": "Betfred"},
    "williamhill":     {"type": "none",     "rate": 0.0,  "label": "William Hill"},
    "coral":           {"type": "none",     "rate": 0.0,  "label": "Coral"},
    "ladbrokes_uk":    {"type": "none",     "rate": 0.0,  "label": "Ladbrokes"},
    "skybet":          {"type": "none",     "rate": 0.0,  "label": "Sky Bet"},
    "paddypower":      {"type": "none",     "rate": 0.0,  "label": "Paddy Power"},
    "boylesports":     {"type": "none",     "rate": 0.0,  "label": "BoyleSports"},
    "betvictor":       {"type": "none",     "rate": 0.0,  "label": "BetVictor"},
    "betway":          {"type": "none",     "rate": 0.0,  "label": "Betway"},
    "leovegas":        {"type": "none",     "rate": 0.0,  "label": "LeoVegas"},
    "casumo":          {"type": "none",     "rate": 0.0,  "label": "Casumo"},
    "virginbet":       {"type": "none",     "rate": 0.0,  "label": "Virgin Bet"},
    "livescorebet":    {"type": "none",     "rate": 0.0,  "label": "LiveScore Bet"},
    "sport888":        {"type": "none",     "rate": 0.0,  "label": "888Sport"},
    "grosvenor":       {"type": "none",     "rate": 0.0,  "label": "Grosvenor"},
}

# Default for any book not in the table (assume no commission)
DEFAULT_COMMISSION: dict = {"type": "none", "rate": 0.0, "label": "unknown"}


def commission_rate(book: str) -> float:
    """Commission as a fraction of net winnings. 0.0 for sportsbooks."""
    entry = BOOK_COMMISSIONS.get(book, DEFAULT_COMMISSION)
    return entry["rate"] if entry["type"] == "winnings" else 0.0


def effective_odds(odds: float, book: str) -> float:
    """Decimal odds after commission deducted from net winnings."""
    c = commission_rate(book)
    if c == 0.0:
        return odds
    return 1.0 + (odds - 1.0) * (1.0 - c)


def effective_implied_prob(odds: float, book: str) -> float:
    """1 / effective_odds — the implied prob you actually pay for."""
    return 1.0 / effective_odds(odds, book)
