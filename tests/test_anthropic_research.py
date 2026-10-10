"""Real Claude SDK with mocked HTTP transport, covering wire authority and failures."""

import asyncio
import json
from collections.abc import Callable
from typing import Any, Literal

import httpx2
import pytest
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient
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

import crypteo.anthropic_research as adapter_module
import crypteo.research_workflow as workflow
from crypteo.anthropic_research import (
    ADAPTER_VERSION,
    AnthropicResearchModel,
    render_anthropic_request,
)
from crypteo.domain.research_workflow import (
    ModelCallRecord,
    OpportunityResearchRequest,
    ResearchRunResult,
    ToolRecord,
)
from crypteo.research_model import (
    FailureReply,
    ModelConfiguration,
    ModelMessage,
    ModelParameters,
    ModelReply,
    ModelRequest,
    ProviderObservation,
    RefusalReply,
    StructuredReply,
    ToolReply,
    render_json,
)
from crypteo.research_workflow import run_opportunity_research

KEY = "test-only-not-a-secret"
FORBIDDEN_WIRE_KEYS = {
    "thinking",
    "fallbacks",
    "stream",
    "metadata",
    "betas",
    "temperature",
    "top_p",
    "top_k",
}

type Handler = Callable[[httpx2.Request], httpx2.Response]


@pytest.fixture(autouse=True)
def no_ambient_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ambient key must never be read: every client gets an explicit test key."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-key-must-not-be-read")
    for name in (
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS",
        "ANTHROPIC_PROFILE",
    ):
        monkeypatch.delenv(name, raising=False)


def client(handler: Handler, **options: Any) -> AsyncAnthropic:
    return AsyncAnthropic(
        api_key=KEY,
        http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
        max_retries=5,
        **options,
    )


def model_request(**parameters: Any) -> ModelRequest:
    model = ScriptedModel([tool()], provider="anthropic")
    run(model)
    request = model.requests[0].model_copy(update={"prepared_input_json": None})
    return configured(request, **parameters)


def synthesis_requests(**parameters: Any) -> list[ModelRequest]:
    model = ScriptedModel([tool()], provider="anthropic")
    run(model)
    return [configured(r, **parameters) for r in model.requests[-3:]]


def configured(request: ModelRequest, **parameters: Any) -> ModelRequest:
    return ModelRequest.model_validate(
        request.model_copy(
            update={
                "configuration": ModelConfiguration(
                    provider=parameters.pop("provider", "anthropic"),
                    model="chosen-test-model",
                    parameters=ModelParameters(**parameters),
                ),
                "prepared_input_json": None,
            }
        )
    )


def envelope(content: list[Any], **changes: Any) -> dict[str, Any]:
    return (
        dict(
            id="msg-observable-1",
            type="message",
            role="assistant",
            model="returned-model-snapshot",
            content=content,
            stop_reason="end_turn",
            stop_sequence=None,
            usage={"input_tokens": 8, "output_tokens": 12},
        )
        | changes
    )


def tool_use(name: Any = "trend", input: Any = None, **changes: Any) -> dict[str, Any]:
    return (
        dict(
            type="tool_use",
            id="toolu-provider-1",
            name=name,
            input={} if input is None else input,
        )
        | changes
    )


