"""
Paper-portfolio strategy variants for shadow A/B testing.

Each StrategyConfig defines one variant of the value-bet scanner.
evaluate_strategy() runs a variant on already-fetched events (no extra API calls).
"""

import hashlib
import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path

_XG_FILE = Path(__file__).parent.parent.parent / "logs" / "team_xg.json"

try:
    from src.betting.devig import shin as _shin, proportional as _proportional, power as _power
    _DEVIG = True
except ImportError:
    _DEVIG = False

try:
    from src.betting.consensus import SHARPNESS_WEIGHTS
except ImportError:
    SHARPNESS_WEIGHTS: dict[str, float] = {}  # type: ignore[misc]

try:
    from src.betting.commissions import (
        commission_rate as _commission_rate,
        effective_odds as _effective_odds,
        effective_implied_prob as _effective_implied_prob,
    )
    _COMMISSIONS = True
except ImportError:
    _COMMISSIONS = False
    def _commission_rate(book: str) -> float: return 0.0  # noqa: E704
    def _effective_odds(odds: float, book: str) -> float: return odds  # noqa: E704
    def _effective_implied_prob(odds: float, book: str) -> float: return 1.0 / odds  # noqa: E704

# ── book sets (derived from config.json) ─────────────────────────────────────

from src.config import load_books as _load_books
_BOOKS = _load_books()
UK_LICENSED_BOOKS = {b["key"] for b in _BOOKS if b["license"] == "UK"}
EXCHANGE_BOOKS    = {b["key"] for b in _BOOKS if b["type"] == "exchange"}

# The Odds API team names → Understat team names (used by K_draw_bias xG lookup).
# Without this map, ~30% of EPL/Bundesliga/Serie A/Ligue 1 fixtures fail name matching
# and K silently rejects every draw bet. Audited 2026-04-30 against logs/team_xg.json.
UNDERSTAT_NAME_ALIAS: dict[str, str] = {
    # EPL
    "Tottenham Hotspur":        "Tottenham",
    "West Ham United":          "West Ham",
    "Leeds United":             "Leeds",
    # Bundesliga
    "1. FC Heidenheim":         "FC Heidenheim",
    "1. FC Köln":               "FC Cologne",
    "Borussia Monchengladbach": "Borussia M.Gladbach",
    "FC St. Pauli":             "St. Pauli",
    "FSV Mainz 05":             "Mainz 05",
    "RB Leipzig":               "RasenBallsport Leipzig",
    "SC Freiburg":              "Freiburg",
    "TSG Hoffenheim":           "Hoffenheim",
    "VfL Wolfsburg":            "Wolfsburg",
    # Serie A
    "AS Roma":                  "Roma",
    "Atalanta BC":              "Atalanta",
    "Hellas Verona":            "Verona",
    "Inter Milan":              "Inter",
    "Parma":                    "Parma Calcio 1913",
    # Ligue 1
    "AS Monaco":                "Monaco",
    "RC Lens":                  "Lens",
}

OUTLIER_Z_MAX = 2.5


