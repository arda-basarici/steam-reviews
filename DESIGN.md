# DESIGN — Steam Review Intelligence

What was built and why — the decisions and their reasoning, kept as a clean snapshot of the
design as it stands. Edited in place, not appended to; the findings and figures live in
[steam_review_report.pdf](steam_review_report.pdf), and the build chronology lives in git
history. How the code is structured → [ARCHITECTURE.md](ARCHITECTURE.md); the front door →
[README.md](README.md).

*Snapshot of the completed project · last updated 2026-07-05.*

---

## Objective

**What does a Steam rating actually measure?** Every game wears a single number — "85%
positive" — and players read it as a verdict. The project takes that number apart across
298,553 reviews / 50 games / 30 languages, asking what the score hides: *when* a player
reviewed, *whether they stayed*, *how they wrote, and who they were.*

The engineering half of the objective is just as deliberate: build the data source from a
real, messy public API — pagination that lies, ids that point at the wrong game, fields that
are silently hollow — with the pipeline discipline (fetch → clean → validate → promote) that
makes the analysis trustworthy.

---

## The pipeline decisions

**Three modules, three jobs.** Fetch = network I/O, clean = pure transform, validate =
assert. Each is testable in isolation; mixing them is the classic data-pipeline mess.

**Two validation boundaries, deliberately separate.** The *identity guard* is a fetch-time
precondition (is this app_id even the game we think it is?) and lives in the fetcher; the
*data contract* (is this table well-formed?) gates promotion to Parquet and lives in
`validation/`. Conflating them is the trap — they fail differently and fix differently. Both
earned their keep on the real data (see ARCHITECTURE — *the identity guard that paid for
itself*).

**Correctness as a pair: at-least-once writes + idempotent cleaning.** The fetcher writes
each review batch to disk *before* recording its cursor, so a crash re-fetches a batch
(duplicate) but never loses one; the cleaner deduplicates on `recommendationid`. Neither
piece has to be perfect alone; together they are correct — and both halves are cheap.

**Errors: raise the exceptional, return the anticipated.** Steam unreachable after retries
raises one domain exception (`SteamAPIError`, a clean resumable stop); "this game has no
reviews" returns empty for the caller to handle. Exceptions are for the exceptional.

**Raw data is immutable and git-ignored; the one committed input is the curated game list.**
Everything under `data/` regenerates from `fetch` + `clean`; `game_list.json` is committed
because its curation — 50 games spanning genres, sizes, and communities — *is* a design
decision. Format choices follow the same logic: JSONL for raw reviews (append-per-batch, a
crash costs at most one half-written line), Parquet for processed tables (types survive,
production signal).

**Collection order is most-recent-first — a scope choice with a stated cost.** Walking
reviews in creation-date order gives deterministic, reproducible pagination and dodges a
documented cursor bug in the helpfulness sort; at the 10,000-per-game cap it captures each
game's *recent window*, not its full history. Review-level findings are sound; historical
questions (review-bombing, long-run trends) are out of range — stated in the report, and
reversible with one config field if a future study needs full histories.

**Two tables, two grains, joined only in notebooks.** Reviews (~300k rows) and games (50
rows) are never denormalized into each other. The epistemics fall out of the schema instead
of being something to remember: review-level claims get review-level N; game-level
observations are hedged to "in our sample of 50."

**A full fetch must be a deliberate act.** Sample mode (500 reviews/game, ~9 minutes) is the
default; `--full` is an explicit flag with a banner, so a multi-hour run can never start by
accident.

## The analysis decisions

**The within-game test is the central decision — a pattern must reproduce inside individual
games before it is believed.** Pooling review-level data across 50 unequally-sized games
invites confounding-by-game (Simpson's paradox): a "pattern" can exist in the pile that
exists in no individual game, manufactured purely by which titles dominate the sample. Every
finding is computed within each game first, then aggregated; the helper is implemented once
and shared by every chapter. The test has a body count: an apparent 42-point Early-Access
effect evaporated under it, while the four reported findings each held in 43–47 of the ~48
qualifying games. (The same discipline recurs across the portfolio — within-group analysis
in [pathfinding-ml](https://github.com/arda-basarici/pathfinding-ml), regime-conditioned
evaluation in [blackjack-rl](https://github.com/arda-basarici/blackjack-rl).)

**Hard-stop vs warn, by one test: would downstream analysis be silently corrupted?**
Structural and key violations (missing column, duplicate key, broken referential integrity)
raise and write nothing; range oddities (a future timestamp, Steam's own totals disagreeing)
warn and pass — the first weird-but-real value shouldn't block 24k good rows. All violations
are collected into one report before deciding, so there is no fix-one-rerun loop.

**Statistics sized to the data's shape.** Playtime and review length are heavily right-tailed
(playtime max ≈ 26,000 hours), so the analysis runs on medians and distribution-free tests
(Mann-Whitney U), reporting effect sizes beside p-values — at N ≈ 300k everything is
"significant," so significance alone is never the claim.

**Restraint on tooling.** One logistic regression earns its place (multivariate confirmation
of the refund-window effect with game fixed effects); clustering and text modeling were
deliberately *not* used — there is no honest clustering question here, and review text is a
future study's subject. The right tool for the question, not every tool available.

**What the analysis refuses to claim is part of its credibility.** The discarded hypotheses
— the EA artifact, an un-baselineable review-bomb, a fragile price signal, hollow variables
(`weighted_vote_score` at its default for ~75% of rows; `num_games_owned == 0` meaning
*private profile*, not empty library) — get their own chapter rather than a silent omission.

**The report is its own writing, not exported notebooks.** One narrative through-line (when →
whether → how → who), figures regenerated at print quality from the processed data with the
notebooks' palette, so report and notebooks read as one body of work.

---

## Outcome

Four findings, each within-game-validated: reviews before the two-hour refund deadline are
far harsher (~61% vs ~86%, a cliff at the line); a review is a goodbye (recommenders keep
playing, median +3h; 63% of pan-ers never meaningfully return); negativity is verbose (83 vs
35 median characters — the one-liner is overwhelmingly a positive act); the veteran is
harsher (~79% vs ~93% recommendation, holding on the same game). The corpus itself is a
finding: under half of it is English, across 30 languages. And the contract passed clean on
the real data — dedup held, referential integrity held, Steam's totals were self-consistent.
Full analysis: [steam_review_report.pdf](steam_review_report.pdf).

---

## Scope & non-goals

- **Observational, 50 games, recent window.** Review-level findings ride on hundreds of
  thousands of rows; game-level observations are suggestive by design and hedged as such.
- **No text analysis.** The corpus is collected and preserved for it (text byte-for-byte,
  real UTF-8, 30 languages), but reading the reviews' *content* is a separate future study.
- **No historical claims.** Review-bombing and long-run trends need full histories this
  collection deliberately doesn't have.

## Future work (curated)

A full-history crawl is one config field (`review_filter`) plus fetch time. The multilingual
corpus makes a future text/NLP study genuinely multilingual rather than English-only. If
fetching ever parallelizes, the single global manifest should become per-game files or a
small database — noted at the design site, not needed for a polite sequential fetch.