def tool_envelope(*blocks: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return envelope(list(blocks), stop_reason="tool_use") | changes


def text(content: str) -> dict[str, Any]:
    return {"type": "text", "text": content}


THINKING = {"type": "thinking", "thinking": "opaque", "signature": "sig-1"}
REDACTED = {"type": "redacted_thinking", "data": "opaque-redacted"}


def invoke(
    handler: Handler,
    request: ModelRequest | None = None,
    observe: Callable[[ProviderObservation], None] | None = None,
) -> ModelReply:
    request = request or model_request()

    async def call() -> ModelReply:
        async with client(handler) as sdk:
            adapter = AnthropicResearchModel(sdk)
            assert adapter._client.max_retries == 0
            assert adapter._client.timeout == 180.0
            assert adapter.identity.provider == "anthropic"
            assert adapter.identity.adapter_version == ADAPTER_VERSION
            assert adapter.identity.sdk_version
            if observe is None:
                return await adapter.invoke(request)
            return await adapter.invoke_observed(request, observe)

    return asyncio.run(call())


def anthropic_research(
    handler: Handler, request: OpportunityResearchRequest
) -> ResearchRunResult:
    """Run the workflow through the real Claude adapter over a mocked transport."""

    async def execute() -> ResearchRunResult:
        async with client(handler) as sdk:
            return await run_opportunity_research(
                request,
                service(),
                AnthropicResearchModel(sdk),
                workflow_config("anthropic"),
                code=CODE,
            )

    return asyncio.run(execute())


def assert_clean_wire(request: httpx2.Request) -> dict[str, Any]:
    wire: dict[str, Any] = json.loads(request.content)
    assert not FORBIDDEN_WIRE_KEYS & set(wire)
    assert "anthropic-beta" not in request.headers
    assert request.headers["x-api-key"] == KEY
    assert "ambient-key-must-not-be-read" not in str(request.headers)
    return wire


def test_valid_tool_use_and_exact_wire_metadata() -> None:
    requests: list[httpx2.Request] = []
    response = tool_envelope(tool_use("relative_strength", {"benchmark": "ETH-USD"}))
    body = json.dumps(response, separators=(",", ":"))
    request = model_request()

    def handler(sent: httpx2.Request) -> httpx2.Response:
        assert sent.method == "POST" and sent.url.path == "/v1/messages"
        requests.append(sent)
        return httpx2.Response(200, text=body, headers={"request-id": "req-claude-1"})

    reply = invoke(handler, request)
    assert len(requests) == 1
    assert isinstance(reply, ToolReply)
    assert reply.requests[0].name == "relative_strength"
    assert reply.requests[0].arguments_json == '{"benchmark":"ETH-USD"}'
    assert reply.requests[0].argument_error is None
    assert reply.requests[0].provider_call_id == "toolu-provider-1"
    assert reply.metadata.response_json == body
    assert reply.metadata.response_id == "msg-observable-1"
    assert reply.metadata.request_id == "req-claude-1"
    assert reply.metadata.returned_model == "returned-model-snapshot"
    assert reply.metadata.status == "tool_use"
    assert reply.metadata.http_status == 200
    assert reply.metadata.model_dump(mode="json")["usage"] == {
        "input_tokens": 8,
        "output_tokens": 12,
    }
    # The exact prepared bytes are the bytes on the wire.
    prepared = render_json(render_anthropic_request(request))
    assert reply.metadata.request_json == prepared
    assert requests[0].content == prepared.encode("utf-8")
    assert KEY not in reply.model_dump_json()
    wire = assert_clean_wire(requests[0])
    assert set(wire) == {
        "model",
        "max_tokens",
        "system",
        "messages",
        "output_config",
        "tools",
        "tool_choice",
    }
    assert wire["model"] == "chosen-test-model"
    assert wire["max_tokens"] == 1024
    assert wire["system"] == request.messages[0].content
    assert wire["messages"] == [
        {"role": "user", "content": request.messages[1].content}
    ]
    assert wire["output_config"] == {
        "format": {
            "type": "json_schema",
            "schema": request.output_schema.model_dump(mode="json")["json_schema"],
        }
    }
    assert wire["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert len(wire["tools"]) == 7
    assert [t["name"] for t in wire["tools"]] == [t.name for t in request.tools]
    for sent_tool, definition in zip(wire["tools"], request.tools, strict=True):
        assert sent_tool == {
            "name": definition.name,
            "description": definition.description,
            "input_schema": definition.model_dump(mode="json")["parameters"],
            "strict": True,
        }


def test_prepared_input_json_round_trips_and_is_sent_verbatim() -> None:
    request = model_request(reasoning_effort="high")
    sent: list[bytes] = []

    async def prepare() -> ModelRequest:
        async with client(lambda r: httpx2.Response(500)) as sdk:
            return AnthropicResearchModel(sdk).prepare(request)

    prepared = asyncio.run(prepare())
    assert prepared.prepared_input_json is not None
    assert json.loads(prepared.prepared_input_json) == render_anthropic_request(request)
    assert render_json(json.loads(prepared.prepared_input_json)) == (
        prepared.prepared_input_json
    )
    assert ModelRequest.model_validate_json(prepared.model_dump_json()) == prepared

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(request.content)
        return httpx2.Response(200, json=envelope([text(completion().content)]))

    reply = invoke(handler, prepared)
    assert isinstance(reply, StructuredReply)
    assert sent == [prepared.prepared_input_json.encode("utf-8")]
    assert reply.metadata.request_json == prepared.prepared_input_json


def test_structured_completion_uses_json_schema_output_config() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(assert_clean_wire(request))
        return httpx2.Response(200, json=envelope([text(completion().content)]))

    reply = invoke(handler)
    assert isinstance(reply, StructuredReply)
    assert reply.content == completion().content
    assert reply.metadata.status == "end_turn"
    assert sent[0]["output_config"]["format"]["type"] == "json_schema"
    assert "effort" not in sent[0]["output_config"]


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh"])
def test_synthesis_wire_has_no_tools_and_maps_effort(effort: str) -> None:
    for request in synthesis_requests(reasoning_effort=effort):
        wire = render_anthropic_request(request)
        assert "tools" not in wire and "tool_choice" not in wire
        assert not FORBIDDEN_WIRE_KEYS & set(wire)
        assert wire["max_tokens"] == 2048
        assert wire["model"] == "chosen-test-model"
        assert wire["output_config"] == {
            "format": {
                "type": "json_schema",
                "schema": request.output_schema.model_dump(mode="json")["json_schema"],
            },
            "effort": effort,
        }
        assert wire["system"] == request.messages[0].content
        assert [m["role"] for m in wire["messages"]] == ["user"]


def test_omitted_effort_is_not_sent() -> None:
    for request in synthesis_requests():
        assert set(render_anthropic_request(request)["output_config"]) == {"format"}


@pytest.mark.parametrize(
    "parameters",
    [
        {"temperature": 0.5},
        {"temperature": 0.0},
        {"reasoning_effort": "none"},
        {"reasoning_effort": "minimal"},
        {"provider": "openai"},
    ],
)
def test_unsupported_configuration_is_rejected(parameters: dict[str, Any]) -> None:
    request = model_request(**parameters)
    with pytest.raises(ValueError):
        render_anthropic_request(request)

    async def prepare() -> None:
        async with client(lambda r: httpx2.Response(500)) as sdk:
            AnthropicResearchModel(sdk).prepare(request)

    with pytest.raises(ValueError):
        asyncio.run(prepare())


@pytest.mark.parametrize(
    "roles",
    [
        ("system",),
        ("system", "user", "system"),
        ("user", "system"),
        ("system", "system"),
    ],
)
def test_message_order_is_validated(
    roles: tuple[Literal["system", "user"], ...],
) -> None:
    request = model_request()
    messages = tuple(ModelMessage(role=r, content=f"{r} text") for r in roles)
    with pytest.raises(ValueError):
        render_anthropic_request(request.model_copy(update={"messages": messages}))


def test_leading_system_messages_join_and_users_are_kept() -> None:
    request = model_request()
    messages = (
        ModelMessage(role="system", content="first"),
        ModelMessage(role="system", content="second"),
        ModelMessage(role="user", content="question"),
        ModelMessage(role="user", content="detail"),
    )
    wire = render_anthropic_request(request.model_copy(update={"messages": messages}))
    assert wire["system"] == "first\n\nsecond"
    assert wire["messages"] == [
        {"role": "user", "content": "question"},
        {"role": "user", "content": "detail"},
    ]
    only_user = (ModelMessage(role="user", content="question"),)
    wire = render_anthropic_request(request.model_copy(update={"messages": only_user}))
    assert "system" not in wire


@pytest.mark.parametrize(
    ("details", "expected"),
    [
        (
            {"category": "cyber", "explanation": "Provider refused."},
            "Provider refused.",
        ),
        ({"category": "cyber", "explanation": " "}, "refusal: cyber"),
        ({"category": "cyber", "explanation": None}, "refusal: cyber"),
        ({"category": None, "explanation": None}, "refusal: unspecified"),
        ({"category": "", "explanation": 7}, "refusal: unspecified"),
        (None, "refusal: unspecified"),
        ("not-an-object", "refusal: unspecified"),
    ],
)
def test_refusal_is_distinct(details: Any, expected: str) -> None:
    value = envelope([], stop_reason="refusal")
    if details is not None:
        value["stop_details"] = details
    reply = invoke(lambda request: httpx2.Response(200, json=value))
    assert isinstance(reply, RefusalReply) and reply.refusal == expected
    assert reply.metadata.status == "refusal"
    assert json.loads(reply.metadata.response_json or "{}") == value


@pytest.mark.parametrize(
    "content",
    [
        [],
        [text('{"summary":"partial')],
        [tool_use("trend", {"partial": True})],
        [THINKING],
    ],
)
def test_max_tokens_is_incomplete_and_retains_exact_response(
    content: list[Any],
) -> None:
    value = envelope(content, stop_reason="max_tokens")
    body = json.dumps(value)
    reply = invoke(lambda request: httpx2.Response(200, text=body))
    assert isinstance(reply, FailureReply) and reply.code.value == "INCOMPLETE_OUTPUT"
    assert reply.message == "Claude response stop_reason: max_tokens"
    assert reply.metadata.status == "max_tokens"
    assert reply.metadata.response_json == body
    assert reply.metadata.response_id == "msg-observable-1"


def test_thinking_blocks_are_ignored() -> None:
    reply = invoke(
        lambda request: httpx2.Response(
            200,
            json=envelope([THINKING, REDACTED, text(completion().content), THINKING]),
        )
    )
    assert isinstance(reply, StructuredReply) and reply.content == completion().content
    assert "opaque-redacted" in (reply.metadata.response_json or "")
    reply = invoke(
        lambda request: httpx2.Response(
            200, json=tool_envelope(THINKING, REDACTED, tool_use())
        )
    )
    assert isinstance(reply, ToolReply) and len(reply.requests) == 1


def test_text_alongside_tool_use_is_not_interpreted() -> None:
    value = tool_envelope(text("I will call a tool."), tool_use(), text("{}"))
    reply = invoke(lambda request: httpx2.Response(200, json=value))
    assert isinstance(reply, ToolReply)
    assert [r.name for r in reply.requests] == ["trend"]
    assert "I will call a tool." in (reply.metadata.response_json or "")


@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        envelope([text("{}")], id=""),
        envelope([text("{}")], id=None),
        envelope([text("{}")], model=" "),
        envelope([text("{}")], stop_reason=None),
        envelope([text("{}")], type="error"),
        envelope([text("{}")], type=None),
        envelope([text("{}")], role="user"),
        envelope([]) | {"content": None},
        envelope([]) | {"content": {"type": "text", "text": "{}"}},
        envelope([text("{}")], usage=[1]),
        envelope([]),
        envelope([text("{}"), text("{}")]),
        envelope([THINKING]),
        envelope([tool_use(), text("{}")]),
        envelope([None]),
        envelope([{"type": "text"}]),
        envelope([{"type": "text", "text": 1}]),
        envelope([{"text": "{}"}]),
        tool_envelope(),
        tool_envelope(text("{}")),
        tool_envelope(tool_use(id=None)),
        tool_envelope(tool_use(id="")),
        tool_envelope(tool_use(id=" ")),
        tool_envelope(tool_use(id=1)),
        tool_envelope(tool_use(name=1)),
        tool_envelope(tool_use(name=None)),
        tool_envelope(tool_use(input='{"benchmark":"ETH-USD"}')),
        tool_envelope(tool_use(input=[])),
        tool_envelope(tool_use(input=None) | {"input": None}),
        tool_envelope({"type": "tool_use", "id": "toolu-1", "name": "trend"}),
    ],
)
def test_malformed_provider_envelope_fails_locally(body: Any) -> None:
    reply = invoke(lambda request: httpx2.Response(200, json=body))
    assert (
        isinstance(reply, FailureReply)
        and reply.code.value == "INVALID_PROVIDER_ENVELOPE"
    )
    assert reply.message == "Claude response envelope failed validation"
    assert reply.metadata.response_json is not None


