from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from crypteo.domain.market import (
    Candle,
    DatasetManifest,
    Timeframe,
    TradingPair,
    Venue,
)

OPEN_TIME = datetime(2026, 1, 1, 10, tzinfo=UTC)
CLOSE_TIME = datetime(2026, 1, 1, 11, tzinfo=UTC)


def candle_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "venue": Venue.COINBASE,
        "pair": TradingPair.BTC_USD,
        "timeframe": Timeframe.ONE_HOUR,
        "open_time": OPEN_TIME,
        "close_time": CLOSE_TIME,
        "available_at": CLOSE_TIME,
        "open": "100.25",
        "high": "105.50",
        "low": "99.75",
        "close": "103.00",
        "volume": "12.500",
    }
    data.update(overrides)
    return data


def manifest_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "schema_version": "1",
        "dataset_version": "coinbase-btc-usd-1h-v1",
        "source": "coinbase_exchange_rest_candles",
        "venue": Venue.COINBASE,
        "pair": TradingPair.BTC_USD,
        "timeframe": Timeframe.ONE_HOUR,
        "start_time": OPEN_TIME,
        "end_time": CLOSE_TIME,
        "created_at": datetime(2026, 1, 2, tzinfo=UTC),
        "row_count": 1,
    }
    data.update(overrides)
    return data


def test_v1_market_identifiers_have_stable_values() -> None:
    assert Venue.COINBASE.value == "COINBASE"
    assert [pair.value for pair in TradingPair] == ["BTC-USD", "ETH-USD", "SOL-USD"]
    assert Timeframe.ONE_HOUR.value == "1h"
    assert Timeframe.ONE_HOUR.duration == timedelta(hours=1)


def test_valid_candle_represents_half_open_interval() -> None:
    candle = Candle.model_validate(candle_data())

    assert candle.open_time == OPEN_TIME
    assert candle.close_time == candle.open_time + candle.timeframe.duration
    assert candle.available_at == candle.close_time
    assert candle.open == Decimal("100.25")
    assert candle.volume == Decimal("12.500")


def test_candle_permits_delayed_availability() -> None:
    delayed = CLOSE_TIME + timedelta(minutes=5)
    assert (
        Candle.model_validate(candle_data(available_at=delayed)).available_at == delayed
    )


def test_candle_json_round_trip_preserves_exact_values_and_utc() -> None:
    candle = Candle.model_validate(candle_data())
    serialized = candle.model_dump(mode="json")

    assert serialized["venue"] == "COINBASE"
    assert serialized["pair"] == "BTC-USD"
    assert serialized["timeframe"] == "1h"
    assert serialized["open"] == "100.25"
    assert serialized["volume"] == "12.500"

    restored = Candle.model_validate_json(candle.model_dump_json())
    assert restored == candle
    assert restored.open_time.utcoffset() == timedelta(0)


def test_candle_is_immutable() -> None:
    candle = Candle.model_validate(candle_data())

    with pytest.raises(ValidationError):
        candle.close = Decimal("104.00")


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
def test_candle_rejects_float_financial_inputs(field: str) -> None:
    with pytest.raises(ValidationError, match="must not be provided as float"):
        Candle.model_validate(candle_data(**{field: 1.25}))


@pytest.mark.parametrize(
    "value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")]
)
@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
def test_candle_rejects_non_finite_financial_values(field: str, value: Decimal) -> None:
    with pytest.raises(ValidationError):
        Candle.model_validate(candle_data(**{field: value}))


@pytest.mark.parametrize("field", ["open_time", "close_time", "available_at"])
def test_candle_rejects_naive_timestamps(field: str) -> None:
    with pytest.raises(ValidationError, match="timezone-aware UTC"):
        Candle.model_validate(candle_data(**{field: OPEN_TIME.replace(tzinfo=None)}))


@pytest.mark.parametrize("field", ["open_time", "close_time", "available_at"])
def test_candle_rejects_non_utc_timestamps(field: str) -> None:
    non_utc = OPEN_TIME.astimezone(timezone(timedelta(hours=8)))
    with pytest.raises(ValidationError, match="timezone-aware UTC"):
        Candle.model_validate(candle_data(**{field: non_utc}))


