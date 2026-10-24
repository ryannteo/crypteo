"""Immutable fixed V1 market evidence, independent of interpretation and replay."""

import json
from collections.abc import Iterator, Mapping
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from itertools import pairwise
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from crypteo.domain._market_research_arithmetic import (
    _bands,
    _difference,
    _divide,
    _return,
)
from crypteo.domain.market import (
    ExactDecimal,
    NonNegativeDecimal,
    PositiveDecimal,
    Timeframe,
    TradingPair,
    Venue,
)
from crypteo.domain.research import EvidenceCategory, EvidenceItem
from crypteo.domain.time import UtcDatetime


def _hourly(value: datetime) -> datetime:
    if value.minute or value.second or value.microsecond:
        raise ValueError("research time must be an hourly UTC boundary")
    return value


HourlyDatetime = Annotated[UtcDatetime, AfterValidator(_hourly)]
Digest = Annotated[str, Field(strict=True, pattern=r"^sha256:[0-9a-f]{64}$")]
_HOUR = Timeframe.ONE_HOUR.duration


class _Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )


class MarketResearchCapability(StrEnum):
    TREND = "trend"
    MOMENTUM = "momentum"
    VOLATILITY = "volatility"
    BOLLINGER_POSITION = "bollinger_position"
    LEVELS = "levels"
    VOLUME_CONTEXT = "volume_context"
    CANDLE_STRUCTURE = "candle_structure"
    HIGHER_TIMEFRAME = "higher_timeframe"
    LOWER_BAND_CROSSINGS = "lower_band_crossings"
    RELATIVE_STRENGTH = "relative_strength"


class MarketRole(StrEnum):
    SUBJECT = "subject"
    BENCHMARK = "benchmark"


class MarketResearchPolicy(_Contract):
    """One fixed policy; window lengths are not configurable query parameters."""

    policy_id: Literal["market_research_v3"] = "market_research_v3"

    def required_bars(self, capability: MarketResearchCapability) -> int:
        match capability:
            case MarketResearchCapability.TREND:
                return 50
            case MarketResearchCapability.VOLATILITY:
                return 41
            case MarketResearchCapability.BOLLINGER_POSITION:
                return 20
            case MarketResearchCapability.CANDLE_STRUCTURE:
                return 22
            case MarketResearchCapability.HIGHER_TIMEFRAME:
                return 600
            case MarketResearchCapability.LOWER_BAND_CROSSINGS:
                return 720
            case _:
                return 25


class MarketResearchQuery(_Contract):
    capability: MarketResearchCapability
    pair: TradingPair
    benchmark: TradingPair | None = None
    venue: Venue = Venue.COINBASE
    timeframe: Timeframe = Timeframe.ONE_HOUR

    @model_validator(mode="after")
    def validate_benchmark(self) -> Self:
        relative = self.capability is MarketResearchCapability.RELATIVE_STRENGTH
        if relative != (self.benchmark is not None):
            raise ValueError("only relative_strength requires an explicit benchmark")
        if self.benchmark == self.pair:
            raise ValueError("self-comparison is not permitted")
        return self

    @property
    def markets(self) -> tuple[tuple[MarketRole, TradingPair], ...]:
        subject = ((MarketRole.SUBJECT, self.pair),)
        if self.benchmark is None:
            return subject
        return subject + ((MarketRole.BENCHMARK, self.benchmark),)


class MarketSourceBinding(_Contract):
    """Runtime provenance bound to the canonical dataset feeding the gateway."""

    venue: Venue = Venue.COINBASE
    pair: TradingPair
    timeframe: Timeframe = Timeframe.ONE_HOUR
    source: Literal["coinbase_exchange_candles"] = "coinbase_exchange_candles"
    source_schema_version: Literal["1"] = "1"
    dataset_version: Digest
    acquisition_id: Digest | None = None


class MarketSourceReference(MarketSourceBinding):
    role: MarketRole


