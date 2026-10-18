"""Ten deterministic research capabilities through one authoritative gateway."""

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from pydantic import TypeAdapter

from crypteo.analytics import _sma20, mean_true_range, rolling_bollinger_statistics
from crypteo.coinbase_acquisition import CoinbaseAcquisitionRecord
from crypteo.coinbase_dataset import (
    CoinbaseCanonicalDataset,
    CoinbaseHistoricalDataset,
    canonical_dataset_artifacts,
)
from crypteo.domain._market_research_arithmetic import (
    _bands,
    _difference,
    _divide,
    _mean,
    _return,
)
from crypteo.domain.market import Candle, TradingPair
from crypteo.domain.market_research import (
    BollingerPositionPayload,
    CandleStructurePayload,
    DailySection,
    FourHourSection,
    HigherTimeframePayload,
    HourlyDatetime,
    LevelsPayload,
    LowerBandCrossingsPayload,
    MarketResearchCapability,
    MarketResearchPayload,
    MarketResearchPolicy,
    MarketResearchQuery,
    MarketSourceBinding,
    MarketSourceReference,
    MomentumPayload,
    RelativeStrengthPayload,
    ResearchContext,
    ResearchInput,
    ResearchToolSuccess,
    ResearchToolUnavailable,
    ResearchUnavailableReason,
    ResearchWindow,
    TrendPayload,
    UndefinedReason,
    VolatilityPayload,
    VolumeContextPayload,
    _candle_anatomy,
    _canonical,
    _digest,
    _evidence_identity,
    _ratio,
)
from crypteo.domain.research import EvidenceCategory, EvidenceItem, EvidenceProvenance
from crypteo.temporal import PointInTimeDataGateway, SimulationClock

_HOURLY = TypeAdapter(HourlyDatetime)
_CANDLES = TypeAdapter(tuple[Candle, ...])
_POLICY = MarketResearchPolicy()