@dataclass(frozen=True)
class StrategyConfig:
    name:                str
    label:               str
    description:         str
    devig:               str   = "shin"          # "shin" | "proportional" | "power"
    consensus_mode:      str   = "mean"          # "mean" | "weighted" | "pinnacle_only"
    pinnacle_weight:     float = 1.0             # used when consensus_mode == "weighted"
    exclude_pinnacle:    bool  = False           # drop Pinnacle from consensus
    book_filter:         str   = "uk_licensed"  # "uk_licensed" | "exchanges_only" | "all"
    min_edge:            float = 0.03
    min_books:           int   = 20
    max_dispersion:      float | None = None     # stdev cap; None = off
    drop_outlier_book:   bool  = False           # reject flagged book if |z| > 2.5
    require_model_agree: bool  = False           # only flag h2h where model edge ≥ model_min_edge
    model_min_edge:      float = 0.0
    markets:             tuple = ("h2h", "totals", "btts")
    # R.1 fields
    min_consensus_prob:  float = 0.0             # M, N: reject sides with cons < this
    max_consensus_prob:  float = 1.0             # N: reject sides with cons > this
    kelly_fraction:      float = 0.5             # L: fractional Kelly multiplier
    # R.1.5 fields
    raw_consensus:       bool  = False           # O: use 1/odds directly, skip devig
    kaunitz_alpha:       float = 0.0             # O: paper's α; replaces additive min_edge rule
    max_odds_shopping:   bool  = False           # O, P: flag at best-priced UK book per side
    # R.2 fields
    sharpness_weights:   dict | None = None      # J: book → weight; None = uniform
    # R.8 fields
    draw_odds_band:      tuple | None = None     # K: (min, max) decimal odds; D bets only
    require_low_xg:      bool  = False           # K: both teams must be below xg_q25
    draws_only:          bool  = False           # K: skip H/A sides entirely

    def config_hash(self) -> str:
        """12-char SHA-256 of behavior fields. Identity (name/label/description) excluded
        so two variants with identical thresholds collide intentionally; renaming a variant
        without changing thresholds preserves its hash. Tweaking any threshold changes only
        that variant's hash. Used by compare_strategies to filter to current eval window."""
        config = asdict(self)
        for k in ("name", "label", "description"):
            config.pop(k, None)
        normalized = json.dumps(config, sort_keys=True, default=str)
        return hashlib.sha256(normalized.encode()).hexdigest()[:12]