class ResearchWindow(_Contract):
    """Exact half-open candle coverage [start, end), including the bar ending T."""

    start: HourlyDatetime
    end: HourlyDatetime
    bar_count: Annotated[int, Field(strict=True, gt=0)]
    timeframe: Timeframe = Timeframe.ONE_HOUR

    @model_validator(mode="after")
    def validate_geometry(self) -> Self:
        if self.end - self.start != self.bar_count * self.timeframe.duration:
            raise ValueError("window geometry does not match bar_count")
        return self


class ResearchInput(_Contract):
    source: MarketSourceReference
    window_digest: Digest


def _validate_window(
    query: MarketResearchQuery, policy: MarketResearchPolicy, window: ResearchWindow
) -> None:
    if window.bar_count != policy.required_bars(query.capability):
        raise ValueError("window does not match fixed capability policy")


class ResearchContext(_Contract):
    query: MarketResearchQuery
    policy: MarketResearchPolicy = MarketResearchPolicy()
    window: ResearchWindow
    inputs: tuple[ResearchInput, ...]

    @field_validator("inputs")
    @classmethod
    def order_roles(
        cls, values: tuple[ResearchInput, ...]
    ) -> tuple[ResearchInput, ...]:
        return tuple(
            sorted(values, key=lambda item: item.source.role is MarketRole.BENCHMARK)
        )

    @model_validator(mode="after")
    def validate_inputs(self) -> Self:
        _validate_window(self.query, self.policy, self.window)
        if (
            tuple((i.source.role, i.source.pair) for i in self.inputs)
            != self.query.markets
        ):
            raise ValueError("source roles/markets must match the query exactly")
        return self


class CandleSpan(_Contract):
    """Consecutive hourly candles a measurement aggregates, named by close time."""

    first_close_time: HourlyDatetime
    last_close_time: HourlyDatetime
    bar_count: Annotated[int, Field(strict=True, gt=0)]

    @model_validator(mode="after")
    def validate_geometry(self) -> Self:
        if self.last_close_time - self.first_close_time != (self.bar_count - 1) * _HOUR:
            raise ValueError("candle span geometry does not match bar_count")
        return self

    def covers(self, close_time: datetime) -> bool:
        return self.first_close_time <= close_time <= self.last_close_time


class UndefinedReason(StrEnum):
    ZERO_PREVIOUS_NORMALIZED_RANGE = "ZERO_PREVIOUS_NORMALIZED_RANGE"
    ZERO_BAND_WIDTH = "ZERO_BAND_WIDTH"
    ZERO_BASELINE_VOLUME = "ZERO_BASELINE_VOLUME"
    ZERO_RANGE = "ZERO_RANGE"
    ZERO_MEAN_TRUE_RANGE = "ZERO_MEAN_TRUE_RANGE"
    ZERO_SAMPLE = "ZERO_SAMPLE"


class ResearchRatio(_Contract):
    value: ExactDecimal | None
    undefined_reason: UndefinedReason | None = None

    @model_validator(mode="after")
    def validate_definition(self) -> Self:
        if (self.value is None) != (self.undefined_reason is not None):
            raise ValueError("ratio requires either a value or an undefined reason")
        return self


def _ratio(
    numerator: Decimal, denominator: Decimal, reason: UndefinedReason
) -> ResearchRatio:
    if denominator == 0:
        return ResearchRatio(value=None, undefined_reason=reason)
    return ResearchRatio(value=_divide(numerator, denominator))


class TrendPayload(_Contract):
    capability: Literal[MarketResearchCapability.TREND] = MarketResearchCapability.TREND
    close: PositiveDecimal
    sma20: PositiveDecimal
    sma50: PositiveDecimal
    sma20_candles: CandleSpan
    sma50_candles: CandleSpan
    close_vs_sma20: ExactDecimal
    close_vs_sma50: ExactDecimal
    sma20_vs_sma50: ExactDecimal

    @model_validator(mode="after")
    def validate_arithmetic(self) -> Self:
        _validate_bar_counts(self)
        if (self.close_vs_sma20, self.close_vs_sma50, self.sma20_vs_sma50) != (
            _return(self.close, self.sma20),
            _return(self.close, self.sma50),
            _return(self.sma20, self.sma50),
        ):
            raise ValueError("trend ratios do not reconcile")
        return self


