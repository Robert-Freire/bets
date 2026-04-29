"""
CatBoost W/D/L classifier on top of pi-ratings + rolling xG features.
Based on Yeung et al. (2023) and Hubáček et al. (2019).

Uses CatBoost (handles NaN natively). Falls back to XGBoost if CatBoost is not installed.
"""

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import cross_val_score

try:
    from catboost import CatBoostClassifier
    CATBOOST_AVAILABLE = True
except ImportError:
    CATBOOST_AVAILABLE = False

try:
    from xgboost import XGBClassifier
    XGBOOST_AVAILABLE = True
except ImportError:
    XGBOOST_AVAILABLE = False

from src.data.features import FEATURE_COLS


class MatchPredictor:
    """
    Gradient-boosted tree classifier for match outcome prediction.

    Outputs calibrated P(home win), P(draw), P(away win).
    """

    def __init__(self, backend: str = "auto", calibrate: bool = True):
        """
        Parameters
        ----------
        backend : 'catboost', 'xgboost', or 'auto' (prefers catboost)
        calibrate : apply isotonic calibration on top of the classifier
        """
        self.backend = backend
        self.calibrate = calibrate
        self.model_ = None
        self.feature_cols_ = None

    def _make_model(self):
        if self.backend == "auto":
            backend = "catboost" if CATBOOST_AVAILABLE else "xgboost"
        else:
            backend = self.backend

        if backend == "catboost":
            if not CATBOOST_AVAILABLE:
                raise ImportError("catboost not installed. Run: pip install catboost")
            return CatBoostClassifier(
                iterations=500,
                learning_rate=0.05,
                depth=6,
                loss_function="MultiClass",
                eval_metric="MultiClass",
                random_seed=42,
                verbose=False,
                allow_writing_files=False,
            )
        elif backend == "xgboost":
            if not XGBOOST_AVAILABLE:
                raise ImportError("xgboost not installed. Run: pip install xgboost")
            return XGBClassifier(
                n_estimators=500,
                learning_rate=0.05,
                max_depth=6,
                objective="multi:softprob",
                num_class=3,
                use_label_encoder=False,
                eval_metric="mlogloss",
                random_state=42,
                verbosity=0,
            )
        else:
            raise ValueError(f"Unknown backend: {backend}")

    def fit(self, matches: pd.DataFrame, feature_cols: list[str] | None = None) -> "MatchPredictor":
        """
        Fit on a DataFrame that includes features and 'outcome' column (0/1/2).

        Parameters
        ----------
        matches : DataFrame with feature columns and 'outcome' target
        feature_cols : which columns to use as features (defaults to FEATURE_COLS)
        """
        if feature_cols is None:
            feature_cols = [c for c in FEATURE_COLS if c in matches.columns]
        self.feature_cols_ = feature_cols

        # Drop rows where target or too many features are missing
        valid = matches["outcome"].notna()
        train = matches[valid].copy()

        # Need at least some history — skip first N matches per team
        min_matches = 3
        has_history = (
            (train["home_n_matches"] >= min_matches) &
            (train["away_n_matches"] >= min_matches)
        ) if "home_n_matches" in train.columns else pd.Series(True, index=train.index)
        train = train[has_history]

        X = train[feature_cols].values.astype(float)
        y = train["outcome"].values.astype(int)

        model = self._make_model()

        if self.calibrate and not CATBOOST_AVAILABLE:
            # CatBoost has built-in calibration; for XGBoost wrap with isotonic
            self.model_ = CalibratedClassifierCV(model, cv=3, method="isotonic")
        else:
            self.model_ = model

        self.model_.fit(X, y)
        return self

    def predict_proba(self, matches: pd.DataFrame) -> pd.DataFrame:
        """
        Return DataFrame with columns [home_win, draw, away_win].
        """
        if self.model_ is None:
            raise RuntimeError("Model not fitted. Call fit() first.")

        X = matches[self.feature_cols_].values.astype(float)
        proba = self.model_.predict_proba(X)

        return pd.DataFrame(
            proba,
            columns=["home_win", "draw", "away_win"],
            index=matches.index,
        )

    def feature_importance(self) -> pd.DataFrame:
        """Return feature importances if available."""
        if self.model_ is None:
            raise RuntimeError("Model not fitted.")

        model = self.model_
        # Unwrap CalibratedClassifierCV
        if hasattr(model, "estimator"):
            model = model.estimator

        if hasattr(model, "feature_importances_"):
            imp = model.feature_importances_
        elif hasattr(model, "get_feature_importance"):
            imp = model.get_feature_importance()
        else:
            return pd.DataFrame()

        return (
            pd.DataFrame({"feature": self.feature_cols_, "importance": imp})
            .sort_values("importance", ascending=False)
            .reset_index(drop=True)
        )
