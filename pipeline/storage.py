"""storage.py — disk persistence for the fetcher.

Separate from fetcher.py on purpose: fetcher.py talks to Steam (network I/O),
this module writes to disk (file I/O). Two different kinds of I/O, two modules,
each testable on its own.

It provides four primitives. It does NOT decide *when* to call them or in what
order — that crash-safe sequencing (at-least-once: reviews before cursor) lives
in the orchestration layer. Storage only guarantees each individual write is
safe.
"""

import json
import os
from typing import TypedDict

from pipeline.config import settings


def atomic_write_json(path, data) -> None:
    """Write `data` as JSON to `path` so a crash can never leave a torn file.

    Strategy: write to a temp file in the same directory, then os.replace() it
    onto the target. os.replace is atomic on a single filesystem — readers see
    either the old file or the new file, never a half-written one. This matters
    most for the manifest, which is rewritten after every batch.

    ensure_ascii=False keeps non-Latin text (e.g. Chinese) as real UTF-8
    characters rather than \\uXXXX escapes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def append_reviews(app_id: int, reviews: list) -> int:
    """Append reviews to {app_id}_reviews.jsonl, one JSON object per line.

    JSON Lines (not one big array) is what makes append-per-batch cheap and
    crash-tolerant: we only ever add lines, never rewrite the file, and a crash
    leaves at most one half-written final line.

    ensure_ascii=False preserves non-English review text intact — the reason
    games like Overwatch 2 (majority Chinese reviews) are in the sample at all.

    Returns the number of reviews written.
    """
    path = settings.raw_reviews_dir / f"{app_id}_reviews.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        for review in reviews:
            handle.write(json.dumps(review, ensure_ascii=False) + "\n")
    return len(reviews)


def write_metadata(app_id: int, appdetails_data: dict, query_summary) -> None:
    """Write the game-level metadata file: storefront details + review totals.

    Combines the two halves of game-level truth — appdetails `data` (from the
    identity guard) and `query_summary` (from the first review batch) — into one
    atomic JSON file at {app_id}_metadata.json.
    """
    path = settings.raw_metadata_dir / f"{app_id}_metadata.json"
    payload = {
        "app_id": app_id,
        "appdetails": appdetails_data,
        "query_summary": query_summary,
    }
    atomic_write_json(path, payload)


def load_manifest() -> dict:
    """Load the global fetch manifest, or an empty dict if it does not exist yet."""
    path = settings.fetch_manifest_path
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_manifest(manifest: dict) -> None:
    """Persist the manifest atomically (it is rewritten after every batch)."""
    atomic_write_json(settings.fetch_manifest_path, manifest)


# Manifest status vocabulary. storage owns the manifest *file format*, so the set
# of legal status strings lives here; the orchestrator owns the transitions.
STATUS_PENDING = "pending"          # not started
STATUS_IN_PROGRESS = "in_progress"  # started, resumable from last_cursor
STATUS_DONE = "done"                # walk complete
STATUS_SKIPPED = "skipped"          # identity guard failed (see guard_status)
STATUS_FAILED = "failed"            # unexpected error


class GameRecord(TypedDict):
    """One game's entry in the fetch manifest.

    This is the documented shape of every value in fetch_manifest.json. storage
    persists these; the orchestrator builds and transitions them. load/save are
    typed loosely as plain dict so partial test fixtures stay convenient, but
    real records always conform to this schema.
    """

    name: str
    status: str               # one of the STATUS_* constants above
    last_cursor: str | None
    reviews_written: int
    guard_status: str | None  # ok / mismatch / no_data
    guard_ratio: float | None
    actual_name: str | None
    started_at: str
    updated_at: str
    error: str | None