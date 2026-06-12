"""orchestrator.py — the fetch conductor.

Ties together the three lower layers into one resumable run:
  fetcher.check_identity   (is this the right game?)
  fetcher.iter_review_batches  (stream the reviews)
  storage.*                (persist reviews, metadata, manifest)

It owns the *sequencing* the lower layers deliberately don't: the at-least-once
order (reviews to disk before the cursor is recorded), resume from the manifest,
and stopping cleanly when Steam is unreachable.
"""

import json
from datetime import datetime, timezone

from pipeline import fetcher, storage
from pipeline.config import settings
from pipeline.storage import GameRecord


def _now() -> str:
    """Current UTC time as an ISO-8601 string, for manifest timestamps."""
    return datetime.now(timezone.utc).isoformat()


def load_game_list() -> list:
    """Read the curated game list (the fetch input)."""
    with open(settings.game_list_path, encoding="utf-8") as handle:
        return json.load(handle)["games"]


def fetch_game(app_id: int, name: str, manifest: dict) -> GameRecord:
    """Fetch one game end to end, updating `manifest` in place. Returns its record.

    Resume-aware: a `done` game is skipped; an `in_progress` game continues from
    its saved cursor (trusting the recorded guard result, metadata already on
    disk); a fresh game runs the identity guard first and is skipped if it fails.
    """
    key = str(app_id)
    record: GameRecord | None = manifest.get(key)

    if record is not None and record["status"] == storage.STATUS_DONE:
        return record  # already complete — nothing to do

    if record is not None and record["status"] == storage.STATUS_IN_PROGRESS:
        # Resume: guard already passed, metadata already on disk.
        current: GameRecord = record
        start_cursor = current["last_cursor"] or "*"
        reviews_written = current["reviews_written"]
        appdetails_data = None
    else:
        # Fresh game: confirm identity before fetching anything.
        guard = fetcher.check_identity(app_id, name)
        if guard.status != fetcher.GUARD_OK:
            skipped: GameRecord = {
                "name": name,
                "status": storage.STATUS_SKIPPED,
                "last_cursor": None,
                "reviews_written": 0,
                "guard_status": guard.status,
                "guard_ratio": guard.ratio,
                "actual_name": guard.actual_name,
                "started_at": _now(),
                "updated_at": _now(),
                "error": None,
            }
            manifest[key] = skipped
            storage.save_manifest(manifest)
            return skipped

        start_cursor = "*"
        reviews_written = 0
        appdetails_data = guard.data
        current = {
            "name": name,
            "status": storage.STATUS_IN_PROGRESS,
            "last_cursor": "*",
            "reviews_written": 0,
            "guard_status": guard.status,
            "guard_ratio": guard.ratio,
            "actual_name": guard.actual_name,
            "started_at": _now(),
            "updated_at": _now(),
            "error": None,
        }
        manifest[key] = current
        storage.save_manifest(manifest)  # mark in_progress before any fetch

    # Walk the reviews. AT-LEAST-ONCE: write the batch to disk, THEN record the
    # cursor. A crash between the two re-fetches one batch (a duplicate the
    # cleaner removes) but never loses one. We mutate `current` in place — it is
    # the same object stored in the manifest, so save_manifest persists it.
    for batch in fetcher.iter_review_batches(app_id, start_cursor):
        if batch.reviews:
            storage.append_reviews(app_id, batch.reviews)
            reviews_written += len(batch.reviews)

        # Metadata is written exactly once: on the fresh first batch, the only
        # moment both halves are in hand (appdetails data + query_summary).
        if appdetails_data is not None and batch.query_summary is not None:
            storage.write_metadata(app_id, appdetails_data, batch.query_summary)

        current["last_cursor"] = batch.next_cursor
        current["reviews_written"] = reviews_written
        current["updated_at"] = _now()
        storage.save_manifest(manifest)

    current["status"] = storage.STATUS_DONE
    current["updated_at"] = _now()
    storage.save_manifest(manifest)
    return current


def _summarize(manifest: dict) -> dict:
    """Count manifest records by status, for the end-of-run summary."""
    counts: dict = {}
    for rec in manifest.values():
        counts[rec["status"]] = counts.get(rec["status"], 0) + 1
    return counts


def run_fetch(games: list | None = None) -> dict:
    """Fetch all games, resuming from the manifest. Returns the final manifest.

    Stops cleanly on SteamAPIError (Steam unreachable): the manifest is already
    saved batch-by-batch, so a rerun resumes where it stopped.
    """
    if games is None:
        games = load_game_list()
    manifest = storage.load_manifest()

    mode = "SAMPLE" if settings.sample_mode else "FULL"
    print(f"[fetch] mode={mode}  cap/game={settings.effective_reviews_cap}  "
          f"games={len(games)}")

    try:
        for index, game in enumerate(games, start=1):
            record = fetch_game(game["app_id"], game["name"], manifest)
            print(f"  [{index}/{len(games)}] {game['name']}: "
                  f"{record['status']} ({record['reviews_written']} reviews)")
    except fetcher.SteamAPIError as exc:
        print(f"[fetch] STOPPED — Steam unreachable: {exc}")
        print("[fetch] Progress saved; rerun to resume from where it stopped.")
        raise SystemExit(1)

    print(f"[fetch] complete: {_summarize(manifest)}")
    return manifest