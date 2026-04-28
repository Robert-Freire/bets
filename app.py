"""
Betting dashboard — view suggested bets, log actual stakes and results.
Run with: python3 app.py
Then open: http://localhost:5000
"""

import csv
import os
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, jsonify

app = Flask(__name__)

BETS_CSV = Path(__file__).parent / "logs" / "bets.csv"

FIELDS = [
    "scanned_at", "sport", "home", "away", "kickoff",
    "side", "book", "odds", "edge", "consensus", "n_books",
    "confidence", "stake", "result", "actual_stake", "pnl",
]


def load_bets() -> list[dict]:
    if not BETS_CSV.exists():
        return []
    with open(BETS_CSV, newline="") as f:
        reader = csv.DictReader(f)
        bets = []
        for i, row in enumerate(reader):
            row["id"] = i
            row.setdefault("actual_stake", "")
            row.setdefault("pnl", "")
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

    with open(BETS_CSV, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(bets)


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


def summary_stats(bets: list[dict]) -> dict:
    placed = [b for b in bets if b.get("actual_stake") and b.get("result")]
    if not placed:
        return {"n": 0, "staked": 0, "pnl": 0, "roi": 0, "won": 0, "lost": 0, "void": 0}

    staked = sum(float(b["actual_stake"]) for b in placed)
    pnl = sum(float(b["pnl"]) for b in placed if b.get("pnl"))
    won = sum(1 for b in placed if b["result"] == "W")
    lost = sum(1 for b in placed if b["result"] == "L")
    void = sum(1 for b in placed if b["result"] == "V")
    roi = (pnl / staked * 100) if staked > 0 else 0

    return {
        "n": len(placed),
        "staked": round(staked, 2),
        "pnl": round(pnl, 2),
        "roi": round(roi, 2),
        "won": won,
        "lost": lost,
        "void": void,
    }


@app.route("/")
def index():
    bets = load_bets()
    bets_rev = list(reversed(bets))
    pending = [b for b in bets_rev if not b.get("result")]
    done = [b for b in bets_rev if b.get("result")]
    stats = summary_stats(bets)
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
