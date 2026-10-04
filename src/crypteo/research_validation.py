"""Pure shared research relationship checks; no inference or market reads."""

import re
from typing import Any

from pydantic import BaseModel, ValidationError

from crypteo.domain.market_research import MarketResearchCapability, ResearchToolSuccess
from crypteo.domain.research import EvidenceItem, ResearchThesis, TradeDecision
from crypteo.domain.research_workflow import (
    BENCHMARK_ORDER,
    InvestigationCompletion,
    ModelCallRecord,
    ModelRequestRejection,
    OpportunityResearchContext,
    ResearchFailure,
    ResearchFailureCode,
    ResearchWorkflowConfig,
    ToolRecord,
    TraceRecord,
)
from crypteo.research_model import (
    ModelOutputError,
    StructuredReply,
    parse_json,
    render_json,
)


class ResearchValidationError(ValueError):
    """A represented research relationship violates its retained contract."""


def validate_trace_relationships(
    context: OpportunityResearchContext,
    trace: tuple[TraceRecord, ...],
    failure: ResearchFailure | None = None,
) -> None:
    run_id = context.request.research_run_id
    subject = context.request.opportunity.pair
    now = context.research_at
    sources = {s.pair: s.model_dump() for s in context.source_bindings}
    evidence: dict[str, EvidenceItem] = {}
    model_ids: set[str] = set()
    tools: dict[str, ToolRecord] = {}

    def local_id(identifier: str, kind: str) -> None:
        if re.fullmatch(re.escape(run_id) + f"/{kind}/[0-9]{{4}}", identifier) is None:
            raise ResearchValidationError(
                "local trace identifier belongs to another research run"
            )

    def failure_id(failure: ResearchFailure | None) -> None:
        if failure is not None and failure.call_id is not None:
            local_id(failure.call_id, "model")

    failure_id(failure)
    for record in trace:
        if isinstance(record, (ModelCallRecord, ModelRequestRejection)):
            request = record.request
            local_id(request.call_id, "model")
            if request.call_id in model_ids:
                raise ResearchValidationError("duplicate local model call identifier")
            model_ids.add(request.call_id)
            if isinstance(record, ModelRequestRejection):
                failure_id(record.failure)
            # The rendered context is a represented relationship, not an
            # independent timestamp/source authority supplied by the model.
            content = parse_json(request.messages[-1].content)
            if not isinstance(content, dict) or not isinstance(
                content.get("research_data"), dict
            ):
                raise ResearchValidationError(
                    "retained model request lacks research context"
                )
            if content["research_data"].get("context") != context.model_dump(
                mode="json"
            ):
                raise ResearchValidationError(
                    "retained model context differs from enclosing research context"
                )
            data = content["research_data"]
            retained = data.get("evidence")
            if not isinstance(retained, list):
                raise ResearchValidationError(
                    "retained model request lacks evidence collection"
                )
            retained_ids: set[str] = set()
            for value in retained:
                item = EvidenceItem.model_validate_json(render_json(value))
                if evidence.get(item.evidence_id) != item:
                    raise ResearchValidationError(
                        "retained model evidence differs from tool evidence"
                    )
                if item.evidence_id in retained_ids:
                    raise ResearchValidationError(
                        "model evidence collection must be deduplicated"
                    )
                retained_ids.add(item.evidence_id)
            if retained_ids != set(evidence):
                raise ResearchValidationError(
                    "retained model request omits collected evidence"
                )
            if data.get("request_history") != [
                tool.model_dump(mode="json") for tool in tools.values()
            ]:
                raise ResearchValidationError(
                    "retained request history differs from tool trace"
                )
            continue
        if not isinstance(record, ToolRecord):
            continue
        local_id(record.attempt_id, "tool")
        local_id(record.model_call_id, "model")
        if record.attempt_id in tools or record.model_call_id not in model_ids:
            raise ResearchValidationError(
                "tool trace has duplicate attempt or absent model call"
            )
        if record.query is not None and record.query.pair != subject:
            raise ResearchValidationError(
                "retained tool subject differs from opportunity"
            )
        result = record.result
        if result is not None:
            query = (
                result.context.query
                if isinstance(result, ResearchToolSuccess)
                else result.query
            )
            window = (
                result.context.window
                if isinstance(result, ResearchToolSuccess)
                else result.window
            )
            if query != record.query or query.pair != subject:
                raise ResearchValidationError(
                    "retained tool result differs from bound subject/query"
                )
            if window.end != now:
                raise ResearchValidationError(
                    "retained research time differs from research_at"
                )
        if isinstance(result, ResearchToolSuccess):
            # Revalidate the entire envelope before comparing repeated IDs,
            # including payload and nested provenance, on every input path.
            item = EvidenceItem.model_validate(result.evidence)
            if item.observed_at != now or item.available_at != now:
                raise ResearchValidationError(
                    "retained evidence time differs from research_at"
                )
            previous = evidence.get(item.evidence_id)
            if previous is not None and previous != item:
                raise ResearchValidationError(
                    "conflicting repeated evidence_id content"
                )
            evidence[item.evidence_id] = item
            for research_input in result.context.inputs:
                source = research_input.source
                if source.model_dump(exclude={"role"}) != sources.get(source.pair):
                    raise ResearchValidationError(
                        "retained provenance differs from bound source"
                    )
        if record.cached_from is not None:
            local_id(record.cached_from, "tool")
            cached = tools.get(record.cached_from)
            if (
                cached is None
                or cached.query != record.query
                or cached.result != record.result
            ):
                raise ResearchValidationError(
                    "cache linkage differs from earlier retained result"
                )
        tools[record.attempt_id] = record


