"""
Backfill devig_method and weight_scheme into existing bets.csv and paper strategy CSVs.

Default mode: fill empty values only — safe to re-run (idempotent).
--migrate:    one-shot re-attribution. For paper CSVs whose strategy stem is in
              _STRATEGY_PROVENANCE, overwrite rows whose current values disagree
              with the canonical mapping (recovers from the original R.7 backfill,
              which wrote uniform/shin to every row before the enum was extended).
"""
import argparse
import csv
import fcntl
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BETS_CSV  = ROOT / "logs" / "bets.csv"
PAPER_DIR = ROOT / "logs" / "paper"

# Per-strategy provenance derived from StrategyConfig.devig + sharpness_weights.
# Unlisted strategies default to shin/uniform.
_STRATEGY_PROVENANCE: dict[str, tuple[str, str]] = {
    "B_strict":         ("shin",         "pinnacle_weighted"),
    "D_pinnacle_only":  ("shin",         "pinnacle_only"),
    "G_proportional":   ("proportional", "uniform"),
    "I_power_devig":    ("power",        "uniform"),
    "J_sharp_weighted": ("shin",         "sharp_v1"),
    "O_kaunitz_classic": ("raw",         "uniform"),
}


def _backfill(path: Path, devig_method: str, weight_scheme: str) -> int:
    """Add missing devig_method/weight_scheme to rows. Returns count of rows updated."""
    if not path.exists():
        return 0

    with open(path, newline="") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    needs_write = False
    for col in ("devig_method", "weight_scheme"):
        if col not in fieldnames:
            for anchor in ("stake", "result"):
                if anchor in fieldnames:
                    fieldnames.insert(fieldnames.index(anchor), col)
                    break
            else:
                fieldnames.append(col)
            needs_write = True

    updated = 0
    for row in rows:
        row_changed = False
        if not row.get("devig_method"):
            row["devig_method"] = devig_method
            row_changed = True
        if not row.get("weight_scheme"):
            row["weight_scheme"] = weight_scheme
            row_changed = True
        if row_changed:
            updated += 1
            needs_write = True

    if not needs_write:
        return 0

    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)
    return updated


def _migrate(path: Path, devig_method: str, weight_scheme: str) -> int:
    """Overwrite mismatched provenance for known strategies. Returns rows changed.

    Used once to correct the original R.7 backfill, which wrote uniform/shin to
    every row before the weight_scheme enum was extended with pinnacle_weighted
    and pinnacle_only. Only touches rows whose value disagrees with the target.
    """
    if not path.exists():
        return 0

    with open(path, newline="") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    if "devig_method" not in fieldnames or "weight_scheme" not in fieldnames:
        return 0  # caller should run plain backfill first

    updated = 0
    for row in rows:
        row_changed = False
        if row.get("devig_method") != devig_method:
            row["devig_method"] = devig_method
            row_changed = True
        if row.get("weight_scheme") != weight_scheme:
            row["weight_scheme"] = weight_scheme
            row_changed = True
        if row_changed:
            updated += 1

    if not updated:
        return 0

    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)
    return updated


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--migrate", action="store_true",
        help="One-shot re-attribution: overwrite mismatched provenance for known strategies.",
    )
    args = parser.parse_args(argv)
    total = 0

    if args.migrate:
        # Migrate-mode only touches paper CSVs whose stem is in the canonical map.
        # bets.csv is shin/uniform always (production scanner only uses Shin), so skip.
        if PAPER_DIR.exists():
            for csv_path in sorted(PAPER_DIR.glob("*.csv")):
                if csv_path.stem not in _STRATEGY_PROVENANCE:
                    print(f"[paper/{csv_path.name}] skipped (not in provenance map)")
                    continue
                devig, weight = _STRATEGY_PROVENANCE[csv_path.stem]
                n = _migrate(csv_path, devig, weight)
                print(f"[paper/{csv_path.name}] {'migrated ' + str(n) + ' row(s) → (' + devig + ',' + weight + ')' if n else 'already correct'}")
                total += n
        print(f"\nMigration done — {total} row(s) corrected.")
        return

    n = _backfill(BETS_CSV, "shin", "uniform")
    print(f"[bets.csv] {'backfilled ' + str(n) + ' row(s)' if n else 'already up to date'}")
    total += n

    if PAPER_DIR.exists():
        for csv_path in sorted(PAPER_DIR.glob("*.csv")):
            devig, weight = _STRATEGY_PROVENANCE.get(csv_path.stem, ("shin", "uniform"))
            n = _backfill(csv_path, devig, weight)
            print(f"[paper/{csv_path.name}] {'backfilled ' + str(n) + ' row(s)' if n else 'already up to date'}")
            total += n

    print(f"\nDone — {total} row(s) updated across all CSVs.")


if __name__ == "__main__":
    main()
