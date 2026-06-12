"""Tests for pipeline.storage.

Unlike the fetcher tests, these touch real files — storage IS file I/O — but
only inside pytest's tmp_path, never the project's data/ dirs. settings is
patched with a frozen copy pointing at the temp directory.
"""

import json
from dataclasses import replace
from unittest.mock import patch

from pipeline import storage


def _patched_settings(tmp_path):
    """A frozen settings copy with all storage paths under tmp_path."""
    test_settings = replace(
        storage.settings,
        raw_reviews_dir=tmp_path / "reviews",
        raw_metadata_dir=tmp_path / "metadata",
        fetch_manifest_path=tmp_path / "fetch_manifest.json",
    )
    return patch.object(storage, "settings", test_settings)


def test_atomic_write_creates_dirs_and_roundtrips(tmp_path):
    target = tmp_path / "nested" / "dir" / "data.json"
    payload = {"a": 1, "list": [1, 2, 3]}
    storage.atomic_write_json(target, payload)
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8")) == payload


def test_atomic_write_leaves_no_tmp_file(tmp_path):
    target = tmp_path / "data.json"
    storage.atomic_write_json(target, {"x": 1})
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []          # temp file was renamed away, not left behind


def test_atomic_write_preserves_unicode(tmp_path):
    target = tmp_path / "u.json"
    storage.atomic_write_json(target, {"text": "好玩"})
    raw = target.read_text(encoding="utf-8")
    assert "好玩" in raw            # real characters, not \uXXXX escapes


def test_append_reviews_writes_one_line_each_and_accumulates(tmp_path):
    with _patched_settings(tmp_path):
        n1 = storage.append_reviews(123, [{"recommendationid": "1"}, {"recommendationid": "2"}])
        n2 = storage.append_reviews(123, [{"recommendationid": "3"}])
        path = (tmp_path / "reviews" / "123_reviews.jsonl")
        lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert (n1, n2) == (2, 1)
    assert len(lines) == 3
    assert json.loads(lines[0])["recommendationid"] == "1"
    assert json.loads(lines[2])["recommendationid"] == "3"


def test_append_reviews_preserves_non_english_text(tmp_path):
    with _patched_settings(tmp_path):
        storage.append_reviews(730, [{"review": "这游戏很好玩"}])
        path = tmp_path / "reviews" / "730_reviews.jsonl"
        content = path.read_text(encoding="utf-8")
    assert "这游戏很好玩" in content


def test_write_metadata_combines_details_and_summary(tmp_path):
    with _patched_settings(tmp_path):
        storage.write_metadata(
            265930,
            appdetails_data={"name": "Goat Simulator", "is_free": False},
            query_summary={"total_reviews": 1000},
        )
        path = tmp_path / "metadata" / "265930_metadata.json"
        data = json.loads(path.read_text(encoding="utf-8"))
    assert data["app_id"] == 265930
    assert data["appdetails"]["name"] == "Goat Simulator"
    assert data["query_summary"]["total_reviews"] == 1000


def test_load_manifest_returns_empty_when_absent(tmp_path):
    with _patched_settings(tmp_path):
        assert storage.load_manifest() == {}


def test_manifest_save_then_load_roundtrips(tmp_path):
    manifest = {"265930": {"status": "done", "reviews_written": 500}}
    with _patched_settings(tmp_path):
        storage.save_manifest(manifest)
        loaded = storage.load_manifest()
    assert loaded == manifest