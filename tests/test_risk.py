import pytest
from src.betting.risk import (
    round_stake,
    drawdown_multiplier,
    _apply_fixture_cap,
    _apply_portfolio_cap,
    apply_risk_pipeline,
    STAKE_ROUNDING,
    DRAWDOWN_THRESHOLD,
)


def _bet(home="A", away="B", stake=100.0):
    return {"home": home, "away": away, "stake": stake}


def test_round_stake_below_half_rounding_drops():
    assert round_stake(2.0) == 0.0


def test_round_stake_to_nearest_5():
    # £12.50 is exactly halfway between £10 and £15 — Python's banker's rounding
    # rounds to nearest even → £10. Lock that in.
    assert round_stake(12.50) == 10.0
    assert round_stake(13.0) == 15.0
    assert round_stake(7.0) == 5.0


def test_fixture_cap_scales_within_fixture():
    bankroll = 1000.0
    bets = [_bet("Arsenal", "Chelsea", 30.0), _bet("Arsenal", "Chelsea", 30.0)]
    _apply_fixture_cap(bets, bankroll)
    assert sum(b["stake"] for b in bets) <= bankroll * 0.05 + 1e-9
    # Bets on different fixtures are unaffected
    other = [_bet("Man City", "Liverpool", 30.0)]
    _apply_fixture_cap(other, bankroll)
    assert other[0]["stake"] == pytest.approx(30.0)


def test_portfolio_cap_scales_uniformly():
    bankroll = 1000.0
    bets = [_bet(str(i), str(i + 1), 10.0) for i in range(20)]  # £200 total > 15%
    original_stakes = [b["stake"] for b in bets]
    _apply_portfolio_cap(bets, bankroll)
    total = sum(b["stake"] for b in bets)
    assert total <= bankroll * 0.15 + 1e-9
    # Relative ratios preserved (all started equal so all end equal)
    for b in bets:
        assert b["stake"] == pytest.approx(bets[0]["stake"])


def test_drawdown_multiplier_halves_when_15pct_below_high_water():
    # Threshold is strict (<), so exactly at 85% (£850) does NOT trigger halving
    assert drawdown_multiplier(849.0, 1000.0) == pytest.approx(0.5)
    assert drawdown_multiplier(850.0, 1000.0) == pytest.approx(1.0)
    assert drawdown_multiplier(900.0, 1000.0) == pytest.approx(1.0)


def test_pipeline_order():
    # Drawdown halving should happen BEFORE caps; rounding LAST.
    bankroll = 1000.0
    bets = [_bet("A", "B", 200.0)]  # would exceed fixture cap before halving
    result = apply_risk_pipeline(bets, bankroll, drawdown_mult=0.5)
    # After 0.5 mult: £100; fixture cap £50; portfolio cap £150 (no further effect)
    # After rounding: £50
    if result:
        assert result[0]["stake"] == pytest.approx(50.0)