@pytest.mark.parametrize(
    "block",
    [
        {"type": "server_tool_use", "id": "srvtoolu-1", "name": "web_search"},
        {"type": "web_search_tool_result", "tool_use_id": "srvtoolu-1"},
        {"type": "image"},
        {"type": "mystery"},
    ],
)
@pytest.mark.parametrize("stop_reason", ["end_turn", "tool_use"])
def test_unknown_block_type_is_invalid_envelope(
    block: dict[str, Any], stop_reason: str
) -> None:
    content = [block, text("{}") if stop_reason == "end_turn" else tool_use()]
    value = envelope(content, stop_reason=stop_reason)
    reply = invoke(lambda request: httpx2.Response(200, json=value))
    assert isinstance(reply, FailureReply) and reply.code == "INVALID_PROVIDER_ENVELOPE"


@pytest.mark.parametrize(
    "stop_reason", ["pause_turn", "stop_sequence", "model_context_window_exceeded", "x"]
)
def test_unknown_stop_reason_is_invalid_envelope(stop_reason: str) -> None:
    value = envelope([text(completion().content)], stop_reason=stop_reason)
    reply = invoke(lambda request: httpx2.Response(200, json=value))
    assert isinstance(reply, FailureReply) and reply.code == "INVALID_PROVIDER_ENVELOPE"
    assert reply.metadata.status == stop_reason


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b" ",
        b"{broken",
        b'{"id":"r","id":"other"}',
        b"NaN",
        b"[" * 2000 + b"0" + b"]" * 2000,
        b"9" * 5000,
        b'"\\ud800"',
        b'"\\udfff"',
        json.dumps(envelope([text("ok")], model="\ud800")).encode(),
        json.dumps(envelope([text("ok")], usage={"nested": ["\udfff"]})).encode(),
        json.dumps(tool_envelope(tool_use(input={"n": 0})))
        .replace('"n": 0', '"n": ' + "[" * 1500 + "0" + "]" * 1500)
        .encode(),
        json.dumps(tool_envelope(tool_use(input={"n": 0})))
        .replace('"n": 0', '"n": ' + "9" * 5000)
        .encode(),
    ],
)
def test_bad_envelope_json_no_repair(body: bytes) -> None:
    reply = invoke(lambda request: httpx2.Response(200, content=body))
    assert (
        isinstance(reply, FailureReply)
        and reply.code.value == "INVALID_PROVIDER_ENVELOPE"
    )
    assert reply.metadata.response_json == body.decode("utf-8")
    reply.model_dump_json().encode("utf-8")