STRATEGIES: list[StrategyConfig] = [
    StrategyConfig(
        name="A_production",
        label="A: Production",
        description="Mirrors production: Shin, mean consensus, all UK-licensed, 3% edge, no model gate",
    ),
    StrategyConfig(
        name="B_strict",
        label="B: Strict",
        description="Weighted consensus (Pinnacle 5×), 5% edge, dispersion filter 0.04",
        consensus_mode="weighted",
        pinnacle_weight=5.0,
        min_edge=0.05,
        max_dispersion=0.04,
    ),
    StrategyConfig(
        name="C_loose",
        label="C: Loose",
        description="Lower edge threshold 2%, otherwise like A",
        min_edge=0.02,
    ),
    StrategyConfig(
        name="D_pinnacle_only",
        label="D: Pinnacle-only",
        description="Edge measured vs Pinnacle's de-vigged prob only",
        consensus_mode="pinnacle_only",
    ),
    StrategyConfig(
        name="E_exchanges_only",
        label="E: Exchanges only",
        # Consensus still uses all UK-licensed books (mean), which dilutes the exchange-only
        # signal. A future refinement is to anchor E on Pinnacle (consensus_mode="pinnacle_only").
        description="Restrict to Betfair Ex / Smarkets / Matchbook; commission auto-applied via commissions.py",
        book_filter="exchanges_only",
        min_edge=0.03,  # commission now applied globally; no longer needs to compensate manually
    ),
    StrategyConfig(
        name="F_model_primary",
        label="F: Model primary",
        description="Model-only: flag h2h where model edge ≥ 3%, skip totals/btts",
        require_model_agree=True,
        model_min_edge=0.03,
        # min_edge=-1.0 so the consensus-edge gate is off: F should fire whenever model
        # agrees at model_min_edge, even if the consensus edge is mildly negative.
        min_edge=-1.0,
        markets=("h2h",),
    ),
    StrategyConfig(
        name="G_proportional",
        label="G: Proportional de-vig",
        description="Proportional de-vig instead of Shin; tests whether Shin adds value",
        devig="proportional",
    ),
    StrategyConfig(
        name="H_no_pinnacle",
        label="H: No Pinnacle in consensus",
        description="Exclude Pinnacle from consensus; isolates Pinnacle's contribution",
        exclude_pinnacle=True,
    ),
    # ── R.1: cheap variants ───────────────────────────────────────────────────
    StrategyConfig(
        name="I_power_devig",
        label="I: Power devig",
        description="Power devig instead of Shin; tests Bethero recommendation",
        devig="power",
    ),
    StrategyConfig(
        name="L_quarter_kelly",
        label="L: 0.4-Kelly",
        description="Tighter Kelly fraction (0.4) — Aldous/Downey caution under uncertainty",
        kelly_fraction=0.4,
    ),
    StrategyConfig(
        name="M_min_prob_15",
        label="M: Min-prob 15%",
        description="Reject bets with consensus prob < 15%; longshot-bias guard (Hegarty & Whelan 2025)",
        min_consensus_prob=0.15,
    ),
    StrategyConfig(
        name="N_competitive_only",
        label="N: Competitive-only",
        description="Only flag matches where consensus prob ∈ [0.30, 0.70]; Clegg & Cartlidge 2025 surviving signal",
        min_consensus_prob=0.30,
        max_consensus_prob=0.70,
    ),
    # ── R.1.5: paper-faithful Kaunitz baseline ────────────────────────────────
    StrategyConfig(
        name="O_kaunitz_classic",
        label="O: Kaunitz classic (paper)",
        description="Paper-faithful Kaunitz: raw consensus, α=0.05, max-odds shopping, min 4 books",
        raw_consensus=True,
        kaunitz_alpha=0.05,
        max_odds_shopping=True,
        min_books=4,
        max_dispersion=None,
        drop_outlier_book=False,
        markets=("h2h",),
    ),
    # ── R.1.6: max-odds shopping variant (optional) ───────────────────────────
    StrategyConfig(
        name="P_max_odds_shopping",
        label="P: Max-odds shopping",
        description="Production logic, but bet at the best-priced UK book on flagged outcome",
        max_odds_shopping=True,
    ),
    # ── R.2: sharp-weighted consensus variant ─────────────────────────────────
    StrategyConfig(
        name="J_sharp_weighted",
        label="J: Sharp-weighted",
        description="Sharpness-weighted consensus per datagolf blind-return ranking",
        sharpness_weights=SHARPNESS_WEIGHTS,
    ),
    # ── R.8: draw-bias variant (Predictology filter) ──────────────────────────
    StrategyConfig(
        name="K_draw_bias",
        label="K: Draw-bias (Predictology)",
        description="Draw bets only; draw odds 3.20–3.60 and both teams in bottom-xG quartile",
        draws_only=True,
        draw_odds_band=(3.20, 3.60),
        require_low_xg=True,
        markets=("h2h",),
    ),
]


# ── de-vig helper ─────────────────────────────────────────────────────────────

def _apply_devig(entries: dict[str, float], method: str) -> dict[str, float]:
    sides = list(entries.keys())
    raw = [1.0 / entries[s] for s in sides]
    if _DEVIG:
        if method == "proportional":
            fair = _proportional(raw)
        elif method == "power":
            try:
                fair = _power(raw)
            except Exception:
                fair = _proportional(raw)
        else:  # shin (default)
            try:
                fair = _shin(raw)
            except Exception:
                fair = _proportional(raw)
    else:
        total = sum(raw)
        fair = [r / total for r in raw]
    return dict(zip(sides, fair))


# ── consensus computation ─────────────────────────────────────────────────────