def test_candle_accepts_distinct_zero_offset_timezone() -> None:
    zero_offset = timezone(timedelta(0), name="ZERO")
    candle = Candle.model_validate(
        candle_data(
            open_time=OPEN_TIME.replace(tzinfo=zero_offset),
            close_time=CLOSE_TIME.replace(tzinfo=zero_offset),
            available_at=CLOSE_TIME.replace(tzinfo=zero_offset),
        )
    )
    assert candle.open_time.tzinfo is zero_offset


def test_candle_rejects_misaligned_open_time() -> None:
    with pytest.raises(ValidationError, match="beginning of an hour"):
        Candle.model_validate(candle_data(open_time=OPEN_TIME + timedelta(minutes=1)))


def test_candle_rejects_incorrect_close_time() -> None:
    with pytest.raises(ValidationError, match="exactly one timeframe"):
        Candle.model_validate(candle_data(close_time=CLOSE_TIME + timedelta(seconds=1)))


def test_candle_rejects_availability_before_close() -> None:
    with pytest.raises(ValidationError, match="at or after close_time"):
        Candle.model_validate(
            candle_data(available_at=CLOSE_TIME - timedelta(microseconds=1))
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("open", "0"),
        ("open", "-1"),
        ("high", "0"),
        ("high", "-1"),
        ("low", "0"),
        ("low", "-1"),
        ("close", "0"),
        ("close", "-1"),
        ("volume", "-0.01"),
    ],
)
def test_candle_rejects_non_positive_prices_and_negative_volume(
    field: str, value: str
) -> None:
    with pytest.raises(ValidationError):
        Candle.model_validate(candle_data(**{field: value}))


@pytest.mark.parametrize(
    "overrides",
    [
        {"high": "99.99"},
        {"close": "106.00"},
        {"low": "100.26"},
        {"close": "99.00"},
        {"high": "98.00", "low": "99.00"},
    ],
)
def test_candle_rejects_impossible_ohlc_combinations(overrides: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        Candle.model_validate(candle_data(**overrides))


def test_candle_accepts_zero_volume() -> None:
    assert Candle.model_validate(candle_data(volume="0")).volume == Decimal(0)


def test_valid_manifest_represents_half_open_coverage() -> None:
    manifest = DatasetManifest.model_validate(manifest_data())

    assert manifest.schema_version == "1"
    assert manifest.start_time == OPEN_TIME
    assert manifest.end_time == CLOSE_TIME
    assert manifest.row_count == 1


def test_manifest_json_round_trip_preserves_enums_and_utc() -> None:
    manifest = DatasetManifest.model_validate(manifest_data())
    serialized = manifest.model_dump(mode="json")

    assert serialized["venue"] == "COINBASE"
    assert serialized["pair"] == "BTC-USD"
    assert serialized["timeframe"] == "1h"

    restored = DatasetManifest.model_validate_json(manifest.model_dump_json())
    assert restored == manifest
    assert restored.created_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize("field", ["start_time", "end_time", "created_at"])
def test_manifest_rejects_naive_and_non_utc_timestamps(field: str) -> None:
    for invalid in (
        OPEN_TIME.replace(tzinfo=None),
        OPEN_TIME.astimezone(timezone(timedelta(hours=-5))),
    ):
        with pytest.raises(ValidationError, match="timezone-aware UTC"):
            DatasetManifest.model_validate(manifest_data(**{field: invalid}))


def test_manifest_rejects_end_before_start() -> None:
    with pytest.raises(ValidationError, match="at or after start_time"):
        DatasetManifest.model_validate(
            manifest_data(end_time=OPEN_TIME - timedelta(hours=1))
        )


def test_manifest_rejects_negative_row_count() -> None:
    with pytest.raises(ValidationError):
        DatasetManifest.model_validate(manifest_data(row_count=-1))


@pytest.mark.parametrize("field", ["schema_version", "dataset_version", "source"])
@pytest.mark.parametrize("value", ["", "   "])
def test_manifest_rejects_empty_identifying_fields(field: str, value: str) -> None:
    with pytest.raises(ValidationError, match="must be non-empty"):
        DatasetManifest.model_validate(manifest_data(**{field: value}))


def test_non_empty_manifest_requires_non_empty_coverage() -> None:
    with pytest.raises(ValidationError, match="non-empty interval"):
        DatasetManifest.model_validate(manifest_data(end_time=OPEN_TIME, row_count=1))


def test_empty_manifest_may_have_empty_coverage() -> None:
    manifest = DatasetManifest.model_validate(
        manifest_data(end_time=OPEN_TIME, row_count=0)
    )
    assert manifest.start_time == manifest.end_time
