"""
Commission rates per bookmaker — loaded from config.json (books section).
Type: 'exchange' = % of net winnings; 'sportsbook' = commission baked into odds.
See docs/COMMISSIONS.md for citation details.
"""
from __future__ import annotations

from src.config import load_books

_BOOKS_BY_KEY: dict[str, dict] = {b["key"]: b for b in load_books()}

_DEFAULT: dict = {"type": "sportsbook", "license": "non-UK", "commission_rate": 0.0}


def commission_rate(book: str) -> float:
    """Commission as a fraction of net winnings. 0.0 for sportsbooks."""
    entry = _BOOKS_BY_KEY.get(book, _DEFAULT)
    return entry["commission_rate"] if entry["type"] == "exchange" else 0.0


def effective_odds(odds: float, book: str) -> float:
    """Decimal odds after commission deducted from net winnings."""
    c = commission_rate(book)
    if c == 0.0:
        return odds
    return 1.0 + (odds - 1.0) * (1.0 - c)


def effective_implied_prob(odds: float, book: str) -> float:
    """1 / effective_odds — the implied prob you actually pay for."""
    return 1.0 / effective_odds(odds, book)
