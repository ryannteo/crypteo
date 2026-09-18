from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import (
    ROUND_DOWN,
    ROUND_UP,
    Decimal,
    DivisionByZero,
    Inexact,
    Rounded,
    getcontext,
    localcontext,
)

import pytest

from crypteo.analytics import (
    BollingerStatistics,
    mean_true_range,
    rolling_bollinger_statistics,
)
from crypteo.domain.market import Candle, Timeframe, TradingPair, Venue
from crypteo.temporal import PointInTimeDataGateway, SimulationClock

BASE_TIME = datetime(2022, 1, 1, tzinfo=UTC)
SQRT_TWO = Decimal("1.4142135623730950488016887242096980785696718753769")
SQRT_TWO_LOWER_BAND = Decimal("97.171572875253809902396622551580603842860656249246")


def range_candle(hour: int, price: str, high: str, low: str) -> Candle:
    return Candle.model_validate(
        candle(hour).model_dump()
        | {
            "open": price,
            "close": price,
            "high": high,
            "low": low,
        }
    )


@pytest.mark.parametrize(
    "previous,expected", [("101", "2"), ("90", "2.5"), ("110", "2.4")]
)
def test_mean_true_range_all_three_formula_branches(
    previous: str, expected: str
) -> None:
    candles = [range_candle(0, previous, previous, previous)] + [
        range_candle(i, "101", "102", "100") for i in range(1, 21)
    ]
    assert mean_true_range(candles) == Decimal(expected)


@pytest.mark.parametrize("count", [0, 1, 19, 20, 22, 40])
def test_mean_true_range_requires_exactly_twenty_one_candles(count: int) -> None:
    with pytest.raises(ValueError, match="exactly 21"):
        mean_true_range(candle(i) for i in range(count))


@pytest.mark.parametrize("case", ["gap", "reverse", "duplicate", "mixed", "corrupt"])
def test_mean_true_range_rejects_invalid_windows(case: str) -> None:
    candles = [candle(i) for i in range(21)]
    if case == "gap":
        candles[-1] = candle(21)
    elif case == "reverse":
        candles.reverse()
    elif case == "duplicate":
        candles[-1] = candles[-2]
    elif case == "mixed":
        candles[-1] = candle(20, pair=TradingPair.ETH_USD)
    else:
        candles[-1] = candles[-1].model_copy(update={"high": Decimal(1)})
    with pytest.raises(ValueError):
        mean_true_range(candles)


def test_mean_true_range_is_exact_beyond_fifty_digits_under_hostile_context() -> None:
    from fractions import Fraction

    high = "1" + "0" * 70 + ".0000000000000000000000000000001"
    low = "0.000000000000000000000000000000000000000001"
    candles = [range_candle(i, "100", high, low) for i in range(21)]
    oracle = Fraction(high) - Fraction(low)
    with localcontext() as hostile:
        hostile.prec, hostile.Emax, hostile.Emin, hostile.clamp = 1, 1, -1, 1
        for signal in hostile.traps:
            hostile.traps[signal] = True
        before = repr(hostile)
        assert Fraction(mean_true_range(candles)) == oracle
        assert repr(hostile) == before


def test_mean_true_range_is_an_unweighted_mean_of_only_the_last_twenty_ranges() -> None:
    candles = (
        [range_candle(0, "100", "9999", "1")]
        + [range_candle(i, "100", "101", "99") for i in range(1, 20)]
        + [range_candle(20, "100", "120", "80")]
    )
    # (19 * 2 + 40) / 20 = 3.9; neither first-bar range nor recursive smoothing.
    assert mean_true_range(candles) == Decimal("3.9")


def candle(
    hour: int,
    close: str = "100",
    *,
    pair: TradingPair = TradingPair.BTC_USD,
    available_at: datetime | None = None,
) -> Candle:
    close_time = BASE_TIME + timedelta(hours=hour + 1)
    return Candle(
        venue=Venue.COINBASE,
        pair=pair,
        timeframe=Timeframe.ONE_HOUR,
        open_time=BASE_TIME + timedelta(hours=hour),
        close_time=close_time,
        available_at=available_at or close_time,
        open=Decimal("50"),
        high=Decimal("10000"),
        low=Decimal("0.01"),
        close=Decimal(close),
        volume=Decimal("7"),
    )


