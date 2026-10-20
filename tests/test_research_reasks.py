"""Bounded re-ask after an invalid structured reply: workflow, journal and Claude."""

import asyncio
import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx2
import pytest
from research_support import (
    CODE,
    ETH,
    ScriptedModel,
    anthropic_research,
    assessment,
    completion,
    critique,
    opportunity,
    run,
    service,
    structured,
    thesis,
    tool,
    workflow_config,
)
from test_research_persistence import ObservedModel, execute

from crypteo.domain.research_workflow import (
    ModelCallRecord,
    NodeTransition,
    OpportunityResearchRequest,
    ResearchFailureCode,
    ResearchRunResult,
    ToolRecord,
)
from crypteo.research_journal import (
    ResearchStorageError,
    canonical_bytes,
    framed_digest,
    project,
)
from crypteo.research_model import (
    FailureReply,
    ModelReply,
    ModelRequest,
    ProviderFailureCode,
    RefusalReply,
    StructuredReply,
    render_json,
)
from crypteo.research_persistence import ResearchStore
from crypteo.research_tools import tool_definitions
from crypteo.research_validation import ResearchValidationError
from crypteo.research_workflow import ResearchState, _ResearchRuntime

STAGES = ("investigate", "draft_thesis", "critique", "final_assessment")
EMPTY = {
    "investigate": {"summary": "", "relevant_evidence_ids": [], "remaining_gaps": []},
    "draft_thesis": {
        "summary": "",
        "bull_case": "",
        "bear_case": " ",
        "supporting_evidence_ids": [],
        "counterevidence_ids": [],
        "assumptions": [],
        "invalidation_conditions": [],
        "uncertainties": [],
    },
    "critique": {
        "summary": "",
        "challenged_claims": [],
        "missing_evidence": [""],
        "contradictions": [],
        "unsupported_assumptions": [],
        "evidence_quality_concerns": [],
        "unresolved_uncertainties": [],
    },
    "final_assessment": {
        "outcome": "ABSTAIN",
        "rationale": "",
        "supporting_evidence_ids": [],
        "counterevidence_ids": [],
        "confidence": None,
        "key_uncertainties": [],
        "critic_response": None,
        "abstention_reasons": ["INSUFFICIENT_EVIDENCE"],
    },
}
PROBLEMS = {
    "investigate": ["summary: text must be nonblank"],
    "draft_thesis": [
        "summary: text must be nonblank",
        "bull_case: text must be nonblank",
        "bear_case: text must be nonblank",
    ],
    "critique": [
        "summary: text must be nonblank",
        "missing_evidence.0: text must be nonblank",
    ],
    "final_assessment": ["rationale: text must be nonblank"],
}


def empty(stage: str) -> StructuredReply:
    return StructuredReply(content=json.dumps(EMPTY[stage]))


def data(request: ModelRequest) -> dict[str, Any]:
    research = json.loads(request.messages[-1].content)["research_data"]
    assert isinstance(research, dict)
    return research


def evidence_ids(request: ModelRequest) -> tuple[str, ...]:
    return tuple(e["evidence_id"] for e in data(request)["evidence"])


type Step = ModelReply | Callable[[ModelRequest], ModelReply]


def valid(stage: str) -> Step:
    """A valid reply for ``stage`` citing whatever evidence the request carries."""

    def reply(request: ModelRequest) -> ModelReply:
        ids = evidence_ids(request)
        return {
            "investigate": completion(ids),
            "draft_thesis": structured(thesis(ids)),
            "critique": structured(critique()),
            "final_assessment": structured(assessment("TRADE", ids)),
        }[stage]

    return reply


def graph(*replies: tuple[str, Step]) -> list[Step]:
    """One tool call, then each stage's given replies, or one valid reply."""
    steps: list[Step] = [tool()]
    for stage in STAGES:
        steps += [step for name, step in replies if name == stage] or [valid(stage)]
    return steps


