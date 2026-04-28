"""
Value bet detection: find matches where our probability exceeds the bookmaker's
implied probability by a minimum threshold.
"""

import pandas as pd
import numpy as np


def implied_prob(odds: float) -> float:
    return 1.0 / odds


def margin_free_probs(odds_h: float, odds_d: float, odds_a: float) -> tuple[float, float, float]:
    """Strip bookmaker margin from raw odds."""
    raw = np.array([1/odds_h, 1/odds_d, 1/odds_a])
    fair = raw / raw.sum()
    return float(fair[0]), float(fair[1]), float(fair[2])


def find_value_bets(
    model_probs: pd.DataFrame,
    matches: pd.DataFrame,
    min_edge: float = 0.03,
    min_odds: float = 1.5,
    max_odds: float = 15.0,
    odds_cols: tuple = ("best_odds_H", "best_odds_D", "best_odds_A"),
) -> pd.DataFrame:
    """
    Identify value bets where model probability exceeds bookmaker implied probability.

    Parameters
    ----------
    model_probs : DataFrame with [home_win, draw, away_win] columns
    matches : original matches DataFrame with bookmaker odds
    min_edge : minimum edge (model_prob - implied_prob) to flag a bet
    min_odds / max_odds : filter out extreme odds (illiquid or low-value)

    Returns
    -------
    DataFrame of value bets with edge and recommended bet side
    """
    oh, od, oa = odds_cols
    results = []

    for i, (idx, model_row) in enumerate(model_probs.iterrows()):
        match_row = matches.loc[idx] if idx in matches.index else matches.iloc[i]

        odds_h = match_row.get(oh)
        odds_d = match_row.get(od)
        odds_a = match_row.get(oa)

        if pd.isna(odds_h) or pd.isna(odds_d) or pd.isna(odds_a):
            continue

        implied_h, implied_d, implied_a = margin_free_probs(odds_h, odds_d, odds_a)

        candidates = [
            ("H", model_row["home_win"], implied_h, odds_h),
            ("D", model_row["draw"], implied_d, odds_d),
            ("A", model_row["away_win"], implied_a, odds_a),
        ]

        for side, our_prob, their_prob, best_odds in candidates:
            edge = our_prob - their_prob
            if edge >= min_edge and min_odds <= best_odds <= max_odds:
                results.append({
                    "match_idx": idx,
                    "date": match_row.get("Date"),
                    "home_team": match_row.get("HomeTeam"),
                    "away_team": match_row.get("AwayTeam"),
                    "bet_side": side,
                    "model_prob": round(our_prob, 4),
                    "implied_prob": round(their_prob, 4),
                    "edge": round(edge, 4),
                    "best_odds": round(best_odds, 2),
                    "result": match_row.get("FTR"),
                })

    df = pd.DataFrame(results)
    if not df.empty:
        df = df.sort_values("edge", ascending=False).reset_index(drop=True)
    return df
