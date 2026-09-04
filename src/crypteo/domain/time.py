"""Shared timestamp contracts for Crypteo domain models."""

from datetime import datetime, timedelta
from typing import Annotated

from pydantic import AfterValidator, Field


def _require_utc(value: datetime) -> datetime:
    """Require an aware datetime whose UTC offset is exactly zero."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware UTC")
    return value


UtcDatetime = Annotated[
    datetime,
    Field(strict=True),
    AfterValidator(_require_utc),
]
"""A strict Python datetime with an exactly zero UTC offset.

Python validation does not coerce strings or epoch numbers. Pydantic's JSON
datetime parsing remains available so serialized RFC 3339 UTC values round-trip.
"""
