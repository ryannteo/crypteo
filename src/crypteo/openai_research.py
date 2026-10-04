"""OpenAI Responses adapter. Async, stateless, no SDK retries or vendor state."""

from typing import Any

import openai
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    HttpxBinaryResponseContent,
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

ADAPTER_VERSION = "openai_responses_research_v1"


def render_openai_request(request: ModelRequest) -> dict[str, Any]:
    """Exact credential-free wire body, retained with every adapter result."""
    request = ModelRequest.model_validate(request)
    if request.configuration.provider != "openai":
        raise ValueError("OpenAI adapter requires provider=openai")
    body: dict[str, Any] = {
        "model": request.configuration.model,
        "input": [m.model_dump(mode="json") for m in request.messages],
        "max_output_tokens": request.max_output_tokens,
        "parallel_tool_calls": False,
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": request.output_schema.name,
                "schema": request.output_schema.model_dump(mode="json")["json_schema"],
                "strict": True,
            }
        },
    }
    if request.tools:
        body["tools"] = [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.model_dump(mode="json")["parameters"],
                "strict": True,
            }
            for t in request.tools
        ]
        body["tool_choice"] = "auto"
    parameters = request.configuration.parameters
    if parameters.temperature is not None:
        body["temperature"] = parameters.temperature
    if parameters.reasoning_effort is not None:
        body["reasoning"] = {"effort": parameters.reasoning_effort}
    return body


