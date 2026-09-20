"""Strict research evidence, interpretation, and decision contracts."""

from collections.abc import Iterator, Mapping
from datetime import datetime
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Annotated, Any, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    GetCoreSchemaHandler,
    JsonValue,
    model_validator,
)
from pydantic_core import CoreSchema, core_schema

from crypteo.domain.market import Timeframe, TradingPair, Venue
from crypteo.domain.time import UtcDatetime


def _require_nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("text must be nonblank")
    return value


NonBlankText = Annotated[str, Field(strict=True), AfterValidator(_require_nonblank)]
EvidenceReferences = tuple[NonBlankText, ...]
TextEntries = tuple[NonBlankText, ...]


class FrozenJsonObject(Mapping[str, "FrozenJsonValue"]):
    """A deeply immutable JSON object with a read-only mapping interface."""

    __slots__ = ("_data",)
    _data: Mapping[str, "FrozenJsonValue"]

    def __init__(self, values: Mapping[str, "FrozenJsonValue"]) -> None:
        object.__setattr__(self, "_data", MappingProxyType(dict(values)))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __getitem__(self, key: str) -> "FrozenJsonValue":
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __deepcopy__(self, memo: dict[int, Any]) -> Self:
        return self


type JsonScalar = str | int | float | bool | None
type FrozenJsonValue = JsonScalar | FrozenJsonObject | tuple[FrozenJsonValue, ...]


