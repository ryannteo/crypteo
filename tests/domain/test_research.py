import copy
from datetime import UTC, datetime, timedelta, timezone
from math import inf, nan
from typing import Any

import pytest
from pydantic import ValidationError

from crypteo.domain.research import (
    AbstentionReason,
    Critique,
    DecisionOutcome,
    EvidenceCategory,
    EvidenceItem,
    EvidenceProvenance,
    EvidenceQuality,
    FrozenJsonObject,
    Opportunity,
    ResearchThesis,
    TradeDecision,
)

OBSERVED_AT = datetime(2026, 1, 1, 20, tzinfo=UTC)


def opportunity_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "opportunity_id": "opp-001",
        "venue": "COINBASE",
        "pair": "BTC-USD",
        "timeframe": "1h",
        "observed_at": OBSERVED_AT,
        "available_at": OBSERVED_AT,
        "trigger_type": "lower_band_cross_below",
        "trigger_payload": {
            "crossed": True,
            "window": 20,
            "bands": ["lower", None],
            "parameters": {"deviations": 2},
        },
        "summary": "Price crossed below the lower volatility band.",
    }
    data.update(overrides)
    return data


def provenance_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "producer": "market_context",
        "source_version": "coinbase-hourly-v1",
        "query_params": {
            "pair": "BTC-USD",
            "window": 20,
            "flags": [True, False, None],
            "settings": {"method": "population"},
        },
    }
    data.update(overrides)
    return data


def test_valid_opportunity_is_a_deterministic_research_candidate() -> None:
    opportunity = Opportunity.model_validate(opportunity_data())

    assert opportunity.opportunity_id == "opp-001"
    assert opportunity.observed_at == OBSERVED_AT
    assert opportunity.available_at == opportunity.observed_at
    assert opportunity.trigger_payload["window"] == 20


def test_opportunity_trigger_payload_is_recursively_immutable_and_copied() -> None:
    original = {"outer": {"array": [{"value": 1}]}}
    opportunity = Opportunity.model_validate(opportunity_data(trigger_payload=original))
    frozen: Any = opportunity.trigger_payload

    assert isinstance(opportunity.trigger_payload, FrozenJsonObject)
    with pytest.raises(TypeError):
        frozen["new"] = True
    with pytest.raises(TypeError):
        frozen["outer"]["new"] = True
    with pytest.raises(TypeError):
        frozen["outer"]["array"][0] = {"value": 2}

    original_value: Any = original
    original_value["outer"]["array"][0]["value"] = 99
    assert frozen["outer"]["array"][0]["value"] == 1


def test_opportunity_serializes_market_enums_stably() -> None:
    serialized = Opportunity.model_validate(opportunity_data()).model_dump(mode="json")

    assert serialized["venue"] == "COINBASE"
    assert serialized["pair"] == "BTC-USD"
    assert serialized["timeframe"] == "1h"


def test_opportunity_round_trips_json() -> None:
    opportunity = Opportunity.model_validate(opportunity_data())

    restored = Opportunity.model_validate_json(opportunity.model_dump_json())
    assert restored == opportunity
    assert restored.observed_at.utcoffset() == timedelta(0)


