"""Five-node bounded opportunity research, isolated from trading and replay."""

import asyncio
import json
from importlib.metadata import version
from time import monotonic
from typing import Any, Literal, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, TypeAdapter, ValidationError

from crypteo.domain.market_research import (
    MarketResearchCapability,
    ResearchToolSuccess,
    ResearchToolUnavailable,
)
from crypteo.domain.research import (
    AbstentionReason,
    Critique,
    DecisionOutcome,
    EvidenceItem,
    ResearchThesis,
    TradeDecision,
)
from crypteo.domain.research_workflow import (
    BENCHMARK_ORDER,
    AssessmentOrigin,
    InvestigationCompletion,
    ModelCallRecord,
    ModelRequestRejection,
    NodeTransition,
    OpportunityResearchContext,
    OpportunityResearchRequest,
    ResearchFailure,
    ResearchFailureCode,
    ResearchRunResult,
    ResearchRunStatus,
    ResearchWorkflowConfig,
    ToolRecord,
    ToolRejection,
    TraceRecord,
)
from crypteo.experiment import CodeIdentity, current_code_identity
from crypteo.market_research import MarketResearchService
from crypteo.research_model import (
    FailureReply,
    ModelOutputError,
    ModelReply,
    ModelRequest,
    ProviderFailureCode,
    ProviderIdentity,
    ProviderMetadata,
    RefusalReply,
    ResearchModel,
    StructuredReply,
    ToolReply,
    parse_json,
)
from crypteo.research_prompts import prompt_identity, prompt_messages, structured_schema
from crypteo.research_tools import bind_tool_request, dispatch_tool, tool_definitions


class ResearchInvariantError(ValueError):
    """Runtime integrity violation: never an ordinary research abstention."""


_OVERDUE_MODEL_TASKS: set[asyncio.Task[ModelReply]] = set()


def _cancel_and_observe(task: asyncio.Task[ModelReply]) -> None:
    """Do not await cancellation cooperation; retain and observe eventual completion."""
    _OVERDUE_MODEL_TASKS.add(task)

    def finished(completed: asyncio.Task[ModelReply]) -> None:
        _OVERDUE_MODEL_TASKS.discard(completed)
        if not completed.cancelled():
            completed.exception()

    task.add_done_callback(finished)
    task.cancel()


class ResearchState(TypedDict):
    context: OpportunityResearchContext
    trace: tuple[TraceRecord, ...]
    action: ToolReply | None
    investigation: InvestigationCompletion | None
    thesis: ResearchThesis | None
    critique: Critique | None
    assessment: TradeDecision | None
    failure: ResearchFailure | None


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


def _tools(state: ResearchState) -> tuple[ToolRecord, ...]:
    return tuple(r for r in state["trace"] if isinstance(r, ToolRecord))


def _models(state: ResearchState) -> tuple[ModelCallRecord, ...]:
    return tuple(r for r in state["trace"] if isinstance(r, ModelCallRecord))


def _failure(
    state: ResearchState,
    code: ResearchFailureCode,
    reason: AbstentionReason,
    message: str,
    call_id: str | None = None,
) -> ResearchState:
    failure = ResearchFailure(
        code=code, abstention_reason=reason, message=message, call_id=call_id
    )
    return {
        **state,
        "failure": failure,
        "action": None,
        "assessment": TradeDecision(
            outcome=DecisionOutcome.ABSTAIN,
            rationale=message,
            abstention_reasons=(reason,),
        ),
    }


