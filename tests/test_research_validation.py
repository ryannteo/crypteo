"""Compact model views of evidence and tool history, and their validation."""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from research_support import (
    BTC,
    CODE,
    ScriptedModel,
    opportunity,
    run,
    service,
    tool,
    workflow_config,
)
from test_research_journal_claude import rewrite
from test_research_persistence import ObservedModel

from crypteo.domain.market_research import ResearchToolSuccess
from crypteo.domain.research_journal import EventEnvelope, ModelRequestPrepared
from crypteo.domain.research_workflow import (
    ModelCallRecord,
    OpportunityResearchRequest,
    ResearchRunResult,
    ToolRecord,
)
from crypteo.research_journal import ResearchStorageError
from crypteo.research_model import ModelRequest, ToolReply, render_json
from crypteo.research_persistence import ResearchStore
from crypteo.research_validation import (
    ResearchInvariantError,
    ResearchValidationError,
    collected_evidence,
    evidence_view,
    request_history_view,
)
from crypteo.research_workflow import run_durable_opportunity_research

BTC_TOOLS = (
    "trend",
    "momentum",
    "volatility",
    "bollinger_position",
    "levels",
    "volume_context",
)
VIEW_KEYS = {
    "evidence_id",
    "category",
    "evidence_type",
    "source",
    "observed_at",
    "available_at",
    "payload",
}


def records(result: ResearchRunResult) -> tuple[ToolRecord, ...]:
    return tuple(r for r in result.trace if isinstance(r, ToolRecord))


def research(request: ModelRequest) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(request.messages[-1].content)["research_data"]
    return value


