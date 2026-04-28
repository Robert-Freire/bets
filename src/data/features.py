"""
Feature engineering: merge football-data.co.uk match results with Understat xG,
build rolling team features (form, xG averages) used by the XGBoost model.
"""

import pandas as pd
import numpy as np


# Team name mappings between football-data.co.uk and Understat
_NAME_MAP = {
    "Manchester United": "Man United",
    "Manchester City": "Man City",
    "Tottenham Hotspur": "Tottenham",
    "Newcastle United": "Newcastle",
    "West Bromwich Albion": "West Brom",
    "Queens Park Rangers": "QPR",
    "Wolverhampton Wanderers": "Wolves",
    "Sheffield United": "Sheffield United",
    "Nottingham Forest": "Nott'm Forest",
    "Brighton & Hove Albion": "Brighton",
    "Huddersfield Town": "Huddersfield",
    "Cardiff City": "Cardiff",
    "Watford": "Watford",
    "Luton Town": "Luton",
    "Brentford": "Brentford",
}


def normalize_team_name(name: str) -> str:
    return _NAME_MAP.get(name, name)


def merge_xg(matches: pd.DataFrame, xg: pd.DataFrame) -> pd.DataFrame:
    """
    Merge Understat xG onto football-data.co.uk matches by date + teams.
    Normalizes team names and joins on date (within ±1 day) + teams.
    """
    xg = xg.copy()
    xg["home_team_norm"] = xg["home_team"].map(normalize_team_name)
    xg["away_team_norm"] = xg["away_team"].map(normalize_team_name)
    xg["date_only"] = xg["date"].dt.date

    matches = matches.copy()
    matches["date_only"] = matches["Date"].dt.date

    merged = matches.merge(
        xg[["date_only", "home_team_norm", "away_team_norm", "home_xg", "away_xg"]],
        left_on=["date_only", "HomeTeam", "AwayTeam"],
        right_on=["date_only", "home_team_norm", "away_team_norm"],
        how="left",
    )

    xg_hit = merged["home_xg"].notna().sum()
    total = len(merged)
    print(f"  xG merge: {xg_hit}/{total} matches matched ({xg_hit/total:.1%})")
    return merged.drop(columns=["date_only", "home_team_norm", "away_team_norm"], errors="ignore")


def rolling_team_stats(matches: pd.DataFrame, windows: list[int] = [5, 10]) -> pd.DataFrame:
    """
    Build rolling xG and goals features per team (walk-forward, no leakage).

    For each match, compute rolling averages of the PREVIOUS N matches for both teams.
    Returns matches DataFrame with new feature columns added.
    """
    matches = matches.sort_values("Date").reset_index(drop=True)

    # Track running stats per team
    team_history: dict[str, list[dict]] = {}

    home_features_list = []
    away_features_list = []

    for _, row in matches.iterrows():
        ht = row["HomeTeam"]
        at = row["AwayTeam"]

        home_feats = _team_rolling_features(team_history.get(ht, []), windows, prefix="home")
        away_feats = _team_rolling_features(team_history.get(at, []), windows, prefix="away")

        home_features_list.append(home_feats)
        away_features_list.append(away_feats)

        # Update history after recording pre-match features
        home_entry = {
            "goals_for": row.get("FTHG", np.nan),
            "goals_against": row.get("FTAG", np.nan),
            "xg_for": row.get("home_xg", np.nan),
            "xg_against": row.get("away_xg", np.nan),
            "result": 1 if row["FTR"] == "H" else (0 if row["FTR"] == "D" else -1),
            "is_home": 1,
        }
        away_entry = {
            "goals_for": row.get("FTAG", np.nan),
            "goals_against": row.get("FTHG", np.nan),
            "xg_for": row.get("away_xg", np.nan),
            "xg_against": row.get("home_xg", np.nan),
            "result": 1 if row["FTR"] == "A" else (0 if row["FTR"] == "D" else -1),
            "is_home": 0,
        }
        team_history.setdefault(ht, []).append(home_entry)
        team_history.setdefault(at, []).append(away_entry)

    home_df = pd.DataFrame(home_features_list)
    away_df = pd.DataFrame(away_features_list)

    for col in home_df.columns:
        matches[col] = home_df[col].values
    for col in away_df.columns:
        matches[col] = away_df[col].values

    return matches


