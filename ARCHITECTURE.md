# ARCHITECTURE — Steam Review Intelligence

How the pipeline is built and why that structure — the module graph, the seams, and the
structural stories, kept as a clean snapshot of the code as it stands. Edited in place; what
was decided and why → [DESIGN.md](DESIGN.md); the front door → [README.md](README.md).

*Snapshot of the completed project · last updated 2026-07-05.*

---

## Design shape

Three stages with strictly one-directional flow — fetch changes nothing, clean touches no
network, validation gates promotion:

```
  data/game_list.json        the one committed input (50 games, hand-curated)
        │
        ▼
  main.py fetch [--full]
        │
        ▼
  pipeline/orchestrator.py       the conductor: per-game loop, resume, at-least-once order
    ├─ pipeline/fetcher.py       Steam API I/O: retry/backoff, identity guard, cursor pages
    └─ pipeline/storage.py       raw persistence (stdlib-only): atomic JSON, JSONL, manifest
        │
        ▼
  data/raw/                  reviews JSONL · metadata JSON · fetch_manifest.json
        │                    (git-ignored, immutable once written)
        ▼
  main.py clean
        │
        ▼
  pipeline/cleaner.py        pure transform: raw records → two tidy DataFrames
  validation/                the data contract: pandera schemas + cross-table checks
  pipeline/writer.py         atomic Parquet promotion
        │
        ▼
  data/processed/            reviews.parquet · metadata.parquet   (git-ignored)
        │
        ▼
  analysis/*.ipynb           six chapters, shared _style.py (the within-game test)
  generate_report.py + report_charts.py ──► steam_review_report.pdf   (committed)
```

The rules, stated once:

- **Every arrow goes forward only.** Notebooks read `data/processed/` and never reach back
  to raw; the cleaner reads raw and never touches the network; the fetcher writes raw
  verbatim and interprets nothing.
- **The fetch path is stdlib + requests only.** pandas / pandera / pyarrow load lazily in
  the clean command; `storage.py` returns plain `list[dict]` so a fetch run never pays for
  the analysis stack. The Parquet writer is its own module for the same reason.
- **Writes are atomic everywhere** (temp file + `os.replace`) — a reader sees the old file
  or the complete new one, never a torn write; the append-only JSONL can lose at most one
  half-written line to a crash.
- **Validation gates promotion.** A hard contract violation aborts the clean command with
  no Parquet written — bad data cannot reach the analysis layer.

### The life of a fetch (resume built in)

```
  for each game in game_list:
      manifest says done? ──► skip
      identity guard ──► mismatch / no_data recorded, no requests wasted
      iter_review_batches (cursor pages, five independent stop conditions)
          each batch:  append_reviews (disk)  THEN  save_manifest (cursor)
                       └─ at-least-once: a crash duplicates a batch, never loses one
      SteamAPIError ──► clean stop; rerun resumes from last_cursor without re-guarding
```

The manifest is one global, atomically-rewritten file: one-read resume, one-glance progress,
a single auditable log of every guard verdict.

## Module responsibilities

One line per module; detail lives in the docstrings.

| module | single job |
| --- | --- |
| `pipeline/config.py` | the frozen `Settings` singleton: endpoints, query policy, rate limits, paths — no logic |
| `pipeline/fetcher.py` | all Steam API I/O: one retry/backoff home (`_get_json`), the identity guard, generator pagination |
| `pipeline/orchestrator.py` | the fetch conductor: sequencing, resume, at-least-once order — owns what the layers below don't |
| `pipeline/storage.py` | raw-data persistence primitives + inverse loaders (stdlib-only; stamps `app_id` from filenames) |
| `pipeline/cleaner.py` | pure transform: flatten, coerce honest dtypes, dedup — records in, two tidy frames out |
| `validation/schemas.py` · `validate.py` | the data contract: declarative per-column schemas + explicit cross-table checks, hard-stop vs warn |
| `pipeline/writer.py` | atomic Parquet promotion of the two processed tables |
| `main.py` | thin CLI (`fetch` / `clean`), lazy imports keep the fetch path light |
| `analysis/_style.py` | the shared visual identity + `within_game_gap` — the signature test, implemented once |
| `analysis/00–05_*.ipynb` | methodology · the four findings · what-we-didn't-use — read processed only |
| `generate_report.py` · `report_charts.py` | the narrative PDF: prose/layout separated from figure generation |
| `tests/` | 71 tests: fetcher (offline, retry counts), storage (real files in tmp), orchestrator (ordering), cleaner, validation, writer |

