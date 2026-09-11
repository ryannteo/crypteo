"""Pure, fixed-window Decimal analytics for the locked V1 scanner."""

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
            sma = sum((item.close for item in window), Decimal(0)) / _WINDOW_SIZE
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
