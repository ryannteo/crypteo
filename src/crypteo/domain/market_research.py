"""Immutable fixed V1 market evidence, independent of interpretation and replay."""

import json
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
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
    RELATIVE_STRENGTH = "relative_strength"


class MarketRole(StrEnum):
    SUBJECT = "subject"
    BENCHMARK = "benchmark"


class MarketResearchPolicy(_Contract):
    """One fixed policy; window lengths are not configurable query parameters."""

    policy_id: Literal["market_research_v1"] = "market_research_v1"

    def required_bars(self, capability: MarketResearchCapability) -> int:
        match capability:
            case MarketResearchCapability.TREND:
                return 50
            case MarketResearchCapability.VOLATILITY:
                return 41
            case MarketResearchCapability.BOLLINGER_POSITION:
                return 20
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


class UndefinedReason(StrEnum):
    ZERO_PREVIOUS_NORMALIZED_RANGE = "ZERO_PREVIOUS_NORMALIZED_RANGE"
    ZERO_BAND_WIDTH = "ZERO_BAND_WIDTH"
    ZERO_BASELINE_VOLUME = "ZERO_BASELINE_VOLUME"


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
    close_vs_sma20: ExactDecimal
    close_vs_sma50: ExactDecimal
    sma20_vs_sma50: ExactDecimal

    @model_validator(mode="after")
    def validate_arithmetic(self) -> Self:
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
    current_mean_true_range: NonNegativeDecimal
    previous_mean_true_range: NonNegativeDecimal
    current_ending_close: PositiveDecimal
    previous_ending_close: PositiveDecimal
    current_normalized_range: NonNegativeDecimal
    previous_normalized_range: NonNegativeDecimal
    normalized_range_ratio: ResearchRatio

    @model_validator(mode="after")
    def validate_arithmetic(self) -> Self:
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

    @model_validator(mode="after")
    def validate_arithmetic(self) -> Self:
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

    @model_validator(mode="after")
    def validate_arithmetic(self) -> Self:
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

    @model_validator(mode="after")
    def validate_arithmetic(self) -> Self:
        if self.volume_ratio != _ratio(
            self.current_base_volume,
            self.previous_mean_base_volume,
            UndefinedReason.ZERO_BASELINE_VOLUME,
        ):
            raise ValueError("volume ratio does not reconcile")
        return self


class RelativeStrengthPayload(_Contract):
    capability: Literal[MarketResearchCapability.RELATIVE_STRENGTH] = (
        MarketResearchCapability.RELATIVE_STRENGTH
    )
    subject_return_6h: ExactDecimal
    subject_return_24h: ExactDecimal
    benchmark_return_6h: ExactDecimal
    benchmark_return_24h: ExactDecimal
    spread_6h: ExactDecimal
    spread_24h: ExactDecimal

    @model_validator(mode="after")
    def validate_arithmetic(self) -> Self:
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
    | RelativeStrengthPayload,
    Field(discriminator="capability"),
]


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
        if isinstance(self.payload, LevelsPayload):
            for timestamp in (
                self.payload.recent_high_close_time,
                self.payload.recent_low_close_time,
            ):
                if not context.window.start < timestamp < context.window.end:
                    raise ValueError("recent levels must belong to prior candles")
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
