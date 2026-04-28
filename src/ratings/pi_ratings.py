"""
Pi-ratings: dynamic team strength ratings based on goal-score discrepancies.
Based on Constantinou & Fenton (2013).

Each team has two ratings: home (h) and away (a).
Updated after every match using the error between expected and actual goal difference.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TeamRating:
    home: float = 0.0
    away: float = 0.0
    matches: int = 0


class PiRatings:
    """
    Dynamic pi-rating system.

    Parameters
    ----------
    lr : float
        Learning rate (0.06 per Constantinou 2013)
    home_advantage : float
        Expected goal advantage for home team in a match between equal teams
    """

    def __init__(self, lr: float = 0.06, home_advantage: float = 0.4):
        self.lr = lr
        self.home_advantage = home_advantage
        self.ratings: dict[str, TeamRating] = {}

    def _get(self, team: str) -> TeamRating:
        if team not in self.ratings:
            self.ratings[team] = TeamRating()
        return self.ratings[team]

    def expected_goal_diff(self, home_team: str, away_team: str) -> float:
        """Expected home_goals - away_goals."""
        h = self._get(home_team)
        a = self._get(away_team)
        return (h.home - a.away) + self.home_advantage

    def update(self, home_team: str, away_team: str, home_goals: int, away_goals: int):
        """Update ratings after a match result."""
        h = self._get(home_team)
        a = self._get(away_team)

        actual_diff = home_goals - away_goals
        expected_diff = self.expected_goal_diff(home_team, away_team)
        error = actual_diff - expected_diff

        h.home += self.lr * error
        h.away += self.lr * error * 0.5
        a.away -= self.lr * error
        a.home -= self.lr * error * 0.5

        h.matches += 1
        a.matches += 1

    def fit(self, matches: pd.DataFrame) -> "PiRatings":
        """
        Fit ratings on a DataFrame of historical matches.

        Expects columns: Date, HomeTeam, AwayTeam, FTHG, FTAG
        Matches must be sorted by Date (ascending).
        """
        for _, row in matches.iterrows():
            self.update(row["HomeTeam"], row["AwayTeam"], int(row["FTHG"]), int(row["FTAG"]))
        return self

    def get_features(self, home_team: str, away_team: str) -> dict:
        """Return rating-based features for a fixture (before the match is played)."""
        h = self._get(home_team)
        a = self._get(away_team)
        exp_diff = self.expected_goal_diff(home_team, away_team)
        return {
            "home_rating_h": h.home,
            "home_rating_a": h.away,
            "away_rating_h": a.home,
            "away_rating_a": a.away,
            "rating_diff_home": h.home - a.away,
            "rating_diff_away": a.home - h.away,
            "expected_goal_diff": exp_diff,
            "home_matches": h.matches,
            "away_matches": a.matches,
        }

    def snapshot(self) -> pd.DataFrame:
        """Return current ratings for all teams as a DataFrame."""
        rows = []
        for team, r in self.ratings.items():
            rows.append({
                "team": team,
                "home_rating": r.home,
                "away_rating": r.away,
                "matches": r.matches,
                "overall": (r.home + r.away) / 2,
            })
        return pd.DataFrame(rows).sort_values("overall", ascending=False).reset_index(drop=True)


def build_rolling_ratings(matches: pd.DataFrame, lr: float = 0.06, home_advantage: float = 0.4) -> pd.DataFrame:
    """
    Build pi-ratings in walk-forward fashion: for each match, record the
    pre-match ratings as features, then update.

    Returns the original matches DataFrame with rating feature columns added.
    """
    matches = matches.sort_values("Date").reset_index(drop=True)
    pi = PiRatings(lr=lr, home_advantage=home_advantage)

    feature_rows = []
    for _, row in matches.iterrows():
        features = pi.get_features(row["HomeTeam"], row["AwayTeam"])
        features["match_idx"] = row.name
        feature_rows.append(features)
        pi.update(row["HomeTeam"], row["AwayTeam"], int(row["FTHG"]), int(row["FTAG"]))

    features_df = pd.DataFrame(feature_rows).set_index("match_idx")
    return matches.join(features_df)
