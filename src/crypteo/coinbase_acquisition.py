"""Immutable Coinbase acquisition completeness and provenance."""

import json
from datetime import datetime
from hashlib import sha256
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from crypteo.domain.time import UtcDatetime

Sha256Id = Annotated[str, Field(strict=True, pattern=r"^sha256:[0-9a-f]{64}$")]


class CoinbaseAcquisitionRecord(BaseModel):
    """Requested coverage and exact captures associated with a canonical dataset."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"]
    dataset_version: Sha256Id
    created_at: UtcDatetime
    requested_start: UtcDatetime
    requested_end: UtcDatetime
    capture_ids: tuple[Sha256Id, ...]
    missing_open_times: tuple[UtcDatetime, ...]

    @field_validator("requested_start", "requested_end")
    @classmethod
    def validate_alignment(cls, value: datetime) -> datetime:
        if value.minute or value.second or value.microsecond:
            raise ValueError("requested boundaries must be hourly aligned")
        return value

    @model_validator(mode="after")
    def validate_completeness(self) -> Self:
        if self.requested_start > self.requested_end:
            raise ValueError("requested_start must be at or before requested_end")
        if self.capture_ids != tuple(sorted(set(self.capture_ids))):
            raise ValueError("capture_ids must be unique and ascending")
        if self.missing_open_times != tuple(sorted(set(self.missing_open_times))):
            raise ValueError("missing_open_times must be unique and ascending")
        for value in self.missing_open_times:
            if value.minute or value.second or value.microsecond:
                raise ValueError("missing_open_times must be hourly aligned")
            if not self.requested_start <= value < self.requested_end:
                raise ValueError("missing_open_times must be within requested interval")
        return self

    def canonical_bytes(self) -> bytes:
        """Stable UTF-8 JSON encoding used only for acquisition artifacts."""
        envelope = self.model_dump(mode="json")
        for field in ("created_at", "requested_start", "requested_end"):
            envelope[field] = _utc_text(getattr(self, field))
        envelope["missing_open_times"] = [
            _utc_text(value) for value in self.missing_open_times
        ]
        return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()

    @property
    def acquisition_id(self) -> str:
        return f"sha256:{sha256(self.canonical_bytes()).hexdigest()}"


def _utc_text(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
