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

def test_strategy_count_and_uniqueness():
    # 8 original + I, L, M, N (R.1) + O (R.1.5) + P (R.1.6) + J (R.2) + K (R.8) = 16
    assert len(STRATEGIES) == 16, f"Expected 16 strategies, got {len(STRATEGIES)}"
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


# ── R.1 tests ─────────────────────────────────────────────────────────────────

def test_variant_I_power_devig_bet_count_similar_to_G(sample_event):
    events = [sample_event]
    i_bets = evaluate_strategy(events, "soccer_epl", _strategy("I_power_devig"))
    g_bets = evaluate_strategy(events, "soccer_epl", _strategy("G_proportional"))
    tolerance = max(3, len(g_bets))
    assert abs(len(i_bets) - len(g_bets)) <= tolerance, (
        f"I_power_devig ({len(i_bets)}) vs G_proportional ({len(g_bets)}) differ by >{tolerance}"
    )


def test_variant_L_quarter_kelly_same_count_and_fraction(sample_event):
    from src.betting.risk import compute_raw_stake

    events = [sample_event]
    a_bets = evaluate_strategy(events, "soccer_epl", _strategy("A_production"))
    l_bets = evaluate_strategy(events, "soccer_epl", _strategy("L_quarter_kelly"))
    assert len(l_bets) == len(a_bets), (
        f"L_quarter_kelly ({len(l_bets)}) must have same bet count as A_production ({len(a_bets)})"
    )
    for bet in l_bets:
        assert bet["kelly_fraction"] == 0.4, (
            f"L_quarter_kelly bet kelly_fraction={bet['kelly_fraction']}, expected 0.4"
        )

    # Verify computed stake is exactly 0.8× at a controlled sub-cap edge.
    # cons=0.32, odds=3.6 → uncapped kelly_a=2.9% of bankroll (< 5% cap).
    bankroll = 1000.0
    a_stake = compute_raw_stake(0.32, 3.6, bankroll, "", 0.5)
    l_stake = compute_raw_stake(0.32, 3.6, bankroll, "", 0.4)
    assert a_stake < 0.05 * bankroll, "Test fixture must be sub-cap for a meaningful ratio check"
    assert abs(l_stake / a_stake - 0.8) < 1e-9, (
        f"L stake ({l_stake:.4f}) should be exactly 0.8× A stake ({a_stake:.4f})"
    )


def test_variant_M_rejects_longshot_bets():
    # AWAY is a heavy underdog (cons < 0.15). A flags it; M rejects it.
    books = {b: (1.50, 3.50, 7.50) for b in list(UK_LICENSED_BOOKS)[:20]}
    books["pinnacle"] = (1.48, 3.55, 7.80)
    books["betfair_ex_uk"] = (1.50, 3.50, 15.0)  # generous AWAY odds
    ev = synthetic_event(h2h_prices=books)

    a_bets = evaluate_strategy([ev], "soccer_epl", _strategy("A_production"))
    m_bets = evaluate_strategy([ev], "soccer_epl", _strategy("M_min_prob_15"))

    away_a = [b for b in a_bets if b["side"] == "A"]
    assert away_a, "A_production should flag AWAY (generous betfair odds, longshot fixture)"
    assert away_a[0]["cons"] < 0.15, (
        f"Fixture setup: AWAY cons {away_a[0]['cons']:.3f} must be < 0.15"
    )
    away_m = [b for b in m_bets if b["side"] == "A"]
    assert away_m == [], (
        f"M_min_prob_15 must reject AWAY bets with cons < 0.15; got {len(away_m)}"
    )


def test_variant_N_competitive_only_rejects_heavy_favourite():
    # HOME is a heavy favourite (cons >> 0.70). A flags it; N rejects it.
    n_uk = [b for b in sorted(UK_LICENSED_BOOKS) if b != "betfair_ex_uk"][:18]
    books = {b: (1.25, 4.0, 8.0) for b in n_uk}
    books["pinnacle"] = (1.23, 4.2, 8.5)
    books["betfair_ex_uk"] = (1.70, 4.0, 8.0)  # generous HOME odds, but cons >> 0.70
    ev = synthetic_event(h2h_prices=books)

    a_bets = evaluate_strategy([ev], "soccer_epl", _strategy("A_production"))
    n_bets = evaluate_strategy([ev], "soccer_epl", _strategy("N_competitive_only"))

    home_a = [b for b in a_bets if b["side"] == "H"]
    assert home_a, "A_production should flag HOME"
    assert home_a[0]["cons"] > 0.70, (
        f"Fixture setup: cons[H]={home_a[0]['cons']:.3f} must be > 0.70"
    )
    home_n = [b for b in n_bets if b["side"] == "H"]
    assert home_n == [], "N_competitive_only must reject HOME bets with cons > 0.70"


