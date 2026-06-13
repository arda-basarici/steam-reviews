"""main.py — command-line entry point for the Steam Review Intelligence pipeline.

Usage (run from the project root, or `python steam-reviews/main.py ...`):
    python main.py fetch          # sample mode (default): ~500 reviews/game (~9 min)
    python main.py fetch --full   # full run: up to 10k reviews/game (~1.5 h)
    python main.py clean          # raw -> clean -> validate -> data/processed/*.parquet

Thin by design: it parses arguments and hands off to the pipeline. All real work
lives in the pipeline package.
"""

import argparse
from dataclasses import replace

from pipeline import config


def _run_fetch(full: bool) -> None:
    # A --full run overrides the safe default (sample_mode=True). We must do this
    # BEFORE importing the pipeline consumers: they bind `settings` at import time
    # via `from pipeline.config import settings`, so replacing config.settings
    # here and importing orchestrator *afterwards* makes the override take effect
    # everywhere. (This is why orchestrator is imported inside the function.)
    if full:
        config.settings = replace(config.settings, sample_mode=False)

    from pipeline import orchestrator
    orchestrator.run_fetch()


def _run_clean() -> None:
    # Heavy deps (pandas, pandera, pyarrow) are imported lazily here so a plain
    # `fetch` run never pays for them — the fetch path stays dependency-light.
    from pipeline import storage, writer
    from pipeline.cleaner import clean_metadata, clean_reviews
    from validation import ValidationError, validate

    print("[clean] loading raw data...")
    reviews = clean_reviews(storage.load_raw_reviews())
    metadata = clean_metadata(storage.load_raw_metadata())
    print(f"[clean] cleaned: {len(reviews)} reviews across "
          f"{reviews['app_id'].nunique()} games; {len(metadata)} games in metadata")

    print("[clean] validating against the data contract...")
    try:
        report = validate(reviews, metadata)        # prints warnings, raises on hard fail
    except ValidationError as exc:
        print("[clean] ABORT — hard contract violations, nothing written:")
        print(exc.report)
        raise SystemExit(1)
    if report.warnings:
        print(f"[clean] {len(report.warnings)} warning(s) above; writing anyway.")

    rev_path, meta_path = writer.write_processed(reviews, metadata)
    print(f"[clean] wrote {rev_path}")
    print(f"[clean] wrote {meta_path}")
    print("[clean] done.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Steam Review Intelligence pipeline.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    fetch_cmd = subcommands.add_parser("fetch", help="Fetch reviews from Steam.")
    fetch_cmd.add_argument(
        "--full",
        action="store_true",
        help="Full run (up to 10k reviews/game). Default is sample mode (~500).",
    )

    subcommands.add_parser(
        "clean",
        help="Clean raw data into validated Parquet tables (data/processed/).",
    )

    args = parser.parse_args()
    if args.command == "fetch":
        _run_fetch(full=args.full)
    elif args.command == "clean":
        _run_clean()


if __name__ == "__main__":
    main()