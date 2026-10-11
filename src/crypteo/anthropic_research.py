"""Claude Messages adapter. Async, stateless, no SDK retries or vendor state."""

from typing import Any, Literal

import anthropic
import httpx2
from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncAnthropic,
    omit,
)
from pydantic import ValidationError

from crypteo.research_model import (
    FailureReply,
    ModelOutputError,
    ModelReply,
    ModelRequest,
    ProviderFailureCode,
    ProviderIdentity,
    ProviderMetadata,
    ProviderObservation,
    RefusalReply,
    ResponseObserver,
    StructuredReply,
    ToolReply,
    ToolRequest,
    parse_json,
    render_json,
    validate_unicode,
)

ADAPTER_VERSION = "anthropic_messages_research_v1"
# Sent per request, so client default headers and the environment cannot change it.
ANTHROPIC_VERSION = "2023-06-01"

# Claude structured outputs reject these keywords. Project validation still
# enforces them; the wire schema only states them in descriptions. Fixed order.
UNSUPPORTED_SCHEMA_CONSTRAINTS = (
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "uniqueItems",
)
# Keywords whose value is a subschema (or, for a list, a list of subschemas)
# versus a map of names to subschemas. Every other value is data, copied as is.
_SUBSCHEMA_KEYWORDS = frozenset(
    {
        "additionalItems",
        "additionalProperties",
        "allOf",
        "anyOf",
        "contains",
        "else",
        "if",
        "items",
        "not",
        "oneOf",
        "prefixItems",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)
_SUBSCHEMA_MAP_KEYWORDS = frozenset(
    {"$defs", "definitions", "dependentSchemas", "patternProperties", "properties"}
)


def render_anthropic_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Copy a JSON schema without Claude-unsupported constraints, iteratively.

    Constraints are removed only at schema positions, never from property names,
    enum or const data, and each removal is noted in that subschema's description.
    """
    result: dict[str, Any] = {}
    pending: list[tuple[Any, Any, Literal["schema", "map", "data"]]] = [
        (schema, result, "schema")
    ]
    while pending:
        source, target, kind = pending.pop()
        if isinstance(source, list):
            element: Literal["schema", "data"] = (
                "schema" if kind == "schema" else "data"
            )
            for item in source:
                target.append(_shell(item))
                if isinstance(item, dict | list):
                    pending.append((item, target[-1], element))
            continue
        for key, value in source.items():
            if kind == "schema" and key in UNSUPPORTED_SCHEMA_CONSTRAINTS:
                continue
            child: Literal["schema", "map", "data"] = "data"
            if kind == "map" or (kind == "schema" and key in _SUBSCHEMA_KEYWORDS):
                child = "schema"
            elif kind == "schema" and key in _SUBSCHEMA_MAP_KEYWORDS:
                child = "map"
            target[key] = _shell(value)
            if isinstance(value, dict | list):
                pending.append((value, target[key], child))
        removed = [k for k in UNSUPPORTED_SCHEMA_CONSTRAINTS if k in source]
        if kind == "schema" and removed:
            note = (
                "Constraints: "
                + ", ".join(f"{k} {render_json(source[k])}" for k in removed)
                + "."
            )
            description = source.get("description")
            if description is not None and not isinstance(description, str):
                raise ValueError("schema description must be a string")
            target["description"] = f"{description}\n\n{note}" if description else note
    return result


def _shell(value: Any) -> Any:
    """Empty container to fill later, or the immutable scalar itself."""
    if isinstance(value, dict):
        return {}
    if isinstance(value, list):
        return []
    return value


def render_anthropic_request(request: ModelRequest) -> dict[str, Any]:
    """Exact credential-free wire body, retained with every adapter result."""
    request = ModelRequest.model_validate(request)
    if request.configuration.provider != "anthropic":
        raise ValueError("Claude adapter requires provider=anthropic")
    leading = 0
    while (
        leading < len(request.messages) and request.messages[leading].role == "system"
    ):
        leading += 1
    system = [m.content for m in request.messages[:leading]]
    messages = request.messages[leading:]
    if not messages or any(m.role != "user" for m in messages):
        raise ValueError("Claude requests need system messages before user messages")
    parameters = request.configuration.parameters
    output_config: dict[str, Any] = {
        "format": {
            "type": "json_schema",
            "schema": render_anthropic_schema(
                request.output_schema.model_dump(mode="json")["json_schema"]
            ),
        }
    }
    if parameters.effort is not None:
        output_config["effort"] = parameters.effort
    body: dict[str, Any] = {
        "model": request.configuration.model,
        "max_tokens": request.max_output_tokens,
        "messages": [m.model_dump(mode="json") for m in messages],
        "output_config": output_config,
    }
    if system:
        body["system"] = "\n\n".join(system)
    if request.tools:
        body["tools"] = [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": render_anthropic_schema(
                    t.model_dump(mode="json")["parameters"]
                ),
                "strict": True,
            }
            for t in request.tools
        ]
        body["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
    return body


class AnthropicResearchModel:
    """Caller owns client lifetime. The adapter disables retries even on injection."""

    def __init__(self, client: AsyncAnthropic) -> None:
        # Client middleware runs inside the SDK retry loop and may rewrite, resend
        # or substitute another model's reply, so injected middleware is dropped.
        self._client = client.with_options(max_retries=0, timeout=180.0, middleware=[])

    @property
    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(
            provider="anthropic",
            adapter_version=ADAPTER_VERSION,
            sdk_version=anthropic.__version__,
        )

    def prepare(self, request: ModelRequest) -> ModelRequest:
        return ModelRequest.model_validate(
            request.model_copy(
                update={
                    "prepared_input_json": render_json(
                        render_anthropic_request(request)
                    ),
                }
            )
        )

    async def invoke(self, request: ModelRequest) -> ModelReply:
        return await self._invoke(request, None)

    async def invoke_observed(
        self, request: ModelRequest, observe: ResponseObserver
    ) -> ModelReply:
        return await self._invoke(request, observe)

    async def _invoke(
        self, request: ModelRequest, observe: ResponseObserver | None
    ) -> ModelReply:
        if request.prepared_input_json is None:
            request = self.prepare(request)
        encoded = request.prepared_input_json
        if encoded is None:
            raise ValueError("Claude request was not prepared")
        metadata = ProviderMetadata(request_json=encoded)
        try:
            # The SDK's byte-content path preserves the exact prepared body, while
            # retaining its authentication, HTTP handling and disabled retries.
            # Client and environment beta headers are dropped so no beta feature,
            # such as server-side fallback, can change which model answers.
            response = await self._client.post(
                "/v1/messages",
                cast_to=httpx2.Response,
                content=encoded.encode("utf-8"),
                options={
                    "headers": {
                        "anthropic-beta": omit,
                        "anthropic-version": ANTHROPIC_VERSION,
                    }
                },
            )
        except APITimeoutError:
            return FailureReply(
                code=ProviderFailureCode.TIMEOUT,
                message="Claude request timed out",
                metadata=metadata,
            )
        except APIConnectionError:
            return FailureReply(
                code=ProviderFailureCode.NETWORK,
                message="Claude connection failed",
                metadata=metadata,
            )
        except APIStatusError as error:
            request_id = error.request_id or None
            if observe is not None:
                observe(
                    ProviderObservation(
                        body=error.response.content,
                        http_status=error.status_code,
                        request_id=request_id,
                        text_encoding="utf-8",
                    )
                )
            return decode_http_error(
                error.response.content,
                error.status_code,
                request_id,
                encoded,
            )
        request_id = response.headers.get("request-id") or None
        if observe is not None:
            observe(
                ProviderObservation(
                    body=response.content,
                    http_status=response.status_code,
                    request_id=request_id,
                    text_encoding="utf-8",
                )
            )
        metadata = ProviderMetadata(
            request_json=metadata.request_json,
            request_id=request_id,
            http_status=response.status_code,
        )
        return decode_response_entity(response.content, metadata)


def decode_response_entity(body: bytes, metadata: ProviderMetadata) -> ModelReply:
    """Interpret an observed entity without repairing malformed UTF-8."""
    try:
        content = body.decode("utf-8")
    except UnicodeDecodeError:
        return FailureReply(
            code=ProviderFailureCode.INVALID_ENVELOPE,
            message="Claude response body is not valid UTF-8",
            metadata=metadata,
        )
    return _decode_response(
        ProviderMetadata.model_validate(
            metadata.model_copy(update={"response_json": content})
        )
    )


def decode_http_error(
    body: bytes,
    status: int,
    request_id: str | None,
    request_json: str,
) -> FailureReply:
    """Normalize only project-owned error fields after observation has committed."""
    error_type: str | None = None
    try:
        parsed = parse_json(body.decode("utf-8"))
        if isinstance(parsed, dict):
            error = parsed.get("error")
            if isinstance(error, dict):
                value = error.get("type")
                error_type = value if isinstance(value, str) and value.strip() else None
    except (ModelOutputError, UnicodeDecodeError):
        pass
    return FailureReply(
        code=ProviderFailureCode.ERROR,
        message="Claude HTTP error",
        metadata=ProviderMetadata(
            request_json=request_json,
            request_id=request_id,
            http_status=status,
            error_type=error_type,
        ),
    )


def _requested_model(request_json: str | None) -> str | None:
    if request_json is None:
        return None
    try:
        request = parse_json(request_json)
    except ModelOutputError:
        return None
    model = request.get("model") if isinstance(request, dict) else None
    return model if isinstance(model, str) else None


def _decode_response(metadata: ProviderMetadata) -> ModelReply:
    """Validate only documented observable output kinds; retain the whole body."""
    try:
        if metadata.response_json is None:
            raise ModelOutputError("missing response body")
        body = parse_json(metadata.response_json, validate_decoded_unicode=False)
        if not isinstance(body, dict):
            raise ModelOutputError("response must be an object")
        # Native tool input is decoded JSON, so render it now: only that opaque
        # argument view may carry an escaped invalid scalar for local rejection.
        # Identity, names, structural fields and all other content remain strict.
        arguments: dict[int, str] = {}
        invalid_arguments: set[int] = set()
        if isinstance(body.get("content"), list):
            for index, block in enumerate(body["content"]):
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and isinstance(block.get("input"), dict)
                ):
                    try:
                        rendered = render_json(block["input"])
                    except (ValueError, RecursionError) as error:
                        raise ModelOutputError("unrenderable tool input") from error
                    try:
                        validate_unicode(rendered)
                    except ModelOutputError:
                        invalid_arguments.add(index)
                        rendered = rendered.encode(
                            "utf-8", errors="backslashreplace"
                        ).decode("utf-8")
                    arguments[index] = rendered
                    block["input"] = {}
        validate_unicode(body)
        for field in ("id", "model", "stop_reason"):
            if not isinstance(body.get(field), str) or not body[field].strip():
                raise ModelOutputError("missing response identity/stop reason")
        metadata = ProviderMetadata.model_validate(
            metadata.model_dump()
            | {
                "response_id": body["id"],
                "returned_model": body["model"],
                "status": body["stop_reason"],
                "usage": body.get("usage"),
            }
        )
        if (
            body.get("type") != "message"
            or body.get("role") != "assistant"
            or not isinstance(body.get("content"), list)
        ):
            raise ModelOutputError("malformed message envelope")
        # Applies to every stop reason: no other model's reply becomes output.
        requested = _requested_model(metadata.request_json)
        if requested is None:
            return FailureReply(
                code=ProviderFailureCode.INVALID_ENVELOPE,
                message="Claude reply lacks its request",
                metadata=metadata,
            )
        if body["model"] != requested:
            return FailureReply(
                code=ProviderFailureCode.INVALID_ENVELOPE,
                message="Claude returned a different model",
                metadata=metadata,
            )
        stop_reason = body["stop_reason"]
        if stop_reason == "max_tokens":
            return FailureReply(
                code=ProviderFailureCode.INCOMPLETE,
                message="Claude response stop_reason: max_tokens",
                metadata=metadata,
            )
        if stop_reason == "refusal":
            details = body.get("stop_details")
            details = details if isinstance(details, dict) else {}
            explanation = details.get("explanation")
            category = details.get("category")
            if not isinstance(explanation, str) or not explanation.strip():
                if not isinstance(category, str) or not category.strip():
                    category = "unspecified"
                explanation = f"refusal: {category}"
            return RefusalReply(refusal=explanation, metadata=metadata)
        if stop_reason not in ("tool_use", "end_turn"):
            raise ModelOutputError("unsupported stop reason")
        tools: list[ToolRequest] = []
        texts: list[str] = []
        for index, block in enumerate(body["content"]):
            if not isinstance(block, dict):
                raise ModelOutputError("malformed content block")
            if block.get("type") == "tool_use":
                if index not in arguments:
                    raise ModelOutputError("tool input must be an object")
                if not isinstance(block.get("id"), str) or not block["id"].strip():
                    raise ModelOutputError("tool use requires id")
                tools.append(
                    ToolRequest.model_validate(
                        {
                            "name": block.get("name"),
                            "arguments_json": arguments[index],
                            "argument_error": "INVALID_UNICODE"
                            if index in invalid_arguments
                            else None,
                            "provider_call_id": block["id"],
                        }
                    )
                )
            elif block.get("type") == "text":
                if not isinstance(block.get("text"), str):
                    raise ModelOutputError("malformed text block")
                texts.append(block["text"])
            elif block.get("type") in ("thinking", "redacted_thinking"):
                # Opaque provider reasoning is not interpreted or represented as
                # hidden chain of thought. Any observable fields remain in raw body.
                continue
            else:
                raise ModelOutputError("unsupported content block")
        if stop_reason == "tool_use":
            if not tools:
                raise ModelOutputError("tool_use stop without tool use")
            # Text alongside tool use stays in the raw body only. Preserve all
            # returned calls; the workflow rejects multiple requests locally as
            # one invalid admitted attempt, dispatching none.
            return ToolReply(requests=tuple(tools), metadata=metadata)
        if tools or len(texts) != 1:
            raise ModelOutputError("expected one structured output")
        return StructuredReply(content=texts[0], metadata=metadata)
    except (ModelOutputError, ValidationError):
        return FailureReply(
            code=ProviderFailureCode.INVALID_ENVELOPE,
            message="Claude response envelope failed validation",
            metadata=metadata,
        )
