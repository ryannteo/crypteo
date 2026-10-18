"""Behavioral and adversarial tests for the first bounded research workflow."""

import asyncio
import inspect
import json
import socket
from datetime import timedelta
from decimal import Decimal
from importlib.metadata import version as package_version
from itertools import combinations
from typing import Any

import pytest
from pydantic import ValidationError, create_model
from research_support import (
    BTC,
    ETH,
    SOL,
    ScriptedModel,
    T,
    assessment,
    candles,
    completion,
    critique,
    opportunity,
    run,
    service,
    structured,
    thesis,
    tool,
)

import crypteo.research_workflow as workflow
from crypteo.domain.market import TradingPair
from crypteo.domain.market_research import (
    MarketResearchQuery,
    MarketSourceBinding,
    ResearchToolSuccess,
    ResearchToolUnavailable,
)
from crypteo.domain.research import AbstentionReason
from crypteo.domain.research_workflow import (
    AssessmentOrigin,
    ModelCallRecord,
    ModelRequestRejection,
    NodeTransition,
    OpportunityResearchRequest,
    ResearchFailureCode,
    ResearchRunResult,
    ResearchRunStatus,
    ResearchWorkflowConfig,
    ToolRecord,
    ToolRejection,
)
from crypteo.market_research import MarketResearchService
from crypteo.research_model import (
    FailureReply,
    ModelConfiguration,
    ModelReply,
    ModelRequest,
    ProviderFailureCode,
    ProviderMetadata,
    RefusalReply,
    StructuredReply,
    ToolReply,
    ToolRequest,
)
from crypteo.research_prompts import (
    INVESTIGATION_INSTRUCTIONS,
    SHARED_POLICY,
    prompt_identity,
)
from crypteo.research_tools import bind_tool_request, dispatch_tool, tool_definitions
from crypteo.research_workflow import ResearchInvariantError, collected_evidence
from crypteo.temporal import PointInTimeDataGateway, SimulationClock


def records(result: ResearchRunResult) -> tuple[ToolRecord, ...]:
    return tuple(r for r in result.trace if isinstance(r, ToolRecord))


def data(request: ModelRequest) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(request.messages[-1].content)["research_data"]
    return value


@pytest.mark.parametrize("outcome", ["TRADE", "NO_TRADE", "ABSTAIN"])
def test_complete_workflow_and_exact_transitions(outcome: str) -> None:
    model = ScriptedModel([tool("momentum"), tool("trend")], outcome=outcome)
    result = run(model)
    assert result.status == ResearchRunStatus.COMPLETED
    assert result.assessment_origin == AssessmentOrigin.MODEL
    assert result.assessment.outcome.value == outcome
    assert result.failure is None
    assert result.assessment.critic_response
    assert [
        (r.source, r.target) for r in result.trace if isinstance(r, NodeTransition)
    ] == [
        ("__start__", "investigate"),
        ("investigate", "execute_tool"),
        ("execute_tool", "investigate"),
        ("investigate", "execute_tool"),
        ("execute_tool", "investigate"),
        ("investigate", "draft_thesis"),
        ("draft_thesis", "critique"),
        ("critique", "final_assessment"),
        ("final_assessment", "__end__"),
    ]
    assert [r.stage for r in model.requests] == ["investigate"] * 3 + [
        "draft_thesis",
        "critique",
        "final_assessment",
    ]
    assert [r.call_id for r in model.requests] == [
        f"run-1/model/{i:04d}" for i in range(1, 7)
    ]
    assert [e["evidence_type"] for e in data(model.requests[2])["evidence"]] == [
        "trend",
        "momentum",
    ]
    assert result.trace[-1].model_dump()["termination_reason"] == "COMPLETED"
    if outcome == "TRADE":
        assert result.assessment.confidence == 5  # no deterministic threshold


def test_adaptive_choice_uses_first_result() -> None:
    def choose(request: ModelRequest) -> ModelReply:
        observed = data(request)["evidence"]
        assert observed[0]["payload"]["sma20"] == "100.0000000000000000000001"
        return tool("relative_strength", {"benchmark": "ETH-USD"})

    result = run(ScriptedModel([tool(), choose]))
    assert [r.query.capability.value for r in records(result) if r.query] == [
        "trend",
        "relative_strength",
    ]
    query = records(result)[1].query
    assert query is not None
    assert query.benchmark == ETH


def test_critic_fresh_context_exact_thesis_all_evidence_and_request_history() -> None:
    model = ScriptedModel(
        [
            tool("trend"),
            tool("momentum"),
            tool("relative_strength", {"benchmark": "ETH-USD"}),
        ]
    )
    # ETH is bound, so relative strength is offered, but its window has a gap.
    svc = MarketResearchService(
        PointInTimeDataGateway(
            SimulationClock(T), candles(BTC) + candles(ETH, delayed=40)
        ),
        [
            MarketSourceBinding(pair=p, dataset_version="sha256:" + "c" * 64)
            for p in (BTC, ETH)
        ],
    )
    result = run(model, svc)
    critic_request, final_request = model.requests[-2:]
    assert len(critic_request.messages) == 2
    assert critic_request.tools == final_request.tools == ()
    assert "independent research critic" in critic_request.messages[0].content
    assert "Investigator completed." not in critic_request.messages[-1].content
    assert "investigation_completion" not in data(critic_request)
    assert result.thesis is not None and result.critique is not None
    assert data(critic_request)["thesis"] == result.thesis.model_dump(mode="json")
    assert len(data(critic_request)["evidence"]) == 2
    assert [r["result"]["status"] for r in data(critic_request)["request_history"]] == [
        "success",
        "success",
        "unavailable",
    ]
    assert data(final_request)["critique"] == result.critique.model_dump(mode="json")
    assert data(final_request)["thesis"] == data(critic_request)["thesis"]


