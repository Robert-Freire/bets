"""
Kaunitz, Zhong & Kreiner (2017) consensus strategy:
Use the market consensus (average fair odds across bookmakers) as the proxy for
true probability. Bet when a single bookmaker offers odds significantly above consensus.

This approach doesn't need a prediction model — the market itself is the model.
"""

import pandas as pd
import numpy as np


# All bookmaker odds columns available in football-data.co.uk (home/draw/away triplets)
BOOKMAKER_GROUPS = {
    "B365": ("B365H", "B365D", "B365A"),
    "BW":   ("BWH",   "BWD",   "BWA"),
    "IW":   ("IWH",   "IWD",   "IWA"),
    "PS":   ("PSH",   "PSD",   "PSA"),
    "WH":   ("WHH",   "WHD",   "WHA"),
    "VC":   ("VCH",   "VCD",   "VCA"),
    "BF":   ("BFH",   "BFD",   "BFA"),
    "BFE":  ("BFEH",  "BFED",  "BFEA"),
    "1XB":  ("1XBH",  "1XBD",  "1XBA"),
    "BFD":  ("BFDH",  "BFDD",  "BFDA"),
    "BV":   ("BVH",   "BVD",   "BVA"),
    "LB":   ("LBH",   "LBD",   "LBA"),
}

SIDES = ["H", "D", "A"]
SIDE_TO_IDX = {"H": 0, "D": 1, "A": 2}


def compute_consensus(matches: pd.DataFrame) -> pd.DataFrame:
    """
    For each match, compute the consensus implied probability (raw average, margin NOT removed)
    across all available bookmakers.

    Kaunitz et al. use the raw average implied prob as the market consensus signal.
    A bookmaker offers value when its implied prob is LOWER than consensus (= better odds).

    Returns matches with added columns:
        consensus_prob_H, consensus_prob_D, consensus_prob_A  (raw, with margin)
        n_books_used
    """
    matches = matches.copy()
    prob_h, prob_d, prob_a, n_books = [], [], [], []

    for _, row in matches.iterrows():
        book_probs = {"H": [], "D": [], "A": []}
        for book, (ch, cd, ca) in BOOKMAKER_GROUPS.items():
            if ch not in row.index:
                continue
            oh, od, oa = row.get(ch), row.get(cd), row.get(ca)
            if pd.isna(oh) or pd.isna(od) or pd.isna(oa):
                continue
            if oh <= 1.0 or od <= 1.0 or oa <= 1.0:
                continue
            # Raw implied probs (still contain margin, intentional — Kaunitz approach)
            book_probs["H"].append(1 / oh)
            book_probs["D"].append(1 / od)
            book_probs["A"].append(1 / oa)

        if book_probs["H"]:
            prob_h.append(np.mean(book_probs["H"]))
            prob_d.append(np.mean(book_probs["D"]))
            prob_a.append(np.mean(book_probs["A"]))
            n_books.append(len(book_probs["H"]))
        else:
            prob_h.append(np.nan)
            prob_d.append(np.nan)
            prob_a.append(np.nan)
            n_books.append(0)

    matches["consensus_prob_H"] = prob_h
    matches["consensus_prob_D"] = prob_d
    matches["consensus_prob_A"] = prob_a
    matches["n_books_used"] = n_books
    return matches


def find_consensus_bets(
    matches: pd.DataFrame,
    min_edge: float = 0.02,
    min_odds: float = 1.5,
    max_odds: float = 15.0,
    min_books: int = 3,
) -> pd.DataFrame:
    """
    Find bets where a single bookmaker's odds exceed the consensus implied probability.

    For each match and outcome, check every bookmaker individually against consensus.
    Returns rows where:
        bookmaker_fair_prob > consensus_prob + min_edge

    Parameters
    ----------
    min_edge : minimum edge over consensus to flag a bet
    min_books : minimum number of books required for a reliable consensus
    """
    if "consensus_prob_H" not in matches.columns:
        matches = compute_consensus(matches)

    results = []
    for _, row in matches.iterrows():
        if row.get("n_books_used", 0) < min_books:
            continue
        if pd.isna(row.get("consensus_prob_H")):
            continue

        consensus = {
            "H": row["consensus_prob_H"],
            "D": row["consensus_prob_D"],
            "A": row["consensus_prob_A"],
        }

        for book, (ch, cd, ca) in BOOKMAKER_GROUPS.items():
            if ch not in row.index:
                continue
            oh, od, oa = row.get(ch), row.get(cd), row.get(ca)
            if pd.isna(oh) or pd.isna(od) or pd.isna(oa):
                continue
            if oh <= 1.0 or od <= 1.0 or oa <= 1.0:
                continue

            book_odds = {"H": oh, "D": od, "A": oa}
            # Raw implied prob per bookmaker (with margin)
            book_impl = {"H": 1/oh, "D": 1/od, "A": 1/oa}

            for side in SIDES:
                best_odds = book_odds[side]
                # Value: bookmaker's implied prob is LOWER than consensus = better odds offered
                edge = consensus[side] - book_impl[side]
                if edge >= min_edge and min_odds <= best_odds <= max_odds:
                    results.append({
                        "match_idx": row.name,
                        "date": row.get("Date"),
                        "home_team": row.get("HomeTeam"),
                        "away_team": row.get("AwayTeam"),
                        "bookmaker": book,
                        "bet_side": side,
                        "book_odds": round(best_odds, 2),
                        "book_impl_prob": round(book_impl[side], 4),
                        "consensus_prob": round(consensus[side], 4),
                        "edge": round(edge, 4),
                        "result": row.get("FTR"),
                        "n_books": row["n_books_used"],
                    })

    df = pd.DataFrame(results)
    if not df.empty:
        df = df.sort_values("edge", ascending=False).reset_index(drop=True)
    return df


