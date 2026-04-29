"""
Betting dashboard — view suggested bets, log actual stakes and results.
Run with: python3 app.py
Then open: http://localhost:5000
"""

import csv
import fcntl
import os
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, jsonify

app = Flask(__name__)

BETS_CSV    = Path(__file__).parent / "logs" / "bets.csv"
DRIFT_CSV   = Path(__file__).parent / "logs" / "drift.csv"

FIELDS = [
    "scanned_at", "sport", "market", "line", "home", "away", "kickoff",
    "side", "book", "odds", "edge", "consensus", "n_books",
    "confidence", "model_signal", "stake", "result", "actual_stake", "pnl",
    "pinnacle_close_prob", "clv_pct",
]


def load_bets() -> list[dict]:
    if not BETS_CSV.exists():
        return []
    with open(BETS_CSV, newline="") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        reader = csv.DictReader(f)
        bets = []
        for i, row in enumerate(reader):
            row["id"] = i
            row.setdefault("market", "h2h")
            row.setdefault("line", "")
            row.setdefault("actual_stake", "")
            row.setdefault("pnl", "")
            row.setdefault("model_signal", "?")
            row.setdefault("pinnacle_close_prob", "")
            row.setdefault("clv_pct", "")
            bets.append(row)
    return bets


def save_bets(bets: list[dict]):
    all_keys = set()
    for b in bets:
        all_keys.update(b.keys())
    # Preserve column order, drop internal id
    fieldnames = [f for f in FIELDS if f in all_keys]
    for f in all_keys:
        if f not in fieldnames and f != "id":
            fieldnames.append(f)

    tmp = BETS_CSV.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(bets)
    os.replace(tmp, BETS_CSV)


def calc_pnl(result: str, actual_stake: str, odds: str) -> str:
    if not result or not actual_stake:
        return ""
    try:
        stake = float(actual_stake)
        o = float(odds)
        if result == "W":
            return str(round(stake * (o - 1), 2))
        elif result == "L":
            return str(round(-stake, 2))
        elif result == "V":
            return "0.0"
    except (ValueError, TypeError):
        pass
    return ""


def load_drift() -> dict[tuple, list[dict]]:
    """Load drift.csv keyed by (home, away, kickoff, side) → sorted drift rows."""
    if not DRIFT_CSV.exists():
        return {}
    by_bet: dict[tuple, list[dict]] = {}
    with open(DRIFT_CSV, newline="") as f:
        for row in csv.DictReader(f):
            key = (row["home"], row["away"], row["kickoff"], row["side"])
            by_bet.setdefault(key, []).append(row)
    # Sort each bet's drift rows by t_minus_min descending (T-60 first)
    for rows in by_bet.values():
        rows.sort(key=lambda r: int(r.get("t_minus_min", 0)), reverse=True)
    return by_bet


def _drift_direction(drift_rows: list[dict]) -> str | None:
    """
    'toward' if Pinnacle odds shortened T-60→T-1 (market agrees with your bet),
    'away'   if they lengthened,
    None     if insufficient data.
    """
    rows_with_pin = [r for r in drift_rows if r.get("pinnacle_odds")]
    if len(rows_with_pin) < 2:
        return None
    first = float(rows_with_pin[0]["pinnacle_odds"])   # earliest (highest t_minus_min)
    last  = float(rows_with_pin[-1]["pinnacle_odds"])  # closest to KO
    if last < first:
        return "toward"   # odds shortened → market moved in your direction
    if last > first:
        return "away"
    return None


def summary_stats(bets: list[dict], drift: dict | None = None) -> dict:
    placed = [b for b in bets if b.get("actual_stake") and b.get("result")]
    if not placed:
        return {
            "n": 0, "staked": 0, "pnl": 0, "roi": 0,
            "won": 0, "lost": 0, "void": 0,
            "avg_clv": None, "clv_pos_rate": None, "bets_w_clv": 0,
            "drift_toward_pct": None,
        }

    staked = sum(float(b["actual_stake"]) for b in placed)
    pnl    = sum(float(b["pnl"]) for b in placed if b.get("pnl"))
    won    = sum(1 for b in placed if b["result"] == "W")
    lost   = sum(1 for b in placed if b["result"] == "L")
    void   = sum(1 for b in placed if b["result"] == "V")
    roi    = (pnl / staked * 100) if staked > 0 else 0

    # CLV stats (settled bets only)
    clv_vals = []
    for b in placed:
        raw = b.get("clv_pct", "")
        if raw:
            try:
                clv_vals.append(float(raw))
            except ValueError:
                pass
    avg_clv      = round(sum(clv_vals) / len(clv_vals) * 100, 2) if clv_vals else None
    clv_pos_rate = round(sum(1 for v in clv_vals if v > 0) / len(clv_vals) * 100) if clv_vals else None
    bets_w_clv   = len(clv_vals)

    # Drift stats
    drift_toward_pct = None
    if drift:
        directions = []
        for b in placed:
            key = (b["home"], b["away"], b["kickoff"], b["side"])
            rows = drift.get(key, [])
            d = _drift_direction(rows)
            if d is not None:
                directions.append(d)
        if directions:
            n_toward = sum(1 for d in directions if d == "toward")
            drift_toward_pct = round(n_toward / len(directions) * 100)

    return {
        "n": len(placed),
        "staked": round(staked, 2),
        "pnl": round(pnl, 2),
        "roi": round(roi, 2),
        "won": won,
        "lost": lost,
        "void": void,
        "avg_clv": avg_clv,
        "clv_pos_rate": clv_pos_rate,
        "bets_w_clv": bets_w_clv,
        "drift_toward_pct": drift_toward_pct,
    }


@app.route("/")
def index():
    bets = load_bets()
    drift = load_drift()
    bets_rev = list(reversed(bets))
    pending = [b for b in bets_rev if not b.get("result")]
    done = [b for b in bets_rev if b.get("result")]
    stats = summary_stats(bets, drift)

    # Attach drift direction to each settled bet for the template
    for b in done:
        key = (b["home"], b["away"], b["kickoff"], b["side"])
        b["_drift_dir"] = _drift_direction(drift.get(key, []))

    return render_template("index.html", pending=pending, done=done, stats=stats)


@app.route("/update/<int:bet_id>", methods=["POST"])
def update(bet_id: int):
    bets = load_bets()
    if bet_id >= len(bets):
        return jsonify({"error": "not found"}), 404

    result = request.form.get("result", "").strip().upper()
    actual_stake = request.form.get("actual_stake", "").strip()
    odds = request.form.get("odds", "").strip()

    bets[bet_id]["result"] = result
    bets[bet_id]["actual_stake"] = actual_stake
    if odds:
        bets[bet_id]["odds"] = odds
    bets[bet_id]["pnl"] = calc_pnl(result, actual_stake, bets[bet_id].get("odds", ""))

    save_bets(bets)
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=5000)