# ── R.1.5 tests ───────────────────────────────────────────────────────────────

def test_variant_O_kaunitz_classic_flags_when_condition_met():
    # 4 UK books at HOME=2.0 + betfair at HOME=2.30
    # cons[H] (raw) = (4 * 0.500 + 0.435) / 5 ≈ 0.487
    # (0.487 - 0.05) * 2.30 ≈ 1.005 > 1.0 → should flag
    uk4 = [b for b in sorted(UK_LICENSED_BOOKS) if b != "betfair_ex_uk"][:4]
    books = {b: (2.0, 3.0, 4.5) for b in uk4}
    books["betfair_ex_uk"] = (2.30, 3.0, 4.5)
    ev = synthetic_event(h2h_prices=books)

    o_bets = evaluate_strategy([ev], "soccer_epl", _strategy("O_kaunitz_classic"))
    home_bets = [b for b in o_bets if b["side"] == "H"]
    assert home_bets, "O_kaunitz_classic should flag HOME when (cons-alpha)*max_odds > 1.0"
    assert home_bets[0]["book"] == "betfair_ex_uk", (
        "O should flag at the max-odds book (betfair_ex_uk)"
    )


def test_variant_O_kaunitz_classic_skips_when_condition_not_met():
    # 4 UK books at HOME=2.0 + betfair at HOME=2.08
    # cons[H] (raw) ≈ (4*0.500 + 0.481)/5 ≈ 0.496
    # (0.496 - 0.05) * 2.08 ≈ 0.928 < 1.0 → should NOT flag
    uk4 = [b for b in sorted(UK_LICENSED_BOOKS) if b != "betfair_ex_uk"][:4]
    books = {b: (2.0, 3.0, 4.5) for b in uk4}
    books["betfair_ex_uk"] = (2.08, 3.0, 4.5)
    ev = synthetic_event(h2h_prices=books)

    o_bets = evaluate_strategy([ev], "soccer_epl", _strategy("O_kaunitz_classic"))
    home_bets = [b for b in o_bets if b["side"] == "H"]
    assert home_bets == [], (
        f"O_kaunitz_classic must not flag HOME when (cons-alpha)*max_odds <= 1.0; got {len(home_bets)}"
    )


def test_variant_O_kaunitz_classic_config():
    o = _strategy("O_kaunitz_classic")
    assert o.raw_consensus is True
    assert o.kaunitz_alpha == 0.05
    assert o.max_odds_shopping is True
    assert o.min_books == 4
    assert o.markets == ("h2h",)


# ── R.2 tests ─────────────────────────────────────────────────────────────────

def test_variant_J_sharp_weighted_wired_to_sharpness_weights():
    from src.betting.consensus import SHARPNESS_WEIGHTS
    j = _strategy("J_sharp_weighted")
    assert j.sharpness_weights is not None
    assert j.sharpness_weights == SHARPNESS_WEIGHTS


def test_sharpness_weights_unknown_book_defaults_to_1_0():
    from src.betting.consensus import SHARPNESS_WEIGHTS
    assert SHARPNESS_WEIGHTS.get("unknown_book_xyz", 1.0) == 1.0


def test_variant_J_sharpness_weights_none_matches_A_production(sample_event):
    # When sharpness_weights=None the weighted mean reduces to uniform → identical to A.
    events = [sample_event]
    a_bets = evaluate_strategy(events, "soccer_epl", _strategy("A_production"))
    j_off = StrategyConfig(name="J_off", label="", description="", sharpness_weights=None)
    j_off_bets = evaluate_strategy(events, "soccer_epl", j_off)
    assert len(j_off_bets) == len(a_bets), (
        f"sharpness_weights=None should match A_production bet count: "
        f"{len(j_off_bets)} vs {len(a_bets)}"
    )