def backtest_consensus(
    matches: pd.DataFrame,
    min_edge: float = 0.02,
    bankroll: float = 1000.0,
    kelly_multiplier: float = 0.5,
    min_books: int = 3,
) -> dict:
    """Full backtest of the Kaunitz consensus strategy."""
    from src.betting.kelly import kelly_fraction

    matches = compute_consensus(matches)
    bets = find_consensus_bets(matches, min_edge=min_edge, min_books=min_books)

    if bets.empty:
        return {"n_bets": 0, "roi": 0.0}

    total_staked = 0.0
    total_pnl = 0.0
    n_won = 0

    for _, bet in bets.iterrows():
        f = kelly_multiplier * kelly_fraction(bet["consensus_prob"], bet["book_odds"])
        f = max(0.0, min(f, 0.05))
        stake = f * bankroll
        won = bet["result"] == bet["bet_side"]
        pnl = stake * (bet["book_odds"] - 1) if won else -stake
        total_staked += stake
        total_pnl += pnl
        if won:
            n_won += 1

    roi = total_pnl / total_staked if total_staked > 0 else 0.0
    return {
        "n_bets": len(bets),
        "n_won": n_won,
        "win_rate": round(n_won / len(bets), 3),
        "total_staked": round(total_staked, 0),
        "total_pnl": round(total_pnl, 0),
        "roi": round(roi, 4),
        "final_bankroll": round(bankroll + total_pnl, 0),
    }


def backtest_combined(
    matches: pd.DataFrame,
    model_probs: pd.DataFrame,
    min_kaunitz_edge: float = 0.03,
    min_model_edge: float = 0.0,
    bankroll: float = 1000.0,
    kelly_multiplier: float = 0.5,
    min_books: int = 3,
) -> dict:
    """
    Dual-filter strategy: bet only when Kaunitz consensus AND CatBoost model both see value.

    model_probs must be indexed like matches (subset only), with columns
    [home_win, draw, away_win]. Only matches covered by the model participate.

    min_kaunitz_edge : minimum consensus edge (same as pure Kaunitz threshold)
    min_model_edge   : model_prob - book_implied_prob must be >= this (0 = directional
                       agreement only; positive = model must also see explicit edge)
    """
    from src.betting.kelly import kelly_fraction

    matches_with_consensus = compute_consensus(matches)
    consensus_bets = find_consensus_bets(
        matches_with_consensus, min_edge=min_kaunitz_edge, min_books=min_books
    )

    if consensus_bets.empty or model_probs.empty:
        return {"n_bets": 0, "roi": 0.0, "n_won": 0, "win_rate": 0.0,
                "total_staked": 0, "total_pnl": 0, "final_bankroll": bankroll}

    side_to_col = {"H": "home_win", "D": "draw", "A": "away_win"}
    total_staked = 0.0
    total_pnl = 0.0
    n_won = 0
    n_filtered = 0

    for _, bet in consensus_bets.iterrows():
        idx = bet["match_idx"]
        if idx not in model_probs.index:
            continue

        col = side_to_col[bet["bet_side"]]
        model_p = model_probs.loc[idx, col]
        if pd.isna(model_p):
            continue

        if model_p - bet["book_impl_prob"] < min_model_edge:
            continue

        n_filtered += 1
        f = kelly_multiplier * kelly_fraction(bet["consensus_prob"], bet["book_odds"])
        f = max(0.0, min(f, 0.05))
        stake = f * bankroll
        won = bet["result"] == bet["bet_side"]
        pnl = stake * (bet["book_odds"] - 1) if won else -stake
        total_staked += stake
        total_pnl += pnl
        if won:
            n_won += 1

    roi = total_pnl / total_staked if total_staked > 0 else 0.0
    return {
        "n_bets": n_filtered,
        "n_won": n_won,
        "win_rate": round(n_won / n_filtered, 3) if n_filtered > 0 else 0.0,
        "total_staked": round(total_staked, 0),
        "total_pnl": round(total_pnl, 0),
        "roi": round(roi, 4),
        "final_bankroll": round(bankroll + total_pnl, 0),
    }