@pytest.mark.parametrize("field", ["observed_at", "available_at"])
@pytest.mark.parametrize("value", [1767297600, "2026-01-01T20:00:00Z"])
def test_opportunity_rejects_coercive_python_timestamps(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        Opportunity.model_validate(opportunity_data(**{field: value}))


@pytest.mark.parametrize("field", ["observed_at", "available_at"])
@pytest.mark.parametrize(
    "value",
    [
        OBSERVED_AT.replace(tzinfo=None),
        OBSERVED_AT.astimezone(timezone(timedelta(hours=8))),
    ],
)
def test_opportunity_rejects_naive_and_non_utc_timestamps(
    field: str, value: datetime
) -> None:
    with pytest.raises(ValidationError, match="timezone-aware UTC"):
        Opportunity.model_validate(opportunity_data(**{field: value}))


@pytest.mark.parametrize("delay", [timedelta(0), timedelta(minutes=5)])
def test_opportunity_accepts_equal_or_delayed_availability(delay: timedelta) -> None:
    opportunity = Opportunity.model_validate(
        opportunity_data(available_at=OBSERVED_AT + delay)
    )
    assert opportunity.available_at == opportunity.observed_at + delay


def test_opportunity_rejects_availability_before_observation() -> None:
    with pytest.raises(ValidationError, match="at or after observed_at"):
        Opportunity.model_validate(
            opportunity_data(available_at=OBSERVED_AT - timedelta(microseconds=1))
        )


@pytest.mark.parametrize("field", ["opportunity_id", "trigger_type", "summary"])
@pytest.mark.parametrize("value", ["", "   "])
def test_opportunity_rejects_blank_meaningful_text(field: str, value: str) -> None:
    with pytest.raises(ValidationError, match="nonblank"):
        Opportunity.model_validate(opportunity_data(**{field: value}))


@pytest.mark.parametrize(
    "trigger_payload",
    [{1: "non-string key"}, {"arbitrary": object()}],
)
def test_opportunity_rejects_non_json_trigger_payload(
    trigger_payload: object,
) -> None:
    with pytest.raises(ValidationError):
        Opportunity.model_validate(opportunity_data(trigger_payload=trigger_payload))


def test_opportunity_is_frozen() -> None:
    opportunity = Opportunity.model_validate(opportunity_data())

    with pytest.raises(ValidationError):
        opportunity.summary = "changed"


def test_opportunity_rejects_undeclared_fields() -> None:
    with pytest.raises(ValidationError):
        Opportunity.model_validate(opportunity_data(run_id="run-001"))


def evidence_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "evidence_id": "ev-001",
        "category": EvidenceCategory.MARKET,
        "evidence_type": "trend",
        "source": "coinbase_exchange_rest_candles",
        "observed_at": OBSERVED_AT,
        "available_at": OBSERVED_AT,
        "provenance": provenance_data(),
        "summary": "Price remains below its moving average.",
        "relevance": "The trend challenges a long reversal candidate.",
        "payload": {"below_average": True, "periods": [5, 20]},
        "quality": EvidenceQuality.HIGH,
    }
    data.update(overrides)
    return data


def thesis_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "summary": "A reversal is possible but trend risk remains material.",
        "bull_case": "Selling pressure is decelerating near support.",
        "bear_case": "The broader trend remains down.",
        "supporting_evidence_ids": ["ev-1", "ev-2"],
        "counterevidence_ids": ["ev-3"],
        "assumptions": ["The observed support remains relevant."],
        "invalidation_conditions": ["Price closes below support."],
        "uncertainties": ["Volume confirmation is limited."],
    }
    data.update(overrides)
    return data


def critique_data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "summary": "The reversal case depends heavily on one level.",
        "challenged_claims": ["Selling pressure is decelerating."],
        "missing_evidence": ["No relative-strength comparison is present."],
        "contradictions": ["Momentum and trend disagree."],
        "unsupported_assumptions": ["Support will attract buyers."],
        "evidence_quality_concerns": ["The volume window is short."],
        "unresolved_uncertainties": ["Regime classification is unclear."],
    }
    data.update(overrides)
    return data


def decision_data(
    outcome: DecisionOutcome = DecisionOutcome.TRADE, **overrides: Any
) -> dict[str, Any]:
    variants: dict[DecisionOutcome, dict[str, Any]] = {
        DecisionOutcome.TRADE: {
            "supporting_evidence_ids": ["ev-1"],
            "counterevidence_ids": ["ev-2"],
            "confidence": 70,
            "critic_response": "Trend risk is acknowledged in the assessment.",
            "abstention_reasons": [],
        },
        DecisionOutcome.NO_TRADE: {
            "supporting_evidence_ids": ["ev-1"],
            "counterevidence_ids": ["ev-2"],
            "confidence": 80,
            "critic_response": (
                "The weak reversal evidence does not answer the critique."
            ),
            "abstention_reasons": [],
        },
        DecisionOutcome.ABSTAIN: {
            "supporting_evidence_ids": [],
            "counterevidence_ids": [],
            "confidence": None,
            "critic_response": None,
            "abstention_reasons": [AbstentionReason.INSUFFICIENT_EVIDENCE],
        },
    }
    data: dict[str, Any] = {
        "outcome": outcome,
        "rationale": "The evidence supports this research conclusion.",
        "key_uncertainties": ["The market regime may change."],
        **variants[outcome],
    }
    data.update(overrides)
    return data


