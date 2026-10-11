"""Project-owned model contracts, lossless serialization and strict JSON parsing."""

import json
from typing import Any

import pytest
from pydantic import ValidationError
from research_support import ScriptedModel, run, tool

from crypteo.research_model import (
    MAX_JSON_DEPTH,
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


def nested(depth: int, inner: str = "0") -> str:
    """Alternate arrays and objects so both container kinds count toward depth."""
    content = inner
    for level in range(depth):
        content = f"[{content}]" if level % 2 else f'{{"k":{content}}}'
    return content


def test_json_depth_limit_is_fixed_at_256() -> None:
    assert MAX_JSON_DEPTH == 256


@pytest.mark.parametrize("wrap", ["[{}]", '{{"k":{}}}'])
def test_json_nesting_at_the_limit_parses(wrap: str) -> None:
    content = nested(MAX_JSON_DEPTH - 1)
    parsed = parse_json(wrap.format(content))
    for _ in range(MAX_JSON_DEPTH):
        parsed = parsed[0] if isinstance(parsed, list) else parsed["k"]
    assert parsed == 0


@pytest.mark.parametrize(
    "content",
    [
        nested(MAX_JSON_DEPTH + 1),
        "[" * (MAX_JSON_DEPTH + 1) + "]" * (MAX_JSON_DEPTH + 1),
        "{" + '"k":{' * MAX_JSON_DEPTH + "}" * (MAX_JSON_DEPTH + 1),
        "[" * 2000 + "0" + "]" * 2000,
    ],
)
def test_json_nesting_beyond_the_limit_is_invalid_before_decoding(
    content: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_decoding(*args: object, **kwargs: object) -> None:
        raise AssertionError("depth must be rejected before json.loads")

    monkeypatch.setattr("crypteo.research_model.json.loads", no_decoding)
    with pytest.raises(ModelOutputError, match="^invalid JSON$"):
        parse_json(content)


@pytest.mark.parametrize(
    "text",
    [
        "[" * 300,
        "{" * 300 + "]" * 5,
        '\\"' + "[" * 300,
        "\\\\" + "{[" * 200,
        '\\"[\\"' * 300,
    ],
)
def test_json_brackets_inside_strings_do_not_count_toward_depth(text: str) -> None:
    content = nested(MAX_JSON_DEPTH - 1, '"' + text + '"')
    parsed = parse_json(f"[{content}]")
    for _ in range(MAX_JSON_DEPTH):
        parsed = parsed[0] if isinstance(parsed, list) else parsed["k"]
    assert parsed == json.loads('"' + text + '"')


def test_json_string_end_after_escaped_backslash_restores_depth_counting() -> None:
    content = '["\\\\", ' + "[" * MAX_JSON_DEPTH + "]" * MAX_JSON_DEPTH + "]"
    with pytest.raises(ModelOutputError, match="^invalid JSON$"):
        parse_json(content)


def test_json_object_keys_containing_brackets_do_not_count_toward_depth() -> None:
    key = json.dumps("[{" * 300 + '"')
    content = nested(MAX_JSON_DEPTH - 1)
    assert parse_json(f"{{{key}:{content}}}") == {json.loads(key): parse_json(content)}


@pytest.mark.parametrize(
    "parameters",
    [
        {"temperature": 0.5},
        {"reasoning_effort": "high"},
        {"effort": "minimal"},
        {"effort": "none"},
        {"effort": "HIGH"},
        {"effort": None, "temperature": None},
        {"max_retries": 3},
        {"cutoff": "later"},
    ],
)
def test_explicit_model_parameters_reject_unknown_or_invalid_values(
    parameters: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        ModelParameters.model_validate(parameters)


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max", None])
def test_model_parameters_accept_only_claude_effort_levels(effort: str | None) -> None:
    parameters = ModelParameters.model_validate({"effort": effort})
    assert parameters.effort == effort
    assert parameters.model_dump() == {"effort": effort}
    assert ModelParameters() == ModelParameters(effort=None)


def test_model_identity_must_be_explicit() -> None:
    for values in ({"provider": "anthropic"}, {"provider": "anthropic", "model": " "}):
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
