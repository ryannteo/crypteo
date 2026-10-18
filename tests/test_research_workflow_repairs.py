"""Adversarial regressions for deadline, parser and exact input-size boundaries."""

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from anthropic import AsyncAnthropic
from research_support import (
    CODE,
    MODEL,
    ScriptedModel,
    anthropic_client,
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

import crypteo.anthropic_research as anthropic_adapter
import crypteo.research_workflow as workflow
from crypteo.anthropic_research import AnthropicResearchModel, render_anthropic_request
from crypteo.domain.research_workflow import (
    ModelCallRecord,
    ModelRequestRejection,
    OpportunityResearchRequest,
    ResearchRunResult,
    ToolRecord,
)
from crypteo.research_model import (
    ModelReply,
    ModelRequest,
    StructuredReply,
    ToolReply,
    ToolRequest,
    render_json,
)
from crypteo.research_workflow import run_opportunity_research


@pytest.fixture
def short_deadlines(monkeypatch: pytest.MonkeyPatch) -> Callable[[float, float], None]:
    original = workflow._ResearchRuntime.__init__

    def configure(per_call: float = 0.025, overall: float = 0.5) -> None:
        def initialize(self: workflow._ResearchRuntime, *args: Any) -> None:
            original(self, *args)
            # Runtime-only test injection; the public locked configuration remains
            # validated and unchanged. No test waits for the production 180 seconds.
            self.config = self.config.model_copy(
                update={"model_call_deadline_seconds": per_call}
            )
            self.deadline = time.monotonic() + overall

        monkeypatch.setattr(workflow._ResearchRuntime, "__init__", initialize)

    configure()
    return configure


async def execute(model: ScriptedModel) -> ResearchRunResult:
    return await run_opportunity_research(
        OpportunityResearchRequest(
            research_run_id="repair-run", opportunity=opportunity()
        ),
        service(),
        model,
        workflow_config(),
        code=CODE,
    )


class CancellationModel(ScriptedModel):
    def __init__(self, behavior: str, *, late_error: bool = False) -> None:
        super().__init__()
        self.behavior, self.late_error = behavior, late_error
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.finished = asyncio.Event()
        self.release = asyncio.Event()

    async def invoke(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        self.started.set()
        try:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                if self.behavior == "normal":
                    raise
                if self.behavior == "wait":
                    await self.release.wait()
            if self.late_error:
                raise RuntimeError("exception after the workflow already returned")
            return tool()  # This overdue action must never dispatch evidence.
        finally:
            self.finished.set()


@pytest.mark.parametrize("behavior", ["normal", "return", "wait"])
def test_provider_cancellation_behavior_cannot_defeat_deadline(
    behavior: str, short_deadlines: Callable[[float, float], None]
) -> None:
    async def scenario() -> None:
        model = CancellationModel(behavior)
        try:
            result = await asyncio.wait_for(execute(model), 0.75)
            assert result.failure and result.failure.code == "MODEL_TIMEOUT"
            assert (
                result.status == "INTERRUPTED" and result.assessment_origin == "RUNTIME"
            )
            assert result.investigation is None and result.thesis is None
            assert result.critique is None
            assert len(model.requests) == 1
            assert not any(isinstance(r, ToolRecord) for r in result.trace)
            before = result.model_dump_json()
        finally:
            model.release.set()
        await asyncio.wait_for(model.finished.wait(), 0.25)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert model.cancelled.is_set()
        assert result.model_dump_json() == before
        assert not workflow._OVERDUE_MODEL_TASKS

    asyncio.run(scenario())


def test_shorter_remaining_overall_deadline_keeps_workflow_timeout_taxonomy(
    short_deadlines: Callable[[float, float], None],
) -> None:
    short_deadlines(0.25, 0.04)

    async def scenario() -> None:
        model = CancellationModel("wait")
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(execute(model), 0.3)
            assert result.failure and result.failure.code == "WORKFLOW_TIMEOUT"
            assert time.monotonic() - started < 0.2
            assert len(model.requests) == 1
        finally:
            model.release.set()
        await asyncio.wait_for(model.finished.wait(), 0.2)
        await asyncio.sleep(0)

    asyncio.run(scenario())


def test_external_cancellation_propagates_even_when_provider_suppresses_it(
    short_deadlines: Callable[[float, float], None],
) -> None:
    short_deadlines(0.4, 0.5)

    async def scenario() -> None:
        model = CancellationModel("wait")
        running = asyncio.create_task(execute(model))
        try:
            await asyncio.wait_for(model.started.wait(), 0.25)
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(running, 0.25)
            assert len(model.requests) == 1
        finally:
            model.release.set()
        await asyncio.wait_for(model.finished.wait(), 0.25)
        await asyncio.sleep(0)
        assert not workflow._OVERDUE_MODEL_TASKS

    asyncio.run(scenario())


def test_overdue_exceptions_are_observed(
    short_deadlines: Callable[[float, float], None],
) -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        errors: list[dict[str, Any]] = []
        loop.set_exception_handler(lambda loop, context: errors.append(context))
        model = CancellationModel("wait", late_error=True)
        try:
            result = await asyncio.wait_for(execute(model), 0.5)
            assert result.failure and result.failure.code == "MODEL_TIMEOUT"
        finally:
            model.release.set()
        await asyncio.wait_for(model.finished.wait(), 0.25)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not workflow._OVERDUE_MODEL_TASKS
        assert errors == []

    asyncio.run(scenario())


def test_reply_completed_after_deadline_without_cancellation_is_rejected(
    short_deadlines: Callable[[float, float], None],
) -> None:
    class LateModel(ScriptedModel):
        async def invoke(self, request: ModelRequest) -> ModelReply:
            self.requests.append(request)
            time.sleep(0.04)  # Deliberately occupy the event loop past the short limit.
            return tool()

    model = LateModel()
    result = run(model)
    assert result.failure and result.failure.code == "MODEL_TIMEOUT"
    assert len(model.requests) == 1
    assert not any(isinstance(r, ToolRecord) for r in result.trace)


BAD_JSON = ["[" * 2000 + "0" + "]" * 2000, "9" * 5000]


@pytest.mark.parametrize(
    "stage", ["investigate", "draft_thesis", "critique", "final_assessment"]
)
@pytest.mark.parametrize("content", BAD_JSON + ['"\\ud800"', '"\\udfff"'])
def test_parser_failures_have_typed_validation_semantics(
    stage: str, content: str
) -> None:
    steps = [completion(), structured(thesis()), structured(critique()), completion()]
    index = ["investigate", "draft_thesis", "critique", "final_assessment"].index(stage)
    steps[index : index + 1] = [StructuredReply(content=content)] * 2
    model = ScriptedModel(steps)
    result = run(model)
    assert result.failure and result.failure.code == "INVALID_MODEL_OUTPUT"
    assert len(model.requests) == index + 2
    assert result.assessment.abstention_reasons[0].value == "VALIDATION_FAILURE"
    result.model_dump_json().encode("utf-8")


@pytest.mark.parametrize("field", ["summary", "assumptions"])
@pytest.mark.parametrize("scalar", ["\ud800", "\udfff"])
def test_surrogate_inside_otherwise_valid_thesis_never_enters_state(
    field: str, scalar: str
) -> None:
    value = thesis().model_dump(mode="json")
    value[field] = [scalar] if field == "assumptions" else scalar
    result = run(
        ScriptedModel([completion()] + [StructuredReply(content=json.dumps(value))] * 2)
    )
    assert result.failure and result.failure.code == "INVALID_MODEL_OUTPUT"
    assert result.thesis is None
    result.model_dump_json().encode("utf-8")


@pytest.mark.parametrize(
    "content", BAD_JSON + ['{"benchmark":"\\ud800"}', '{"benchmark":"\\udfff"}']
)
def test_bad_tool_argument_parser_failures_consume_invalid_attempts(
    content: str,
) -> None:
    reply = ToolReply(
        requests=(ToolRequest(name="relative_strength", arguments_json=content),)
    )
    model = ScriptedModel([reply, reply])
    result = run(model)
    assert result.failure and result.failure.code == "INVALID_TOOL_REQUEST"
    attempts = [r for r in result.trace if isinstance(r, ToolRecord)]
    assert len(attempts) == 2 and all(r.admitted and r.result is None for r in attempts)
    assert len(model.requests) == 2
    result.model_dump_json().encode("utf-8")


@pytest.mark.parametrize("scalar", ["\ud800", "\udfff"])
def test_unchecked_raw_surrogates_are_rejected_before_trace_retention(
    scalar: str,
) -> None:
    bad = completion().model_copy(update={"content": scalar})
    result = run(ScriptedModel([bad]))
    assert result.failure and result.failure.code == "INVALID_MODEL_OUTPUT"
    result.model_dump_json().encode("utf-8")
    bad_tool = tool().model_copy(
        update={
            "requests": (
                tool().requests[0].model_copy(update={"arguments_json": scalar}),
            )
        }
    )
    result = run(ScriptedModel([bad_tool, bad_tool]))
    assert result.failure and result.failure.code == "INVALID_TOOL_REQUEST"
    result.model_dump_json().encode("utf-8")


def test_valid_unicode_is_preserved() -> None:
    output = thesis().model_dump(mode="json") | {"summary": "市場 evidence 😀 café"}
    result = run(ScriptedModel([completion(), structured(output)], outcome="ABSTAIN"))
    assert result.status == "COMPLETED" and result.thesis
    assert result.thesis.summary == "市場 evidence 😀 café"
    assert "市場" in result.model_dump_json()


REFUSAL = {
    "id": "response-1",
    "type": "message",
    "role": "assistant",
    "model": MODEL,
    "content": [],
    "stop_reason": "refusal",
    "stop_details": {"explanation": "test refusal"},
}


class SizedClaudeModel(AnthropicResearchModel):
    def __init__(self, client: AsyncAnthropic, size: int, marker: str) -> None:
        super().__init__(client)
        self.size, self.marker = size, marker
        self.prepared: list[ModelRequest] = []

    def prepare(self, request: ModelRequest) -> ModelRequest:
        body = render_anthropic_request(request)
        room = self.size - len(render_json(body).encode("utf-8"))
        count, remainder = divmod(room, len(self.marker.encode("utf-8")))
        body["messages"][-1]["content"] += self.marker * count + "x" * remainder
        prepared = ModelRequest.model_validate(
            request.model_copy(update={"prepared_input_json": render_json(body)})
        )
        self.prepared.append(prepared)
        return prepared


@pytest.mark.parametrize("size", [131071, 131072, 131073])
@pytest.mark.parametrize("marker", ["x", "界😀"])
def test_exact_provider_facing_utf8_size_limit_and_transmitted_bytes(
    size: int, marker: str
) -> None:
    received: list[bytes] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        received.append(request.content)
        return httpx2.Response(200, json=REFUSAL)

    async def scenario() -> None:
        async with anthropic_client(handler) as client:
            model = SizedClaudeModel(client, size, marker)
            result = await run_opportunity_research(
                OpportunityResearchRequest(
                    research_run_id="size-run", opportunity=opportunity()
                ),
                service(),
                model,
                workflow_config("anthropic"),
                code=CODE,
            )
            assert len(model.prepared) == 1
            encoded = model.prepared[0].prepared_input_json
            assert encoded is not None and len(encoded.encode("utf-8")) == size
            assert len(render_json(model.prepared[0]).encode("utf-8")) > 131072
            if size <= 131072:
                assert received == [encoded.encode("utf-8")]
                assert result.failure and result.failure.code == "REFUSAL"
                record = next(r for r in result.trace if isinstance(r, ModelCallRecord))
                assert record.request.prepared_input_json == encoded
                assert record.reply.metadata.request_json == encoded
            else:
                assert received == []
                assert (
                    result.failure and result.failure.code == "INPUT_BUDGET_EXHAUSTED"
                )
                rejected = next(
                    r for r in result.trace if isinstance(r, ModelRequestRejection)
                )
                assert rejected.request.prepared_input_json == encoded

    asyncio.run(scenario())


def test_internal_metadata_does_not_count_as_provider_input_and_body_is_prepared_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered = 0
    original = render_anthropic_request

    def render_once(request: ModelRequest) -> dict[str, Any]:
        nonlocal rendered
        rendered += 1
        assert rendered == 1, "invoke must not independently render the prepared body"
        return original(request)

    monkeypatch.setattr(anthropic_adapter, "render_anthropic_request", render_once)

    class MetadataHeavyModel(AnthropicResearchModel):
        def prepare(self, request: ModelRequest) -> ModelRequest:
            request = request.model_copy(
                update={
                    "prompt_identity": request.prompt_identity.model_copy(
                        update={"version": "metadata" * 20000}
                    )
                }
            )
            return super().prepare(request)

    received: list[bytes] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        received.append(request.content)
        return httpx2.Response(200, json=REFUSAL)

    async def scenario() -> None:
        async with anthropic_client(handler) as client:
            result = await run_opportunity_research(
                OpportunityResearchRequest(
                    research_run_id="metadata-run", opportunity=opportunity()
                ),
                service(),
                MetadataHeavyModel(client),
                workflow_config("anthropic"),
                code=CODE,
            )
        assert result.failure and result.failure.code == "REFUSAL"
        record = next(r for r in result.trace if isinstance(r, ModelCallRecord))
        encoded = record.request.prepared_input_json
        assert encoded is not None
        assert len(encoded.encode("utf-8")) < 131072
        assert len(render_json(record.request).encode("utf-8")) > 131072
        assert received == [encoded.encode("utf-8")]
        assert record.reply.metadata.request_json == encoded
        assert rendered == 1

    asyncio.run(scenario())
