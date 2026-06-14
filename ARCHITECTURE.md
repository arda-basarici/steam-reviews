# Steam Review Intelligence — Architecture

## Overview

A data pipeline that pulls real review data from Steam's public APIs and turns it
into clean, validated tables for analysis. Three stages with strict one-directional
flow and assertions at two distinct boundaries.

- **Fetcher**: talks to Steam, saves raw responses verbatim, changes nothing
- **Cleaner**: transforms raw JSON → tidy tables (no network access)
- **Validator**: the data contract — asserts invariants before data is promoted

Status: Phase 2, project 2. **Complete** — pipeline (fetch → clean → validate →
Parquet), a five-chapter analysis, and a generated PDF report — all unit-tested and
exposed via a CLI (`main.py fetch` / `main.py clean`). The full fetch produced
298,553 cleaned reviews across all 50 games; the analysis lives in `analysis/`, the
deliverable in `steam_review_report.pdf`.

## Data Flow

```
game_list.json            (committed, hand-curated — 50 games)
  → main.py                CLI: `python main.py fetch [--full]` | `python main.py clean`
  → orchestrator.py        [BUILT: per-game loop, resume, at-least-once, clean stop]
      ├─ fetcher.py        [BUILT: retry/backoff, identity guard, cursor pagination]
      └─ storage.py        [BUILT: atomic writes, JSONL append, manifest, metadata]
  → data/raw/reviews/{app_id}_reviews.jsonl      (gitignored, append-per-batch)
    data/raw/metadata/{app_id}_metadata.json     (gitignored)
    data/raw/fetch_manifest.json                 (progress + audit log)
  → storage.load_raw_*()   [BUILT: read raw back as list[dict]; app_id from filename]
  → cleaner.py             [BUILT: flatten author, coerce dtypes, dedup → two tidy tables]
  → validation/            [BUILT: pandera schemas + cross-table checks; hard-stop vs warn]
  → writer.py              [BUILT: atomic Parquet write of the processed tables]
  → data/processed/reviews.parquet + metadata.parquet   (gitignored)
  → analysis/*.ipynb         [BUILT: read processed only; within-game validation; shared _style.py]
  → generate_report.py       [BUILT: regenerates figures, assembles the narrative PDF]
  → steam_review_report.pdf  (committed — the portfolio deliverable)
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
- **Heavy deps off the fetch path** — pandas / pyarrow / pandera are imported only
  in the cleaning stage. Storage's raw loaders return plain `list[dict]`, and
  `main.py` imports the clean modules lazily inside `_run_clean`. A `fetch` run
  never loads them, keeping the fetch path lean. The same principle put the Parquet
  writers in their own module (`writer.py`) instead of in stdlib-only `storage.py`.
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
- `filter=recent` — walks the review set in creation-date order; avoids the
  helpfulness-sort cursor-loop bug, yields temporal ordering for free, and is
  deterministic/reproducible (relevance re-ranks over time and biases toward
  already-upvoted reviews, which would distort sentiment analysis). **Analytical
  cost**: at the 10k/game cap this captures each game's _recent window_, not its
  full history — so review-level findings are sound but historical questions
  (review-bombing, long-run trends) are out of range. Reversible by config
  (`review_filter`) for a future full-history crawl; this analysis didn't need it.
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
  `load_manifest`, `save_manifest`, `load_raw_reviews`, `load_raw_metadata`
- **Purpose**: all RAW-data disk persistence — kept separate from fetcher.py
  because writing files is a different job from talking to Steam. Stdlib-only (no
  pandas), so the fetch path stays light. Provides safe write primitives and the
  inverse readers for the cleaning stage; does not decide when/in what order to
  call them
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
- `load_raw_reviews` / `load_raw_metadata` return `list[dict]`, not DataFrames —
  keeps pandas off the fetch path; building tidy frames is the cleaner's job. The
  review JSON has no `app_id` inside, so the loader parses it from the filename and
  stamps it on each record: this is where the foreign key to metadata is born.
  Files are read sorted (reproducible); blank lines and empty files are skipped

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

- **Purpose**: thin CLI entry point — `python main.py fetch [--full]` and
  `python main.py clean`
- **Depends on**: `config`, `orchestrator` (fetch) and `storage` / `cleaner` /
  `validation` / `writer` (clean), all imported lazily
- **Depended on by**: nothing — top of the dependency tree

## Decisions Log

- `--full` overrides the safe `sample_mode=True` default; the override is applied
  to `config.settings` _before_ orchestrator is imported, so the import-time
  `from pipeline.config import settings` bindings pick it up (hence the lazy
  import inside the function)
- `clean` lazily imports the heavy modules (pandas/pandera/pyarrow) so a `fetch`
  run never pays for them; the command is load → clean → validate → write, and a
  hard validation failure aborts with `SystemExit(1)` writing no Parquet
- Stays thin — argument parsing and orchestration only; all real work is in the
  pipeline package

### `pipeline/cleaner.py`

- **Functions**: `clean_reviews`, `clean_metadata` (+ `_genre_list`); column-policy
  constants `KEEP_REVIEW_FIELDS`, `KEEP_AUTHOR_FIELDS`
- **Purpose**: pure transform — raw records (`list[dict]`) → two tidy DataFrames.
  No network, no disk, no model assumptions. Flattens the nested `author`, coerces
  honest dtypes, parses Unix timestamps to UTC, deduplicates, preserves review text
  byte-for-byte
- **Depends on**: `pandas`
- **Depended on by**: `main.py`, `tests/test_cleaner.py`

## Decisions Log

- **Takes records, returns frames** — `clean_*(list[dict]) -> DataFrame`. Building
  the frame is the cleaner's job (it owns pandas); reading files is storage's. This
  is what keeps pandas off the fetch path
- **Column policy: keep generously, drop only pure cruft** — every field with any
  plausible analytic value to _anyone_ (not just our five questions) is kept;
  only identity/UI cruft (avatar hash, profile_url, persona_status, personaname,
  store-page HTML/images/requirements) is dropped. Nothing is truly lost: raw JSONL
  is immutable, so a different analyst can re-clean with a different list. The
  kept/dropped lists are explicit, named constants with rationale — curation, not
  carelessness. Dropping personaname/avatar also reduces personal data in the
  shareable artifact
- **Identifiers kept as strings** — `steamid` and `recommendationid` are 17-digit
  ids that would lose precision as float64; they are identifiers, not quantities
- **Nullable dtypes** (`Int64`, `boolean`, `string`) — survive into Parquet and
  represent missing values honestly, which plain `int`/`bool` cannot
- **Review text is never altered** — `\r\n`, ASCII art, and non-Latin scripts pass
  through untouched; the cleaner shapes structure, not content (keeps Phase 3/4 NLP
  open)
- **`genres` kept as a list** `["Action", "RPG"]`, not exploded or string-joined —
  faithful to the multi-valued structure, native to Parquet's `list<string>` type,
  explodable on demand in a notebook. Exploding would shatter the metadata table's
  one-row-per-game grain; a joined string would force re-parsing
- **Two tables, joined on `app_id` in notebooks** — reviews stay strictly
  review-level; game-level facts are not denormalized onto each review (one grain,
  one source of truth, no 24k stale copies of a corrected price)
- **`query_summary` totals live in the metadata row** — they are whole-population
  counts (e.g. 1,037,403 for L4D2), the denominators for per-game sentiment and
  review-bombing rates, distinct from our 500-review _sample_

### `validation/` (`schemas.py`, `validate.py`)

- **Public surface**: `validate(reviews, metadata)`, `ValidationError`,
  `ValidationReport`; pandera schemas `REVIEWS_STRUCTURE/RANGES`,
  `METADATA_STRUCTURE/RANGES`
- **Purpose**: the data contract — the gate between cleaning and Parquet. Asserts
  invariants and decides whether the cleaned tables may be promoted
- **Depends on**: `pandas`, `pandera>=0.30` (pandas-3.x support), `pipeline.config`
- **Depended on by**: `main.py`, `tests/test_validation.py`

## Decisions Log

- **Hard-stop vs warn** — structural/key violations _raise_ and write nothing
  (missing column, duplicate `recommendationid`, broken referential integrity,
  empty table): these would silently corrupt analysis. Soft range oddities only
  _warn_ (score just outside [0,1], future-looking timestamp, Steam's own totals
  disagreeing): unusual but not impossible, and the first weird-but-real value
  shouldn't block 24k good rows. Test: "would a downstream analysis be silently
  corrupted?" → raise; "is this just an outlier to surface?" → warn
- **Collect all, then decide** — every violation is gathered into one
  `ValidationReport` before raising, not fail-fast; no fix-one-rerun loop
- **Pandera for the schema layer, plain Python for cross-table** — declarative
  schemas read like a spec for per-column presence/nullability/uniqueness/ranges;
  referential integrity, row counts, and the pos+neg≤total check span both frames,
  so they live in `validate.py` as explicit Python
- **pandera imported lazily** — only inside the schema runner / `validate`, so
  importing the package doesn't require pandera and the cross-table logic stays
  testable without it
- **Dtypes intentionally not asserted** — the cleaner already coerces them, and
  pinning exact nullable/arrow dtype strings is brittle across versions; the
  contract focuses on presence, keys, and value ranges

### `pipeline/writer.py`

- **Functions**: `write_processed_reviews`, `write_processed_metadata`,
  `write_processed` (+ `_atomic_write`)
- **Purpose**: the PROCESSED-data disk layer — atomic Parquet writes of the two
  cleaned tables. Separate from stdlib-only `storage.py` because it needs
  pandas/pyarrow and only the clean path uses it
- **Depends on**: `pandas` (+ `pyarrow` engine), `pipeline.config`
- **Depended on by**: `main.py`, `tests/test_writer.py`

## Decisions Log

- **Atomic write** (temp file + `os.replace`) — same crash-safety storage uses for
  JSON; a reader sees the old Parquet or the complete new one, never a torn file
- **Serialization separated from the atomic move** — `_atomic_write(path, write_fn)`
  takes the serializer as a callback, so the rename/crash-safety logic is testable
  without pyarrow and any format could reuse it
- **Tests split by dependency** — atomic-write tests use a fake serializer (run
  anywhere); Parquet round-trip tests (incl. the `genres` list column surviving)
  require pyarrow

## The Analysis Layer

The pipeline produces clean tables; the analysis turns them into an argument. It
lives in `analysis/` as six numbered notebooks (a methodology chapter, four finding
chapters, and a limitations chapter) plus a shared helper module, and it feeds a
standalone report generator at the project root.

```
analysis/_style.py          shared visual identity + the within-game test
analysis/00_methodology.ipynb   corpus, the multilingual finding, the method itself
analysis/01..04_*.ipynb         the four findings, one per chapter
analysis/05_what_we_didnt_use   the discarded hypotheses + data limits
generate_report.py          regenerates print-quality figures, assembles the PDF
report_charts.py            the figures, isolated from the prose/layout
```

The same forward-only rule holds: notebooks read `data/processed/` and never reach
back to raw.

### The within-game test (the signature method)

The core analytical decision, and the spine of every finding: **a pattern is
computed inside each game first, then aggregated across games — never pooled.**
Pooling review-level data across 50 unequally-sized games invites
confounding-by-game (Simpson's paradox): a pattern can appear in the pile that
exists in no individual game, manufactured purely by _which_ games dominate the
sample. The within-game test is the guard against it — an effect must reproduce
title by title before it is believed.

This is not decoration. The clearest example: reviews written during Early Access
recommend at 44.8% pooled, against an 87% baseline — a dramatic 42-point "finding."
It evaporates within-game (only one game has enough reviews both during and after
EA to compare, and there the gap reverses): it was an artifact of _which_ games had
troubled EA periods, not an effect of reviewing early. The four reported findings
all survived this same test (holding in 43–47 of the ~48 qualifying games each); EA,
price, and language did not. The method is what separates the two groups.

### Analysis Decisions Log

- **Within-game over pooled, everywhere** — the signature method above. Implemented
  once in `_style.within_game_gap(...)` so every chapter computes it identically,
  rather than copy-pasted per notebook
- **`agg="mean"` for binary, `agg="median"` for continuous** — the within-game
  helper takes the aggregation explicitly. Median of a 0/1 column (e.g. `voted_up`)
  is meaningless (it returns 0 or 1, not a rate); mean is the rate. Skewed
  continuous columns (review length, playtime) use median, which resists their fat
  tails. A single wrong default here silently corrupts every binary within-game test
- **Minimum 30 reviews per side** — a within-game gap from a handful of reviews is
  noise. Each test includes only games with ≥30 on both sides of its split, which
  leaves 46–48 of the 50 games qualifying depending on the split. Stated in the
  report so the shifting denominators aren't mistaken for lost data
- **scikit-learn used only where it earns its place** — a logistic regression in the
  refund chapter does real work (multivariate confirmation that playtime predicts
  sentiment with game/length/votes controlled, via game fixed effects + log-scaled
  skewed features, interpreted as odds ratios against a baseline, not as a
  predictor). KMeans and any text modeling were _deliberately not used_ — there is no
  honest clustering question here, and review _text_ is explicitly Phase 4's job.
  Restraint is the signal: the right tool for the question, not every tool available
- **Medians and distribution-free tests over means** — playtime and length are
  heavily right-tailed (playtime max ≈ 26,000 hours). Means mislead; the analysis
  uses medians, Mann-Whitney U (with effect size reported alongside the p-value,
  since at N≈300k every difference is "significant"), and log transforms where a
  model needs them
- **`weighted_vote_score` abandoned; `num_games_owned==0` is a privacy default** —
  two variables that looked usable but weren't. `weighted_vote_score` sits at its
  default 0.5 for ~75% of reviews (hollow). `num_games_owned` is 0 for 54% of
  reviewers — Steam's private-profile default, not a real empty library — so the
  veteran chapter restricts to the 46% public-profile subset and says so. Catching
  hollow variables before building on them is what the playground (`eda.ipynb`,
  not committed) is for
- **"What we didn't use" is a deliberate chapter** — the discarded hypotheses (EA
  artifact, un-baselineable review-bomb, fragile price signal, critics-agree-with-
  players, language/game confound, free-copy null) are documented, not hidden. What
  an analysis refuses to claim is part of its credibility
- **The report is its own writing, not exported notebooks** — `generate_report.py`
  is a purpose-built narrative document with a single through-line (when → whether →
  how → who), not a `nbconvert` dump. Charts are regenerated fresh at print
  resolution from the processed data, reusing the notebooks' palette so the report
  and notebooks read as one body of work. `report_charts.py` keeps the figures
  separate from the prose/layout, mirroring the pipeline's "I/O vs transform vs
  assert" separation one level up
- **Single-accent visual identity** (`_style.py`) — one crimson accent marks "the
  finding," everything else in greys ("the context"). The colour follows the
  argument, so each chart's point is legible before the caption is read. Chosen
  deliberately _not_ to match the previous project's palette — a portfolio should
  show growth, not brand consistency

## Data Facts (full fetch)

What the real cleaned data is, after the full `fetch --full` + `clean` run that the
analysis and report are built on:

- **298,553 reviews across all 50 games**, 23 columns; **50 games**, 21 columns of
  metadata. Reviews per game are unequal _by design_: popular titles hit the
  10,000-review cap (e.g. Baldur's Gate 3), niche titles contribute their full
  recent history (e.g. Mount & Blade II: Bannerlord, ~1,800). This is precisely why
  the analysis works within-game rather than pooling — popular titles would
  otherwise dominate any aggregate.
- **The corpus is multilingual — under half is English**: english ~45% (~136k),
  then russian ~40k, schinese ~35k, spanish ~19k, brazilian ~17k, and a long tail
  across **30 languages**. Validates `language=all` and `ensure_ascii=False`; makes
  the Phase 4 NLP genuinely multilingual.
- **The contract passed clean** on the real data — dedup held
  (`recommendationid` unique), every review's `app_id` matched a metadata row, and
  Steam's sentiment totals were self-consistent.
- **Overall recommend rate 84.8%** — the headline number the report exists to take
  apart.

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