@pytest.mark.parametrize("body", [b"\xff\xfe", b'{"id":"\xc3"}', b"\xed\xa0\x80"])
def test_non_utf8_body_is_invalid_envelope_without_repair(body: bytes) -> None:
    observed: list[ProviderObservation] = []
    reply = invoke(
        lambda request: httpx2.Response(200, content=body), observe=observed.append
    )
    assert isinstance(reply, FailureReply) and reply.code == "INVALID_PROVIDER_ENVELOPE"
    assert reply.message == "Claude response body is not valid UTF-8"
    assert reply.metadata.response_json is None
    assert [o.body for o in observed] == [body]


def test_valid_provider_unicode_including_surrogate_pair_escape_is_preserved() -> None:
    content = '{"text":"市場 café \\ud83d\\ude00"}'
    reply = invoke(lambda request: httpx2.Response(200, json=envelope([text(content)])))
    assert isinstance(reply, StructuredReply) and reply.content == content
    reply.model_dump_json().encode("utf-8")


@pytest.mark.parametrize("ascii_escaped", [True, False])
def test_valid_unicode_tool_input_is_rendered_and_unchanged(
    ascii_escaped: bool,
) -> None:
    value = tool_envelope(tool_use("relative_strength", {"benchmark": "市場 café 😀"}))
    body = json.dumps(value, ensure_ascii=ascii_escaped)
    reply = invoke(lambda request: httpx2.Response(200, text=body))
    assert isinstance(reply, ToolReply)
    assert reply.requests[0].arguments_json == '{"benchmark":"市場 café 😀"}'
    assert reply.requests[0].argument_error is None
    assert reply.metadata.response_json == body
    # Unicode content is valid; the fixed benchmark allowlist still rejects it.
    result = run(ScriptedModel([reply], outcome="ABSTAIN"))
    attempt = next(r for r in result.trace if isinstance(r, ToolRecord))
    assert result.status == "COMPLETED"
    assert (
        attempt.rejection
        and attempt.rejection.message
        == "relative_strength requires only a permitted benchmark"
    )