class ResearchInvariantError(ValueError):
    """Runtime integrity violation: never an ordinary research abstention."""


def collected_evidence(trace: tuple[TraceRecord, ...]) -> tuple[EvidenceItem, ...]:
    """Deduplicate exact runtime envelopes, preserving a stable presentation order."""
    evidence: dict[str, tuple[tuple[int, int, str], EvidenceItem]] = {}
    capabilities = tuple(MarketResearchCapability)
    for record in trace:
        if not isinstance(record, ToolRecord) or not isinstance(
            record.result, ResearchToolSuccess
        ):
            continue
        item, query = record.result.evidence, record.result.context.query
        previous = evidence.get(item.evidence_id)
        if previous is not None and previous[1] != item:
            raise ResearchInvariantError("conflicting evidence identity")
        key = (
            capabilities.index(query.capability),
            BENCHMARK_ORDER.index(query.benchmark) if query.benchmark else -1,
            item.evidence_id,
        )
        evidence[item.evidence_id] = (key, item)
    return tuple(
        item for _, item in sorted(evidence.values(), key=lambda entry: entry[0])
    )


def validate_stage_output(
    trace: tuple[TraceRecord, ...],
    reply: StructuredReply,
    schema: type[BaseModel],
) -> BaseModel | ResearchFailureCode:
    try:
        parsed = parse_json(reply.content)
        # Wire schemas require every field. Local providers/fakes are held to the
        # same contract rather than silently filling domain defaults.
        if not isinstance(parsed, dict) or set(parsed) != set(schema.model_fields):
            raise ModelOutputError(
                "output must contain exactly the structured schema fields"
            )
        output = schema.model_validate(parsed)
        references: tuple[str, ...] = ()
        if isinstance(output, InvestigationCompletion):
            references = output.relevant_evidence_ids
        elif isinstance(output, (ResearchThesis, TradeDecision)):
            references = output.supporting_evidence_ids + output.counterevidence_ids
        if set(references) - {e.evidence_id for e in collected_evidence(trace)}:
            return ResearchFailureCode.UNKNOWN_EVIDENCE_REFERENCE
        if isinstance(output, TradeDecision) and output.critic_response is None:
            raise ModelOutputError(
                "completed assessment requires nonblank critic_response"
            )
        return output
    except (ModelOutputError, ValidationError):
        return ResearchFailureCode.INVALID_MODEL_OUTPUT


def research_data(
    context: OpportunityResearchContext,
    trace: tuple[TraceRecord, ...],
    configuration: ResearchWorkflowConfig,
    stage: str,
    stages: dict[str, BaseModel],
) -> dict[str, Any]:
    tools = tuple(r for r in trace if isinstance(r, ToolRecord))
    data: dict[str, Any] = {
        "context": context.model_dump(mode="json"),
        "evidence": [e.model_dump(mode="json") for e in collected_evidence(trace)],
        "request_history": [r.model_dump(mode="json") for r in tools],
    }
    if stage == "investigate":
        data["attempts_remaining"] = configuration.budget.max_tool_attempts - sum(
            r.admitted for r in tools
        )
    required = {
        "investigate": (),
        "draft_thesis": (("investigate", "investigation_completion"),),
        "critique": (("draft_thesis", "thesis"),),
        "final_assessment": (("draft_thesis", "thesis"), ("critique", "critique")),
    }[stage]
    for prerequisite, key in required:
        if prerequisite not in stages:
            raise ResearchInvariantError("stage lacks validated prerequisite")
        data[key] = stages[prerequisite].model_dump(mode="json")
    return data
