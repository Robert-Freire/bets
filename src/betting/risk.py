"""
Risk management: stake rounding, per-fixture cap, portfolio cap, drawdown brake.
"""
import json
import os
from pathlib import Path

try:
    from src.betting.commissions import effective_odds as _effective_odds
except ImportError:
    def _effective_odds(odds: float, book: str) -> float: return odds  # noqa: E704

_LOGS_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
_BANKROLL_STATE = _LOGS_DIR / "bankroll.json"

STAKE_ROUNDING = 5           # round to nearest £5
MAX_FIXTURE_FRACTION = 0.05  # cap total exposure per fixture
MAX_PORTFOLIO_FRACTION = 0.15  # cap all stakes in one scan
DRAWDOWN_THRESHOLD = 0.85    # halve stakes if bankroll < high_water * this


def get_bankroll() -> float:
    """Read bankroll from BANKROLL env var, then config.json, then default 1000."""
    if "BANKROLL" in os.environ:
        return float(os.environ["BANKROLL"])
    config_path = Path(__file__).resolve().parent.parent.parent / "config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text())
            if "bankroll" in cfg:
                return float(cfg["bankroll"])
        except Exception:
            pass
    return 1000.0


def compute_raw_stake(cons: float, odds: float, bankroll: float, book: str = "",
                      kelly_multiplier: float = 0.5) -> float:
    """Fractional-Kelly stake, hard-capped at 5% bankroll before risk adjustments.
    At typical edges (3–8%) the 5% cap dominates — Kelly rarely reaches it."""
    eff = _effective_odds(odds, book) if book else odds
    kelly = max(0.0, min(kelly_multiplier * (cons * eff - 1) / (eff - 1), 0.05))
    return kelly * bankroll


def round_stake(stake: float, rounding: int = STAKE_ROUNDING) -> float:
    """Round to nearest £rounding; return 0 if below half-rounding (too small to place)."""
    if stake < rounding / 2:
        return 0.0
    return float(round(stake / rounding) * rounding)


def load_drawdown_state(bankroll: float,
                        settled_pnl: float = 0.0) -> tuple[float, float]:
    """Returns (current_bankroll, high_water). Reads and updates logs/bankroll.json.

    Pass settled_pnl from the DB (e.g. repo.get_settled_pnl()) so the
    drawdown brake reflects real P&L. Defaults to 0.0 when DB is unavailable.
    """
    state: dict = {}
    if _BANKROLL_STATE.exists():
        try:
            state = json.loads(_BANKROLL_STATE.read_text())
        except Exception:
            pass

    initial = float(state.get("initial_bankroll", bankroll))
    high_water = float(state.get("high_water", initial))
    current = initial + settled_pnl

    if current > high_water:
        high_water = current

    state.update({
        "initial_bankroll": initial,
        "high_water": round(high_water, 2),
        "current_bankroll": round(current, 2),
    })
    _BANKROLL_STATE.write_text(json.dumps(state, indent=2))
    return current, high_water


def drawdown_multiplier(current: float, high_water: float) -> float:
    """0.5 if in drawdown (> 15% below high water), else 1.0."""
    if high_water > 0 and current < high_water * DRAWDOWN_THRESHOLD:
        return 0.5
    return 1.0


def _apply_fixture_cap(bets: list[dict], bankroll: float) -> None:
    """Scale bets on the same fixture so their combined stake ≤ MAX_FIXTURE_FRACTION * bankroll."""
    max_stake = bankroll * MAX_FIXTURE_FRACTION
    totals: dict[tuple, float] = {}
    for bet in bets:
        key = (bet["home"], bet["away"])
        totals[key] = totals.get(key, 0.0) + bet["stake"]
    for bet in bets:
        key = (bet["home"], bet["away"])
        total = totals[key]
        if total > max_stake and total > 0:
            bet["stake"] = bet["stake"] * max_stake / total


def _apply_portfolio_cap(bets: list[dict], bankroll: float) -> None:
    """Scale all stakes uniformly if their sum exceeds MAX_PORTFOLIO_FRACTION * bankroll."""
    max_total = bankroll * MAX_PORTFOLIO_FRACTION
    total = sum(b["stake"] for b in bets)
    if total > max_total and total > 0:
        scale = max_total / total
        for bet in bets:
            bet["stake"] = bet["stake"] * scale


def apply_risk_pipeline(
    bets: list[dict],
    bankroll: float,
    drawdown_mult: float = 1.0,
) -> list[dict]:
    """
    Full pipeline: drawdown multiplier → fixture cap → portfolio cap → rounding.
    Modifies each bet's 'stake' in place. Returns bets with stake > 0 only.
    """
    if drawdown_mult != 1.0:
        for bet in bets:
            bet["stake"] *= drawdown_mult

    _apply_fixture_cap(bets, bankroll)
    _apply_portfolio_cap(bets, bankroll)

    for bet in bets:
        bet["stake"] = round_stake(bet["stake"])

    return [b for b in bets if b["stake"] > 0]
