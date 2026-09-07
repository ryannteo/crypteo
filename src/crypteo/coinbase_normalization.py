"""Normalize decoded Coinbase candle rows into canonical market records."""

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from crypteo.domain.market import Candle, Timeframe, TradingPair, Venue


def normalize_coinbase_candles(
    *, pair: TradingPair, rows: Iterable[object]
) -> tuple[Candle, ...]:
    """Validate, deduplicate, and chronologically order Coinbase candle rows."""
    if not isinstance(pair, TradingPair):
        raise TypeError("pair must be a supported TradingPair")

    candles_by_identity: dict[
        tuple[Venue, TradingPair, Timeframe, datetime], Candle
    ] = {}
    for row in rows:
        candle = _normalize_row(pair, row)
        identity = (
            candle.venue,
            candle.pair,
            candle.timeframe,
            candle.open_time,
        )
        existing = candles_by_identity.get(identity)
        if existing is not None and existing != candle:
            raise ValueError("conflicting Coinbase candle rows share an identity")
        candles_by_identity[identity] = candle

    return tuple(
        sorted(candles_by_identity.values(), key=lambda candle: candle.open_time)
    )


def coinbase_row_open_time(row: object) -> datetime:
    """Validate row structure and timestamp without inspecting OHLCV values."""
    if not isinstance(row, Sequence) or isinstance(row, (str, bytes, bytearray)):
        raise ValueError("Coinbase candle row must be a sequence")
    if len(row) != 6:
        raise ValueError("Coinbase candle row must contain exactly six values")
    return _parse_timestamp(row[0])


def _normalize_row(pair: TradingPair, row: object) -> Candle:
    open_time = coinbase_row_open_time(row)
    assert isinstance(row, Sequence)
    _, low, high, open_price, close, volume = row
    close_time = open_time + Timeframe.ONE_HOUR.duration
    return Candle(
        venue=Venue.COINBASE,
        pair=pair,
        timeframe=Timeframe.ONE_HOUR,
        open_time=open_time,
        close_time=close_time,
        available_at=close_time,
        open=_parse_decimal(open_price),
        high=_parse_decimal(high),
        low=_parse_decimal(low),
        close=_parse_decimal(close),
        volume=_parse_decimal(volume),
    )


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Coinbase candle time must be an integer Unix timestamp")
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError) as error:
        raise ValueError("Coinbase candle time is invalid") from error


def _parse_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("Coinbase candle numeric values must be numbers")
    try:
        return Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("Coinbase candle numeric value is invalid") from error
