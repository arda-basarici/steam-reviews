"""Tests for pipeline.writer.

Two groups:
  * _atomic_write tests use a fake serializer (no pyarrow) — the rename/crash-safety
    logic is what matters and is fully testable here.
  * Parquet round-trip tests need pyarrow; they prove dtypes (and the genres
    list<string> column) survive a write/read cycle.
"""

from dataclasses import replace
from unittest.mock import patch

import pandas as pd

from pipeline import writer


def _patched_settings(tmp_path):
    test_settings = replace(
        writer.settings,
        reviews_parquet=tmp_path / "processed" / "reviews.parquet",
        metadata_parquet=tmp_path / "processed" / "metadata.parquet",
    )
    return patch.object(writer, "settings", test_settings)


# --- atomic write (no pyarrow) ----------------------------------------------

def test_atomic_write_creates_parent_and_writes(tmp_path):
    target = tmp_path / "deep" / "nested" / "out.txt"
    writer._atomic_write(target, lambda p: p.write_text("hello"))
    assert target.exists()
    assert target.read_text() == "hello"


def test_atomic_write_leaves_no_tmp_file(tmp_path):
    target = tmp_path / "out.txt"
    writer._atomic_write(target, lambda p: p.write_text("x"))
    assert list(tmp_path.glob("*.tmp")) == []     # temp renamed away, not left


def test_atomic_write_returns_final_path(tmp_path):
    target = tmp_path / "out.txt"
    result = writer._atomic_write(target, lambda p: p.write_text("x"))
    assert result == target


# --- Parquet round-trip (requires pyarrow) ----------------------------------

def test_reviews_roundtrip_preserves_data(tmp_path):
    df = pd.DataFrame({
        "app_id": pd.array([550, 570], dtype="Int64"),
        "recommendationid": pd.array(["1", "2"], dtype="string"),
        "review": pd.array(["好玩", "good"], dtype="string"),
        "weighted_vote_score": [0.5, 0.9],
    })
    with _patched_settings(tmp_path):
        path = writer.write_processed_reviews(df)
        back = pd.read_parquet(path)
    assert path.exists()
    assert list(back["recommendationid"]) == ["1", "2"]
    assert back.loc[0, "review"] == "好玩"          # unicode survives Parquet


def test_metadata_roundtrip_preserves_genres_list(tmp_path):
    df = pd.DataFrame({
        "app_id": pd.array([550], dtype="Int64"),
        "name": pd.array(["Left 4 Dead 2"], dtype="string"),
        "genres": [["Action", "Indie"]],            # the list<string> column
    })
    with _patched_settings(tmp_path):
        path = writer.write_processed_metadata(df)
        back = pd.read_parquet(path)
    assert list(back.loc[0, "genres"]) == ["Action", "Indie"]   # list survives # type: ignore[arg-type]