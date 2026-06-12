"""validation — the data contract enforced between cleaning and Parquet output."""

from validation.validate import ValidationError, ValidationReport, validate

__all__ = ["validate", "ValidationError", "ValidationReport"]