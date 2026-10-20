"""Pure shared research relationship checks; no inference or market reads."""

import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorDetails

from crypteo.domain.market_research import (
    MarketResearchCapability,
    ResearchToolSuccess,
    ResearchToolUnavailable,
)
from crypteo.domain.research import (
    Critique,
    EvidenceItem,
    ResearchThesis,
    TradeDecision,
)
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
    ModelRequest,
    StructuredReply,
    parse_json,
    render_json,
)
from crypteo.research_tools import tool_definitions


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
    last_work: TraceRecord | None = None

    def local_id(identifier: str, kind: str) -> None:
        if re.fullmatch(re.escape(run_id) + f"/{kind}/[0-9]{{4}}", identifier) is None:
            raise ResearchValidationError(
                "local trace identifier belongs to another research run"
            )

    def failure_id(failure: ResearchFailure | None) -> None:
        if failure is not None and failure.call_id is not None:
            local_id(failure.call_id, "model")

    failure_id(failure)
    for index, record in enumerate(trace):
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
            # Rendered comparison keeps JSON types exact (true is not 1).
            if render_json(data.get("evidence")) != render_json(
                [evidence_view(e) for e in collected_evidence(trace[:index])]
            ):
                raise ResearchValidationError(
                    "retained model evidence differs from collected evidence"
                )
            if render_json(data.get("request_history")) != render_json(
                request_history_view(tuple(tools.values()))
            ):
                raise ResearchValidationError(
                    "retained request history differs from tool trace"
                )
            # Requests offering no tools carry the run's definitions as data.
            definitions = None if request.tools else tool_definitions_view(context)
            if ("tool_definitions" in data) != (definitions is not None) or render_json(
                data.get("tool_definitions")
            ) != render_json(definitions):
                raise ResearchValidationError(
                    "retained tool definitions differ from offered tools"
                )
            # Only the call after an invalid structured first reply re-asks it.
            rejected = None
            if isinstance(last_work, ModelCallRecord):
                rejected = output_rejection(trace[:index], last_work)
                if rejected is not None and (
                    request_rejection(last_work.request) is not None
                    or last_work.request.stage != request.stage
                ):
                    raise ResearchValidationError(
                        "model call after an unrecoverable invalid reply"
                    )
            if ("output_rejection" in data) != (rejected is not None) or data.get(
                "output_rejection"
            ) != rejected:
                raise ResearchValidationError(
                    "output rejection differs from preceding structured reply"
                )
            last_work = record
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
        last_work = record


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


_EVIDENCE_VIEW = (
    "evidence_id",
    "category",
    "evidence_type",
    "source",
    "observed_at",
    "available_at",
    "payload",
)


def evidence_view(item: EvidenceItem) -> dict[str, Any]:
    """The model's compact evidence; the trace retains the full envelope."""
    encoded = item.model_dump(mode="json")
    return {key: encoded[key] for key in _EVIDENCE_VIEW}


def request_history_view(tools: tuple[ToolRecord, ...]) -> list[dict[str, Any]]:
    """The model's compact tool history; the trace retains full records."""
    positions: dict[str, int] = {}
    view: list[dict[str, Any]] = []
    for attempt, record in enumerate(tools, 1):
        entry: dict[str, Any] = {
            "attempt": attempt,
            "requests": [
                {"name": r.name, "arguments_json": r.arguments_json}
                for r in record.requests
            ],
            "admitted": record.admitted,
        }
        result = record.result
        if record.rejection is not None:
            entry |= {
                "status": "rejected",
                "code": record.rejection.code,
                "message": record.rejection.message,
            }
        elif isinstance(result, ResearchToolSuccess):
            entry |= {"status": "success", "evidence_id": result.evidence.evidence_id}
        elif isinstance(result, ResearchToolUnavailable):
            entry |= {
                "status": "unavailable",
                "role": result.role.value,
                "reason": result.reason.value,
            }
        if record.cached_from is not None:
            if record.cached_from not in positions:
                raise ResearchInvariantError("cache link names no earlier attempt")
            entry["cached_from_attempt"] = positions[record.cached_from]
        positions.setdefault(record.attempt_id, attempt)
        view.append(entry)
    return view


_OUTPUT_SCHEMAS: dict[str, type[BaseModel]] = {
    "investigate": InvestigationCompletion,
    "draft_thesis": ResearchThesis,
    "critique": Critique,
    "final_assessment": TradeDecision,
}


def _problem(error: ErrorDetails) -> str:
    location = ".".join(str(part) for part in error["loc"]) or "output"
    cause = error.get("ctx", {}).get("error")
    # Project validator messages and pydantic error types, never provider text.
    return f"{location}: {cause if cause is not None else error['type']}"


