# Dispersion Shape Analysis — All Leagues (2026-05)

Generated 2026-05-01 from Azure Blob `raw-api-snapshots` archives. **No fresh API credits spent** — this entire analysis ran offline against snapshots already paid for. Per memory `feedback_reuse_archived_data.md`.

> **Tool:** `scripts/analyse_dispersion.py --blob <path>` (read-only).

---

## TL;DR

**Bimodality is universal**, not unique to high-dispersion leagues. Every football league analysed shows 75–93% bimodal fixture×outcome rows. The differentiator is **cluster amplitude** — how far apart the low and high clusters sit.

| League | n_fix | n_rows | bimodal % | **amplitude** | top sharps (centre%) |
|---|---|---|---|---|---|
| Ligue 1 | 18 | 54 | 90.7% | **0.0518** | marathonbet (94%), everygame (89%), smarkets (85%) |
| La Liga | 20 | 60 | 78.3% | **0.0441** | pinnacle (89%), marathonbet (88%), matchbook (87%) |
| Serie A | 20 | 60 | 91.7% | 0.0381 | codere_it (100%), unibet_fr (91%), betclic_fr (90%) |
| Primeira | 9 | 27 | 77.8% | 0.0286 | coolbet (93%), marathonbet (93%), onexbet (92%) |
| Ligue 2 | 9 | 27 | 81.5% | 0.0274 | betsson (100%), nordicbet (100%), codere_it (100%) |
| Eredivisie | 18 | 27 | 92.6% | 0.0244 | codere_it (100%), unibet_uk (93%), paddypower (89%) |
| Bundesliga | 11 | 33 | 92.6% | 0.0242 | unibet_uk (96%), onexbet (96%), marathonbet (96%) |
| La Liga 2 | 10 | 27 | 85.2% | 0.0235 | unibet_fr (96%), nordicbet (93%), betsson (93%) |
| Championship | 12 | 36 | 91.7% | 0.0232 | pinnacle (92%), onexbet (92%), marathonbet (92%) |
| EPL | 20 | 57 | 91.2% | 0.0228 | betclic_fr (93%), matchbook (90%), everygame (89%) |

> **Note.** This is a single-scan snapshot. Cluster persistence across multiple scans (Sat/Sun) needs to be confirmed before drawing strong conclusions. M.7 phase tasks include this.

---

## Methodology limitation — "sharp = centre rate" is a proxy, not a measurement

The script identifies "sharp" books as those with ≥ 80% centre rate (rarely in either tail cluster). This is a proxy for "agrees with the consensus median," not a direct measure of sharpness.

**Three known weaknesses:**

1. **Median bias toward soft consensus.** UK retail books (bet365, Coral, Ladbrokes, Sky Bet) often quote nearly-identical prices. If 8 soft books cluster around the same number, the median sits with them — and a real sharp who knows better appears to be in a tail. **A sharp can look soft under this metric.**
2. **Followers look sharp.** A book that prices midway between Pinnacle and the soft consensus has a high centre rate by construction, without producing any independent signal.
3. **Empirical contradiction.** Pinnacle is the canonical sharp anchor in betting literature, yet under centre-rate it's outside the top-5 sharps for EPL, Bundesliga, Serie A, and Ligue 1 (densely-priced markets where many books cluster). It IS top-3 on La Liga and Championship (where the soft consensus is more spread out). Either Pinnacle's sharpness genuinely varies by league, or the metric mislabels.

**Better metrics, ordered by effort:**

| Metric | Pro | Con |
|---|---|---|
| **Pinnacle-anchored deviation** — `mean(\|fair_book - fair_pinnacle\|)` per row | Uses domain knowledge of Pinnacle as canonical sharp; bakes in known truth signal | Circular if Pinnacle is wrong on a specific league; fails where Pinnacle has thin coverage |
| **Trimmed-mean centre** — drop top/bottom 20% of books before computing median, re-classify | Less sensitive to soft-book copying | Still not anchored to truth |
| **Closing-line deviation** (gold standard) — `mean(\|book_open - pinnacle_close\|)` | Direct CLV measure; textbook definition | Requires ≥ 50+ historical fixtures per book × league with FDCO close odds |

**TODO for M.7 phase work:** add Pinnacle-anchored deviation as a second column in `analyse_dispersion.py` output. If centre-rate and Pinnacle-anchored broadly agree per league, centre-rate is good enough. If they disagree systematically, trust Pinnacle-anchored. Once 4+ weekends of CLV land, swap in closing-line deviation as the gold-standard metric for h2h on top-6 leagues.

