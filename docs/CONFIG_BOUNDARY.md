# Config vs Code vs DB

Working agreement for where each kind of fact lives. Apply when introducing a new constant, table, or config key, and when reviewing PRs that move data between layers.

## The rule

| Fact type | Home | Why |
|---|---|---|
| Static reference data — slow-moving, non-developer editable | `config.json` (JSON) | Ships with repo → reaches Pi via `git pull`. One source for everything that describes a *thing* (a book, a league). |
| Tuning constants and behavioural configs — changes affect betting decisions | Code (`src/`) | PR review is the correct friction. Version-controlled, branchable, blame-able. |
| Time-varying observed signals — recomputed on a cadence | DB (Azure SQL) | Joined into queries; written by jobs; read by dashboard and scanner. |
| Per-event historical snapshots — what was true *at decision time* | DB (Azure SQL) | Reproducibility of past decisions. Never overwritten. |

A fact lives in **one** place. If two layers carry the same fact, drift is guaranteed. The unused `books.commission_rate` column we found 2026-05-02 is the canonical example of what this rule prevents.

## Current assignment

| Item | Home | Notes |
|---|---|---|
| `leagues` (key, label, min_books, fdco_code) | `config.json` | Already moved (M.1). |
| `canary_league` | `config.json` | Already moved. |
| `books` (commission_rate, type, region, license, label) | `config.json` | **Move pending** — see issue. Replaces `BOOK_COMMISSIONS`, `UK_LICENSED_BOOKS`, `EXCHANGE_BOOKS`. |
| `STRATEGIES` (16 paper variants) | Code (`src/betting/strategies.py`) | Each variant is a contract with `evaluate_strategy()`; new variants almost always need engine changes too. JSON would create the illusion that a new variant is shippable without code review. |
| `MAX_DISPERSION`, `OUTLIER_Z_THRESHOLD`, `MIN_EDGE`, `MODEL_MIN_EDGE`, `SPORT_MIN_EDGE` | Code | Tuning knobs that move money. PR review > JSON edit. |
| `SHARPNESS_WEIGHTS` (per-book) | Code | Tuning weight today. Moves to DB if/when derived from `book_skill` (B.1+). |
| `UNDERSTAT_NAME_ALIAS` | Code | Typos here silently mismatch teams. Friction is wanted. |
| `book_skill` rows (computed scores, biases, drift) | DB | Already there. |
| Per-bet snapshots (`bets.commission_rate`, `bets.odds`, `bets.consensus`, etc.) | DB | Already there. Distinct from the "current rate" question — these are historical record. |

## Policy for new facts

When adding a constant, table, or config key, ask in order:

1. **Does it change at runtime / on a cadence?** → DB.
2. **Does changing it affect the betting decision logic, model selection, or risk math?** → Code.
3. **Is it data that *describes a thing* (a book, a league, a sport)?** → `config.json`.
4. **Per-event record of what was true at the moment of a decision?** → DB (separate from any "current value" of the same fact).

If two cells claim it, the answer is wrong — keep narrowing until exactly one fits.

## Anti-patterns to reject in review

- A DB column whose value is set only by code defaults and never read (the `books.commission_rate` pattern).
- A JSON key duplicated by an in-code constant "for fallback" beyond a single hardcoded last-resort default in the loader.
- A "tuning JSON" that lets non-developers change numbers that affect betting decisions.
- A `STRATEGIES` entry in JSON — strategies are behaviour, not config.
