# Steam Review Intelligence — Architecture

## Overview

A data pipeline that pulls real review data from Steam's public APIs and turns it
into clean, validated tables for analysis. Three stages with strict one-directional
flow and assertions at two distinct boundaries.

- **Fetcher**: talks to Steam, saves raw responses verbatim, changes nothing
- **Cleaner**: transforms raw JSON → tidy tables (no network access)
- **Validator**: the data contract — asserts invariants before data is promoted

Status: Phase 2, project 2. Fetcher in progress (request layer + identity guard
built; pagination, checkpointing, and orchestration pending). Cleaner and
validator not yet started.

## Data Flow

```
game_list.json            (committed, hand-curated — 50 games)
  → fetcher.py            [BUILT: requests w/ retry+backoff, identity guard]
                          [PENDING: cursor pagination, checkpoint/resume]
  → data/raw/reviews/{app_id}_reviews.jsonl      (gitignored, append-per-batch)
    data/raw/metadata/{app_id}_metadata.json     (gitignored)
    data/raw/fetch_manifest.json                 (progress + audit log)
  → cleaner.py            [PENDING] raw → tidy DataFrame, dedup, encoding, features
  → validation/          [PENDING] assert schema + invariants (the data contract)
  → data/processed/reviews.parquet + metadata.parquet   (gitignored)
  → analysis notebooks    [PENDING] read processed only — never touch raw
```

Every arrow goes forward only. Notebooks never reach back to raw. That one rule
is what keeps the pipeline from becoming spaghetti.

## Cross-Cutting Design Decisions

These are project-wide choices that don't belong to any single file.

- **Three modules, three jobs**: fetch = I/O, clean = transform, validate = assert.
  Each is testable in isolation. Mixing them is the classic data-pipeline mess.
- **Two validation boundaries, deliberately separate**: the _identity guard_ is a
  precondition that lives in the fetcher (it decides whether to even write a
  file); the _data contract_ lives in `validation/` (it gates promotion to
  parquet). Conflating "is this the right game" with "is this table well-formed"
  is the trap — they fail differently and fix differently.
- **Raise vs return contract**: Steam unreachable after retries → raise
  `SteamAPIError` (stop the run, resume later). Expected "this game has no data"
  → return `None`/empty for the caller to handle. Exceptions are for the
  exceptional; anticipated outcomes are returned, not thrown.
- **At-least-once writes**: the fetcher writes a batch of reviews to disk _before_
  recording its cursor in the manifest. A crash between the two re-fetches that
  batch on resume (a duplicate), never loses it. The fetcher guarantees no loss;
  the cleaner guarantees no duplicates (dedup on `recommendationid`). Neither has
  to be perfect alone; together they are correct.
- **Raw data is immutable and gitignored** — large and fully reproducible from the
  pipeline. Only `game_list.json` is committed, because its curation _is_ a design
  decision.
- **JSONL for raw reviews** (one JSON object per line) — supports append-per-batch
  with no read/parse/rewrite, and a crash leaves at most one half-written line
  (trivially detectable) instead of a corrupted array.
- **Parquet for processed data** (not CSV) — preserves types, compresses, and reads
  as a production signal.
- **Sample mode, default ON** — shallow full-coverage runs (~500 reviews × all 50
  games, ~9 min) for building and testing; one switch flips to the full ~1.5 h
  run. Default-on so a multi-hour fetch is never triggered by accident.
- **Review-level vs game-level kept as two tables** — reviews (N ≈ 500k) and games
  (N = 50) join only in notebooks. The epistemics (price-tier claims valid at
  review level; game-sample claims hedged to "in our sample") fall out of the
  schema instead of being something to remember.

## Files

### `pipeline/config.py`

- **Class**: `Settings` (frozen dataclass); module-level singleton `settings`
- **Purpose**: single source of truth — endpoints, query params, rate-limit
  policy, file paths. No logic.
- **Key fields**: `reviews_endpoint`, `appdetails_endpoint`, `review_filter`,
  `review_language`, `purchase_type`, `num_per_page`, `reviews_per_game_cap`,
  `sample_mode`, `request_delay_seconds`, `max_retries`, `backoff_factor`,
  `identity_match_threshold`, plus all `*_path` / `*_dir` paths
