"""Tests for pipeline.cleaner.

The cleaner takes a DataFrame, so tests build tiny in-memory messy frames — no
files, fast and deterministic. Each test targets one cleaning concern.
"""

import pandas as pd

from pipeline.cleaner import clean_reviews, KEEP_AUTHOR_FIELDS, KEEP_REVIEW_FIELDS


def _author(**over):
    base = {
        "steamid": "76561199000000001",
        "num_games_owned": 10,
        "num_reviews": 3,
        "playtime_forever": 1200,
        "playtime_last_two_weeks": 60,
        "playtime_at_review": 800,
        "last_played": 1781229000,
        # cruft that must be dropped:
        "personaname": "SomeName",
        "persona_status": "offline",
        "profile_url": "https://steamcommunity.com/id/x/",
        "avatar": "deadbeefhash",
    }
    base.update(over)
    return base


def _raw():
    rows = [
        {  # a normal review (Chinese text + CRLF, must survive untouched)
            "app_id": 730, "recommendationid": "111", "language": "schinese",
            "review": "好玩\r\nvery nice", "timestamp_created": 1781229432,
            "timestamp_updated": 1781229432, "voted_up": True, "votes_up": 5,
            "votes_funny": 0, "weighted_vote_score": "0.523809552192687988",
            "comment_count": 0, "steam_purchase": True, "received_for_free": False,
            "refunded": False, "written_during_early_access": False,
            "primarily_steam_deck": False, "reactions": [],
            "app_release_date": "1258434000", "author": _author(),
        },
        {  # EXACT duplicate recommendationid (at-least-once resume artifact)
            "app_id": 730, "recommendationid": "111", "language": "schinese",
            "review": "好玩\r\nvery nice", "timestamp_created": 1781229432,
            "timestamp_updated": 1781229432, "voted_up": True, "votes_up": 5,
            "votes_funny": 0, "weighted_vote_score": "0.523809552192687988",
            "comment_count": 0, "steam_purchase": True, "received_for_free": False,
            "refunded": False, "written_during_early_access": False,
            "primarily_steam_deck": False, "reactions": [],
            "app_release_date": "1258434000", "author": _author(),
        },
        {  # a second distinct review
            "app_id": 730, "recommendationid": "222", "language": "english",
            "review": "good game", "timestamp_created": 1781227823,
            "timestamp_updated": 1781227823, "voted_up": False, "votes_up": 0,
            "votes_funny": 1, "weighted_vote_score": "0", "comment_count": 2,
            "steam_purchase": False, "received_for_free": True, "refunded": False,
            "written_during_early_access": True, "primarily_steam_deck": True,
            "reactions": [], "app_release_date": "1258434000",
            "author": _author(steamid="76561199000000002", num_games_owned=200),
        },
    ]
    return pd.DataFrame(rows)


def test_cruft_columns_are_dropped():
    out = clean_reviews(_raw())
    for cruft in ("personaname", "persona_status", "profile_url", "avatar",
                  "reactions", "app_release_date"):
        assert cruft not in out.columns


def test_kept_author_and_review_fields_present():
    out = clean_reviews(_raw())
    for col in KEEP_AUTHOR_FIELDS + KEEP_REVIEW_FIELDS:
        assert col in out.columns


def test_dedup_removes_repeated_recommendationid():
    out = clean_reviews(_raw())
    assert len(out) == 2                       # 3 rows in, one was a dup
    assert sorted(out["recommendationid"]) == ["111", "222"]


def test_weighted_vote_score_becomes_float():
    out = clean_reviews(_raw())
    assert pd.api.types.is_float_dtype(out["weighted_vote_score"])
    val = out.loc[out["recommendationid"] == "111", "weighted_vote_score"].iloc[0]
    assert abs(val - 0.523809552192687988) < 1e-9


def test_timestamps_become_datetime():
    out = clean_reviews(_raw())
    assert pd.api.types.is_datetime64_any_dtype(out["timestamp_created"])
    assert pd.api.types.is_datetime64_any_dtype(out["last_played"])


def test_review_text_preserved_exactly():
    out = clean_reviews(_raw())
    text = out.loc[out["recommendationid"] == "111", "review"].iloc[0]
    assert text == "好玩\r\nvery nice"          # Chinese + CRLF untouched


def test_dtypes_are_honest():
    out = clean_reviews(_raw())
    assert out["voted_up"].dtype == "boolean"
    assert out["recommendationid"].dtype == "string"
    assert out["steamid"].dtype == "string"     # kept as string, no precision loss
    assert str(out["num_games_owned"].dtype) == "Int64"