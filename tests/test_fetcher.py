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