def test_variant_J_sharp_weights_shift_consensus_toward_sharp_books():
    # 15 neutral UK books + 2 sharp UK books (betfair_ex_uk, smarkets) favouring HOME
    # + 2 soft UK books (betfred_uk, coral) favouring AWAY + pinnacle neutral = 20 books.
    # Under J, sharps (weight 1.5) outweigh softs (weight 0.7) → HOME cons rises vs A.
    from src.betting.consensus import SHARPNESS_WEIGHTS

    neutral = [b for b in sorted(UK_LICENSED_BOOKS)
               if b not in {"betfair_ex_uk", "smarkets", "betfred_uk", "coral"}]  # 15 books
    books = {b: (2.5, 3.3, 2.9) for b in neutral}
    books["betfair_ex_uk"] = (1.80, 3.5, 5.0)   # sharp (1.5): strongly HOME
    books["smarkets"]      = (1.82, 3.5, 5.0)   # sharp (1.5): strongly HOME
    books["betfred_uk"]    = (5.50, 3.5, 1.75)  # soft (0.7): strongly AWAY
    books["coral"]         = (5.50, 3.5, 1.75)  # soft (0.7): strongly AWAY
    books["pinnacle"]      = (2.50, 3.3, 2.90)  # neutral anchor; reaches min_books=20

    ev = synthetic_event(h2h_prices=books)

    # Use min_edge=-1.0 to force at least one HOME bet regardless of edge
    a_probe = StrategyConfig(name="_probe_a", label="", description="", min_edge=-1.0)
    j_probe = StrategyConfig(
        name="_probe_j", label="", description="",
        min_edge=-1.0, sharpness_weights=SHARPNESS_WEIGHTS,
    )
    a_bets = evaluate_strategy([ev], "soccer_epl", a_probe)
    j_bets = evaluate_strategy([ev], "soccer_epl", j_probe)

    a_h = next((b["cons"] for b in a_bets if b["side"] == "H"), None)
    j_h = next((b["cons"] for b in j_bets if b["side"] == "H"), None)

    assert a_h is not None, "A probe must produce a HOME bet (check min_books vs fixture)"
    assert j_h is not None, "J probe must produce a HOME bet"
    # Sharps strongly favour HOME; up-weighting them raises J's HOME consensus above A's.
    assert j_h > a_h, (
        f"J HOME cons {j_h:.4f} should > A HOME cons {a_h:.4f} "
        "when sharp books (weight 1.5) strongly favour HOME and soft books are down-weighted"
    )


# ── R.8 tests (K_draw_bias) ───────────────────────────────────────────────────

def _low_xg_team_data(home: str, away: str, q25: float = 1.2) -> dict:
    """Synthetic team_xg with both teams below q25."""
    return {
        "xg_q25": q25,
        "teams": {
            home: {"avg_xg": round(q25 * 0.7, 3), "n": 5},
            away: {"avg_xg": round(q25 * 0.6, 3), "n": 5},
        },
    }


def _high_xg_team_data(home: str, away: str, q25: float = 1.2) -> dict:
    """Synthetic team_xg with both teams above q25."""
    return {
        "xg_q25": q25,
        "teams": {
            home: {"avg_xg": round(q25 * 1.5, 3), "n": 5},
            away: {"avg_xg": round(q25 * 1.8, 3), "n": 5},
        },
    }


def _k_event(draw_odds: float = 3.55) -> dict:
    """Event with 20 UK books + betfair at the given draw odds.

    Base books have short draw odds (consensus draw prob ~0.323). After Betfair's
    5% commission, draw_odds≥3.55 is needed for a genuine ≥3% true edge (cons − eff_implied).
    """
    base = [b for b in sorted(UK_LICENSED_BOOKS) if b != "betfair_ex_uk"][:19]
    books = {b: (2.70, 2.85, 2.80) for b in base}  # short draw → high consensus draw prob
    books["pinnacle"] = (2.65, 2.90, 2.85)
    books["betfair_ex_uk"] = (2.70, draw_odds, 2.80)
    return synthetic_event(h2h_prices=books)


def test_variant_K_draw_bias_config():
    k = _strategy("K_draw_bias")
    assert k.draws_only is True
    assert k.draw_odds_band == (3.20, 3.60)
    assert k.require_low_xg is True
    assert k.markets == ("h2h",)


