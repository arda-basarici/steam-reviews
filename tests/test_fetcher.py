"""Tests for pipeline.fetcher.

The network is never touched: we patch `requests.get` so every test drives the
retry/backoff logic against fake responses. `time.sleep` is patched to a no-op
so the suite runs instantly instead of waiting out real delays/backoff.
"""

from unittest.mock import patch, MagicMock

import pytest

from pipeline import fetcher
from pipeline.fetcher import _get_json, SteamAPIError


def _fake_response(status_code: int, json_body=None, bad_json: bool = False):
    """Build a stand-in for a requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    if bad_json:
        resp.json.side_effect = ValueError("no json")
    else:
        resp.json.return_value = json_body
    return resp


# Silence the politeness delay + backoff for the whole module.
@pytest.fixture(autouse=True)
def _no_sleep():
    with patch.object(fetcher.time, "sleep"):
        yield


def test_returns_parsed_json_on_200():
    payload = {"success": 1, "reviews": [{"recommendationid": "1"}]}
    with patch.object(fetcher.requests, "get",
                      return_value=_fake_response(200, payload)) as mock_get:
        result = _get_json("http://x", {"a": 1})
    assert result == payload
    assert mock_get.call_count == 1  # success on first try, no retries


def test_retries_then_succeeds_on_transient_status():
    responses = [_fake_response(503), _fake_response(200, {"ok": True})]
    with patch.object(fetcher.requests, "get",
                      side_effect=responses) as mock_get:
        result = _get_json("http://x", {})
    assert result == {"ok": True}
    assert mock_get.call_count == 2  # one 503, then a 200


def test_retries_on_network_error_then_succeeds():
    import requests as real_requests
    responses = [real_requests.RequestException("conn reset"),
                 _fake_response(200, {"ok": True})]
    with patch.object(fetcher.requests, "get",
                      side_effect=responses) as mock_get:
        result = _get_json("http://x", {})
    assert result == {"ok": True}
    assert mock_get.call_count == 2


def test_raises_on_permanent_status_without_retry():
    with patch.object(fetcher.requests, "get",
                      return_value=_fake_response(404)) as mock_get:
        with pytest.raises(SteamAPIError):
            _get_json("http://x", {})
    assert mock_get.call_count == 1  # 404 is permanent — no retry


def test_raises_after_exhausting_retries():
    # Always 503: should try (max_retries + 1) times, then give up.
    with patch.object(fetcher.requests, "get",
                      return_value=_fake_response(503)) as mock_get:
        with pytest.raises(SteamAPIError):
            _get_json("http://x", {})
    assert mock_get.call_count == fetcher.settings.max_retries + 1


def test_raises_on_invalid_json():
    with patch.object(fetcher.requests, "get",
                      return_value=_fake_response(200, bad_json=True)) as mock_get:
        with pytest.raises(SteamAPIError):
            _get_json("http://x", {})
    assert mock_get.call_count == 1  # bad JSON is permanent


# ---------------------------------------------------------------------------
# Piece 2 — identity guard
# ---------------------------------------------------------------------------

from pipeline.fetcher import (
    _normalize_name,
    _name_similarity,
    fetch_app_details,
    check_identity,
    GUARD_OK,
    GUARD_MISMATCH,
    GUARD_NO_DATA,
)


def test_normalize_strips_symbols_case_and_punctuation():
    assert _normalize_name("DARK SOULS\u2122 III") == "dark souls iii"
    assert _normalize_name("Baldur\u2019s Gate 3") == "baldur s gate 3"
    assert _normalize_name("ELDEN RING") == "elden ring"


def test_similarity_passes_legit_variants_fails_wrong_game():
    assert _name_similarity("Elden Ring", "ELDEN RING") == 1.0
    assert _name_similarity(
        "Mass Effect Legendary Edition", "Mass Effect\u2122 Legendary Edition"
    ) >= 0.85
    assert _name_similarity("Goat Simulator", "Pavlov VR") < 0.85


def test_fetch_app_details_returns_data_on_success():
    payload = {"265930": {"success": True, "data": {"name": "Goat Simulator"}}}
    with patch.object(fetcher, "_get_json", return_value=payload):
        data = fetch_app_details(265930)
    assert data == {"name": "Goat Simulator"}


def test_fetch_app_details_returns_none_when_unsuccessful():
    payload = {"1372880": {"success": False}}  # delisted / invalid id
    with patch.object(fetcher, "_get_json", return_value=payload):
        assert fetch_app_details(1372880) is None


def test_fetch_app_details_returns_none_when_id_absent():
    with patch.object(fetcher, "_get_json", return_value={}):
        assert fetch_app_details(999999) is None


def test_check_identity_ok_on_name_match():
    data = {"name": "Goat Simulator", "is_free": False}
    with patch.object(fetcher, "fetch_app_details", return_value=data):
        result = check_identity(265930, "Goat Simulator")
    assert result.status == GUARD_OK
    assert result.data == data           # metadata carried through on success
    assert result.ratio == 1.0


def test_check_identity_mismatch_on_wrong_game():
    data = {"name": "Pavlov VR"}          # the classic wrong-id case
    with patch.object(fetcher, "fetch_app_details", return_value=data):
        result = check_identity(555160, "Goat Simulator")
    assert result.status == GUARD_MISMATCH
    assert result.data is None            # wrong game's metadata is discarded
    assert result.actual_name == "Pavlov VR"


def test_check_identity_no_data_when_details_missing():
    with patch.object(fetcher, "fetch_app_details", return_value=None):
        result = check_identity(1372880, "The Day Before")
    assert result.status == GUARD_NO_DATA
    assert result.data is None