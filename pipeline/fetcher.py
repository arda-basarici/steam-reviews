"""fetcher.py — Steam API I/O for the Review Intelligence pipeline.

Responsibility: talk to Steam, hand back what it returns, change nothing.
All network access in the project funnels through here. Nothing in this module
cleans, dedupes, or interprets review *content* — that is the cleaner's job.

Built bottom-up. This first section is the foundation every other piece calls:
a single GET with retry + exponential backoff, and the one custom exception that
distinguishes "Steam is unreachable, stop the run" from "this game simply has no
data, handle it and move on".
"""

import time

import requests

from pipeline.config import settings


class SteamAPIError(RuntimeError):
    """The Steam API failed in a way we cannot proceed past.

    Raised when a request keeps failing transiently until retries are exhausted,
    or returns a permanent error (bad status code, unparseable body). This means
    "stop the run" — it is deliberately DISTINCT from the expected, non-error
    condition "this game has no reviews", which callers detect by branching on a
    None / empty result rather than by catching an exception. Exceptions are for
    the exceptional; expected outcomes are returned, not thrown.
    """


# HTTP statuses that mean "Steam is busy/hiccuping" — worth retrying.
# Everything else in the 4xx range is permanent and must not be retried.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _get_json(url: str, params: dict) -> dict:
    """Perform ONE GET against Steam, with politeness delay, retries, backoff.

    This is the single place retry logic lives, so no other function in the
    pipeline ever re-implements it.

    Returns:
        The parsed JSON body as a dict, on HTTP 200.

    Raises:
        SteamAPIError: if the request fails transiently until retries are
            exhausted, hits a permanent (non-retryable) status, or returns a
            body that is not valid JSON.

    Note: this low-level helper does not interpret Steam's `success` field —
    that meaning differs per endpoint (appreviews uses success==2 for "no
    reviews"; appdetails uses success==false per app id), so each caller applies
    that one-line check itself. What the callers see overall is the agreed
    contract: unreachable Steam -> raises here; "no data" -> caller returns None.
    """
    headers = {"User-Agent": settings.user_agent}
    last_error: Exception | None = None

    # range(max_retries + 1) == one initial attempt plus `max_retries` retries.
    for attempt in range(settings.max_retries + 1):
        # Politeness pause before EVERY Steam call (this is why the delay lives
        # here and not in the pagination loop — it spaces out *all* requests,
        # review pages and metadata calls alike).
        time.sleep(settings.request_delay_seconds)

        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=settings.request_timeout,
            )
        except requests.RequestException as exc:
            # Network-level failure (timeout, DNS, connection reset). Transient.
            last_error = exc
        else:
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    # 200 but the body is not JSON — not something a retry fixes.
                    raise SteamAPIError(f"Invalid JSON from {url}") from exc

            if response.status_code not in _RETRYABLE_STATUS:
                # Permanent error (e.g. 404, 403, 400). Retrying is pointless.
                raise SteamAPIError(
                    f"HTTP {response.status_code} from {url}"
                )

            # Retryable status (429 / 5xx): record and fall through to backoff.
            last_error = SteamAPIError(
                f"HTTP {response.status_code} from {url}"
            )

        # Reached only on a transient failure. Back off, unless that was the
        # final attempt, in which case we exit the loop and give up below.
        if attempt < settings.max_retries:
            backoff_wait = settings.backoff_factor ** attempt
            time.sleep(backoff_wait)

    raise SteamAPIError(
        f"Giving up on {url} after {settings.max_retries + 1} attempts"
    ) from last_error