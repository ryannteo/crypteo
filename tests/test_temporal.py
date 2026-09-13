import inspect
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from crypteo.domain.market import Candle, Timeframe, TradingPair, Venue
from crypteo.temporal import PointInTimeDataGateway, SimulationClock

BASE_TIME = datetime(2026, 1, 1, 10, tzinfo=UTC)


def candle(
    open_time: datetime,
    *,
    pair: TradingPair = TradingPair.BTC_USD,
    available_at: datetime | None = None,
) -> Candle:
    close_time = open_time + timedelta(hours=1)
    return Candle(
        venue=Venue.COINBASE,
        pair=pair,
        timeframe=Timeframe.ONE_HOUR,
        open_time=open_time,
        close_time=close_time,
        available_at=available_at or close_time,
        open=Decimal("100"),
        high=Decimal("105"),
        low=Decimal("99"),
        close=Decimal("103"),
        volume=Decimal("10"),
    )


def read_btc(gateway: PointInTimeDataGateway) -> tuple[Candle, ...]:
    return gateway.read_candles(
        venue=Venue.COINBASE,
        pair=TradingPair.BTC_USD,
        timeframe=Timeframe.ONE_HOUR,
    )


def test_simulation_clock_initializes_with_strict_utc_time() -> None:
    clock = SimulationClock(BASE_TIME)

    assert clock.now == BASE_TIME


@pytest.mark.parametrize(
    "invalid",
    [
        datetime(2026, 1, 1, 10),
        BASE_TIME.astimezone(timezone(timedelta(hours=8))),
        "2026-01-01T10:00:00Z",
        1767261600,
    ],
)
def test_simulation_clock_reuses_strict_utc_boundary(invalid: Any) -> None:
    with pytest.raises(ValidationError):
        SimulationClock(invalid)


def test_simulation_clock_advances_forward_and_allows_same_time() -> None:
    clock = SimulationClock(BASE_TIME)

    clock.advance_to(BASE_TIME)
    clock.advance_to(BASE_TIME + timedelta(hours=1))

    assert clock.now == BASE_TIME + timedelta(hours=1)


def test_simulation_clock_rejects_backward_movement() -> None:
    clock = SimulationClock(BASE_TIME)

    with pytest.raises(ValueError, match="cannot move backward"):
        clock.advance_to(BASE_TIME - timedelta(microseconds=1))

    assert clock.now == BASE_TIME


@pytest.mark.parametrize(
    "invalid",
    [
        datetime(2026, 1, 1, 11),
        (BASE_TIME + timedelta(hours=1)).astimezone(timezone(timedelta(hours=8))),
        "2026-01-01T11:00:00Z",
        1767265200,
    ],
)
def test_simulation_clock_advance_reuses_strict_utc_boundary(invalid: Any) -> None:
    clock = SimulationClock(BASE_TIME)

    with pytest.raises(ValidationError):
        clock.advance_to(invalid)

    assert clock.now == BASE_TIME


def test_gateway_exposes_before_and_exactly_at_clock_but_not_future() -> None:
    clock_time = BASE_TIME + timedelta(hours=3)
    visible_before = candle(BASE_TIME)
    visible_exactly = candle(BASE_TIME + timedelta(hours=1), available_at=clock_time)
    planted_future = candle(
        BASE_TIME + timedelta(hours=2),
        available_at=clock_time + timedelta(microseconds=1),
    )
    gateway = PointInTimeDataGateway(
        SimulationClock(clock_time),
        [planted_future, visible_exactly, visible_before],
    )

    assert read_btc(gateway) == (visible_before, visible_exactly)


def test_advancing_clock_reveals_planted_future_data_deterministically() -> None:
    initially_visible = candle(BASE_TIME)
    future = candle(BASE_TIME + timedelta(hours=1))
    clock = SimulationClock(BASE_TIME + timedelta(hours=1))
    gateway = PointInTimeDataGateway(clock, [future, initially_visible])

    assert read_btc(gateway) == (initially_visible,)

    clock.advance_to(future.available_at)

    assert read_btc(gateway) == (initially_visible, future)