class MomentumPayload(_Contract):
    capability: Literal[MarketResearchCapability.MOMENTUM] = (
        MarketResearchCapability.MOMENTUM
    )
    return_1h: ExactDecimal
    return_6h: ExactDecimal
    return_24h: ExactDecimal
    previous_return_6h: ExactDecimal
    return_change_6h: ExactDecimal

    @model_validator(mode="after")
    def validate_arithmetic(self) -> Self:
        if self.return_change_6h != _difference(
            self.return_6h, self.previous_return_6h
        ):
            raise ValueError("momentum return change does not reconcile")
        return self


class VolatilityPayload(_Contract):
    capability: Literal[MarketResearchCapability.VOLATILITY] = (
        MarketResearchCapability.VOLATILITY
    )
    current_candles: CandleSpan
    previous_candles: CandleSpan
    current_mean_true_range: NonNegativeDecimal
    previous_mean_true_range: NonNegativeDecimal
    current_ending_close: PositiveDecimal
    previous_ending_close: PositiveDecimal
    current_normalized_range: NonNegativeDecimal
    previous_normalized_range: NonNegativeDecimal
    normalized_range_ratio: ResearchRatio

    @model_validator(mode="after")
    def validate_arithmetic(self) -> Self:
        _validate_bar_counts(self)
        if (
            self.previous_candles.last_close_time
            != self.current_candles.first_close_time - _HOUR
        ):
            raise ValueError("volatility spans must be consecutive")
        if (self.current_normalized_range, self.previous_normalized_range) != (
            _divide(self.current_mean_true_range, self.current_ending_close),
            _divide(self.previous_mean_true_range, self.previous_ending_close),
        ) or self.normalized_range_ratio != _ratio(
            self.current_normalized_range,
            self.previous_normalized_range,
            UndefinedReason.ZERO_PREVIOUS_NORMALIZED_RANGE,
        ):
            raise ValueError("volatility ratios do not reconcile")
        return self


class BollingerPositionPayload(_Contract):
    capability: Literal[MarketResearchCapability.BOLLINGER_POSITION] = (
        MarketResearchCapability.BOLLINGER_POSITION
    )
    close: PositiveDecimal
    sma20: PositiveDecimal
    population_std: NonNegativeDecimal
    lower_band: ExactDecimal
    upper_band: ExactDecimal
    percent_b: ResearchRatio
    bandwidth_fraction: NonNegativeDecimal
    band_candles: CandleSpan

    @model_validator(mode="after")
    def validate_arithmetic(self) -> Self:
        _validate_bar_counts(self)
        lower, upper = _bands(self.sma20, self.population_std)
        width = _difference(upper, lower)
        if (self.lower_band, self.upper_band) != (lower, upper) or (
            self.percent_b
            != _ratio(
                _difference(self.close, lower), width, UndefinedReason.ZERO_BAND_WIDTH
            )
            or self.bandwidth_fraction != _divide(width, self.sma20)
        ):
            raise ValueError("Bollinger statistics do not reconcile")
        return self


class LevelsPayload(_Contract):
    """Signed distances from recent levels: current close / recent level - 1."""

    capability: Literal[MarketResearchCapability.LEVELS] = (
        MarketResearchCapability.LEVELS
    )
    close: PositiveDecimal
    recent_high: PositiveDecimal
    recent_low: PositiveDecimal
    recent_high_close_time: HourlyDatetime
    recent_low_close_time: HourlyDatetime
    recent_high_distance: ExactDecimal
    recent_low_distance: ExactDecimal
    level_candles: CandleSpan

    @model_validator(mode="after")
    def validate_arithmetic(self) -> Self:
        _validate_bar_counts(self)
        if not (
            self.level_candles.covers(self.recent_high_close_time)
            and self.level_candles.covers(self.recent_low_close_time)
        ):
            raise ValueError("recent levels must belong to prior candles")
        if self.recent_low > self.recent_high or (
            self.recent_high_distance,
            self.recent_low_distance,
        ) != (
            _return(self.close, self.recent_high),
            _return(self.close, self.recent_low),
        ):
            raise ValueError("recent levels do not reconcile")
        return self