---

## Methodology revision

The original M.7 spec used `bimodal share ≥ 30%` as the threshold for "sharp-weighting hypothesis testable." That threshold is uselessly low — every league passes it. Replacing with:

| Metric | What it measures | Decision rule |
|---|---|---|
| Cluster amplitude | Mean distance between low-cluster median and high-cluster median (in fair-prob units) | **≥ 0.04** = exploitable disagreement; < 0.025 = books mostly agree, weak edge potential |
| Sharp persistence | Same books in centre across multiple scans (Spearman) | ≥ 0.6 = structural; < 0.3 = noise |
| Per-league sharps | Books with ≥ 80% centre rate | Use as `book_weights.by_league` overrides for `J2_sharp_weighted_per_league` |

Update the M.7 acceptance criteria in `docs/PLAN_MARKET_COVERAGE_2026-05.md` accordingly.

---

## Key findings

### 1. Bimodality is the default, not the exception

Every football league analysed is heavily bimodal. The Odds API book set systematically produces clusters because the books span:
- Sharp anchors (Pinnacle, exchanges, Marathonbet, niche European specialists)
- Mid-tier UK retail (Bet365, Coral, Ladbrokes, William Hill, Sky Bet)
- Soft retail (Virginbet, Livescorebet, Paddypower, Betway)

Whenever ≥10 books quote a market, you almost always get 2+ books at each extreme. The **pure shape diagnostic** (bimodal vs unimodal) doesn't differentiate leagues. Cluster amplitude does.

### 2. Ligue 1 has the highest amplitude, not La Liga

Ligue 1 (already in prod) shows mean cluster amplitude **0.0518** — higher than La Liga's 0.0441. This is surprising:
- Either Ligue 1 has unrealised edge that flat-consensus is missing
- OR amplitude alone doesn't translate to extractable edge (sharps and softs disagree but the sharps' consensus is already what we trust)

This is the question to investigate next. Run sharp-weighted variants on Ligue 1 and compare vs flat consensus on the existing CLV-validated dataset.

### 3. Sharp identity shifts per league

The hardcoded `J_sharp_weighted` weights (Pinnacle 3.0, Betfair Exchange 2.5, Smarkets 2.0, others 1.0) are league-blind. The data says they should be league-aware:

| Book | EPL | Bund | SA | Ligue 1 | Champ | La Liga | Eredivisie | Primeira | La Liga 2 | Ligue 2 |
|---|---|---|---|---|---|---|---|---|---|---|
| pinnacle | — | — | — | — | **sharp** | **sharp** | — | — | — | — |
| marathonbet | — | **sharp** | — | **sharp** | **sharp** | **sharp** | sharp | **sharp** | — | — |
| matchbook | **sharp** | — | — | — | — | **sharp** | — | — | — | — |
| smarkets | — | — | — | **sharp** | — | sharp | — | — | — | — |
| betfair_ex_uk | — | — | — | — | — | — | — | — | — | — |
| codere_it | — | — | **100%** | — | — | sharp | **100%** | — | — | **100%** |
| unibet_uk | — | **sharp** | sharp | sharp | — | sharp | **sharp** | sharp | — | sharp |
| betclic_fr | **sharp** | — | **sharp** | — | — | — | — | — | sharp | — |

Notable patterns:
- **Marathonbet is a near-universal sharp** (8 of 10 leagues) and currently weighted 1.0 in `J_sharp_weighted`. Should be 2.5–3.0 baseline.
- **Pinnacle's "universal sharp" reputation breaks down on the largest leagues.** It's centre-dominant on La Liga and Championship but middle-of-the-pack on EPL/Bundesliga/Serie A/Ligue 1. Its sharpness is stronger where book disagreement is wider — exactly where we need anchoring.
- **Codere_it** and **unibet_fr** are niche European specialists; codere_it shows 100% centre on Serie A and Eredivisie — strong directional signal.
- **Betfair Exchange UK** never makes it to the top sharps in this dataset — exchange depth varies by league, and on niche leagues it's actually scattered.

### 4. Soft UK books ARE persistent

Across leagues, the same UK retail books show up as soft (low centre rate, often directional bias):
- **virginbet, livescorebet, betway, paddypower, skybet, ladbrokes_uk** — soft on most leagues
- **betfair_sb_uk** (sportsbook, not exchange) — extremely soft (12–44% centre across leagues)

These are the books the Kaunitz strategy already correctly identifies as edge sources when their prices deviate from sharp consensus. The data validates that part.

### 5. Anomalies worth flagging

