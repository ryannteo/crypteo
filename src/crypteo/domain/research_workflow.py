"""Immutable opportunity-led workflow inputs, retained policy, and audit records."""

import re
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from crypteo.domain.market import TradingPair
from crypteo.domain.market_research import (
    HourlyDatetime,
    MarketResearchQuery,
    MarketSourceBinding,
    ResearchToolSuccess,
    ResearchToolUnavailable,
)
from crypteo.domain.research import (
    AbstentionReason,
    Critique,
    DecisionOutcome,
    EvidenceItem,
    NonBlankText,
    Opportunity,
    ResearchThesis,
    TextEntries,
    TradeDecision,
    UniqueEvidenceReferences,
)
from crypteo.experiment import CodeIdentity
from crypteo.research_model import (
    ModelConfiguration,
    ModelContract,
    ModelReply,
    ModelRequest,
    ProviderIdentity,
    ToolRequest,
    parse_json,
    render_json,
)

BENCHMARK_ORDER = (
    TradingPair.BTC_USD,
    TradingPair.ETH_USD,
    TradingPair.SOL_USD,
)


class OpportunityResearchRequest(ModelContract):
    research_run_id: NonBlankText
    opportunity: Opportunity


class ResearchBudgetPolicy(ModelContract):
    policy_id: Literal["bounded_research_v1"] = "bounded_research_v1"
    max_tool_attempts: Literal[8] = 8
    max_tools_per_response: Literal[1] = 1
    max_investigation_calls: Literal[9] = 9
    thesis_calls: Literal[1] = 1
    critic_calls: Literal[1] = 1
    assessment_calls: Literal[1] = 1
    max_model_calls: Literal[12] = 12
    max_invalid_tool_requests: Literal[2] = 2
    provider_retries: Literal[0] = 0
    graph_retries: Literal[0] = 0
    recursion_limit: Literal[24] = 24

    @field_validator("*", mode="before")
    @classmethod
    def strict_literals(cls, value: object, info: ValidationInfo) -> object:
        if type(value) is not type(cls.model_fields[info.field_name or ""].default):
            raise ValueError("budget policy values must have their exact literal types")
        return value


