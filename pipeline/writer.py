"""writer.py — write the cleaned tables to Parquet (the processed-data output).

Separate from storage.py on purpose. storage.py is the RAW-data disk layer and is
deliberately stdlib-only, so the fetch path stays dependency-light. This module is
the PROCESSED-data disk layer: it needs pandas + pyarrow to write Parquet, and only
the cleaning path imports it.

Writes are atomic (temp file + os.replace), the same crash-safety storage uses for
JSON: a reader sees either the old Parquet or the complete new one, never a
half-written file. Parquet is chosen for the processed tables because it is typed
and compressed, and round-trips pandas dtypes — including the genres list<string>
column — losslessly via the pyarrow engine.
"""

import os
from pathlib import Path
from typing import Callable

import pandas as pd

from pipeline.config import settings


def _atomic_write(path: Path, write_fn: Callable[[Path], object]) -> Path:
    """Run `write_fn` against a temp file, then atomically move it onto `path`.

    write_fn does the actual serialization (it receives the temp path); its return
    value, if any, is ignored. Keeping the atomic move separate from the serializer
    means this rename logic is testable without pyarrow, and any writer (Parquet,
    CSV, ...) can reuse it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    write_fn(tmp_path)
    os.replace(tmp_path, path)
    return path


def write_processed_reviews(reviews: pd.DataFrame) -> Path:
    """Write the cleaned review-level table to data/processed/reviews.parquet."""
    return _atomic_write(
        settings.reviews_parquet,
        lambda p: reviews.to_parquet(p, index=False),
    )


def write_processed_metadata(metadata: pd.DataFrame) -> Path:
    """Write the cleaned game-level table to data/processed/metadata.parquet."""
    return _atomic_write(
        settings.metadata_parquet,
        lambda p: metadata.to_parquet(p, index=False),
    )


def write_processed(reviews: pd.DataFrame, metadata: pd.DataFrame) -> tuple[Path, Path]:
    """Write both processed tables; return their paths."""
    return write_processed_reviews(reviews), write_processed_metadata(metadata)