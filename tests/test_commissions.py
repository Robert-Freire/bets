"""Tests for src/betting/commissions.py"""
import pytest
from src.betting.commissions import (
    commission_rate,
    effective_odds,
    effective_implied_prob,
)
from src.betting.risk import compute_raw_stake


def test_sportsbook_effective_odds_unchanged():
    assert effective_odds(2.5, "williamhill") == 2.5


def test_betfair_5pct_winnings():
    # 2.5 → 1 + 1.5 * 0.95 = 2.425
    assert effective_odds(2.5, "betfair_ex_uk") == pytest.approx(2.425)


def test_smarkets_2pct():
    # 2.5 → 1 + 1.5 * 0.98 = 2.47
    assert effective_odds(2.5, "smarkets") == pytest.approx(2.47)


def test_unknown_book_defaults_to_zero():
    assert effective_odds(3.0, "unknown_bookmaker_xyz") == 3.0
    assert commission_rate("unknown_bookmaker_xyz") == 0.0


def test_kelly_uses_effective_odds():
    """Same gross edge → smaller stake on Betfair than Smarkets due to higher commission."""
    cons = 0.55
    odds = 2.0
    stake_betfair  = compute_raw_stake(cons, odds, 1000.0, "betfair_ex_uk")
    stake_smarkets = compute_raw_stake(cons, odds, 1000.0, "smarkets")
    stake_sport    = compute_raw_stake(cons, odds, 1000.0, "williamhill")

    # Higher commission → lower effective odds → smaller Kelly stake
    assert stake_betfair < stake_smarkets < stake_sport
