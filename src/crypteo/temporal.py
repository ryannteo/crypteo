"""Authoritative simulation time and point-in-time candle visibility."""

from collections.abc import Iterable
from datetime import datetime

from pydantic import TypeAdapter

from crypteo.domain.market import Candle, Timeframe, TradingPair, Venue
from crypteo.domain.time import UtcDatetime

_UTC_DATETIME_ADAPTER = TypeAdapter(UtcDatetime)


class SimulationClock:
    """A deterministic UTC clock controlled only by replay runtime."""

    def __init__(self, initial_time: datetime) -> None:
        self._now = _UTC_DATETIME_ADAPTER.validate_python(initial_time)

    @property
    def now(self) -> datetime:
        """Return the authoritative simulation time."""
        return self._now

    def advance_to(self, new_time: datetime) -> None:
        """Advance to an explicit UTC time without permitting reversal."""
        validated_time = _UTC_DATETIME_ADAPTER.validate_python(new_time)
        if validated_time < self._now:
            raise ValueError("simulation clock cannot move backward")
        self._now = validated_time


class PointInTimeDataGateway:
    """Expose only completed candles visible at the simulation clock."""

    def __init__(self, clock: SimulationClock, candles: Iterable[Candle]) -> None:
        materialized_candles = tuple(
            Candle.model_validate(candle.model_dump()) for candle in candles
        )
        identities = [
            (candle.venue, candle.pair, candle.timeframe, candle.open_time)
            for candle in materialized_candles
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate candle identity")
        self._clock = clock
        self._candles = tuple(
            sorted(materialized_candles, key=lambda candle: candle.open_time)
        )

    @property
    def now(self) -> datetime:
        """Read authoritative time without exposing clock mutation to consumers."""
        return self._clock.now

    def read_candles(
        self,
        *,
        venue: Venue,
        pair: TradingPair,
        timeframe: Timeframe,
    ) -> tuple[Candle, ...]:
        """Return deterministically ordered candles visible for one market."""
        now = self._clock.now
        return tuple(
            candle
            for candle in self._candles
            if candle.venue is venue
            and candle.pair is pair
            and candle.timeframe is timeframe
            and candle.close_time <= now
            and candle.available_at <= now
        )
