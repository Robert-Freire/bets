"""
Betting dashboard — view suggested bets, log actual stakes and results.
Run with: python3 app.py
Then open: http://localhost:5000

A.9: data flow is DB-only via BetRepo (BETS_DB_WRITE=1 + DSN env required).
A banner appears when DB is configured but unreachable.
"""

import base64
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, jsonify

# Allow `from src...` imports regardless of CWD.
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.storage.repo import BetRepo
from src.storage._keys import scan_date_of

app = Flask(__name__)

# A.7: defense-in-depth allowlist on top of Container Apps Easy Auth.
# Empty result => not enforced (local dev). Read on each request so test
# isolation isn't broken by module-level caching.
def _allowed_emails() -> set[str]:
    return {
        e.strip().lower()
        for e in os.environ.get("DASHBOARD_ALLOWED_EMAILS", "").split(",")
        if e.strip()
    }


def _principal_email() -> str | None:
    """Email of the signed-in user from Easy Auth headers, or None.

    Container Apps injects X-MS-CLIENT-PRINCIPAL-NAME (Google → email)
    and X-MS-CLIENT-PRINCIPAL (b64 JSON with a `claims` array). We try
    the convenience header first, then fall back to the principal blob.
    """
    name = request.headers.get("X-MS-CLIENT-PRINCIPAL-NAME")
    if name and "@" in name:
        return name.lower()
    raw = request.headers.get("X-MS-CLIENT-PRINCIPAL")
    if not raw:
        return None
    try:
        principal = json.loads(base64.b64decode(raw).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    for c in principal.get("claims", []):
        if c.get("typ") in ("emails", "email", "preferred_username", "name"):
            v = c.get("val", "")
            if "@" in v:
                return v.lower()
    return None


@app.before_request
def _allowlist_check():
    allowed = _allowed_emails()
    if not allowed:
        return None
    if request.path == "/health":
        return None
    email = _principal_email()
    if email is None:
        return jsonify({"error": "auth required"}), 401
    if email not in allowed:
        return jsonify({"error": "forbidden"}), 403
    return None


RESEARCH_FEED_MD   = Path(__file__).parent / "docs" / "RESEARCH_FEED.md"


def _repo() -> BetRepo:
    """Construct a per-request BetRepo. Cheap when DB disabled (no
    network); when enabled, the connection is only opened on first DB
    method call."""
    return BetRepo()

_RUN_RE = re.compile(
    r"^## Run (\d{4}-\d{2}-\d{2})(?:\s+\d{2}:\d{2}\s+UTC)?\s+\(mode:\s*(\w+)\)\s+—\s*(\d+)\s+findings",
    re.MULTILINE,
)


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
    row.setdefault("devig_method", "shin")
    row.setdefault("weight_scheme", "uniform")


def load_bets(repo: BetRepo | None = None) -> list[dict]:
    """Return all bets as dicts from DB, ordered by scanned_at ascending.

    Returns an empty list when DB is unavailable.
    """
    db_rows = repo.get_bets() if repo is not None else None
    bets = []
    if db_rows is not None:
        for row in db_rows:
            _normalise_row(row, row.get("_source", "db"))
        bets.extend(db_rows)
    for i, row in enumerate(bets):
        row["id"] = i
    return bets


def calc_pnl(result: str, actual_stake: str, odds: str, commission_rate: str = "0") -> str:
    if not result or not actual_stake:
        return ""
    try:
        stake = float(actual_stake)
        o = float(odds)
        comm = float(commission_rate or 0)
        if result == "W":
            gross = stake * (o - 1)
            return str(round(gross * (1 - comm), 2))
        elif result == "L":
            return str(round(-stake, 2))
        elif result == "V":
            return "0.0"
    except (ValueError, TypeError):
        pass
    return ""


def load_drift(repo: BetRepo | None = None) -> dict[tuple, list[dict]]:
    """Load drift snapshots keyed by (home, away, kickoff, side, market, line) from DB."""
    if repo is not None:
        db = repo.get_drift()
        if db is not None:
            return db
    return {}


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
            "clv_breakdown": None,
            "drift_toward_pct": None,
        }

    staked = sum(float(b["actual_stake"]) for b in placed)
    pnl    = sum(float(b["pnl"]) for b in placed if b.get("pnl"))
    won    = sum(1 for b in placed if b["result"] == "W")
    lost   = sum(1 for b in placed if b["result"] == "L")
    void   = sum(1 for b in placed if b["result"] == "V")
    roi    = (pnl / staked * 100) if staked > 0 else 0

    clv_vals = []
    clv_by_method: dict = {}
    for b in placed:
        raw = b.get("clv_pct", "")
        if raw:
            try:
                v = float(raw)
                clv_vals.append(v)
                method = b.get("devig_method") or "shin"
                clv_by_method.setdefault(method, []).append(v)
            except ValueError:
                pass
    avg_clv      = round(sum(clv_vals) / len(clv_vals) * 100, 2) if clv_vals else None
    clv_pos_rate = round(sum(1 for v in clv_vals if v > 0) / len(clv_vals) * 100) if clv_vals else None
    bets_w_clv   = len(clv_vals)
    clv_breakdown = (
        {m: round(sum(v) / len(v) * 100, 2) for m, v in clv_by_method.items()}
        if len(clv_by_method) >= 2 and all(len(v) >= 20 for v in clv_by_method.values())
        else None
    )

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
        "clv_breakdown": clv_breakdown,
        "drift_toward_pct": drift_toward_pct,
    }