def _freeze_json(value: JsonValue) -> FrozenJsonValue:
    if isinstance(value, dict):
        return FrozenJsonObject(
            {key: _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("JSON numbers must be finite")
    return value


def _serialize_frozen_json(value: FrozenJsonValue) -> JsonValue:
    if isinstance(value, FrozenJsonObject):
        return {key: _serialize_frozen_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_serialize_frozen_json(item) for item in value]
    return value


class _ImmutableJsonAnnotation:
    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(
            _freeze_json,
            handler.generate_schema(JsonValue),
            serialization=core_schema.plain_serializer_function_ser_schema(
                _serialize_frozen_json,
                return_schema=handler.generate_schema(JsonValue),
            ),
        )


class _ImmutableJsonObjectAnnotation:
    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return core_schema.no_info_after_validator_function(
            _freeze_json,
            handler.generate_schema(dict[str, JsonValue]),
            serialization=core_schema.plain_serializer_function_ser_schema(
                _serialize_frozen_json,
                return_schema=handler.generate_schema(JsonValue),
            ),
        )


ImmutableJsonValue = Annotated[
    FrozenJsonValue,
    _ImmutableJsonAnnotation,
]
ImmutableJsonObject = Annotated[
    FrozenJsonObject,
    _ImmutableJsonObjectAnnotation,
]


def _reject_duplicate_references(references: EvidenceReferences) -> EvidenceReferences:
    if len(references) != len(set(references)):
        raise ValueError("evidence references must not contain duplicates")
    return references


UniqueEvidenceReferences = Annotated[
    EvidenceReferences,
    AfterValidator(_reject_duplicate_references),
]


class Opportunity(BaseModel):
    """A deterministic research candidate, not a trade recommendation.

    Runtime or scanner logic supplies the identifier and timestamps. For a
    window-derived trigger, ``observed_at`` is the latest point required for the
    trigger to be valid, and ``available_at`` is when it could first be known.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    opportunity_id: NonBlankText
    venue: Venue
    pair: TradingPair
    timeframe: Timeframe
    observed_at: UtcDatetime
    available_at: UtcDatetime
    trigger_type: NonBlankText
    trigger_payload: ImmutableJsonObject
    summary: NonBlankText

    @property
    def identity_key(self) -> tuple[str, Venue, TradingPair, Timeframe, datetime]:
        """ADR-004 identity; trigger_type includes the scanner trigger version."""
        return (
            self.trigger_type,
            self.venue,
            self.pair,
            self.timeframe,
            self.observed_at,
        )

    @model_validator(mode="after")
    def validate_temporal_consistency(self) -> Self:
        if self.available_at < self.observed_at:
            raise ValueError("available_at must be at or after observed_at")
        return self


class EvidenceCategory(StrEnum):
    """Research domains represented by normalized evidence in V1."""

    MARKET = "market"


class EvidenceQuality(StrEnum):
    """Qualitative quality of a normalized evidence item."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class EvidenceProvenance(BaseModel):
    """Compact, auditable provenance for normalized evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    producer: NonBlankText
    source_version: NonBlankText
    query_params: ImmutableJsonObject
    artifact_ref: NonBlankText | None = None


class EvidenceItem(BaseModel):
    """Normalized evidence without an independent directional trading vote.

    For evidence derived from a completed window, ``observed_at`` is the latest
    required completed observation/window, not the start of its lookback.
    ``available_at`` is the earliest time Crypteo could legitimately know it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: NonBlankText
    category: EvidenceCategory
    evidence_type: NonBlankText
    source: NonBlankText
    observed_at: UtcDatetime
    available_at: UtcDatetime
    provenance: EvidenceProvenance
    summary: NonBlankText
    relevance: NonBlankText
    payload: ImmutableJsonValue | None = None
    quality: EvidenceQuality | None = None

    @model_validator(mode="after")
    def validate_temporal_consistency_and_grounding(self) -> Self:
        if self.available_at < self.observed_at:
            raise ValueError("available_at must be at or after observed_at")
        if self.payload is None and self.provenance.artifact_ref is None:
            raise ValueError("evidence requires a payload or artifact_ref")
        return self


class ResearchThesis(BaseModel):
    """Structured interpretation produced by the draft-thesis stage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: NonBlankText
    bull_case: NonBlankText
    bear_case: NonBlankText
    supporting_evidence_ids: UniqueEvidenceReferences = ()
    counterevidence_ids: UniqueEvidenceReferences = ()
    assumptions: TextEntries = ()
    invalidation_conditions: TextEntries = ()
    uncertainties: TextEntries = ()


class Critique(BaseModel):
    """Concise structured output from the research critic."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: NonBlankText
    challenged_claims: TextEntries = ()
    missing_evidence: TextEntries = ()
    contradictions: TextEntries = ()
    unsupported_assumptions: TextEntries = ()
    evidence_quality_concerns: TextEntries = ()
    unresolved_uncertainties: TextEntries = ()


class DecisionOutcome(StrEnum):
    """Possible research assessments, distinct from deterministic risk results.

    ``TRADE`` supports proceeding with a candidate long trade, but is not an
    order, execution authorization, risk approval, sizing, or portfolio approval.
    ``NO_TRADE`` actively rejects a candidate based on sufficient evidence.
    ``ABSTAIN`` means no sufficiently reliable research conclusion can be formed.
    """

    TRADE = "TRADE"
    NO_TRADE = "NO_TRADE"
    ABSTAIN = "ABSTAIN"


class AbstentionReason(StrEnum):
    """Closed set of reasons why V1 research cannot form a conclusion."""

    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    UNAVAILABLE_EVIDENCE = "UNAVAILABLE_EVIDENCE"
    UNRELIABLE_EVIDENCE = "UNRELIABLE_EVIDENCE"
    RESEARCH_FAILURE = "RESEARCH_FAILURE"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    VALIDATION_FAILURE = "VALIDATION_FAILURE"


Confidence = Annotated[int, Field(strict=True, ge=0, le=100)]


class TradeDecision(BaseModel):
    """Final research assessment, before deterministic risk authorization.

    ``confidence`` is Crypteo's self-assessed confidence in its research
    conclusion. It is not probability of profit, probability of a price rise,
    expected return, calibrated forecast probability, or risk-adjusted return.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: DecisionOutcome
    rationale: NonBlankText
    supporting_evidence_ids: UniqueEvidenceReferences = ()
    counterevidence_ids: UniqueEvidenceReferences = ()
    confidence: Confidence | None = None
    key_uncertainties: TextEntries = ()
    critic_response: NonBlankText | None = None
    abstention_reasons: tuple[AbstentionReason, ...] = ()

    @model_validator(mode="after")
    def validate_outcome_requirements(self) -> Self:
        if self.outcome is DecisionOutcome.TRADE:
            if not self.supporting_evidence_ids:
                raise ValueError("TRADE requires supporting evidence")
            if self.confidence is None:
                raise ValueError("TRADE requires confidence")
            if self.critic_response is None:
                raise ValueError("TRADE requires a critic response")
            if self.abstention_reasons:
                raise ValueError("TRADE must not include abstention reasons")
        elif self.outcome is DecisionOutcome.NO_TRADE:
            if not self.counterevidence_ids:
                raise ValueError("NO_TRADE requires counterevidence")
            if self.confidence is None:
                raise ValueError("NO_TRADE requires confidence")
            if self.critic_response is None:
                raise ValueError("NO_TRADE requires a critic response")
            if self.abstention_reasons:
                raise ValueError("NO_TRADE must not include abstention reasons")
        elif not self.abstention_reasons:
            raise ValueError("ABSTAIN requires at least one abstention reason")
        return self