def test_incomplete_and_future_candles_cannot_leak() -> None:
    clock_time = BASE_TIME + timedelta(hours=1)
    completed = candle(BASE_TIME)
    incomplete = candle(BASE_TIME + timedelta(hours=1))
    gateway = PointInTimeDataGateway(
        SimulationClock(clock_time), [incomplete, completed]
    )

    returned = read_btc(gateway)

    assert returned == (completed,)
    assert all(item.close_time <= clock_time for item in returned)
    assert all(item.available_at <= clock_time for item in returned)


def test_gateway_filters_market_and_orders_candles_deterministically() -> None:
    earlier = candle(BASE_TIME)
    later = candle(BASE_TIME + timedelta(hours=1))
    other_pair = candle(BASE_TIME, pair=TradingPair.ETH_USD)
    gateway = PointInTimeDataGateway(
        SimulationClock(BASE_TIME + timedelta(hours=3)),
        [later, other_pair, earlier],
    )

    assert read_btc(gateway) == (earlier, later)


def test_gateway_rejects_exact_duplicate_candle_identity() -> None:
    duplicate = candle(BASE_TIME)

    with pytest.raises(ValueError, match="duplicate candle identity"):
        PointInTimeDataGateway(
            SimulationClock(BASE_TIME + timedelta(hours=2)),
            [duplicate, duplicate],
        )


@pytest.mark.parametrize("reverse", [False, True])
def test_gateway_rejects_conflicting_duplicate_identity_in_any_input_order(
    reverse: bool,
) -> None:
    first = candle(BASE_TIME)
    conflicting = first.model_copy(update={"close": Decimal("104")})
    candles = [first, conflicting]
    if reverse:
        candles.reverse()

    with pytest.raises(ValueError, match="duplicate candle identity"):
        PointInTimeDataGateway(SimulationClock(BASE_TIME + timedelta(hours=2)), candles)


def test_gateway_accepts_distinct_market_or_timestamp_identities() -> None:
    btc = candle(BASE_TIME)
    eth = candle(BASE_TIME, pair=TradingPair.ETH_USD)
    later_btc = candle(BASE_TIME + timedelta(hours=1))
    gateway = PointInTimeDataGateway(
        SimulationClock(BASE_TIME + timedelta(hours=3)),
        [later_btc, eth, btc],
    )

    assert read_btc(gateway) == (btc, later_btc)
    assert gateway.read_candles(
        venue=Venue.COINBASE,
        pair=TradingPair.ETH_USD,
        timeframe=Timeframe.ONE_HOUR,
    ) == (eth,)


def test_gateway_membership_is_isolated_from_source_list_mutation() -> None:
    original = candle(BASE_TIME)
    source = [original]
    gateway = PointInTimeDataGateway(
        SimulationClock(BASE_TIME + timedelta(hours=3)), source
    )

    source.clear()
    source.append(candle(BASE_TIME + timedelta(hours=1)))

    assert read_btc(gateway) == (original,)


def test_gateway_read_api_has_no_caller_supplied_cutoff() -> None:
    parameters = inspect.signature(PointInTimeDataGateway.read_candles).parameters

    assert set(parameters) == {"self", "venue", "pair", "timeframe"}
    assert all(
        parameter.kind
        not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        for parameter in parameters.values()
    )


def test_gateway_time_is_read_only_and_tracks_authoritative_clock() -> None:
    clock = SimulationClock(BASE_TIME)
    gateway = PointInTimeDataGateway(clock, [])
    assert gateway.now == BASE_TIME
    clock.advance_to(BASE_TIME + timedelta(hours=1))
    assert gateway.now == clock.now
    with pytest.raises(AttributeError):
        gateway.now = BASE_TIME  # type: ignore[misc]