@app.route("/")
def index():
    repo = _repo()
    db_status = repo.db_status()
    bets = load_bets(repo)
    drift = load_drift(repo)
    bets_rev = list(reversed(bets))
    pending = [b for b in bets_rev if not b.get("result")]
    done = [b for b in bets_rev if b.get("result")]
    stats = summary_stats(bets, drift)

    for b in done:
        key = (b["home"], b["away"], b["kickoff"], b["side"],
               b.get("market", "h2h"), b.get("line", ""))
        b["_drift_dir"] = _drift_direction(drift.get(key, []))

    research = latest_research_findings()
    repo.close()
    return render_template(
        "index.html",
        pending=pending, done=done, stats=stats, research=research,
        db_status=db_status,
    )


@app.route("/health")
def health():
    """Lightweight liveness probe.

    `db` is one of 'ok' (configured + reachable), 'down' (configured but
    connect failed), or 'disabled' (env not set). 503 when db=down.
    """
    repo = _repo()
    db = repo.db_status()
    repo.close()
    code = 200 if db != "down" else 503
    return jsonify({"db": db}), code


@app.route("/update/<int:bet_id>", methods=["POST"])
def update(bet_id: int):
    repo = _repo()
    bets = load_bets(repo)
    if bet_id >= len(bets):
        repo.close()
        return jsonify({"error": "not found"}), 404

    b = bets[bet_id]
    result = request.form.get("result", "").strip().upper()
    actual_stake = request.form.get("actual_stake", "").strip()
    odds = request.form.get("odds", "").strip()
    pnl = calc_pnl(result, actual_stake, odds or b.get("odds", ""),
                   b.get("commission_rate", "0"))

    if repo.db_enabled:
        rows_updated = repo.update_bet_settle(
            scan_date=scan_date_of(b.get("scanned_at", "")),
            kickoff=b.get("kickoff", ""),
            home=b.get("home", ""),
            away=b.get("away", ""),
            market=b.get("market") or "h2h",
            line=b.get("line", ""),
            side=b.get("side", ""),
            book=b.get("book", ""),
            result=result or "pending",
            actual_stake=actual_stake or None,
            pnl=pnl or None,
            odds=odds or None,
        )
        if rows_updated == 0:
            sys.stderr.write(
                f"[dashboard] WARN: DB update wrote 0 rows for bet {bet_id} "
                f"({b.get('home')} vs {b.get('away')} {b.get('kickoff')} "
                f"{b.get('side')} {b.get('book')})\n"
            )
    repo.close()
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=5000)