class ResearchWorkflowConfig(ModelContract):
    model: ModelConfiguration
    budget: ResearchBudgetPolicy = ResearchBudgetPolicy()
    investigation_output_cap: Literal[1024] = 1024
    synthesis_output_cap: Literal[2048] = 2048
    model_call_deadline_seconds: Literal[60] = 60
    workflow_deadline_seconds: Literal[600] = 600
    max_model_input_bytes: Literal[131072] = 131072

    @field_validator(
        "investigation_output_cap",
        "synthesis_output_cap",
        "model_call_deadline_seconds",
        "workflow_deadline_seconds",
        "max_model_input_bytes",
        mode="before",
    )
    @classmethod
    def strict_limits(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("workflow limits must be exact integers")
        return value


class OpportunityResearchContext(ModelContract):
    request: OpportunityResearchRequest
    research_at: HourlyDatetime
    benchmarks: tuple[TradingPair, ...]
    source_bindings: tuple[MarketSourceBinding, ...]

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        opportunity, now = self.request.opportunity, self.research_at
        if opportunity.observed_at != now or opportunity.available_at > now:
            raise ValueError("opportunity must be current and available at research T")
        if self.benchmarks != tuple(
            p for p in BENCHMARK_ORDER if p != opportunity.pair
        ):
            raise ValueError(
                "benchmarks must follow the deterministic two-asset policy"
            )
        pairs = tuple(s.pair for s in self.source_bindings)
        if pairs != tuple(p for p in BENCHMARK_ORDER if p in pairs):
            raise ValueError("source bindings must be unique and canonically ordered")
        return self


class InvestigationCompletion(ModelContract):
    summary: NonBlankText
    relevant_evidence_ids: UniqueEvidenceReferences
    remaining_gaps: TextEntries


class ResearchFailureCode(StrEnum):
    PROVIDER_NETWORK = "PROVIDER_NETWORK"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    WORKFLOW_TIMEOUT = "WORKFLOW_TIMEOUT"
    REFUSAL = "REFUSAL"
    INCOMPLETE_OUTPUT = "INCOMPLETE_OUTPUT"
    INVALID_PROVIDER_ENVELOPE = "INVALID_PROVIDER_ENVELOPE"
    INVALID_MODEL_OUTPUT = "INVALID_MODEL_OUTPUT"
    UNKNOWN_EVIDENCE_REFERENCE = "UNKNOWN_EVIDENCE_REFERENCE"
    INVALID_TOOL_REQUEST = "INVALID_TOOL_REQUEST"
    TOOL_BUDGET_EXHAUSTED = "TOOL_BUDGET_EXHAUSTED"
    MODEL_BUDGET_EXHAUSTED = "MODEL_BUDGET_EXHAUSTED"
    INPUT_BUDGET_EXHAUSTED = "INPUT_BUDGET_EXHAUSTED"


class ResearchFailure(ModelContract):
    code: ResearchFailureCode
    abstention_reason: AbstentionReason
    message: NonBlankText
    call_id: NonBlankText | None = None

    @model_validator(mode="after")
    def validate_reason(self) -> Self:
        validation_codes = {
            ResearchFailureCode.INVALID_PROVIDER_ENVELOPE,
            ResearchFailureCode.INVALID_MODEL_OUTPUT,
            ResearchFailureCode.UNKNOWN_EVIDENCE_REFERENCE,
            ResearchFailureCode.INVALID_TOOL_REQUEST,
        }
        budget_codes = {
            ResearchFailureCode.TOOL_BUDGET_EXHAUSTED,
            ResearchFailureCode.MODEL_BUDGET_EXHAUSTED,
            ResearchFailureCode.INPUT_BUDGET_EXHAUSTED,
        }
        expected = (
            AbstentionReason.VALIDATION_FAILURE
            if self.code in validation_codes
            else AbstentionReason.BUDGET_EXHAUSTED
            if self.code in budget_codes
            else AbstentionReason.RESEARCH_FAILURE
        )
        if self.abstention_reason is not expected:
            raise ValueError("failure code must retain its precise abstention category")
        return self


class ToolRejection(ModelContract):
    code: Literal["INVALID_TOOL_REQUEST", "TOOL_BUDGET_EXHAUSTED"]
    message: NonBlankText


class ModelCallRecord(ModelContract):
    kind: Literal["model"] = "model"
    request: ModelRequest
    reply: ModelReply


class ModelRequestRejection(ModelContract):
    kind: Literal["model_rejection"] = "model_rejection"
    request: ModelRequest
    failure: ResearchFailure


ToolResult = Annotated[
    ResearchToolSuccess | ResearchToolUnavailable, Field(discriminator="status")
]


class ToolRecord(ModelContract):
    kind: Literal["tool"] = "tool"
    attempt_id: NonBlankText
    model_call_id: NonBlankText
    requests: tuple[ToolRequest, ...]
    admitted: bool = Field(strict=True)
    query: MarketResearchQuery | None = None
    result: ToolResult | None = None
    rejection: ToolRejection | None = None
    cached_from: NonBlankText | None = None

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        if self.rejection is None:
            if (
                len(self.requests) != 1
                or not self.admitted
                or self.query is None
                or self.result is None
            ):
                raise ValueError(
                    "successful dispatch requires one admitted bound query"
                )
        elif self.result is not None or self.cached_from is not None:
            raise ValueError("rejected requests cannot contain results/cache links")
        return self


class NodeTransition(ModelContract):
    kind: Literal["transition"] = "transition"
    source: NonBlankText
    target: NonBlankText
    termination_reason: NonBlankText | None = None


TraceRecord = Annotated[
    ModelCallRecord | ModelRequestRejection | ToolRecord | NodeTransition,
    Field(discriminator="kind"),
]


class ResearchRunStatus(StrEnum):
    COMPLETED = "COMPLETED"
    INTERRUPTED = "INTERRUPTED"


class AssessmentOrigin(StrEnum):
    MODEL = "MODEL"
    RUNTIME = "RUNTIME"


class ResearchRunResult(ModelContract):
    status: ResearchRunStatus
    assessment_origin: AssessmentOrigin
    context: OpportunityResearchContext
    configuration: ResearchWorkflowConfig
    code: CodeIdentity
    provider: ProviderIdentity
    langgraph_version: NonBlankText
    trace: tuple[TraceRecord, ...]
    investigation: InvestigationCompletion | None = None
    thesis: ResearchThesis | None = None
    critique: Critique | None = None
    assessment: TradeDecision
    failure: ResearchFailure | None = None

    @model_validator(mode="after")
    def validate_trace_relationships(self) -> Self:
        run_id = self.context.request.research_run_id
        subject = self.context.request.opportunity.pair
        now = self.context.research_at
        sources = {s.pair: s.model_dump() for s in self.context.source_bindings}
        evidence: dict[str, EvidenceItem] = {}
        model_ids: set[str] = set()
        tools: dict[str, ToolRecord] = {}

        def local_id(identifier: str, kind: str) -> None:
            if (
                re.fullmatch(re.escape(run_id) + f"/{kind}/[0-9]{{4}}", identifier)
                is None
            ):
                raise ValueError(
                    "local trace identifier belongs to another research run"
                )

        def failure_id(failure: ResearchFailure | None) -> None:
            if failure is not None and failure.call_id is not None:
                local_id(failure.call_id, "model")

        failure_id(self.failure)
        for record in self.trace:
            if isinstance(record, (ModelCallRecord, ModelRequestRejection)):
                request = record.request
                local_id(request.call_id, "model")
                if request.call_id in model_ids:
                    raise ValueError("duplicate local model call identifier")
                model_ids.add(request.call_id)
                if isinstance(record, ModelRequestRejection):
                    failure_id(record.failure)
                # The rendered context is a represented relationship, not an
                # independent timestamp/source authority supplied by the model.
                content = parse_json(request.messages[-1].content)
                if not isinstance(content, dict) or not isinstance(
                    content.get("research_data"), dict
                ):
                    raise ValueError("retained model request lacks research context")
                if content["research_data"].get("context") != self.context.model_dump(
                    mode="json"
                ):
                    raise ValueError(
                        "retained model context differs from enclosing research context"
                    )
                data = content["research_data"]
                retained = data.get("evidence")
                if not isinstance(retained, list):
                    raise ValueError("retained model request lacks evidence collection")
                retained_ids: set[str] = set()
                for value in retained:
                    item = EvidenceItem.model_validate_json(render_json(value))
                    if evidence.get(item.evidence_id) != item:
                        raise ValueError(
                            "retained model evidence differs from tool evidence"
                        )
                    if item.evidence_id in retained_ids:
                        raise ValueError(
                            "model evidence collection must be deduplicated"
                        )
                    retained_ids.add(item.evidence_id)
                if retained_ids != set(evidence):
                    raise ValueError("retained model request omits collected evidence")
                if data.get("request_history") != [
                    tool.model_dump(mode="json") for tool in tools.values()
                ]:
                    raise ValueError("retained request history differs from tool trace")
                continue
            if not isinstance(record, ToolRecord):
                continue
            local_id(record.attempt_id, "tool")
            local_id(record.model_call_id, "model")
            if record.attempt_id in tools or record.model_call_id not in model_ids:
                raise ValueError(
                    "tool trace has duplicate attempt or absent model call"
                )
            if record.query is not None and record.query.pair != subject:
                raise ValueError("retained tool subject differs from opportunity")
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
                    raise ValueError(
                        "retained tool result differs from bound subject/query"
                    )
                if window.end != now:
                    raise ValueError("retained research time differs from research_at")
            if isinstance(result, ResearchToolSuccess):
                # Revalidate the entire envelope before comparing repeated IDs,
                # including payload and nested provenance, on every input path.
                item = EvidenceItem.model_validate(result.evidence)
                if item.observed_at != now or item.available_at != now:
                    raise ValueError("retained evidence time differs from research_at")
                previous = evidence.get(item.evidence_id)
                if previous is not None and previous != item:
                    raise ValueError("conflicting repeated evidence_id content")
                evidence[item.evidence_id] = item
                for research_input in result.context.inputs:
                    source = research_input.source
                    if source.model_dump(exclude={"role"}) != sources.get(source.pair):
                        raise ValueError(
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
                    raise ValueError(
                        "cache linkage differs from earlier retained result"
                    )
            tools[record.attempt_id] = record
        return self

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        completed = self.status is ResearchRunStatus.COMPLETED
        if completed != (self.assessment_origin is AssessmentOrigin.MODEL):
            raise ValueError("COMPLETED iff MODEL; INTERRUPTED iff RUNTIME")
        if completed:
            if self.failure is not None or any(
                x is None for x in (self.investigation, self.thesis, self.critique)
            ):
                raise ValueError(
                    "completed research requires investigation/thesis/critique"
                )
            if self.assessment.critic_response is None:
                raise ValueError(
                    "every completed assessment requires a critic response"
                )
        elif (
            self.failure is None
            or self.assessment.outcome is not DecisionOutcome.ABSTAIN
            or self.assessment.abstention_reasons != (self.failure.abstention_reason,)
        ):
            raise ValueError(
                "interruption requires typed failure and matching runtime ABSTAIN"
            )
        evidence_ids = {
            record.result.evidence.evidence_id
            for record in self.trace
            if isinstance(record, ToolRecord)
            and isinstance(record.result, ResearchToolSuccess)
        }
        references: tuple[str, ...] = ()
        if self.investigation is not None:
            references += self.investigation.relevant_evidence_ids
        for output in (self.thesis, self.assessment):
            if output is not None:
                references += (
                    output.supporting_evidence_ids + output.counterevidence_ids
                )
        if set(references) - evidence_ids:
            raise ValueError("research output references evidence absent from this run")
        return self
