"""Tests for the validation contract.

Two groups:
  * validate() end-to-end tests exercise the pandera schemas (require pandera).
  * Direct cross-table tests exercise the plain-Python checks (no pandera).

The deliberately-broken frames prove the HARD stops actually fire, and that soft
oddities only warn.
"""

import pandas as pd
import pytest

from validation import validate, ValidationError, ValidationReport
from validation.validate import (
    _check_referential_integrity,
    _check_row_counts,
    _check_sentiment_totals,
    _check_future_timestamps,
)


def _good_reviews():
    return pd.DataFrame({
        "app_id": pd.array([550, 550, 570], dtype="Int64"),
        "recommendationid": pd.array(["1", "2", "3"], dtype="string"),
        "language": pd.array(["english", "russian", "schinese"], dtype="string"),
        "review": pd.array(["great", "ok", "好玩"], dtype="string"),
        "voted_up": pd.array([True, False, True], dtype="boolean"),
        "timestamp_created": pd.to_datetime([1700000000, 1700000001, 1700000002], unit="s", utc=True),
        "weighted_vote_score": [0.5, 0.0, 0.9],
        "steamid": pd.array(["76561199000000001", "76561199000000002", "76561199000000003"], dtype="string"),
        "num_games_owned": pd.array([10, 20, 30], dtype="Int64"),
        "playtime_forever": pd.array([100, 200, 300], dtype="Int64"),
        "playtime_at_review": pd.array([50, 150, 250], dtype="Int64"),
        "votes_up": pd.array([1, 0, 2], dtype="Int64"),
        "votes_funny": pd.array([0, 0, 1], dtype="Int64"),
        "comment_count": pd.array([0, 1, 0], dtype="Int64"),
    })


def _good_metadata():
    return pd.DataFrame({
        "app_id": pd.array([550, 570], dtype="Int64"),
        "name": pd.array(["Left 4 Dead 2", "Dota 2"], dtype="string"),
        "total_positive": pd.array([1000, 500], dtype="Int64"),
        "total_negative": pd.array([100, 50], dtype="Int64"),
        "total_reviews": pd.array([1100, 550], dtype="Int64"),
        "review_score": pd.array([9, 8], dtype="Int64"),
        "price_final_usd": [9.99, None],
        "metacritic_score": pd.array([89, pd.NA], dtype="Int64"),
    })


# === end-to-end validate() (require pandera) ================================

def test_valid_data_passes():
    report = validate(_good_reviews(), _good_metadata(), verbose=False)
    assert report.ok
    assert report.hard_failures == []


def test_duplicate_recommendationid_is_hard_failure():
    rev = _good_reviews()
    rev.loc[1, "recommendationid"] = "1"            # dup -> dedup "failed"
    with pytest.raises(ValidationError) as exc:
        validate(rev, _good_metadata(), verbose=False)
    assert any("recommendationid" in m for m in exc.value.report.hard_failures)


def test_missing_required_column_is_hard_failure():
    rev = _good_reviews().drop(columns=["review"])  # text column gone
    with pytest.raises(ValidationError):
        validate(rev, _good_metadata(), verbose=False)


def test_score_out_of_range_is_warning_not_failure():
    rev = _good_reviews()
    rev.loc[0, "weighted_vote_score"] = 1.5         # impossible-ish but soft
    report = validate(rev, _good_metadata(), verbose=False)
    assert report.ok                                 # did NOT raise
    assert any("weighted_vote_score" in w for w in report.warnings)


# === direct cross-table checks (no pandera) ================================

def test_referential_integrity_flags_orphan_app_id():
    rev = _good_reviews()
    rev.loc[0, "app_id"] = 999                       # not in metadata
    hard = []
    _check_referential_integrity(rev, _good_metadata(), hard)
    assert len(hard) == 1 and "999" in hard[0]


def test_referential_integrity_passes_when_all_present():
    hard = []
    _check_referential_integrity(_good_reviews(), _good_metadata(), hard)
    assert hard == []


def test_empty_reviews_is_hard_failure():
    hard, warns = [], []
    _check_row_counts(pd.DataFrame({"app_id": []}), _good_metadata(), hard, warns)
    assert any("empty" in m for m in hard)


def test_sentiment_totals_warns_when_inconsistent():
    meta = _good_metadata()
    meta.loc[0, "total_reviews"] = 5                 # < positive+negative
    warns = []
    _check_sentiment_totals(meta, warns)
    assert len(warns) == 1 and "550" in warns[0]


def test_future_timestamp_warns():
    rev = _good_reviews()
    # build a future timestamp at the SAME (second) resolution as the column —
    # pandas 3.0 refuses to assign a nanosecond Timestamp into a datetime64[s] column
    future_epoch = int((pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=2)).timestamp())
    rev["timestamp_created"] = pd.to_datetime(
        [future_epoch, 1700000001, 1700000002], unit="s", utc=True)
    warns = []
    _check_future_timestamps(rev, warns)
    assert len(warns) == 1 and "future" in warns[0]


def test_report_str_and_ok():
    clean = ValidationReport([], [])
    assert clean.ok and "all checks passed" in str(clean)
    broken = ValidationReport(["bad thing"], ["odd thing"])
    assert not broken.ok
    s = str(broken)
    assert "FAIL" in s and "warn" in s