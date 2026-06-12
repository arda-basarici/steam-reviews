"""validate.py — the data contract, enforced before promoting cleaned data to Parquet.

Two severities:
  HARD -> raise ValidationError, write nothing. Structural/key violations that would
          silently corrupt analysis: a missing column, a duplicate recommendationid
          (dedup failed), broken referential integrity, an empty table.
  SOFT -> log a warning, continue. Values that are unusual but not impossible: a
          score just outside [0, 1], a timestamp that looks slightly future-dated.

All violations are collected first, then we decide — one report, never a
fix-one-rerun-find-the-next loop. pandera is imported lazily (only when actually
validating) so importing this module doesn't require it.
"""

import pandas as pd

from pipeline.config import settings


class ValidationReport:
    """The outcome of a validation run: every hard failure and every warning."""

    def __init__(self, hard_failures, warnings):
        self.hard_failures = list(hard_failures)
        self.warnings = list(warnings)

    @property
    def ok(self) -> bool:
        return not self.hard_failures

    def __str__(self) -> str:
        lines = []
        if self.hard_failures:
            lines.append(f"{len(self.hard_failures)} hard failure(s):")
            lines += [f"  [FAIL] {m}" for m in self.hard_failures]
        if self.warnings:
            lines.append(f"{len(self.warnings)} warning(s):")
            lines += [f"  [warn] {m}" for m in self.warnings]
        return "\n".join(lines) if lines else "all checks passed"


class ValidationError(Exception):
    """Raised on HARD contract violations. Carries the full report for the caller."""

    def __init__(self, report: "ValidationReport"):
        self.report = report
        super().__init__("data contract violated:\n" + str(report))


# --- pandera schema runner ---------------------------------------------------
def _run_schema(schema, df, label, sink) -> None:
    """Validate `df` against a pandera schema in lazy mode (collect ALL failures),
    appending one readable message per failure to `sink`."""
    import pandera.errors as pa_errors        # lazy: pandera only needed here
    try:
        schema.validate(df, lazy=True)
    except pa_errors.SchemaErrors as exc:
        for _, row in exc.failure_cases.iterrows():
            sink.append(f"{label}.{row.get('column')}: {row.get('check')} "
                        f"(e.g. {row.get('failure_case')!r})")


# --- cross-table / cross-column checks (plain Python) ------------------------
def _check_referential_integrity(reviews, metadata, hard) -> None:
    """Every review's app_id must exist in the metadata table."""
    review_ids = set(reviews["app_id"].dropna().unique())
    meta_ids = set(metadata["app_id"].dropna().unique())
    orphans = review_ids - meta_ids
    if orphans:
        hard.append(f"reviews reference app_ids absent from metadata: {sorted(orphans)}")


def _check_row_counts(reviews, metadata, hard, warns) -> None:
    """Tables must be non-empty (hard); review count over the expected ceiling
    is suspicious but allowed (soft)."""
    if len(reviews) == 0:
        hard.append("reviews table is empty")
    if len(metadata) == 0:
        hard.append("metadata table is empty")
    if len(metadata):
        n_games = metadata["app_id"].nunique()
        ceiling = n_games * settings.effective_reviews_cap
        if len(reviews) > ceiling:
            warns.append(f"reviews row count {len(reviews)} exceeds expected ceiling "
                         f"{ceiling} ({n_games} games x {settings.effective_reviews_cap}/game)")


def _check_sentiment_totals(metadata, warns) -> None:
    """total_positive + total_negative should not exceed total_reviews (soft:
    Steam's own totals occasionally disagree, so warn rather than block)."""
    needed = {"total_positive", "total_negative", "total_reviews"}
    if needed <= set(metadata.columns):
        pos = metadata["total_positive"].fillna(0)
        neg = metadata["total_negative"].fillna(0)
        tot = metadata["total_reviews"].fillna(0)
        for _, r in metadata[pos + neg > tot].iterrows():
            warns.append(f"metadata app_id {r['app_id']}: "
                         f"total_positive + total_negative > total_reviews")


def _check_future_timestamps(reviews, warns) -> None:
    """Reviews dated in the future suggest clock skew or a parse bug (soft)."""
    if "timestamp_created" in reviews.columns:
        ts = reviews["timestamp_created"]
        future = ts[ts > pd.Timestamp.now(tz="UTC")]
        if len(future):
            warns.append(f"{len(future)} review(s) have timestamp_created in the future")


# --- entry point -------------------------------------------------------------
def validate(reviews: pd.DataFrame, metadata: pd.DataFrame, *, verbose: bool = True) -> ValidationReport:
    """Validate both cleaned tables against the contract.

    Raises ValidationError on any hard failure (so the caller writes no Parquet);
    logs warnings and returns the report when there are no hard failures.
    """
    from validation import schemas          # lazy: only need pandera when validating

    hard: list[str] = []
    warns: list[str] = []

    _run_schema(schemas.REVIEWS_STRUCTURE, reviews, "reviews", hard)
    _run_schema(schemas.METADATA_STRUCTURE, metadata, "metadata", hard)
    _run_schema(schemas.REVIEWS_RANGES, reviews, "reviews", warns)
    _run_schema(schemas.METADATA_RANGES, metadata, "metadata", warns)

    _check_referential_integrity(reviews, metadata, hard)
    _check_row_counts(reviews, metadata, hard, warns)
    _check_sentiment_totals(metadata, warns)
    _check_future_timestamps(reviews, warns)

    report = ValidationReport(hard, warns)
    if verbose:
        for w in report.warnings:
            print(f"[validate][warn] {w}")
    if report.hard_failures:
        raise ValidationError(report)
    return report