class MarketResearchService:
    """Stateless, fixed-window evidence; runtime supplies bound source provenance.

    Use build_market_research for canonical dataset assembly. Direct construction
    is a runtime integration seam: sources must describe the gateway's datasets.
    """

    def __init__(
        self, gateway: PointInTimeDataGateway, sources: Iterable[MarketSourceBinding]
    ) -> None:
        bindings = tuple(MarketSourceBinding.model_validate(item) for item in sources)
        if len({item.pair for item in bindings}) != len(bindings):
            raise ValueError("supply exactly one source binding per pair")
        self._gateway = gateway
        self._sources = MappingProxyType({item.pair: item for item in bindings})

    @property
    def now(self) -> datetime:
        """Read the tools' authoritative time without exposing clock mutation."""
        return self._gateway.now

    @property
    def source_bindings(self) -> tuple[MarketSourceBinding, ...]:
        """Immutable provenance in canonical asset order, without dataset access."""
        return tuple(self._sources[p] for p in TradingPair if p in self._sources)

    def trend(self, pair: TradingPair) -> ResearchToolSuccess | ResearchToolUnavailable:
        return self._research(
            MarketResearchQuery(capability=MarketResearchCapability.TREND, pair=pair)
        )

    def momentum(
        self, pair: TradingPair
    ) -> ResearchToolSuccess | ResearchToolUnavailable:
        return self._research(
            MarketResearchQuery(capability=MarketResearchCapability.MOMENTUM, pair=pair)
        )

    def volatility(
        self, pair: TradingPair
    ) -> ResearchToolSuccess | ResearchToolUnavailable:
        return self._research(
            MarketResearchQuery(
                capability=MarketResearchCapability.VOLATILITY, pair=pair
            )
        )

    def bollinger_position(
        self, pair: TradingPair
    ) -> ResearchToolSuccess | ResearchToolUnavailable:
        return self._research(
            MarketResearchQuery(
                capability=MarketResearchCapability.BOLLINGER_POSITION, pair=pair
            )
        )

    def levels(
        self, pair: TradingPair
    ) -> ResearchToolSuccess | ResearchToolUnavailable:
        return self._research(
            MarketResearchQuery(capability=MarketResearchCapability.LEVELS, pair=pair)
        )

    def volume_context(
        self, pair: TradingPair
    ) -> ResearchToolSuccess | ResearchToolUnavailable:
        return self._research(
            MarketResearchQuery(
                capability=MarketResearchCapability.VOLUME_CONTEXT, pair=pair
            )
        )

    def candle_structure(
        self, pair: TradingPair
    ) -> ResearchToolSuccess | ResearchToolUnavailable:
        return self._research(
            MarketResearchQuery(
                capability=MarketResearchCapability.CANDLE_STRUCTURE, pair=pair
            )
        )

    def higher_timeframe(
        self, pair: TradingPair
    ) -> ResearchToolSuccess | ResearchToolUnavailable:
        return self._research(
            MarketResearchQuery(
                capability=MarketResearchCapability.HIGHER_TIMEFRAME, pair=pair
            )
        )

    def lower_band_crossings(
        self, pair: TradingPair
    ) -> ResearchToolSuccess | ResearchToolUnavailable:
        return self._research(
            MarketResearchQuery(
                capability=MarketResearchCapability.LOWER_BAND_CROSSINGS, pair=pair
            )
        )

    def relative_strength(
        self, pair: TradingPair, benchmark: TradingPair
    ) -> ResearchToolSuccess | ResearchToolUnavailable:
        return self._research(
            MarketResearchQuery(
                capability=MarketResearchCapability.RELATIVE_STRENGTH,
                pair=pair,
                benchmark=benchmark,
            )
        )

    def _research(
        self, query: MarketResearchQuery
    ) -> ResearchToolSuccess | ResearchToolUnavailable:
        now = _HOURLY.validate_python(self._gateway.now)
        count = _POLICY.required_bars(query.capability)
        window = ResearchWindow(
            start=now - count * query.timeframe.duration, end=now, bar_count=count
        )
        for role, pair in query.markets:
            if pair not in self._sources:
                return ResearchToolUnavailable(
                    query=query,
                    window=window,
                    role=role,
                    reason=ResearchUnavailableReason.SOURCE_UNAVAILABLE,
                )

        # Validate every returned record before classifying ordinary missing data.
        # Read each market at the captured T and detect clock changes after each read.
        histories: list[tuple[Candle, ...]] = []
        for _, pair in query.markets:
            history = self._read(query, pair, now)
            if self._gateway.now != now:
                raise ValueError("gateway clock changed during research reads")
            histories.append(history)

        windows: list[tuple[Candle, ...]] = []
        inputs: list[ResearchInput] = []
        for (role, pair), history in zip(query.markets, histories, strict=True):
            reason = _unavailable(history, window)
            if reason is not None:
                return ResearchToolUnavailable(
                    query=query, window=window, role=role, reason=reason
                )
            contributing = tuple(
                item for item in history if item.open_time >= window.start
            )
            windows.append(contributing)
            inputs.append(
                ResearchInput(
                    source=MarketSourceReference(
                        **self._sources[pair].model_dump(), role=role
                    ),
                    window_digest=_digest(
                        {
                            "identity_encoding_version": "1",
                            "market": {
                                "venue": query.venue,
                                "pair": pair,
                                "timeframe": query.timeframe,
                            },
                            "window": window,
                            "candles": contributing,
                        }
                    ),
                )
            )
        payload = _calculate(
            query.capability, windows[0], windows[1] if len(windows) == 2 else None
        )
        context = ResearchContext(query=query, window=window, inputs=tuple(inputs))
        # All dependencies participate, including the previous-close seed for TR.
        available_at = max(item.available_at for candles in windows for item in candles)
        evidence = EvidenceItem(
            evidence_id=_evidence_identity(context, payload),
            category=EvidenceCategory.MARKET,
            evidence_type=query.capability.value,
            source="coinbase_exchange_candles",
            observed_at=now,
            available_at=available_at,
            provenance=EvidenceProvenance(
                producer=f"{_POLICY.policy_id}.{query.capability.value}",
                source_version="1",
                query_params=_canonical(context),
            ),
            summary=(
                f"Deterministic {query.capability.value} measurements "
                "from completed hourly candles."
            ),
            relevance="Market-derived measurements for research interpretation.",
            payload=_canonical(payload),
        )
        return ResearchToolSuccess(context=context, payload=payload, evidence=evidence)

    def _read(
        self, query: MarketResearchQuery, pair: TradingPair, now: datetime
    ) -> tuple[Candle, ...]:
        candles = tuple(
            Candle.model_validate(item.model_dump())
            for item in self._gateway.read_candles(
                venue=query.venue,
                pair=pair,
                timeframe=query.timeframe,
            )
        )
        previous: Candle | None = None
        for candle in candles:
            if (candle.venue, candle.pair, candle.timeframe) != (
                query.venue,
                pair,
                query.timeframe,
            ):
                raise ValueError("gateway returned a different market")
            if candle.close_time > now or candle.available_at > now:
                raise ValueError("gateway returned invisible history")
            if previous is not None and candle.open_time <= previous.open_time:
                raise ValueError(
                    "gateway history must be strictly ordered without duplicates"
                )
            previous = candle
        return candles