class VolumeContextPayload(_Contract):
    capability: Literal[MarketResearchCapability.VOLUME_CONTEXT] = (
        MarketResearchCapability.VOLUME_CONTEXT
    )
    current_base_volume: NonNegativeDecimal
    previous_mean_base_volume: NonNegativeDecimal
    volume_ratio: ResearchRatio
    baseline_candles: CandleSpan

    @model_validator(mode="after")
    def validate_arithmetic(self) -> Self:
        _validate_bar_counts(self)
        if self.volume_ratio != _ratio(
            self.current_base_volume,
            self.previous_mean_base_volume,
            UndefinedReason.ZERO_BASELINE_VOLUME,
        ):
            raise ValueError("volume ratio does not reconcile")
        return self


class CandleStructurePayload(_Contract):
    """Anatomy of the candle ending at T; fractions are of its own high-low range."""

    capability: Literal[MarketResearchCapability.CANDLE_STRUCTURE] = (
        MarketResearchCapability.CANDLE_STRUCTURE
    )
    open: PositiveDecimal
    high: PositiveDecimal
    low: PositiveDecimal
    close: PositiveDecimal
    range: NonNegativeDecimal
    body: ExactDecimal
    upper_wick: NonNegativeDecimal
    lower_wick: NonNegativeDecimal
    body_fraction: ResearchRatio
    upper_wick_fraction: ResearchRatio
    lower_wick_fraction: ResearchRatio
    close_location: ResearchRatio
    baseline_mean_true_range: NonNegativeDecimal
    range_vs_baseline_mean_true_range: ResearchRatio
    baseline_candles: CandleSpan

    @model_validator(mode="after")
    def validate_arithmetic(self) -> Self:
        _validate_bar_counts(self)
        if self.low > min(self.open, self.close) or self.high < max(
            self.open, self.close
        ):
            raise ValueError("candle prices are inconsistent")
        derived = _candle_anatomy(
            self.open, self.high, self.low, self.close, self.baseline_mean_true_range
        )
        if any(getattr(self, name) != value for name, value in derived.items()):
            raise ValueError("candle structure does not reconcile")
        return self


def _candle_anatomy(
    open: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    baseline_mean_true_range: Decimal,
) -> dict[str, Any]:
    """Derived candle fields, shared by calculation and recomputing validation."""
    span = _difference(high, low)
    body = _difference(close, open)
    upper_wick = _difference(high, max(open, close))
    lower_wick = _difference(min(open, close), low)
    reason = UndefinedReason.ZERO_RANGE
    return dict(
        range=span,
        body=body,
        upper_wick=upper_wick,
        lower_wick=lower_wick,
        body_fraction=_ratio(body.copy_abs(), span, reason),
        upper_wick_fraction=_ratio(upper_wick, span, reason),
        lower_wick_fraction=_ratio(lower_wick, span, reason),
        close_location=_ratio(_difference(close, low), span, reason),
        baseline_mean_true_range=baseline_mean_true_range,
        range_vs_baseline_mean_true_range=_ratio(
            span, baseline_mean_true_range, UndefinedReason.ZERO_MEAN_TRUE_RANGE
        ),
    )