def test_research_enums_have_exact_stable_values() -> None:
    assert [item.value for item in EvidenceCategory] == ["market"]
    assert [item.value for item in EvidenceQuality] == ["LOW", "MEDIUM", "HIGH"]
    assert [item.value for item in DecisionOutcome] == [
        "TRADE",
        "NO_TRADE",
        "ABSTAIN",
    ]
    assert [item.value for item in AbstentionReason] == [
        "INSUFFICIENT_EVIDENCE",
        "CONFLICTING_EVIDENCE",
        "STALE_EVIDENCE",
        "UNAVAILABLE_EVIDENCE",
        "UNRELIABLE_EVIDENCE",
        "RESEARCH_FAILURE",
        "BUDGET_EXHAUSTED",
        "VALIDATION_FAILURE",
    ]


def test_risk_rejected_is_not_a_research_outcome() -> None:
    data = decision_data()
    data["outcome"] = "RISK_REJECTED"
    with pytest.raises(ValidationError):
        TradeDecision.model_validate(data)


def test_valid_provenance_round_trips_json() -> None:
    provenance = EvidenceProvenance.model_validate(provenance_data())

    assert provenance.artifact_ref is None
    frozen_params: Any = provenance.query_params
    assert frozen_params["flags"] == (True, False, None)
    assert (
        EvidenceProvenance.model_validate_json(provenance.model_dump_json())
        == provenance
    )


def test_provenance_query_params_are_recursively_immutable_and_copied() -> None:
    original = {"outer": {"array": [{"value": 1}]}}
    provenance = EvidenceProvenance.model_validate(
        provenance_data(query_params=original)
    )
    frozen: Any = provenance.query_params

    with pytest.raises(TypeError):
        frozen["new"] = True
    with pytest.raises(TypeError):
        frozen["outer"]["new"] = True
    with pytest.raises(TypeError):
        frozen["outer"]["array"][0] = {"value": 2}

    original_value: Any = original
    original_value["outer"]["array"][0]["value"] = 99
    assert frozen["outer"]["array"][0]["value"] == 1


@pytest.mark.parametrize("field", ["producer", "source_version"])
@pytest.mark.parametrize("value", ["", "   "])
def test_provenance_rejects_blank_identifiers(field: str, value: str) -> None:
    with pytest.raises(ValidationError, match="nonblank"):
        EvidenceProvenance.model_validate(provenance_data(**{field: value}))


def test_provenance_accepts_nonblank_artifact_reference() -> None:
    provenance = EvidenceProvenance.model_validate(
        provenance_data(artifact_ref="artifact://market/ev-1")
    )
    assert provenance.artifact_ref == "artifact://market/ev-1"


@pytest.mark.parametrize(
    "query_params",
    [{1: "not-a-string-key"}, {"arbitrary": object()}],
)
def test_query_params_reject_non_json_values(query_params: object) -> None:
    with pytest.raises(ValidationError):
        EvidenceProvenance.model_validate(provenance_data(query_params=query_params))


def test_provenance_is_frozen_and_forbids_extra_fields() -> None:
    provenance = EvidenceProvenance.model_validate(provenance_data())
    with pytest.raises(ValidationError):
        provenance.producer = "changed"
    with pytest.raises(ValidationError):
        EvidenceProvenance.model_validate(provenance_data(extra="forbidden"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"payload": {"trend": "down"}},
        {
            "payload": None,
            "provenance": provenance_data(artifact_ref="artifact://market/ev-1"),
        },
        {
            "payload": {"trend": "down"},
            "provenance": provenance_data(artifact_ref="artifact://market/ev-1"),
        },
    ],
)
def test_evidence_accepts_payload_or_artifact_grounding(
    overrides: dict[str, Any],
) -> None:
    EvidenceItem.model_validate(evidence_data(**overrides))