def read_btc(gateway: PointInTimeDataGateway) -> tuple[Candle, ...]:
    return gateway.read_candles(
        venue=Venue.COINBASE,
        pair=TradingPair.BTC_USD,
        timeframe=Timeframe.ONE_HOUR,
    )


def test_alternating_closes_exact_golden() -> None:
    candles = tuple(candle(i, "99" if i % 2 == 0 else "101") for i in range(20))

    results = rolling_bollinger_statistics(candles)

    assert results == (None,) * 19 + (
        BollingerStatistics(
            sma=Decimal("100"),
            population_std=Decimal("1"),
            lower_band=Decimal("98"),
            observed_at=candles[-1].close_time,
            available_at=candles[-1].available_at,
        ),
    )


def test_non_perfect_square_population_standard_deviation() -> None:
    # Five 98s, ten 100s, five 102s: mean 100 and population variance 2.
    # The 50-significant-digit sqrt(2) literal was independently checked using
    # integer square root and midpoint rounding on 2 * 10**98. The band literal
    # was checked with integer subtraction and half-even rounding to 48 places.
    closes = ["98"] * 5 + ["100"] * 10 + ["102"] * 5

    result = rolling_bollinger_statistics(candle(i, c) for i, c in enumerate(closes))[
        -1
    ]

    assert result is not None
    assert result.sma == Decimal("100")
    assert result.population_std == SQRT_TWO
    assert result.lower_band == SQRT_TWO_LOWER_BAND


@pytest.mark.parametrize("count", [0, 19, 20, 21])
def test_warm_up_and_equal_closes(count: int) -> None:
    candles = tuple(candle(i, "123.45678901234567890123456789") for i in range(count))

    results = rolling_bollinger_statistics(candles)

    assert isinstance(results, tuple)
    assert len(results) == count
    assert results[:19] == (None,) * min(count, 19)
    for result, item in zip(results[19:], candles[19:], strict=True):
        assert result == BollingerStatistics(
            sma=Decimal("123.45678901234567890123456789"),
            population_std=Decimal("0"),
            lower_band=Decimal("123.45678901234567890123456789"),
            observed_at=item.close_time,
            available_at=item.available_at,
        )


def test_current_candle_is_included() -> None:
    candles = [candle(i) for i in range(19)] + [candle(19, "120")]

    result = rolling_bollinger_statistics(candles)[-1]

    assert result is not None
    assert result.sma == Decimal("101")
    assert result.population_std > 0


def test_twenty_first_candle_drops_oldest_close() -> None:
    candles = [candle(0, "80")] + [candle(i) for i in range(1, 21)]

    results = rolling_bollinger_statistics(candles)

    assert results[19] is not None
    assert results[19].sma == Decimal("99")
    assert results[20] is not None
    assert results[20].sma == Decimal("100")
    assert results[20].population_std == Decimal("0")
    assert results[20].lower_band == Decimal("100")


@pytest.mark.parametrize("missing_hours", [1, 3])
def test_internal_gap_invalidates_then_recovers(missing_hours: int) -> None:
    before = [candle(i, "1000") for i in range(20)]
    restart = 20 + missing_hours
    after = [candle(i) for i in range(restart, restart + 21)]

    results = rolling_bollinger_statistics(before + after)

    assert len(results) == 41
    assert results[:19] == (None,) * 19
    assert results[19] is not None
    assert results[19].sma == Decimal("1000")
    assert results[20:39] == (None,) * 19
    for result, item in zip(results[39:], after[19:], strict=True):
        assert result == BollingerStatistics(
            sma=Decimal("100"),
            population_std=Decimal("0"),
            lower_band=Decimal("100"),
            observed_at=item.close_time,
            available_at=item.available_at,
        )


def test_old_gap_during_initial_warm_up_does_not_poison_later_window() -> None:
    candles = [candle(0, "1000")] + [candle(i) for i in range(2, 23)]

    results = rolling_bollinger_statistics(candles)

    assert len(results) == 22
    assert results[:20] == (None,) * 20
    assert results[20] is not None
    assert results[20].sma == Decimal("100")
    assert results[21] is not None
    assert results[21].sma == Decimal("100")


@pytest.mark.parametrize("hours", [(0, 0), (1, 0), (0, 2, 1), (0, 1, 0)])
def test_rejects_duplicates_and_reversed_order(hours: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="strictly ordered"):
        rolling_bollinger_statistics(candle(i) for i in hours)


