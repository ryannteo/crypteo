"""Serialization, nested revalidation and immutable research result contracts."""

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from crypteo.domain.market import Candle, TradingPair
from crypteo.domain.market_research import (
    MarketResearchCapability,
    MarketResearchPolicy,
    MarketResearchQuery,
    MarketRole,
    MarketSourceBinding,
    ResearchContext,
    ResearchInput,
    ResearchRatio,
    ResearchToolSuccess,
    ResearchToolUnavailable,
    ResearchWindow,
    UndefinedReason,
    _canonical,
    _evidence_identity,
)
from crypteo.domain.research import EvidenceItem, EvidenceQuality
from crypteo.market_research import MarketResearchService
from crypteo.temporal import PointInTimeDataGateway, SimulationClock

T = datetime(2022, 1, 4, tzinfo=UTC)
BTC, ETH, _ = TradingPair
CAPABILITIES = tuple(MarketResearchCapability)


def result(
    capability: MarketResearchCapability = MarketResearchCapability.TREND,
) -> ResearchToolSuccess:
    candles = tuple(
        Candle.model_validate(
            dict(
                venue="COINBASE",
                pair=pair,
                timeframe="1h",
                open_time=T - timedelta(hours=60 - i),
                close_time=T - timedelta(hours=59 - i),
                available_at=T - timedelta(hours=59 - i),
                open="100",
                high="102",
                low="98",
                close="100",
                volume="1",
            )
        )
        for pair in (BTC, ETH)
        for i in range(60)
    )
    svc = MarketResearchService(
        PointInTimeDataGateway(SimulationClock(T), candles),
        [
            MarketSourceBinding(pair=p, dataset_version="sha256:" + "a" * 64)
            for p in (BTC, ETH)
        ],
    )
    outcome = (
        svc.relative_strength(BTC, ETH)
        if capability is MarketResearchCapability.RELATIVE_STRENGTH
        else getattr(svc, capability.value)(BTC)
    )
    assert isinstance(outcome, ResearchToolSuccess)
    return outcome


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_success_round_trip_and_schema(capability: MarketResearchCapability) -> None:
    original = result(capability)
    assert (
        ResearchToolSuccess.model_validate_json(original.model_dump_json()) == original
    )
    assert ResearchToolSuccess.model_validate(original) == original
    assert (
        EvidenceItem.model_validate_json(original.evidence.model_dump_json())
        == original.evidence
    )
    assert ResearchToolSuccess.model_json_schema()["additionalProperties"] is False
    adapter: TypeAdapter[ResearchToolSuccess | ResearchToolUnavailable] = TypeAdapter(
        ResearchToolSuccess | ResearchToolUnavailable
    )
    assert adapter.validate_json(original.model_dump_json()) == original