def test_evidence_rejects_prose_only_grounding() -> None:
    with pytest.raises(ValidationError, match="payload or artifact_ref"):
        EvidenceItem.model_validate(evidence_data(payload=None))


@pytest.mark.parametrize("delay", [timedelta(0), timedelta(minutes=5)])
def test_evidence_accepts_equal_or_delayed_availability(delay: timedelta) -> None:
    evidence = EvidenceItem.model_validate(
        evidence_data(available_at=OBSERVED_AT + delay)
    )
    assert evidence.available_at == evidence.observed_at + delay


def test_evidence_rejects_availability_before_observation() -> None:
    with pytest.raises(ValidationError, match="at or after observed_at"):
        EvidenceItem.model_validate(
            evidence_data(available_at=OBSERVED_AT - timedelta(microseconds=1))
        )


@pytest.mark.parametrize("field", ["observed_at", "available_at"])
@pytest.mark.parametrize(
    "value",
    [
        "2026-01-01T20:00:00Z",
        1767297600,
        OBSERVED_AT.replace(tzinfo=None),
        OBSERVED_AT.astimezone(timezone(timedelta(hours=8))),
    ],
)
def test_evidence_requires_strict_utc_python_timestamps(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        EvidenceItem.model_validate(evidence_data(**{field: value}))


def test_evidence_accepts_distinct_zero_offset_timezone() -> None:
    zero_offset = timezone(timedelta(0), name="ZERO")
    value = OBSERVED_AT.replace(tzinfo=zero_offset)
    evidence = EvidenceItem.model_validate(
        evidence_data(observed_at=value, available_at=value)
    )
    assert evidence.observed_at.tzinfo is zero_offset


@pytest.mark.parametrize(
    "field", ["evidence_id", "evidence_type", "source", "summary", "relevance"]
)
@pytest.mark.parametrize("value", ["", "  "])
def test_evidence_rejects_blank_meaningful_text(field: str, value: str) -> None:
    with pytest.raises(ValidationError, match="nonblank"):
        EvidenceItem.model_validate(evidence_data(**{field: value}))


@pytest.mark.parametrize("payload", [{"value": object()}, {1: "non-string key"}])
def test_evidence_rejects_arbitrary_payload_objects_and_non_string_keys(
    payload: object,
) -> None:
    with pytest.raises(ValidationError):
        EvidenceItem.model_validate(evidence_data(payload=payload))


def test_evidence_payload_is_recursively_immutable_and_copied() -> None:
    original = {"outer": {"array": [{"value": 1}]}}
    evidence = EvidenceItem.model_validate(evidence_data(payload=original))
    frozen: Any = evidence.payload

    with pytest.raises(TypeError):
        frozen["new"] = True
    with pytest.raises(TypeError):
        frozen["outer"]["new"] = True
    with pytest.raises(TypeError):
        frozen["outer"]["array"][0] = {"value": 2}

    original_value: Any = original
    original_value["outer"]["array"][0]["value"] = 99
    assert frozen["outer"]["array"][0]["value"] == 1


@pytest.mark.parametrize("non_finite", [nan, inf, -inf])
@pytest.mark.parametrize(
    "nesting", [lambda value: value, lambda value: {"a": [{"b": value}]}]
)
@pytest.mark.parametrize("field", ["trigger_payload", "query_params", "payload"])
def test_json_fields_reject_non_finite_numbers_at_any_depth(
    field: str,
    nesting: Any,
    non_finite: float,
) -> None:
    value = nesting(non_finite)
    with pytest.raises(ValidationError, match="finite"):
        if field == "trigger_payload":
            Opportunity.model_validate(
                opportunity_data(trigger_payload={"value": value})
            )
        elif field == "query_params":
            EvidenceProvenance.model_validate(
                provenance_data(query_params={"value": value})
            )
        else:
            EvidenceItem.model_validate(evidence_data(payload=value))


@pytest.mark.parametrize("finite", [-1.5, 0.0, 2.75])
def test_json_fields_accept_finite_floats(finite: float) -> None:
    Opportunity.model_validate(opportunity_data(trigger_payload={"value": finite}))
    EvidenceProvenance.model_validate(provenance_data(query_params={"value": finite}))
    EvidenceItem.model_validate(evidence_data(payload={"value": finite}))


def test_frozen_json_serializes_normally_and_round_trips_exactly() -> None:
    value = {
        "object": {"array": [None, True, False, "text", 7, 1.25]},
        "empty_object": {},
        "empty_array": [],
    }
    opportunity = Opportunity.model_validate(opportunity_data(trigger_payload=value))
    provenance = EvidenceProvenance.model_validate(provenance_data(query_params=value))
    evidence = EvidenceItem.model_validate(evidence_data(payload=value))

    for model in (opportunity, provenance, evidence):
        dumped = model.model_dump(mode="json")
        restored = type(model).model_validate_json(model.model_dump_json())
        assert restored == model
        assert isinstance(dumped, dict)

    assert opportunity.model_dump(mode="json")["trigger_payload"] == value
    assert provenance.model_dump(mode="json")["query_params"] == value
    assert evidence.model_dump(mode="json")["payload"] == value


@pytest.mark.parametrize(
    "model",
    [
        Opportunity.model_validate(
            opportunity_data(trigger_payload={"nested": {"array": [1]}})
        ),
        EvidenceProvenance.model_validate(
            provenance_data(query_params={"nested": {"array": [1]}})
        ),
        EvidenceItem.model_validate(evidence_data(payload={"nested": {"array": [1]}})),
    ],
)
def test_research_models_support_deep_copy_with_immutable_json(model: Any) -> None:
    for copied in (copy.deepcopy(model), model.model_copy(deep=True)):
        assert copied == model
        assert copied.model_dump(mode="json") == model.model_dump(mode="json")
        assert copied.model_dump_json() == model.model_dump_json()

        if isinstance(copied, Opportunity):
            field = "trigger_payload"
        elif isinstance(copied, EvidenceProvenance):
            field = "query_params"
        else:
            field = "payload"
        frozen: Any = getattr(copied, field)
        with pytest.raises(TypeError):
            frozen["new"] = True
        with pytest.raises(TypeError):
            frozen["nested"]["new"] = True
        with pytest.raises(TypeError):
            frozen["nested"]["array"][0] = "changed"


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (Opportunity.model_validate(opportunity_data()), "trigger_payload"),
        (EvidenceProvenance.model_validate(provenance_data()), "query_params"),
        (EvidenceItem.model_validate(evidence_data()), "payload"),
    ],
)
def test_frozen_json_backing_state_cannot_be_reassigned(model: Any, field: str) -> None:
    frozen: Any = getattr(model, field)
    before = model.model_dump(mode="json")

    with pytest.raises(AttributeError, match="immutable"):
        frozen._data = {"changed": True}

    assert model.model_dump(mode="json") == before
    assert type(model).model_validate_json(model.model_dump_json()) == model


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (Opportunity.model_validate(opportunity_data()), "trigger_payload"),
        (EvidenceProvenance.model_validate(provenance_data()), "query_params"),
        (EvidenceItem.model_validate(evidence_data()), "payload"),
    ],
)
def test_frozen_json_backing_state_cannot_be_deleted(model: Any, field: str) -> None:
    frozen: Any = getattr(model, field)
    before = model.model_dump(mode="json")
    before_json = model.model_dump_json()

    with pytest.raises(AttributeError, match="immutable"):
        del frozen._data

    assert model.model_dump(mode="json") == before
    assert model.model_dump_json() == before_json
    assert type(model).model_validate_json(before_json) == model