class _BlockSection(_Contract):
    """Block 0 is the newest block of consecutive hourly candles ending at T."""

    block_hours: int
    close: PositiveDecimal
    sma20: PositiveDecimal
    previous_sma20: PositiveDecimal
    sma20_candles: CandleSpan
    previous_sma20_candles: CandleSpan
    close_vs_sma20: ExactDecimal
    sma20_change: ExactDecimal
    close_5_blocks_ago: PositiveDecimal
    close_5_blocks_ago_close_time: HourlyDatetime
    return_5_blocks: ExactDecimal
    range_high: PositiveDecimal
    range_low: PositiveDecimal
    range_high_close_time: HourlyDatetime
    range_low_close_time: HourlyDatetime
    range_candles: CandleSpan
    range_high_distance: ExactDecimal
    range_low_distance: ExactDecimal

    @model_validator(mode="after")
    def validate_arithmetic(self) -> Self:
        _validate_bar_counts(self)
        block = self.block_hours * _HOUR
        end = self.sma20_candles.last_close_time
        if (
            self.previous_sma20_candles.last_close_time != end - 5 * block
            or self.range_candles.last_close_time != end - block
            or self.close_5_blocks_ago_close_time
            != self.previous_sma20_candles.last_close_time
        ):
            raise ValueError("higher-timeframe spans do not match block offsets")
        if not (
            self.range_candles.covers(self.range_high_close_time)
            and self.range_candles.covers(self.range_low_close_time)
        ):
            raise ValueError("higher-timeframe range times must lie in range_candles")
        if self.range_low > self.range_high or (
            self.close_vs_sma20,
            self.sma20_change,
            self.return_5_blocks,
            self.range_high_distance,
            self.range_low_distance,
        ) != (
            _return(self.close, self.sma20),
            _return(self.sma20, self.previous_sma20),
            _return(self.close, self.close_5_blocks_ago),
            _return(self.close, self.range_high),
            _return(self.close, self.range_low),
        ):
            raise ValueError("higher-timeframe ratios do not reconcile")
        return self


class FourHourSection(_BlockSection):
    block_hours: Literal[4] = 4


class DailySection(_BlockSection):
    block_hours: Literal[24] = 24


class HigherTimeframePayload(_Contract):
    """Blocks of consecutive hourly candles ending at T, not calendar-aligned."""

    capability: Literal[MarketResearchCapability.HIGHER_TIMEFRAME] = (
        MarketResearchCapability.HIGHER_TIMEFRAME
    )
    four_hour: FourHourSection
    daily: DailySection

    @model_validator(mode="after")
    def validate_sections(self) -> Self:
        if self.four_hour.close != self.daily.close:
            raise ValueError("higher-timeframe sections must share the current close")
        if (
            self.four_hour.sma20_candles.last_close_time
            != self.daily.sma20_candles.last_close_time
        ):
            raise ValueError("higher-timeframe sections must end at the same candle")
        return self


Count = Annotated[int, Field(strict=True, ge=0)]


class LowerBandCrossingsPayload(_Contract):
    """Scanner-rule crossings in the window; samples have all outcomes closed by T."""

    capability: Literal[MarketResearchCapability.LOWER_BAND_CROSSINGS] = (
        MarketResearchCapability.LOWER_BAND_CROSSINGS
    )
    lookback_bars: Literal[720] = 720
    horizon_bars: Literal[6] = 6
    lookback_candles: CandleSpan
    sample_count: Count
    pending_count: Count
    pending_close_times: tuple[HourlyDatetime, ...]
    current_candle_crossed: Annotated[bool, Field(strict=True)]
    reached_sma20_count: Count
    positive_close_count: Count
    reached_sma20_fraction: ResearchRatio
    positive_close_fraction: ResearchRatio
    mean_close_return: ExactDecimal | None
    median_close_return: ExactDecimal | None
    min_close_return: ExactDecimal | None
    max_close_return: ExactDecimal | None
    mean_adverse_excursion: ExactDecimal | None
    mean_favorable_excursion: ExactDecimal | None

    @model_validator(mode="after")
    def validate_pending(self) -> Self:
        _validate_bar_counts(self)
        last = self.lookback_candles.last_close_time
        times = self.pending_close_times
        # Scanner-rule crossings are never one hour apart: crossing at i + 1
        # requires the close at i to be at or above its lower band.
        if (
            len(times) != self.pending_count
            or any(later - earlier <= _HOUR for earlier, later in pairwise(times))
            or any(
                not last - (self.horizon_bars - 1) * _HOUR <= time <= last
                for time in times
            )
            or self.current_candle_crossed != (last in times)
        ):
            raise ValueError("pending crossing times do not reconcile")
        return self

    @model_validator(mode="after")
    def validate_aggregates(self) -> Self:
        samples = Decimal(self.sample_count)
        if (
            max(self.reached_sma20_count, self.positive_close_count) > self.sample_count
            or self.reached_sma20_fraction
            != _ratio(
                Decimal(self.reached_sma20_count),
                samples,
                UndefinedReason.ZERO_SAMPLE,
            )
            or self.positive_close_fraction
            != _ratio(
                Decimal(self.positive_close_count),
                samples,
                UndefinedReason.ZERO_SAMPLE,
            )
        ):
            raise ValueError("crossing counts do not reconcile")
        aggregates = (
            self.mean_close_return,
            self.median_close_return,
            self.min_close_return,
            self.max_close_return,
            self.mean_adverse_excursion,
            self.mean_favorable_excursion,
        )
        if any((value is None) != (self.sample_count == 0) for value in aggregates):
            raise ValueError("crossing aggregates must exist exactly with samples")
        mean, median, low, high = aggregates[:4]
        if (
            mean is not None
            and median is not None
            and low is not None
            and high is not None
            and not (low <= median <= high and low <= mean <= high)
        ):
            raise ValueError("crossing close-return statistics are inconsistent")
        return self


