"""schemas.py — pandera schemas for the cleaned tables (the declarative contract).

Two schemas per table:
  *_STRUCTURE : HARD rules — required columns present, non-null keys, unique ids.
  *_RANGES    : SOFT rules — value bounds that are unusual-but-not-impossible.

Dtypes are intentionally NOT asserted here: the cleaner already coerces them, and
pinning exact pandas nullable/arrow dtype strings is brittle across versions. The
contract focuses on presence, keys, and value ranges. Cross-table and cross-column
checks live in validate.py (plain Python).

strict=False everywhere: extra columns are fine; we only assert about the ones we
name. required=False on range columns: check the bound only if the column exists
(its presence is the structure schema's job, so faults aren't double-reported).
"""

try:
    import pandera.pandas as pa            # modern namespace (pandera >= ~0.20)
except ImportError:                        # pragma: no cover - older pandera
    import pandera as pa


# --- reviews -----------------------------------------------------------------
REVIEWS_STRUCTURE = pa.DataFrameSchema(
    {
        "app_id": pa.Column(nullable=False),
        "recommendationid": pa.Column(nullable=False, unique=True),  # dedup proof
        "language": pa.Column(nullable=False),
        "review": pa.Column(nullable=True),            # text column MUST exist
        "voted_up": pa.Column(nullable=True),
        "timestamp_created": pa.Column(nullable=True),
        "weighted_vote_score": pa.Column(nullable=True),
        # author fields that must survive flattening:
        "steamid": pa.Column(nullable=True),
        "num_games_owned": pa.Column(nullable=True),
        "playtime_forever": pa.Column(nullable=True),
        "playtime_at_review": pa.Column(nullable=True),
    },
    strict=False,
    name="reviews_structure",
)

REVIEWS_RANGES = pa.DataFrameSchema(
    {
        "weighted_vote_score": pa.Column(checks=pa.Check.in_range(0, 1), nullable=True, required=False),
        "playtime_forever": pa.Column(checks=pa.Check.ge(0), nullable=True, required=False),
        "playtime_at_review": pa.Column(checks=pa.Check.ge(0), nullable=True, required=False),
        "votes_up": pa.Column(checks=pa.Check.ge(0), nullable=True, required=False),
        "votes_funny": pa.Column(checks=pa.Check.ge(0), nullable=True, required=False),
        "comment_count": pa.Column(checks=pa.Check.ge(0), nullable=True, required=False),
    },
    strict=False,
    name="reviews_ranges",
)


# --- metadata ----------------------------------------------------------------
METADATA_STRUCTURE = pa.DataFrameSchema(
    {
        "app_id": pa.Column(nullable=False, unique=True),   # one row per game
        "name": pa.Column(nullable=True),
        "total_positive": pa.Column(nullable=True),
        "total_negative": pa.Column(nullable=True),
        "total_reviews": pa.Column(nullable=True),
    },
    strict=False,
    name="metadata_structure",
)

METADATA_RANGES = pa.DataFrameSchema(
    {
        "total_positive": pa.Column(checks=pa.Check.ge(0), nullable=True, required=False),
        "total_negative": pa.Column(checks=pa.Check.ge(0), nullable=True, required=False),
        "total_reviews": pa.Column(checks=pa.Check.ge(0), nullable=True, required=False),
        "review_score": pa.Column(checks=pa.Check.in_range(0, 9), nullable=True, required=False),
        "price_final_usd": pa.Column(checks=pa.Check.ge(0), nullable=True, required=False),
        "metacritic_score": pa.Column(checks=pa.Check.in_range(0, 100), nullable=True, required=False),
    },
    strict=False,
    name="metadata_ranges",
)