def _unavailable(
    history: tuple[Candle, ...], window: ResearchWindow
) -> ResearchUnavailableReason | None:
    if not history or history[-1].close_time != window.end:
        return ResearchUnavailableReason.REFERENCE_UNAVAILABLE
    if history[0].open_time > window.start:
        return ResearchUnavailableReason.INSUFFICIENT_HISTORY
    actual = tuple(item.open_time for item in history if item.open_time >= window.start)
    expected = tuple(
        window.start + i * window.timeframe.duration for i in range(window.bar_count)
    )
    if actual != expected:
        return ResearchUnavailableReason.HISTORY_GAP
    return None


def _calculate(
    capability: MarketResearchCapability,
    candles: tuple[Candle, ...],
    benchmark: tuple[Candle, ...] | None,
) -> MarketResearchPayload:
    close = candles[-1].close
    match capability:
        case MarketResearchCapability.TREND:
            sma20 = _sma20(candles[-20:])
            sma50 = _mean(tuple(item.close for item in candles))
            return TrendPayload(
                close=close,
                sma20=sma20,
                sma50=sma50,
                close_vs_sma20=_return(close, sma20),
                close_vs_sma50=_return(close, sma50),
                sma20_vs_sma50=_return(sma20, sma50),
            )
        case MarketResearchCapability.MOMENTUM:
            current = _return(close, candles[-7].close)
            previous = _return(candles[-7].close, candles[-13].close)
            return MomentumPayload(
                return_1h=_return(close, candles[-2].close),
                return_6h=current,
                return_24h=_return(close, candles[-25].close),
                previous_return_6h=previous,
                return_change_6h=_difference(current, previous),
            )
        case MarketResearchCapability.VOLATILITY:
            current_tr = mean_true_range(candles[20:])
            previous_tr = mean_true_range(candles[:21])
            current = _divide(current_tr, close)
            previous = _divide(previous_tr, candles[20].close)
            return VolatilityPayload(
                current_mean_true_range=current_tr,
                previous_mean_true_range=previous_tr,
                current_ending_close=close,
                previous_ending_close=candles[20].close,
                current_normalized_range=current,
                previous_normalized_range=previous,
                normalized_range_ratio=_ratio(
                    current, previous, UndefinedReason.ZERO_PREVIOUS_NORMALIZED_RANGE
                ),
            )
        case MarketResearchCapability.BOLLINGER_POSITION:
            stats = rolling_bollinger_statistics(candles)[-1]
            if stats is None:
                raise ValueError("validated Bollinger window unexpectedly unavailable")
            _, upper = _bands(stats.sma, stats.population_std)
            width = _difference(upper, stats.lower_band)
            return BollingerPositionPayload(
                close=close,
                sma20=stats.sma,
                population_std=stats.population_std,
                lower_band=stats.lower_band,
                upper_band=upper,
                percent_b=_ratio(
                    _difference(close, stats.lower_band),
                    width,
                    UndefinedReason.ZERO_BAND_WIDTH,
                ),
                bandwidth_fraction=_divide(width, stats.sma),
            )
        case MarketResearchCapability.LEVELS:
            # Equal extrema select the most recent completed prior candle.
            high = max(candles[:-1], key=lambda item: (item.high, item.close_time))
            low = min(reversed(candles[:-1]), key=lambda item: item.low)
            return LevelsPayload(
                close=close,
                recent_high=high.high,
                recent_low=low.low,
                recent_high_close_time=high.close_time,
                recent_low_close_time=low.close_time,
                recent_high_distance=_return(close, high.high),
                recent_low_distance=_return(close, low.low),
            )
        case MarketResearchCapability.VOLUME_CONTEXT:
            baseline = _mean(tuple(item.volume for item in candles[:-1]))
            return VolumeContextPayload(
                current_base_volume=candles[-1].volume,
                previous_mean_base_volume=baseline,
                volume_ratio=_ratio(
                    candles[-1].volume, baseline, UndefinedReason.ZERO_BASELINE_VOLUME
                ),
            )
        case MarketResearchCapability.CANDLE_STRUCTURE:
            last = candles[-1]
            return CandleStructurePayload(
                open=last.open,
                high=last.high,
                low=last.low,
                close=close,
                **_candle_anatomy(
                    last.open, last.high, last.low, close, mean_true_range(candles[:21])
                ),
            )
        case MarketResearchCapability.HIGHER_TIMEFRAME:
            return HigherTimeframePayload(
                four_hour=FourHourSection(**_block_section(candles, 4)),
                daily=DailySection(**_block_section(candles, 24)),
            )
        case MarketResearchCapability.LOWER_BAND_CROSSINGS:
            return _lower_band_crossings(candles)
        case MarketResearchCapability.RELATIVE_STRENGTH:
            if benchmark is None or tuple(c.open_time for c in candles) != tuple(
                c.open_time for c in benchmark
            ):
                raise ValueError("relative strength requires exactly aligned windows")
            subject6, subject24 = (
                _return(close, candles[-7].close),
                _return(close, candles[-25].close),
            )
            benchmark6 = _return(benchmark[-1].close, benchmark[-7].close)
            benchmark24 = _return(benchmark[-1].close, benchmark[-25].close)
            return RelativeStrengthPayload(
                subject_pair=candles[-1].pair,
                benchmark_pair=benchmark[-1].pair,
                subject_return_6h=subject6,
                subject_return_24h=subject24,
                benchmark_return_6h=benchmark6,
                benchmark_return_24h=benchmark24,
                spread_6h=_difference(subject6, benchmark6),
                spread_24h=_difference(subject24, benchmark24),
            )