def _team_rolling_features(history: list[dict], windows: list[int], prefix: str) -> dict:
    feats = {}
    if not history:
        for w in windows:
            feats.update({
                f"{prefix}_gf_{w}": np.nan,
                f"{prefix}_ga_{w}": np.nan,
                f"{prefix}_xgf_{w}": np.nan,
                f"{prefix}_xga_{w}": np.nan,
                f"{prefix}_pts_{w}": np.nan,
            })
        feats[f"{prefix}_n_matches"] = 0
        return feats

    feats[f"{prefix}_n_matches"] = len(history)
    for w in windows:
        recent = history[-w:]
        feats[f"{prefix}_gf_{w}"] = np.nanmean([h["goals_for"] for h in recent])
        feats[f"{prefix}_ga_{w}"] = np.nanmean([h["goals_against"] for h in recent])
        feats[f"{prefix}_xgf_{w}"] = np.nanmean([h["xg_for"] for h in recent])
        feats[f"{prefix}_xga_{w}"] = np.nanmean([h["xg_against"] for h in recent])
        # Points per game: win=3, draw=1, loss=0
        pts = [3 if h["result"] == 1 else (1 if h["result"] == 0 else 0) for h in recent]
        feats[f"{prefix}_pts_{w}"] = np.mean(pts)
    return feats


def build_feature_matrix(matches: pd.DataFrame, xg: pd.DataFrame | None = None) -> pd.DataFrame:
    """
    Full feature engineering pipeline.
    Returns matches with all model features and target variable.
    """
    if xg is not None:
        matches = merge_xg(matches, xg)

    matches = rolling_team_stats(matches)

    # Differential features (home minus away) — research shows these outperform raw values
    stat_pairs = [
        ("home_gf_5", "away_gf_5", "diff_gf_5"),
        ("home_ga_5", "away_ga_5", "diff_ga_5"),
        ("home_xgf_5", "away_xgf_5", "diff_xgf_5"),
        ("home_xga_5", "away_xga_5", "diff_xga_5"),
        ("home_pts_5", "away_pts_5", "diff_pts_5"),
        ("home_gf_10", "away_gf_10", "diff_gf_10"),
        ("home_xgf_10", "away_xgf_10", "diff_xgf_10"),
        ("home_pts_10", "away_pts_10", "diff_pts_10"),
    ]
    for home_col, away_col, diff_col in stat_pairs:
        if home_col in matches.columns and away_col in matches.columns:
            matches[diff_col] = matches[home_col] - matches[away_col]

    # Target: 0=home win, 1=draw, 2=away win
    matches["outcome"] = matches["FTR"].map({"H": 0, "D": 1, "A": 2})

    return matches


FEATURE_COLS = [
    # Pi-rating features (added by build_rolling_ratings)
    "home_rating_h", "home_rating_a", "away_rating_h", "away_rating_a",
    "rating_diff_home", "expected_goal_diff",
    # Rolling goals
    "home_gf_5", "home_ga_5", "away_gf_5", "away_ga_5",
    "home_gf_10", "home_ga_10", "away_gf_10", "away_ga_10",
    # Rolling xG
    "home_xgf_5", "home_xga_5", "away_xgf_5", "away_xga_5",
    "home_xgf_10", "home_xga_10", "away_xgf_10", "away_xga_10",
    # Points form
    "home_pts_5", "away_pts_5", "home_pts_10", "away_pts_10",
    # Differentials
    "diff_gf_5", "diff_ga_5", "diff_xgf_5", "diff_xga_5", "diff_pts_5",
    "diff_gf_10", "diff_xgf_10", "diff_pts_10",
    # Match count (proxy for data quality / team age in league)
    "home_n_matches", "away_n_matches",
]
