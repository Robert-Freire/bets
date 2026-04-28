"""
Kelly criterion bet sizing.
Uses half-Kelly (0.5x) for safety as recommended by Kaunitz et al. (2017)
and Hubáček et al. (2019).
"""

import pandas as pd
import numpy as np


def kelly_fraction(prob: float, odds: float) -> float:
    """
    Full Kelly fraction of bankroll to bet.

    Parameters
    ----------
    prob : our estimated probability of winning
    odds : decimal odds offered by bookmaker

    Returns
    -------
    fraction of bankroll to bet (can be negative = no bet)
    """
    b = odds - 1  # net profit per unit staked
    return (prob * odds - 1) / b


def half_kelly(prob: float, odds: float) -> float:
    """Half-Kelly (safer, less variance)."""
    return 0.5 * kelly_fraction(prob, odds)


def size_bets(
    value_bets: pd.DataFrame,
    bankroll: float = 1000.0,
    kelly_multiplier: float = 0.5,
    max_bet_fraction: float = 0.05,
) -> pd.DataFrame:
    """
    Add bet sizing to a value bets DataFrame.

    Parameters
    ----------
    value_bets : output from betting.value.find_value_bets
    bankroll : total available bankroll
    kelly_multiplier : 0.5 = half-Kelly (recommended)
    max_bet_fraction : cap any single bet at this fraction of bankroll
    """
    df = value_bets.copy()

    fractions = []
    amounts = []
    for _, row in df.iterrows():
        f = kelly_multiplier * kelly_fraction(row["model_prob"], row["best_odds"])
        f = max(0.0, min(f, max_bet_fraction))  # floor at 0, cap at max
        fractions.append(round(f, 4))
        amounts.append(round(f * bankroll, 2))

    df["kelly_fraction"] = fractions
    df["bet_amount"] = amounts
    return df


def simulate_bankroll(sized_bets: pd.DataFrame, initial_bankroll: float = 1000.0) -> pd.DataFrame:
    """
    Simulate bankroll evolution over a sequence of sized bets.

    sized_bets must have: bet_amount, best_odds, bet_side, result columns.
    result is FTR string ('H', 'D', 'A'), bet_side is ('H', 'D', 'A').
    """
    df = sized_bets.copy()
    bankroll = initial_bankroll
    history = []

    for _, row in df.iterrows():
        won = row["result"] == row["bet_side"]
        if won:
            pnl = row["bet_amount"] * (row["best_odds"] - 1)
        else:
            pnl = -row["bet_amount"]

        bankroll += pnl
        history.append({
            "date": row.get("date"),
            "home_team": row.get("home_team"),
            "away_team": row.get("away_team"),
            "bet_side": row["bet_side"],
            "result": row["result"],
            "won": won,
            "bet_amount": row["bet_amount"],
            "pnl": round(pnl, 2),
            "bankroll": round(bankroll, 2),
        })

    result_df = pd.DataFrame(history)
    if not result_df.empty:
        n = len(result_df)
        n_won = result_df["won"].sum()
        total_staked = result_df["bet_amount"].sum()
        total_pnl = result_df["pnl"].sum()
        roi = total_pnl / total_staked if total_staked > 0 else 0.0
        print(f"Bets: {n} | Won: {n_won} ({n_won/n:.1%}) | Staked: {total_staked:.0f} | "
              f"P&L: {total_pnl:+.0f} | ROI: {roi:+.2%} | "
              f"Final bankroll: {bankroll:.0f}")
    return result_df
