from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import BaseModel, ValidationError

from crypteo.domain.time import UtcDatetime


class TimestampRecord(BaseModel):
    timestamp: UtcDatetime


def test_utc_datetime_accepts_any_exact_zero_offset_timezone() -> None:
    zero_offset = timezone(timedelta(0), name="ZERO")
    value = datetime(2026, 1, 1, tzinfo=zero_offset)

    assert TimestampRecord(timestamp=value).timestamp.tzinfo is zero_offset


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 1, 1),
        datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8))),
    ],
)
def test_utc_datetime_rejects_naive_and_nonzero_offsets(value: datetime) -> None:
    with pytest.raises(ValidationError, match="timezone-aware UTC"):
        TimestampRecord(timestamp=value)


@pytest.mark.parametrize("value", [1767225600, "2026-01-01T00:00:00Z"])
def test_utc_datetime_rejects_coercive_python_inputs(value: object) -> None:
    with pytest.raises(ValidationError):
        TimestampRecord.model_validate({"timestamp": value})


def test_utc_datetime_round_trips_rfc3339_json() -> None:
    record = TimestampRecord(timestamp=datetime(2026, 1, 1, tzinfo=UTC))

    assert TimestampRecord.model_validate_json(record.model_dump_json()) == record
