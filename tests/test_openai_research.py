"""Real SDK with mocked HTTP transport, covering wire authority and failures."""

import asyncio
import json
from collections.abc import Callable
from hashlib import sha256
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI
from research_support import (
    BTC,
    CODE,
    ScriptedModel,
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

import crypteo.research_workflow as workflow
from crypteo.domain.research_workflow import (
    ModelCallRecord,
    OpportunityResearchRequest,
    ResearchRunResult,
    ToolRecord,
)
from crypteo.openai_research import OpenAIResearchModel, render_openai_request
from crypteo.research_model import (
    FailureReply,
    ModelConfiguration,
    ModelParameters,
    ModelReply,
    ModelRequest,
    RefusalReply,
    StructuredReply,
    ToolReply,
)
from crypteo.research_prompts import (
    ASSESSMENT_INSTRUCTIONS,
    CRITIC_INSTRUCTIONS,
    INVESTIGATION_INSTRUCTIONS,
    SHARED_POLICY,
    THESIS_INSTRUCTIONS,
    prompt_identity,
    structured_schema,
)
from crypteo.research_tools import tool_definitions
from crypteo.research_workflow import run_opportunity_research


def openai_research(
    handler: Callable[[httpx.Request], httpx.Response],
    request: OpportunityResearchRequest,
) -> ResearchRunResult:
    """Run the workflow through the real OpenAI adapter over a mocked transport."""

    async def execute() -> ResearchRunResult:
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client,
            AsyncOpenAI(api_key="test-only", http_client=http_client) as client,
        ):
            return await run_opportunity_research(
                request,
                service(),
                OpenAIResearchModel(client),
                workflow_config("openai"),
                code=CODE,
            )

    return asyncio.run(execute())


def model_request() -> ModelRequest:
    model = ScriptedModel([tool()], provider="openai")
    run(model)
    return model.requests[0].model_copy(update={"prepared_input_json": None})


def envelope(output: list[dict[str, Any]], **changes: Any) -> dict[str, Any]:
    return (
        dict(
            id="resp-observable-1",
            model="returned-model-snapshot",
            status="completed",
            output=output,
            usage={"input_tokens": 8, "output_tokens": 12, "total_tokens": 20},
        )
        | changes
    )


def function(
    name: Any = "trend", arguments: Any = "{}", **changes: Any
) -> dict[str, Any]:
    return (
        dict(
            type="function_call",
            name=name,
            arguments=arguments,
            call_id="provider-call-1",
            status="completed",
        )
        | changes
    )


def message(content: str, *, refusal: bool = False) -> dict[str, Any]:
    part = (
        {"type": "refusal", "refusal": content}
        if refusal
        else {"type": "output_text", "text": content, "annotations": []}
    )
    return {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [part],
    }


def invoke(
    handler: Callable[[httpx.Request], httpx.Response],
    request: ModelRequest | None = None,
) -> ModelReply:
    request = request or model_request()

    async def call() -> ModelReply:
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client,
            AsyncOpenAI(
                api_key="test-only-not-a-secret", http_client=http_client, max_retries=5
            ) as client,
        ):
            adapter = OpenAIResearchModel(client)
            assert adapter._client.max_retries == 0
            assert adapter.identity.provider == "openai"
            assert adapter.identity.sdk_version
            return await adapter.invoke(request)

    return asyncio.run(call())


def test_valid_native_function_call_and_exact_wire_metadata() -> None:
    requests: list[bytes] = []
    response = envelope([function("relative_strength", '{"benchmark":"ETH-USD"}')])
    body = json.dumps(response, separators=(",", ":"))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/responses"
        requests.append(request.content)
        return httpx.Response(
            200, text=body, headers={"x-request-id": "http-request-1"}
        )

    reply = invoke(handler)
    assert isinstance(reply, ToolReply)
    assert reply.requests[0].name == "relative_strength"
    assert reply.requests[0].arguments_json == '{"benchmark":"ETH-USD"}'
    assert reply.requests[0].provider_call_id == "provider-call-1"
    assert reply.metadata.response_json == body
    assert reply.metadata.response_id == "resp-observable-1"
    assert reply.metadata.request_id == "http-request-1"
    assert reply.metadata.returned_model == "returned-model-snapshot"
    assert (
        reply.metadata.usage is not None and reply.metadata.usage["total_tokens"] == 20
    )
    assert json.loads(reply.metadata.request_json or "{}") == json.loads(requests[0])
    assert "test-only-not-a-secret" not in reply.model_dump_json()
    wire = json.loads(requests[0])
    assert wire["store"] is False and wire["parallel_tool_calls"] is False
    assert len(wire["tools"]) == 7
    assert all(t["strict"] is True for t in wire["tools"])
    assert wire["max_output_tokens"] == 8192
    assert wire["text"]["format"]["strict"] is True


