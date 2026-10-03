"""Locked policy, immutable nested inputs, and result semantics."""

import json
from datetime import datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError
from research_support import ScriptedModel, T, assessment, opportunity, run, tool

from crypteo.domain.research_workflow import (
    OpportunityResearchRequest,
    ResearchBudgetPolicy,
    ResearchFailure,
    ResearchRunResult,
    ResearchWorkflowConfig,
    ToolRecord,
)
from crypteo.research_model import ModelConfiguration, RefusalReply


@pytest.mark.parametrize(
    "field",
    [
        "max_tool_attempts",
        "max_tools_per_response",
        "max_investigation_calls",
        "thesis_calls",
        "critic_calls",
        "assessment_calls",
        "max_model_calls",
        "max_invalid_tool_requests",
        "provider_retries",
        "graph_retries",
        "recursion_limit",
    ],
)
@pytest.mark.parametrize("mutation", ["different", "bool", "float", "string"])
def test_budget_policy_values_cannot_be_changed_or_coerced(
    field: str, mutation: str
) -> None:
    policy = ResearchBudgetPolicy()
    value = policy.model_dump()[field]
    replacement = {
        "different": value + 1,
        "bool": bool(value),
        "float": float(value),
        "string": str(value),
    }[mutation]
    with pytest.raises(ValidationError):
        ResearchBudgetPolicy.model_validate(
            policy.model_copy(update={field: replacement})
        )


@pytest.mark.parametrize(
    "field",
    [
        "investigation_output_cap",
        "synthesis_output_cap",
        "model_call_deadline_seconds",
        "workflow_deadline_seconds",
        "max_model_input_bytes",
    ],
)
@pytest.mark.parametrize("mutation", ["different", "float", "string"])
def test_output_and_deadline_configuration_is_locked(field: str, mutation: str) -> None:
    config = ResearchWorkflowConfig(
        model=ModelConfiguration(provider="fake", model="explicit")
    )
    value = config.model_dump()[field]
    replacement = {"different": value - 1, "float": float(value), "string": str(value)}[
        mutation
    ]
    with pytest.raises(ValidationError):
        ResearchWorkflowConfig.model_validate(
            config.model_copy(update={field: replacement})
        )


def test_request_detaches_nested_mutable_input() -> None:
    payload: dict[str, Any] = {"items": [{"value": "1.000001"}]}
    op = opportunity().model_copy(update={"trigger_payload": payload})
    request = OpportunityResearchRequest(research_run_id="explicit-run", opportunity=op)
    before = request.model_dump_json()
    payload["items"][0]["value"] = "changed"
    assert request.model_dump_json() == before
    assert OpportunityResearchRequest.model_validate_json(before) == request
    with pytest.raises(ValidationError):
        OpportunityResearchRequest.model_validate({"opportunity": opportunity()})


@pytest.mark.parametrize("completed", [True, False])
def test_completed_and_interrupted_results_enforce_required_payloads(
    completed: bool,
) -> None:
    result = run(ScriptedModel([tool()] if completed else [RefusalReply(refusal="No")]))
    changes = {"critique": None} if completed else {"failure": None}
    with pytest.raises(ValidationError):
        ResearchRunResult.model_validate(result.model_copy(update=changes))
    if completed:
        bad = assessment("ABSTAIN", ()).model_copy(update={"critic_response": None})
    else:
        bad = assessment("ABSTAIN", ()).model_copy(
            update={"abstention_reasons": ("BUDGET_EXHAUSTED",)}
        )
    with pytest.raises(ValidationError):
        ResearchRunResult.model_validate(result.model_copy(update={"assessment": bad}))


@pytest.mark.parametrize(
    "code,reason",
    [
        ("PROVIDER_ERROR", "VALIDATION_FAILURE"),
        ("INVALID_TOOL_REQUEST", "RESEARCH_FAILURE"),
        ("TOOL_BUDGET_EXHAUSTED", "RESEARCH_FAILURE"),
    ],
)
def test_failure_cannot_change_semantic_category(code: str, reason: str) -> None:
    with pytest.raises(ValidationError):
        ResearchFailure.model_validate(
            dict(code=code, abstention_reason=reason, message="failure")
        )


@pytest.fixture(scope="module")
def cached_result() -> ResearchRunResult:
    return run(ScriptedModel([tool(), tool()]))


def reconstruct(
    result: ResearchRunResult, data: dict[str, Any], path: str
) -> ResearchRunResult:
    def python_input(value: Any) -> Any:
        if isinstance(value, list):
            return [python_input(item) for item in value]
        if isinstance(value, dict):
            return {
                key: datetime.fromisoformat(item)
                if key in {"observed_at", "available_at", "research_at", "start", "end"}
                and isinstance(item, str)
                else item
                if key in {"query_params", "payload", "trigger_payload"}
                else python_input(item)
                for key, item in value.items()
            }
        return value

    match path:
        case "construction":
            return ResearchRunResult(**python_input(data))
        case "instance":
            return ResearchRunResult.model_validate(
                result.model_copy(update=python_input(data))
            )
        case "dump":
            return ResearchRunResult.model_validate(python_input(data))
        case "json":
            return ResearchRunResult.model_validate_json(json.dumps(data))
        case _:
            raise AssertionError("unknown test reconstruction path")