def reask_steps(stage: str, second: Step | None = None) -> list[Step]:
    return graph((stage, empty(stage)), (stage, second or valid(stage)))


def transitions(result: ResearchRunResult) -> list[tuple[str, str]]:
    return [(r.source, r.target) for r in result.trace if isinstance(r, NodeTransition)]


@pytest.mark.parametrize("stage", STAGES)
def test_empty_reply_then_valid_reask_completes(stage: str) -> None:
    model = ScriptedModel(reask_steps(stage))
    result = run(model)
    assert result.status == "COMPLETED" and result.failure is None
    index = 1 + STAGES.index(stage)  # One tool call precedes investigation.
    first, reask = model.requests[index], model.requests[index + 1]
    assert first.stage == reask.stage == stage
    assert data(reask).pop("output_rejection") == {
        "call_id": first.call_id,
        "problems": PROBLEMS[stage],
    }
    # Otherwise the stage's normal request, under the normal tools rule.
    assert "output_rejection" not in data(first)
    assert {k: v for k, v in data(reask).items() if k != "output_rejection"} == data(
        first
    )
    assert reask.messages[0] == first.messages[0]
    assert reask.prompt_identity == first.prompt_identity
    assert reask.output_schema == first.output_schema
    assert reask.tools == first.tools
    assert reask.tools == (
        tool_definitions(opportunity().pair, (ETH,)) if index == 1 else ()
    )
    # The rejected reply stays in the trace as an ordinary model call record.
    calls = [r for r in result.trace if isinstance(r, ModelCallRecord)]
    assert calls[index].reply == empty(stage)
    assert (stage, stage) in transitions(result)
    assert len(model.requests) == 6
    # Later stages see only the valid re-asked output.
    for request in model.requests[index + 2 :]:
        assert "output_rejection" not in data(request)
    assert result.investigation is not None
    assert result.investigation.summary == "Investigator completed."
    if stage == "investigate":
        assert data(model.requests[3])["investigation_completion"] == (
            result.investigation.model_dump(mode="json")
        )


@pytest.mark.parametrize("stage", STAGES)
def test_reask_carries_tool_definitions_only_without_tools(stage: str) -> None:
    model = ScriptedModel(reask_steps(stage))
    assert run(model).status == "COMPLETED"
    index = 1 + STAGES.index(stage)
    first, reask = model.requests[index], model.requests[index + 1]
    assert "output_rejection" in data(reask)
    offered = tool_definitions(opportunity().pair, (ETH,))
    for request in (first, reask):
        if stage == "investigate":
            # One tool call precedes it, so seven attempts remain.
            assert data(request)["attempts_remaining"] == 7
            assert request.tools == offered
            assert "tool_definitions" not in data(request)
        else:
            assert request.tools == ()
            assert data(request)["tool_definitions"] == [
                {"name": t.name, "description": t.description} for t in offered
            ]


@pytest.mark.parametrize("stage", STAGES)
def test_empty_reask_interrupts_without_further_calls(stage: str) -> None:
    model = ScriptedModel(reask_steps(stage, empty(stage)))
    result = run(model)
    index = 1 + STAGES.index(stage)
    assert len(model.requests) == index + 2
    assert result.status == "INTERRUPTED" and result.assessment_origin == "RUNTIME"
    assert result.failure is not None
    assert result.failure.code == "INVALID_MODEL_OUTPUT"
    assert result.failure.call_id == model.requests[-1].call_id
    assert result.assessment.outcome == "ABSTAIN"
    assert transitions(result)[-1] == (stage, "__end__")


