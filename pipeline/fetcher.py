"""fetcher.py — Steam API I/O for the Review Intelligence pipeline.

Responsibility: talk to Steam, hand back what it returns, change nothing.
All network access in the project funnels through here. Nothing in this module
cleans, dedupes, or interprets review *content* — that is the cleaner's job.

Built bottom-up. This first section is the foundation every other piece calls:
a single GET with retry + exponential backoff, and the one custom exception that
distinguishes "Steam is unreachable, stop the run" from "this game simply has no
data, handle it and move on".
"""

import difflib
import re
import time
import unicodedata
from dataclasses import dataclass

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


# ============================================================================
# Piece 2 — the identity guard
#
# Before fetching a single review we confirm the app_id actually resolves to the
# game we expect. This is the permanent fix for the wrong-id class of bug (e.g.
# app_id 555160 is Pavlov VR, not Goat Simulator): even if a bad id slips into
# game_list.json later, the fetcher refuses to pull the wrong game's reviews.
# ============================================================================

# Possible outcomes of the guard. Strings (not magic literals scattered around)
# so the manifest can record exactly why a game was accepted or skipped.
GUARD_OK = "ok"            # name matches — proceed, metadata captured
GUARD_MISMATCH = "mismatch"  # store name differs too much — skip, log
GUARD_NO_DATA = "no_data"    # storefront has no data for this id — skip, log


@dataclass(frozen=True)
class GuardResult:
    """Outcome of checking one app_id's identity. Immutable record for the log."""

    status: str               # one of GUARD_OK / GUARD_MISMATCH / GUARD_NO_DATA
    expected_name: str
    actual_name: str | None   # store name, when we got one
    ratio: float | None       # similarity score, when we compared
    data: dict | None         # appdetails 'data' block, only when status == ok


def _normalize_name(name: str) -> str:
    """Reduce a game name to a comparable core.

    Lowercases, strips trademark/registered/copyright symbols, removes accents
    and punctuation, and collapses whitespace — so 'DARK SOULS™ III' and
    'Dark Souls III', or curly vs straight apostrophes in "Baldur's Gate 3",
    compare as equal.
    """
    text = name.casefold()
    for symbol in ("™", "®", "©", "℠"):
        text = text.replace(symbol, "")
    # Decompose accented characters, then drop the combining marks.
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # Any run of non-alphanumeric characters becomes a single space.
    text = re.sub(r"[^0-9a-z]+", " ", text)
    return text.strip()


def _name_similarity(a: str, b: str) -> float:
    """Similarity in [0, 1] between two names, after normalization."""
    return difflib.SequenceMatcher(None, _normalize_name(a), _normalize_name(b)).ratio()


def fetch_app_details(app_id: int) -> dict | None:
    """Fetch the storefront 'data' block for an app_id.

    Returns the appdetails `data` dict on success, or None when Steam reports no
    data for this id (`success: false` — e.g. a delisted or invalid app). Raises
    SteamAPIError only when Steam itself is unreachable (propagated from
    _get_json). This is the appdetails-side application of the agreed contract:
    unreachable -> raise; no data -> None.
    """
    params = {
        "appids": app_id,
        "cc": settings.appdetails_country,
        "l": settings.appdetails_language,
    }
    payload = _get_json(settings.appdetails_endpoint, params)
    # Response is keyed by the app_id as a string.
    entry = payload.get(str(app_id))
    if not entry or not entry.get("success") or "data" not in entry:
        return None
    return entry["data"]


def check_identity(app_id: int, expected_name: str) -> GuardResult:
    """Confirm an app_id resolves to the expected game.

    Never raises for a per-game problem: a missing storefront entry or a name
    mismatch is returned as a GuardResult for the caller to log and skip. Only a
    genuinely unreachable Steam (from _get_json, via fetch_app_details) raises.
    """
    data = fetch_app_details(app_id)
    if data is None:
        return GuardResult(GUARD_NO_DATA, expected_name, None, None, None)

    actual_name = data.get("name", "")
    ratio = _name_similarity(expected_name, actual_name)
    if ratio >= settings.identity_match_threshold:
        return GuardResult(GUARD_OK, expected_name, actual_name, ratio, data)
    return GuardResult(GUARD_MISMATCH, expected_name, actual_name, ratio, None)