## Seams that carried weight

- **The guard returns a verdict, it never acts.** `check_identity` produces a `GuardResult`;
  the orchestrator decides skip/proceed and the manifest logs the full comparison —
  `mismatch` and `no_data` stay distinct because a wrong id and a delisted game are
  different problems.
- **Pagination is a generator.** `iter_review_batches` streams batches and touches no disk —
  which is exactly what makes the at-least-once write order possible for its caller.
- **One retry home.** Every request goes through `_get_json` (backoff, politeness delay,
  retryable-status set); no other function re-implements retries.
- **`within_game_gap` exists once.** Every chapter computes the signature test through the
  same helper — with the aggregation explicit per column type (mean for binary rates,
  median for fat-tailed continuous), because a single wrong default there would silently
  corrupt every binary test.
- **Collect-then-decide validation.** Every violation lands in one `ValidationReport` before
  anything raises — no fix-one-rerun loop.

---

## Structural stories

### The identity guard that paid for itself

The guard exists because a hand-curated id list *will* contain mistakes. On the first real
fetch it proved it: **five of fifty app_ids pointed at entirely different games** (Democracy 3
→ "Dex", VVVVVV → "Shovel Knight", …) — all caught automatically by low name-match ratios or
missing data, logged in the manifest, and fixed in `game_list.json` against the live store.
Two more skips were *edition drift* — the right game under a longer store name ("Disco
Elysium **- The Final Cut**") — fixed in code with a token-prefix rule rather than by editing
the data to match (which would make the list lie and not generalize). Wrong-game metadata is
discarded on mismatch, so one game's file can never carry another's data. The lasting shape:
name match = normalize, then similarity ≥ 0.85, plus the edition-prefix acceptance — brittle
exact-match and looser thresholds were both rejected for stated reasons.

### At-least-once, proven by ordering tests

The correctness pair (fetcher never loses, cleaner never duplicates — DESIGN) is enforced in
the orchestrator as a write *order*: reviews to disk, then cursor to manifest. The test suite
asserts the order itself — a `save_manifest` follows every `append_reviews`, and resume
passes the saved cursor onward without re-guarding — not just the happy-path outcome.

### The honest testing boundary

Fetcher tests never touch the network (`requests.get` patched) and never wait
(`time.sleep` patched out) — and they assert **retry counts**, proving the backoff path
actually retries and permanent errors don't. Storage tests do the opposite on purpose:
storage *is* file I/O, so they exercise real files, confined to pytest's `tmp_path` with a
redirected settings copy, and verify the atomic-write mechanism (temp file gone afterwards),
not just the result.

### Steam can't be trusted to say "done"

The pagination loop carries five independent stop conditions — empty batch, short batch,
missing cursor, repeated-cursor loop guard, and the cap (which trims mid-batch rather than
overshoot) — because the API signals completion inconsistently across apps. Related field
notes are encoded in config rather than folklore: `num_per_page=80` (100 has a documented
early-truncation bug), `purchase_type=all` (the default silently excludes key-activated
owners), `language=all` (some games are majority non-English).

### Cleaning keeps generously, drops only cruft

The column policy is explicit named constants with rationale: every field with plausible
analytic value to *anyone* is kept; only identity/UI cruft is dropped (which also strips
personal data — persona names, avatars — from the shareable artifact). Identifiers stay
strings (17-digit ids would lose precision as float64), dtypes are nullable and honest,
review text passes through byte-for-byte, and `genres` stays a real list (native to
Parquet). Nothing is truly lost either way: raw is immutable, so a different analyst can
re-clean with a different policy.

---

## Deliberately not done

- **No per-game manifest files.** One global atomic manifest wins for a sequential, polite
  fetch (one-read resume, one auditable artifact); per-game files only pay under concurrent
  writers. If fetching ever parallelizes, that's the design site to revisit.
- **No dtype assertions in the contract.** The cleaner already coerces them; pinning exact
  nullable/arrow dtype strings is brittle across library versions. The contract asserts
  presence, keys, and ranges — the things that silently corrupt analysis.
- **No clustering, no text modeling.** There is no honest clustering question in this data,
  and review *text* is a future study's subject (DESIGN — restraint on tooling).
- **No notebook-export report.** The PDF is purpose-written; `nbconvert` dumps were rejected
  so the deliverable reads as one argument, not six stitched notebooks.