def _compute_consensus(
    books_data: list[dict],
    impl_by_side: dict[str, list[float]],
    strategy: StrategyConfig,
) -> tuple[dict[str, float], dict[str, float]]:
    """
    Returns (cons, disp):
      cons: {side -> fair consensus prob}
      disp: {side -> stdev of fair probs across all books}
    Returns ({}, {}) if consensus cannot be computed.
    """
    pinnacle_fair = next((b["fair"] for b in books_data if b["book"] == "pinnacle"), {})
    sides = list(impl_by_side.keys())

    disp = {s: (statistics.stdev(v) if len(v) >= 2 else 0.0) for s, v in impl_by_side.items()}

    if strategy.consensus_mode == "pinnacle_only":
        if not pinnacle_fair:
            return {}, disp
        return dict(pinnacle_fair), disp

    # mean or weighted
    side_accum: dict[str, list[tuple[float, float]]] = {s: [] for s in sides}
    for b in books_data:
        if strategy.exclude_pinnacle and b["book"] == "pinnacle":
            continue
        if strategy.sharpness_weights is not None:
            w = strategy.sharpness_weights.get(b["book"], 1.0)
        elif strategy.consensus_mode == "weighted" and b["book"] == "pinnacle":
            w = strategy.pinnacle_weight
        else:
            w = 1.0
        for s in sides:
            if s in b["fair"]:
                side_accum[s].append((b["fair"][s], w))

    cons = {}
    for s, pw_list in side_accum.items():
        if not pw_list:
            continue
        total_w = sum(w for _, w in pw_list)
        cons[s] = sum(p * w for p, w in pw_list) / total_w

    return cons, disp


# ── per-bet flagging ──────────────────────────────────────────────────────────