- **winamax_fr / winamax_de** show extreme outcome-specific bias on La Liga (always low on Draws, always high on Aways). Different pricing model entirely. Worth understanding before trusting their prices anywhere.
- **tipico_de** consistently soft on EPL/Bundesliga/Serie A — German book, may have weaker info on non-German leagues.
- **codere_it** at 100% centre on Italian + niche leagues; possibly an information specialist for Southern Europe.

---

## Recommended `book_weights.by_league` seed (for M.6)

Based on this single-scan analysis. **Subject to revision after cluster persistence is verified across multiple scans.**

```json
{
  "book_weights": {
    "default": {
      "marathonbet": 2.5,
      "pinnacle": 2.0,
      "smarkets": 2.0,
      "matchbook": 2.0,
      "unibet_uk": 1.5,
      "betfair_ex_uk": 1.0,
      "betfair_ex_eu": 1.0,
      "*": 1.0
    },
    "by_league": {
      "soccer_spain_la_liga": {
        "pinnacle": 3.0, "marathonbet": 2.5, "matchbook": 2.5, "smarkets": 2.0,
        "betfair_ex_uk": 1.0, "*": 1.0
      },
      "soccer_efl_champ": {
        "pinnacle": 3.0, "marathonbet": 2.5, "onexbet": 2.0, "*": 1.0
      },
      "soccer_france_ligue_one": {
        "marathonbet": 3.0, "everygame": 2.5, "smarkets": 2.5, "unibet_uk": 2.0, "*": 1.0
      },
      "soccer_italy_serie_a": {
        "codere_it": 3.0, "unibet_fr": 2.5, "betclic_fr": 2.5, "unibet_uk": 2.0, "*": 1.0
      },
      "soccer_germany_bundesliga": {
        "marathonbet": 3.0, "unibet_uk": 2.5, "onexbet": 2.5, "nordicbet": 2.0, "*": 1.0
      },
      "soccer_epl": {
        "betclic_fr": 2.5, "matchbook": 2.5, "everygame": 2.0, "leovegas_se": 1.5, "*": 1.0
      }
    }
  }
}
```

The `default` block changes are the most important: **promote marathonbet 1.0 → 2.5** as a near-universal sharp.

---

## Weekly post-mortem cadence

This analysis should run **every Monday morning** as part of the post-mortem, against the previous weekend's blobs. Pattern:

```bash
# Pull blobs for all 11 leagues from this past weekend (free — already archived)
WEEKEND="2026-05-03"   # adjust per Monday
mkdir -p /tmp/blobs_$WEEKEND
for sport in soccer_epl soccer_germany_bundesliga soccer_italy_serie_a soccer_efl_champ \
             soccer_france_ligue_one soccer_germany_bundesliga2 soccer_spain_la_liga \
             soccer_spain_segunda_division soccer_netherlands_eredivisie \
             soccer_portugal_primeira_liga soccer_france_ligue_two; do
  latest=$(az storage blob list --account-name kaunitzdevstrfk1 -c raw-api-snapshots --auth-mode key \
    --query "sort_by([?contains(name,'${sport}_odds') && contains(name,'${WEEKEND:0:7}')], &properties.lastModified)[-1].name" -o tsv)
  [ -n "$latest" ] && az storage blob download --account-name kaunitzdevstrfk1 -c raw-api-snapshots --auth-mode key \
    -n "$latest" -f "/tmp/blobs_$WEEKEND/$(basename $latest)" --no-progress > /dev/null
done

# Run analysis on each
for f in /tmp/blobs_$WEEKEND/*.json.gz; do
  echo "=== $(basename $f) ==="
  python3 scripts/analyse_dispersion.py --blob "$f" | head -20
done

# Compare against prior week's findings — note any sharp/soft drift
diff <(grep "sharps:" docs/DISPERSION_SHAPES_2026-05.md | sort) \
     <(./generate_current_sharps.sh | sort)
```

**What to look for week-on-week:**
- Sharp identity drift — same books staying in centre? If a top sharp drops out, investigate (book changed pricing model, or its desk stopped covering that league).
- Cluster amplitude trend — rising amplitude in a league means more disagreement (more potential edge AND more risk).
- New books appearing at >80% centre rate that aren't in `book_weights` — should they be added?
- Soft UK books with stable bias — these are the edge-flagging targets; their persistence validates flagging logic.

**Outcome:** by 4–6 weeks of weekly post-mortems, we have empirical evidence on which books are *persistently* sharp per league, validating the M.6 weights. Without this cadence, the weights are a one-shot guess.