def _block_section(candles: tuple[Candle, ...], hours: int) -> dict[str, Any]:
    """25 consecutive blocks of `hours` candles ending at T; block 0 is newest."""
    blocks = tuple(
        candles[len(candles) - (k + 1) * hours : len(candles) - k * hours]
        for k in range(25)
    )
    closes = tuple(block[-1].close for block in blocks)
    close, sma20, previous_sma20 = closes[0], _mean(closes[:20]), _mean(closes[5:])
    range_high = max(item.high for block in blocks[1:21] for item in block)
    range_low = min(item.low for block in blocks[1:21] for item in block)
    return dict(
        close=close,
        sma20=sma20,
        previous_sma20=previous_sma20,
        close_vs_sma20=_return(close, sma20),
        sma20_change=_return(sma20, previous_sma20),
        close_5_blocks_ago=closes[5],
        return_5_blocks=_return(close, closes[5]),
        range_high=range_high,
        range_low=range_low,
        range_high_distance=_return(close, range_high),
        range_low_distance=_return(close, range_low),
    )


_HORIZON = 6


def _lower_band_crossings(candles: tuple[Candle, ...]) -> LowerBandCrossingsPayload:
    """Sample scanner-rule crossings whose whole outcome horizon closed by T.

    The window ends at T, so no outcome can read a candle after the window.
    """
    statistics = rolling_bollinger_statistics(candles)
    last = len(candles) - 1
    pending = 0
    reached: list[bool] = []
    closes: list[Decimal] = []
    adverse: list[Decimal] = []
    favorable: list[Decimal] = []
    for i in range(1, len(candles)):
        previous, current = statistics[i - 1], statistics[i]
        if previous is None or current is None:
            continue
        if not (
            candles[i - 1].close >= previous.lower_band
            and candles[i].close < current.lower_band
        ):
            continue
        if i + _HORIZON > last:
            pending += 1
            continue
        entry, outcome = candles[i].close, candles[i + 1 : i + 1 + _HORIZON]
        reached.append(any(item.high >= current.sma for item in outcome))
        closes.append(_return(outcome[-1].close, entry))
        adverse.append(_return(min(item.low for item in outcome), entry))
        favorable.append(_return(max(item.high for item in outcome), entry))
    count = len(closes)
    ordered = sorted(closes)
    middle = count // 2
    median = (
        None
        if not count
        else ordered[middle]
        if count % 2
        else _mean((ordered[middle - 1], ordered[middle]))
    )
    positive = sum(1 for value in closes if value > 0)
    return LowerBandCrossingsPayload(
        sample_count=count,
        pending_count=pending,
        reached_sma20_count=sum(reached),
        positive_close_count=positive,
        reached_sma20_fraction=_ratio(
            Decimal(sum(reached)), Decimal(count), UndefinedReason.ZERO_SAMPLE
        ),
        positive_close_fraction=_ratio(
            Decimal(positive), Decimal(count), UndefinedReason.ZERO_SAMPLE
        ),
        mean_close_return=_mean(tuple(closes)) if count else None,
        median_close_return=median,
        min_close_return=ordered[0] if count else None,
        max_close_return=ordered[-1] if count else None,
        mean_adverse_excursion=_mean(tuple(adverse)) if count else None,
        mean_favorable_excursion=_mean(tuple(favorable)) if count else None,
    )