def test_two_reasks_succeed_and_a_third_invalid_output_ends_the_run() -> None:
    steps = graph(
        ("investigate", empty("investigate")),
        ("investigate", valid("investigate")),
        ("draft_thesis", empty("draft_thesis")),
        ("draft_thesis", valid("draft_thesis")),
    )
    model = ScriptedModel(steps)
    completed = run(model)
    assert completed.status == "COMPLETED" and len(model.requests) == 7
    third = graph(
        ("investigate", empty("investigate")),
        ("investigate", valid("investigate")),
        ("draft_thesis", empty("draft_thesis")),
        ("draft_thesis", valid("draft_thesis")),
        ("critique", empty("critique")),
    )
    model = ScriptedModel(third)
    result = run(model)
    assert len(model.requests) == 6  # The critique is not re-asked.
    assert result.failure is not None
    assert result.failure.code == "INVALID_MODEL_OUTPUT"
    assert result.failure.call_id == model.requests[-1].call_id
    assert model.requests[-1].stage == "critique"
    assert result.thesis is not None and result.critique is None


def test_unknown_evidence_reference_is_reasked() -> None:
    model = ScriptedModel(
        [tool(), completion(("fabricated-id",)), completion(("other-id", "x"))]
    )
    result = run(model)
    assert data(model.requests[2])["output_rejection"]["problems"] == [
        "relevant_evidence_ids: unknown evidence id fabricated-id"
    ]
    assert result.failure is not None
    assert result.failure.code == "UNKNOWN_EVIDENCE_REFERENCE"
    assert result.failure.call_id == model.requests[2].call_id
    bad = assessment("ABSTAIN", ()).model_dump(mode="json") | {
        "counterevidence_ids": ["fabricated-id"],
        "critic_response": None,
    }
    model = ScriptedModel(
        graph(
            ("final_assessment", structured(bad)),
            ("final_assessment", valid("final_assessment")),
        )
    )
    assert run(model).status == "COMPLETED"
    assert data(model.requests[-1])["output_rejection"]["problems"] == [
        "counterevidence_ids: unknown evidence id fabricated-id",
        "critic_response: required for every outcome",
    ]


@pytest.mark.parametrize(
    "reply,code",
    [
        (FailureReply(code=ProviderFailureCode.ERROR, message="err"), "PROVIDER_ERROR"),
        (FailureReply(code=ProviderFailureCode.TIMEOUT, message="t"), "MODEL_TIMEOUT"),
        (
            FailureReply(code=ProviderFailureCode.INCOMPLETE, message="cut"),
            "INCOMPLETE_OUTPUT",
        ),
        (
            FailureReply(code=ProviderFailureCode.INVALID_OUTPUT, message="bad"),
            "INVALID_MODEL_OUTPUT",
        ),
        (RefusalReply(refusal="no"), "REFUSAL"),
        (tool(), "INVALID_MODEL_OUTPUT"),
    ],
)
@pytest.mark.parametrize("stage", ["draft_thesis", "final_assessment"])
def test_non_structured_failures_are_not_reasked(
    stage: str, reply: ModelReply, code: str
) -> None:
    model = ScriptedModel(graph((stage, reply)))
    result = run(model)
    assert len(model.requests) == 2 + STAGES.index(stage)
    assert result.failure is not None and result.failure.code == code
    assert all("output_rejection" not in data(r) for r in model.requests)


def test_invalid_tool_requests_and_tool_budget_are_not_reasked() -> None:
    bad = tool("unknown_tool")
    model = ScriptedModel([bad, bad])
    assert run(model).failure.code == "INVALID_TOOL_REQUEST"  # type: ignore[union-attr]
    assert len(model.requests) == 2
    model = ScriptedModel([tool()] * 9)
    assert run(model).failure.code == "TOOL_BUDGET_EXHAUSTED"  # type: ignore[union-attr]
    assert len(model.requests) == 9
    assert all("output_rejection" not in data(r) for r in model.requests)


def max_length_steps() -> list[Step]:
    """Eight tools, nine investigations and both re-asks: fourteen model calls."""
    return [tool()] * 8 + [
        empty("investigate"),
        valid("investigate"),
        empty("draft_thesis"),
        valid("draft_thesis"),
        valid("critique"),
        valid("final_assessment"),
    ]