def test_rejects_conflicting_duplicate_after_valid_window() -> None:
    candles = [candle(i) for i in range(20)] + [candle(19, "120")]

    with pytest.raises(ValueError, match="duplicates"):
        rolling_bollinger_statistics(candles)


@pytest.mark.parametrize("count", [1, 20])
def test_rejects_mixed_market(count: int) -> None:
    candles = [candle(i) for i in range(count)] + [
        candle(count, pair=TradingPair.ETH_USD)
    ]

    with pytest.raises(ValueError, match="market identity"):
        rolling_bollinger_statistics(candles)


@pytest.mark.parametrize("precision,rounding", [(6, ROUND_UP), (80, ROUND_DOWN)])
def test_decimal_context_is_independent_and_preserved(
    precision: int, rounding: str
) -> None:
    closes = ["98"] * 5 + ["100"] * 10 + ["102"] * 5
    candles = [candle(i, c) for i, c in enumerate(closes)]
    with localcontext() as caller_context:
        caller_context.prec = precision
        caller_context.rounding = rounding
        caller_context.Emin = -2
        caller_context.Emax = 2
        caller_context.traps[Inexact] = True
        caller_context.traps[Rounded] = True
        caller_context.flags[DivisionByZero] = True
        before = repr(caller_context)

        for _ in range(2):
            result = rolling_bollinger_statistics(candles)[-1]

            assert result is not None
            assert result.sma == Decimal("100")
            assert result.population_std == SQRT_TWO
            assert result.lower_band == SQRT_TWO_LOWER_BAND
            assert getcontext() is caller_context
            assert repr(caller_context) == before


def test_invalid_input_also_preserves_caller_context() -> None:
    candles = [candle(i, str(i + 1)) for i in range(20)] + [candle(0)]
    before = repr(getcontext())

    with pytest.raises(ValueError, match="strictly ordered"):
        rolling_bollinger_statistics(candles)

    assert repr(getcontext()) == before


def test_generator_observes_only_caller_decimal_context() -> None:
    candles = tuple(candle(i, str(i + 1)) for i in range(21))
    expected = rolling_bollinger_statistics(candles)
    consumed: list[Candle] = []
    with localcontext() as caller_context:
        caller_context.prec = 7
        caller_context.rounding = ROUND_DOWN
        before = repr(caller_context)

        def generate() -> Iterator[Candle]:
            for item in candles:
                assert getcontext() is caller_context
                assert repr(caller_context) == before
                consumed.append(item)
                yield item
                assert getcontext() is caller_context
                assert repr(caller_context) == before

        assert rolling_bollinger_statistics(generate()) == expected
        assert consumed == list(candles)
        assert getcontext() is caller_context
        assert repr(caller_context) == before


def test_generator_holding_own_decimal_context_preserves_results_and_contexts() -> None:
    closes = ["98"] * 5 + ["100"] * 10 + ["102"] * 5 + ["99"]
    candles = tuple(candle(i, c) for i, c in enumerate(closes))
    expected = rolling_bollinger_statistics(candles)
    consumed: list[Candle] = []
    with localcontext() as caller_context:
        before = repr(caller_context)

        def generate() -> Iterator[Candle]:
            with localcontext() as generator_context:
                generator_context.prec = 6
                generator_context.rounding = ROUND_UP
                generator_context.clear_flags()
                generator_context.flags[DivisionByZero] = True
                generator_before = repr(generator_context)
                for item in candles:
                    consumed.append(item)
                    yield item
                    assert getcontext() is generator_context
                    assert repr(generator_context) == generator_before

        results = rolling_bollinger_statistics(generate())

        assert results == expected
        assert consumed == list(candles)
        assert results[19] is not None
        assert results[19].sma == Decimal("100")
        assert results[19].population_std == SQRT_TWO
        assert results[19].lower_band == SQRT_TWO_LOWER_BAND
        assert getcontext() is caller_context
        assert repr(caller_context) == before


def test_sma_rounds_exact_midpoint_to_even_at_precision_fifty() -> None:
    # The exact sum is 20 + 10**-48, representable in 50 significant digits.
    # Dividing by 20 gives 1 + 5 * 10**-50, exactly halfway between 1 and
    # 1 + 10**-49. Half-even selects 1; half-up would select the latter.
    candles = [candle(i, "1") for i in range(19)] + [
        candle(19, "1.000000000000000000000000000000000000000000000001")
    ]

    result = rolling_bollinger_statistics(candles)[-1]

    assert result is not None
    assert result.sma == Decimal("1")