def test_tool_input_is_rendered_canonically() -> None:
    value = tool_envelope(
        tool_use("relative_strength", {"z": 1.50, "benchmark": "ETH-USD", "a": [1]})
    )
    body = '{"content":[{"type":"tool_use","id":"toolu-1","name":"relative_strength",'
    body += '"input":{"z":1.50,"benchmark":"ETH-USD","a":[1]}}],'
    body += json.dumps({k: v for k, v in value.items() if k != "content"})[1:]
    reply = invoke(lambda request: httpx2.Response(200, text=body))
    assert isinstance(reply, ToolReply)
    assert reply.requests[0].arguments_json == (
        '{"a":[1],"benchmark":"ETH-USD","z":1.5}'
    )
    assert reply.metadata.response_json == body


def test_deep_but_decodable_tool_input_is_an_invalid_attempt() -> None:
    nested = "[" * 900 + "0" + "]" * 900
    body = json.dumps(tool_envelope(tool_use("relative_strength", {"benchmark": 0})))
    body = body.replace('"benchmark": 0', '"benchmark": ' + nested)
    reply = invoke(lambda request: httpx2.Response(200, text=body))
    assert isinstance(reply, ToolReply) and reply.requests[0].argument_error is None
    model = ScriptedModel([reply, reply])
    result = run(model)
    assert result.failure and result.failure.code == "INVALID_TOOL_REQUEST"
    attempts = [r for r in result.trace if isinstance(r, ToolRecord)]
    assert len(attempts) == 2 and all(r.admitted and r.result is None for r in attempts)
    result.model_dump_json().encode("utf-8")