def leaves(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [leaf for item in value.values() for leaf in leaves(item)]
    if isinstance(value, list):
        return [leaf for item in value for leaf in leaves(item)]
    return [value]


def test_evidence_view_is_exact_subset_of_real_envelope() -> None:
    success = service().trend(BTC)
    assert isinstance(success, ResearchToolSuccess)
    full = success.evidence.model_dump(mode="json")
    view = evidence_view(success.evidence)
    assert set(view) == VIEW_KEYS
    assert view == {key: full[key] for key in VIEW_KEYS}
    assert view["payload"] == full["payload"]
    assert view["payload"]["sma20"] == "100.0000000000000000000001"


def test_history_view_success_cache_and_rejections() -> None:
    model = ScriptedModel(
        [
            tool("trend"),
            tool("unknown"),
            tool("trend"),
            ToolReply(requests=(tool("trend").requests[0], tool("levels").requests[0])),
        ]
    )
    # The second invalid request interrupts the run after it is recorded.
    result = run(model)
    trend, unknown, cached, double = records(result)
    assert isinstance(trend.result, ResearchToolSuccess)
    assert unknown.rejection is not None and double.rejection is not None
    evidence_id = trend.result.evidence.evidence_id
    assert request_history_view(records(result)) == [
        {
            "attempt": 1,
            "requests": [{"name": "trend", "arguments_json": "{}"}],
            "admitted": True,
            "status": "success",
            "evidence_id": evidence_id,
        },
        {
            "attempt": 2,
            "requests": [{"name": "unknown", "arguments_json": "{}"}],
            "admitted": True,
            "status": "rejected",
            "code": "INVALID_TOOL_REQUEST",
            "message": unknown.rejection.message,
        },
        {
            "attempt": 3,
            "requests": [{"name": "trend", "arguments_json": "{}"}],
            "admitted": True,
            "status": "success",
            "evidence_id": evidence_id,
            "cached_from_attempt": 1,
        },
        {
            "attempt": 4,
            "requests": [
                {"name": "trend", "arguments_json": "{}"},
                {"name": "levels", "arguments_json": "{}"},
            ],
            "admitted": True,
            "status": "rejected",
            "code": "INVALID_TOOL_REQUEST",
            "message": double.rejection.message,
        },
    ]
    assert research(model.requests[-1])["request_history"] == request_history_view(
        records(result)[:3]
    )


def test_history_view_unavailable_role_and_reason() -> None:
    result = run(ScriptedModel([tool()], outcome="ABSTAIN"), service(delayed=40))
    assert request_history_view(records(result)) == [
        {
            "attempt": 1,
            "requests": [{"name": "trend", "arguments_json": "{}"}],
            "admitted": True,
            "status": "unavailable",
            "role": "subject",
            "reason": "HISTORY_GAP",
        }
    ]


def test_history_view_budget_exhausted_rejection_is_not_admitted() -> None:
    result = run(ScriptedModel([tool()] * 9))
    view = request_history_view(records(result))
    rejection = records(result)[-1].rejection
    assert rejection is not None
    assert view[-1] == {
        "attempt": 9,
        "requests": [{"name": "trend", "arguments_json": "{}"}],
        "admitted": False,
        "status": "rejected",
        "code": "TOOL_BUDGET_EXHAUSTED",
        "message": rejection.message,
    }
    assert [entry["cached_from_attempt"] for entry in view[1:8]] == [1] * 7


@pytest.mark.parametrize("target", ["absent", "self", "later"])
def test_history_view_dangling_cache_reference_raises(target: str) -> None:
    first, second = records(run(ScriptedModel([tool(), tool("momentum")])))
    reference = {
        "absent": "run-1/tool/0009",
        "self": first.attempt_id,
        "later": second.attempt_id,
    }[target]
    dangling = first.model_copy(update={"cached_from": reference})
    with pytest.raises(ResearchInvariantError, match="no earlier attempt"):
        request_history_view((dangling, second))


def test_btc_only_requests_omit_envelope_detail_and_keep_evidence() -> None:
    model = ScriptedModel([tool(name) for name in BTC_TOOLS])
    result = run(model, service(pairs=(BTC,)))
    assert result.status == "COMPLETED" and len(model.requests) == 10
    for request in model.requests:
        content = request.messages[-1].content
        for hidden in ("provenance", "query_params", "window_digest"):
            assert hidden not in content
    final = model.requests[-1].messages[-1].content
    evidence = collected_evidence(result.trace)
    assert len(evidence) == len(BTC_TOOLS)
    for item in evidence:
        assert item.evidence_id in final
        for leaf in leaves(item.model_dump(mode="json")["payload"]):
            assert render_json(leaf) in final


def test_btc_only_durable_run_round_trips(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    model = ObservedModel([tool(name) for name in BTC_TOOLS])
    artifact = asyncio.run(
        run_durable_opportunity_research(
            OpportunityResearchRequest(
                research_run_id="run-1", opportunity=opportunity()
            ),
            service(pairs=(BTC,)),
            model,
            workflow_config(model.provider),
            store=store,
            code=CODE,
        )
    )
    assert artifact.result.status == "COMPLETED"
    assert store.load_terminal("run-1") == artifact
    assert ResearchStore(store.path).load_terminal("run-1") == artifact
    assert store.inspect_run("run-1").state == "COMPLETED"


# The fourth request sees two evidence items and a cache hit at attempt 3.
SCRIPT = ("trend", "momentum", "trend")
TAMPERED_CALL = "run-1/model/0004"
EVIDENCE_MISMATCH = "retained model evidence differs from collected evidence"
HISTORY_MISMATCH = "retained request history differs from tool trace"


type Forgery = Callable[[dict[str, Any], tuple[Any, ...]], None]


def full_envelopes(data: dict[str, Any], trace: tuple[Any, ...]) -> None:
    data["evidence"] = [e.model_dump(mode="json") for e in collected_evidence(trace)]


def history(index: int, **changes: Any) -> Forgery:
    return lambda data, trace: data["request_history"][index].update(changes)


MUTATIONS: dict[str, tuple[str, Forgery]] = {
    "full_evidence": (EVIDENCE_MISMATCH, full_envelopes),
    "reordered_evidence": (
        EVIDENCE_MISMATCH,
        lambda data, trace: data.update(evidence=data["evidence"][::-1]),
    ),
    "status": (HISTORY_MISMATCH, history(0, status="unavailable")),
    "evidence_id": (HISTORY_MISMATCH, history(0, evidence_id="sha256:" + "0" * 64)),
    "cached_from_attempt": (HISTORY_MISMATCH, history(2, cached_from_attempt=2)),
    "admitted_type": (HISTORY_MISMATCH, history(0, admitted=1)),
}


def unchanged(data: dict[str, Any], trace: tuple[Any, ...]) -> None:
    """Control: a re-render without a change reproduces the request exactly."""


def trace_json(trace: list[Any]) -> list[Any]:
    return [r.model_dump(mode="json") for r in trace]


def tampered(
    request: ModelRequest, mutation: str, trace: tuple[Any, ...]
) -> ModelRequest:
    """Re-render one request's research data as a self-consistent forgery."""
    content = json.loads(request.messages[-1].content)
    data = content["research_data"]
    assert len(data["evidence"]) == 2 and len(data["request_history"]) == 3
    (MUTATIONS[mutation][1] if mutation in MUTATIONS else unchanged)(data, trace)
    message = request.messages[-1].model_copy(update={"content": render_json(content)})
    changed = request.model_copy(
        update={"messages": request.messages[:-1] + (message,)}
    )
    return changed.model_copy(
        update={
            "prepared_input_json": render_json(
                changed.model_copy(update={"prepared_input_json": None})
            )
        }
    )


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_in_memory_result_rejects_forged_request_views(mutation: str) -> None:
    result = run(ScriptedModel([tool(name) for name in SCRIPT]))
    trace = list(result.trace)
    index = next(
        i
        for i, r in enumerate(trace)
        if isinstance(r, ModelCallRecord) and r.request.call_id == TAMPERED_CALL
    )
    record = trace[index]
    assert isinstance(record, ModelCallRecord)
    trace[index] = record.model_copy(
        update={"request": tampered(record.request, mutation, tuple(trace[:index]))}
    )
    forged = {**dict(result), "trace": tuple(trace)}
    with pytest.raises(ValidationError, match=MUTATIONS[mutation][0]):
        ResearchRunResult.model_validate(forged)
    with pytest.raises(ValidationError, match=MUTATIONS[mutation][0]):
        ResearchRunResult.model_validate_json(
            render_json({**result.model_dump(mode="json"), "trace": trace_json(trace)})
        )


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_journal_rejects_forged_request_views(tmp_path: Path, mutation: str) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    model = ObservedModel([tool(name) for name in SCRIPT])
    artifact = asyncio.run(
        run_durable_opportunity_research(
            OpportunityResearchRequest(
                research_run_id="run-1", opportunity=opportunity()
            ),
            service(),
            model,
            workflow_config(model.provider),
            store=store,
            code=CODE,
        )
    )
    trace = artifact.result.trace
    index = next(
        i
        for i, r in enumerate(trace)
        if isinstance(r, ModelCallRecord) and r.request.call_id == TAMPERED_CALL
    )
    sequence = next(
        e.sequence
        for e in store.inspect_run("run-1").events
        if isinstance(e.event, ModelRequestPrepared)
        and e.event.request.call_id == TAMPERED_CALL
    )

    def forge(envelope: EventEnvelope) -> EventEnvelope:
        assert isinstance(envelope.event, ModelRequestPrepared)
        request = tampered(envelope.event.request, mutation, trace[:index])
        return envelope.model_copy(
            update={"event": envelope.event.model_copy(update={"request": request})}
        )

    rewrite(store, sequence, forge)
    for load in (store.load_terminal, store.inspect_run):
        with pytest.raises(ResearchStorageError) as raised:
            load("run-1")
        assert isinstance(raised.value.__cause__, ResearchValidationError)
        assert str(raised.value.__cause__) == (
            "request differs from accumulated context/policy"
        )


def test_unchanged_rerender_reproduces_request() -> None:
    result = run(ScriptedModel([tool(name) for name in SCRIPT]))
    trace = list(result.trace)
    index = next(
        i
        for i, r in enumerate(trace)
        if isinstance(r, ModelCallRecord) and r.request.call_id == TAMPERED_CALL
    )
    record = trace[index]
    assert isinstance(record, ModelCallRecord)
    assert tampered(record.request, "unchanged", tuple(trace[:index])) == record.request