def _flag_bets(
    home: str, away: str, commence: str,
    market: str, line,
    books_data: list[dict],
    impl_by_side: dict[str, list[float]],
    strategy: StrategyConfig,
    *,
    sport_key: str = "",
    model_signals: dict | None = None,
    api_to_fd: dict | None = None,
    team_xg: dict | None = None,
) -> list[dict]:
    if model_signals is None:
        model_signals = {}
    if api_to_fd is None:
        api_to_fd = {}

    if len(books_data) < strategy.min_books:
        return []

    cons, disp = _compute_consensus(books_data, impl_by_side, strategy)
    if not cons:
        return []

    pinnacle_fair = next((b["fair"] for b in books_data if b["book"] == "pinnacle"), {})
    sides = list(impl_by_side.keys())
    n = len(books_data)
    conf = "HIGH" if n >= 30 else ("MED" if n >= 20 else "LOW")

    if strategy.book_filter == "exchanges_only":
        target_books: set | None = EXCHANGE_BOOKS
    elif strategy.book_filter == "all":
        target_books = None
    else:
        target_books = UK_LICENSED_BOOKS

    bets = []

    if strategy.max_odds_shopping:
        # For each side, find the UK-licensed book with the best (highest) odds.
        best_by_side: dict[str, tuple[str, float, dict]] = {}  # side -> (book_key, odds, book_data)
        for b in books_data:
            if target_books is not None and b["book"] not in target_books:
                continue
            for side in sides:
                odds = b.get(side)
                if not odds or not (1.2 <= odds <= 15.0):
                    continue
                if side not in best_by_side or odds > best_by_side[side][1]:
                    best_by_side[side] = (b["book"], odds, b)

        for side in sides:
            if strategy.draws_only and side != "D":
                continue
            if side not in cons or side not in best_by_side:
                continue
            if cons[side] < strategy.min_consensus_prob:
                continue
            if cons[side] > strategy.max_consensus_prob:
                continue
            if strategy.max_dispersion is not None:
                if disp.get(side, 0.0) > strategy.max_dispersion:
                    continue

            book_key, odds, b = best_by_side[side]

            if side == "D":
                if strategy.draw_odds_band is not None:
                    lo, hi = strategy.draw_odds_band
                    if not (lo <= odds <= hi):
                        continue
                if strategy.require_low_xg:
                    _td = (team_xg or {}).get("teams", {})
                    _q25 = (team_xg or {}).get("xg_q25", 0.0)
                    _h_key = UNDERSTAT_NAME_ALIAS.get(home, home)
                    _a_key = UNDERSTAT_NAME_ALIAS.get(away, away)
                    h_xg = _td.get(_h_key, {}).get("avg_xg")
                    a_xg = _td.get(_a_key, {}).get("avg_xg")
                    # Missing team → block (conservative); also gates out NBA/tennis/non-model leagues.
                    if h_xg is None or a_xg is None or not (h_xg <= _q25 and a_xg <= _q25):
                        continue

            fair_side = b["fair"].get(side, 1.0 / odds)
            edge_gross = cons[side] - fair_side
            eff_implied = _effective_implied_prob(odds, book_key)
            edge = cons[side] - eff_implied

            if strategy.kaunitz_alpha > 0:
                if (cons[side] - strategy.kaunitz_alpha) * odds - 1 <= 0:
                    continue
            else:
                if edge_gross < strategy.min_edge:
                    continue

            impl_raw = round(1.0 / odds, 4)
            impl_effective = round(eff_implied, 4)
            z = 0.0  # no outlier filter when shopping for max odds

            ms = "?"
            if market == "h2h":
                h = api_to_fd.get(home, home)
                a = api_to_fd.get(away, away)
                sig = model_signals.get(f"{sport_key}:{h}|{a}")
                if sig is not None:
                    ms_edge = sig.get(side, 0.0) - impl_raw
                    ms = f"{ms_edge:+.3f}"
                if strategy.require_model_agree:
                    try:
                        if float(ms) < strategy.model_min_edge:
                            continue
                    except (ValueError, TypeError):
                        continue

            bets.append({
                "market": market, "line": line,
                "commence": commence, "home": home, "away": away,
                "side": side, "book": book_key, "odds": odds,
                "impl_raw": impl_raw, "impl_effective": impl_effective,
                "cons": round(cons[side], 4),
                "edge": round(edge, 4), "edge_gross": round(edge_gross, 4),
                "effective_odds": round(_effective_odds(odds, book_key), 4),
                "commission_rate": round(_commission_rate(book_key), 4),
                "pinnacle_cons": round(pinnacle_fair.get(side, 0.0), 4),
                "n_books": n, "confidence": conf,
                "model_signal": ms,
                "dispersion": round(disp.get(side, 0.0), 4),
                "outlier_z": round(z, 3),
                "kelly_fraction": strategy.kelly_fraction,
            })
        return bets

    # ── Normal per-book path ──────────────────────────────────────────────────
    for b in books_data:
        if target_books is not None and b["book"] not in target_books:
            continue
        for side in sides:
            if strategy.draws_only and side != "D":
                continue
            if side not in cons:
                continue
            odds = b.get(side)
            if not odds or odds <= 1.0:
                continue
            if not (1.2 <= odds <= 15.0):
                continue

            if side == "D":
                if strategy.draw_odds_band is not None:
                    lo, hi = strategy.draw_odds_band
                    if not (lo <= odds <= hi):
                        continue
                if strategy.require_low_xg:
                    _td = (team_xg or {}).get("teams", {})
                    _q25 = (team_xg or {}).get("xg_q25", 0.0)
                    _h_key = UNDERSTAT_NAME_ALIAS.get(home, home)
                    _a_key = UNDERSTAT_NAME_ALIAS.get(away, away)
                    h_xg = _td.get(_h_key, {}).get("avg_xg")
                    a_xg = _td.get(_a_key, {}).get("avg_xg")
                    # Missing team → block (conservative); also gates out NBA/tennis/non-model leagues.
                    if h_xg is None or a_xg is None or not (h_xg <= _q25 and a_xg <= _q25):
                        continue

            if cons[side] < strategy.min_consensus_prob:
                continue
            if cons[side] > strategy.max_consensus_prob:
                continue

            if strategy.max_dispersion is not None:
                if disp.get(side, 0.0) > strategy.max_dispersion:
                    continue

            fair_side = b["fair"].get(side, 1.0 / odds)
            edge_gross = cons[side] - fair_side
            # Commission shrinks effective payout → raises effective implied prob → reduces edge.
            # Filter on gross edge (Shin-devigged, consistent with production find_value_bets).
            eff_implied = _effective_implied_prob(odds, b["book"])
            edge = cons[side] - eff_implied

            if strategy.kaunitz_alpha > 0:
                if (cons[side] - strategy.kaunitz_alpha) * odds - 1 <= 0:
                    continue
            else:
                if edge_gross < strategy.min_edge:
                    continue

            # Outlier-book check
            z = 0.0
            if strategy.drop_outlier_book:
                other_probs = [b2["fair"][side] for b2 in books_data
                               if b2["book"] != b["book"] and side in b2["fair"]]
                if len(other_probs) >= 2:
                    om = statistics.mean(other_probs)
                    os_ = statistics.stdev(other_probs)
                    z = (fair_side - om) / os_ if os_ > 0 else 0.0
                if abs(z) > OUTLIER_Z_MAX:
                    continue

            impl_raw       = round(1.0 / odds, 4)
            impl_effective = round(eff_implied, 4)

            # Model signal always vs raw implied (commission is a payout adjustment, not prob)
            ms = "?"
            if market == "h2h":
                h = api_to_fd.get(home, home)
                a = api_to_fd.get(away, away)
                sig = model_signals.get(f"{sport_key}:{h}|{a}")
                if sig is not None:
                    ms_edge = sig.get(side, 0.0) - impl_raw
                    ms = f"{ms_edge:+.3f}"

                if strategy.require_model_agree:
                    try:
                        if float(ms) < strategy.model_min_edge:
                            continue
                    except (ValueError, TypeError):
                        continue  # no signal → skip for model-primary variant

            bets.append({
                "market": market,
                "line": line,
                "commence": commence,
                "home": home,
                "away": away,
                "side": side,
                "book": b["book"],
                "odds": odds,
                "impl_raw": impl_raw,
                "impl_effective": impl_effective,
                "cons": round(cons[side], 4),
                "edge": round(edge, 4),
                "edge_gross": round(edge_gross, 4),
                "effective_odds": round(_effective_odds(odds, b["book"]), 4),
                "commission_rate": round(_commission_rate(b["book"]), 4),
                "pinnacle_cons": round(pinnacle_fair.get(side, 0.0), 4),
                "n_books": n,
                "confidence": conf,
                "model_signal": ms,
                "dispersion": round(disp.get(side, 0.0), 4),
                "outlier_z": round(z, 3),
                "kelly_fraction": strategy.kelly_fraction,
            })

    return bets


