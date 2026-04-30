"""
Walk-forward backtest primitive built on sklearn.model_selection.TimeSeriesSplit.

No third-party walk-forward dep: the primitive is ~30 lines of fold iteration.
Bugs in an external package would silently corrupt per-fold ROI numbers used for
graduation evidence. At our scale, serial execution is seconds-to-minutes.
"""

from pathlib import Path

import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

from src.betting.consensus import BOOKMAKER_GROUPS, backtest_consensus

_RAW_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "raw"

# All individual bookmaker odds columns (H/D/A triplets) that must be numeric.
_ODDS_COLS = [col for triplet in BOOKMAKER_GROUPS.values() for col in triplet]


def load_backtest_data() -> pd.DataFrame:
    """
    Load all football-data.co.uk CSVs from data/raw/, concatenate, sort by Date.

    Handles two encoding variants: UTF-8-with-BOM (recent seasons) and Latin-1
    (older seasons with non-ASCII team names). Odds columns are coerced to float
    so that stray string values in source data (e.g. a bookmaker name accidentally
    in an odds cell) become NaN and are silently skipped by compute_consensus().

    Returns a single time-ordered DataFrame in the shape backtest_consensus() expects.
    """
    frames = []
    for csv_path in sorted(_RAW_DIR.glob("*.csv")):
        try:
            df = pd.read_csv(csv_path, low_memory=False, encoding="utf-8-sig")
        except UnicodeDecodeError:
            try:
                df = pd.read_csv(csv_path, low_memory=False, encoding="latin1")
            except Exception:
                continue
        except Exception:
            continue
        if "Date" not in df.columns:
            continue
        frames.append(df)

    if not frames:
        raise FileNotFoundError(f"No CSV files with a Date column found in {_RAW_DIR}")

    combined = pd.concat(frames, ignore_index=True)
    combined["Date"] = pd.to_datetime(combined["Date"], format="mixed", dayfirst=True, errors="coerce")
    combined = combined.dropna(subset=["Date"])

    # Coerce odds columns to float — source CSVs occasionally contain stray strings.
    for col in _ODDS_COLS:
        if col in combined.columns:
            combined[col] = pd.to_numeric(combined[col], errors="coerce")

    combined = combined.sort_values("Date").reset_index(drop=True)
    return combined


def walk_forward_backtest(
    matches: pd.DataFrame,
    *,
    consensus_method: str,
    min_edge: float,
    n_splits: int = 5,
    **kwargs,
) -> pd.DataFrame:
    """
    Walk-forward backtest using TimeSeriesSplit.

    Splits matches temporally into n_splits folds. For each fold the test slice is
    passed to backtest_consensus(); the train slice is intentionally ignored —
    consensus betting has no training step, so train_idx is unused by design.

    Parameters
    ----------
    matches          : time-ordered DataFrame from load_backtest_data()
    consensus_method : forwarded to backtest_consensus()
    min_edge         : forwarded to backtest_consensus()
    n_splits         : number of TimeSeriesSplit folds (default 5)
    **kwargs         : forwarded to backtest_consensus() (bankroll, kelly_multiplier, min_books)

    Returns
    -------
    DataFrame with one row per fold and columns:
        fold_idx, n_bets, n_won, total_staked, total_pnl, roi, start_date, end_date
    """
    # TimeSeriesSplit reserves the first n/(n_splits+1) rows as train-only; they never
    # appear in any test fold. That is standard TSS semantics, not a loader bug.
    tscv = TimeSeriesSplit(n_splits=n_splits)
    rows = []

    for fold_idx, (train_idx, test_idx) in enumerate(tscv.split(matches)):
        # train_idx is intentionally unused — consensus betting has no training step.
        fold = matches.iloc[test_idx]

        result = backtest_consensus(
            fold,
            min_edge=min_edge,
            consensus_method=consensus_method,
            **kwargs,
        )

        dates = pd.to_datetime(fold["Date"], errors="coerce").dropna()
        start_date = dates.min() if not dates.empty else pd.NaT
        end_date = dates.max() if not dates.empty else pd.NaT

        total_staked = result.get("total_staked", 0.0)
        total_pnl = result.get("total_pnl", 0.0)
        # ROI = total_pnl / total_staked from raw counters (not mean of per-bet ROIs —
        # variable Kelly stakes make the latter incorrect).
        roi = total_pnl / total_staked if total_staked > 0 else 0.0

        rows.append({
            "fold_idx": fold_idx,
            "n_bets": result.get("n_bets", 0),
            "n_won": result.get("n_won", 0),
            "total_staked": total_staked,
            "total_pnl": total_pnl,
            "roi": roi,
            "start_date": start_date,
            "end_date": end_date,
        })

    return pd.DataFrame(rows)