@pytest.mark.parametrize("scalar", ["\ud800", "\udfff"])
@pytest.mark.parametrize("position", ["value", "key", "nested"])
@pytest.mark.parametrize("occurrences", [1, 2])
def test_tool_input_surrogates_have_identical_invalid_attempt_accounting(
    scalar: str, position: str, occurrences: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs: dict[str, dict[str, Any]] = {
        "value": {"benchmark": scalar},
        "key": {scalar: "ETH-USD"},
        "nested": {"benchmark": ["ETH-USD", {"inner": scalar}]},
    }
    arguments = inputs[position]
    bad_body = json.dumps(tool_envelope(tool_use("relative_strength", arguments)))
    responses = [bad_body] * occurrences + [
        json.dumps(envelope([text(completion().content)])),
        json.dumps(envelope([text(structured(thesis()).content)])),
        json.dumps(envelope([text(structured(critique()).content)])),
        json.dumps(envelope([text(structured(assessment("ABSTAIN", ())).content)])),
    ]
    sent: list[dict[str, Any]] = []

    def no_dispatch(*args: Any) -> Any:
        raise AssertionError("malformed arguments must never dispatch a research tool")

    monkeypatch.setattr(workflow, "dispatch_tool", no_dispatch)

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(assert_clean_wire(request))
        return httpx2.Response(200, text=responses.pop(0))

    result = anthropic_research(
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
    second_input = json.loads(sent[1]["messages"][-1]["content"])["research_data"]
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
        assert call.reply.requests[0].argument_error == "INVALID_UNICODE"
        call.reply.requests[0].arguments_json.encode("utf-8")
    assert type(result).model_validate_json(result.model_dump_json()) == result
    result.model_dump_json().encode("utf-8")


@pytest.mark.parametrize("scalar", ["\ud800", "\udfff"])
@pytest.mark.parametrize(
    "field",
    ["id", "model", "stop_reason", "name", "tool_id", "text", "thinking", "usage"],
)
def test_envelope_surrogates_remain_envelope_failures(scalar: str, field: str) -> None:
    item = tool_use("relative_strength", {"benchmark": "\ud800"})
    body = tool_envelope(text("alongside"), THINKING | {}, item)
    match field:
        case "id" | "model" | "stop_reason":
            body[field] = scalar
        case "name":
            item["name"] = scalar
        case "tool_id":
            item["id"] = scalar
        case "text":
            body["content"][0]["text"] = scalar
        case "thinking":
            body["content"][1] = THINKING | {"thinking": scalar}
        case "usage":
            body["usage"] = {"input_tokens": scalar}
    reply = invoke(lambda request: httpx2.Response(200, text=json.dumps(body)))
    assert isinstance(reply, FailureReply) and reply.code == "INVALID_PROVIDER_ENVELOPE"
    result = run(ScriptedModel([reply]))
    assert result.failure and result.failure.code == "INVALID_PROVIDER_ENVELOPE"
    assert not any(isinstance(r, ToolRecord) for r in result.trace)
    result.model_dump_json().encode("utf-8")


def test_multiple_tool_uses_preserved_for_local_attempt_rejection() -> None:
    reply = invoke(
        lambda request: httpx2.Response(
            200,
            json=tool_envelope(tool_use(), tool_use("momentum", id="toolu-provider-2")),
        )
    )
    assert isinstance(reply, ToolReply) and len(reply.requests) == 2
    assert [r.provider_call_id for r in reply.requests] == [
        "toolu-provider-1",
        "toolu-provider-2",
    ]
    result = run(ScriptedModel([reply, reply]))
    assert result.failure and result.failure.code == "INVALID_TOOL_REQUEST"
    assert all(r.result is None for r in result.trace if isinstance(r, ToolRecord))


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (400, "invalid_request_error"),
        (401, "authentication_error"),
        (429, "rate_limit_error"),
        (500, "api_error"),
        (503, "api_error"),
        (529, "overloaded_error"),
    ],
)
def test_http_error_no_sdk_retry_metadata_without_headers(
    status: int, error_type: str
) -> None:
    count = 0
    observed: list[ProviderObservation] = []
    body = json.dumps(
        {"type": "error", "error": {"type": error_type, "message": KEY}}
    ).encode()

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal count
        count += 1
        return httpx2.Response(
            status,
            content=body,
            headers={
                "request-id": "error-request-1",
                "retry-after": "0",
                "x-should-retry": "true",
                "authorization": KEY,
            },
        )

    reply = invoke(handler, observe=observed.append)
    assert count == 1
    assert isinstance(reply, FailureReply) and reply.code.value == "PROVIDER_ERROR"
    assert reply.message == "Claude HTTP error"
    assert reply.metadata.http_status == status
    assert reply.metadata.request_id == "error-request-1"
    assert reply.metadata.error_type == error_type
    assert reply.metadata.error_code is None
    assert reply.metadata.response_json is None
    assert reply.metadata.request_json is not None
    assert KEY not in reply.model_dump_json()
    assert observed == [
        ProviderObservation(body=body, http_status=status, request_id="error-request-1")
    ]


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json",
        b"\xff",
        b'{"error":"flat"}',
        b'{"type":"error","error":{"type":" "}}',
        b'{"type":"error","error":{"type":7}}',
        b'{"type":"rate_limit_error"}',
        b"[]",
    ],
)
def test_unrecognized_http_error_body_has_no_error_type(body: bytes) -> None:
    reply = invoke(lambda request: httpx2.Response(500, content=body))
    assert isinstance(reply, FailureReply) and reply.code.value == "PROVIDER_ERROR"
    assert reply.metadata.error_type is None and reply.metadata.error_code is None
    assert reply.metadata.request_id is None


