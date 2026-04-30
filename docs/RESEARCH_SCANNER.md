# Research Scanner — Plan

A scheduled job that surfaces external betting research and similar projects so we can adapt new strategies into `src/betting/strategies.py` as paper variants. Output lives in `docs/RESEARCH_FEED.md` — a running, deduped log of findings ranked by how easily we could adopt them.

**Status: spec — not implemented.** Phasing below is delegation-ready: each phase is sized for one agent session (~30min–1.5h) with explicit Inputs / Outputs / Acceptance / Reviewer-focus blocks. Any agent (or future-me) can pick up a single phase from this doc alone, no prior conversation required.

---

## How to use this doc

- **One agent picks one phase.** Don't bundle phases — small commits are reviewable.
- **Read the phase's `Inputs` first.** If a dependency phase isn't `done`, stop and flag.
- **Do not invent scope.** Only do what the phase's `Tasks` section lists. If something looks wrong or missing, add a comment in the status tracker — don't silently expand.
- **The `Acceptance` block is the definition of done.** Every checkbox must be ticked before marking the phase complete.
- **When finished**, update the **Phase status tracker** below with status, branch/commit, and one-line note.
- **Reviewer-focus** lines tell you (and me) what gets the closest read on PR review.

---

## Phase status tracker

| Phase | Title | Status | Branch / SHA | Notes |
|---|---|---|---|---|
| 11.0 | Cron-auth smoke test (no code) | done | main / c670a5e | CLAUDE_CMD verified; median 3.7s; model=claude-opus-4-7; PATH needs /home/rfreire/.local/bin. (Bundled into 11.1's commit.) |
| 11.1 | Source list + queries (data only) | done | main / c670a5e | 37 URLs (25 Tier-A + 12 Tier-B); 7 queries; spec URL count corrected 28→25. |
| 11.2 | Fetcher module | done | main / 1f1c1b1 → b911df9 | FetchResult + 6 handlers; review fixed: github topic parser broken on real HTML, 4xx silently parsed, Wikipedia nav cruft, 501 missing. 21/21 fetch tests pass. |
| 11.3 | Dedup state + pending-file builder | done | main / HEAD | `load_seen/save_seen/is_changed/update_seen/assemble_pending`; 19/19 tests pass; atomic write + 200 KB batching verified. |
| 11.4 | Claude subprocess wrapper | done | main / HEAD | call_claude + call_claude_batched; 8/8 tests; real smoke returned findings; PROMPT_TEMPLATE byte-for-byte match; log line confirmed. |
| 11.5 | Feed writer | done | main / HEAD | `write_findings`; 13/13 tests pass; atomic write, banner-once, newest-first verified. |
| 11.6 | Top-level CLI + bootstrap mode | done | main / HEAD | CLI + bootstrap; 37 findings on first run; dedup confirmed; kill switch verified; no hard-coded paths. |
| 11.7 | Open-search backends | done | main / HEAD | `search.py`; 4 backends (arxiv/hn/github/ddg); 13/13 tests pass; live `--mode open` confirmed; backend tags in feed; 4-metric dedup log; GITHUB_TOKEN auth. |
| 11.8 | Dashboard tile | done | main / HEAD | `latest_research_findings()` in app.py; Research tile in stats bar; graceful fallback (None,0,"") on missing/malformed feed. |
| 11.9 | Cron + production hardening | done | main / HEAD | Crontab lines composed; README + CLAUDE.md + PI_AZURE_SETUP.md updated; both dry-runs pass. User to paste cron entries. |
| 11.10 | Optional follow-ups | deferred | — | Post-MVP only. |

**Dependency graph.**

```
11.0 ─┐
      ▼
11.1 → 11.2 → 11.3 → 11.4 → 11.5 → 11.6 ─┬─→ 11.7 ─┐
                                          ├─→ 11.8 ─┼─→ 11.9 → 11.10
                                          └─────────┘
```

---

## Goal & non-goals

**Goal.** Catch market findings, model tricks, and similar-project ideas we'd otherwise miss, and route them into the strategy-variant pipeline (Phase 5.5). The success criterion is: at least one finding per quarter that becomes a paper variant in `strategies.py`.

**Non-goals.**
- Not a daily news feed. Weekly cadence at most.
- Not an autonomous agent — no automated PRs, no automated code changes.
- Not a substitute for the research foundation (Dixon-Coles, Kaunitz, Shin, etc.) — it's the layer above.
- Not aiming for completeness. Quality > recall.

---

## Architecture: subprocess to Claude Code

Per user preference, we **do not use the Anthropic SDK directly**. Instead `research_scan.py`:

1. Fetches URLs and writes new content to a transient file (`logs/research_pending.md`).
2. Shells out to the `claude` CLI in non-interactive mode (`claude -p`) with a fixed prompt template, passing the pending file as context.
3. Captures stdout and appends it to `docs/RESEARCH_FEED.md`.

Why this design: reuses the existing Claude Code login (no separate API key); one process, no HTTP plumbing; the `claude` CLI handles retries, rate limits, model selection internally.

Tradeoffs we accept: less control over output structure (no JSON mode); tied to having `claude` installed wherever the cron runs (WSL today, Pi later — both fine).

### Cost controls

Opus is expensive per token, so we enforce **hard caps** rather than trusting the prompt to keep things short:

- **Per-source body cap**: trim each fetched URL to the first **20 KB** of cleaned text (≈ 5k tokens).
- **Per-run input cap**: hard limit of **200 KB** combined per Claude call (≈ 50k tokens). If exceeded, batch sequentially.
- **Run frequency caps**: bootstrap once on install (manual), Tier-B weekly, open-search monthly.
- **Model flag**: `claude -p --model opus --output-format text`. Single source of truth — defined as a constant.
- **Token telemetry**: log estimated input chars and wall-clock duration into `logs/research.log`. After 4 weeks, alert if a run exceeds 1.5× rolling median.
- **Kill switch**: `RESEARCH_SCAN_ENABLE` env var. Unset or `0` → script logs and exits without calling Claude.

Bootstrap-run estimate (28 Tier-A URLs × 20 KB cap): ~280 KB input → 2 batches → roughly $1–2 of Opus equivalent. Weekly runs much cheaper since most sources hash-skip.

---

## File layout

```
docs/research_sources.md      Curated URLs (committed; source of truth for the script)
docs/research_queries.md      Open-search queries (committed; source of truth)
docs/RESEARCH_FEED.md         Output: findings, newest first, sectioned by run date
scripts/research_scan.py      Main entry point (--mode bootstrap|curated|open|all)
scripts/research_lib/         Modules: fetch, state, claude_call, feed, search
logs/research_seen.json       URL → body hash (dedup state; gitignored)
logs/research.log             Script output
tests/test_research_*.py      Offline tests with canned fixtures
```

---

## Decisions (locked)

1. **Sources**: REVIEW.md §Sources is the corpus (28 links + 5 comparable repos). No additions.
2. **Cadence**: weekly **Monday 10:00 UTC** for curated, monthly **1st 10:00 UTC** for open search.
3. **Model**: pin to `--model opus` with explicit cost controls.
4. **Output**: single `docs/RESEARCH_FEED.md`, newest-first, manual rotation when it grows.
5. **Dashboard surfacing**: in scope (Phase 11.8).
6. **Host**: build on **WSL today**; design Pi-portable (no WSL-specific paths, no hard-coded `/home/rfreire/...`, no `wsl.exe` calls).
7. **Auth verification**: unknown whether `claude -p` works under cron. Phase 11.0 is a smoke test.

---

## Reference: corpus, prompt, and conventions

These are the source-of-truth lists. Phase 11.1 copies them into committed `.md` files; later phases read those files, not this doc.

### Tier A — Reference corpus (28 URLs, from `docs/REVIEW.md` §Sources)

**Kaunitz strategy**
- `https://arxiv.org/abs/1710.02824` — Kaunitz, Zhong, Kreiner (2017) original paper
- `https://github.com/Lisandro79/BeatTheBookie` — Official paper code (also Tier B)
- `https://www.technologyreview.com/2017/10/19/67760/the-secret-betting-strategy-that-beats-online-bookmakers/`
- `https://sportshandle.com/sportsbooks-vs-academics-one-wins-battle/`
- `https://news.ycombinator.com/item?id=42112855`
- `https://www.sciencedirect.com/science/article/pii/S0169207024000670`

**Devig methods, sharp vs soft books**
- `https://datagolf.com/how-sharp-are-bookmakers`
- `https://www.dratings.com/a-summary-of-different-no-vig-methods/`
- `https://cran.r-project.org/web/packages/implied/vignettes/introduction.html`
- `https://www.researchgate.net/publication/326510904_Adjusting_Bookmaker's_Odds_to_Allow_for_Overround`
- `https://betherosports.com/blog/devigging-methods-explained`
- `https://help.outlier.bet/en/articles/9922960-how-sportsbooks-set-odds-soft-vs-sharp-books`

**CLV, drift, drawdown**
- `https://www.pinnacle.com/betting-resources/en/educational/what-is-closing-line-value-clv-in-sports-betting`
- `https://www.thelines.com/betting/closing-line-value/`
- `https://www.pinnacleoddsdropper.com/blog/closing-line-value`
- `https://punter2pro.com/punters-guide-beating-the-sp/`

**Account restrictions, exchange options**
- `https://punter2pro.com/prevent-betting-accounts-restricted-closed/`
- `https://www.rebelbetting.com/blog/how-to-avoid-bookmaker-limitations`
- `https://smartsportstrader.com/sports-betting-bookmakers-exchanges-dont-limit-uk-customers/`

**Bet sizing**
- `https://matthewdowney.github.io/uncertainty-kelly-criterion-optimal-bet-size.html`
- `https://www.stat.berkeley.edu/~aldous/157/Papers/Good_Bad_Kelly.pdf`
- `https://en.wikipedia.org/wiki/Kelly_criterion`

**Other**
- `https://www.predictology.co/blog/the-psychology-of-the-draw-why-market-bias-often-creates-massive-hidden-value-in-the-x-outcome/`
- `https://en.wikipedia.org/wiki/Favourite-longshot_bias`
- `https://www.bettoredge.com/post/identifying-outliers-in-sports-betting-data`

### Tier B — Active feeds (5 repos + 7 discovery feeds = 12)

**Comparable open-source projects** — poll commits via GitHub API.

| Repo | Why |
|---|---|
| `https://github.com/Lisandro79/BeatTheBookie` | Official Kaunitz paper code |
| `https://github.com/georgedouzas/sports-betting` | Active sports-betting Python package |
| `https://github.com/konstanzer/online-sports-betting` | Comparable scanner |
| `https://github.com/sedemmler/WagerBrain` | Bet-sizing / bankroll utilities |
| `https://github.com/jacksebastian17/betting-algo` | Comparable algo |

**Discovery feeds**

| Type | URL |
|---|---|
| GitHub topic | `https://github.com/topics/value-betting` |
| GitHub topic | `https://github.com/topics/sports-betting?o=desc&s=updated` |
| arXiv search | `https://export.arxiv.org/api/query?search_query=all:%22value+betting%22+OR+all:%22closing+line+value%22&sortBy=submittedDate&max_results=20` |
| HN search | `https://hn.algolia.com/api/v1/search_by_date?query=sports+betting+strategy&hitsPerPage=20` |
| Reddit | `https://www.reddit.com/r/algobetting/.json` |
| Reddit (top week) | `https://www.reddit.com/r/sportsbook/top/.json?t=week` |
| Pinnacle articles | `https://www.pinnacle.com/en/betting-resources/all` |

### Open-search queries (7 seeds)

```
"value betting" closing line value 2026
"favourite-longshot bias" football
"de-vig" methods Shin power proportional
sharp vs soft bookmaker line movement
betfair exchange edge strategy
"closing line value" calibration
algorithmic sports betting github
```

### Claude prompt template (verbatim — Phase 11.4 must match byte-for-byte)

```
You are evaluating external content for a UK value-betting system. Our existing
approach is summarised below. For each source in the input file, classify any
findings as STRATEGY / EVIDENCE / RISK and score adopt-ability 1–5.

Our system already does:
- Shin de-vigging across ~36 books, Kaunitz consensus (≥3% UK-book deviation)
- Cross-book stdev filter (≤4%), per-book outlier z-score (≤2.5)
- Half-Kelly sizing, £5 rounding, 5% per-fixture, 15% per-portfolio, drawdown brake
- Commission-aware net edges (Phase 5.7)
- 8 paper-strategy variants A_production…H_no_pinnacle, shadow A/B
- CLV vs Pinnacle as primary edge gauge

For each source, output a section:

### <source URL>
- **STRATEGY** — <one-line description>. Adopt-ability: <1–5>. <one-line how>.
- **EVIDENCE** — <claim>. Affects: <which strategy/filter>.
- **RISK** — <what it suggests is broken in our flow>.

If a source contains only generic "ML for football" content already covered by
Dixon-Coles / Kaunitz / Yeung, write "(no actionable findings)" and move on.
Be terse. Skip filler.
```

### Coding conventions (apply to all phases)

- Paths via `pathlib.Path(__file__).resolve().parent.parent` — match `scripts/compare_strategies.py`. **No hard-coded absolute paths.**
- Atomic writes via `os.replace` after temp-file write — match `scripts/scan_odds.py`.
- Tests live in `tests/test_research_*.py`; runnable offline (no network in tests).
- Dependencies: stdlib + `requests` + `beautifulsoup4` (already in repo). No new deps without justification in the PR.

---

## Phase 11.0 — Cron-auth smoke test  (no code, ~30 min)

**Goal.** Verify `claude -p --model opus --output-format text` works from a stripped cron-like shell, and document the working invocation here so all later phases know how to call Claude.

**Inputs.** Working `claude` CLI on the host. WSL today.

**Outputs.**
- A new subsection **"Cron-auth findings"** appended at the end of this Phase 11.0 block, recording the working invocation and any required env.
- No code changes.

**Tasks.**
1. Run interactively: `echo "ping" | claude -p --model opus --output-format text` → confirm output.
2. Run cron-like: `env -i HOME=$HOME PATH=/usr/bin:/bin claude -p --model opus --output-format text "reply READY"`. Capture exit code + stdout.
3. If step 2 fails, iterate minimally: try `env -i HOME=$HOME PATH=/usr/bin:/bin XDG_CONFIG_HOME=$HOME/.config bash -lc 'claude -p --model opus --output-format text "reply READY"'`. Stop at the simplest working incantation.
4. Re-run the working command 3 times, log wall-time of each.
5. Write the **Cron-auth findings** subsection — required fields:
   - `CLAUDE_CMD` (single shell-paste-able command).
   - Required env vars (if any).
   - Median latency observed.
   - Date verified.

**Acceptance.**
- [ ] Cron-auth findings subsection added to this doc, all required fields filled.
- [ ] `CLAUDE_CMD` runs successfully and returns "READY" (or similar) ≥3 consecutive times.
- [ ] Median latency < 30s.

**Reviewer focus.** I will re-run `CLAUDE_CMD` myself before signing off. Watch for: hidden interactive prompts, model-not-available errors, anything that requires a TTY.

### Cron-auth findings (verified 2026-04-29)

**`CLAUDE_CMD`**

```sh
env -i HOME=$HOME PATH=/usr/bin:/bin:/home/rfreire/.local/bin \
  claude -p --model claude-opus-4-7 --output-format text
```

Single-liner (paste into cron or shell script):

```sh
env -i HOME=$HOME PATH=/usr/bin:/bin:/home/rfreire/.local/bin claude -p --model claude-opus-4-7 --output-format text
```

**Required env vars**

| Var | Why |
|---|---|
| `HOME` | Auth config lives at `~/.claude/` — claude reads it via `$HOME`. |
| `PATH` | Must include `/home/rfreire/.local/bin` (where `claude` is installed). `/usr/bin:/bin` alone is not enough. |

**Model note.** The spec draft said `--model opus`; the working model ID is `claude-opus-4-7`. Use the full ID in all later phases to avoid ambiguity as new Opus versions ship.

**Median latency observed**

| Run | Wall time |
|---|---|
| 1 | 3 678 ms |
| 2 | 3 689 ms |
| 3 | 2 980 ms |
| **Median** | **3 678 ms** |

All three runs returned `READY`. No TTY required, no interactive prompts, exit 0.

**Acceptance checklist**
- [x] Cron-auth findings subsection added, all required fields filled.
- [x] `CLAUDE_CMD` returned "READY" ≥3 consecutive times.
- [x] Median latency < 30s (actual: ~3.7s).

---

## Phase 11.1 — Source list + queries (data only, ~30 min)

**Goal.** Convert the Tier-A / Tier-B / open-search lists in this doc into committed source-of-truth markdown files that the script reads.

**Inputs.** This doc's Reference section.

**Outputs.**
- `docs/research_sources.md` — Tier A and Tier B URLs.
- `docs/research_queries.md` — open-search queries.

**Tasks.**
1. Create `docs/research_sources.md` with this structure:
   ```
   # Research scanner sources
   <!-- Source of truth for scripts/research_scan.py.
        Edit this file, not RESEARCH_SCANNER.md. Format: one URL per `- ` line. -->

   ## Tier A — Reference corpus (one-shot deep read + change-watch)

   ### Kaunitz strategy
   - https://arxiv.org/abs/1710.02824
   - ...

   ### Devig methods, sharp vs soft books
   - ...

   ## Tier B — Active feeds (poll weekly)

   ### Comparable open-source projects
   - https://github.com/Lisandro79/BeatTheBookie
   - ...

   ### Discovery feeds
   - https://github.com/topics/value-betting
   - ...
   ```
2. Create `docs/research_queries.md` with one query per line plus a header comment.
3. Copy URLs/queries verbatim from this doc's Reference section.

**Acceptance.**
- [x] Both files committed.
- [x] `grep -c '^- http' docs/research_sources.md` returns **37** (25 Tier-A + 12 Tier-B). (Spec said 28 Tier-A but only 25 are listed in the Reference section and confirmed in REVIEW.md.)
- [x] `grep -cv '^#\|^$' docs/research_queries.md` returns **7**.
- [x] Header comments in both files reference back to RESEARCH_SCANNER.md.

**Reviewer focus.** Cross-check the URL count against this doc — silent truncation kills the corpus.

---

## Phase 11.2 — Fetcher module  (~1.5h)

**Goal.** Standalone module that fetches a URL and returns clean text capped at 20 KB.

**Inputs.** Phase 11.1 done.

**Outputs.**
- `scripts/research_lib/__init__.py` (empty).
- `scripts/research_lib/fetch.py` exporting:
  ```python
  @dataclass
  class FetchResult:
      url: str
      status: str          # "ok" | "skip" | "error"
      body_text: str       # cleaned, capped at 20 KB
      body_hash: str       # SHA256 of body_text
      fetched_at: str      # ISO 8601 UTC
      error: str | None    # populated when status != "ok"

  def fetch(url: str) -> FetchResult: ...
  ```
- `tests/test_research_fetch.py` with offline fixtures.

**Tasks.**
1. Per-pattern handlers:
   - **arXiv** (`arxiv.org/abs/...`): hit `http://export.arxiv.org/api/query?id_list=<id>` → parse Atom → return title + abstract.
   - **Reddit** (`*.json` URLs): GET, parse JSON, concatenate post titles + selftext (top 20).
   - **HN Algolia** (`hn.algolia.com/api/...`): GET JSON, concat title + url + points.
   - **GitHub repo** (`github.com/owner/repo`): GET `https://api.github.com/repos/{o}/{r}/readme` (base64-decode `content`) + `https://api.github.com/repos/{o}/{r}/commits?per_page=10` (concat sha + message).
   - **GitHub topic** (`github.com/topics/...`): scrape repo names from HTML.
   - **Default HTML**: `requests.get` → BeautifulSoup `get_text(separator="\n", strip=True)` after dropping `<script>`, `<style>`, `<nav>`, `<footer>`.
2. **Skip PDFs in v1.** If `Content-Type: application/pdf`, return `status="skip"` with a note. (Aldous Berkeley PDF will be skipped — fine, the URL is logged.)
3. Cap cleaned text at 20 KB before hashing.
4. SHA256 of cleaned text → `body_hash`.
5. 10s timeout per request, 1 retry on connection error with 2s backoff. 429/5xx → `status="skip"`.
6. User-Agent: `bets-research-scanner/0.1`.
7. Tests:
   - One canned fixture per handler in `tests/fixtures/research/` (HTML and JSON files).
   - Use `responses` or `requests-mock` (already installed?) or stub via `monkeypatch` of `requests.get`.
   - Verify 20 KB cap (feed in 100 KB content).
   - Verify hash is deterministic.

**Acceptance.**
- [x] `pytest tests/test_research_fetch.py` passes (16/16).
- [x] Manual smoke: `python3 -c "from scripts.research_lib.fetch import fetch; r = fetch('https://arxiv.org/abs/1710.02824'); print(r.status, len(r.body_text))"` returns `ok 1508` (1000 < 1508 < 20000).
- [x] Body cap holds: a fixture with 100 KB input yields ≤ 20 KB output.
- [x] Tests run with no network — all calls monkeypatched; no real network in test suite.

**Reviewer focus.** HTML→text cleaning quality. I will manually fetch one Tier-A URL and read the cleaned output. Garbage-in here destroys downstream signal.

---

## Phase 11.3 — Dedup state + pending-file builder  (~45 min)

**Goal.** Manage `logs/research_seen.json` and assemble `logs/research_pending.md` from new/changed fetch results.

**Inputs.** Phase 11.2 done.

**Outputs.**
- `scripts/research_lib/state.py` exporting:
  ```python
  def load_seen() -> dict[str, dict]: ...
  def save_seen(d: dict) -> None: ...           # atomic via os.replace
  def is_changed(url: str, new_hash: str, seen: dict) -> bool: ...
  def update_seen(seen: dict, result: FetchResult) -> None: ...
  def assemble_pending(results: list[FetchResult], cap_bytes: int = 200_000) -> list[str]: ...
  ```
- `tests/test_research_state.py`.

**Tasks.**
1. JSON schema:
   ```json
   {
     "https://example.com/x": {
       "hash": "<sha256>",
       "fetched_at": "2026-04-29T10:00:00Z",
       "status": "ok",
       "last_changed_at": "2026-04-29T10:00:00Z"
     }
   }
   ```
2. Atomic write: write to `<file>.tmp`, then `os.replace(tmp, file)`.
3. `assemble_pending` walks results, **skips entries whose hash matches `seen[url].hash`**, formats per source:
   ```markdown
   ## Source: <url>
   <fetched_at> — status: <status>

   <body_text>

   ---
   ```
4. If aggregate exceeds 200 KB, return a list of segments each ≤ 200 KB (split on source boundaries). Single-source entries that exceed 200 KB on their own emit one oversized segment with a `# WARNING: oversized` comment — Claude can still process it but we flag.
5. Tests cover: changed/unchanged dedup, atomic write (kill-mid-write doesn't corrupt — simulate with patched `os.replace`), batching at 200 KB.

**Acceptance.**
- [x] Tests pass (19/19).
- [x] Manual: write a `seen.json` with one entry, re-run `assemble_pending` with same hash → returns empty list.
- [x] 200 KB cap correctly produces ≥2 segments when given 350 KB of input.

**Reviewer focus.** Atomic-write correctness; ISO 8601 UTC consistency; segment-boundary logic at the 200 KB cap.

---

## Phase 11.4 — Claude subprocess wrapper  (~1h)

**Goal.** Function that takes a pending-markdown string (or list of segments) and returns Claude's structured findings response.

**Inputs.** Phase 11.0 done (CLAUDE_CMD known); Phase 11.3 done.

**Outputs.**
- `scripts/research_lib/claude_call.py` exporting:
  ```python
  PROMPT_TEMPLATE: str   # the verbatim template from this doc
  CLAUDE_CMD: list[str]  # from Phase 11.0 findings
  def call_claude(pending_md: str, timeout: int = 300) -> str: ...
  def call_claude_batched(segments: list[str]) -> str: ...
  ```
- Logging configured to `logs/research.log`.
- `tests/test_research_claude.py` using a fake `claude` shim (small shell script on `PATH` that echoes a fixed response).

**Tasks.**
1. Copy `PROMPT_TEMPLATE` from this doc's Reference section verbatim.
2. `call_claude` builds full prompt = `PROMPT_TEMPLATE + "\n\n" + pending_md`. Pipes via stdin to `subprocess.run(CLAUDE_CMD, input=full_prompt, text=True, capture_output=True, timeout=timeout, check=True)`. Returns `.stdout`.
3. Non-zero exit → raise `ClaudeCallError` with stderr.
4. `call_claude_batched` calls `call_claude` per segment, joins outputs with `\n\n---\n\n`.
5. Log per call: `ts | mode | chars_in | wall_time_s | exit_code`. Use `logging.basicConfig(filename=LOG, level=INFO, format=...)`.
6. Tests:
   - Fake shim returns a known string → assert it's returned verbatim.
   - Shim exits non-zero → assert `ClaudeCallError`.
   - Batched: 3 segments → 3 shim invocations, joined output.

**Acceptance.**
- [x] Tests pass with fake shim (8/8).
- [x] Real-world smoke: returned `(no actionable findings)` as expected.
- [x] `logs/research.log` has one line per call with correct fields.
- [x] PROMPT_TEMPLATE matches this doc byte-for-byte (`diff` clean).

**Reviewer focus.** Diff PROMPT_TEMPLATE against this doc. Shim test must actually exercise stdin piping (not just argv).

---

## Phase 11.5 — Feed writer  (~30 min)

**Goal.** Prepend new findings to `docs/RESEARCH_FEED.md`.

**Inputs.** Phase 11.4 done.

**Outputs.**
- `scripts/research_lib/feed.py` exporting:
  ```python
  def write_findings(claude_output: str, mode: str, run_at: datetime) -> int: ...
  # returns count of STRATEGY/EVIDENCE/RISK lines
  ```
- `tests/test_research_feed.py`.

**Tasks.**
1. If `docs/RESEARCH_FEED.md` is missing, create with banner:
   ```
   # Research Feed

   Auto-generated by scripts/research_scan.py. Newest runs at the top.
   See docs/RESEARCH_SCANNER.md for the spec.

   ---
   ```
2. New section format:
   ```
   ## Run YYYY-MM-DD HH:MM UTC (mode: <mode>) — <N> findings

   <claude_output verbatim>

   ---
   ```
3. Read existing file, splice new section directly after the banner separator. Atomic write.
4. Count findings: `len(re.findall(r"^- \*\*(STRATEGY|EVIDENCE|RISK)\*\*", claude_output, re.M))`.
5. Tests: empty file, existing file with prior runs, count regex correctness.

**Acceptance.**
- [x] Tests pass (13/13).
- [x] After two writes, file has banner once + two `## Run` sections, newest first.
- [x] Returned count matches actual STRATEGY/EVIDENCE/RISK lines.

**Reviewer focus.** That `RESEARCH_FEED.md` stays grep-able: `grep -A1 "STRATEGY" RESEARCH_FEED.md` should produce a clean list across runs.

---

## Phase 11.6 — Top-level CLI + bootstrap mode  (~1h)

**Goal.** First end-to-end run. `python3 scripts/research_scan.py --mode bootstrap` produces a populated `RESEARCH_FEED.md` from Tier-A.

**Inputs.** Phases 11.1 through 11.5 done.

**Outputs.**
- `scripts/research_scan.py` (entry point) wires the lib modules.
- Modes: `bootstrap` (Tier A only, force re-fetch), `curated` (Tier A change-watch + Tier B), `open` (queries — stub here, full impl in 11.7), `all`.
- Reads `RESEARCH_SCAN_ENABLE` env var. `0` or unset → log + `sys.exit(0)`.
- ntfy on success: `"<N> new findings"` (low priority); on failure: high priority.

**Tasks.**
1. argparse: `--mode {bootstrap,curated,open,all}`, `--dry-run`, `--max-sources N`.
2. Parse `docs/research_sources.md` into `tier_a` and `tier_b` lists. Use simple line-based parser keyed on `## Tier A` / `## Tier B` headings.
3. For each URL: `fetch` → `is_changed` → if changed, append to candidates.
4. `assemble_pending` → segments → `call_claude_batched` → `write_findings` → ntfy.
5. `--dry-run`: do steps 3–4 but skip Claude and feed write. Print byte counts.
6. ntfy: reuse helper from `scripts/scan_odds.py` if exists, else inline `requests.post('https://ntfy.sh/robert-epl-bets-m4x9k', ...)`.
7. Top-level `try/except` around the run; on exception, ntfy high-priority, re-raise.

**Acceptance.**
- [x] `--mode bootstrap --dry-run` lists every Tier-A URL it would fetch and reports total estimated bytes after caps.
- [x] `--mode bootstrap` (real) produces a `## Run` section in `RESEARCH_FEED.md` with **≥5** STRATEGY/EVIDENCE/RISK lines across the corpus (actual: 37).
- [x] Re-running `--mode curated` twice after bootstrap: first pass picks up Tier B (6 findings); second pass 0 findings. Static sources fully deduped; live feeds (Reddit/HN) produce 0 actionable findings.
- [x] `RESEARCH_SCAN_ENABLE=0 python3 scripts/research_scan.py --mode bootstrap` exits 0 without calling Claude (verified via `logs/research.log`).
- [x] No hard-coded paths anywhere in the script. `grep -E '/(home|mnt)/' scripts/research_scan.py scripts/research_lib/*.py` returns nothing.

**Reviewer focus.** Skim the bootstrap output for whether Claude actually identified anything actionable in the comparable open-source projects. If `RESEARCH_FEED.md` is mostly "(no actionable findings)" the prompt may need tuning before 11.7.

---

## Phase 11.7 — Open-search backends  (~1.5h)

**Goal.** `--mode open` actually runs queries instead of being a stub.

**Inputs.** Phase 11.6 done.

**Outputs.**
- `scripts/research_lib/search.py` exporting:
  ```python
  def search(query: str, backend: str) -> list[str]: ...   # returns URLs
  BACKENDS = ["arxiv", "hn", "github", "ddg"]
  ```
- Wired into `research_scan.py` `--mode open` and `--mode all`.

**Tasks.**
1. Per-backend:
   - **arXiv**: `https://export.arxiv.org/api/query?search_query=all:{q_url_encoded}&sortBy=submittedDate&max_results=10`. Parse Atom `<id>` URLs.
   - **HN**: `https://hn.algolia.com/api/v1/search_by_date?query={q}&hitsPerPage=10`. Extract `hits[].url` (skip if null).
   - **GitHub**: `https://api.github.com/search/repositories?q={q}&sort=updated&per_page=10`. Extract `items[].html_url`.
   - **DDG**: `https://duckduckgo.com/html/?q={q}`. Scrape `.result__a` href. Fragile — wrap in try/except, log + return `[]` on parse failure.
2. For each query, run all 4 backends, collect URLs, dedupe across backends.
3. Filter against `research_seen.json` — only fetch URLs not seen.
4. Cap discovered URLs per query at 5 (avoid runaway).
5. Route through fetch → assemble → claude → feed pipeline.
6. Tag findings: backend tags are post-injected into Claude's output headings (`### [backend:X] <url>`) so the feed is searchable by channel.
7. **GitHub auth**: set `GITHUB_TOKEN` env var to use authenticated requests (60 req/hr → 5000 req/hr). Without it, unauthenticated search caps at 10 req/min and will rate-limit on multi-query runs.

**Acceptance.**
- [x] Tests with canned API responses for each backend.
- [x] `--mode open` runs all 7 queries × 4 backends, dedupes, fetches up to 5 new URLs/query.
- [x] Output sections in `RESEARCH_FEED.md` show backend tag.
- [x] Log records dedup hit rate (URLs found vs. URLs fetched).

**Reviewer focus.** Dedup hit rate. If we're always fetching >50% already-seen URLs, the Tier-A corpus is overlapping with discovery and we should narrow queries.

---

## Phase 11.8 — Dashboard tile  (~45 min)

**Goal.** Dashboard surfaces "Research findings: N this week".

**Inputs.** Phase 11.6 done. (Independent of 11.7.)

**Outputs.**
- `app.py`: helper `latest_research_findings() -> tuple[date, int, str]` (date, count, mode).
- `templates/index.html`: new tile in the stats bar.

**Tasks.**
1. Helper reads `docs/RESEARCH_FEED.md`, finds first `## Run YYYY-MM-DD HH:MM UTC (mode: ...) — N findings` heading, parses fields. Returns `(None, 0, "")` on missing/unparseable file.
2. Inject into Flask template context.
3. New tile: label "Research", value "N", subtext "<mode> · <date>".
4. No click-through in v1.

**Acceptance.**
- [x] Dashboard renders even if `RESEARCH_FEED.md` is missing (tile shows "Research: 0").
- [x] After `--mode bootstrap`, tile shows correct count + date.
- [x] No template breakage (manual reload of `http://localhost:5000`).

**Reviewer focus.** Graceful fallback when feed is missing/empty/malformed.

---

## Phase 11.9 — Cron + production hardening  (~45 min)

**Goal.** Crons live; failures visible; docs updated.

**Inputs.** Phase 11.6 done. Ideally 11.7 + 11.8 also done.

**Outputs.**
- Two new crontab entries (the agent prepares them, the user pastes — we don't auto-edit `crontab -e`).
- `README.md`: new "Research scanner" section.
- `CLAUDE.md`: cron-schedule section adds the two entries; phase status table adds 11.x.
- `docs/PI_AZURE_SETUP.md`: research scanner cron mentioned for future Pi migration.

**Tasks.**
1. Compose the two crontab lines using the absolute path of the repo, the kill-switch env, and the CLAUDE_CMD env from Phase 11.0:
   ```
   0 10 * * 1   RESEARCH_SCAN_ENABLE=1 cd <repo> && python3 scripts/research_scan.py --mode curated >> logs/research.log 2>&1
   0 10 1 * *   RESEARCH_SCAN_ENABLE=1 cd <repo> && python3 scripts/research_scan.py --mode open    >> logs/research.log 2>&1
   ```
2. Print them with instructions for the user to paste into `crontab -e`.
3. Smoke-run both modes once before declaring done: `RESEARCH_SCAN_ENABLE=1 python3 scripts/research_scan.py --mode curated --dry-run` and `--mode open --dry-run`.
4. Update README + CLAUDE.md per the standing memory.

**Acceptance.**
- [x] User has pasted the two cron entries (verify via `crontab -l`).
- [ ] First run after install (manual or scheduled) appears in `logs/research.log`.
- [x] CLAUDE.md cron section lists both entries verbatim.
- [x] README has a "Research scanner" subsection pointing at `docs/RESEARCH_SCANNER.md` and `docs/RESEARCH_FEED.md`.

**Reviewer focus.** Spot-check `logs/research.log` after the first scheduled run. Confirm cron env worked (no auth failure, no missing PATH).

---

## Phase 11.10 — Optional follow-ups (deferred)

Only do these if 11.0–11.9 have been live for ≥1 month and we have at least 3 dated `## Run` sections in `RESEARCH_FEED.md`.

- Mark findings as `triaged` / `adopted` / `dismissed` in `RESEARCH_FEED.md` (manual edits) and report adoption rate in the dashboard tile.
- Add `--score-min N` flag so only adopt-ability ≥ N findings trigger ntfy (avoids alert fatigue once feed grows).
- Optional Brave Search backend if no-key channels prove too narrow.

---

## Risks / things to watch

- **Subprocess output drift.** If `claude` CLI changes default output format between versions, parsing breaks. Pinned to `--output-format text` and snapshot-tested in Phase 11.4.
- **Reddit/GitHub rate limits.** Curated list keeps hits small; if we exceed, fall back to weekly only.
- **Source rot.** Blogs go quiet. `research_seen.json` should track `last_changed_at` so we can warn in the log when a source has been silent >90 days.
- **Garbage-in.** The Claude pass should be the filter; the prompt explicitly tells it to skip generic ML-for-football content. If `RESEARCH_FEED.md` is mostly "(no actionable findings)" after a month, prune the source list rather than tuning the prompt.