@pytest.mark.parametrize(
    "reason",
    [
        "SOURCE_UNAVAILABLE",
        "REFERENCE_UNAVAILABLE",
        "INSUFFICIENT_HISTORY",
        "HISTORY_GAP",
    ],
)
def test_unavailable_round_trip_and_role_validation(reason: str) -> None:
    context = result().context
    unavailable = ResearchToolUnavailable.model_validate(
        dict(query=context.query, window=context.window, role="subject", reason=reason)
    )
    assert (
        ResearchToolUnavailable.model_validate_json(unavailable.model_dump_json())
        == unavailable
    )
    with pytest.raises(ValidationError):
        ResearchToolUnavailable.model_validate(
            unavailable.model_copy(update={"role": MarketRole.BENCHMARK})
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"benchmark": BTC},
        {"benchmark": ETH},
        {"window": 20},
        {"cutoff": T},
        {"observed_at": T},
        {"pair": "DOGE-USD"},
        {"capability": "news"},
        {"timeframe": "5m"},
        {"venue": "BINANCE"},
    ],
)
def test_query_rejects_unsupported_and_unbounded_requests(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        MarketResearchQuery.model_validate(
            dict(capability="trend", pair=BTC) | overrides
        )


def test_relative_query_requires_benchmark() -> None:
    with pytest.raises(ValidationError):
        MarketResearchQuery(
            capability=MarketResearchCapability.RELATIVE_STRENGTH, pair=BTC
        )


@pytest.mark.parametrize(
    "override", [{"policy_id": "v2"}, {"window": 30}, {"lookback": 30}]
)
def test_policy_is_fixed(override: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        MarketResearchPolicy.model_validate(override)


@pytest.mark.parametrize("field", ["start", "end"])
@pytest.mark.parametrize(
    "value",
    [
        T.replace(tzinfo=None),
        T.astimezone(timezone(timedelta(hours=8))),
        T + timedelta(minutes=1),
        "2022-01-04T00:00:00Z",
        1641254400,
    ],
)
def test_window_timestamps_are_strict_hourly_utc(field: str, value: Any) -> None:
    window = result().context.window
    with pytest.raises(ValidationError):
        ResearchWindow.model_validate(window.model_copy(update={field: value}))


@pytest.mark.parametrize("count", [49, 0, True, "50"])
def test_window_geometry_and_count_are_validated(count: Any) -> None:
    with pytest.raises(ValidationError):
        ResearchWindow.model_validate(
            result().context.window.model_copy(update={"bar_count": count})
        )


def test_context_enforces_fixed_window_even_when_geometry_is_consistent() -> None:
    original = result().context
    wrong = ResearchWindow(start=T - timedelta(hours=25), end=T, bar_count=25)
    with pytest.raises(ValidationError, match="fixed capability"):
        ResearchContext.model_validate(original.model_copy(update={"window": wrong}))


@pytest.mark.parametrize(
    "case",
    [
        "policy",
        "query",
        "window",
        "source",
        "input",
        "payload",
        "provenance",
        "evidence",
    ],
)
@pytest.mark.parametrize("mode", ["instance", "dict", "json"])
def test_forged_nested_models_are_revalidated(case: str, mode: str) -> None:
    original = result()
    context = original.context
    if case == "policy":
        context = context.model_copy(
            update={"policy": context.policy.model_copy(update={"policy_id": "forged"})}
        )
    elif case == "query":
        context = context.model_copy(
            update={"query": context.query.model_copy(update={"benchmark": ETH})}
        )
    elif case == "window":
        context = context.model_copy(
            update={"window": context.window.model_copy(update={"bar_count": 1})}
        )
    elif case == "source":
        source = context.inputs[0].source.model_copy(
            update={"source_schema_version": "2"}
        )
        context = context.model_copy(
            update={
                "inputs": (context.inputs[0].model_copy(update={"source": source}),)
            }
        )
    elif case == "input":
        context = context.model_copy(
            update={
                "inputs": (
                    context.inputs[0].model_copy(update={"window_digest": "fake"}),
                )
            }
        )
    elif case == "payload":
        original = original.model_copy(
            update={
                "payload": original.payload.model_copy(update={"sma20": Decimal(-1)})
            }
        )
    elif case == "provenance":
        provenance = original.evidence.provenance.model_copy(update={"producer": ""})
        original = original.model_copy(
            update={
                "evidence": original.evidence.model_copy(
                    update={"provenance": provenance}
                )
            }
        )
    else:
        original = original.model_copy(
            update={
                "evidence": original.evidence.model_copy(
                    update={"available_at": T - timedelta(hours=1)}
                )
            }
        )
    forged = original.model_copy(update={"context": context})
    with pytest.raises(ValidationError):
        if mode == "json":
            ResearchToolSuccess.model_validate_json(forged.model_dump_json())
        elif mode == "dict":
            # Retain unchecked nested instances, not only a fully dumped tree.
            ResearchToolSuccess.model_validate(
                {
                    "context": forged.context,
                    "payload": forged.payload,
                    "evidence": forged.evidence,
                }
            )
        else:
            ResearchToolSuccess.model_validate(forged)


@pytest.mark.parametrize(
    "capability,field",
    [
        (MarketResearchCapability.TREND, "close_vs_sma20"),
        (MarketResearchCapability.MOMENTUM, "return_change_6h"),
        (MarketResearchCapability.VOLATILITY, "current_normalized_range"),
        (MarketResearchCapability.BOLLINGER_POSITION, "upper_band"),
        (MarketResearchCapability.LEVELS, "recent_high_distance"),
        (MarketResearchCapability.RELATIVE_STRENGTH, "spread_6h"),
    ],
)
def test_payload_arithmetic_invariants_reject_forgery(
    capability: MarketResearchCapability, field: str
) -> None:
    original = result(capability).payload
    with pytest.raises(ValidationError):
        type(original).model_validate(
            original.model_copy(update={field: Decimal("999")})
        )


@pytest.mark.parametrize(
    "capability,field",
    [
        (MarketResearchCapability.VOLATILITY, "normalized_range_ratio"),
        (MarketResearchCapability.BOLLINGER_POSITION, "percent_b"),
        (MarketResearchCapability.VOLUME_CONTEXT, "volume_ratio"),
    ],
)
def test_nested_ratio_and_reason_revalidation(
    capability: MarketResearchCapability, field: str
) -> None:
    payload = result(capability).payload
    with pytest.raises(ValidationError):
        type(payload).model_validate(
            payload.model_copy(
                update={
                    field: ResearchRatio.model_construct(
                        value=None, undefined_reason=None
                    )
                }
            )
        )
    with pytest.raises(ValidationError):
        type(payload).model_validate(
            payload.model_copy(
                update={
                    field: ResearchRatio(
                        value=None,
                        undefined_reason=UndefinedReason.ZERO_BASELINE_VOLUME,
                    )
                }
            )
        )


@pytest.mark.parametrize(
    "values",
    [
        {"value": None},
        {"value": "0", "undefined_reason": "ZERO_BAND_WIDTH"},
        {"value": 0.1},
        {"value": "NaN"},
        {"value": "Infinity"},
    ],
)
def test_ratio_rejects_invalid_and_float_values(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ResearchRatio.model_validate(values)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source", "wrong"),
        ("evidence_type", "momentum"),
        ("quality", EvidenceQuality.HIGH),
        ("evidence_id", "sha256:" + "f" * 64),
        ("payload", {"capability": "trend"}),
        ("available_at", T + timedelta(hours=1)),
        ("observed_at", T - timedelta(hours=1)),
    ],
)
def test_envelope_linkage_validation(field: str, value: Any) -> None:
    original = result()
    forged = original.evidence.model_copy(update={field: value})
    with pytest.raises(ValidationError):
        ResearchToolSuccess.model_validate(
            original.model_copy(update={"evidence": forged})
        )


@pytest.mark.parametrize("case", ["missing", "duplicate", "pair", "role"])
def test_context_source_roles_are_complete_and_unique(case: str) -> None:
    original = result(MarketResearchCapability.RELATIVE_STRENGTH).context
    first, second = original.inputs
    inputs: tuple[ResearchInput, ...]
    if case == "missing":
        inputs = (first,)
    elif case == "duplicate":
        inputs = (first, first)
    else:
        source = second.source.model_copy(
            update={"pair": BTC} if case == "pair" else {"role": MarketRole.SUBJECT}
        )
        inputs = (first, second.model_copy(update={"source": source}))
    with pytest.raises(ValidationError):
        ResearchContext.model_validate(original.model_copy(update={"inputs": inputs}))


def test_context_canonicalizes_role_order_before_identity() -> None:
    original = result(MarketResearchCapability.RELATIVE_STRENGTH)
    reordered = ResearchContext.model_validate(
        original.context.model_copy(
            update={"inputs": tuple(reversed(original.context.inputs))}
        )
    )
    assert reordered == original.context
    assert (
        _evidence_identity(reordered, original.payload) == original.evidence.evidence_id
    )
    assert [i["source"]["role"] for i in _canonical(reordered)["inputs"]] == [
        "subject",
        "benchmark",
    ]


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_contracts_are_frozen_and_forbid_interpretation_fields(
    capability: MarketResearchCapability,
) -> None:
    original = result(capability)
    for model in (
        original,
        original.payload,
        original.context,
        original.context.query,
        original.context.policy,
        original.context.window,
        original.context.inputs[0],
        original.context.inputs[0].source,
    ):
        field = next(iter(type(model).model_fields))
        with pytest.raises(ValidationError):
            setattr(model, field, getattr(model, field))
        for name in ("confidence", "weighting", "decision", "bullish"):
            with pytest.raises(ValidationError):
                type(model).model_validate(model.model_dump() | {name: "forbidden"})
    with pytest.raises(TypeError):
        original.context.inputs[0] = original.context.inputs[0]  # type: ignore[index]
    payload: Any = original.evidence.payload
    with pytest.raises(TypeError):
        payload["capability"] = "forged"


def test_prose_and_artifact_reference_do_not_affect_semantic_identity() -> None:
    original = result()
    envelope = original.evidence.model_dump()
    envelope["summary"] = "Other neutral description."
    envelope["relevance"] = "Another neutral description."
    envelope["provenance"]["artifact_ref"] = "artifact://optional"
    rebuilt = ResearchToolSuccess(
        context=original.context,
        payload=original.payload,
        evidence=EvidenceItem.model_validate(envelope),
    )
    assert rebuilt.evidence.evidence_id == original.evidence.evidence_id


def test_incorrect_query_params_and_payload_capability_are_rejected() -> None:
    original = result()
    serialized = json.loads(original.model_dump_json())
    serialized["evidence"]["provenance"]["query_params"]["inputs"][0]["source"][
        "dataset_version"
    ] = "sha256:" + "b" * 64
    with pytest.raises(ValidationError):
        ResearchToolSuccess.model_validate_json(json.dumps(serialized))
    with pytest.raises(ValidationError):
        ResearchToolSuccess(
            context=original.context,
            payload=result(MarketResearchCapability.MOMENTUM).payload,
            evidence=original.evidence,
        )


def test_level_times_must_exclude_current_candle() -> None:
    original = result(MarketResearchCapability.LEVELS)
    payload = original.payload.model_copy(update={"recent_high_close_time": T})
    with pytest.raises(ValidationError, match="prior candles"):
        ResearchToolSuccess(
            context=original.context, payload=payload, evidence=original.evidence
        )
