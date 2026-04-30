"""
Weekly xG snapshot — fetches last 5 results per team from Understat and writes
logs/team_xg.json with per-team avg scoring xG and the cross-team 25th-percentile
threshold used by the K_draw_bias strategy filter.

Cron: 0 6 * * 1  (Mondays 06:00 UTC)
Usage: python3 scripts/refresh_xg.py
"""
import asyncio
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

XG_FILE = Path(__file__).parent.parent / "logs" / "team_xg.json"

# Understat league names for the four leagues we have model coverage for
LEAGUES = ["EPL", "Bundesliga", "Serie_A", "Ligue_1"]

_now = datetime.now()
# Season 2024 = 2024/25; starts in August, so if month < 8 we're still in the prior season.
CURRENT_SEASON: int = _now.year if _now.month >= 8 else _now.year - 1

try:
    import aiohttp
    import understat as _understat_lib
    _DEPS_OK = True
except ImportError:
    _DEPS_OK = False


async def _fetch_league(session, league: str) -> dict[str, list[float]]:
    """Return {team: [xg_scored, ...]} for all played matches in the season."""
    u = _understat_lib.Understat(session)
    matches = await u.get_league_results(league, CURRENT_SEASON)
    team_xg: dict[str, list[float]] = {}
    for m in matches:
        if not m.get("isResult"):
            continue
        home = m["h"]["title"]
        away = m["a"]["title"]
        h_xg = float(m.get("xG", {}).get("h", 0))
        a_xg = float(m.get("xG", {}).get("a", 0))
        team_xg.setdefault(home, []).append(h_xg)
        team_xg.setdefault(away, []).append(a_xg)
    return team_xg


async def _fetch_all() -> dict[str, list[float]]:
    combined: dict[str, list[float]] = {}
    async with aiohttp.ClientSession() as session:
        for league in LEAGUES:
            print(f"  [{league}] fetching ... ", end="", flush=True)
            try:
                data = await _fetch_league(session, league)
                print(f"{len(data)} teams")
                combined.update(data)
            except Exception as exc:
                print(f"ERROR: {exc}")
    return combined


def _build_snapshot(team_xg: dict[str, list[float]]) -> dict:
    teams: dict[str, dict] = {}
    for team, xgs in team_xg.items():
        last5 = xgs[-5:] if len(xgs) >= 1 else []
        if last5:
            teams[team] = {"avg_xg": round(statistics.mean(last5), 3), "n": len(last5)}

    if not teams:
        return {"updated": datetime.now(timezone.utc).isoformat(), "xg_q25": 1.0, "teams": {}}

    sorted_avgs = sorted(t["avg_xg"] for t in teams.values())
    # Pooled q25 across all four leagues — deliberate choice: teams that score less than
    # the 25th percentile globally are low-xG regardless of league context.  A league-
    # relative threshold would require storing per-league data; the pooled version is
    # simpler and still captures the "genuinely defensive" tier.  Revise if Ligue 1
    # systematic bias becomes apparent in K_draw_bias shadow results.
    # Index uses lower bound (slightly below true q25) — intentional conservative choice.
    q25_idx = max(0, int(len(sorted_avgs) * 0.25) - 1)
    xg_q25 = sorted_avgs[q25_idx]

    return {
        "updated": datetime.now(timezone.utc).isoformat(),
        "xg_q25": round(xg_q25, 3),
        "teams": teams,
    }


def main() -> None:
    if not _DEPS_OK:
        print("understat/aiohttp not installed — skipping xG refresh (keeping existing file)")
        return

    print(f"Refreshing team xG snapshot (season {CURRENT_SEASON}/{CURRENT_SEASON + 1}) ...")
    try:
        team_xg = asyncio.run(_fetch_all())
        snapshot = _build_snapshot(team_xg)
        XG_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = XG_FILE.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(snapshot, f, indent=2)
        tmp.replace(XG_FILE)
        print(
            f"Written {len(snapshot['teams'])} teams → {XG_FILE} "
            f"(xg_q25={snapshot['xg_q25']})"
        )
    except Exception as exc:
        print(f"xG refresh failed: {exc} — keeping existing {XG_FILE}")


if __name__ == "__main__":
    main()
