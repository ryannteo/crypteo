"""Stateless deterministic trade levels from current point-in-time history."""

from functools import partial

from pydantic import TypeAdapter

from crypteo._execution_costs import _floor_increment
from crypteo.analytics import _sma20, mean_true_range
from crypteo.domain._execution_arithmetic import _multiply, _subtract
from crypteo.domain.market import Candle, Timeframe, TradingPair, Venue
from crypteo.domain.trade_terms import TradeTerms, TradeTermsConfig, TradeTermsRejection
from crypteo.temporal import PointInTimeDataGateway

_PAIR_ADAPTER = TypeAdapter(TradingPair)


class TradeTermsService:
    """Generate from exactly [gateway.now - 21h, gateway.now), with no fallback."""

    def __init__(
        self, gateway: PointInTimeDataGateway, config: TradeTermsConfig
    ) -> None:
        self._gateway = gateway
        self._config = TradeTermsConfig.model_validate(config.model_dump())

    def generate(self, *, pair: TradingPair) -> TradeTerms | TradeTermsRejection:
        """Reject missing reference/history, zero range, raw stop/target, alignment.

        Malformed inputs raise before ordinary rejection. Time and history come
        exclusively from the gateway; this service never advances its clock.
        """
        pair = _PAIR_ADAPTER.validate_python(pair)
        now = self._gateway.now
        venue, timeframe = Venue.COINBASE, Timeframe.ONE_HOUR
        candles = tuple(
            Candle.model_validate(item.model_dump())
            for item in self._gateway.read_candles(
                venue=venue, pair=pair, timeframe=timeframe
            )
        )
        previous: Candle | None = None
        for candle in candles:
            if (candle.venue, candle.pair, candle.timeframe) != (
                venue,
                pair,
                timeframe,
            ):
                raise ValueError("gateway returned a different market")
            if candle.close_time > now or candle.available_at > now:
                raise ValueError("gateway returned invisible history")
            if previous is not None and candle.open_time <= previous.open_time:
                raise ValueError("candles must be strictly ordered without duplicates")
            previous = candle
        reject = partial(
            TradeTermsRejection,
            venue=venue,
            pair=pair,
            timeframe=timeframe,
            decision_at=now,
            config=self._config,
        )
        if not candles or candles[-1].close_time != now:
            return reject(reason="REFERENCE_UNAVAILABLE")
        start = now - 21 * timeframe.duration
        window = tuple(candle for candle in candles if candle.open_time >= start)
        if len(window) != 21 or any(
            candle.open_time != start + index * timeframe.duration
            for index, candle in enumerate(window)
        ):
            return reject(reason="HISTORY_UNAVAILABLE")
        reference = window[-1].close
        mean_range = mean_true_range(window)
        if mean_range == 0:
            return reject(reason="ZERO_MEAN_TRUE_RANGE")
        raw_stop = _subtract(
            reference, _multiply(mean_range, self._config.stop_true_range_multiple)
        )
        if not 0 < raw_stop < reference:
            return reject(reason="INVALID_RAW_STOP")
        target_sma = _sma20(window[1:])
        if not reference < target_sma:
            return reject(reason="INVALID_RAW_TARGET")
        stop = _floor_increment(raw_stop, self._config.price_increment)
        target = _floor_increment(target_sma, self._config.price_increment)
        if not 0 < stop < reference < target:
            return reject(reason="INVALID_ALIGNED_LEVELS")
        return TradeTerms(
            venue=venue,
            pair=pair,
            timeframe=timeframe,
            decision_at=now,
            config=self._config,
            reference_price=reference,
            mean_true_range=mean_range,
            target_sma=target_sma,
            stop_price=stop,
            target_price=target,
        )
