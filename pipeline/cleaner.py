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
    authors = pd.json_normalize(list(df["author"]))
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


# =============================================================================
# Game-level metadata (one row per game, ~50 rows)
# =============================================================================
# Same column philosophy as reviews: keep analytic value, drop store-page cruft
# (HTML descriptions, image URLs, OS requirements, screenshots, per-country
# ratings, package groups) — all recoverable from the immutable raw JSON.
#
# Two grains live in one raw file and we keep them in one game-level row:
#   - `appdetails`     : the store listing (name, price, genres, release, ...)
#   - `query_summary`  : WHOLE-POPULATION review totals (e.g. 1,037,403 for L4D2),
#                        NOT our 500-row sample. These are the denominators for
#                        per-game sentiment and review-bombing rates.


def _genre_list(appdetails: dict) -> list:
    """genres is [{'id','description'}, ...] -> ['Action', 'RPG']; [] if absent.

    Kept as a list (not a joined string): faithful to the multi-valued structure,
    natively supported by Parquet's list<string> type, and explodable on demand
    in a notebook. Empty list (never None) so downstream code needn't special-case.
    """
    return [g["description"] for g in appdetails.get("genres", []) or []]


def clean_metadata(raw: pd.DataFrame) -> pd.DataFrame:
    """Build the game-level table from raw metadata records.

    `raw` has one row per game with an `app_id`, a nested `appdetails` dict, and a
    nested `query_summary` dict (as saved by storage.write_metadata).
    """
    rows = []
    for _, rec in raw.iterrows():
        app = rec.get("appdetails") or {}
        qs = rec.get("query_summary") or {}
        price = app.get("price_overview") or {}        # absent for free games
        release = app.get("release_date") or {}
        metacritic = app.get("metacritic") or {}
        recommendations = app.get("recommendations") or {}
        platforms = app.get("platforms") or {}

        price_final = price.get("final")
        price_initial = price.get("initial")

        rows.append({
            "app_id": rec.get("app_id"),
            "name": app.get("name"),
            "type": app.get("type"),
            "is_free": app.get("is_free"),
            "required_age": app.get("required_age"),
            # price: keep the live (fetch-time) price in dollars; None when free.
            "price_final_usd": price_final / 100 if price_final is not None else None,
            "price_initial_usd": price_initial / 100 if price_initial is not None else None,
            "discount_percent": price.get("discount_percent"),
            "genres": _genre_list(app),
            "release_date_raw": release.get("date"),     # e.g. "Nov 16, 2009"
            "coming_soon": release.get("coming_soon"),
            "metacritic_score": metacritic.get("score"),         # absent for many
            "recommendations_total": recommendations.get("total"),
            "platform_windows": platforms.get("windows"),
            "platform_mac": platforms.get("mac"),
            "platform_linux": platforms.get("linux"),
            # whole-population review totals (the denominators):
            "total_positive": qs.get("total_positive"),
            "total_negative": qs.get("total_negative"),
            "total_reviews": qs.get("total_reviews"),
            "review_score": qs.get("review_score"),
            "review_score_desc": qs.get("review_score_desc"),
        })

    df = pd.DataFrame(rows)

    # dtypes — tolerant of the real gaps (free games, missing metacritic, vague
    # release dates). errors='coerce' turns the unparseable into NA, not crashes.
    df["release_date"] = pd.to_datetime(df["release_date_raw"], errors="coerce")
    df = df.drop(columns=["release_date_raw"])

    int_cols = [
        "app_id", "required_age", "discount_percent", "metacritic_score",
        "recommendations_total", "total_positive", "total_negative",
        "total_reviews", "review_score",
    ]
    for col in int_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    for col in ["price_final_usd", "price_initial_usd"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["is_free", "coming_soon", "platform_windows", "platform_mac", "platform_linux"]:
        df[col] = df[col].astype("boolean")
    for col in ["name", "type", "review_score_desc"]:
        df[col] = df[col].astype("string")

    return df.reset_index(drop=True)