class OpenAIResearchModel:
    """Caller owns client lifetime. The adapter disables retries even on injection."""

    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client.with_options(max_retries=0, timeout=60.0)

    @property
    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(
            provider="openai",
            adapter_version=ADAPTER_VERSION,
            sdk_version=openai.__version__,
        )

    def prepare(self, request: ModelRequest) -> ModelRequest:
        return ModelRequest.model_validate(
            request.model_copy(
                update={
                    "prepared_input_json": render_json(render_openai_request(request)),
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
            raise ValueError("OpenAI request was not prepared")
        metadata = ProviderMetadata(request_json=encoded)
        try:
            # The SDK's public byte-content path preserves the exact prepared body,
            # while retaining its authentication, HTTP handling and disabled retries.
            raw_response = await self._client.post(
                "/responses",
                cast_to=HttpxBinaryResponseContent,
                content=encoded.encode("utf-8"),
            )
        except APITimeoutError:
            return FailureReply(
                code=ProviderFailureCode.TIMEOUT,
                message="OpenAI request timed out",
                metadata=metadata,
            )
        except APIConnectionError:
            return FailureReply(
                code=ProviderFailureCode.NETWORK,
                message="OpenAI connection failed",
                metadata=metadata,
            )
        except APIStatusError as error:
            if observe is not None:
                observe(
                    ProviderObservation(
                        body=error.response.content,
                        http_status=error.status_code,
                        request_id=error.request_id,
                        text_encoding="utf-8",
                    )
                )
            return decode_http_error(
                error.response.content,
                error.status_code,
                error.request_id,
                encoded,
            )
        response = raw_response.response
        if observe is not None:
            observe(
                ProviderObservation(
                    body=response.content,
                    http_status=response.status_code,
                    request_id=response.headers.get("x-request-id") or None,
                    text_encoding="utf-8",
                )
            )
        metadata = ProviderMetadata(
            request_json=metadata.request_json,
            request_id=response.headers.get("x-request-id") or None,
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
            message="OpenAI response body is not valid UTF-8",
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
    code: str | None = None
    error_type: str | None = None
    try:
        parsed = parse_json(body.decode("utf-8"))
        if isinstance(parsed, dict):
            error = parsed.get("error", parsed)
            if isinstance(error, dict):
                value = error.get("code")
                code = value if isinstance(value, str) and value.strip() else None
                value = error.get("type")
                error_type = value if isinstance(value, str) and value.strip() else None
    except (ModelOutputError, UnicodeDecodeError):
        pass
    return FailureReply(
        code=ProviderFailureCode.ERROR,
        message="OpenAI HTTP error",
        metadata=ProviderMetadata(
            request_json=request_json,
            request_id=request_id,
            http_status=status,
            error_code=code,
            error_type=error_type,
        ),
    )


def _decode_response(metadata: ProviderMetadata) -> ModelReply:
    """Validate only documented observable output kinds; retain the whole body."""
    try:
        if metadata.response_json is None:
            raise ModelOutputError("missing response body")
        body = parse_json(metadata.response_json, validate_decoded_unicode=False)
        if not isinstance(body, dict):
            raise ModelOutputError("response must be an object")
        invalid_arguments: set[int] = set()
        if isinstance(body.get("output"), list):
            for index, item in enumerate(body["output"]):
                if (
                    isinstance(item, dict)
                    and item.get("type") == "function_call"
                    and isinstance(item.get("arguments"), str)
                ):
                    try:
                        validate_unicode(item["arguments"])
                    except ModelOutputError:
                        invalid_arguments.add(index)
                        item["arguments"] = (
                            item["arguments"]
                            .encode("utf-8", errors="backslashreplace")
                            .decode("utf-8")
                        )
        # Only opaque native argument strings have a safe escaped view now.
        # Identity, names, structural fields and all other content remain strict.
        validate_unicode(body)
        for field in ("id", "model", "status"):
            if not isinstance(body.get(field), str) or not body[field].strip():
                raise ModelOutputError("missing response identity/status")
        metadata = ProviderMetadata.model_validate(
            metadata.model_dump()
            | {
                "response_id": body["id"],
                "returned_model": body["model"],
                "status": body["status"],
                "usage": body.get("usage"),
            }
        )
        if body["status"] in ("incomplete", "failed", "cancelled"):
            return FailureReply(
                code=ProviderFailureCode.INCOMPLETE,
                message=f"OpenAI response status: {body['status']}",
                metadata=metadata,
            )
        if body["status"] != "completed" or not isinstance(body.get("output"), list):
            raise ModelOutputError("unexpected response status/output")
        tools: list[ToolRequest] = []
        texts: list[str] = []
        refusals: list[str] = []
        for index, item in enumerate(body["output"]):
            if not isinstance(item, dict):
                raise ModelOutputError("malformed output item")
            if item.get("type") == "function_call":
                if item.get("status") not in (None, "completed"):
                    raise ModelOutputError("incomplete function call")
                tools.append(
                    ToolRequest.model_validate(
                        {
                            "name": item.get("name"),
                            "arguments_json": item.get("arguments"),
                            "argument_error": "INVALID_UNICODE"
                            if index in invalid_arguments
                            else None,
                            "provider_call_id": item.get("call_id"),
                        }
                    )
                )
                if tools[-1].provider_call_id is None:
                    raise ModelOutputError("function call requires call_id")
            elif item.get("type") == "message":
                if (
                    item.get("role") != "assistant"
                    or item.get("status") != "completed"
                    or not isinstance(item.get("content"), list)
                ):
                    raise ModelOutputError("malformed message envelope")
                for part in item["content"]:
                    if not isinstance(part, dict):
                        raise ModelOutputError("malformed message content")
                    if part.get("type") == "output_text" and isinstance(
                        part.get("text"), str
                    ):
                        texts.append(part["text"])
                    elif part.get("type") == "refusal" and isinstance(
                        part.get("refusal"), str
                    ):
                        refusals.append(part["refusal"])
                    else:
                        raise ModelOutputError("unsupported message content")
            elif item.get("type") == "reasoning":
                # Opaque provider reasoning is not interpreted or represented as
                # hidden chain of thought. Any observable fields remain in raw body.
                continue
            else:
                raise ModelOutputError("unsupported output item")
        if refusals:
            if tools or texts or len(refusals) != 1:
                raise ModelOutputError("ambiguous refusal envelope")
            return RefusalReply(refusal=refusals[0], metadata=metadata)
        if tools:
            if texts:
                raise ModelOutputError("mixed function and structured output")
            # Preserve all returned calls; the workflow rejects multiple requests
            # locally as one invalid admitted attempt, dispatching none.
            return ToolReply(requests=tuple(tools), metadata=metadata)
        if len(texts) != 1:
            raise ModelOutputError("expected one structured output")
        return StructuredReply(content=texts[0], metadata=metadata)
    except (ModelOutputError, ValidationError):
        return FailureReply(
            code=ProviderFailureCode.INVALID_ENVELOPE,
            message="OpenAI response envelope failed validation",
            metadata=metadata,
        )