def build_market_research(
    clock: SimulationClock,
    datasets: Iterable[CoinbaseCanonicalDataset | CoinbaseHistoricalDataset],
) -> MarketResearchService:
    """Validate canonical inputs and bind their provenance to the same gateway data.

    The supplied authoritative clock remains runtime-owned. This assembly neither
    reads wall time nor changes scanner-only replay. Raw capture verification stays
    at the existing acquisition/publication boundary.
    """
    candles: list[Candle] = []
    sources: list[MarketSourceBinding] = []
    for dataset in datasets:
        manifest, encoded = canonical_dataset_artifacts(
            dataset.candles, dataset.manifest
        )
        acquisition = dataset.acquisition
        if acquisition is not None:
            acquisition = CoinbaseAcquisitionRecord.model_validate(
                acquisition.model_dump()
            )
            if acquisition.dataset_version != manifest.dataset_version:
                raise ValueError("acquisition dataset linkage mismatch")
        candles.extend(_CANDLES.validate_json(encoded))
        sources.append(
            MarketSourceBinding(
                venue=manifest.venue,
                pair=manifest.pair,
                timeframe=manifest.timeframe,
                source_schema_version=manifest.schema_version,
                dataset_version=manifest.dataset_version,
                acquisition_id=acquisition.acquisition_id if acquisition else None,
            )
        )
    return MarketResearchService(PointInTimeDataGateway(clock, candles), sources)
