"""Locked hourly lower-Bollinger crossover scanner over visible history."""

import json
from decimal import Decimal
from hashlib import sha256

from crypteo.analytics import BollingerStatistics, rolling_bollinger_statistics
from crypteo.domain.market import Candle, Timeframe, TradingPair, Venue
from crypteo.domain.research import FrozenJsonObject, Opportunity
from crypteo.temporal import PointInTimeDataGateway

_TRIGGER_TYPE = "bollinger_lower_cross_v1"


class OpportunityScanner:
    """Return chronological snapshots with no event-delivery state.

    Each scan reads only gateway-visible Coinbase hourly candles. Unknown bands
    reset comparison state; the next valid band initializes it without emitting.
    Subsequent at/above-to-below transitions use each close's own lower band.
    """

    def __init__(self, gateway: PointInTimeDataGateway) -> None:
        self._gateway = gateway

    def scan(self, *, pair: TradingPair) -> tuple[Opportunity, ...]:
        """Return every qualifying opportunity in the currently visible history."""
        candles = self._gateway.read_candles(
            venue=Venue.COINBASE, pair=pair, timeframe=Timeframe.ONE_HOUR
        )
        statistics = rolling_bollinger_statistics(candles)
        previous: tuple[Candle, BollingerStatistics] | None = None
        opportunities: list[Opportunity] = []
        for candle, current in zip(candles, statistics, strict=True):
            if current is None:
                previous = None
                continue
            if previous is not None:
                previous_candle, previous_statistics = previous
                if (
                    previous_candle.close >= previous_statistics.lower_band
                    and candle.close < current.lower_band
                ):
                    opportunities.append(
                        _opportunity(
                            candle, current, previous_candle, previous_statistics
                        )
                    )
            previous = (candle, current)
        return tuple(opportunities)


def _opportunity(
    candle: Candle,
    current: BollingerStatistics,
    previous_candle: Candle,
    previous: BollingerStatistics,
) -> Opportunity:
    identity = {
        "trigger_type": _TRIGGER_TYPE,
        "venue": Venue.COINBASE.value,
        "pair": candle.pair.value,
        "timeframe": Timeframe.ONE_HOUR.value,
        "observed_at": current.observed_at.isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        ),
    }
    canonical_json = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return Opportunity(
        opportunity_id=f"sha256:{sha256(canonical_json).hexdigest()}",
        venue=Venue.COINBASE,
        pair=candle.pair,
        timeframe=Timeframe.ONE_HOUR,
        observed_at=current.observed_at,
        available_at=max(previous.available_at, current.available_at),
        trigger_type=_TRIGGER_TYPE,
        trigger_payload=FrozenJsonObject(
            {
                "previous_close": _decimal_string(previous_candle.close),
                "previous_lower_band": _decimal_string(previous.lower_band),
                "current_close": _decimal_string(candle.close),
                "current_lower_band": _decimal_string(current.lower_band),
            }
        ),
        summary=f"{candle.pair.value} 1h close crossed below its lower Bollinger band.",
    )


def _decimal_string(value: Decimal) -> str:
    """Serialize without exponent notation or caller-context rounding."""
    if value == 0:
        return "0"
    fixed_point = format(value, "f")
    return fixed_point.rstrip("0").rstrip(".") if "." in fixed_point else fixed_point