@pytest.mark.parametrize("model", [Opportunity, EvidenceProvenance, EvidenceItem])
def test_research_models_generate_json_schema(model: Any) -> None:
    schema = model.model_json_schema()

    assert schema["type"] == "object"
    assert "properties" in schema


def test_evidence_json_round_trip_preserves_enums_utc_and_json_values() -> None:
    evidence = EvidenceItem.model_validate(evidence_data())
    serialized = evidence.model_dump(mode="json")

    assert serialized["category"] == "market"
    assert serialized["quality"] == "HIGH"
    assert EvidenceItem.model_validate_json(evidence.model_dump_json()) == evidence


def test_evidence_is_frozen_and_forbids_extra_fields() -> None:
    evidence = EvidenceItem.model_validate(evidence_data())
    with pytest.raises(ValidationError):
        evidence.summary = "changed"
    with pytest.raises(ValidationError):
        EvidenceItem.model_validate(evidence_data(direction="BULLISH"))


def test_valid_thesis_preserves_order_and_round_trips_json() -> None:
    thesis = ResearchThesis.model_validate(thesis_data())

    assert thesis.supporting_evidence_ids == ("ev-1", "ev-2")
    assert isinstance(thesis.assumptions, tuple)
    assert ResearchThesis.model_validate_json(thesis.model_dump_json()) == thesis