def test_eight_attempts_and_ninth_completion_twelve_calls_cache_linkage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = dispatch_tool
    dispatched = []

    def dispatch(*args: Any) -> ResearchToolSuccess | ResearchToolUnavailable:
        dispatched.append(args[1])
        return original(*args)

    monkeypatch.setattr(workflow, "dispatch_tool", dispatch)
    model = ScriptedModel([tool()] * 8)
    result = run(model)
    assert result.status == "COMPLETED"
    assert len(dispatched) == 1
    assert len(model.requests) == 12
    assert len(records(result)) == 8
    assert data(model.requests[8])["attempts_remaining"] == 0
    assert all(r.admitted for r in records(result))
    assert all(r.cached_from == "run-1/tool/0001" for r in records(result)[1:])
    assert all(r.result == records(result)[0].result for r in records(result))
    assert len(collected_evidence(result.trace)) == 1


def test_final_investigation_offers_no_tools_and_earlier_calls_keep_them() -> None:
    model = ScriptedModel([tool()] * 8)
    result = run(model)
    assert result.status == "COMPLETED" and result.investigation is not None
    investigations = [r for r in model.requests if r.stage == "investigate"]
    assert len(investigations) == 9
    for request in investigations[:8]:
        assert data(request)["attempts_remaining"] > 0
        assert request.tools == tool_definitions(BTC, (ETH,))
    final = investigations[8]
    assert data(final)["attempts_remaining"] == 0
    assert final.tools == ()
    assert final.messages[0].content == SHARED_POLICY + "\n\n" + (
        INVESTIGATION_INSTRUCTIONS
    )
    assert "no tools are offered; return" in final.messages[0].content
    assert final.prompt_identity == prompt_identity("investigate")
    assert final.prompt_identity.version == "investigate_prompt_v4"
    assert all(r.tools == () for r in model.requests[9:])


def test_blank_ninth_completion_after_budget_fails_validation_without_repair() -> None:
    empty = '{"summary":"","relevant_evidence_ids":[],"remaining_gaps":[]}'
    model = ScriptedModel([tool()] * 8 + [StructuredReply(content=empty)] * 2)
    result = run(model)
    final = model.requests[-1]
    # The one re-ask is a new recorded call; its blank reply is never filled.
    assert len(model.requests) == 10 and len(records(result)) == 8
    assert data(final)["attempts_remaining"] == 0
    assert data(final)["output_rejection"] == {
        "call_id": "run-1/model/0009",
        "problems": ["summary: text must be nonblank"],
    }
    assert final.tools == ()
    assert final.messages[0].content == SHARED_POLICY + "\n\n" + (
        INVESTIGATION_INSTRUCTIONS
    )
    assert "summary must be a nonblank synthesis" in final.messages[0].content
    assert final.prompt_identity == prompt_identity("investigate")
    assert result.status == "INTERRUPTED" and result.assessment_origin == "RUNTIME"
    assert result.failure is not None
    assert result.failure.code == ResearchFailureCode.INVALID_MODEL_OUTPUT
    assert result.assessment.abstention_reasons == (
        AbstentionReason.VALIDATION_FAILURE,
    )
    assert result.thesis is None and result.critique is None


def test_ninth_tool_request_interrupts_without_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    count = 0
    original = dispatch_tool

    def dispatch(*args: Any) -> ResearchToolSuccess | ResearchToolUnavailable:
        nonlocal count
        count += 1
        return original(*args)

    monkeypatch.setattr(workflow, "dispatch_tool", dispatch)
    model = ScriptedModel([tool()] * 9)
    result = run(model)
    assert count == 1
    assert len(model.requests) == 9
    # The ninth call offers no tools; a tool request there is still rejected by
    # the budget check, never dispatched, accepted or converted into completion.
    assert model.requests[8].tools == ()
    assert all(r.tools == tool_definitions(BTC, (ETH,)) for r in model.requests[:8])
    assert result.status == "INTERRUPTED" and result.assessment_origin == "RUNTIME"
    assert result.investigation is None
    rejection = records(result)[-1].rejection
    assert rejection is not None and rejection.code == "TOOL_BUDGET_EXHAUSTED"
    assert sum(r.admitted for r in records(result)) == 8
    assert not records(result)[-1].admitted
    assert result.failure is not None
    assert result.failure.code == ResearchFailureCode.TOOL_BUDGET_EXHAUSTED
    assert result.assessment.abstention_reasons == (AbstentionReason.BUDGET_EXHAUSTED,)
    assert result.thesis is None and result.critique is None


