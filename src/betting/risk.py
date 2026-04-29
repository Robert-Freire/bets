"""
Risk management: stake rounding, per-fixture cap, portfolio cap, drawdown brake.
"""
import csv
import json
import os
from pathlib import Path

_LOGS_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
_BANKROLL_STATE = _LOGS_DIR / "bankroll.json"
_BETS_CSV = _LOGS_DIR / "bets.csv"

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


def compute_raw_stake(cons: float, odds: float, bankroll: float) -> float:
    """Half-Kelly stake, hard-capped at 5% bankroll before risk adjustments.
    At typical edges (3–8%) the 5% cap dominates — Kelly rarely reaches it."""
    kelly = max(0.0, min(0.5 * (cons * odds - 1) / (odds - 1), 0.05))
    return kelly * bankroll


def round_stake(stake: float, rounding: int = STAKE_ROUNDING) -> float:
    """Round to nearest £rounding; return 0 if below half-rounding (too small to place)."""
    if stake < rounding / 2:
        return 0.0
    return float(round(stake / rounding) * rounding)


def _settled_pnl() -> float:
    if not _BETS_CSV.exists():
        return 0.0
    total = 0.0
    try:
        with open(_BETS_CSV, newline="") as f:
            for row in csv.DictReader(f):
                raw = row.get("pnl", "")
                if raw:
                    try:
                        total += float(raw)
                    except ValueError:
                        pass
    except Exception:
        pass
    return total


def load_drawdown_state(bankroll: float) -> tuple[float, float]:
    """
    Returns (current_bankroll, high_water). Reads and updates logs/bankroll.json.
    current_bankroll = initial + all settled P&L from bets.csv.
    """
    state: dict = {}
    if _BANKROLL_STATE.exists():
        try:
            state = json.loads(_BANKROLL_STATE.read_text())
        except Exception:
            pass

    initial = float(state.get("initial_bankroll", bankroll))
    high_water = float(state.get("high_water", initial))
    current = initial + _settled_pnl()

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