@pytest.mark.parametrize(
    "low,high,sma,lower", [("1", "6", "2", "-2"), ("3", "8", "4", "0")]
)
def test_nonpositive_derived_lower_band_is_valid(
    low: str, high: str, sma: str, lower: str
) -> None:
    closes = [low] * 16 + [high] * 4

    result = rolling_bollinger_statistics(candle(i, c) for i, c in enumerate(closes))[
        -1
    ]

    assert result is not None
    assert result.sma == Decimal(sma)
    assert result.population_std == Decimal("2")
    assert result.lower_band == Decimal(lower)


def test_metadata_uses_only_contributing_window() -> None:
    delayed_at = BASE_TIME + timedelta(hours=100)
    candles = [candle(0, available_at=delayed_at)] + [candle(i) for i in range(1, 21)]

    results = rolling_bollinger_statistics(candles)

    assert results[19] is not None
    assert results[19].observed_at == candles[19].close_time
    assert results[19].available_at == delayed_at
    assert results[20] is not None
    assert results[20].observed_at == candles[20].close_time
    assert results[20].available_at == candles[20].available_at


def test_statistics_are_immutable() -> None:
    result = rolling_bollinger_statistics(candle(i) for i in range(20))[-1]

    assert result is not None
    with pytest.raises(FrozenInstanceError):
        result.sma = Decimal("200")  # type: ignore[misc]


def test_gateway_hides_future_closes_until_exact_completion_boundary() -> None:
    candles = tuple(candle(i) for i in range(20)) + (candle(20, "120"),)
    boundary = candles[19].close_time
    clock = SimulationClock(boundary - timedelta(microseconds=1))
    gateway = PointInTimeDataGateway(clock, candles)

    assert read_btc(gateway) == candles[:19]
    assert rolling_bollinger_statistics(read_btc(gateway)) == (None,) * 19

    clock.advance_to(boundary)

    assert read_btc(gateway) == candles[:20]
    result = rolling_bollinger_statistics(read_btc(gateway))[-1]
    assert result is not None
    assert result.sma == Decimal("100")
    assert result.observed_at == boundary
    assert result.available_at == boundary

    clock.advance_to(candles[20].close_time - timedelta(microseconds=1))
    assert read_btc(gateway) == candles[:20]
    assert rolling_bollinger_statistics(read_btc(gateway))[-1] == result

    clock.advance_to(candles[20].close_time)

    assert read_btc(gateway) == candles
    result = rolling_bollinger_statistics(read_btc(gateway))[-1]
    assert result is not None
    assert result.sma == Decimal("101")
    assert result.observed_at == candles[20].close_time
    assert result.available_at == clock.now


def test_gateway_delayed_internal_candle_enters_only_at_exact_availability() -> None:
    delayed_at = BASE_TIME + timedelta(hours=21, microseconds=1)
    candles = tuple(
        candle(
            i,
            "99" if i % 2 == 0 else "101",
            available_at=delayed_at if i == 10 else None,
        )
        for i in range(20)
    ) + (candle(20), candle(21, "9999"))
    clock = SimulationClock(BASE_TIME + timedelta(hours=20))
    gateway = PointInTimeDataGateway(clock, candles)

    assert read_btc(gateway) == candles[:10] + candles[11:20]
    assert rolling_bollinger_statistics(read_btc(gateway)) == (None,) * 19

    clock.advance_to(delayed_at - timedelta(microseconds=1))

    # Twenty visible records are insufficient because the hidden candle is a gap.
    assert read_btc(gateway) == candles[:10] + candles[11:21]
    assert rolling_bollinger_statistics(read_btc(gateway)) == (None,) * 20

    clock.advance_to(delayed_at)

    assert read_btc(gateway) == candles[:21]
    results = rolling_bollinger_statistics(read_btc(gateway))
    assert results[:19] == (None,) * 19
    assert results[19] == BollingerStatistics(
        sma=Decimal("100"),
        population_std=Decimal("1"),
        lower_band=Decimal("98"),
        observed_at=candles[19].close_time,
        available_at=delayed_at,
    )
    assert results[20] is not None
    assert results[20].sma == Decimal("100.05")
    assert results[20].observed_at == candles[20].close_time
    assert results[20].available_at == delayed_at
