"""
Phase 7.1 + 7.3: Walk-forward hold-out evaluation for all leagues.
Produces uncalibrated and calibrated leaderboards plus reliability curves.

Usage:
    python3 scripts/eval_model_holdout.py
"""
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np

from src.data.downloader import LEAGUES
from src.data.loader import load_league
from src.data.features import build_feature_matrix
from src.data.understat import UNDERSTAT_LEAGUES, load_xg
from src.ratings.pi_ratings import build_rolling_ratings
from src.model.catboost_model import MatchPredictor
from src.model.calibration import evaluate
from src.model.holdout import rolling_holdout_eval
from src.model.reliability import reliability_curve, brier_score, calibration_verdict

OUT_DIR = ROOT / "logs" / "model_eval"
MIN_MATCHES_FOR_EDGE = 100


def run_league_calibrated(sport_key: str) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """
    Run calibrated holdout for one league and compute reliability curves.
    Returns (holdout_df, reliability_curves_by_class).
    """
    try:
        matches = load_league(sport_key, since="1415")
    except RuntimeError:
        return pd.DataFrame(), {}

    completed = matches.dropna(subset=["FTHG", "FTAG", "FTR"]).copy()
    if len(completed) < 200:
        return pd.DataFrame(), {}

    completed = build_rolling_ratings(completed)

    xg = None
    understat_key = UNDERSTAT_LEAGUES.get(sport_key)
    if understat_key:
        try:
            xg = load_xg(league=understat_key, since_season=2014)
        except FileNotFoundError:
            pass

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        completed = build_feature_matrix(completed, xg)

    seasons = sorted(completed["season"].unique())
    TRAIN_WINDOW = 3
    rows = []
    all_probs = []
    all_outcomes = []

    for i, test_season in enumerate(seasons):
        if i < TRAIN_WINDOW:
            continue
        train_seasons = seasons[i - TRAIN_WINDOW:i]
        train = completed[completed["season"].isin(train_seasons)]
        test = completed[completed["season"] == test_season]

        if len(test) < 10:
            continue

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                model = MatchPredictor(backend="catboost", calibrate=True)
                model.fit(train)
                model_probs = model.predict_proba(test)
        except Exception as e:
            print(f"  {sport_key}/{test_season}: calibrated training failed — {e}")
            continue

        outcomes = test["FTR"].map({"H": 0, "D": 1, "A": 2}).dropna()
        probs_aligned = model_probs.loc[outcomes.index]

        result = evaluate(model_probs, test)
        bs = brier_score(probs_aligned, outcomes)
        rows.append({
            "sport_key": sport_key,
            "test_season": test_season,
            "n_matches": result["n_matches"],
            "model_rps": result["model_rps"],
            "bookmaker_rps": result["bookmaker_rps"],
            "rps_gap": result["rps_vs_bookmaker"],
            "model_accuracy": result["model_accuracy"],
            "has_edge": result["has_edge"],
            "brier": round(bs, 5),
        })

        all_probs.append(probs_aligned)
        all_outcomes.append(outcomes)

    holdout_df = pd.DataFrame(rows)

    reliability_curves = {}
    if all_probs:
        combined_probs = pd.concat(all_probs)
        combined_outcomes = pd.concat(all_outcomes)
        class_names = ["home_win", "draw", "away_win"]
        for cls_idx, cls_name in enumerate(class_names):
            binary = (combined_outcomes == cls_idx).astype(float)
            curve = reliability_curve(combined_probs[cls_name], binary)
            reliability_curves[cls_name] = curve

    return holdout_df, reliability_curves


def write_uncalibrated_summary(df: pd.DataFrame, path: Path):
    lines = ["# Uncalibrated Hold-out Leaderboard\n"]
    lines.append("| league | test_season | n_matches | model_rps | bookmaker_rps | rps_gap | has_edge |")
    lines.append("|---|---|---|---|---|---|---|")
    for _, row in df.sort_values(["sport_key", "test_season"]).iterrows():
        edge_flag = "edge" if (row["has_edge"] and row["n_matches"] >= MIN_MATCHES_FOR_EDGE) else ""
        lines.append(
            f"| {row['sport_key']} | {row['test_season']} | {row['n_matches']} "
            f"| {row['model_rps']:.5f} | {row['bookmaker_rps']:.5f} "
            f"| {row['rps_gap']:.5f} | {edge_flag} |"
        )
    path.write_text("\n".join(lines) + "\n")


