"""
Dixon-Coles Poisson model for football match outcome prediction.
Based on Dixon & Coles (1997).

Models home and away goals as independent Poisson distributions with
a low-score correction factor rho (ρ) applied to scorelines 0-0, 1-0, 0-1, 1-1.
"""

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson
from typing import Optional


MAX_GOALS = 10  # Score matrix cap — covers >99.99% of probability mass


def rho_correction(home_goals: int, away_goals: int, lam_h: float, lam_a: float, rho: float) -> float:
    """Multiplicative correction for low-scoring outcomes."""
    if home_goals == 0 and away_goals == 0:
        return 1 - lam_h * lam_a * rho
    elif home_goals == 1 and away_goals == 0:
        return 1 + lam_a * rho
    elif home_goals == 0 and away_goals == 1:
        return 1 + lam_h * rho
    elif home_goals == 1 and away_goals == 1:
        return 1 - rho
    return 1.0


def score_matrix(lam_h: float, lam_a: float, rho: float, max_goals: int = MAX_GOALS) -> np.ndarray:
    """
    Compute (max_goals+1 x max_goals+1) matrix of P(home=i, away=j).
    Rows = home goals, columns = away goals.
    """
    mat = np.outer(
        poisson.pmf(range(max_goals + 1), lam_h),
        poisson.pmf(range(max_goals + 1), lam_a),
    )
    for i in range(2):
        for j in range(2):
            mat[i, j] *= rho_correction(i, j, lam_h, lam_a, rho)
    return mat


def outcome_probs(lam_h: float, lam_a: float, rho: float) -> tuple[float, float, float]:
    """Return (P_home_win, P_draw, P_away_win)."""
    mat = score_matrix(lam_h, lam_a, rho)
    p_home = float(np.tril(mat, -1).sum())
    p_draw = float(np.trace(mat))
    p_away = float(np.triu(mat, 1).sum())
    return p_home, p_draw, p_away


class DixonColesModel:
    """
    Dixon-Coles Poisson model.

    Parameters
    ----------
    xi : float
        Time-decay rate. exp(-xi * days) weights older matches less.
        0.0 = no decay, 0.005 ≈ half-weight after ~140 days.
    """

    def __init__(self, xi: float = 0.005):
        self.xi = xi
        self.params_: Optional[np.ndarray] = None
        self.teams_: Optional[list[str]] = None
        self.rho_: Optional[float] = None
        self.home_adv_: Optional[float] = None

    def _build_teams(self, matches: pd.DataFrame):
        teams = sorted(set(matches["HomeTeam"]) | set(matches["AwayTeam"]))
        self.teams_ = teams
        self.team_idx_ = {t: i for i, t in enumerate(teams)}
        return len(teams)

    def _unpack(self, params: np.ndarray) -> tuple:
        n = len(self.teams_)
        attack = params[:n]
        defense = params[n:2*n]
        home_adv = params[2*n]
        rho = params[2*n + 1]
        return attack, defense, home_adv, rho

    def _lambda(self, params, home_team: str, away_team: str) -> tuple[float, float]:
        attack, defense, home_adv, rho = self._unpack(params)
        hi = self.team_idx_[home_team]
        ai = self.team_idx_[away_team]
        lam_h = np.exp(attack[hi] - defense[ai] + home_adv)
        lam_a = np.exp(attack[ai] - defense[hi])
        return lam_h, lam_a

    def _neg_log_likelihood(self, params: np.ndarray, matches: pd.DataFrame, weights: np.ndarray) -> float:
        attack, defense, home_adv, rho = self._unpack(params)
        ll = 0.0
        for i, row in enumerate(matches.itertuples()):
            hi = self.team_idx_[row.HomeTeam]
            ai = self.team_idx_[row.AwayTeam]
            lam_h = np.exp(attack[hi] - defense[ai] + home_adv)
            lam_a = np.exp(attack[ai] - defense[hi])
            hg, ag = int(row.FTHG), int(row.FTAG)
            p = (poisson.pmf(hg, lam_h) * poisson.pmf(ag, lam_a)
                 * rho_correction(hg, ag, lam_h, lam_a, rho))
            if p <= 0:
                ll += weights[i] * -20  # heavy penalty for zero-prob outcomes
            else:
                ll += weights[i] * np.log(p)
        return -ll

    def fit(self, matches: pd.DataFrame, ref_date: Optional[pd.Timestamp] = None) -> "DixonColesModel":
        """
        Fit the model to historical match data.

        Parameters
        ----------
        matches : DataFrame with columns HomeTeam, AwayTeam, FTHG, FTAG, Date
        ref_date : Date to compute time-decay weights from. Defaults to max date.
        """
        matches = matches.dropna(subset=["HomeTeam", "AwayTeam", "FTHG", "FTAG", "Date"])
        n_teams = self._build_teams(matches)

        if ref_date is None:
            ref_date = matches["Date"].max()

        days_ago = (ref_date - matches["Date"]).dt.days.clip(lower=0).values
        weights = np.exp(-self.xi * days_ago)

        # Initial params: all zeros for attack/defense, typical home_adv and rho
        n = n_teams
        x0 = np.zeros(2 * n + 2)
        x0[2*n] = 0.3    # home advantage
        x0[2*n + 1] = -0.1  # rho

        # Constrain: sum of attack params = 0 (identification)
        # We'll just use bounds and let optimizer handle it
        bounds = (
            [(-3, 3)] * n +    # attack
            [(-3, 3)] * n +    # defense
            [(0.0, 1.0)] +     # home_adv
            [(-0.5, 0.0)]      # rho (negative = positive correlation)
        )

        result = minimize(
            self._neg_log_likelihood,
            x0,
            args=(matches, weights),
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 500, "ftol": 1e-10},
        )

        self.params_ = result.x
        attack, defense, home_adv, rho = self._unpack(result.x)
        self.home_adv_ = float(home_adv)
        self.rho_ = float(rho)
        return self

    def predict(self, home_team: str, away_team: str) -> dict:
        """Predict outcome probabilities for a fixture."""
        if self.params_ is None:
            raise RuntimeError("Model not fitted. Call fit() first.")
        lam_h, lam_a = self._lambda(self.params_, home_team, away_team)
        p_h, p_d, p_a = outcome_probs(lam_h, lam_a, self.rho_)
        return {
            "home_win": p_h,
            "draw": p_d,
            "away_win": p_a,
            "lambda_home": lam_h,
            "lambda_away": lam_a,
        }

    def predict_batch(self, fixtures: pd.DataFrame) -> pd.DataFrame:
        """
        Predict for multiple fixtures.
        fixtures must have columns: HomeTeam, AwayTeam
        """
        results = []
        for _, row in fixtures.iterrows():
            try:
                pred = self.predict(row["HomeTeam"], row["AwayTeam"])
            except (KeyError, RuntimeError):
                pred = {"home_win": np.nan, "draw": np.nan, "away_win": np.nan,
                        "lambda_home": np.nan, "lambda_away": np.nan}
            results.append(pred)
        return pd.DataFrame(results)

    def team_strengths(self) -> pd.DataFrame:
        """Return attack/defense parameters per team as a DataFrame."""
        if self.params_ is None:
            raise RuntimeError("Model not fitted.")
        attack, defense, _, _ = self._unpack(self.params_)
        rows = []
        for team in self.teams_:
            i = self.team_idx_[team]
            rows.append({
                "team": team,
                "attack": attack[i],
                "defense": defense[i],
                "strength": attack[i] - defense[i],
            })
        return pd.DataFrame(rows).sort_values("strength", ascending=False).reset_index(drop=True)
