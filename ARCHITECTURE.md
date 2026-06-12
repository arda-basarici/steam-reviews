# Steam Review Intelligence — Architecture

## Overview

A data pipeline that pulls real review data from Steam's public APIs and turns it
into clean, validated tables for analysis. Three stages with strict one-directional
flow and assertions at two distinct boundaries.

- **Fetcher**: talks to Steam, saves raw responses verbatim, changes nothing
- **Cleaner**: transforms raw JSON → tidy tables (no network access)
- **Validator**: the data contract — asserts invariants before data is promoted

Status: Phase 2, project 2. Fetcher complete — requests, identity guard,
pagination, persistence, and orchestration, all unit-tested — and exposed via a
CLI (main.py). Cleaner and validator not yet started.

## Data Flow

```
game_list.json            (committed, hand-curated — 50 games)
  → main.py                CLI: `python main.py fetch [--full]`
  → orchestrator.py        [BUILT: per-game loop, resume, at-least-once, clean stop]
      ├─ fetcher.py        [BUILT: retry/backoff, identity guard, cursor pagination]
      └─ storage.py        [BUILT: atomic writes, JSONL append, manifest, metadata]
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
- **One global manifest, not one file per game** — at 50 sequential games the
  per-batch write cost of rewriting all records is negligible (~10 KB, sub-ms),
  while a global file gives one-read resume, one-glance progress, and a single
  auditable artifact. Per-game files only win under _concurrent_ writers, which
  we deliberately don't have (sequential fetch for politeness). Atomic writes
  already cover the corruption risk that per-game files would otherwise reduce.
  Forward note: if a later phase parallelizes fetching, per-game files or a small
  database become the right call.

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
  `fetch_app_details`, `check_identity`, `_normalize_name`, `_name_similarity`,
  `ReviewBatch`, `iter_review_batches`
- **Purpose**: all Steam API I/O. Polite, robust single requests; per-game identity
  verification; cursor pagination. (Orchestration / resume wiring is the next
  piece.)
- **Key methods**: `_get_json()` — one GET with retry/backoff, the single home of
  retry logic; `check_identity()` — name-match guard returning a `GuardResult`
- **Depends on**: `config.py`, `requests`
- **Depended on by**: `orchestrator.py`, `tests/test_fetcher.py`

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
- Pagination is a _generator_ (`iter_review_batches`), not a list-builder or a
  self-writer — it streams `ReviewBatch` objects and touches no disk, so
  persistence is a separate job (storage.py). Streaming is also what makes
  at-least-once possible: the caller writes each batch before recording its cursor
- Five independent stop conditions (empty batch, short batch, missing cursor,
  repeated-cursor loop guard, cap) — Steam can't be trusted to signal "done"
  cleanly even once
- `query_summary` captured from the first batch only — same totals repeat on every
  page; a zero-review game still emits one batch so the summary is recorded
- Cap trims mid-batch — never overshoot `effective_reviews_cap`, even partially
- Edition-drift tolerance (added after the first real fetch): the guard also
  accepts when the expected name is a clean leading _token-prefix_ of the store
  name (store = our name + extra words), e.g. "Disco Elysium" →
  "Disco Elysium - The Final Cut". Requires ≥2 expected tokens so a short name
  can't latch onto a different longer-named game. Chosen over editing names in
  game_list (which would make the data lie and not generalize) and over lowering
  the threshold (which would weaken wrong-game detection)

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

### `pipeline/storage.py`

- **Functions**: `atomic_write_json`, `append_reviews`, `write_metadata`,
  `load_manifest`, `save_manifest`
- **Purpose**: all disk persistence for the fetcher — kept separate from
  fetcher.py because writing files is a different job from talking to Steam.
  Provides safe write primitives; does not decide when/in what order to call them
- **Depends on**: `config.py`
- **Depended on by**: `orchestrator.py`, `tests/test_storage.py`

## Decisions Log

- Separate module from fetcher.py — network I/O and disk I/O are different jobs
  (acted on the instinct that "saving is another job, maybe another module")
- `atomic_write_json` writes a temp file then `os.replace` — a torn write is
  structurally impossible; the manifest (rewritten every batch) can't corrupt
- JSONL for reviews, append mode — add lines, never rewrite; a crash leaves at
  most one half-written final line
- `ensure_ascii=False` everywhere — non-Latin review text (e.g. Overwatch 2's
  majority-Chinese corpus) is stored as real UTF-8, not escapes
- Storage provides primitives but not sequencing — the at-least-once order
  (reviews before cursor) is the orchestrator's responsibility, not storage's
- `GameRecord` (TypedDict) documents the manifest record schema beside the status
  constants — storage owns the manifest format, so the record shape lives here.
  load/save stay typed as plain `dict` so partial test fixtures remain convenient;
  the precise typing is applied where records are _built_ (the orchestrator)

### `tests/test_storage.py`

- **Purpose**: unit tests for storage — real file I/O, confined to pytest's
  `tmp_path`; `settings` patched to point at the temp directory

## Decisions Log

- Tests touch real files (storage _is_ file I/O) but never the project's data/
  dirs — paths are redirected to tmp_path via a patched frozen settings copy
- Asserts the temp file is gone after an atomic write (verifies the mechanism,
  not just the result) and that non-Latin text survives the round trip

### `pipeline/orchestrator.py`

- **Functions**: `fetch_game`, `run_fetch`, `load_game_list`, `_summarize`, `_now`
- **Purpose**: the fetch conductor — ties the guard, the pagination stream, and
  storage into one resumable run. Owns the _sequencing_ the lower layers don't:
  at-least-once writes, resume from the manifest, and stopping cleanly when Steam
  is unreachable
- **Depends on**: `fetcher`, `storage`, `config`
- **Depended on by**: `main.py`, `tests/test_orchestrator.py`

## Decisions Log

- At-least-once made concrete: `append_reviews` (disk) precedes `save_manifest`
  (cursor) for every batch — a crash duplicates one batch, never loses one
- `current` manifest record is mutated in place — it is the same object stored in
  the manifest, so save_manifest persists it; also keeps the type checker happy
  (no `{**record}` unpack of a possibly-None value)
- Resume trusts the recorded guard result — an in_progress game is not re-guarded
  and its metadata is not rewritten; it continues from `last_cursor`
- Metadata written iff `appdetails_data and query_summary` are both present —
  one condition cleanly covers fresh-first-batch (write) and resume (skip)
- Skip branches (`mismatch`, `no_data`) never call the pagination loop — no
  request is wasted on a game that failed identity
- Only `SteamAPIError` is caught at the top (clean, resumable stop); any other
  exception propagates as a real bug to fix
- Sample-mode banner printed at run start — a sample run can never be mistaken
  for the real thing

### `tests/test_orchestrator.py`

- **Purpose**: unit tests for the conductor — every lower layer mocked; verifies
  branching and ordering, not real I/O

## Decisions Log

- Asserts a `save` follows every `append` (proves at-least-once order) and that
  resume passes the saved cursor into `iter_review_batches` without re-guarding

### `main.py`

- **Purpose**: thin CLI entry point — `python main.py fetch [--full]`
- **Depends on**: `config`, `orchestrator` (imported lazily)
- **Depended on by**: nothing — top of the dependency tree

## Decisions Log

- `--full` overrides the safe `sample_mode=True` default; the override is applied
  to `config.settings` _before_ orchestrator is imported, so the import-time
  `from pipeline.config import settings` bindings pick it up (hence the lazy
  import inside the function)
- Stays thin — argument parsing only; all real work is in the pipeline package

## Known Bugs

### Wrong app_ids in game_list.json caught by the identity guard (fixed)

The first sample fetch skipped 7 of 50 games. The guard's manifest log showed two
causes:

- **Five wrong app_ids** — the id pointed at an entirely different game:
  Democracy 3 → "Dex", VVVVVV → "Shovel Knight", Tavern Master → "Strange
  Horticulture", Warsim → "ScreenPlay", A Way Out → (no storefront entry). All
  five had low guard ratios (≤0.29) or no_data. Fixed by correcting the ids in
  game_list.json (245470, 70300, 1525700, 659540, 1222700), each verified against
  the live store. The manual pre-fetch verification only checked four ids; the
  guard caught these five automatically — exactly its purpose.
- **Two edition-drift false-skips** — right game, longer store name (Disco Elysium
  → "...- The Final Cut", Shadow of the Tomb Raider → "...: Definitive Edition"),
  ratios 0.65–0.72. Fixed in code via the edition-prefix rule (see fetcher
  Decisions Log), not by editing the data.