@pytest.mark.parametrize(
    "bad",
    [
        tool("unknown"),
        tool("trend", {"pair": "ETH-USD"}),
        ToolReply(requests=(tool().requests[0], tool("levels").requests[0])),
        ToolReply(requests=()),
    ],
)
def test_first_invalid_feedback_consumes_attempt_second_interrupts(
    bad: ToolReply,
) -> None:
    model = ScriptedModel([bad, bad])
    result = run(model)
    assert len(model.requests) == 2
    assert result.failure is not None
    assert result.failure.code == ResearchFailureCode.INVALID_TOOL_REQUEST
    assert result.status == "INTERRUPTED" and result.assessment_origin == "RUNTIME"
    assert all(r.result is None and r.admitted for r in records(result))
    assert len(data(model.requests[1])["request_history"]) == 1
    assert data(model.requests[1])["attempts_remaining"] == 7
    assert (
        data(model.requests[1])["request_history"][0]["rejection"]["code"]
        == "INVALID_TOOL_REQUEST"
    )


def test_first_invalid_request_can_recover_and_complete() -> None:
    model = ScriptedModel([tool("trend", {"lookback": 100}), tool()])
    result = run(model)
    assert result.status == "COMPLETED"
    assert records(result)[0].rejection is not None
    assert records(result)[1].result is not None
    assert data(model.requests[2])["attempts_remaining"] == 6
    assert all(
        r.tools == tool_definitions(BTC, (ETH,))
        for r in model.requests
        if r.stage == "investigate"
    )


@pytest.mark.parametrize("subject", tuple(TradingPair))
@pytest.mark.parametrize("size", [0, 1, 2])
def test_context_offers_and_binds_only_bound_benchmarks(
    subject: TradingPair, size: int
) -> None:
    others = tuple(p for p in (BTC, ETH, SOL) if p != subject)
    for benchmarks in combinations(others, size):
        model = ScriptedModel([tool()], outcome="ABSTAIN")
        request = OpportunityResearchRequest(
            research_run_id="bound-run", opportunity=opportunity(subject)
        )
        result = run(model, service(pairs=(subject, *benchmarks)), request=request)
        assert result.context.benchmarks == benchmarks
        investigations = [r for r in model.requests if r.stage == "investigate"]
        assert all(
            r.tools == tool_definitions(subject, benchmarks) for r in investigations
        )
        bound_query = records(result)[0].query
        assert bound_query is not None and bound_query.pair == subject
        for benchmark in others:
            query = bind_tool_request(
                tool("relative_strength", {"benchmark": benchmark.value}).requests,
                subject,
                result.context.benchmarks,
            )
            if benchmark in benchmarks:
                assert isinstance(query, MarketResearchQuery)
                assert (query.pair, query.benchmark) == (subject, benchmark)
            else:
                assert isinstance(query, ToolRejection)


def test_btc_only_rejects_unoffered_relative_strength_and_completes() -> None:
    model = ScriptedModel(
        [tool("relative_strength", {"benchmark": "ETH-USD"}), tool()],
        outcome="NO_TRADE",
    )
    result = run(model, service(pairs=(BTC,)))
    assert result.status == "COMPLETED" and result.context.benchmarks == ()
    investigations = [r for r in model.requests if r.stage == "investigate"]
    assert all(
        [t.name for t in r.tools]
        == [
            "trend",
            "momentum",
            "volatility",
            "bollinger_position",
            "levels",
            "volume_context",
        ]
        for r in investigations
    )
    first = records(result)[0]
    assert first.admitted and first.query is None and first.result is None
    assert first.rejection == ToolRejection(
        code="INVALID_TOOL_REQUEST",
        message="relative_strength is not offered without a bound benchmark",
    )
    assert data(investigations[1])["attempts_remaining"] == 7
    assert records(result)[1].result is not None


def test_second_unoffered_relative_strength_interrupts_run() -> None:
    model = ScriptedModel([tool("relative_strength", {"benchmark": "ETH-USD"})] * 2)
    result = run(model, service(pairs=(BTC,)))
    assert len(model.requests) == 2 and len(records(result)) == 2
    assert all(r.admitted and r.result is None for r in records(result))
    assert result.status == "INTERRUPTED" and result.assessment_origin == "RUNTIME"
    assert result.failure is not None
    assert result.failure.code == ResearchFailureCode.INVALID_TOOL_REQUEST
    assert result.assessment.abstention_reasons == (
        AbstentionReason.VALIDATION_FAILURE,
    )


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("trend", {"pair": "ETH-USD"}),
        ("trend", {"timestamp": T.isoformat()}),
        ("trend", {"cutoff": T.isoformat()}),
        ("trend", {"lookback": 20}),
        ("trend", {"window": 20}),
        ("trend", {"price": "100"}),
        ("trend", {"source": "other"}),
        ("trend", {"code": "__import__('os').system('bad')"}),
        ("__class__", {}),
        ("relative_strength", {"benchmark": "BTC-USD"}),
        ("relative_strength", {"benchmark": "DOGE-USD"}),
        ("relative_strength", {"benchmark": ["ETH-USD"]}),
        ("relative_strength", {"benchmark": "ETH-USD", "pair": "SOL-USD"}),
        ("relative_strength", {}),
        ("trend", {"benchmark": "ETH-USD"}),
    ],
)
def test_authority_rejections(name: str, arguments: dict[str, Any]) -> None:
    result = run(ScriptedModel([tool(name, arguments), tool(name, arguments)]))
    assert result.failure and result.failure.code == "INVALID_TOOL_REQUEST"
    assert not collected_evidence(result.trace)


