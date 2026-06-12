"""cleaner.py — turn raw Steam review JSON into tidy, analysis-ready tables.

No network, no model assumptions. The cleaner takes already-loaded raw data
(DataFrames) and returns clean ones; reading files is a separate loader's job, so
the transform logic here is testable against tiny in-memory frames.

What "clean" means here: flat columns (nested `author` expanded), real datetimes,
honest dtypes, deduplicated, review text preserved byte-for-byte. It is
deliberately model-agnostic — turning this into PyTorch features is a Phase 3
concern that reads this output, not something baked in here.

Column policy (see KEEP_* below): we keep generously — every field with any
plausible analytic value to anyone, not just our five questions — and drop only
pure identity/UI cruft. Nothing is truly lost: the raw JSONL on disk is immutable
and complete, so a different analyst can re-clean with a different column list.
"""

import pandas as pd

# --- column policy -----------------------------------------------------------
# Author sub-fields worth keeping (reviewer profile + behaviour). Dropped author
# fields — personaname, persona_status, profile_url, avatar — are pure identity/
# UI cruft with no analytic value (and recoverable from raw if ever needed).
KEEP_AUTHOR_FIELDS = [
    "steamid",
    "num_games_owned",
    "num_reviews",
    "playtime_forever",
    "playtime_last_two_weeks",
    "playtime_at_review",
    "last_played",
]

# Top-level review fields worth keeping. Dropped (recoverable from raw):
# `reactions` (list, awkward and low-value), `app_release_date` (game-level —
# lives in the metadata table), `developer_response`/`timestamp_dev_responded`
# (rarely present). `app_id` is added by the loader as the join key to metadata.
KEEP_REVIEW_FIELDS = [
    "app_id",
    "recommendationid",
    "language",
    "review",
    "timestamp_created",
    "timestamp_updated",
    "voted_up",
    "votes_up",
    "votes_funny",
    "weighted_vote_score",
    "comment_count",
    "steam_purchase",
    "received_for_free",
    "refunded",
    "written_during_early_access",
    "primarily_steam_deck",
]

# --- dtype groups (applied after selection) ----------------------------------
_UNIX_TIMESTAMP_COLS = ["timestamp_created", "timestamp_updated", "last_played"]
_BOOL_COLS = [
    "voted_up", "steam_purchase", "received_for_free",
    "refunded", "written_during_early_access", "primarily_steam_deck",
]
_INT_COLS = [
    "app_id", "num_games_owned", "num_reviews", "playtime_forever",
    "playtime_last_two_weeks", "playtime_at_review", "votes_up",
    "votes_funny", "comment_count",
]
# Identifiers kept as strings: huge 64-bit ids (steamid, recommendationid) would
# lose precision as floats, and they're identifiers, not quantities anyway.
_STRING_COLS = ["recommendationid", "steamid", "language", "review"]


def clean_reviews(raw: pd.DataFrame) -> pd.DataFrame:
    """Flatten, type, and deduplicate raw review records into a tidy DataFrame.

    `raw` is expected to have one row per review, an `app_id` column (added by the
    loader), and a nested `author` dict column. Review text is never altered.
    """
    df = raw.reset_index(drop=True)

    # 1) Flatten the nested `author` dict into selected top-level columns.
    authors = authors = pd.json_normalize(list(df["author"]))
    for field in KEEP_AUTHOR_FIELDS:
        df[field] = authors[field] if field in authors.columns else pd.NA

    # 2) Select the kept columns (cruft dropped; raw retains everything).
    keep = [c for c in KEEP_REVIEW_FIELDS + KEEP_AUTHOR_FIELDS if c in df.columns]
    df = df[keep].copy()

    # 3) Deduplicate. The fetcher's at-least-once design can re-write one batch
    #    after a crash, so a repeated recommendationid is expected, not an error.
    df = df.drop_duplicates(subset="recommendationid", keep="first")

    # 4) Coerce dtypes to something honest and Parquet-friendly.
    if "weighted_vote_score" in df.columns:
        # Steam returns this as a STRING (e.g. "0.523..."). Make it a float.
        df["weighted_vote_score"] = pd.to_numeric(
            df["weighted_vote_score"], errors="coerce"
        )
    for col in _UNIX_TIMESTAMP_COLS:
        if col in df.columns:
            # Unix seconds -> timezone-aware UTC datetime.
            df[col] = pd.to_datetime(df[col], unit="s", utc=True)
    for col in _INT_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    for col in _BOOL_COLS:
        if col in df.columns:
            df[col] = df[col].astype("boolean")
    for col in _STRING_COLS:
        if col in df.columns:
            df[col] = df[col].astype("string")

    return df.reset_index(drop=True)