def test_budget_edges_per_stage_limits_ignore_reasks_up_to_fourteen_calls() -> None:
    model = ScriptedModel(max_length_steps())
    result = run(model)
    assert result.status == "COMPLETED"
    assert len(model.requests) == result.configuration.budget.max_model_calls == 14
    investigations = [r for r in model.requests if r.stage == "investigate"]
    # Nine budgeted investigation calls plus one re-ask, at attempts_remaining 0.
    assert len(investigations) == 10
    assert data(investigations[9])["attempts_remaining"] == 0
    assert data(investigations[9])["output_rejection"]["call_id"] == (
        investigations[8].call_id
    )
    assert investigations[8].tools == investigations[9].tools == ()
    # 22 node executions fit the unchanged LangGraph recursion limit of 24.
    assert len(transitions(result)) == 23
    assert result.configuration.budget.recursion_limit == 24


def test_fourteen_call_cap_holds_even_for_a_due_reask() -> None:
    model = ScriptedModel(max_length_steps())
    result = run(model)
    calls = [r for r in result.trace if isinstance(r, ModelCallRecord)]
    assert len(calls) == 14
    runtime = _ResearchRuntime(service(), model, result.configuration)
    state: ResearchState = {
        "context": result.context,
        "trace": result.trace,
        "action": None,
        "investigation": result.investigation,
        "thesis": result.thesis,
        "critique": result.critique,
        "assessment": None,
        "failure": None,
        "rejected": calls[-1],
    }
    refused = runtime.reask_or_fail(state, ResearchFailureCode.INVALID_MODEL_OUTPUT)
    assert refused["failure"] is not None and refused["rejected"] is None
    after, reply = asyncio.run(runtime.call(state, "final_assessment"))
    assert reply is None and len(model.requests) == 14
    assert after["failure"] is not None
    assert after["failure"].code == "MODEL_BUDGET_EXHAUSTED"


def strip_rejection(request: ModelRequest) -> ModelRequest:
    content = json.loads(request.messages[-1].content)
    content["research_data"].pop("output_rejection")
    messages = (
        *request.messages[:-1],
        request.messages[-1].model_copy(update={"content": render_json(content)}),
    )
    return request.model_copy(update={"messages": messages})


def test_in_memory_trace_rejects_reask_relationships_out_of_order() -> None:
    result = run(ScriptedModel(reask_steps("draft_thesis")))
    ResearchRunResult.model_validate(result.model_dump())
    index = next(
        i
        for i, r in enumerate(result.trace)
        if isinstance(r, ModelCallRecord) and "output_rejection" in data(r.request)
    )
    record = result.trace[index]
    assert isinstance(record, ModelCallRecord)
    forged = [
        record.model_copy(update={"request": strip_rejection(record.request)}),
    ]
    rejection = data(record.request)["output_rejection"]
    for problems in ([], ["summary: blank"], rejection["problems"][:1]):
        content = json.loads(record.request.messages[-1].content)
        content["research_data"]["output_rejection"]["problems"] = problems
        message = record.request.messages[-1].model_copy(
            update={"content": render_json(content)}
        )
        request = record.request.model_copy(
            update={"messages": (*record.request.messages[:-1], message)}
        )
        forged.append(record.model_copy(update={"request": request}))
    for replacement in forged:
        trace = (*result.trace[:index], replacement, *result.trace[index + 1 :])
        with pytest.raises(ValueError, match="output rejection differs"):
            ResearchRunResult.model_validate(
                result.model_copy(update={"trace": trace}).model_dump()
            )


PATHS: dict[str, list[Step]] = {
    **{f"{stage}_reasked": reask_steps(stage) for stage in STAGES},
    **{f"{stage}_reask_invalid": reask_steps(stage, empty(stage)) for stage in STAGES},
    "two_reasks_then_invalid": graph(
        ("investigate", empty("investigate")),
        ("investigate", valid("investigate")),
        ("critique", empty("critique")),
        ("critique", valid("critique")),
        ("final_assessment", empty("final_assessment")),
    ),
    "unknown_reference": [
        tool(),
        completion(("fabricated-id",)),
        valid("investigate"),
        valid("draft_thesis"),
        valid("critique"),
        valid("final_assessment"),
    ],
    "fourteen_calls": max_length_steps(),
}


