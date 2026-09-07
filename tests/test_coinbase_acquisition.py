from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha256
from typing import Any

import pytest
from pydantic import ValidationError

from crypteo.coinbase_acquisition import CoinbaseAcquisitionRecord

START = datetime(2026, 1, 1, tzinfo=UTC)
ID = "sha256:" + "a" * 64


def record(**overrides: Any) -> CoinbaseAcquisitionRecord:
    values = {
        "schema_version": "1",
        "dataset_version": ID,
        "created_at": START + timedelta(days=1),
        "requested_start": START,
        "requested_end": START + timedelta(hours=3),
        "capture_ids": (ID,),
        "missing_open_times": (START,),
    }
    values.update(overrides)
    return CoinbaseAcquisitionRecord.model_validate(values)


@pytest.mark.parametrize("field", ["created_at", "requested_start", "requested_end"])
@pytest.mark.parametrize(
    "bad",
    [
        datetime(2026, 1, 1),
        START.astimezone(timezone(timedelta(hours=8))),
        "2026-01-01T00:00:00Z",
        1767225600,
    ],
)
def test_requires_strict_utc(field: str, bad: object) -> None:
    with pytest.raises(ValidationError):
        record(**{field: bad})


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": "2"},
        {"dataset_version": "../bad"},
        {"capture_ids": ("bad",)},
        {"capture_ids": (ID, ID)},
        {"capture_ids": ("sha256:" + "b" * 64, ID)},
        {"requested_start": START + timedelta(hours=4)},
        {"requested_end": START + timedelta(minutes=30)},
        {"missing_open_times": (START, START)},
        {"missing_open_times": (START + timedelta(hours=1), START)},
        {"missing_open_times": (START - timedelta(hours=1),)},
        {"missing_open_times": (START + timedelta(hours=3),)},
        {"missing_open_times": (START + timedelta(minutes=1),)},
        {"missing_open_times": (datetime(2026, 1, 1),)},
        {"missing_open_times": (START.astimezone(timezone(timedelta(hours=8))),)},
        {"unexpected": True},
    ],
)
def test_invalid_contract(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        record(**overrides)


def test_immutable_roundtrip_and_content_identity() -> None:
    captures = [ID]
    missing = [START]
    value = record(capture_ids=captures, missing_open_times=missing)
    captures.clear()
    missing.clear()
    assert value.capture_ids == (ID,)
    assert value.missing_open_times == (START,)
    with pytest.raises(ValidationError):
        value.created_at = START
    encoded = value.canonical_bytes()
    restored = CoinbaseAcquisitionRecord.model_validate_json(encoded)
    assert restored == value
    assert (
        restored.acquisition_id
        == value.acquisition_id
        == "sha256:" + sha256(encoded).hexdigest()
    )
    assert (
        record(created_at=START + timedelta(days=2)).acquisition_id
        != value.acquisition_id
    )
