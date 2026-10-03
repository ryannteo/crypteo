"""Project-owned model contracts, lossless serialization and strict JSON parsing."""

from typing import Any

import pytest
from pydantic import ValidationError
from research_support import ScriptedModel, run, tool

from crypteo.research_model import (
    ModelConfiguration,
    ModelOutputError,
    ModelParameters,
    ModelRequest,
    ProviderMetadata,
    ToolReply,
    ToolRequest,
    parse_json,
)


@pytest.mark.parametrize(
    "content", ['{"x":1,"x":2}', '{"x":NaN}', "Infinity", "{invalid"]
)
def test_json_parser_never_repairs_duplicates_nonfinite_or_malformed(
    content: str,
) -> None:
    with pytest.raises(ModelOutputError):
        parse_json(content)


@pytest.mark.parametrize(
    "parameters",
    [
        {"temperature": True},
        {"temperature": "1"},
        {"reasoning_effort": "arbitrary"},
        {"max_retries": 3},
        {"cutoff": "later"},
    ],
)
def test_explicit_model_parameters_reject_unknown_or_invalid_values(
    parameters: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        ModelParameters.model_validate(parameters)


def test_model_identity_must_be_explicit() -> None:
    for values in ({"provider": "openai"}, {"provider": "openai", "model": " "}):
        with pytest.raises(ValidationError):
            ModelConfiguration.model_validate(values)


def test_model_request_tool_and_metadata_nested_revalidation_and_immutability() -> None:
    model = ScriptedModel([tool()])
    run(model)
    request = model.requests[0]
    mutable: dict[str, Any] = {"items": [{"value": 1}]}
    definition = request.tools[0].model_copy(update={"parameters": mutable})
    restored = ModelRequest.model_validate(
        request.model_copy(update={"tools": [definition]})
    )
    before = restored.model_dump_json()
    mutable["items"][0]["value"] = 2
    assert restored.model_dump_json() == before
    assert ModelRequest.model_validate_json(before) == restored
    with pytest.raises(ValidationError):
        ModelRequest.model_validate(
            request.model_copy(
                update={
                    "configuration": request.configuration.model_copy(
                        update={"model": " "}
                    )
                }
            )
        )
    with pytest.raises(ValidationError):
        ModelRequest.model_validate(
            model.requests[-1].model_copy(update={"tools": request.tools})
        )
    requests = [ToolRequest(name="trend", arguments_json="{}")]
    reply = ToolReply.model_validate({"requests": requests})
    requests.append(ToolRequest(name="levels", arguments_json="{}"))
    assert len(reply.requests) == 1
    usage: dict[str, Any] = {"tokens": 1}
    metadata = ProviderMetadata.model_validate({"usage": usage})
    usage["tokens"] = 2
    assert metadata.usage is not None and metadata.usage["tokens"] == 1