# ── per-market collectors ─────────────────────────────────────────────────────

def _collect_h2h(ev: dict, strategy: StrategyConfig) -> tuple[list[dict], dict[str, list[float]]]:
    home, away = ev["home_team"], ev["away_team"]
    impl: dict[str, list[float]] = {}
    books: list[dict] = []

    for b in ev.get("bookmakers", []):
        for m in b.get("markets", []):
            if m["key"] != "h2h":
                continue
            oc = {o["name"]: o["price"] for o in m["outcomes"]}
            entries: dict[str, float] = {"H": oc.get(home), "A": oc.get(away)}
            draw = oc.get("Draw")
            if draw:
                entries["D"] = draw
            if not all(v and v > 1.0 for v in entries.values()):
                continue
            if strategy.raw_consensus:
                fair = {s: 1.0 / v for s, v in entries.items()}
            else:
                fair = _apply_devig(entries, strategy.devig)
            for s, fp in fair.items():
                impl.setdefault(s, []).append(fp)
            books.append({"book": b["key"], "fair": fair, **entries})

    return books, impl


def _collect_totals(ev: dict, strategy: StrategyConfig) -> dict[float, tuple[list[dict], dict[str, list[float]]]]:
    by_pt: dict[float, tuple[list[dict], dict[str, list[float]]]] = {}

    for b in ev.get("bookmakers", []):
        for m in b.get("markets", []):
            if m["key"] != "totals":
                continue
            pt_oc: dict[float, dict[str, float]] = {}
            for o in m.get("outcomes", []):
                pt = o.get("point")
                if pt is None:
                    continue
                pt_oc.setdefault(float(pt), {})[o["name"].upper()] = o["price"]
            for pt, oc in pt_oc.items():
                over, under = oc.get("OVER"), oc.get("UNDER")
                if not (over and under and over > 1.0 and under > 1.0):
                    continue
                entries = {"OVER": over, "UNDER": under}
                if strategy.raw_consensus:
                    fair = {s: 1.0 / v for s, v in entries.items()}
                else:
                    fair = _apply_devig(entries, strategy.devig)
                if pt not in by_pt:
                    by_pt[pt] = ([], {})
                books, impl = by_pt[pt]
                for s, fp in fair.items():
                    impl.setdefault(s, []).append(fp)
                books.append({"book": b["key"], "fair": fair, **entries})

    return by_pt