@pytest.mark.parametrize("timeout", [False, True])
def test_network_and_sdk_timeout_do_not_retry(timeout: bool) -> None:
    count = 0
    observed: list[ProviderObservation] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal count
        count += 1
        if timeout:
            raise httpx2.ReadTimeout("test timeout", request=request)
        raise httpx2.ConnectError("test failure", request=request)

    reply = invoke(handler, observe=observed.append)
    assert isinstance(reply, FailureReply)
    assert reply.code.value == ("MODEL_TIMEOUT" if timeout else "PROVIDER_NETWORK")
    assert reply.metadata.request_json is not None
    assert reply.metadata.http_status is None
    assert count == 1
    assert observed == []


@pytest.mark.parametrize("sources", [("env",), ("client",), ("env", "client")])
def test_client_and_environment_beta_headers_never_reach_transport(
    sources: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    if "env" in sources:
        monkeypatch.setenv(
            "ANTHROPIC_CUSTOM_HEADERS",
            "anthropic-beta: env-fallback-beta\nx-env-header: kept",
        )
    options: dict[str, Any] = {}
    if "client" in sources:
        options["default_headers"] = {
            "anthropic-beta": "client-fallback-beta",
            "x-client-header": "kept",
        }
    sent: list[httpx2.Request] = []
    request = model_request()

    def handler(sent_request: httpx2.Request) -> httpx2.Response:
        sent.append(sent_request)
        return httpx2.Response(200, json=envelope([text(completion().content)]))

    async def call() -> ModelReply:
        async with client(handler, **options) as sdk:
            return await AnthropicResearchModel(sdk).invoke(request)

    reply = asyncio.run(call())
    assert isinstance(reply, StructuredReply)
    assert len(sent) == 1
    assert_clean_wire(sent[0])
    assert "fallback-beta" not in str(sent[0].headers)
    # Only the beta header is dropped; other caller headers still apply.
    if "env" in sources:
        assert sent[0].headers["x-env-header"] == "kept"
    if "client" in sources:
        assert sent[0].headers["x-client-header"] == "kept"


def test_injected_client_middleware_never_runs() -> None:
    calls: list[str] = []
    request = model_request()

    async def middleware(request: Any, call_next: Any) -> Any:
        calls.append("middleware")
        return await call_next(request)

    async def call() -> ModelReply:
        async with client(
            lambda request: httpx2.Response(
                200, json=envelope([text(completion().content)])
            ),
            middleware=[middleware],
        ) as sdk:
            return await AnthropicResearchModel(sdk).invoke(request)

    reply = asyncio.run(call())
    assert isinstance(reply, StructuredReply)
    assert calls == []


def test_observe_receives_exact_entity_before_decoding() -> None:
    body = json.dumps(envelope([text(completion().content)])).encode("utf-8")
    events: list[str] = []
    observed: list[ProviderObservation] = []

    def observe(observation: ProviderObservation) -> None:
        events.append("observe")
        observed.append(observation)

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=body, headers={"request-id": "obs-1"})

    decode = adapter_module.decode_response_entity

    def recording_decode(*args: Any) -> ModelReply:
        events.append("decode")
        return decode(*args)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(adapter_module, "decode_response_entity", recording_decode)
        reply = invoke(handler, observe=observe)
    assert events == ["observe", "decode"]
    assert observed == [
        ProviderObservation(body=body, http_status=200, request_id="obs-1")
    ]
    assert isinstance(reply, StructuredReply)
    assert reply.metadata.response_json == body.decode("utf-8")
    assert reply.metadata.request_id == "obs-1"