def test_completion_uses_strict_structured_schema() -> None:
    reply = invoke(
        lambda request: httpx.Response(
            200, json=envelope([message(completion().content)])
        )
    )
    assert isinstance(reply, StructuredReply)
    assert reply.content == completion().content
    assert reply.metadata.status == "completed"


def test_refusal_is_distinct() -> None:
    reply = invoke(
        lambda request: httpx.Response(
            200, json=envelope([message("Provider refused.", refusal=True)])
        )
    )
    assert isinstance(reply, RefusalReply) and reply.refusal == "Provider refused."


@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled"])
def test_incomplete_status_retains_exact_response(status: str) -> None:
    value = envelope(
        [], status=status, incomplete_details={"reason": "max_output_tokens"}
    )
    reply = invoke(lambda request: httpx.Response(200, json=value))
    assert isinstance(reply, FailureReply) and reply.code.value == "INCOMPLETE_OUTPUT"
    assert reply.metadata.status == status
    assert json.loads(reply.metadata.response_json or "{}") == value


@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        {"id": "r", "model": "m", "status": "queued", "output": []},
        envelope([]),
        envelope([function(call_id=None)]),
        envelope([function(name=1)]),
        envelope([function(arguments={})]),
        envelope([function(status="in_progress")]),
        envelope([{"type": "web_search_call"}]),
        envelope(
            [{"type": "message", "role": "user", "status": "completed", "content": []}]
        ),
        envelope([message("{}"), message("{}")]),
        envelope([function(), message("{}")]),
        envelope([message("refused", refusal=True), message("{}")]),
        envelope(
            [
                {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [None],
                }
            ]
        ),
    ],
)
def test_malformed_provider_envelope_fails_locally(body: Any) -> None:
    reply = invoke(lambda request: httpx.Response(200, json=body))
    assert (
        isinstance(reply, FailureReply)
        and reply.code.value == "INVALID_PROVIDER_ENVELOPE"
    )
    assert reply.metadata.response_json is not None


@pytest.mark.parametrize(
    "body",
    [
        "",
        " ",
        "{broken",
        '{"id":"r","id":"other"}',
        "NaN",
        "[" * 2000 + "0" + "]" * 2000,
        "9" * 5000,
        '"\\ud800"',
        '"\\udfff"',
        json.dumps(envelope([message("ok")], model="\ud800")),
        json.dumps(envelope([message("ok")], usage={"nested": ["\udfff"]})),
    ],
)
def test_bad_envelope_json_no_repair(body: str) -> None:
    reply = invoke(lambda request: httpx.Response(200, text=body))
    assert (
        isinstance(reply, FailureReply)
        and reply.code.value == "INVALID_PROVIDER_ENVELOPE"
    )
    assert reply.metadata.response_json == body
    reply.model_dump_json().encode("utf-8")


@pytest.mark.parametrize(
    "arguments",
    [
        "[" * 2000 + "0" + "]" * 2000,
        "9" * 5000,
        '{"benchmark":"\\ud800"}',
        '{"benchmark":"\\udfff"}',
    ],
)
def test_untrusted_native_arguments_follow_invalid_attempt_policy(
    arguments: str,
) -> None:
    reply = invoke(
        lambda request: httpx.Response(
            200, json=envelope([function("relative_strength", arguments)])
        )
    )
    assert isinstance(reply, ToolReply)
    model = ScriptedModel([reply, reply])
    result = run(model)
    assert result.failure and result.failure.code == "INVALID_TOOL_REQUEST"
    assert len(model.requests) == 2
    attempts = [r for r in result.trace if isinstance(r, ToolRecord)]
    assert len(attempts) == 2 and all(r.admitted and r.result is None for r in attempts)
    result.model_dump_json().encode("utf-8")


