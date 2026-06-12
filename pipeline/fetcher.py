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


def _is_edition_of(expected: str, actual: str) -> bool:
    """True if `actual` is `expected` plus a trailing edition/subtitle.

    Steam often stores a longer name than ours — "Disco Elysium" becomes
    "Disco Elysium - The Final Cut", "Shadow of the Tomb Raider" becomes
    "...: Definitive Edition". In every such case our expected name is a clean
    leading *token* prefix of the store name (store = our name + extra words).

    Guard against false matches: we require expected to be at least two tokens, so
    a short/generic name can't latch onto a different, longer-named game. (A wrong
    game like "Dex" or "Shovel Knight: Treasure Trove" is not a token-prefix of
    our expected name, so it never reaches GUARD_OK this way.)
    """
    e = _normalize_name(expected).split()
    a = _normalize_name(actual).split()
    return len(e) >= 2 and len(a) > len(e) and a[: len(e)] == e


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
    if ratio >= settings.identity_match_threshold or _is_edition_of(expected_name, actual_name):
        return GuardResult(GUARD_OK, expected_name, actual_name, ratio, data)
    return GuardResult(GUARD_MISMATCH, expected_name, actual_name, ratio, None)


# ============================================================================
# Piece 3 — the pagination loop
#
# Walks a game's reviews via Steam's cursor pagination and *yields* them batch
# by batch. It deliberately does NOT write to disk: producing the stream and
# persisting it are separate jobs (persistence + the manifest are piece 4). This
# generator's only concerns are talking to Steam, stopping correctly, and
# respecting the per-game cap.
# ============================================================================


@dataclass(frozen=True)
class ReviewBatch:
    """One page of results from the reviews walk.

    reviews        : the review dicts in this batch (possibly empty)
    next_cursor    : the cursor that fetches the NEXT batch — the writer records
                     this in the manifest *after* persisting `reviews`, which is
                     what makes at-least-once resume work
    query_summary  : game-level totals (total_positive / total_negative /
                     total_reviews across all of Steam). Present only on the
                     first batch, where Steam returns it; None thereafter.
    """

    reviews: list
    next_cursor: str | None
    query_summary: dict | None


def _review_params(cursor: str) -> dict:
    """Build the appreviews query string for a given cursor (from config)."""
    return {
        "json": 1,
        "filter": settings.review_filter,
        "language": settings.review_language,
        "purchase_type": settings.purchase_type,
        "review_type": settings.review_type,
        "num_per_page": settings.num_per_page,
        "cursor": cursor,  # requests URL-encodes this for us
    }


def iter_review_batches(app_id: int, start_cursor: str = "*"):
    """Yield ReviewBatch objects for a game until the walk is complete.

    Start at `start_cursor` ("*" for a fresh game, or a saved cursor to resume).
    Stops on ANY of:
      * empty batch     — Steam returned no reviews (incl. the no-data case)
      * short batch     — fewer than num_per_page, i.e. the last page
      * repeated cursor — Steam handed back a cursor already used (its loop bug)
      * cap reached     — effective_reviews_cap reviews have been yielded

    Raises SteamAPIError only if Steam is unreachable (propagated from _get_json).
    """
    url = f"{settings.reviews_endpoint}/{app_id}"
    cap = settings.effective_reviews_cap
    cursor = start_cursor
    seen_cursors: set[str] = set()
    total_yielded = 0
    first = True

    while total_yielded < cap:
        # Loop-bug guard: if we've already requested with this cursor, Steam has
        # cycled us back — stop rather than re-fetch the same pages forever.
        if cursor in seen_cursors:
            return
        seen_cursors.add(cursor)

        payload = _get_json(url, _review_params(cursor))
        reviews = payload.get("reviews") or []
        next_cursor = payload.get("cursor")
        summary = payload.get("query_summary") if first else None

        # Never exceed the cap: trim a batch that would overshoot.
        remaining = cap - total_yielded
        if len(reviews) > remaining:
            reviews = reviews[:remaining]

        # Always emit the first response (it carries query_summary, even when a
        # game has zero reviews — e.g. The Day Before). Afterwards, only emit
        # batches that actually contain reviews.
        if reviews or first:
            yield ReviewBatch(reviews, next_cursor, summary)
            total_yielded += len(reviews)

        first = False

        # Stop conditions, checked after emitting.
        if not reviews:
            return                                  # empty → end of data
        if len(reviews) < settings.num_per_page:
            return                                  # short page → last page
        if not next_cursor:
            return                                  # no cursor → cannot continue
        cursor = next_cursor