def test_observer_errors_propagate_before_decoding() -> None:
    class ObserverFailure(Exception):
        pass

    def observe(observation: ProviderObservation) -> None:
        raise ObserverFailure

    for status in (200, 500):

        def handler(request: httpx2.Request, status: int = status) -> httpx2.Response:
            return httpx2.Response(status, json=envelope([text("{}")]))

        with pytest.raises(ObserverFailure):
            invoke(handler, observe=observe)


@pytest.mark.parametrize("status", [200, 503])
@pytest.mark.parametrize("headers", [{}, {"request-id": ""}])
def test_missing_or_empty_request_id_header_is_none(
    status: int, headers: dict[str, str]
) -> None:
    observed: list[ProviderObservation] = []
    reply = invoke(
        lambda request: httpx2.Response(
            status, json=envelope([text(completion().content)]), headers=headers
        ),
        observe=observed.append,
    )
    assert isinstance(reply, StructuredReply if status == 200 else FailureReply)
    assert reply.metadata.request_id is None
    assert reply.metadata.http_status == status
    assert [o.request_id for o in observed] == [None]


def test_complete_graph_with_mocked_anthropic_transport() -> None:
    svc = service()
    result = svc.trend(BTC)
    evidence_id = result.model_dump(mode="json")["evidence"]["evidence_id"]
    responses = [
        tool_envelope(THINKING, tool_use()),
        envelope([THINKING, text(completion((evidence_id,)).content)]),
        envelope([text(structured(thesis((evidence_id,))).content)]),
        envelope([REDACTED, text(structured(critique()).content)]),
        envelope([text(structured(assessment("TRADE", (evidence_id,))).content)]),
    ]
    bodies: list[dict[str, Any]] = []
    raw: list[bytes] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        raw.append(request.content)
        bodies.append(assert_clean_wire(request))
        return httpx2.Response(
            200,
            json=responses.pop(0),
            headers={"request-id": f"request-{len(bodies)}"},
        )

    research = anthropic_research(
        handler,
        OpportunityResearchRequest(
            research_run_id="anthropic-mocked", opportunity=opportunity()
        ),
    )
    assert (
        research.status == "COMPLETED" and research.assessment.outcome.value == "TRADE"
    )
    assert research.provider.provider == "anthropic"
    assert research.provider.adapter_version == ADAPTER_VERSION
    assert len(bodies) == 5
    assert not responses
    calls = [r for r in research.trace if isinstance(r, ModelCallRecord)]
    assert [(r.reply.metadata.request_json or "").encode() for r in calls] == raw
    assert [r.reply.metadata.request_id for r in calls] == [
        f"request-{i}" for i in range(1, 6)
    ]
    assert all(
        [m["role"] for m in b["messages"]] == ["user"] and isinstance(b["system"], str)
        for b in bodies
    )
    assert all("tools" in b and "tool_choice" in b for b in bodies[:2])
    assert all("tools" not in b and "tool_choice" not in b for b in bodies[2:])
    tools = [r for r in research.trace if isinstance(r, ToolRecord)]
    assert len(tools) == 1 and tools[0].result is not None


@pytest.mark.parametrize(
    "content",
    [
        "{bad",
        '{"summary":"only one field"}',
        '{"summary":"x","relevant_evidence_ids":[],"remaining_gaps":[],"confidence":80}',
    ],
)
def test_invalid_json_schema_interrupted_without_retry(content: str) -> None:
    reply = invoke(lambda request: httpx2.Response(200, json=envelope([text(content)])))
    assert isinstance(reply, StructuredReply) and reply.content == content
    model = ScriptedModel([reply])
    result = run(model)
    assert result.failure and result.failure.code == "INVALID_MODEL_OUTPUT"
    assert len(model.requests) == 1