def test_valid_provider_unicode_including_surrogate_pair_escape_is_preserved() -> None:
    content = '{"text":"市場 café \\ud83d\\ude00"}'
    reply = invoke(
        lambda request: httpx.Response(200, json=envelope([message(content)]))
    )
    assert isinstance(reply, StructuredReply) and reply.content == content
    reply.model_dump_json().encode("utf-8")


@pytest.mark.parametrize("scalar", ["\ud800", "\udfff"])
@pytest.mark.parametrize("encoding", ["nested_escape", "outer_decoded"])
@pytest.mark.parametrize("occurrences", [1, 2])
def test_native_surrogate_arguments_have_identical_invalid_attempt_accounting(
    scalar: str, encoding: str, occurrences: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments = (
        json.dumps({"benchmark": scalar})
        if encoding == "nested_escape"
        else '{"benchmark":"' + scalar + '"}'
    )
    bad_body = json.dumps(envelope([function("relative_strength", arguments)]))
    responses = [bad_body] * occurrences + [
        json.dumps(envelope([message(completion().content)])),
        json.dumps(envelope([message(structured(thesis()).content)])),
        json.dumps(envelope([message(structured(critique()).content)])),
        json.dumps(envelope([message(structured(assessment("ABSTAIN", ())).content)])),
    ]
    sent: list[dict[str, Any]] = []

    def no_dispatch(*args: Any) -> Any:
        raise AssertionError("malformed arguments must never dispatch a research tool")

    monkeypatch.setattr(workflow, "dispatch_tool", no_dispatch)

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, text=responses.pop(0))

    result = openai_research(
        handler,
        OpportunityResearchRequest(research_run_id="f2-run", opportunity=opportunity()),
    )
    attempts = [r for r in result.trace if isinstance(r, ToolRecord)]
    assert len(attempts) == occurrences
    assert all(r.admitted and r.result is None and r.query is None for r in attempts)
    assert all(
        r.rejection and r.rejection.code == "INVALID_TOOL_REQUEST" for r in attempts
    )
    assert all(
        r.rejection and r.rejection.message == "invalid Unicode surrogate scalar"
        for r in attempts
    )
    second_input = json.loads(sent[1]["input"][-1]["content"])["research_data"]
    assert second_input["attempts_remaining"] == 7
    assert len(second_input["request_history"]) == 1
    assert (
        second_input["request_history"][0]["rejection"]["code"]
        == "INVALID_TOOL_REQUEST"
    )
    if occurrences == 1:
        assert result.status == "COMPLETED" and result.failure is None
        assert len(sent) == 5
    else:
        assert result.status == "INTERRUPTED"
        assert result.failure and result.failure.code == "INVALID_TOOL_REQUEST"
        assert len(sent) == 2
    calls = [r for r in result.trace if isinstance(r, ModelCallRecord)]
    assert all(r.reply.metadata.response_json == bad_body for r in calls[:occurrences])
    for call in calls[:occurrences]:
        assert isinstance(call.reply, ToolReply)
        assert call.reply.requests[0].argument_error == (
            "INVALID_UNICODE" if encoding == "outer_decoded" else None
        )
    assert type(result).model_validate_json(result.model_dump_json()) == result
    result.model_dump_json().encode("utf-8")


@pytest.mark.parametrize("scalar", ["\ud800", "\udfff"])
@pytest.mark.parametrize("field", ["id", "model", "name", "call_id", "status"])
def test_native_envelope_surrogates_remain_envelope_failures(
    scalar: str, field: str
) -> None:
    item = function("relative_strength", '{"benchmark":"\ud800"}')
    body = envelope([item])
    if field in {"id", "model"}:
        body[field] = scalar
    else:
        item[field] = scalar
    reply = invoke(lambda request: httpx.Response(200, text=json.dumps(body)))
    assert isinstance(reply, FailureReply) and reply.code == "INVALID_PROVIDER_ENVELOPE"
    result = run(ScriptedModel([reply]))
    assert result.failure and result.failure.code == "INVALID_PROVIDER_ENVELOPE"
    assert not any(isinstance(r, ToolRecord) for r in result.trace)
    result.model_dump_json().encode("utf-8")