@pytest.mark.parametrize("path", PATHS)
def test_durable_reask_paths_reload_and_inspect(tmp_path: Path, path: str) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    model = ObservedModel(list(PATHS[path]))
    artifact = asyncio.run(execute(store, model))
    expected = "INTERRUPTED" if "invalid" in path else "COMPLETED"
    assert artifact.result.status == expected
    assert any("output_rejection" in data(r) for r in model.requests)
    assert store.load_terminal("run-1") == artifact
    assert ResearchStore(store.path).load_terminal("run-1") == artifact
    inspection = store.inspect_run("run-1")
    assert inspection.state == expected and inspection.pending_boundaries == ()
    prepared = [
        e.event.request
        for e in inspection.events
        if e.event.kind == "MODEL_REQUEST_PREPARED"
    ]
    assert prepared == model.requests
    # The same validated trace as the in-memory entrypoint produces.
    memory = run(ScriptedModel(list(PATHS[path])))
    assert memory.trace == artifact.result.trace


def tamper(store: ResearchStore, mutate: Callable[[list[dict[str, Any]]], int]) -> None:
    """Rewrite one event with a valid checksum; ``mutate`` returns its index."""
    with closing(sqlite3.connect(store.path, isolation_level=None)) as connection:
        rows = connection.execute(
            "SELECT envelope_json FROM research_events_v1 ORDER BY sequence"
        ).fetchall()
        events = [json.loads(raw) for (raw,) in rows]
        value = events[mutate(events)]
        raw = canonical_bytes(value)
        checksum = framed_digest("crypteo.research.event.v1", raw, b"absent", b"")
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='research_events_v1_update'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER research_events_v1_update")
        connection.execute(
            "UPDATE research_events_v1 SET envelope_json=?, checksum=? "
            "WHERE sequence=?",
            (raw, checksum, value["sequence"]),
        )
        connection.execute(trigger)


def prepared_indexes(events: list[dict[str, Any]]) -> list[int]:
    return [
        i
        for i, e in enumerate(events)
        if e["event"]["kind"] == "MODEL_REQUEST_PREPARED"
    ]


def edit_data(event: dict[str, Any], change: Callable[[dict[str, Any]], None]) -> None:
    request = event["event"]["request"]
    content = json.loads(request["messages"][-1]["content"])
    change(content["research_data"])
    request["messages"][-1]["content"] = render_json(content)
    request["prepared_input_json"] = render_json(
        request | {"prepared_input_json": None}
    )


@pytest.mark.parametrize("mutation", ["problems", "missing", "extra", "call_id"])
def test_loaders_reject_tampered_reask_requests(tmp_path: Path, mutation: str) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    asyncio.run(execute(store, ObservedModel(reask_steps("draft_thesis"))))

    def mutate(events: list[dict[str, Any]]) -> int:
        prepared = prepared_indexes(events)
        # Calls: tool, investigation, empty thesis, re-ask, critique, assessment.
        reask, critique_call = prepared[3], prepared[4]
        if mutation == "problems":
            edit_data(
                events[reask],
                lambda d: d["output_rejection"].update(problems=["summary: blank"]),
            )
        elif mutation == "missing":
            edit_data(events[reask], lambda d: d.pop("output_rejection"))
        elif mutation == "call_id":
            edit_data(
                events[reask],
                lambda d: d["output_rejection"].update(call_id="run-1/model/0002"),
            )
        else:
            rejection = {"call_id": "run-1/model/0004", "problems": ["summary: x"]}
            edit_data(
                events[critique_call], lambda d: d.update(output_rejection=rejection)
            )
            return critique_call
        return reask

    tamper(store, mutate)
    for load in (store.inspect_run, store.load_terminal):
        with pytest.raises(ResearchStorageError) as raised:
            load("run-1")
        assert isinstance(raised.value.__cause__, ValueError)
        assert "request differs from accumulated context/policy" in str(
            raised.value.__cause__
        )


