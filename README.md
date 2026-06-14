# Steam Review Intelligence

**What does a Steam rating actually measure?**

Every game on Steam wears a single number — _"85% positive"_ — and players read it as a verdict. This project takes that number apart. Across **298,553 reviews from 50 games in 30 languages**, the headline score turns out to hide _when_ a player reviewed, _whether they stayed_, _how they wrote_, and _who they were_.

📄 **[Read the full report → `steam_review_report.pdf`](steam_review_report.pdf)**

---

## The findings

Four patterns, each validated _within individual games_ (not just in aggregate, which separates real effects from artifacts of which games are in the sample):

- **When — the refund window.** Reviews written before Steam's two-hour refund deadline are far harsher: recommendation falls to ~61% below the line versus ~86% above it, with a sharp cliff right at two hours. Corroborated by Steam's own refund flag and a logistic-regression control.
- **Whether they stayed — the goodbye.** Players who recommend a game keep playing it (a median 3 hours more after reviewing); players who pan it stop — 63% never meaningfully return. Stated sentiment predicts behaviour.
- **How they wrote — negativity is verbose.** Negative reviews run more than twice as long (83 vs 35 median characters). The asymmetry lives at the short end: the one-line review is overwhelmingly a _positive_ act.
- **Who they were — the veteran is harsher.** Among public profiles, players with 200+ games recommend at ~79% versus ~93% for the smallest libraries — and the gap holds _on the same game_, ruling out selection.

The report also documents **what we deliberately didn't claim** — an un-baselineable review-bomb, a fragile price signal, a hollow helpfulness score — because what an analysis refuses to claim is part of its credibility.

---

## How to run

Built and tested on Python 3.13 (Windows / PowerShell). From `steam-reviews/`:

```powershell
# 1. setup
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. collect data from Steam's public API
python main.py fetch          # sample run (500 reviews/game)
python main.py fetch --full   # full run (up to 10,000 reviews/game, ~2.5h)

# 3. clean + validate -> Parquet
python main.py clean

# 4. explore the analysis (Jupyter / VS Code)
#    open analysis/00_methodology.ipynb ... 05_what_we_didnt_use.ipynb

# 5. regenerate the PDF report
python generate_report.py

# 6. run the test suite
pytest
```

Raw and processed data are git-ignored and fully reproducible from `fetch` + `clean`. The curated game list (`data/game_list.json`) is the only committed input.

---

## Project structure

```
steam-reviews/
├── main.py                  # CLI entry point: fetch / clean commands
├── generate_report.py       # builds the PDF report from processed data
├── report_charts.py         # print-quality figures for the report
├── requirements.txt
├── ARCHITECTURE.md          # design decisions + engineering rationale
│
├── pipeline/                # the data pipeline
│   ├── config.py            #   single source of truth for settings
│   ├── fetcher.py           #   Steam API client (paginated, resumable)
│   ├── orchestrator.py      #   drives the fetch, at-least-once + resume
│   ├── storage.py           #   raw JSONL / JSON persistence + loaders
│   ├── cleaner.py           #   normalize, dedup, type-coerce
│   └── writer.py            #   atomic Parquet writes
│
├── validation/              # data-contract enforcement
│   ├── schemas.py           #   pandera schemas
│   └── validate.py          #   cross-table checks, hard-stop vs warn
│
├── analysis/                # the report, chapter by chapter
│   ├── _style.py            #   shared visual identity + within-game test
│   ├── 00_methodology.ipynb #   corpus, multilingual finding, the method
│   ├── 01_refund_window.ipynb
│   ├── 02_a_review_is_a_goodbye.ipynb
│   ├── 03_negativity_is_verbose.ipynb
│   ├── 04_the_veteran_is_harsher.ipynb
│   └── 05_what_we_didnt_use.ipynb
│
├── tests/                   # ~71 tests across the pipeline
├── data/
│   ├── game_list.json       #   curated input (committed)
│   ├── raw/                 #   fetched JSONL/JSON (git-ignored)
│   └── processed/           #   reviews.parquet, metadata.parquet (git-ignored)
│
└── steam_review_report.pdf  # the deliverable
```

---

## A note on method

The signature discipline here is the **within-game test**: every finding is computed inside each game first, then aggregated, so a pattern has to reproduce title by title before it's believed. This is what kills between-game artifacts — an "Early Access reviews are harsh" effect that looked dramatic pooled (44.8% vs 87%) evaporated under this test, while the four reported findings survived it.

One scope choice shapes what the data can say: reviews were collected **most-recent-first** (newest reviews, up to 10,000 per game) for reliable, reproducible, time-ordered pagination. This means the dataset captures each game's _recent window_ rather than its full history — which is why historical questions (review-bombing, long-run trends) are out of range. It's a one-line configuration change to collect full histories instead. See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full reasoning.

The analysis is observational and rests on 50 games: review-level findings (hundreds of thousands of rows) are strong; game-level observations are treated as suggestive.

---

## Part of AI Journey

A structured learning arc from Python foundations toward AI engineering — every project real, complete, and publicly documented. This is **Phase 2 (Data & ML Engineering)**; the next phase turns from the _structure_ of reviews to their _text_.

→ [github.com/arda-basarici/ai-journey](https://github.com/arda-basarici/ai-journey)