@pytest.mark.parametrize("arguments", ["{broken", "not json"])
def test_ordinary_malformed_native_arguments_can_recover(arguments: str) -> None:
    reply = invoke(
        lambda request: httpx.Response(
            200, json=envelope([function("trend", arguments)])
        )
    )
    assert isinstance(reply, ToolReply) and reply.requests[0].argument_error is None
    result = run(ScriptedModel([reply], outcome="ABSTAIN"))
    attempts = [r for r in result.trace if isinstance(r, ToolRecord)]
    assert result.status == "COMPLETED" and result.failure is None
    assert len(attempts) == 1 and attempts[0].admitted and attempts[0].result is None


@pytest.mark.parametrize("escaped", [True, False])
def test_valid_unicode_native_arguments_remain_opaque_and_unchanged(
    escaped: bool,
) -> None:
    arguments = json.dumps({"benchmark": "市場 café 😀"}, ensure_ascii=escaped)
    reply = invoke(
        lambda request: httpx.Response(
            200, json=envelope([function("relative_strength", arguments)])
        )
    )
    assert isinstance(reply, ToolReply)
    assert reply.requests[0].arguments_json == arguments
    assert reply.requests[0].argument_error is None
    reply.model_dump_json().encode("utf-8")
    # Unicode content is valid; the fixed benchmark allowlist still rejects it.
    result = run(ScriptedModel([reply], outcome="ABSTAIN"))
    attempt = next(r for r in result.trace if isinstance(r, ToolRecord))
    assert result.status == "COMPLETED"
    assert (
        attempt.rejection
        and attempt.rejection.message
        == "relative_strength requires only a permitted benchmark"
    )


def test_multiple_tool_calls_preserved_for_local_attempt_rejection() -> None:
    reply = invoke(
        lambda request: httpx.Response(
            200,
            json=envelope(
                [function(), function("momentum", call_id="provider-call-2")]
            ),
        )
    )
    assert isinstance(reply, ToolReply) and len(reply.requests) == 2
    result = run(ScriptedModel([reply, reply]))
    assert result.failure and result.failure.code == "INVALID_TOOL_REQUEST"
    assert all(r.result is None for r in result.trace if isinstance(r, ToolRecord))


@pytest.mark.parametrize("status", [400, 401, 429, 500, 503])
def test_http_error_no_sdk_retry_metadata_without_headers(status: int) -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        return httpx.Response(
            status,
            json={
                "error": {
                    "message": "test-only-not-a-secret",
                    "type": "api_error",
                    "code": "specific_error",
                }
            },
            headers={
                "x-request-id": "error-request-1",
                "authorization": "test-only-not-a-secret",
            },
        )

    reply = invoke(handler)
    assert count == 1
    assert isinstance(reply, FailureReply) and reply.code.value == "PROVIDER_ERROR"
    assert (
        reply.metadata.http_status == status
        and reply.metadata.request_id == "error-request-1"
    )
    assert (
        reply.metadata.error_type == "api_error"
        and reply.metadata.error_code == "specific_error"
    )
    assert "test-only-not-a-secret" not in reply.model_dump_json()


@pytest.mark.parametrize("timeout", [False, True])
def test_network_and_sdk_timeout_do_not_retry(timeout: bool) -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        if timeout:
            raise httpx.ReadTimeout("test timeout", request=request)
        raise httpx.ConnectError("test failure", request=request)

    reply = invoke(handler)
    assert isinstance(reply, FailureReply)
    assert reply.code.value == ("MODEL_TIMEOUT" if timeout else "PROVIDER_NETWORK")
    assert count == 1


