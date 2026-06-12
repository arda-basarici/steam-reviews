"""main.py — command-line entry point for the Steam Review Intelligence pipeline.

Usage (run from the project root, or `python steam-reviews/main.py ...`):
    python main.py fetch          # sample mode (default): ~500 reviews/game (~9 min)
    python main.py fetch --full   # full run: up to 10k reviews/game (~1.5 h)

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Steam Review Intelligence pipeline.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    fetch_cmd = subcommands.add_parser("fetch", help="Fetch reviews from Steam.")
    fetch_cmd.add_argument(
        "--full",
        action="store_true",
        help="Full run (up to 10k reviews/game). Default is sample mode (~500).",
    )

    args = parser.parse_args()
    if args.command == "fetch":
        _run_fetch(full=args.full)


if __name__ == "__main__":
    main()