def write_reliability_md(all_curves: dict, verdicts: dict, path: Path):
    lines = ["# Reliability Curves (calibrated model)\n"]
    for sport_key, curves_by_class in all_curves.items():
        lines.append(f"\n## {sport_key}\n")
        for cls_name, curve in curves_by_class.items():
            verdict = verdicts.get(f"{sport_key}:{cls_name}", "INSUFFICIENT_DATA")
            lines.append(f"### {cls_name} — {verdict}\n")
            lines.append("| bin_lo | bin_hi | mean_pred | empirical_freq | count |")
            lines.append("|---|---|---|---|---|")
            for _, r in curve.iterrows():
                if r["count"] == 0:
                    continue
                lines.append(
                    f"| {r['bin_lo']:.2f} | {r['bin_hi']:.2f} | {r['mean_pred']:.4f} "
                    f"| {r['empirical_freq']:.4f} | {int(r['count'])} |"
                )
    path.write_text("\n".join(lines) + "\n")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=== Phase 7.1: Uncalibrated hold-out leaderboard ===\n")
    uncal_frames = []
    for sport_key in LEAGUES:
        print(f"  {sport_key}...")
        try:
            df = rolling_holdout_eval(sport_key, train_window=3, since="1415", calibrate=False)
            if not df.empty:
                uncal_frames.append(df)
                edges = df[(df["has_edge"]) & (df["n_matches"] >= MIN_MATCHES_FOR_EDGE)]
                print(f"    {len(df)} seasons evaluated, {len(edges)} with edge (n≥{MIN_MATCHES_FOR_EDGE})")
        except Exception as e:
            print(f"    SKIP: {e}")

    if not uncal_frames:
        print("No data. Aborting.")
        sys.exit(1)

    uncal_df = pd.concat(uncal_frames, ignore_index=True)
    uncal_df.to_csv(OUT_DIR / "holdout_uncalibrated.csv", index=False)
    write_uncalibrated_summary(uncal_df, OUT_DIR / "holdout_uncalibrated_summary.md")
    print(f"\nWrote {OUT_DIR}/holdout_uncalibrated.csv ({len(uncal_df)} rows)")
    print(f"Wrote {OUT_DIR}/holdout_uncalibrated_summary.md")

    print("\n=== Phase 7.3: Calibrated hold-out + reliability ===\n")
    cal_frames = []
    all_curves = {}
    verdicts = {}

    for sport_key in LEAGUES:
        print(f"  {sport_key}...")
        try:
            cal_df, curves = run_league_calibrated(sport_key)
            if not cal_df.empty:
                cal_frames.append(cal_df)
                all_curves[sport_key] = curves
                edges = cal_df[(cal_df["has_edge"]) & (cal_df["n_matches"] >= MIN_MATCHES_FOR_EDGE)]
                print(f"    {len(cal_df)} seasons, {len(edges)} with edge")
                for cls_name, curve in curves.items():
                    v = calibration_verdict(curve)
                    verdicts[f"{sport_key}:{cls_name}"] = v

                # Write per-league reliability CSV
                for cls_name, curve in curves.items():
                    curve["class"] = cls_name
                    curve["sport_key"] = sport_key
                rel_csv = pd.concat(list(curves.values()), ignore_index=True)
                rel_csv.to_csv(OUT_DIR / f"reliability_{sport_key}.csv", index=False)
        except Exception as e:
            print(f"    SKIP: {e}")

    if cal_frames:
        cal_df_all = pd.concat(cal_frames, ignore_index=True)
        cal_df_all.to_csv(OUT_DIR / "holdout_calibrated.csv", index=False)
        write_reliability_md(all_curves, verdicts, OUT_DIR / "reliability.md")
        print(f"\nWrote {OUT_DIR}/holdout_calibrated.csv ({len(cal_df_all)} rows)")
        print(f"Wrote {OUT_DIR}/reliability.md")

        # Acceptance check: calibrated avg brier <= uncalibrated
        if not uncal_df.empty:
            uncal_mean_rps = uncal_df[uncal_df["n_matches"] >= 200]["rps_gap"].mean()
            cal_mean_rps = cal_df_all[cal_df_all["n_matches"] >= 200]["rps_gap"].mean()
            print(f"\nRPS gap avg (n≥200): uncalibrated={uncal_mean_rps:.5f}  calibrated={cal_mean_rps:.5f}")
            if cal_mean_rps <= uncal_mean_rps + 0.001:
                print("OK: calibration did not worsen point accuracy beyond tolerance")
            else:
                print("WARNING: calibrated RPS gap worse than uncalibrated by > 0.001")

        well_cal = [k for k, v in verdicts.items() if v == "WELL_CALIBRATED"]
        print(f"\nWell-calibrated (league×class): {well_cal if well_cal else 'none'}")
    else:
        print("No calibrated data to write.")


if __name__ == "__main__":
    main()
