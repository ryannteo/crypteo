from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from crypteo.coinbase_normalization import normalize_coinbase_candles
from crypteo.domain.market import Timeframe, TradingPair, Venue

TIMESTAMP = 1767225600
OPEN_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def row(**overrides: object) -> list[object]:
    values: dict[str, object] = {
        "time": TIMESTAMP,
        "low": 99.125,
        "high": 105.875,
        "open": 100.25,
        "close": 103.5,
        "volume": 12.125,
    }
    values.update(overrides)
    return [
        values["time"],
        values["low"],
        values["high"],
        values["open"],
        values["close"],
        values["volume"],
    ]


def test_normalizes_coinbase_column_order_and_decimal_values() -> None:
    candle = normalize_coinbase_candles(pair=TradingPair.BTC_USD, rows=[row()])[0]

    assert candle.low == Decimal("99.125")
    assert candle.high == Decimal("105.875")
    assert candle.open == Decimal("100.25")
    assert candle.close == Decimal("103.5")
    assert candle.volume == Decimal("12.125")


def test_preserves_high_precision_decimal_source_values_exactly() -> None:
    precise = Decimal("100.1234567890123456789012345678")

    candle = normalize_coinbase_candles(
        pair=TradingPair.BTC_USD,
        rows=[
            row(
                low=Decimal("99.1"),
                high=Decimal("101.1"),
                open=precise,
                close=precise,
                volume=Decimal("0.000000000000000000123456789"),
            )
        ],
    )[0]

    assert candle.open == precise
    assert candle.close == precise
    assert candle.volume == Decimal("0.000000000000000000123456789")


def test_normalizes_timestamp_and_fixed_candle_metadata() -> None:
    candle = normalize_coinbase_candles(pair=TradingPair.BTC_USD, rows=[row()])[0]

    assert candle.venue is Venue.COINBASE
    assert candle.timeframe is Timeframe.ONE_HOUR
    assert candle.open_time == OPEN_TIME
    assert candle.open_time.tzinfo is UTC
    assert candle.close_time == OPEN_TIME + timedelta(hours=1)
    assert candle.available_at == candle.close_time


@pytest.mark.parametrize("pair", list(TradingPair))
def test_normalizes_each_v1_trading_pair(pair: TradingPair) -> None:
    candle = normalize_coinbase_candles(pair=pair, rows=[row()])[0]

    assert candle.pair is pair


def test_returns_candles_in_ascending_open_time_order() -> None:
    later = row(time=TIMESTAMP + 7200)
    earlier = row(time=TIMESTAMP)
    middle = row(time=TIMESTAMP + 3600)

    candles = normalize_coinbase_candles(
        pair=TradingPair.ETH_USD, rows=[later, earlier, middle]
    )

    assert [candle.open_time for candle in candles] == [
        OPEN_TIME,
        OPEN_TIME + timedelta(hours=1),
        OPEN_TIME + timedelta(hours=2),
    ]


@pytest.mark.parametrize(
    "malformed",
    [
        None,
        {},
        "not a row",
        b"not a row",
        [TIMESTAMP, 1, 2, 3, 4],
        [TIMESTAMP, 1, 2, 3, 4, 5, 6],
    ],
)
def test_rejects_malformed_row_structure(malformed: object) -> None:
    with pytest.raises(ValueError):
        normalize_coinbase_candles(pair=TradingPair.BTC_USD, rows=[malformed])


@pytest.mark.parametrize(
    "overrides",
    [
        {"time": 1767225600.0},
        {"time": "1767225600"},
        {"low": None},
        {"high": "105"},
        {"open": object()},
        {"close": True},
        {"volume": "12"},
    ],
)
def test_rejects_invalid_source_value_types(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        normalize_coinbase_candles(pair=TradingPair.BTC_USD, rows=[row(**overrides)])


@pytest.mark.parametrize(
    "overrides",
    [
        {"high": 99},
        {"low": 104},
        {"volume": -1},
    ],
)
def test_existing_candle_contract_rejects_invalid_market_values(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        normalize_coinbase_candles(pair=TradingPair.SOL_USD, rows=[row(**overrides)])


def test_exact_duplicate_rows_collapse_to_one_candle() -> None:
    duplicate = row()

    candles = normalize_coinbase_candles(
        pair=TradingPair.BTC_USD, rows=[duplicate, list(duplicate)]
    )

    assert len(candles) == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_duplicate_rows_fail_in_any_source_order(reverse: bool) -> None:
    first = row()
    conflicting = row(close=104)
    rows = [first, conflicting]
    if reverse:
        rows.reverse()

    with pytest.raises(ValueError, match="conflicting"):
        normalize_coinbase_candles(pair=TradingPair.BTC_USD, rows=rows)


def test_duplicate_handling_is_order_independent_and_output_identities_are_unique() -> (
    None
):
    first = row()
    duplicate = list(first)
    later = row(time=TIMESTAMP + 3600)

    forward = normalize_coinbase_candles(
        pair=TradingPair.BTC_USD, rows=[first, duplicate, later]
    )
    reverse = normalize_coinbase_candles(
        pair=TradingPair.BTC_USD, rows=[later, duplicate, first]
    )
    identities = [
        (candle.venue, candle.pair, candle.timeframe, candle.open_time)
        for candle in forward
    ]

    assert forward == reverse
    assert len(identities) == len(set(identities))


def test_empty_rows_produce_empty_candles() -> None:
    assert normalize_coinbase_candles(pair=TradingPair.BTC_USD, rows=[]) == ()
