"""
De-vigging: remove bookmaker overround from raw implied probabilities.

Three methods (in order of theoretical sophistication):
  proportional  — divide each raw prob by the sum (simplest, biased toward long shots)
  shin          — Shin (1993) iterative method; best for soccer 1X2 markets
  power         — power/exponent method; good for two-outcome markets

Reference: Shin, H.S. (1993). Measuring the Incidence of Insider Trading in a
Market for State-Contingent Claims. The Economic Journal, 103(420), 1141-1153.
"""

import math


def proportional(probs: list[float]) -> list[float]:
    """Divide each implied prob by the overround (sum)."""
    total = sum(probs)
    if total <= 0:
        raise ValueError(f"probs must be positive, got sum={total}")
    return [p / total for p in probs]


def shin(probs: list[float], tol: float = 1e-9, max_iter: int = 50) -> list[float]:
    """
    Shin (1993) iterative de-vigging via binary search on insider-trader fraction z.

    For a market with overround s = sum(probs), solves for z ∈ (0, 1) such that
    the fair probabilities (derived from the Shin model) sum to exactly 1.

    Fair probability for outcome i:
        pᵢ_fair = (√(z² + 4(1−z)·pᵢ²/s) − z) / (2(1−z))

    Falls back to proportional if the market already sums to ≤1 or does not converge.
    """
    s = sum(probs)
    if abs(s - 1.0) < tol:
        return list(probs)  # no overround — return unchanged

    def _fair(z: float) -> list[float]:
        out = []
        two_minus_2z = 2.0 * (1.0 - z)
        for p in probs:
            disc = z * z + 4.0 * (1.0 - z) * p * p / s
            out.append((math.sqrt(max(disc, 0.0)) - z) / two_minus_2z)
        return out

    # g(z) = sum(fair probs) − 1; want g(z) = 0
    # At z=0: sum = sqrt(s) > 1 (since s > 1) → g(0) > 0
    # As z → 1: sum → sum(pᵢ²)/s < 1 for typical markets → g(1−ε) < 0
    lo, hi = 0.0, 1.0 - 1e-10

    if sum(_fair(lo)) - 1.0 <= 0.0:
        return proportional(probs)  # unexpected — fall back

    converged = False
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        gm = sum(_fair(mid)) - 1.0
        if abs(gm) < tol:
            lo = hi = mid
            converged = True
            break
        if gm > 0.0:
            lo = mid
        else:
            hi = mid

    if not converged and abs(sum(_fair((lo + hi) / 2.0)) - 1.0) > 1e-4:
        return proportional(probs)  # binary search didn't converge well enough

    fair = _fair((lo + hi) / 2.0)
    total = sum(fair)
    return [f / total for f in fair]  # normalise for floating-point cleanliness


def power(probs: list[float], tol: float = 1e-9, max_iter: int = 100) -> list[float]:
    """
    Power method: find exponent k > 1 such that sum(pᵢᵏ) = 1.

    Raises each implied prob to power k until they sum to 1.
    Good for two-outcome markets (tennis, NBA moneyline).
    Falls back to proportional if no solution found in [1, 100].
    """
    s = sum(probs)
    if abs(s - 1.0) < tol:
        return list(probs)

    def f(k: float) -> float:
        return sum(p ** k for p in probs) - 1.0

    if f(1.0) <= 0.0:
        return proportional(probs)

    hi = 100.0
    if f(hi) > 0.0:
        return proportional(probs)  # no solution in range

    lo = 1.0
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        fm = f(mid)
        if abs(fm) < tol:
            break
        if fm > 0.0:
            lo = mid
        else:
            hi = mid

    k = (lo + hi) / 2.0
    fair = [p ** k for p in probs]
    total = sum(fair)
    return [f / total for f in fair]