def test_journal_rejects_terminating_when_a_reask_was_due(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    asyncio.run(
        execute(store, ObservedModel(reask_steps("critique", empty("critique"))))
    )
    events = store.inspect_run("run-1").events
    finished = [
        i for i, e in enumerate(events) if e.event.kind == "MODEL_CALL_FINISHED"
    ]
    # Drop the re-ask: end directly after the first invalid critique reply.
    first_invalid = finished[3]
    reask_start = first_invalid + 2  # Skip the critique -> critique transition.
    tail = events[-2:]  # Terminal transition and RUN_FINISHED.
    forged = (*events[: first_invalid + 1], *tail)
    renumbered = tuple(
        e.model_copy(update={"sequence": i}) for i, e in enumerate(forged)
    )
    assert events[first_invalid + 1].event.kind == "NODE_TRANSITION"
    assert events[reask_start].event.kind == "MODEL_REQUEST_PREPARED"
    with pytest.raises(ResearchValidationError, match="termination contradicts"):
        project(renumbered)
    project(events)


def _claude_reask_handler(bodies: list[dict[str, Any]]) -> Callable[..., Any]:
    """Claude: tool, completion, blank thesis, valid re-asked thesis, rest valid."""

    def handle(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        bodies.append(body)
        research = json.loads(body["messages"][-1]["content"])["research_data"]
        ids = tuple(e["evidence_id"] for e in research["evidence"])
        n = len(bodies)
        message: dict[str, Any] = {
            "id": f"msg-reask-{n}",
            "type": "message",
            "role": "assistant",
            "model": body["model"],
            "stop_sequence": None,
            "usage": {"input_tokens": 8, "output_tokens": 12},
            "stop_reason": "end_turn",
        }
        if n == 1:
            tool_use = {"type": "tool_use", "id": "toolu-1", "name": "trend"}
            message["content"] = [tool_use | {"input": {}}]
            message["stop_reason"] = "tool_use"
            return httpx2.Response(200, json=message, headers={"request-id": "req-1"})
        output = {
            2: completion(ids).content,
            3: json.dumps(EMPTY["draft_thesis"]),
            4: structured(thesis(ids)).content,
            5: structured(critique()).content,
            6: structured(assessment("TRADE", ids)).content,
        }[n]
        message["content"] = [
            {"type": "thinking", "thinking": "opaque", "signature": "sig"},
            {"type": "text", "text": output},
        ]
        return httpx2.Response(200, json=message, headers={"request-id": f"req-{n}"})

    return handle


def test_claude_reask_completes_and_reloads(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    bodies: list[dict[str, Any]] = []
    artifact = anthropic_research(
        _claude_reask_handler(bodies),
        OpportunityResearchRequest(research_run_id="run-1", opportunity=opportunity()),
        store,
    )
    assert len(bodies) == 6
    first, reask = (json.loads(b["messages"][-1]["content"]) for b in bodies[2:4])
    assert "output_rejection" not in first["research_data"]
    assert reask["research_data"].pop("output_rejection") == {
        "call_id": "run-1/model/0003",
        "problems": PROBLEMS["draft_thesis"],
    }
    assert reask == first
    assert "tools" not in bodies[3]
    assert bodies[3]["system"] == bodies[2]["system"]
    result = artifact.result
    assert result.status == "COMPLETED"
    assert result.thesis == thesis(result.investigation.relevant_evidence_ids)  # type: ignore[union-attr]
    assert sum(isinstance(r, ToolRecord) for r in result.trace) == 1
    assert store.load_terminal("run-1") == artifact
    inspection = store.inspect_run("run-1")
    assert inspection.state == "COMPLETED" and inspection.pending_boundaries == ()
    assert workflow_config("anthropic") == result.configuration
    assert result.code == CODE