@pytest.mark.parametrize("path", ["construction", "instance", "dump", "json"])
def test_identical_cached_evidence_remains_valid_on_every_input_path(
    cached_result: ResearchRunResult, path: str
) -> None:
    restored = reconstruct(cached_result, cached_result.model_dump(mode="json"), path)
    attempts = [r for r in restored.trace if isinstance(r, ToolRecord)]
    assert len(attempts) == 2
    assert attempts[1].cached_from == attempts[0].attempt_id
    assert attempts[0].result == attempts[1].result
    assert restored == cached_result


@pytest.mark.parametrize("path", ["construction", "instance", "dump", "json"])
@pytest.mark.parametrize("field", ["summary", "payload", "provenance"])
def test_conflicting_same_id_evidence_rejected_on_every_input_path(
    cached_result: ResearchRunResult, path: str, field: str
) -> None:
    data = cached_result.model_dump(mode="json")
    attempts = [r for r in data["trace"] if r["kind"] == "tool"]
    first, second = attempts[0]["result"]["evidence"], attempts[1]["result"]["evidence"]
    assert first["evidence_id"] == second["evidence_id"]
    if field == "summary":
        second["summary"] = "Different evidence with the original ID."
    elif field == "provenance":
        second["provenance"]["artifact_ref"] = "foreign-artifact"
    else:
        second["payload"] = {"unexpected": "changed"}
    with pytest.raises(ValidationError):
        reconstruct(cached_result, data, path)


@pytest.mark.parametrize("path", ["construction", "instance", "dump", "json"])
@pytest.mark.parametrize(
    "mutation",
    [
        "run_id",
        "subject",
        "research_at",
        "source_binding",
        "model_id",
        "tool_id",
        "tool_model_id",
        "cache_id",
        "foreign_tool_trace",
        "request_context",
        "request_evidence",
        "request_history",
    ],
)
def test_run_relational_corruption_rejected_on_every_input_path(
    cached_result: ResearchRunResult, path: str, mutation: str
) -> None:
    data = cached_result.model_dump(mode="json")
    context = data["context"]
    calls = [r for r in data["trace"] if r["kind"] == "model"]
    attempts = [r for r in data["trace"] if r["kind"] == "tool"]
    match mutation:
        case "run_id":
            context["request"]["research_run_id"] = "replacement-run"
        case "subject":
            context["request"]["opportunity"]["pair"] = "ETH-USD"
            context["benchmarks"] = ["BTC-USD", "SOL-USD"]
        case "research_at":
            later = (T + timedelta(hours=1)).isoformat()
            context["research_at"] = later
            context["request"]["opportunity"].update(
                observed_at=later, available_at=later
            )
        case "source_binding":
            context["source_bindings"][0]["dataset_version"] = "sha256:" + "d" * 64
        case "model_id":
            calls[0]["request"]["call_id"] = "foreign/model/0001"
        case "tool_id":
            attempts[0]["attempt_id"] = "foreign/tool/0001"
        case "tool_model_id":
            attempts[0]["model_call_id"] = "foreign/model/0001"
        case "cache_id":
            attempts[1]["cached_from"] = "foreign/tool/0001"
        case "foreign_tool_trace":
            other = run(
                ScriptedModel([tool()]),
                request=cached_result.context.request.model_copy(
                    update={"research_run_id": "foreign"}
                ),
            )
            foreign = next(r for r in other.trace if isinstance(r, ToolRecord))
            attempts[0].update(foreign.model_dump(mode="json"))
        case "request_context" | "request_evidence" | "request_history":
            content = json.loads(calls[1]["request"]["messages"][-1]["content"])
            supplied = content["research_data"]
            if mutation == "request_context":
                supplied["context"]["request"]["research_run_id"] = "foreign"
            elif mutation == "request_evidence":
                supplied["evidence"][0]["summary"] = "Conflicting model copy."
            else:
                supplied["request_history"][0]["attempt_id"] = "foreign/tool/0001"
            calls[1]["request"]["messages"][-1]["content"] = json.dumps(content)
    # For these mutations update the repeated context too: the tool/evidence
    # relationship must fail independently of model-message context equality.
    if mutation in {"subject", "research_at", "source_binding"}:
        for call in calls:
            content = json.loads(call["request"]["messages"][-1]["content"])
            content["research_data"]["context"] = context
            call["request"]["messages"][-1]["content"] = json.dumps(content)
    with pytest.raises(ValidationError):
        reconstruct(cached_result, data, path)


@pytest.mark.parametrize("path", ["construction", "instance", "dump", "json"])
def test_interrupted_failure_call_id_must_belong_to_run(path: str) -> None:
    result = run(ScriptedModel([RefusalReply(refusal="No")]))
    data = result.model_dump(mode="json")
    data["failure"]["call_id"] = "foreign/model/0001"
    with pytest.raises(ValidationError):
        reconstruct(result, data, path)