def _check_stage_output(
    trace: tuple[TraceRecord, ...],
    reply: StructuredReply,
    schema: type[BaseModel],
) -> BaseModel | tuple[ResearchFailureCode, tuple[str, ...]]:
    invalid = ResearchFailureCode.INVALID_MODEL_OUTPUT
    try:
        parsed = parse_json(reply.content)
    except ModelOutputError as error:
        return invalid, (f"output: {error}",)
    if not isinstance(parsed, dict):
        return invalid, ("output: must be a JSON object",)
    # Wire schemas require every field. Local providers/fakes are held to the
    # same contract rather than silently filling domain defaults.
    fields = tuple(schema.model_fields)
    problems = tuple(f"{name}: missing" for name in fields if name not in parsed)
    problems += tuple(
        f"{name}: unexpected field" for name in sorted(set(parsed) - set(fields))
    )
    if problems:
        return invalid, problems
    try:
        output = schema.model_validate(parsed)
    except ValidationError as error:
        return invalid, tuple(_problem(e) for e in error.errors(include_url=False))
    reference_fields: tuple[str, ...] = ()
    if isinstance(output, InvestigationCompletion):
        reference_fields = ("relevant_evidence_ids",)
    elif isinstance(output, (ResearchThesis, TradeDecision)):
        reference_fields = ("supporting_evidence_ids", "counterevidence_ids")
    known = {e.evidence_id for e in collected_evidence(trace)}
    unknown = tuple(
        f"{name}: unknown evidence id {reference}"
        for name in reference_fields
        for reference in getattr(output, name)
        if reference not in known
    )
    problems = unknown
    if isinstance(output, TradeDecision) and output.critic_response is None:
        problems += ("critic_response: required for every outcome",)
    if unknown:
        return ResearchFailureCode.UNKNOWN_EVIDENCE_REFERENCE, problems
    return (invalid, problems) if problems else output


def validate_stage_output(
    trace: tuple[TraceRecord, ...],
    reply: StructuredReply,
    schema: type[BaseModel],
) -> BaseModel | ResearchFailureCode:
    checked = _check_stage_output(trace, reply, schema)
    return checked[0] if isinstance(checked, tuple) else checked


def output_rejection(
    trace: tuple[TraceRecord, ...], record: ModelCallRecord
) -> dict[str, Any] | None:
    """Code-derived re-ask feedback for a structured reply failing validation."""
    if not isinstance(record.reply, StructuredReply):
        return None
    checked = _check_stage_output(
        trace, record.reply, _OUTPUT_SCHEMAS[record.request.stage]
    )
    if not isinstance(checked, tuple):
        return None
    return {"call_id": record.request.call_id, "problems": list(checked[1])}


def request_rejection(request: ModelRequest) -> Any:
    """The output_rejection a retained request carries, or None for a first ask."""
    content = parse_json(request.messages[-1].content)
    data = content.get("research_data") if isinstance(content, dict) else None
    return data.get("output_rejection") if isinstance(data, dict) else None


def offers_tools(stage: str, data: Mapping[str, Any]) -> bool:
    """Code owns the tool budget: offer no tool the model may not use."""
    return stage == "investigate" and data["attempts_remaining"] > 0


def tool_definitions_view(context: OpportunityResearchContext) -> list[dict[str, Any]]:
    """The run's tool names and descriptions, in offer order, as read-only data."""
    return [
        {"name": t.name, "description": t.description}
        for t in tool_definitions(context.request.opportunity.pair, context.benchmarks)
    ]


def research_data(
    context: OpportunityResearchContext,
    trace: tuple[TraceRecord, ...],
    configuration: ResearchWorkflowConfig,
    stage: str,
    stages: dict[str, BaseModel],
    rejected: ModelCallRecord | None = None,
) -> dict[str, Any]:
    """Stage request data; ``rejected`` makes it a re-ask of that invalid reply."""
    tools = tuple(r for r in trace if isinstance(r, ToolRecord))
    data: dict[str, Any] = {
        "context": context.model_dump(mode="json"),
        "evidence": [evidence_view(e) for e in collected_evidence(trace)],
        "request_history": request_history_view(tools),
    }
    if stage == "investigate":
        data["attempts_remaining"] = configuration.budget.max_tool_attempts - sum(
            r.admitted for r in tools
        )
    if not offers_tools(stage, data):
        data["tool_definitions"] = tool_definitions_view(context)
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
    if rejected is not None:
        rejection = output_rejection(trace, rejected)
        if rejection is None or rejected.request.stage != stage:
            raise ResearchInvariantError("re-ask lacks a rejected structured reply")
        data["output_rejection"] = rejection
    return data
