"""Tests for pipeline.orchestrator.

The orchestrator is pure sequencing, so every lower layer is mocked: the guard,
the pagination stream, and all storage writes. Tests verify the *order* and
*branching*, not real I/O.
"""

from unittest.mock import patch, MagicMock

import pytest

from pipeline import orchestrator, fetcher, storage
from pipeline.fetcher import GuardResult, ReviewBatch, GUARD_OK, GUARD_MISMATCH, GUARD_NO_DATA


def _guard(status, name="Game", actual: str | None = "Game",
           ratio: float | None = 1.0, data: dict | None = None):
    return GuardResult(status, name, actual, ratio, data)


def test_done_game_is_skipped_without_guard_or_fetch():
    manifest = {"1": {"status": storage.STATUS_DONE, "reviews_written": 500, "name": "Game"}}
    with patch.object(fetcher, "check_identity") as mock_guard, \
         patch.object(fetcher, "iter_review_batches") as mock_iter:
        record = orchestrator.fetch_game(1, "Game", manifest)
    assert record["status"] == storage.STATUS_DONE
    mock_guard.assert_not_called()
    mock_iter.assert_not_called()


def test_guard_mismatch_records_skip_and_does_not_fetch():
    manifest = {}
    with patch.object(fetcher, "check_identity",
                      return_value=_guard(GUARD_MISMATCH, actual="Pavlov VR", ratio=0.2)), \
         patch.object(fetcher, "iter_review_batches") as mock_iter, \
         patch.object(storage, "save_manifest"):
        record = orchestrator.fetch_game(555160, "Goat Simulator", manifest)
    assert record["status"] == storage.STATUS_SKIPPED
    assert record["guard_status"] == GUARD_MISMATCH
    assert record["actual_name"] == "Pavlov VR"
    mock_iter.assert_not_called()


def test_guard_no_data_records_skip():
    manifest = {}
    with patch.object(fetcher, "check_identity",
                      return_value=_guard(GUARD_NO_DATA, actual=None, ratio=None)), \
         patch.object(fetcher, "iter_review_batches") as mock_iter, \
         patch.object(storage, "save_manifest"):
        record = orchestrator.fetch_game(1372880, "The Day Before", manifest)
    assert record["status"] == storage.STATUS_SKIPPED
    assert record["guard_status"] == GUARD_NO_DATA
    mock_iter.assert_not_called()


def test_happy_path_writes_reviews_metadata_and_marks_done():
    manifest = {}
    batches = [
        ReviewBatch([{"recommendationid": "1"}, {"recommendationid": "2"}], "c1", {"total_reviews": 3}),
        ReviewBatch([{"recommendationid": "3"}], None, None),
    ]
    with patch.object(fetcher, "check_identity",
                      return_value=_guard(GUARD_OK, data={"name": "Game"})), \
         patch.object(fetcher, "iter_review_batches", return_value=batches), \
         patch.object(storage, "append_reviews") as mock_append, \
         patch.object(storage, "write_metadata") as mock_meta, \
         patch.object(storage, "save_manifest"):
        record = orchestrator.fetch_game(1, "Game", manifest)
    assert record["status"] == storage.STATUS_DONE
    assert record["reviews_written"] == 3
    assert mock_append.call_count == 2          # one per non-empty batch
    mock_meta.assert_called_once()              # metadata written exactly once
    # metadata got appdetails data + first-batch summary
    args = mock_meta.call_args
    assert args.args[1] == {"name": "Game"}
    assert args.args[2] == {"total_reviews": 3}


def test_at_least_once_writes_reviews_before_recording_cursor():
    manifest = {}
    batches = [ReviewBatch([{"recommendationid": "1"}], None, {"total_reviews": 1})]
    manager = MagicMock()
    with patch.object(fetcher, "check_identity",
                      return_value=_guard(GUARD_OK, data={"name": "Game"})), \
         patch.object(fetcher, "iter_review_batches", return_value=batches), \
         patch.object(storage, "append_reviews", manager.append), \
         patch.object(storage, "write_metadata"), \
         patch.object(storage, "save_manifest", manager.save):
        orchestrator.fetch_game(1, "Game", manifest)
    names = [c[0] for c in manager.mock_calls]
    # every append must be followed by a save (cursor recorded after disk write)
    for i, n in enumerate(names):
        if n == "append":
            assert any(names[j] == "save" for j in range(i + 1, len(names)))


def test_resume_continues_from_saved_cursor_without_reguarding():
    manifest = {"1": {
        "status": storage.STATUS_IN_PROGRESS, "last_cursor": "cX",
        "reviews_written": 160, "name": "Game",
        "guard_status": GUARD_OK, "guard_ratio": 1.0, "actual_name": "Game",
        "started_at": "t", "updated_at": "t", "error": None,
    }}
    batches = [ReviewBatch([{"recommendationid": "x"}], None, None)]
    with patch.object(fetcher, "check_identity") as mock_guard, \
         patch.object(fetcher, "iter_review_batches", return_value=batches) as mock_iter, \
         patch.object(storage, "append_reviews", return_value=1), \
         patch.object(storage, "write_metadata") as mock_meta, \
         patch.object(storage, "save_manifest"):
        record = orchestrator.fetch_game(1, "Game", manifest)
    mock_guard.assert_not_called()              # trusts recorded guard result
    mock_iter.assert_called_once_with(1, "cX")  # resumes from saved cursor
    mock_meta.assert_not_called()               # metadata already exists
    assert record["status"] == storage.STATUS_DONE
    assert record["reviews_written"] == 161     # 160 + 1 new


def test_run_fetch_stops_cleanly_on_steam_unreachable():
    games = [{"app_id": 1, "name": "Game"}]
    with patch.object(storage, "load_manifest", return_value={}), \
         patch.object(orchestrator, "fetch_game", side_effect=fetcher.SteamAPIError("down")), \
         patch.object(storage, "save_manifest"):
        with pytest.raises(SystemExit):
            orchestrator.run_fetch(games)