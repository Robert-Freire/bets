import math
from src.betting.devig import shin, proportional, power


def test_shin_no_overround_returns_unchanged():
    probs = [0.5, 0.3, 0.2]
    result = shin(probs)
    for orig, fair in zip(probs, result):
        assert abs(orig - fair) < 1e-9


def test_shin_5pct_overround_2way():
    probs = [0.55, 0.50]  # sum = 1.05
    result = shin(probs)
    assert abs(sum(result) - 1.0) < 1e-6


def test_shin_5pct_overround_3way():
    probs = [0.50, 0.30, 0.25]  # sum = 1.05
    result = shin(probs)
    assert abs(sum(result) - 1.0) < 1e-6
    # ordering preserved: favourite stays favourite
    assert result[0] > result[1] > result[2]


def test_shin_falls_back_to_proportional_on_pathological_input():
    # Extreme overround that might stress the bisection
    probs = [0.99, 0.99]  # sum = 1.98, very high overround
    result = shin(probs)
    # Should not raise and should sum to ~1.0
    assert abs(sum(result) - 1.0) < 1e-5


def test_proportional_normalises_sum():
    probs = [0.4, 0.3, 0.2, 0.15]
    result = proportional(probs)
    assert abs(sum(result) - 1.0) < 1e-12


def test_power_two_way_convergence():
    probs = [0.55, 0.50]  # sum = 1.05
    result = power(probs)
    assert abs(sum(result) - 1.0) < 1e-6