- **Derived**: `effective_reviews_cap` (read-only property)
- **Depended on by**: everything

## Decisions Log

- `frozen=True` — config is read, never written; a stray mutation crashes loudly
  (FrozenInstanceError) instead of silently corrupting a shared value
- All paths anchored to `PROJECT_ROOT` via `Path(__file__)` — pipeline runs
  identically regardless of the current working directory
- Paths derive from a module constant, not from sibling fields — sidesteps the
  frozen-dataclass "field can't reference field" issue without `__post_init__`
- `filter=recent` — walks the full review set in creation-date order; avoids the
  helpfulness-sort cursor-loop bug and yields temporal ordering for free
- `purchase_type=all` — the API default `steam` silently excludes key-activated
  owners, a real sampling bias
- `language=all` — some games (e.g. Overwatch 2) are majority non-English
- `num_per_page=80` — 100 is the max but has a documented early-truncation bug for
  some apps; completeness matters more than ~20% fewer requests
- Descriptive `User-Agent` — the default scripting agent gets throttled
- `sample_mode` default `True` — a full fetch must be a deliberate act
- `effective_reviews_cap` is a property, not a field — it _selects_ between two
  settings (config's job), which is derived config, not behavior

### `pipeline/fetcher.py`

- **Public surface (built so far)**: `SteamAPIError`, `_get_json`, `GuardResult`,
  `fetch_app_details`, `check_identity`, `_normalize_name`, `_name_similarity`
- **Purpose**: all Steam API I/O. Polite, robust single requests; per-game identity
  verification. (Cursor pagination, `query_summary` capture, and checkpoint/resume
  are the next pieces.)
- **Key methods**: `_get_json()` — one GET with retry/backoff, the single home of
  retry logic; `check_identity()` — name-match guard returning a `GuardResult`
- **Depends on**: `config.py`, `requests`
- **Depended on by**: `main.py` (pending), `tests/test_fetcher.py`

## Decisions Log

- Retry/backoff lives in exactly one function (`_get_json`) — every other piece
  calls it and never re-implements retries
- The politeness delay lives in `_get_json` — so it spaces _all_ Steam calls
  (review pages and metadata alike), not just the pagination loop
- Retryable = `{429, 500, 502, 503, 504}` + network exceptions; any other 4xx is
  permanent and raised without retry
- `SteamAPIError` wraps library failures at the boundary — callers catch one domain
  exception, not `requests`' classes; `raise ... from e` preserves the root cause
- `_get_json` stays generic (raises on failure, returns JSON) — the per-endpoint
  "no data → None" interpretation lives in each caller, because appreviews and
  appdetails signal emptiness differently; keeps retry logic DRY across both
- The identity guard returns a _verdict_ (`GuardResult`), it does not act — the
  orchestrator decides skip/proceed, and the manifest logs the full comparison
- `mismatch` and `no_data` are distinct statuses — a wrong id and a delisted game
  are different problems with different fixes; collapsing to `None` loses that
- On `mismatch`, the wrong game's metadata is discarded — never contaminate one
  game's file with another's data
- Name match = normalize (lowercase, strip ™/®/© and punctuation) then
  `SequenceMatcher` ratio ≥ `0.85`. Rejected alternatives: exact match (too
  brittle — `ELDEN RING`, `DARK SOULS™ III`, curly apostrophes all fail) and a
  looser threshold (risks accepting a different game). `difflib` is stdlib — no
  new dependency

### `tests/test_fetcher.py`

- **Purpose**: unit tests for the fetcher — 14 tests across the request helper and
  identity guard
- **Depends on**: `pipeline.fetcher`, `pytest`, `unittest.mock`

## Decisions Log

- The network is never touched — `requests.get` is patched, so tests are
  deterministic and run offline
- `time.sleep` is patched out — the suite runs instantly instead of waiting out
  real delays and backoff
- Tests assert retry _counts_, not just final outcomes — proves the backoff path
  actually retries (and that permanent errors do not)

## Known Bugs

None currently.