@pytest.mark.parametrize(
    "arguments",
    [
        "null",
        "[]",
        "{broken",
        '{"benchmark":"ETH-USD","benchmark":"SOL-USD"}',
        '{"price":NaN}',
    ],
)
def test_bad_argument_json_consumes_attempt(arguments: str) -> None:
    bad = ToolReply(
        requests=(ToolRequest(name="relative_strength", arguments_json=arguments),)
    )
    result = run(ScriptedModel([bad, bad]))
    assert result.failure and result.failure.code == "INVALID_TOOL_REQUEST"


def test_unavailable_cached_and_not_synthetic_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    count = 0
    original = dispatch_tool

    def dispatch(*args: Any) -> ResearchToolSuccess | ResearchToolUnavailable:
        nonlocal count
        count += 1
        return original(*args)

    monkeypatch.setattr(workflow, "dispatch_tool", dispatch)
    model = ScriptedModel([tool()] * 8, outcome="ABSTAIN")
    result = run(model, service(delayed=40))
    assert result.status == "COMPLETED" and count == 1
    assert collected_evidence(result.trace) == ()
    assert all(isinstance(r.result, ResearchToolUnavailable) for r in records(result))
    assert all(
        r.cached_from == records(result)[0].attempt_id for r in records(result)[1:]
    )
    first = records(result)[0].result
    assert first is not None
    assert first.model_dump()["reason"] == "HISTORY_GAP"


def test_relative_strength_executes_once_per_benchmark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[TradingPair | None] = []
    original = dispatch_tool

    def dispatch(*args: Any) -> ResearchToolSuccess | ResearchToolUnavailable:
        calls.append(args[1].benchmark)
        return original(*args)

    monkeypatch.setattr(workflow, "dispatch_tool", dispatch)
    result = run(
        ScriptedModel(
            [
                tool("relative_strength", {"benchmark": p})
                for p in ["SOL-USD", "ETH-USD", "SOL-USD", "ETH-USD"]
            ]
        ),
        service(pairs=(BTC, ETH, SOL)),
    )
    assert calls == [SOL, ETH]
    assert [
        e.model_dump(mode="json")["provenance"]["query_params"]["query"]["benchmark"]
        for e in collected_evidence(result.trace)
    ] == ["ETH-USD", "SOL-USD"]
    assert [r.cached_from for r in records(result)] == [
        None,
        None,
        "run-1/tool/0001",
        "run-1/tool/0002",
    ]


def test_future_and_delayed_data_visibility() -> None:
    normal = run(ScriptedModel([tool()]))
    future = run(ScriptedModel([tool()]), service(future=True))
    assert records(normal)[0].result == records(future)[0].result
    delayed = run(ScriptedModel([tool()], outcome="ABSTAIN"), service(delayed=40))
    assert isinstance(records(delayed)[0].result, ResearchToolUnavailable)
    assert records(delayed)[0].result.model_dump()["reason"] == "HISTORY_GAP"  # type: ignore[union-attr]
    assert collected_evidence(delayed.trace) == ()


def test_model_preserves_exact_decimals_undefined_reasons_and_provenance() -> None:
    model = ScriptedModel([tool(), tool("volume_context")])
    result = run(model)
    evidence = data(model.requests[2])["evidence"]
    assert evidence[0]["payload"]["sma20"] == "100.0000000000000000000001"
    assert evidence[1]["payload"]["volume_ratio"] == {
        "value": None,
        "undefined_reason": "ZERO_BASELINE_VOLUME",
    }
    assert evidence == [
        e.model_dump(mode="json") for e in collected_evidence(result.trace)
    ]
    assert (
        evidence[0]["provenance"]["query_params"]["inputs"][0]["source"][
            "dataset_version"
        ]
        == "sha256:" + "c" * 64
    )


@pytest.mark.parametrize("stage", ["investigate", "draft_thesis", "final_assessment"])
def test_unknown_or_unavailable_evidence_citations_interrupt(stage: str) -> None:
    bad_id = "unavailable-or-fabricated-id"
    steps: list[Any] = [tool()]
    if stage == "investigate":
        steps.append(completion((bad_id,)))
    elif stage == "draft_thesis":
        steps += [completion(), structured(thesis((bad_id,)))]
    else:
        steps += [
            completion(),
            structured(thesis()),
            structured(critique()),
            structured(assessment("TRADE", (bad_id,))),
        ]
    model = ScriptedModel(steps + steps[-1:])
    result = run(model, service(delayed=40))
    assert isinstance(records(result)[0].result, ResearchToolUnavailable)
    assert result.failure and result.failure.code == "UNKNOWN_EVIDENCE_REFERENCE"
    assert result.failure.call_id == model.requests[-1].call_id
    assert result.assessment.abstention_reasons == (
        AbstentionReason.VALIDATION_FAILURE,
    )