class RelativeStrengthPayload(_Contract):
    capability: Literal[MarketResearchCapability.RELATIVE_STRENGTH] = (
        MarketResearchCapability.RELATIVE_STRENGTH
    )
    subject_pair: TradingPair
    benchmark_pair: TradingPair
    subject_return_6h: ExactDecimal
    subject_return_24h: ExactDecimal
    benchmark_return_6h: ExactDecimal
    benchmark_return_24h: ExactDecimal
    spread_6h: ExactDecimal
    spread_24h: ExactDecimal

    @model_validator(mode="after")
    def validate_arithmetic(self) -> Self:
        if self.subject_pair == self.benchmark_pair:
            raise ValueError("self-comparison is not permitted")
        if (self.spread_6h, self.spread_24h) != (
            _difference(self.subject_return_6h, self.benchmark_return_6h),
            _difference(self.subject_return_24h, self.benchmark_return_24h),
        ):
            raise ValueError("relative return spreads do not reconcile")
        return self


MarketResearchPayload = Annotated[
    TrendPayload
    | MomentumPayload
    | VolatilityPayload
    | BollingerPositionPayload
    | LevelsPayload
    | VolumeContextPayload
    | CandleStructurePayload
    | HigherTimeframePayload
    | LowerBandCrossingsPayload
    | RelativeStrengthPayload,
    Field(discriminator="capability"),
]


def _span_geometry(
    payload: BaseModel,
) -> tuple[tuple[CandleSpan, int, int], ...]:
    """Each span with its fixed bar count and its last close's hours before T."""
    match payload:
        case TrendPayload():
            return ((payload.sma20_candles, 20, 0), (payload.sma50_candles, 50, 0))
        case VolatilityPayload():
            return (
                (payload.current_candles, 20, 0),
                (payload.previous_candles, 20, 20),
            )
        case BollingerPositionPayload():
            return ((payload.band_candles, 20, 0),)
        case LevelsPayload():
            return ((payload.level_candles, 24, 1),)
        case VolumeContextPayload():
            return ((payload.baseline_candles, 24, 1),)
        case CandleStructurePayload():
            return ((payload.baseline_candles, 20, 1),)
        case _BlockSection():
            b = payload.block_hours
            return (
                (payload.sma20_candles, 20 * b, 0),
                (payload.previous_sma20_candles, 20 * b, 5 * b),
                (payload.range_candles, 20 * b, b),
            )
        case HigherTimeframePayload():
            return _span_geometry(payload.four_hour) + _span_geometry(payload.daily)
        case LowerBandCrossingsPayload():
            return ((payload.lookback_candles, payload.lookback_bars, 0),)
    return ()


def _validate_bar_counts(payload: BaseModel) -> None:
    if any(span.bar_count != bars for span, bars, _ in _span_geometry(payload)):
        raise ValueError("candle span does not match its fixed bar count")