def test_thesis_allows_empty_evidence_and_text_collections() -> None:
    thesis = ResearchThesis.model_validate(
        thesis_data(
            supporting_evidence_ids=[],
            counterevidence_ids=[],
            assumptions=[],
            invalidation_conditions=[],
            uncertainties=[],
        )
    )
    assert thesis.supporting_evidence_ids == ()


@pytest.mark.parametrize("field", ["summary", "bull_case", "bear_case"])
def test_thesis_rejects_blank_primary_text(field: str) -> None:
    with pytest.raises(ValidationError):
        ResearchThesis.model_validate(thesis_data(**{field: " "}))


@pytest.mark.parametrize(
    "field",
    [
        "supporting_evidence_ids",
        "counterevidence_ids",
        "assumptions",
        "invalidation_conditions",
        "uncertainties",
    ],
)
def test_thesis_rejects_blank_collection_entries(field: str) -> None:
    with pytest.raises(ValidationError):
        ResearchThesis.model_validate(thesis_data(**{field: [" "]}))


@pytest.mark.parametrize("field", ["supporting_evidence_ids", "counterevidence_ids"])
def test_thesis_rejects_duplicate_evidence_references(field: str) -> None:
    with pytest.raises(ValidationError, match="duplicates"):
        ResearchThesis.model_validate(thesis_data(**{field: ["ev-1", "ev-1"]}))


def test_thesis_is_frozen_and_forbids_extra_fields() -> None:
    thesis = ResearchThesis.model_validate(thesis_data())
    with pytest.raises(ValidationError):
        thesis.summary = "changed"
    with pytest.raises(ValidationError):
        ResearchThesis.model_validate(thesis_data(extra="forbidden"))


def test_valid_critique_uses_tuples_and_round_trips_json() -> None:
    critique = Critique.model_validate(critique_data())

    assert isinstance(critique.challenged_claims, tuple)
    assert Critique.model_validate_json(critique.model_dump_json()) == critique


def test_critique_allows_empty_collections() -> None:
    critique = Critique(summary="No material concerns identified.")
    assert critique.unresolved_uncertainties == ()


@pytest.mark.parametrize(
    "field",
    [
        "summary",
        "challenged_claims",
        "missing_evidence",
        "contradictions",
        "unsupported_assumptions",
        "evidence_quality_concerns",
        "unresolved_uncertainties",
    ],
)
def test_critique_rejects_blank_text(field: str) -> None:
    value: object = " " if field == "summary" else [" "]
    with pytest.raises(ValidationError):
        Critique.model_validate(critique_data(**{field: value}))


def test_critique_is_frozen_and_forbids_extra_fields() -> None:
    critique = Critique.model_validate(critique_data())
    with pytest.raises(ValidationError):
        critique.summary = "changed"
    with pytest.raises(ValidationError):
        Critique.model_validate(critique_data(extra="forbidden"))


@pytest.mark.parametrize("outcome", list(DecisionOutcome))
def test_valid_decisions_round_trip_json(outcome: DecisionOutcome) -> None:
    decision = TradeDecision.model_validate(decision_data(outcome))

    assert TradeDecision.model_validate_json(decision.model_dump_json()) == decision
    assert isinstance(decision.supporting_evidence_ids, tuple)
    assert isinstance(decision.key_uncertainties, tuple)