@pytest.mark.parametrize(
    "stage", ["investigate", "draft_thesis", "critique", "final_assessment"]
)
@pytest.mark.parametrize("bad", ["{broken", "{}", '{"confidence":101}', "[]"])
def test_invalid_structured_output_no_repair_or_retry(stage: str, bad: str) -> None:
    steps: list[Any] = [
        tool(),
        completion(),
        structured(thesis()),
        structured(critique()),
        structured(assessment("ABSTAIN", ())),
    ]
    index = {"investigate": 1, "draft_thesis": 2, "critique": 3, "final_assessment": 4}[
        stage
    ]
    # The invalid reply repeats on its single re-ask; nothing is repaired.
    steps[index : index + 1] = [StructuredReply(content=bad)] * 2
    model = ScriptedModel(steps)
    result = run(model)
    assert result.failure and result.failure.code == "INVALID_MODEL_OUTPUT"
    assert len(model.requests) == index + 2
    assert result.assessment.outcome.value == "ABSTAIN"


@pytest.mark.parametrize(
    "change",
    [
        {"confidence": "20"},
        {"confidence": 100.5},
        {"confidence": True},
        {"confidence": -1},
        {"critic_response": None},
        {"critic_response": " "},
        {"stop": "90"},
        {"quantity": "1"},
    ],
)
def test_invalid_final_assessment_never_coerced(change: dict[str, Any]) -> None:
    bad = assessment("ABSTAIN", ()).model_dump(mode="json") | change
    result = run(
        ScriptedModel(
            [
                completion(),
                structured(thesis()),
                structured(critique()),
                structured(bad),
                structured(bad),
            ]
        )
    )
    assert result.failure and result.failure.code == "INVALID_MODEL_OUTPUT"
    assert result.failure.call_id == "run-1/model/0005"


@pytest.mark.parametrize(
    "reply,code,reason",
    [
        (
            FailureReply(code=ProviderFailureCode.NETWORK, message="network failure"),
            "PROVIDER_NETWORK",
            "RESEARCH_FAILURE",
        ),
        (
            FailureReply(code=ProviderFailureCode.ERROR, message="provider error"),
            "PROVIDER_ERROR",
            "RESEARCH_FAILURE",
        ),
        (
            FailureReply(code=ProviderFailureCode.TIMEOUT, message="timeout"),
            "MODEL_TIMEOUT",
            "RESEARCH_FAILURE",
        ),
        (
            FailureReply(code=ProviderFailureCode.INCOMPLETE, message="incomplete"),
            "INCOMPLETE_OUTPUT",
            "RESEARCH_FAILURE",
        ),
        (
            FailureReply(
                code=ProviderFailureCode.INVALID_ENVELOPE, message="malformed"
            ),
            "INVALID_PROVIDER_ENVELOPE",
            "VALIDATION_FAILURE",
        ),
        (RefusalReply(refusal="Refused"), "REFUSAL", "RESEARCH_FAILURE"),
    ],
)
def test_provider_failure_typed_runtime_abstention_no_retry(
    reply: ModelReply, code: str, reason: str
) -> None:
    model = ScriptedModel([reply])
    result = run(model)
    assert len(model.requests) == 1
    assert result.failure and result.failure.code.value == code
    assert result.status == "INTERRUPTED" and result.assessment_origin == "RUNTIME"
    assert result.assessment.abstention_reasons[0].value == reason
    assert (
        isinstance(result.trace[1], ModelCallRecord) and result.trace[1].reply == reply
    )


def test_failure_after_thesis_retains_validated_intermediates() -> None:
    result = run(
        ScriptedModel(
            [completion(), structured(thesis()), RefusalReply(refusal="No critique.")]
        )
    )
    assert result.investigation and result.thesis
    assert result.critique is None and result.failure