def test_variant_K_only_produces_draw_bets():
    ev = _k_event(draw_odds=3.55)
    xg = _low_xg_team_data("Arsenal", "Chelsea")
    bets = evaluate_strategy([ev], "soccer_epl", _strategy("K_draw_bias"), team_xg=xg)
    assert bets, "K_draw_bias should produce at least one bet on low-xG in-band fixture"
    for bet in bets:
        assert bet["side"] == "D", (
            f"K_draw_bias must only produce draw bets; got side={bet['side']}"
        )


def test_variant_K_rejects_draw_outside_odds_band():
    # Draw odds 3.70 — outside (3.20, 3.60) band → no K bets
    ev = _k_event(draw_odds=3.70)
    xg = _low_xg_team_data("Arsenal", "Chelsea")
    bets = evaluate_strategy([ev], "soccer_epl", _strategy("K_draw_bias"), team_xg=xg)
    draw_bets = [b for b in bets if b["side"] == "D"]
    assert draw_bets == [], (
        f"K_draw_bias must reject draw odds 3.70 (outside 3.20–3.60); got {draw_bets}"
    )


def test_variant_K_rejects_high_xg_fixture():
    # Both teams above xg_q25 → K must not flag draw
    ev = _k_event(draw_odds=3.40)
    xg = _high_xg_team_data("Arsenal", "Chelsea")
    bets = evaluate_strategy([ev], "soccer_epl", _strategy("K_draw_bias"), team_xg=xg)
    draw_bets = [b for b in bets if b["side"] == "D"]
    assert draw_bets == [], (
        f"K_draw_bias must reject high-xG fixtures; got {draw_bets}"
    )


def test_variant_K_blocks_bets_when_no_xg_data():
    # team_xg={} means no teams found → missing data is treated as "unknown = block"
    # to prevent polluting paper data with band-only draw bets.
    ev = _k_event(draw_odds=3.40)
    bets = evaluate_strategy([ev], "soccer_epl", _strategy("K_draw_bias"), team_xg={})
    assert bets == [], (
        f"K_draw_bias must block all bets when team_xg has no data; got {bets}"
    )


def test_variant_K_rejects_below_band_odds():
    # Draw odds 3.10 — below the 3.20 floor → rejected
    ev = _k_event(draw_odds=3.10)
    xg = _low_xg_team_data("Arsenal", "Chelsea")
    bets = evaluate_strategy([ev], "soccer_epl", _strategy("K_draw_bias"), team_xg=xg)
    draw_bets = [b for b in bets if b["side"] == "D"]
    assert draw_bets == [], (
        f"K_draw_bias must reject draw odds 3.10 (below 3.20 floor); got {draw_bets}"
    )


# ── R.11: config_hash provenance ─────────────────────────────────────────────


def test_config_hash_deterministic():
    """Same config → same hash, both within a run and across StrategyConfig instances."""
    from src.betting.strategies import StrategyConfig
    a = StrategyConfig(name="X", label="X", description="x", min_edge=0.03, min_books=20)
    b = StrategyConfig(name="X", label="X", description="x", min_edge=0.03, min_books=20)
    assert a.config_hash() == b.config_hash()
    assert len(a.config_hash()) == 12


def test_config_hash_changes_when_threshold_changes():
    """Tweaking any behavior field must produce a different hash."""
    from src.betting.strategies import StrategyConfig
    base = StrategyConfig(name="X", label="X", description="x", min_edge=0.03)
    diff = StrategyConfig(name="X", label="X", description="x", min_edge=0.04)
    assert base.config_hash() != diff.config_hash()


def test_config_hash_excludes_identity_fields():
    """name / label / description must NOT affect the hash — they're identity, not behavior.
    Renaming a variant without changing thresholds preserves the hash."""
    from src.betting.strategies import StrategyConfig
    a = StrategyConfig(name="A", label="A: lab", description="alpha", min_edge=0.03)
    b = StrategyConfig(name="B", label="B: lab", description="beta",  min_edge=0.03)
    assert a.config_hash() == b.config_hash()


def test_every_shipped_variant_has_a_distinct_config_hash():
    """In STRATEGIES, no two variants should share a hash — collision means one is
    functionally a duplicate of the other (or the hash function is broken)."""
    from src.betting.strategies import STRATEGIES
    by_hash: dict = {}
    for s in STRATEGIES:
        by_hash.setdefault(s.config_hash(), []).append(s.name)
    collisions = {h: names for h, names in by_hash.items() if len(names) > 1}
    assert not collisions, f"variants with identical config_hash: {collisions}"
