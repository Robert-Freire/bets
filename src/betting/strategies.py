"""
Paper-portfolio strategy variants for shadow A/B testing.

Each StrategyConfig defines one variant of the value-bet scanner.
evaluate_strategy() runs a variant on already-fetched events (no extra API calls).
"""

import statistics
from dataclasses import dataclass

try:
    from src.betting.devig import shin as _shin, proportional as _proportional, power as _power
    _DEVIG = True
except ImportError:
    _DEVIG = False

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

# ── book sets ────────────────────────────────────────────────────────────────

UK_LICENSED_BOOKS = {
    "betfair_ex_uk", "betfair_sb_uk", "smarkets", "matchbook",
    "betfred_uk", "williamhill", "coral", "ladbrokes_uk", "skybet",
    "paddypower", "boylesports", "betvictor", "betway", "leovegas",
    "casumo", "virginbet", "livescorebet", "sport888", "grosvenor",
}

EXCHANGE_BOOKS = {"betfair_ex_uk", "smarkets", "matchbook"}

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
        w = (strategy.pinnacle_weight
             if strategy.consensus_mode == "weighted" and b["book"] == "pinnacle"
             else 1.0)
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
    for b in books_data:
        if target_books is not None and b["book"] not in target_books:
            continue
        for side in sides:
            if side not in cons:
                continue
            odds = b.get(side)
            if not odds or odds <= 1.0:
                continue
            if not (1.2 <= odds <= 15.0):
                continue

            if strategy.max_dispersion is not None:
                if disp.get(side, 0.0) > strategy.max_dispersion:
                    continue

            fair_side = b["fair"].get(side, 1.0 / odds)
            edge_gross = cons[side] - fair_side
            # Commission shrinks effective payout → raises effective implied prob → reduces edge
            eff_implied = _effective_implied_prob(odds, b["book"])
            edge = cons[side] - eff_implied

            if edge < strategy.min_edge:
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

            ip = round(eff_implied, 4)

            # Model signal (h2h only)
            ms = "?"
            if market == "h2h":
                h = api_to_fd.get(home, home)
                a = api_to_fd.get(away, away)
                sig = model_signals.get(f"{sport_key}:{h}|{a}")
                if sig is not None:
                    ms_edge = sig.get(side, 0.0) - ip
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
                "impl": ip,
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
            ))

        if "totals" in strategy.markets:
            for pt, (books, impl) in _collect_totals(ev, strategy).items():
                bets.extend(_flag_bets(
                    home, away, commence, "totals", pt,
                    books, impl, strategy,
                    sport_key=sport_key,
                ))

        if "btts" in strategy.markets:
            books, impl = _collect_btts(ev, strategy)
            bets.extend(_flag_bets(
                home, away, commence, "btts", "",
                books, impl, strategy,
                sport_key=sport_key,
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