def test_synthesis_wire_has_no_tools_and_explicit_parameters() -> None:
    model = ScriptedModel([tool()], provider="openai")
    run(model)
    for request in model.requests[-3:]:
        request = ModelRequest.model_validate(
            request.model_copy(
                update={
                    "configuration": ModelConfiguration(
                        provider="openai",
                        model="chosen-test-model",
                        parameters=ModelParameters(
                            temperature=0.5, reasoning_effort="low"
                        ),
                    )
                }
            )
        )
        wire = render_openai_request(request)
        assert "tools" not in wire and "tool_choice" not in wire
        assert wire["max_output_tokens"] == 16384
        assert wire["temperature"] == 0.5 and wire["reasoning"] == {"effort": "low"}
        assert wire["model"] == "chosen-test-model"
        assert wire["text"]["format"]["strict"] is True


def test_complete_graph_with_mocked_openai_transport() -> None:
    svc = service()
    result = svc.trend(BTC)
    evidence_id = result.model_dump(mode="json")["evidence"]["evidence_id"]
    responses = [
        envelope([function()]),
        envelope([message(completion((evidence_id,)).content)]),
        envelope([message(structured(thesis((evidence_id,))).content)]),
        envelope([message(structured(critique()).content)]),
        envelope([message(structured(assessment("TRADE", (evidence_id,))).content)]),
    ]
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json=responses.pop(0),
            headers={"x-request-id": f"request-{len(bodies)}"},
        )

    research = openai_research(
        handler,
        OpportunityResearchRequest(
            research_run_id="openai-mocked", opportunity=opportunity()
        ),
    )
    assert (
        research.status == "COMPLETED" and research.assessment.outcome.value == "TRADE"
    )
    assert len(bodies) == 5
    assert not responses
    calls = [r for r in research.trace if isinstance(r, ModelCallRecord)]
    assert [json.loads(r.reply.metadata.request_json or "{}") for r in calls] == bodies
    assert [r.reply.metadata.request_id for r in calls] == [
        f"request-{i}" for i in range(1, 6)
    ]
    assert all("previous_response_id" not in b for b in bodies)
    assert all("tools" not in b for b in bodies[-3:])


@pytest.mark.parametrize(
    "content",
    [
        "{bad",
        '{"summary":"only one field"}',
        '{"summary":"x","relevant_evidence_ids":[],"remaining_gaps":[],"confidence":80}',
    ],
)
def test_openai_invalid_json_schema_interrupted_without_retry(content: str) -> None:
    reply = invoke(
        lambda request: httpx.Response(200, json=envelope([message(content)]))
    )
    assert isinstance(reply, StructuredReply) and reply.content == content
    model = ScriptedModel([reply])
    result = run(model)
    assert result.failure and result.failure.code == "INVALID_MODEL_OUTPUT"
    assert len(model.requests) == 1


def digest(value: Any) -> str:
    return (
        "sha256:"
        + sha256(
            json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
    )


def test_stable_prompt_schema_and_tool_digests_independent_encoding() -> None:
    instructions = {
        "investigate": INVESTIGATION_INSTRUCTIONS,
        "draft_thesis": THESIS_INSTRUCTIONS,
        "critique": CRITIC_INSTRUCTIONS,
        "final_assessment": ASSESSMENT_INSTRUCTIONS,
    }
    for stage, instruction in instructions.items():
        assert prompt_identity(stage).version == f"{stage}_prompt_v1"
        assert (
            prompt_identity(stage).digest
            == "sha256:"
            + sha256((SHARED_POLICY + "\n\n" + instruction).encode()).hexdigest()
        )
        schema = structured_schema(stage)
        assert schema.identity.digest == digest(
            schema.model_dump(mode="json")["json_schema"]
        )
        assert schema == structured_schema(stage)
        value = schema.model_dump(mode="json")["json_schema"]
        assert value["required"] == list(value["properties"])
        assert "default" not in json.dumps(value)
        assert value["additionalProperties"] is False
    for t in tool_definitions(BTC):
        assert t.identity.version == "bounded_market_tools_v1"
        assert t.identity.digest == digest(
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.model_dump(mode="json")["parameters"],
                "strict": True,
            }
        )