@pytest.mark.parametrize(
    "change",
    [
        {"research_run_id": " "},
        {"pair": "BTC-USD"},
        {"timestamp": T},
        {"permitted_benchmarks": ["ETH-USD"]},
    ],
)
def test_narrow_request_rejects_invalid_inputs(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        OpportunityResearchRequest.model_validate(
            dict(research_run_id="r", opportunity=opportunity()) | change
        )


@pytest.mark.parametrize(
    "offset", [timedelta(hours=-1), timedelta(hours=1), timedelta(minutes=1)]
)
def test_opportunity_t_mismatch_raises_before_invocation(offset: timedelta) -> None:
    model = ScriptedModel()
    with pytest.raises(ValueError):
        run(model, service(SimulationClock(T + offset)))
    assert model.requests == []


def test_invalid_nested_config_and_input_revalidate_before_invocation() -> None:
    model = ScriptedModel()
    forged = OpportunityResearchRequest(
        research_run_id="r", opportunity=opportunity()
    ).model_copy(
        update={"opportunity": opportunity().model_copy(update={"summary": " "})}
    )
    with pytest.raises(ValidationError):
        run(model, request=forged)
    config = ResearchWorkflowConfig(
        model=ModelConfiguration(provider="fake", model="m")
    )
    with pytest.raises(ValidationError):
        run(model, config=config.model_copy(update={"model_call_deadline_seconds": 1}))
    with pytest.raises(ValueError, match="provider"):
        run(
            model,
            config=config.model_copy(
                update={"model": ModelConfiguration(provider="other", model="m")}
            ),
        )
    assert model.requests == []


def test_clock_movement_during_model_latency_raises() -> None:
    clock = SimulationClock(T)

    class MovingModel(ScriptedModel):
        async def invoke(self, request: ModelRequest) -> ModelReply:
            await asyncio.sleep(0)
            clock.advance_to(T + timedelta(hours=1))
            return completion()

    with pytest.raises(ResearchInvariantError, match="clock moved"):
        run(MovingModel(), service(clock))


def test_model_latency_does_not_advance_clock() -> None:
    clock = SimulationClock(T)

    class DelayedModel(ScriptedModel):
        async def invoke(self, request: ModelRequest) -> ModelReply:
            await asyncio.sleep(0.001)
            return await super().invoke(request)

    result = run(DelayedModel([tool()]), service(clock))
    assert result.status == "COMPLETED" and clock.now == T


def test_clock_movement_during_tool_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = SimulationClock(T)
    original = dispatch_tool

    def dispatch(*args: Any) -> ResearchToolSuccess | ResearchToolUnavailable:
        result = original(*args)
        clock.advance_to(T + timedelta(hours=1))
        return result

    monkeypatch.setattr(workflow, "dispatch_tool", dispatch)
    with pytest.raises(ResearchInvariantError, match="clock moved"):
        run(ScriptedModel([tool()]), service(clock))


def test_clock_movement_during_cache_validation_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = SimulationClock(T)
    original = ResearchToolSuccess.model_validate

    def validate(value: Any, **kwargs: Any) -> ResearchToolSuccess:
        result = original(value, **kwargs)
        if isinstance(value, ResearchToolSuccess):
            validate.calls += 1  # type: ignore[attr-defined]
            if validate.calls == 2:  # type: ignore[attr-defined]
                clock.advance_to(T + timedelta(hours=1))
        return result

    validate.calls = 0  # type: ignore[attr-defined]
    monkeypatch.setattr(ResearchToolSuccess, "model_validate", validate)
    with pytest.raises(ResearchInvariantError, match="clock moved"):
        run(ScriptedModel([tool(), tool()]), service(clock))


def test_clock_movement_before_return_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = SimulationClock(T)
    original = package_version

    def version(name: str) -> str:
        clock.advance_to(T + timedelta(hours=1))
        return original(name)

    monkeypatch.setattr(workflow, "version", version)
    with pytest.raises(ResearchInvariantError, match="clock moved"):
        run(ScriptedModel([tool()]), service(clock))


@pytest.mark.parametrize(
    "kind", ["corrupt", "wrong_query", "wrong_time", "wrong_source"]
)
def test_corrupt_deterministic_result_raises(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    svc = service()
    result = svc.trend(BTC)
    assert isinstance(result, ResearchToolSuccess)
    if kind == "corrupt":
        result = result.model_copy(
            update={"payload": result.payload.model_copy(update={"sma20": Decimal(-1)})}
        )
    elif kind == "wrong_query":
        other = svc.momentum(BTC)
        assert isinstance(other, ResearchToolSuccess)
        result = other
    elif kind == "wrong_time":
        other = service(SimulationClock(T + timedelta(hours=1)), future=True).trend(BTC)
        assert isinstance(other, ResearchToolSuccess)
        result = other
    else:
        source = result.context.inputs[0].source.model_copy(
            update={"dataset_version": "sha256:" + "d" * 64}
        )
        context = result.context.model_copy(
            update={
                "inputs": (
                    result.context.inputs[0].model_copy(update={"source": source}),
                )
            }
        )
        from crypteo.domain.market_research import _canonical

        evidence = result.evidence.model_copy(
            update={
                "provenance": result.evidence.provenance.model_copy(
                    update={"query_params": _canonical(context)}
                )
            }
        )
        result = ResearchToolSuccess(
            context=context, payload=result.payload, evidence=evidence
        )
    monkeypatch.setattr(workflow, "dispatch_tool", lambda *args: result)
    with pytest.raises(ValueError):
        run(ScriptedModel([tool()]), svc)


def test_conflicting_same_id_envelope_raises() -> None:
    result = run(ScriptedModel([tool()]))
    record = records(result)[0]
    assert isinstance(record.result, ResearchToolSuccess)
    conflicting = ResearchToolSuccess.model_validate(
        record.result.model_copy(
            update={
                "evidence": record.result.evidence.model_copy(
                    update={"summary": "Changed neutral prose."}
                )
            }
        )
    )
    trace = result.trace + (record.model_copy(update={"result": conflicting}),)
    with pytest.raises(ResearchInvariantError, match="conflicting evidence identity"):
        collected_evidence(trace)


def test_call_deadline_cancels_model_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_wait = asyncio.wait
    limits: list[float | None] = []

    async def short_wait(tasks: Any, *, timeout: float | None = None) -> Any:
        limits.append(timeout)
        return await actual_wait(tasks, timeout=0.001)

    class HangingModel(ScriptedModel):
        cancelled = False

        async def invoke(self, request: ModelRequest) -> ModelReply:
            self.requests.append(request)
            try:
                await asyncio.sleep(100)
            finally:
                self.cancelled = True
            return completion()

    monkeypatch.setattr(asyncio, "wait", short_wait)
    model = HangingModel()
    result = run(model)
    assert result.failure and result.failure.code == "MODEL_TIMEOUT"
    assert len(limits) == 1 and limits[0] is not None and 179.9 < limits[0] <= 180
    assert model.cancelled and len(model.requests) == 1


def test_overall_deadline_interrupts(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 0.0

    def time() -> float:
        return now

    def jump(request: ModelRequest) -> ModelReply:
        nonlocal now
        now = 901
        return completion()

    monkeypatch.setattr(workflow, "monotonic", time)
    result = run(ScriptedModel([jump]))
    assert result.failure and result.failure.code == "WORKFLOW_TIMEOUT"
    assert result.assessment.abstention_reasons == (AbstentionReason.RESEARCH_FAILURE,)


def test_oversized_model_input_rejected_without_invocation() -> None:
    op = opportunity().model_copy(update={"summary": "x" * 131072})
    model = ScriptedModel()
    result = run(
        model,
        request=OpportunityResearchRequest(research_run_id="large", opportunity=op),
    )
    assert model.requests == []
    assert result.failure and result.failure.code == "INPUT_BUDGET_EXHAUSTED"
    rejected = [r for r in result.trace if isinstance(r, ModelRequestRejection)]
    assert len(rejected) == 1 and rejected[0].failure == result.failure
    assert (
        data(rejected[0].request)["context"]["request"]["opportunity"]["summary"]
        == op.summary
    )


def test_deadline_crossed_while_rendering_never_invokes_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 0.0
    original = ScriptedModel.prepare

    def prepare(model: ScriptedModel, request: ModelRequest) -> ModelRequest:
        nonlocal now
        prepared = original(model, request)
        now = 901
        return prepared

    monkeypatch.setattr(workflow, "monotonic", lambda: now)
    monkeypatch.setattr(ScriptedModel, "prepare", prepare)
    model = ScriptedModel()
    result = run(model)
    assert model.requests == []
    assert result.failure and result.failure.code == "WORKFLOW_TIMEOUT"
    assert any(isinstance(r, ModelRequestRejection) for r in result.trace)


def test_deadline_crossed_while_constructing_result_interrupts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 0.0

    def version(name: str) -> str:
        nonlocal now
        now = 901
        return package_version(name)

    monkeypatch.setattr(workflow, "monotonic", lambda: now)
    monkeypatch.setattr(workflow, "version", version)
    result = run(ScriptedModel([tool()]))
    assert result.status == "INTERRUPTED" and result.assessment_origin == "RUNTIME"
    assert result.failure and result.failure.code == "WORKFLOW_TIMEOUT"
    assert result.thesis and result.critique
    assert result.assessment.outcome.value == "ABSTAIN"
    assert result.trace[-1].model_dump()["termination_reason"] == "WORKFLOW_TIMEOUT"


@pytest.mark.parametrize(
    "capability",
    [
        "trend",
        "momentum",
        "volatility",
        "bollinger_position",
        "levels",
        "volume_context",
    ],
)
def test_each_single_asset_tool_executes_at_most_once(
    capability: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def dispatch(*args: Any) -> ResearchToolSuccess | ResearchToolUnavailable:
        calls.append(args[1].capability.value)
        return dispatch_tool(*args)

    monkeypatch.setattr(workflow, "dispatch_tool", dispatch)
    result = run(ScriptedModel([tool(capability)] * 3))
    assert result.status == "COMPLETED"
    assert calls == [capability]
    assert len(collected_evidence(result.trace)) == 1


def test_eighth_invalid_attempt_consumed_then_ninth_completes() -> None:
    model = ScriptedModel([tool()] * 7 + [tool("", {"pair": "BTC-USD"})])
    result = run(model)
    assert result.status == "COMPLETED"
    assert len(model.requests) == 12 and len(records(result)) == 8
    assert records(result)[-1].rejection is not None
    assert data(model.requests[8])["attempts_remaining"] == 0
    # An admitted invalid request consumes an attempt, so the rule follows
    # attempts_remaining: the ninth call offers no tools here too.
    assert model.requests[7].tools == tool_definitions(BTC, (ETH,))
    assert model.requests[8].tools == ()


def test_all_eight_distinct_queries_preserve_deterministic_results() -> None:
    requests = [
        tool(c)
        for c in [
            "trend",
            "momentum",
            "volatility",
            "bollinger_position",
            "levels",
            "volume_context",
        ]
    ]
    requests += [
        tool("relative_strength", {"benchmark": p}) for p in ["ETH-USD", "SOL-USD"]
    ]
    svc = service(pairs=(BTC, ETH, SOL))
    model = ScriptedModel(requests)
    result = run(model, svc)
    assert result.status == "COMPLETED" and len(model.requests) == 12
    assert len(collected_evidence(result.trace)) == 8
    for r in records(result):
        assert r.query is not None and r.cached_from is None
        assert r.result == dispatch_tool(svc, r.query)


def test_unavailable_opportunity_raises_before_invocation() -> None:
    op = opportunity().model_copy(update={"available_at": T + timedelta(hours=1)})
    model = ScriptedModel()
    with pytest.raises(ValueError, match="current and available"):
        run(
            model,
            request=OpportunityResearchRequest(
                research_run_id="unavailable-op", opportunity=op
            ),
        )
    assert model.requests == []


@pytest.mark.parametrize(
    "error",
    [
        ValueError("programmer bug"),
        RuntimeError("bug"),
        TimeoutError("untyped provider bug"),
    ],
)
def test_programmer_errors_raise_without_becoming_abstention(error: Exception) -> None:
    def fail(request: ModelRequest) -> ModelReply:
        raise error

    with pytest.raises(type(error), match=str(error)):
        run(ScriptedModel([fail]))


def test_trade_cannot_reach_financial_or_network_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from crypteo.execution import SimulatedBroker
    from crypteo.risk import RiskService
    from crypteo.trade_terms import TradeTermsService

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("research crossed authority boundary")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    for cls in (SimulatedBroker, RiskService, TradeTermsService):
        monkeypatch.setattr(cls, "__init__", forbidden)
    model = ScriptedModel([tool()])
    result = run(model)
    assert result.assessment.outcome.value == "TRADE"
    assert set(result.assessment.model_dump()) == {
        "outcome",
        "rationale",
        "supporting_evidence_ids",
        "counterevidence_ids",
        "confidence",
        "key_uncertainties",
        "critic_response",
        "abstention_reasons",
    }
    assert (
        "portfolio"
        not in inspect.signature(workflow.run_opportunity_research).parameters
    )


@pytest.mark.parametrize("completed", [True, False])
def test_result_roundtrip_nested_revalidation_immutable_origin_invariant(
    completed: bool,
) -> None:
    result = run(ScriptedModel([tool()] if completed else [RefusalReply(refusal="No")]))
    assert ResearchRunResult.model_validate_json(result.model_dump_json()) == result
    assert ResearchRunResult.model_validate(result) == result
    parent: Any = create_model("Envelope", result=(ResearchRunResult, ...))
    assert parent(result=result).result == result
    with pytest.raises(ValidationError):
        result.status = ResearchRunStatus.INTERRUPTED
    with pytest.raises(ValidationError, match="iff"):
        ResearchRunResult.model_validate(
            result.model_copy(
                update={
                    "assessment_origin": AssessmentOrigin.RUNTIME
                    if completed
                    else AssessmentOrigin.MODEL
                }
            )
        )
    forged = result.model_copy(
        update={
            "context": result.context.model_copy(
                update={
                    "request": result.context.request.model_copy(
                        update={"research_run_id": " "}
                    )
                }
            )
        }
    )
    with pytest.raises(ValidationError):
        ResearchRunResult.model_validate(forged)


def test_exact_audit_metadata_request_and_id_retention() -> None:
    metadata = ProviderMetadata.model_validate(
        dict(
            response_id="provider-response",
            request_id="provider-http-request",
            returned_model="resolved-model",
            status="completed",
            usage={"input_tokens": 8, "output_tokens": 9},
            request_json='{"observable":"request"}',
            response_json='{"observable":"response"}',
        )
    )
    reply = tool().model_copy(update={"metadata": metadata})
    model = ScriptedModel([reply])
    result = run(model)
    calls = tuple(r for r in result.trace if isinstance(r, ModelCallRecord))
    assert calls[0].reply == reply
    assert calls[0].request == model.requests[0]
    assert calls[0].request.call_id == "run-1/model/0001"
    assert calls[0].reply.metadata.response_id == "provider-response"
    assert result.configuration.budget.policy_id == "bounded_research_v2"
    assert (
        result.provider == model.identity
        and result.context.request.research_run_id == "run-1"
    )
    assert [r.max_output_tokens for r in model.requests] == [
        8192,
        8192,
        16384,
        16384,
        16384,
    ]
    assert model.requests[0].configuration.model == "explicit-test-model"


def test_fresh_run_ids_are_explicit_and_distinct_for_same_context() -> None:
    first = run(ScriptedModel([tool()]))
    second = run(
        ScriptedModel([tool()]),
        request=OpportunityResearchRequest(
            research_run_id="fresh-2", opportunity=opportunity()
        ),
    )
    assert first.context.request.opportunity == second.context.request.opportunity
    assert collected_evidence(first.trace) == collected_evidence(second.trace)
    assert isinstance(second.trace[1], ModelCallRecord)
    assert second.trace[1].request.call_id == "fresh-2/model/0001"
