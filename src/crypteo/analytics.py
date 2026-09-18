"""Pure, fixed-window Decimal analytics for V1 market calculations."""

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)

from crypteo.domain._execution_arithmetic import _add, _multiply, _subtract
from crypteo.domain.market import Candle

_WINDOW_SIZE = 20
_DECIMAL_CONTEXT = Context(
    prec=50,
    rounding=ROUND_HALF_EVEN,
    Emin=-999999,
    Emax=999999,
    capitals=1,
    clamp=0,
    flags=[],
    traps=[InvalidOperation, DivisionByZero, Overflow],
)


@dataclass(frozen=True)
class BollingerStatistics:
    """Statistics and temporal provenance of 20 contiguous hourly closes."""

    sma: Decimal
    population_std: Decimal
    lower_band: Decimal
    observed_at: datetime
    available_at: datetime


def _sma20(candles: Iterable[Candle]) -> Decimal:
    """Reuse the scanner's ordered sum and private 50-digit SMA semantics."""
    window = tuple(candles)
    if len(window) != _WINDOW_SIZE:
        raise ValueError("SMA20 requires exactly 20 candles")
    with localcontext(_DECIMAL_CONTEXT):
        return sum((item.close for item in window), Decimal(0)) / _WINDOW_SIZE


def mean_true_range(candles: Iterable[Candle]) -> Decimal:
    """Exact arithmetic mean of 20 true ranges from 21 contiguous hourly bars.

    The first candle supplies only the previous close. This is not Wilder ATR.
    Visibility belongs to the gateway; malformed or incomplete windows raise.
    """
    window = tuple(Candle.model_validate(item.model_dump()) for item in candles)
    if len(window) != _WINDOW_SIZE + 1:
        raise ValueError("mean true range requires exactly 21 candles")
    total = Decimal(0)
    for previous, current in zip(window[:-1], window[1:], strict=True):
        if (previous.venue, previous.pair, previous.timeframe) != (
            current.venue,
            current.pair,
            current.timeframe,
        ):
            raise ValueError("candles must share one market identity")
        if current.open_time != previous.close_time:
            raise ValueError("candles must be contiguous and strictly ordered")
        true_range = max(
            _subtract(current.high, current.low),
            _subtract(current.high, previous.close).copy_abs(),
            _subtract(current.low, previous.close).copy_abs(),
        )
        total = _add(total, true_range)
    # Division by the fixed 20 is terminating: multiply exactly by 1/20.
    return _multiply(total, Decimal("0.05"))


def rolling_bollinger_statistics(
    candles: Iterable[Candle],
) -> tuple[BollingerStatistics | None, ...]:
    """Return one result per candle, including the current close in each window.

    Input must contain a single market in strictly increasing time order;
    duplicates and reversed ordering raise ValueError. Missing hours reset the
    window, yielding None until 20 contiguous candles are present. All arithmetic
    uses a private 50-digit, half-even Decimal context, entered only after input
    iteration and validation. Visibility filtering is the caller's responsibility
    through PointInTimeDataGateway.
    """
    window: deque[Candle] = deque(maxlen=_WINDOW_SIZE)
    results: list[BollingerStatistics | None] = []
    previous: Candle | None = None
    for candle in candles:
        if previous is not None:
            if (candle.venue, candle.pair, candle.timeframe) != (
                previous.venue,
                previous.pair,
                previous.timeframe,
            ):
                raise ValueError("candles must share one market identity")
            if candle.open_time <= previous.open_time:
                raise ValueError("candles must be strictly ordered without duplicates")
            if candle.open_time != previous.close_time:
                window.clear()
        window.append(candle)
        previous = candle
        if len(window) < _WINDOW_SIZE:
            results.append(None)
            continue

        with localcontext(_DECIMAL_CONTEXT):
            sma = _sma20(window)
            variance = (
                sum(((item.close - sma) ** 2 for item in window), Decimal(0))
                / _WINDOW_SIZE
            )
            population_std = variance.sqrt()
            lower_band = sma - 2 * population_std
        results.append(
            BollingerStatistics(
                sma=sma,
                population_std=population_std,
                lower_band=lower_band,
                observed_at=candle.close_time,
                available_at=max(item.available_at for item in window),
            )
        )
    return tuple(results)