def test_abstain_may_include_available_research_results() -> None:
    decision = TradeDecision.model_validate(
        decision_data(
            DecisionOutcome.ABSTAIN,
            supporting_evidence_ids=["ev-1"],
            counterevidence_ids=["ev-2"],
            confidence=25,
            critic_response="The critique confirms that conflict remains unresolved.",
        )
    )
    assert decision.confidence == 25


@pytest.mark.parametrize(
    ("outcome", "overrides", "message"),
    [
        (DecisionOutcome.TRADE, {"supporting_evidence_ids": []}, "supporting"),
        (DecisionOutcome.TRADE, {"confidence": None}, "confidence"),
        (DecisionOutcome.TRADE, {"critic_response": None}, "critic"),
        (DecisionOutcome.NO_TRADE, {"counterevidence_ids": []}, "counterevidence"),
        (DecisionOutcome.NO_TRADE, {"confidence": None}, "confidence"),
        (DecisionOutcome.NO_TRADE, {"critic_response": None}, "critic"),
        (DecisionOutcome.ABSTAIN, {"abstention_reasons": []}, "abstention"),
    ],
)
def test_decision_rejects_missing_outcome_requirements(
    outcome: DecisionOutcome, overrides: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        TradeDecision.model_validate(decision_data(outcome, **overrides))


@pytest.mark.parametrize("outcome", [DecisionOutcome.TRADE, DecisionOutcome.NO_TRADE])
def test_non_abstaining_decisions_reject_abstention_reasons(
    outcome: DecisionOutcome,
) -> None:
    with pytest.raises(ValidationError, match="must not include"):
        TradeDecision.model_validate(
            decision_data(
                outcome,
                abstention_reasons=[AbstentionReason.CONFLICTING_EVIDENCE],
            )
        )


@pytest.mark.parametrize("outcome", [DecisionOutcome.TRADE, DecisionOutcome.NO_TRADE])
def test_normal_decisions_reject_blank_critic_response(
    outcome: DecisionOutcome,
) -> None:
    with pytest.raises(ValidationError):
        TradeDecision.model_validate(decision_data(outcome, critic_response=" "))


@pytest.mark.parametrize("value", [-1, 101, 1.5, "50", True])
def test_confidence_rejects_out_of_range_or_nonstrict_values(value: object) -> None:
    with pytest.raises(ValidationError):
        TradeDecision.model_validate(decision_data(confidence=value))


@pytest.mark.parametrize("value", [0, 100])
def test_confidence_accepts_inclusive_boundaries(value: int) -> None:
    assert (
        TradeDecision.model_validate(decision_data(confidence=value)).confidence
        == value
    )


@pytest.mark.parametrize("field", ["supporting_evidence_ids", "counterevidence_ids"])
@pytest.mark.parametrize("values", [["ev-1", "ev-1"], ["ev-1", " "]])
def test_decision_rejects_duplicate_or_blank_evidence_references(
    field: str, values: list[str]
) -> None:
    data = decision_data()
    data[field] = values
    with pytest.raises(ValidationError):
        TradeDecision.model_validate(data)


def test_decision_preserves_evidence_reference_order() -> None:
    decision = TradeDecision.model_validate(
        decision_data(supporting_evidence_ids=["ev-2", "ev-1"])
    )
    assert decision.supporting_evidence_ids == ("ev-2", "ev-1")


@pytest.mark.parametrize("field", ["position_size", "execution_price", "pnl"])
def test_decision_rejects_execution_and_financial_fields(field: str) -> None:
    data = decision_data()
    data[field] = "1"
    with pytest.raises(ValidationError):
        TradeDecision.model_validate(data)


def test_decision_is_frozen_and_forbids_extra_fields() -> None:
    decision = TradeDecision.model_validate(decision_data())
    with pytest.raises(ValidationError):
        decision.rationale = "changed"
    with pytest.raises(ValidationError):
        TradeDecision.model_validate(decision_data(extra="forbidden"))