def _collect_btts(ev: dict, strategy: StrategyConfig) -> tuple[list[dict], dict[str, list[float]]]:
    impl: dict[str, list[float]] = {}
    books: list[dict] = []

    for b in ev.get("bookmakers", []):
        for m in b.get("markets", []):
            if m["key"] != "btts":
                continue
            oc = {o["name"].upper(): o["price"] for o in m.get("outcomes", [])}
            yes_o, no_o = oc.get("YES"), oc.get("NO")
            if not (yes_o and no_o and yes_o > 1.0 and no_o > 1.0):
                continue
            entries = {"YES": yes_o, "NO": no_o}
            if strategy.raw_consensus:
                fair = {s: 1.0 / v for s, v in entries.items()}
            else:
                fair = _apply_devig(entries, strategy.devig)
            for s, fp in fair.items():
                impl.setdefault(s, []).append(fp)
            books.append({"book": b["key"], "fair": fair, **entries})

    return books, impl


# ── main entry point ──────────────────────────────────────────────────────────

def evaluate_strategy(
    events: list,
    sport_key: str,
    strategy: StrategyConfig,
    *,
    model_signals: dict | None = None,
    api_to_fd: dict | None = None,
    team_xg: dict | None = None,
) -> list[dict]:
    """
    Run one strategy variant on a list of already-fetched events.
    Returns list of bet dicts (same schema as find_value_bets output).
    No extra API calls — reuses the events passed in.
    """
    if model_signals is None:
        model_signals = {}
    if api_to_fd is None:
        api_to_fd = {}
    if strategy.require_low_xg and team_xg is None:
        try:
            with open(_XG_FILE) as _f:
                team_xg = json.load(_f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            team_xg = {}

    bets: list[dict] = []

    for ev in events:
        home = ev["home_team"]
        away = ev["away_team"]
        commence = ev["commence_time"]

        if "h2h" in strategy.markets:
            books, impl = _collect_h2h(ev, strategy)
            bets.extend(_flag_bets(
                home, away, commence, "h2h", "",
                books, impl, strategy,
                sport_key=sport_key,
                model_signals=model_signals,
                api_to_fd=api_to_fd,
                team_xg=team_xg,
            ))

        if "totals" in strategy.markets:
            for pt, (books, impl) in _collect_totals(ev, strategy).items():
                bets.extend(_flag_bets(
                    home, away, commence, "totals", pt,
                    books, impl, strategy,
                    sport_key=sport_key,
                    team_xg=team_xg,
                ))

        if "btts" in strategy.markets:
            books, impl = _collect_btts(ev, strategy)
            bets.extend(_flag_bets(
                home, away, commence, "btts", "",
                books, impl, strategy,
                sport_key=sport_key,
                team_xg=team_xg,
            ))

    # Dedup: best edge per (fixture, market, line, side)
    bets.sort(key=lambda x: x["edge"], reverse=True)
    seen: set = set()
    out: list[dict] = []
    for vb in bets:
        k = (vb["home"], vb["away"], vb["market"], str(vb.get("line", "")), vb["side"])
        if k not in seen:
            seen.add(k)
            out.append(vb)
    return out
