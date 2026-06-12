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


# ---------------------------------------------------------------------------
# clean_metadata — game-level table
# ---------------------------------------------------------------------------

from pipeline.cleaner import clean_metadata


def _meta_raw():
    rows = [
        {  # paid game, full data (mirrors real Left 4 Dead 2 record)
            "app_id": 550,
            "appdetails": {
                "type": "game", "name": "Left 4 Dead 2", "is_free": False,
                "required_age": 0,
                "price_overview": {"currency": "USD", "initial": 999,
                                   "final": 999, "discount_percent": 0},
                "genres": [{"id": "1", "description": "Action"}],
                "release_date": {"coming_soon": False, "date": "Nov 16, 2009"},
                "metacritic": {"score": 89},
                "recommendations": {"total": 123456},
                "platforms": {"windows": True, "mac": False, "linux": True},
                # cruft that must NOT appear in the clean table:
                "detailed_description": "<p>blah</p>", "screenshots": [{}],
                "pc_requirements": {"minimum": "<strong>..."},
            },
            "query_summary": {"num_reviews": 80, "review_score": 9,
                              "review_score_desc": "Overwhelmingly Positive",
                              "total_positive": 1011765, "total_negative": 25638,
                              "total_reviews": 1037403},
        },
        {  # FREE game: no price_overview, no metacritic, two genres
            "app_id": 570,
            "appdetails": {
                "type": "game", "name": "Dota 2", "is_free": True,
                "required_age": 0,
                "genres": [{"id": "1", "description": "Action"},
                           {"id": "2", "description": "Strategy"}],
                "release_date": {"coming_soon": False, "date": "Jul 9, 2013"},
                "platforms": {"windows": True, "mac": True, "linux": True},
            },
            "query_summary": {"total_positive": 100, "total_negative": 10,
                              "total_reviews": 110, "review_score": 8,
                              "review_score_desc": "Very Positive"},
        },
        {  # coming-soon: no genres key, unparseable date
            "app_id": 999,
            "appdetails": {
                "type": "game", "name": "Future Game", "is_free": False,
                "required_age": 0,
                "release_date": {"coming_soon": True, "date": "Coming soon"},
                "platforms": {"windows": True, "mac": False, "linux": False},
            },
            "query_summary": {"total_positive": 0, "total_negative": 0,
                              "total_reviews": 0},
        },
    ]
    return pd.DataFrame(rows)


def test_metadata_cruft_dropped():
    out = clean_metadata(_meta_raw())
    for cruft in ("detailed_description", "screenshots", "pc_requirements",
                  "appdetails", "query_summary"):
        assert cruft not in out.columns


def test_metadata_paid_game_fields():
    out = clean_metadata(_meta_raw())
    row = out[out["app_id"] == 550].iloc[0]
    assert row["name"] == "Left 4 Dead 2"
    assert abs(row["price_final_usd"] - 9.99) < 1e-9
    assert row["metacritic_score"] == 89
    assert row["total_reviews"] == 1037403          # population, not our sample
    assert row["release_date"] == pd.Timestamp("2009-11-16")


def test_metadata_genres_is_a_list():
    out = clean_metadata(_meta_raw())
    assert out[out["app_id"] == 550].iloc[0]["genres"] == ["Action"]
    assert out[out["app_id"] == 570].iloc[0]["genres"] == ["Action", "Strategy"]
    assert out[out["app_id"] == 999].iloc[0]["genres"] == []   # missing -> empty


def test_metadata_free_game_has_no_price():
    out = clean_metadata(_meta_raw())
    row = out[out["app_id"] == 570].iloc[0]
    assert row["is_free"] == True
    assert pd.isna(row["price_final_usd"])          # no price_overview -> NA
    assert pd.isna(out[out["app_id"] == 570].iloc[0]["metacritic_score"])


def test_metadata_coming_soon_date_is_nat_not_crash():
    out = clean_metadata(_meta_raw())
    row = out[out["app_id"] == 999].iloc[0]
    assert row["coming_soon"] == True
    assert pd.isna(row["release_date"])             # "Coming soon" -> NaT


def test_metadata_dtypes():
    out = clean_metadata(_meta_raw())
    assert str(out["total_reviews"].dtype) == "Int64"
    assert pd.api.types.is_float_dtype(out["price_final_usd"])
    assert out["is_free"].dtype == "boolean"
    assert out["name"].dtype == "string"
    assert pd.api.types.is_datetime64_any_dtype(out["release_date"])