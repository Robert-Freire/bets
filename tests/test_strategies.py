"""
Tests for src/betting/strategies.py — verifying each of the 8 strategy variants
behaves according to its specification.

Reference: docs/PLAN.md §4.5.4 and §5.6.1
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.betting.strategies import (
    STRATEGIES,
    StrategyConfig,
    evaluate_strategy,
    EXCHANGE_BOOKS,
    UK_LICENSED_BOOKS,
)
from tests.conftest import synthetic_event


# ── helpers ────────────────────────────────────────────────────────────────────

def _strategy(name: str) -> StrategyConfig:
    return next(s for s in STRATEGIES if s.name == name)


def _prices(books, home_odds, draw_odds, away_odds):
    return {b: (home_odds, draw_odds, away_odds) for b in books}


def _load_scan_odds(monkeypatch):
    """Load scripts/scan_odds.py with a dummy API key (avoids RuntimeError on import)."""
    monkeypatch.setenv("ODDS_API_KEY", "dummy")
    spec = importlib.util.spec_from_file_location(
        "_scan_odds_test", ROOT / "scripts" / "scan_odds.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Test 10: count and uniqueness (run first for fast feedback) ────────────────

def test_strategy_count_is_8():
    assert len(STRATEGIES) == 8
    names = [s.name for s in STRATEGIES]
    assert len(names) == len(set(names)), "Strategy names must be unique"


# ── Test 1: A mirrors production h2h count ────────────────────────────────────

def test_variant_A_matches_production_h2h_count(sample_event, monkeypatch):
    scan_odds = _load_scan_odds(monkeypatch)

    events = [sample_event]
    prod_h2h = [
        b for b in scan_odds.find_value_bets(events, "soccer_epl")
        if b.get("market", "h2h") == "h2h"
    ]
    a_h2h = [
        b for b in evaluate_strategy(events, "soccer_epl", _strategy("A_production"))
        if b["market"] == "h2h"
    ]
    assert abs(len(a_h2h) - len(prod_h2h)) <= 1, (
        f"A_production h2h count {len(a_h2h)} differs from production {len(prod_h2h)} by >1"
    )


# ── Test 2: C_loose finds >= A bets ───────────────────────────────────────────

def test_variant_C_loose_finds_more_bets_than_A(sample_event):
    events = [sample_event]
    a_bets = evaluate_strategy(events, "soccer_epl", _strategy("A_production"))
    c_bets = evaluate_strategy(events, "soccer_epl", _strategy("C_loose"))
    assert len(c_bets) >= len(a_bets), (
        f"C_loose ({len(c_bets)}) should find >= A_production ({len(a_bets)}) bets"
    )


# ── Test 3: E_exchanges_only never flags a non-exchange book ──────────────────

def test_variant_E_exchanges_only_no_williamhill(sample_event):
    bets = evaluate_strategy(
        [sample_event], "soccer_epl", _strategy("E_exchanges_only")
    )
    for bet in bets:
        assert bet["book"] in EXCHANGE_BOOKS, (
            f"E_exchanges_only flagged non-exchange book: {bet['book']}"
        )


# ── Test 4: D_pinnacle_only uses Pinnacle's de-vigged prob, not market mean ───

def test_variant_D_pinnacle_only_uses_pinnacle_devig():
    # 18 balanced UK books + Pinnacle strongly favouring HOME + betfair at generous HOME odds.
    # betfair HOME edge vs Pinnacle-only consensus >> edge vs market mean.
    base_books = [b for b in sorted(UK_LICENSED_BOOKS) if b != "betfair_ex_uk"][:18]
    h2h = _prices(base_books, 3.0, 3.3, 2.6)       # balanced
    h2h["pinnacle"] = (1.85, 3.8, 5.0)               # Pinnacle: HOME very likely
    h2h["betfair_ex_uk"] = (3.8, 3.0, 2.4)           # betfair: generous HOME odds

    events = [synthetic_event(h2h_prices=h2h)]

    a_bets = evaluate_strategy(events, "soccer_epl", _strategy("A_production"))
    d_bets = evaluate_strategy(events, "soccer_epl", _strategy("D_pinnacle_only"))

    betfair_a = next(
        (b for b in a_bets if b["book"] == "betfair_ex_uk" and b["side"] == "H"), None
    )
    betfair_d = next(
        (b for b in d_bets if b["book"] == "betfair_ex_uk" and b["side"] == "H"), None
    )

    assert betfair_d is not None, "D_pinnacle_only should flag betfair_ex_uk HOME"
    assert betfair_a is not None, "A_production should also flag betfair_ex_uk HOME"
    # D's consensus is Pinnacle-only (higher HOME prob); A's is the market mean (lower)
    assert betfair_d["cons"] > betfair_a["cons"] + 0.05, (
        f"D cons {betfair_d['cons']:.3f} should exceed A cons {betfair_a['cons']:.3f} by >0.05 "
        "(D uses Pinnacle-only; Pinnacle strongly favours HOME)"
    )


# ── Test 5: F_model_primary only flags h2h bets ───────────────────────────────

def test_variant_F_model_primary_skips_totals_btts(sample_event):
    bets = evaluate_strategy(
        [sample_event], "soccer_epl", _strategy("F_model_primary")
    )
    for bet in bets:
        assert bet["market"] == "h2h", (
            f"F_model_primary flagged non-h2h market: {bet['market']}"
        )


# ── Test 6: F flags nothing without model signals ─────────────────────────────

def test_variant_F_requires_positive_model_edge(sample_event):
    bets = evaluate_strategy(
        [sample_event], "soccer_epl", _strategy("F_model_primary"),
        model_signals={},
    )
    assert bets == [], (
        f"F_model_primary should flag nothing without model signals; got {len(bets)} bet(s)"
    )


# ── Test 7: H_no_pinnacle excludes Pinnacle from consensus ───────────────────

def test_variant_H_excludes_pinnacle_from_consensus():
    # Pinnacle strongly favours HOME; including it raises the HOME consensus (variant A).
    # Excluding Pinnacle (variant H) gives a lower HOME consensus.
    base_books = [b for b in sorted(UK_LICENSED_BOOKS)][:19]
    h2h = _prices(base_books, 2.9, 3.3, 2.6)
    h2h["pinnacle"] = (1.50, 4.5, 8.0)    # Pinnacle: HOME extremely likely
    h2h["betfair_ex_uk"] = (3.8, 3.0, 2.4)  # flaggable HOME bet

    events = [synthetic_event(h2h_prices=h2h)]
    a_bets = evaluate_strategy(events, "soccer_epl", _strategy("A_production"))
    h_bets = evaluate_strategy(events, "soccer_epl", _strategy("H_no_pinnacle"))

    a_home = [b for b in a_bets if b["side"] == "H"]
    h_home = [b for b in h_bets if b["side"] == "H"]

    assert a_home, "A_production should flag at least one HOME bet"
    assert h_home, "H_no_pinnacle should flag at least one HOME bet"

    # A includes the very-HOME-biased Pinnacle → higher HOME consensus
    assert a_home[0]["cons"] > h_home[0]["cons"], (
        f"A cons {a_home[0]['cons']:.3f} should > H cons {h_home[0]['cons']:.3f} "
        "when Pinnacle strongly favours HOME"
    )


# ── Test 8: Dispersion filter blocks high-dispersion market ──────────────────

def test_dispersion_filter_blocks_high_dispersion():
    # 10 books strongly favour HOME; 10 books strongly favour AWAY.
    # Cross-book stdev of HOME probs >> 0.04 → B_strict flags nothing; A_production flags ≥1.
    group_a = [b for b in sorted(UK_LICENSED_BOOKS) if b != "pinnacle"][:10]
    group_b = [b for b in sorted(UK_LICENSED_BOOKS) if b != "pinnacle"][10:20]
    h2h = {}
    for b in group_a:
        h2h[b] = (1.80, 3.5, 4.5)   # strongly HOME
    for b in group_b:
        h2h[b] = (5.50, 3.5, 1.70)  # strongly AWAY
    h2h["pinnacle"] = (3.00, 3.5, 2.60)       # midpoint
    h2h["betfair_ex_uk"] = (2.90, 3.5, 2.60)  # extra book for count

    events = [synthetic_event(h2h_prices=h2h)]
    a_bets = evaluate_strategy(events, "soccer_epl", _strategy("A_production"))
    b_bets = evaluate_strategy(events, "soccer_epl", _strategy("B_strict"))

    assert len(a_bets) >= 1, (
        "A_production should flag at least 1 bet on a split-opinion market"
    )
    assert len(b_bets) == 0, (
        f"B_strict should flag 0 bets (dispersion >> 0.04); got {len(b_bets)}"
    )


# ── Test 9: Outlier-book filter blocks the rogue book ────────────────────────

def test_outlier_book_filter_blocks_outlier():
    # 20 books agree on HOME ~2.5; one rogue UK book (betfair_ex_uk) quotes HOME at 10.0.
    # betfair_ex_uk HOME has a massive edge but |z| >> 2.5 → blocked by outlier filter.
    normal_books = [b for b in sorted(UK_LICENSED_BOOKS) if b != "betfair_ex_uk"][:20]
    h2h = _prices(normal_books, 2.5, 3.3, 2.9)
    h2h["pinnacle"] = (2.0, 3.5, 4.5)            # Pinnacle: HOME more likely (raises cons)
    h2h["betfair_ex_uk"] = (10.0, 3.3, 1.35)     # rogue: HOME massively overpriced

    events = [synthetic_event(h2h_prices=h2h)]

    # Without outlier filter: betfair_ex_uk HOME has huge edge and should be flagged
    no_filter = StrategyConfig(
        name="_test_no_filter",
        label="",
        description="",
        drop_outlier_book=False,
        min_edge=0.03,
    )
    bets_no_filter = evaluate_strategy(events, "soccer_epl", no_filter)
    assert any(b["book"] == "betfair_ex_uk" and b["side"] == "H" for b in bets_no_filter), (
        "Without outlier filter, betfair_ex_uk HOME should be flagged (huge edge)"
    )

    # With outlier filter: betfair_ex_uk HOME must be blocked (|z| >> 2.5)
    with_filter = StrategyConfig(
        name="_test_with_filter",
        label="",
        description="",
        drop_outlier_book=True,
        min_edge=0.03,
    )
    bets_with_filter = evaluate_strategy(events, "soccer_epl", with_filter)
    rogue_bets = [
        b for b in bets_with_filter
        if b["book"] == "betfair_ex_uk" and b["side"] == "H"
    ]
    assert rogue_bets == [], (
        f"Outlier filter should block betfair_ex_uk HOME (|z|>>2.5); got {rogue_bets}"
    )


def test_impl_raw_equals_inverse_odds():
    """impl_raw must always be 1/odds; impl_effective may differ for exchange books."""
    from tests.conftest import synthetic_event

    books = {b: (2.1, 3.4, 3.8) for b in list(UK_LICENSED_BOOKS)[:25]}
    ev = synthetic_event(h2h_prices=books)
    strategy = StrategyConfig(
        name="_test_impl",
        label="",
        description="",
        min_edge=0.0,
    )
    bets = evaluate_strategy([ev], "soccer_epl", strategy)
    for b in bets:
        expected_raw = round(1.0 / b["odds"], 4)
        assert b["impl_raw"] == expected_raw, (
            f"impl_raw mismatch for {b['book']}: expected {expected_raw}, got {b['impl_raw']}"
        )
        # impl_effective <= impl_raw is only guaranteed for commission > 0 books;
        # for zero-commission books they're equal
        if b.get("commission_rate", 0.0) > 0:
            assert b["impl_effective"] >= b["impl_raw"], (
                f"Commission should raise effective implied prob for {b['book']}"
            )


def test_edge_filter_uses_gross_edge():
    """Every flagged bet must have edge_gross >= strategy.min_edge.

    In production, the edge filter is: cons - Shin-devigged fair >= min_edge (gross).
    strategies.py must use the same gross edge for the flag decision, not the net edge
    (which uses 1/odds instead of Shin-devigged fair and would be spuriously higher).
    """
    from tests.conftest import synthetic_event

    # Build an event with a rogue book at a clearly over-generous price on HOME
    books = {b: (2.10, 3.30, 3.90) for b in list(UK_LICENSED_BOOKS)[:25]}
    # williamhill has extreme HOME odds → huge z-score; test without outlier filter
    books["williamhill"] = (9.9, 3.30, 3.90)
    ev = synthetic_event(h2h_prices=books)

    no_filter = StrategyConfig(
        name="_test_gross_edge",
        label="", description="",
        min_edge=0.03, drop_outlier_book=False,
    )
    bets = evaluate_strategy([ev], "soccer_epl", no_filter)

    # If any bets are flagged, each must satisfy edge_gross >= min_edge
    for b in bets:
        assert b["edge_gross"] >= no_filter.min_edge, (
            f"{b['book']} {b['side']}: edge_gross {b['edge_gross']:.4f} < min_edge {no_filter.min_edge}"
        )
