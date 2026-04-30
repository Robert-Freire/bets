"""
Betting dashboard — view suggested bets, log actual stakes and results.
Run with: python3 app.py
Then open: http://localhost:5000
"""

import csv
import fcntl
import os
import re
from datetime import date
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, jsonify

app = Flask(__name__)

BETS_CSV           = Path(__file__).parent / "logs" / "bets.csv"
BETS_LEGACY_CSV    = Path(__file__).parent / "logs" / "bets_legacy.csv"
DRIFT_CSV          = Path(__file__).parent / "logs" / "drift.csv"
RESEARCH_FEED_MD   = Path(__file__).parent / "docs" / "RESEARCH_FEED.md"

_RUN_RE = re.compile(
    r"^## Run (\d{4}-\d{2}-\d{2})(?:\s+\d{2}:\d{2}\s+UTC)?\s+\(mode:\s*(\w+)\)\s+—\s*(\d+)\s+findings",
    re.MULTILINE,
)

FIELDS = [
    "scanned_at", "sport", "market", "line", "home", "away", "kickoff",
    "side", "book", "odds", "impl_raw", "impl_effective", "edge", "edge_gross",
    "effective_odds", "commission_rate", "consensus", "pinnacle_cons",
    "n_books", "confidence", "model_signal", "dispersion", "outlier_z",
    "stake", "result", "actual_stake", "pnl",
    "pinnacle_close_prob", "clv_pct",
]


def _normalise_row(row: dict, source: str) -> None:
    row["_source"] = source
    row.setdefault("market", "h2h")
    row.setdefault("line", "")
    row.setdefault("edge_gross", row.get("edge", ""))
    row.setdefault("impl_raw", row.get("impl", row.get("odds", "")))
    row.setdefault("impl_effective", row.get("impl", row.get("odds", "")))
    row.setdefault("effective_odds", row.get("odds", ""))
    row.setdefault("commission_rate", "0")
    row.setdefault("pinnacle_cons", "")
    row.setdefault("pinnacle_close_prob", "")
    row.setdefault("clv_pct", "")
    row.setdefault("actual_stake", "")
    row.setdefault("pnl", "")
    row.setdefault("model_signal", "?")
    row.setdefault("dispersion", "")
    row.setdefault("outlier_z", "")


def _read_csv_file(path: Path, source: str) -> list[dict]:
    rows = []
    with open(path, newline="") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        for row in csv.DictReader(f):
            _normalise_row(row, source)
            rows.append(row)
    return rows


def load_bets() -> list[dict]:
    bets = []
    if BETS_LEGACY_CSV.exists():
        bets.extend(_read_csv_file(BETS_LEGACY_CSV, "legacy"))
    if BETS_CSV.exists():
        bets.extend(_read_csv_file(BETS_CSV, "new"))
    for i, row in enumerate(bets):
        row["id"] = i
    return bets


def _save_to_file(path: Path, rows: list[dict]):
    if not rows:
        return
    all_keys = set()
    for b in rows:
        all_keys.update(k for k in b.keys() if not k.startswith("_") and k != "id")
    fieldnames = [f for f in FIELDS if f in all_keys]
    for f in sorted(all_keys):
        if f not in fieldnames:
            fieldnames.append(f)

    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def save_bets(bets: list[dict]):
    legacy_rows = [b for b in bets if b.get("_source") == "legacy"]
    new_rows    = [b for b in bets if b.get("_source") != "legacy"]
    if legacy_rows:
        _save_to_file(BETS_LEGACY_CSV, legacy_rows)
    if new_rows:
        _save_to_file(BETS_CSV, new_rows)


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
    """Load drift.csv keyed by (home, away, kickoff, side, market, line) → sorted drift rows."""
    if not DRIFT_CSV.exists():
        return {}
    by_bet: dict[tuple, list[dict]] = {}
    with open(DRIFT_CSV, newline="") as f:
        for row in csv.DictReader(f):
            key = (row["home"], row["away"], row["kickoff"], row["side"],
                   row.get("market", "h2h"), row.get("line", ""))
            by_bet.setdefault(key, []).append(row)
    for rows in by_bet.values():
        rows.sort(key=lambda r: _safe_t_minus(r.get("t_minus_min")), reverse=True)
    return by_bet


def _safe_t_minus(v) -> int:
    try:
        return int(v)
    except (ValueError, TypeError):
        return 0


def _drift_direction(drift_rows: list[dict]) -> str | None:
    """
    'toward' if Pinnacle odds shortened T-60→T-1 (market agrees with your bet),
    'away'   if they lengthened,
    None     if insufficient data.
    """
    rows_with_pin = [r for r in drift_rows if r.get("pinnacle_odds")]
    if len(rows_with_pin) < 2:
        return None
    first = float(rows_with_pin[0]["pinnacle_odds"])
    last  = float(rows_with_pin[-1]["pinnacle_odds"])
    if last < first:
        return "toward"
    if last > first:
        return "away"
    return None


def latest_research_findings() -> tuple:
    """Return (run_date, count, mode) from the most recent ## Run heading, or (None, 0, "")."""
    if not RESEARCH_FEED_MD.exists():
        return (None, 0, "")
    try:
        text = RESEARCH_FEED_MD.read_text()
        m = _RUN_RE.search(text)
        if not m:
            return (None, 0, "")
        return (date.fromisoformat(m.group(1)), int(m.group(3)), m.group(2))
    except Exception:
        return (None, 0, "")


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

    drift_toward_pct = None
    if drift:
        directions = []
        for b in placed:
            key = (b["home"], b["away"], b["kickoff"], b["side"],
                   b.get("market", "h2h"), b.get("line", ""))
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

    for b in done:
        key = (b["home"], b["away"], b["kickoff"], b["side"],
               b.get("market", "h2h"), b.get("line", ""))
        b["_drift_dir"] = _drift_direction(drift.get(key, []))

    research = latest_research_findings()
    return render_template("index.html", pending=pending, done=done, stats=stats, research=research)


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
