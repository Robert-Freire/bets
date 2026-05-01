"""
Phase 7.4: Diff model_signals.json vs model_signals_calibrated.json.
Prints per-league summary of probability shifts.

Usage:
    python3 scripts/diff_model_signals.py
"""
import sys
import json
from pathlib import Path
from collections import defaultdict

import numpy as np

ROOT = Path(__file__).parent.parent
BASE = ROOT / "logs" / "model_signals.json"
CALIBRATED = ROOT / "logs" / "model_signals_calibrated.json"


def load_signals(path: Path) -> dict:
    with open(path) as f:
        return json.load(f).get("signals", {})


def sport_key_of(key: str) -> str:
    return key.split(":")[0] if ":" in key else "soccer_epl"


def main():
    if not BASE.exists():
        print(f"ERROR: {BASE} not found")
        sys.exit(1)
    if not CALIBRATED.exists():
        print(f"ERROR: {CALIBRATED} not found. Run: python3 scripts/model_signals.py --calibrate")
        sys.exit(1)

    base = load_signals(BASE)
    cal = load_signals(CALIBRATED)

    matched_keys = set(base) & set(cal)
    print(f"Base:       {len(base)} signals")
    print(f"Calibrated: {len(cal)} signals")
    print(f"Matched:    {len(matched_keys)} keys in both\n")

    # Per-league stats
    league_shifts: dict[str, list[float]] = defaultdict(list)
    league_top: dict[str, list[tuple]] = defaultdict(list)

    for key in matched_keys:
        b, c = base[key], cal[key]
        total_shift = sum(abs(c[o] - b[o]) for o in ["H", "D", "A"])
        league = sport_key_of(key)
        league_shifts[league].append(total_shift)
        league_top[league].append((total_shift, key, b, c))

    print("=== Per-league summary ===\n")
    for league in sorted(league_shifts):
        shifts = league_shifts[league]
        print(f"{league}")
        print(f"  n_signals: {len(shifts)}")
        print(f"  mean abs shift: {np.mean(shifts):.4f}")
        print(f"  median:         {np.median(shifts):.4f}")
        print(f"  max:            {np.max(shifts):.4f}")

        # Histogram
        bins = [0.0, 0.01, 0.03, 0.05, 0.10, 0.20, 1.0]
        labels = ["<1%", "1-3%", "3-5%", "5-10%", "10-20%", ">20%"]
        hist = np.histogram(shifts, bins=bins)[0]
        hist_str = "  shift dist: " + "  ".join(f"{l}:{n}" for l, n in zip(labels, hist))
        print(hist_str)
        print()

    print("=== Top 10 fixtures by total absolute prob shift ===\n")
    all_top = []
    for league in league_top:
        all_top.extend(league_top[league])
    all_top.sort(reverse=True)
    for shift, key, b, c in all_top[:10]:
        parts = key.split(":")
        fixture = parts[1] if len(parts) > 1 else key
        home, away = fixture.split("|") if "|" in fixture else (fixture, "?")
        print(f"  {home:25s} vs {away:25s}  shift={shift:.4f}")
        print(f"    base:  H={b['H']:.4f}  D={b['D']:.4f}  A={b['A']:.4f}")
        print(f"    cal:   H={c['H']:.4f}  D={c['D']:.4f}  A={c['A']:.4f}")
        print()


if __name__ == "__main__":
    main()
