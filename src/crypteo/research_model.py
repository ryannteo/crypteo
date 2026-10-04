"""Project-owned async model boundary; no vendor objects cross this module."""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Any, Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from crypteo.domain.market_research import Digest
from crypteo.domain.research import ImmutableJsonObject, NonBlankText


class ModelContract(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    @field_validator("*", mode="after")
    @classmethod
    def validate_unicode(cls, value: Any) -> Any:
        validate_unicode(value)
        return value


def content_digest(content: str) -> str:
    return "sha256:" + sha256(content.encode("utf-8")).hexdigest()


def render_json(value: Any) -> str:
    """Lossless compact JSON for project models and already serialized values."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


class ModelOutputError(ValueError):
    """Only untrusted model/provider JSON errors, never programmer invariants."""


def validate_unicode(value: Any) -> None:
    """Reject surrogate code points in keys and values without recursive traversal."""
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as error:
                raise ModelOutputError("invalid Unicode surrogate scalar") from error
        elif isinstance(item, Mapping):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)


def parse_json(content: str, *, validate_decoded_unicode: bool = True) -> Any:
    """Strict decoding, with optional field-specific decoded Unicode validation."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ModelOutputError("duplicate JSON key")
            result[key] = value
        return result

    def constant(value: str) -> Any:
        raise ModelOutputError(f"nonfinite JSON constant: {value}")

    validate_unicode(content)
    try:
        parsed = json.loads(content, object_pairs_hook=pairs, parse_constant=constant)
    except ModelOutputError:
        raise
    except (ValueError, RecursionError) as error:
        # This catch is confined to the JSON parser: integer conversion limits,
        # nesting limits and syntax errors are all untrusted input failures.
        raise ModelOutputError("invalid JSON") from error
    if validate_decoded_unicode:
        validate_unicode(parsed)
    return parsed


class ContentIdentity(ModelContract):
    version: NonBlankText
    digest: Digest


class ModelParameters(ModelContract):
    """Explicit supported parameters only; no arbitrary provider kwargs."""

    temperature: Annotated[float, Field(strict=True, ge=0, le=2)] | None = None
    reasoning_effort: (
        Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None
    ) = None


class ModelConfiguration(ModelContract):
    provider: NonBlankText
    model: NonBlankText
    parameters: ModelParameters = ModelParameters()


class ProviderIdentity(ModelContract):
    provider: NonBlankText
    adapter_version: NonBlankText
    sdk_version: NonBlankText


class ModelMessage(ModelContract):
    role: Literal["system", "user"]
    content: NonBlankText


class ToolDefinition(ModelContract):
    name: NonBlankText
    description: NonBlankText
    parameters: ImmutableJsonObject
    identity: ContentIdentity


class StructuredSchema(ModelContract):
    name: NonBlankText
    json_schema: ImmutableJsonObject
    identity: ContentIdentity


class ModelRequest(ModelContract):
    call_id: NonBlankText
    stage: Literal["investigate", "draft_thesis", "critique", "final_assessment"]
    configuration: ModelConfiguration
    messages: tuple[ModelMessage, ...]
    prompt_identity: ContentIdentity
    tools: tuple[ToolDefinition, ...] = ()
    output_schema: StructuredSchema
    max_output_tokens: Annotated[int, Field(strict=True, gt=0)]
    parallel_tool_calls: Literal[False] = False
    prepared_input_json: NonBlankText | None = None

    @model_validator(mode="after")
    def validate_tools(self) -> Self:
        if not self.messages or (self.stage != "investigate" and self.tools):
            raise ValueError("only investigation may have tools")
        return self


class ProviderMetadata(ModelContract):
    """Observable body and IDs only. Never headers, credentials, or SDK objects."""

    response_id: NonBlankText | None = None
    request_id: NonBlankText | None = None
    returned_model: NonBlankText | None = None
    status: NonBlankText | None = None
    usage: ImmutableJsonObject | None = None
    request_json: NonBlankText | None = None
    response_json: Annotated[str, Field(strict=True)] | None = None
    # Escaped diagnostic view of an invalid project reply, never an HTTP entity.
    rejected_reply_json: Annotated[str, Field(strict=True)] | None = None
    http_status: Annotated[int, Field(strict=True)] | None = None
    error_type: NonBlankText | None = None
    error_code: NonBlankText | None = None


class ToolRequest(ModelContract):
    # Even an empty/unknown model-authored name is an invalid tool attempt,
    # handled with typed feedback rather than a provider-envelope interruption.
    name: str = Field(strict=True)
    arguments_json: str = Field(strict=True)
    # Native outer JSON can materialize invalid scalars inside opaque arguments.
    # Retain an escaped view, explicitly marked for rejection by the tool parser;
    # the exact original provider body remains in ProviderMetadata.response_json.
    argument_error: Literal["INVALID_UNICODE"] | None = None
    provider_call_id: NonBlankText | None = None


class ProviderFailureCode(StrEnum):
    NETWORK = "PROVIDER_NETWORK"
    ERROR = "PROVIDER_ERROR"
    TIMEOUT = "MODEL_TIMEOUT"
    INCOMPLETE = "INCOMPLETE_OUTPUT"
    INVALID_ENVELOPE = "INVALID_PROVIDER_ENVELOPE"
    INVALID_OUTPUT = "INVALID_MODEL_OUTPUT"


class StructuredReply(ModelContract):
    kind: Literal["structured"] = "structured"
    content: str = Field(strict=True)
    metadata: ProviderMetadata = ProviderMetadata()


class ToolReply(ModelContract):
    kind: Literal["tools"] = "tools"
    requests: tuple[ToolRequest, ...]
    metadata: ProviderMetadata = ProviderMetadata()


class RefusalReply(ModelContract):
    kind: Literal["refusal"] = "refusal"
    refusal: NonBlankText
    metadata: ProviderMetadata = ProviderMetadata()


class FailureReply(ModelContract):
    kind: Literal["failure"] = "failure"
    code: ProviderFailureCode
    message: NonBlankText
    metadata: ProviderMetadata = ProviderMetadata()


ModelReply = Annotated[
    StructuredReply | ToolReply | RefusalReply | FailureReply,
    Field(discriminator="kind"),
]


class ResearchModel(Protocol):
    @property
    def identity(self) -> ProviderIdentity: ...

    def prepare(self, request: ModelRequest) -> ModelRequest:
        """Attach the canonical provider-facing JSON used for size and transport."""
        ...

    async def invoke(self, request: ModelRequest) -> ModelReply: ...


@dataclass(frozen=True)
class ProviderObservation:
    """Observable HTTP entity body, before project decoding; no headers/SDK state."""

    body: bytes
    http_status: int
    request_id: str | None = None
    text_encoding: str = "utf-8"


ResponseObserver = Callable[[ProviderObservation], None]


@runtime_checkable
class ObservableResearchModel(ResearchModel, Protocol):
    async def invoke_observed(
        self, request: ModelRequest, observe: ResponseObserver
    ) -> ModelReply:
        """Observe every available entity before decoding; observer errors propagate."""
        ...