def _datetimes(value: Any) -> Iterator[datetime]:
    if isinstance(value, datetime):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _datetimes(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _datetimes(item)


def _canonical(value: Any) -> Any:
    """Exact semantic encoding, also used to embed typed records in EvidenceItem."""
    if isinstance(value, BaseModel):
        return _canonical(value.model_dump(mode="python"))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("canonical decimals must be finite")
        fixed = format(value, "f")
        return (
            "0"
            if value == 0
            else fixed.rstrip("0").rstrip(".")
            if "." in fixed
            else fixed
        )
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, Mapping):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical(item) for item in value]
    if isinstance(value, float):
        raise ValueError("research canonical content must not contain binary floats")
    return value


def _digest(value: Any) -> str:
    encoded = json.dumps(
        _canonical(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return "sha256:" + sha256(encoded.encode("utf-8")).hexdigest()


def _evidence_identity(context: ResearchContext, payload: MarketResearchPayload) -> str:
    return _digest(
        {
            "identity_encoding_version": "1",
            "policy": context.policy,
            "capability": context.query.capability,
            "observed_at": context.window.end,
            "window": context.window,
            "inputs": [
                {
                    "role": item.source.role,
                    "venue": item.source.venue,
                    "pair": item.source.pair,
                    "timeframe": item.source.timeframe,
                    "window_digest": item.window_digest,
                }
                for item in context.inputs
            ],
            "payload": payload,
        }
    )


class ResearchToolSuccess(_Contract):
    status: Literal["success"] = "success"
    context: ResearchContext
    payload: MarketResearchPayload
    evidence: EvidenceItem

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        context, evidence = self.context, self.evidence
        if self.payload.capability != context.query.capability:
            raise ValueError("payload capability does not match query")
        # Payload validators fix span sizes and relations; anchoring each span to T
        # also keeps level and range extremes and pending crossings inside the window.
        # The final bound also covers any payload time that no span constrains.
        start, end = context.window.start, context.window.end
        if any(
            span.last_close_time != end - offset * _HOUR
            or span.first_close_time <= start
            for span, _, offset in _span_geometry(self.payload)
        ):
            raise ValueError("payload candle spans are not anchored to T")
        if any(time > end for time in _datetimes(self.payload.model_dump())):
            raise ValueError("payload times must not exceed T")
        if isinstance(self.payload, RelativeStrengthPayload) and (
            self.payload.subject_pair,
            self.payload.benchmark_pair,
        ) != (context.query.pair, context.query.benchmark):
            raise ValueError("relative strength pairs do not match the query")
        if (
            evidence.category is not EvidenceCategory.MARKET
            or evidence.evidence_type != context.query.capability.value
            or evidence.source != "coinbase_exchange_candles"
            or evidence.observed_at != context.window.end
            or evidence.available_at != context.window.end
            or evidence.quality is not None
            or evidence.provenance.producer
            != f"{context.policy.policy_id}.{context.query.capability.value}"
            or evidence.provenance.source_version != "1"
            or _canonical(evidence.provenance.query_params) != _canonical(context)
            or _canonical(evidence.payload) != _canonical(self.payload)
            or evidence.evidence_id != _evidence_identity(context, self.payload)
        ):
            raise ValueError("evidence envelope does not reconcile with typed research")
        return self


class ResearchUnavailableReason(StrEnum):
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
    REFERENCE_UNAVAILABLE = "REFERENCE_UNAVAILABLE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    HISTORY_GAP = "HISTORY_GAP"


class ResearchToolUnavailable(_Contract):
    status: Literal["unavailable"] = "unavailable"
    query: MarketResearchQuery
    policy: MarketResearchPolicy = MarketResearchPolicy()
    window: ResearchWindow
    role: MarketRole
    reason: ResearchUnavailableReason

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        _validate_window(self.query, self.policy, self.window)
        if self.role not in tuple(role for role, _ in self.query.markets):
            raise ValueError("unavailable role is not in query")
        return self