class _ResearchRuntime:
    def __init__(
        self,
        service: MarketResearchService,
        model: ResearchModel,
        config: ResearchWorkflowConfig,
    ) -> None:
        self.service, self.model, self.config = service, model, config
        self.deadline = monotonic() + config.workflow_deadline_seconds

    def guard_clock(self, state: ResearchState) -> None:
        if self.service.now != state["context"].research_at:
            raise ResearchInvariantError(
                "authoritative research clock moved from captured T"
            )

    def guard_deadline(self, state: ResearchState) -> ResearchState:
        self.guard_clock(state)
        if monotonic() >= self.deadline and state["failure"] is None:
            return _failure(
                state,
                ResearchFailureCode.WORKFLOW_TIMEOUT,
                AbstentionReason.RESEARCH_FAILURE,
                "overall workflow deadline exceeded",
            )
        return state

    def data(self, state: ResearchState, stage: str) -> dict[str, Any]:
        data: dict[str, Any] = {
            "context": state["context"].model_dump(mode="json"),
            "evidence": [
                e.model_dump(mode="json") for e in collected_evidence(state["trace"])
            ],
            "request_history": [r.model_dump(mode="json") for r in _tools(state)],
        }
        if stage == "investigate":
            data["attempts_remaining"] = self.config.budget.max_tool_attempts - sum(
                r.admitted for r in _tools(state)
            )
        if stage == "draft_thesis":
            completion = state["investigation"]
            if completion is None:
                raise ResearchInvariantError("thesis requires investigation completion")
            data["investigation_completion"] = completion.model_dump(mode="json")
        if stage in ("critique", "final_assessment"):
            thesis = state["thesis"]
            if thesis is None:
                raise ResearchInvariantError(
                    "critic and assessment require validated thesis"
                )
            data["thesis"] = thesis.model_dump(mode="json")
        if stage == "final_assessment":
            critique = state["critique"]
            if critique is None:
                raise ResearchInvariantError("assessment requires independent critique")
            data["critique"] = critique.model_dump(mode="json")
        return data

    async def call(
        self,
        state: ResearchState,
        stage: Literal["investigate", "draft_thesis", "critique", "final_assessment"],
    ) -> tuple[ResearchState, ModelReply | None]:
        state = self.guard_deadline(state)
        if state["failure"] is not None:
            return state, None
        previous = _models(state)
        stage_count = sum(r.request.stage == stage for r in previous)
        max_stage = (
            self.config.budget.max_investigation_calls if stage == "investigate" else 1
        )
        if (
            len(previous) >= self.config.budget.max_model_calls
            or stage_count >= max_stage
        ):
            return _failure(
                state,
                ResearchFailureCode.MODEL_BUDGET_EXHAUSTED,
                AbstentionReason.BUDGET_EXHAUSTED,
                "model call budget exhausted",
            ), None
        call_id = (
            f"{state['context'].request.research_run_id}/model/{len(previous) + 1:04d}"
        )
        request = ModelRequest(
            call_id=call_id,
            stage=stage,
            configuration=self.config.model,
            messages=prompt_messages(stage, self.data(state, stage)),
            prompt_identity=prompt_identity(stage),
            tools=tool_definitions(state["context"].request.opportunity.pair)
            if stage == "investigate"
            else (),
            output_schema=structured_schema(stage),
            max_output_tokens=self.config.investigation_output_cap
            if stage == "investigate"
            else self.config.synthesis_output_cap,
        )
        request = ModelRequest.model_validate(self.model.prepare(request))
        prepared = request.prepared_input_json
        if prepared is None:
            raise ResearchInvariantError("model adapter did not prepare its input")
        if len(prepared.encode("utf-8")) > self.config.max_model_input_bytes:
            state = _failure(
                state,
                ResearchFailureCode.INPUT_BUDGET_EXHAUSTED,
                AbstentionReason.BUDGET_EXHAUSTED,
                "serialized model input exceeds 128 KiB",
                call_id,
            )
            failure = state["failure"]
            if failure is None:
                raise ResearchInvariantError("input budget rejection lacks failure")
            return {
                **state,
                "trace": state["trace"]
                + (ModelRequestRejection(request=request, failure=failure),),
            }, None
        state = self.guard_deadline(state)
        pending_failure = state["failure"]
        if pending_failure is not None:
            return {
                **state,
                "trace": state["trace"]
                + (ModelRequestRejection(request=request, failure=pending_failure),),
            }, None
        started = monotonic()
        call_deadline = min(
            started + self.config.model_call_deadline_seconds,
            self.deadline,
        )
        overall_limited = call_deadline == self.deadline
        task = asyncio.create_task(self.model.invoke(request), name=call_id)
        try:
            done, _ = await asyncio.wait(
                {task},
                timeout=max(0, call_deadline - monotonic()),
            )
        except asyncio.CancelledError:
            _cancel_and_observe(task)
            raise
        try:
            self.guard_clock(state)
        except ResearchInvariantError:
            _cancel_and_observe(task)
            raise
        overdue = not done or monotonic() >= call_deadline
        reply: ModelReply
        if overdue:
            _cancel_and_observe(task)
            reply = FailureReply(
                code=ProviderFailureCode.TIMEOUT,
                message="software model-call deadline exceeded",
                metadata=ProviderMetadata(request_json=prepared),
            )
        else:
            reply = task.result()
        try:
            reply = TypeAdapter(ModelReply).validate_python(reply)
        except ValidationError:
            # Reject invalid Unicode/envelopes before retaining the reply. Escaped
            # audit JSON preserves the original code units without unsafe scalars.
            metadata = ProviderMetadata(
                response_json=json.dumps(
                    reply.model_dump(mode="python"), ensure_ascii=True
                ),
                request_json=prepared,
            )
            reply = (
                ToolReply(requests=(), metadata=metadata)
                if isinstance(reply, ToolReply)
                else FailureReply(
                    code=ProviderFailureCode.INVALID_OUTPUT
                    if isinstance(reply, StructuredReply)
                    else ProviderFailureCode.INVALID_ENVELOPE,
                    message="model reply failed Unicode or envelope validation",
                    metadata=metadata,
                )
            )
        state = {
            **state,
            "trace": state["trace"] + (ModelCallRecord(request=request, reply=reply),),
        }
        if overdue and overall_limited:
            return _failure(
                state,
                ResearchFailureCode.WORKFLOW_TIMEOUT,
                AbstentionReason.RESEARCH_FAILURE,
                "overall workflow deadline exceeded",
                call_id,
            ), None
        state = self.guard_deadline(state)
        if state["failure"] is not None:
            return state, None
        if isinstance(reply, FailureReply):
            reason = (
                AbstentionReason.VALIDATION_FAILURE
                if reply.code
                in (
                    ProviderFailureCode.INVALID_ENVELOPE,
                    ProviderFailureCode.INVALID_OUTPUT,
                )
                else AbstentionReason.RESEARCH_FAILURE
            )
            return _failure(
                state,
                ResearchFailureCode(reply.code.value),
                reason,
                reply.message,
                call_id,
            ), None
        if isinstance(reply, RefusalReply):
            return _failure(
                state,
                ResearchFailureCode.REFUSAL,
                AbstentionReason.RESEARCH_FAILURE,
                "provider refused research output",
                call_id,
            ), None
        return state, reply

    def validate_output(
        self, state: ResearchState, reply: StructuredReply, schema: type[BaseModel]
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
            if set(references) - {
                e.evidence_id for e in collected_evidence(state["trace"])
            }:
                return ResearchFailureCode.UNKNOWN_EVIDENCE_REFERENCE
            if isinstance(output, TradeDecision) and output.critic_response is None:
                raise ModelOutputError(
                    "completed assessment requires nonblank critic_response"
                )
            return output
        except (ModelOutputError, ValidationError):
            return ResearchFailureCode.INVALID_MODEL_OUTPUT

    async def investigate(self, state: ResearchState) -> ResearchState:
        state, reply = await self.call(state, "investigate")
        if reply is None:
            return state
        if isinstance(reply, ToolReply):
            return {**state, "action": reply}
        if not isinstance(reply, StructuredReply):
            raise ResearchInvariantError("unexpected model reply variant")
        output = self.validate_output(state, reply, InvestigationCompletion)
        if isinstance(output, ResearchFailureCode):
            return self.invalid_output(state, output)
        if not isinstance(output, InvestigationCompletion):
            raise ResearchInvariantError("unexpected investigation schema")
        return {**state, "investigation": output, "action": None}

    def invalid_output(
        self, state: ResearchState, code: ResearchFailureCode
    ) -> ResearchState:
        return _failure(
            state,
            code,
            AbstentionReason.VALIDATION_FAILURE,
            "model output failed structured validation or evidence grounding",
            _models(state)[-1].request.call_id,
        )

    async def execute_tool(self, state: ResearchState) -> ResearchState:
        state = self.guard_deadline(state)
        if state["failure"] is not None:
            return state
        action = state["action"]
        if action is None:
            raise ResearchInvariantError("execute_tool requires a model tool response")
        history = _tools(state)
        admitted = (
            sum(r.admitted for r in history) < self.config.budget.max_tool_attempts
        )
        attempt_id = (
            f"{state['context'].request.research_run_id}/tool/{len(history) + 1:04d}"
        )
        call_id = _models(state)[-1].request.call_id
        query_or_rejection = (
            bind_tool_request(
                action.requests, state["context"].request.opportunity.pair
            )
            if admitted
            else ToolRejection(
                code="TOOL_BUDGET_EXHAUSTED",
                message="another tool request after eight admitted attempts",
            )
        )
        if isinstance(query_or_rejection, ToolRejection):
            record = ToolRecord(
                attempt_id=attempt_id,
                model_call_id=call_id,
                requests=action.requests,
                admitted=admitted,
                rejection=query_or_rejection,
            )
            state = {**state, "trace": state["trace"] + (record,), "action": None}
            if not admitted:
                return _failure(
                    state,
                    ResearchFailureCode.TOOL_BUDGET_EXHAUSTED,
                    AbstentionReason.BUDGET_EXHAUSTED,
                    query_or_rejection.message,
                    call_id,
                )
            invalid = sum(
                r.rejection is not None and r.rejection.code == "INVALID_TOOL_REQUEST"
                for r in _tools(state)
            )
            if invalid >= self.config.budget.max_invalid_tool_requests:
                return _failure(
                    state,
                    ResearchFailureCode.INVALID_TOOL_REQUEST,
                    AbstentionReason.VALIDATION_FAILURE,
                    "second invalid tool request",
                    call_id,
                )
            return state
        query = query_or_rejection
        cached = next(
            (r for r in history if r.query == query and r.result is not None), None
        )
        self.guard_clock(state)
        try:
            result = (
                cached.result
                if cached is not None
                else dispatch_tool(self.service, query)
            )
        finally:
            self.guard_clock(state)
        if isinstance(result, ResearchToolSuccess):
            validated = ResearchToolSuccess.model_validate(result)
            if (
                validated.context.query != query
                or validated.context.window.end != state["context"].research_at
            ):
                raise ResearchInvariantError(
                    "tool result query/time differs from bound request"
                )
            for item in validated.context.inputs:
                source = item.source.model_dump(exclude={"role"})
                if source not in [
                    s.model_dump() for s in state["context"].source_bindings
                ]:
                    raise ResearchInvariantError(
                        "tool result source differs from runtime binding"
                    )
        elif isinstance(result, ResearchToolUnavailable):
            unavailable = ResearchToolUnavailable.model_validate(result)
            if (
                unavailable.query != query
                or unavailable.window.end != state["context"].research_at
            ):
                raise ResearchInvariantError(
                    "unavailable result differs from bound query/time"
                )
        else:
            raise ResearchInvariantError("deterministic tool returned unknown result")
        record = ToolRecord(
            attempt_id=attempt_id,
            model_call_id=call_id,
            requests=action.requests,
            admitted=True,
            query=query,
            result=result,
            cached_from=cached.attempt_id if cached else None,
        )
        state = {**state, "trace": state["trace"] + (record,), "action": None}
        collected_evidence(state["trace"])
        return self.guard_deadline(state)

    async def draft_thesis(self, state: ResearchState) -> ResearchState:
        state, reply = await self.call(state, "draft_thesis")
        if reply is None:
            return state
        if not isinstance(reply, StructuredReply):
            return self.invalid_output(state, ResearchFailureCode.INVALID_MODEL_OUTPUT)
        output = self.validate_output(state, reply, ResearchThesis)
        if isinstance(output, ResearchFailureCode):
            return self.invalid_output(state, output)
        if not isinstance(output, ResearchThesis):
            raise ResearchInvariantError("unexpected thesis schema")
        return {**state, "thesis": output}

    async def critique(self, state: ResearchState) -> ResearchState:
        state, reply = await self.call(state, "critique")
        if reply is None:
            return state
        if not isinstance(reply, StructuredReply):
            return self.invalid_output(state, ResearchFailureCode.INVALID_MODEL_OUTPUT)
        output = self.validate_output(state, reply, Critique)
        if isinstance(output, ResearchFailureCode):
            return self.invalid_output(state, output)
        if not isinstance(output, Critique):
            raise ResearchInvariantError("unexpected critique schema")
        return {**state, "critique": output}

    async def final_assessment(self, state: ResearchState) -> ResearchState:
        state, reply = await self.call(state, "final_assessment")
        if reply is None:
            return state
        if not isinstance(reply, StructuredReply):
            return self.invalid_output(state, ResearchFailureCode.INVALID_MODEL_OUTPUT)
        output = self.validate_output(state, reply, TradeDecision)
        if isinstance(output, ResearchFailureCode):
            return self.invalid_output(state, output)
        if not isinstance(output, TradeDecision):
            raise ResearchInvariantError("unexpected assessment schema")
        return {**state, "assessment": output}

    def transition(
        self, state: ResearchState, source: str, target: str
    ) -> ResearchState:
        failure = state["failure"]
        return {
            **state,
            "trace": state["trace"]
            + (
                NodeTransition(
                    source=source,
                    target=target,
                    termination_reason=(failure.code.value if failure else "COMPLETED")
                    if target == END
                    else None,
                ),
            ),
        }


async def run_opportunity_research(
    request: OpportunityResearchRequest,
    service: MarketResearchService,
    model: ResearchModel,
    configuration: ResearchWorkflowConfig,
    *,
    code: CodeIdentity | None = None,
) -> ResearchRunResult:
    """Fresh inference at one frozen simulation boundary; no persistence/retries."""
    request = OpportunityResearchRequest.model_validate(request)
    configuration = ResearchWorkflowConfig.model_validate(configuration)
    provider = ProviderIdentity.model_validate(model.identity)
    if provider.provider != configuration.model.provider:
        raise ValueError("configured provider differs from model adapter")
    context = OpportunityResearchContext(
        request=request,
        research_at=service.now,
        benchmarks=tuple(p for p in BENCHMARK_ORDER if p != request.opportunity.pair),
        source_bindings=service.source_bindings,
    )
    code = CodeIdentity.model_validate(code) if code else current_code_identity()
    runtime = _ResearchRuntime(service, model, configuration)
    state: ResearchState = {
        "context": context,
        "trace": (),
        "action": None,
        "investigation": None,
        "thesis": None,
        "critique": None,
        "assessment": None,
        "failure": None,
    }
    state = runtime.transition(state, START, "investigate")
    graph = StateGraph(ResearchState)

    async def investigate(state: ResearchState) -> ResearchState:
        state = await runtime.investigate(state)
        target = (
            END
            if state["failure"]
            else "execute_tool"
            if state["action"]
            else "draft_thesis"
        )
        return runtime.transition(state, "investigate", target)

    async def execute_tool(state: ResearchState) -> ResearchState:
        state = await runtime.execute_tool(state)
        return runtime.transition(
            state, "execute_tool", END if state["failure"] else "investigate"
        )

    async def draft_thesis(state: ResearchState) -> ResearchState:
        state = await runtime.draft_thesis(state)
        return runtime.transition(
            state, "draft_thesis", END if state["failure"] else "critique"
        )

    async def critique(state: ResearchState) -> ResearchState:
        state = await runtime.critique(state)
        return runtime.transition(
            state, "critique", END if state["failure"] else "final_assessment"
        )

    async def final_assessment(state: ResearchState) -> ResearchState:
        state = await runtime.final_assessment(state)
        state = runtime.guard_deadline(state)
        return runtime.transition(state, "final_assessment", END)

    graph.add_node("investigate", investigate)
    graph.add_node("execute_tool", execute_tool)
    graph.add_node("draft_thesis", draft_thesis)
    graph.add_node("critique", critique)
    graph.add_node("final_assessment", final_assessment)
    graph.add_edge(START, "investigate")

    def route(state: ResearchState) -> str:
        transition = state["trace"][-1]
        if not isinstance(transition, NodeTransition):
            raise ResearchInvariantError("node lacks transition")
        return transition.target

    for node in (
        "investigate",
        "execute_tool",
        "draft_thesis",
        "critique",
        "final_assessment",
    ):
        graph.add_conditional_edges(node, route)
    compiled = graph.compile()
    final = cast(
        ResearchState,
        await compiled.ainvoke(
            state,
            {"recursion_limit": configuration.budget.recursion_limit, "callbacks": []},
        ),
    )
    final = runtime.guard_deadline(final)
    # A deadline crossed during graph teardown must be reflected in termination.
    last = final["trace"][-1]
    if (
        final["failure"] is not None
        and isinstance(last, NodeTransition)
        and last.termination_reason == "COMPLETED"
    ):
        final = runtime.transition(final, "return", END)
    assessment = final["assessment"]
    if assessment is None:
        raise ResearchInvariantError("graph terminated without assessment")
    result = ResearchRunResult(
        status=ResearchRunStatus.INTERRUPTED
        if final["failure"]
        else ResearchRunStatus.COMPLETED,
        assessment_origin=AssessmentOrigin.RUNTIME
        if final["failure"]
        else AssessmentOrigin.MODEL,
        context=context,
        configuration=configuration,
        code=code,
        provider=provider,
        langgraph_version=version("langgraph"),
        trace=final["trace"],
        investigation=final["investigation"],
        thesis=final["thesis"],
        critique=final["critique"],
        assessment=assessment,
        failure=final["failure"],
    )
    checked = runtime.guard_deadline(final)
    if checked["failure"] is not None and final["failure"] is None:
        checked = runtime.transition(checked, "return", END)
        result = ResearchRunResult.model_validate(
            result.model_copy(
                update={
                    "status": ResearchRunStatus.INTERRUPTED,
                    "assessment_origin": AssessmentOrigin.RUNTIME,
                    "trace": checked["trace"],
                    "assessment": checked["assessment"],
                    "failure": checked["failure"],
                }
            )
        )
    runtime.guard_clock(final)
    return result
