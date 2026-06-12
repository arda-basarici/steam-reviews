"""Central configuration for the Steam Review Intelligence pipeline.

Single source of truth: every tunable value (endpoints, query params, rate-limit
politeness, file paths) lives here, so nothing is hardcoded inside the fetcher or
cleaner. Frozen so it cannot be mutated at runtime — config is read, never written.

The values here are deliberate answers to documented Steam API behaviour; see the
inline notes for the "why" behind each one.
"""

from dataclasses import dataclass
from pathlib import Path

# Project root = the steam-reviews/ folder.
# This file lives at steam-reviews/pipeline/config.py, so two parents up is the root.
# Anchoring every path to this constant (instead of to the current working directory)
# means the pipeline runs identically no matter which folder you launch it from.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    """Immutable container of pipeline settings. Instantiated once as `settings`."""

    # ----------------------------------------------------------------- paths
    project_root: Path = PROJECT_ROOT
    game_list_path: Path = PROJECT_ROOT / "data" / "game_list.json"

    raw_reviews_dir: Path = PROJECT_ROOT / "data" / "raw" / "reviews"
    raw_metadata_dir: Path = PROJECT_ROOT / "data" / "raw" / "metadata"
    processed_dir: Path = PROJECT_ROOT / "data" / "processed"

    reviews_parquet: Path = PROJECT_ROOT / "data" / "processed" / "reviews.parquet"
    metadata_parquet: Path = PROJECT_ROOT / "data" / "processed" / "metadata.parquet"

    # The fetch manifest records, per game: reviews pulled, last cursor, timestamps,
    # identity-guard result, and any warnings. It is what makes a fetch resumable
    # after a crash and auditable afterwards.
    fetch_manifest_path: Path = PROJECT_ROOT / "data" / "raw" / "fetch_manifest.json"

    # ------------------------------------------------------------- endpoints
    # Reviews: full URL is f"{reviews_endpoint}/{app_id}". Keyless, public.
    reviews_endpoint: str = "https://store.steampowered.com/appreviews"
    # Storefront details: used for game metadata AND the identity guard
    # (does this app_id actually resolve to the game we expect?).
    appdetails_endpoint: str = "https://store.steampowered.com/api/appdetails"
    # Storefront call returns prices in this currency and names in this language,
    # so they line up with the USD / English values in game_list.json.
    appdetails_country: str = "us"
    appdetails_language: str = "english"

    # --------------------------------------------------- review query params
    # filter=recent walks the full review set in creation-date order. The
    # helpfulness sort (filter=all) is what triggers the documented cursor-loop
    # bug, and recency also gives us clean temporal ordering for free — useful
    # for the review-bombing time series.
    review_filter: str = "recent"

    # language=all is mandatory: some games (e.g. Overwatch 2) are majority
    # non-English, and the default would silently drop those reviews.
    review_language: str = "all"

    # purchase_type defaults to "steam" on the API, which silently EXCLUDES
    # reviews from key-activated owners — a real sampling bias. "all" keeps them.
    purchase_type: str = "all"

    # all reviews, positive and negative.
    review_type: str = "all"

    # Max is 100, but batch size 100 has a documented truncation bug for some
    # apps (returns a short batch then zero, ending the walk early and dropping
    # most reviews). 80 is the community-verified reliable value. Completeness
    # matters more here than the ~20% extra requests.
    num_per_page: int = 80

    # ------------------------------------------------------- identity guard
    # How similar the store's name must be to the expected game_list name
    # (after normalization: lowercased, ™/®/© and punctuation stripped) to
    # accept an app_id. 0.85 tolerates formatting drift while still rejecting a
    # wholly different game. Lower it if a legitimate game is being false-skipped
    # (every decision is recorded in the fetch manifest, so misses are visible).
    identity_match_threshold: float = 0.85

    # ------------------------------------------------------------------ caps
    # Per-game ceiling for a FULL run. Keeps the dataset ~500k reviews total
    # across 50 games so iteration stays fast. Smaller games return what exists.
    reviews_per_game_cap: int = 10_000

    # --- sample mode ---------------------------------------------------------
    # While BUILDING the pipeline we run shallow: all 50 games, ~500 reviews each
    # (~9 min total), which exercises every edge case (empty/delisted games,
    # non-English corpora) without the full ~1.5 h fetch. Flip to False for the
    # one real run at the end. Default True so a full fetch is never accidental.
    sample_mode: bool = True
    sample_reviews_per_game: int = 500

    # ------------------------------------------------ politeness & reliability
    # Steam's rate limits are undocumented; the accepted approach is to stay
    # polite and back off on errors. A fixed delay between requests is the floor.
    request_delay_seconds: float = 1.5

    # On a failed/429 request: retry up to this many times, waiting
    # backoff_factor ** attempt seconds between tries (exponential backoff).
    max_retries: int = 5
    backoff_factor: float = 2.0

    # Seconds before a single HTTP request is abandoned as hung.
    request_timeout: int = 30

    # A descriptive, honest User-Agent. The default requests/urllib agent gets
    # throttled; this identifies the project and a contact URL. If Steam ever
    # rejects it, fall back to a plain browser string.
    user_agent: str = (
        "ai-journey-steam-reviews/1.0 "
        "(+https://github.com/arda-basarici/ai-journey)"
    )

    # ------------------------------------------------------- derived (read-only)
    @property
    def effective_reviews_cap(self) -> int:
        """The per-game cap actually in force, picked by sample_mode.

        Derived config, not behaviour: it only *selects* between two settings,
        so the fetcher reads one value and never repeats the if/else itself.
        """
        return self.sample_reviews_per_game if self.sample_mode else self.reviews_per_game_cap


# Module-level singleton. Import this everywhere:  from pipeline.config import settings
settings = Settings()