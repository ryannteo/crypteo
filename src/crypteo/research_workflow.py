"""Five-node bounded opportunity research, isolated from trading and replay."""

import asyncio
import json
from contextlib import suppress
from importlib.metadata import version
from time import monotonic
from typing import Any, Literal, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from pydantic import TypeAdapter, ValidationError

from crypteo.domain.market_research import (
    ResearchToolSuccess,
    ResearchToolUnavailable,
)
from crypteo.domain.research import (
    Critique,
    DecisionOutcome,
    ResearchThesis,
    TradeDecision,
)
from crypteo.domain.research_journal import (
    AbortDiagnostic,
    AssessmentStage,
    CritiqueStage,
    InvestigationStage,
    LifecycleEvent,
    ModelCallFinished,
    ModelCallStarted,
    ModelRequestPrepared,
    ModelRequestRejected,
    ModelResponseObserved,
    NodeTransitioned,
    ResearchArtifact,
    ResearchRunHeader,
    StageCompleted,
    ThesisStage,
    ToolRequested,
    ToolResolved,
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
    _abstention_reason,
)
from crypteo.experiment import CodeIdentity, current_code_identity
from crypteo.market_research import MarketResearchService
from crypteo.research_journal import ResearchStorageError
from crypteo.research_model import (
    FailureReply,
    ModelReply,
    ModelRequest,
    ObservableResearchModel,
    ProviderFailureCode,
    ProviderIdentity,
    ProviderMetadata,
    ProviderObservation,
    RefusalReply,
    ResearchModel,
    StructuredReply,
    ToolReply,
)
from crypteo.research_persistence import ResearchJournal, ResearchStore
from crypteo.research_prompts import prompt_identity, prompt_messages, structured_schema
from crypteo.research_tools import bind_tool_request, dispatch_tool, tool_definitions
from crypteo.research_validation import ResearchInvariantError as ResearchInvariantError
from crypteo.research_validation import collected_evidence as collected_evidence
from crypteo.research_validation import (
    offers_tools,
    request_rejection,
    research_data,
    validate_stage_output,
)

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
    # An invalid structured reply that the next call of its stage re-asks.
    rejected: ModelCallRecord | None


def _tools(state: ResearchState) -> tuple[ToolRecord, ...]:
    return tuple(r for r in state["trace"] if isinstance(r, ToolRecord))


def _models(state: ResearchState) -> tuple[ModelCallRecord, ...]:
    return tuple(r for r in state["trace"] if isinstance(r, ModelCallRecord))


def _reasks(state: ResearchState) -> tuple[ModelCallRecord, ...]:
    return tuple(r for r in _models(state) if request_rejection(r.request) is not None)


def _failure(
    state: ResearchState,
    code: ResearchFailureCode,
    message: str,
    call_id: str | None = None,
) -> ResearchState:
    failure = ResearchFailure(
        code=code,
        abstention_reason=_abstention_reason(code),
        message=message,
        call_id=call_id,
    )
    return {
        **state,
        "failure": failure,
        "action": None,
        "rejected": None,
        "assessment": TradeDecision(
            outcome=DecisionOutcome.ABSTAIN,
            rationale=message,
            abstention_reasons=(failure.abstention_reason,),
        ),
    }


class _ResearchRuntime:
    def __init__(
        self,
        service: MarketResearchService,
        model: ResearchModel,
        config: ResearchWorkflowConfig,
        journal: ResearchJournal | None = None,
    ) -> None:
        self.journal = journal
        self.service, self.model, self.config = service, model, config
        self.deadline = monotonic() + config.workflow_deadline_seconds

    def emit(self, event: LifecycleEvent) -> None:
        if self.journal is not None:
            self.journal.append(event)

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
                "overall workflow deadline exceeded",
            )
        return state

    def data(
        self,
        state: ResearchState,
        stage: str,
        rejected: ModelCallRecord | None,
    ) -> dict[str, Any]:
        return research_data(
            state["context"],
            state["trace"],
            self.config,
            stage,
            {
                key: value
                for key, value in (
                    ("investigate", state["investigation"]),
                    ("draft_thesis", state["thesis"]),
                    ("critique", state["critique"]),
                )
                if value is not None
            },
            rejected,
        )

    async def call(
        self,
        state: ResearchState,
        stage: Literal["investigate", "draft_thesis", "critique", "final_assessment"],
    ) -> tuple[ResearchState, ModelReply | None]:
        state = self.guard_deadline(state)
        if state["failure"] is not None:
            return state, None
        rejected, state = state["rejected"], {**state, "rejected": None}
        previous = _models(state)
        # Re-asks are budgeted by max_output_reasks, not by per-stage limits.
        stage_count = sum(
            r.request.stage == stage and request_rejection(r.request) is None
            for r in previous
        )
        max_stage = (
            self.config.budget.max_investigation_calls if stage == "investigate" else 1
        )
        if len(previous) >= self.config.budget.max_model_calls or (
            rejected is None and stage_count >= max_stage
        ):
            return _failure(
                state,
                ResearchFailureCode.MODEL_BUDGET_EXHAUSTED,
                "model call budget exhausted",
            ), None
        call_id = (
            f"{state['context'].request.research_run_id}/model/{len(previous) + 1:04d}"
        )
        data = self.data(state, stage, rejected)
        request = ModelRequest(
            call_id=call_id,
            stage=stage,
            configuration=self.config.model,
            messages=prompt_messages(stage, data),
            prompt_identity=prompt_identity(stage),
            # Code owns the tool budget: offer no tool the model may not use.
            tools=tool_definitions(
                state["context"].request.opportunity.pair,
                state["context"].benchmarks,
            )
            if offers_tools(stage, data)
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
        self.emit(ModelRequestPrepared(request=request))
        if len(prepared.encode("utf-8")) > self.config.max_model_input_bytes:
            state = _failure(
                state,
                ResearchFailureCode.INPUT_BUDGET_EXHAUSTED,
                "serialized model input exceeds 128 KiB",
                call_id,
            )
            failure = state["failure"]
            if failure is None:
                raise ResearchInvariantError("input budget rejection lacks failure")
            return self.reject(state, request, failure), None
        state = self.guard_deadline(state)
        pending_failure = state["failure"]
        if pending_failure is not None:
            return self.reject(state, request, pending_failure), None
        started = monotonic()
        call_deadline = min(
            started + self.config.model_call_deadline_seconds,
            self.deadline,
        )
        overall_limited = call_deadline == self.deadline
        observation_open = True

        def observe(observation: ProviderObservation) -> None:
            if not observation_open or monotonic() >= call_deadline:
                return
            self.emit(
                ModelResponseObserved(
                    call_id=call_id,
                    body=observation.body,
                    http_status=observation.http_status,
                    request_id=observation.request_id,
                    text_encoding=observation.text_encoding,
                )
            )

        async def invoke_before_deadline() -> ModelReply:
            if monotonic() >= call_deadline:
                return FailureReply(
                    code=ProviderFailureCode.TIMEOUT,
                    message="software model-call deadline exceeded",
                    metadata=ProviderMetadata(request_json=prepared),
                )
            # No yield between this guard and entering the provider: scheduling
            # the task itself may have consumed the remaining inference budget.
            if self.journal is not None and isinstance(
                self.model, ObservableResearchModel
            ):
                return await self.model.invoke_observed(request, observe)
            return await self.model.invoke(request)

        self.emit(ModelCallStarted(call_id=call_id))
        task: asyncio.Task[ModelReply] | None = None
        done: set[asyncio.Task[ModelReply]] = set()
        # The synchronous commit can consume the remaining inference budget.
        # Do not even create the provider coroutine once that budget has expired.
        if monotonic() < call_deadline:
            task = asyncio.create_task(invoke_before_deadline(), name=call_id)
            try:
                done, _ = await asyncio.wait(
                    {task},
                    timeout=max(0, call_deadline - monotonic()),
                )
            except asyncio.CancelledError:
                observation_open = False
                _cancel_and_observe(task)
                raise
            finally:
                observation_open = False
        else:
            observation_open = False
        try:
            self.guard_clock(state)
        except ResearchInvariantError:
            if task is not None:
                _cancel_and_observe(task)
            raise
        if self.journal is not None and not self.journal.healthy:
            if task is not None:
                if task.done():
                    task.result()  # Preserve the original observation exception.
                _cancel_and_observe(task)
            raise ResearchStorageError("provider observation could not be persisted")
        overdue = task is None or not done or monotonic() >= call_deadline
        reply: ModelReply
        if overdue:
            if task is not None:
                _cancel_and_observe(task)
            reply = FailureReply(
                code=ProviderFailureCode.TIMEOUT,
                message="software model-call deadline exceeded",
                metadata=ProviderMetadata(request_json=prepared),
            )
        else:
            assert task is not None
            reply = task.result()
        try:
            reply = TypeAdapter(ModelReply).validate_python(reply)
        except ValidationError:
            # Reject invalid Unicode/envelopes before retaining the reply. Escaped
            # audit JSON preserves the original code units without unsafe scalars.
            metadata = ProviderMetadata(
                rejected_reply_json=json.dumps(
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
        record = ModelCallRecord(request=request, reply=reply)
        self.emit(ModelCallFinished(record=record))
        state = {**state, "trace": state["trace"] + (record,)}
        if overdue and overall_limited:
            return _failure(
                state,
                ResearchFailureCode.WORKFLOW_TIMEOUT,
                "overall workflow deadline exceeded",
                call_id,
            ), None
        state = self.guard_deadline(state)
        if state["failure"] is not None:
            return state, None
        if isinstance(reply, FailureReply):
            return _failure(
                state,
                ResearchFailureCode(reply.code.value),
                reply.message,
                call_id,
            ), None
        if isinstance(reply, RefusalReply):
            return _failure(
                state,
                ResearchFailureCode.REFUSAL,
                "provider refused research output",
                call_id,
            ), None
        return state, reply

    def reject(
        self, state: ResearchState, request: ModelRequest, failure: ResearchFailure
    ) -> ResearchState:
        record = ModelRequestRejection(request=request, failure=failure)
        self.emit(ModelRequestRejected(record=record))
        return {**state, "trace": state["trace"] + (record,)}

    async def investigate(self, state: ResearchState) -> ResearchState:
        state, reply = await self.call(state, "investigate")
        if reply is None:
            return state
        if isinstance(reply, ToolReply):
            return {**state, "action": reply}
        if not isinstance(reply, StructuredReply):
            raise ResearchInvariantError("unexpected model reply variant")
        output = validate_stage_output(state["trace"], reply, InvestigationCompletion)
        if isinstance(output, ResearchFailureCode):
            return self.reask_or_fail(state, output)
        if not isinstance(output, InvestigationCompletion):
            raise ResearchInvariantError("unexpected investigation schema")
        self.emit(
            StageCompleted(
                call_id=_models(state)[-1].request.call_id,
                value=InvestigationStage(output=output),
            )
        )
        return {**state, "investigation": output, "action": None}

    def invalid_output(
        self, state: ResearchState, code: ResearchFailureCode
    ) -> ResearchState:
        return _failure(
            state,
            code,
            "model output failed structured validation or evidence grounding",
            _models(state)[-1].request.call_id,
        )

    def reask_or_fail(
        self, state: ResearchState, code: ResearchFailureCode
    ) -> ResearchState:
        """Re-ask an invalid structured first reply once, within the run budget."""
        budget, record = self.config.budget, _models(state)[-1]
        reasks = _reasks(state)
        if (
            request_rejection(record.request) is not None
            or len(reasks) >= budget.max_output_reasks
            or len(_models(state)) >= budget.max_model_calls
        ):
            return self.invalid_output(state, code)
        return {**state, "rejected": record}

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
        self.emit(
            ToolRequested(
                attempt_id=attempt_id, model_call_id=call_id, requests=action.requests
            )
        )
        query_or_rejection = (
            bind_tool_request(
                action.requests,
                state["context"].request.opportunity.pair,
                state["context"].benchmarks,
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
            self.emit(ToolResolved(record=record))
            state = {**state, "trace": state["trace"] + (record,), "action": None}
            if not admitted:
                return _failure(
                    state,
                    ResearchFailureCode.TOOL_BUDGET_EXHAUSTED,
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
        self.emit(ToolResolved(record=record))
        state = {**state, "trace": state["trace"] + (record,), "action": None}
        collected_evidence(state["trace"])
        return self.guard_deadline(state)

    async def draft_thesis(self, state: ResearchState) -> ResearchState:
        state, reply = await self.call(state, "draft_thesis")
        if reply is None:
            return state
        if not isinstance(reply, StructuredReply):
            return self.invalid_output(state, ResearchFailureCode.INVALID_MODEL_OUTPUT)
        output = validate_stage_output(state["trace"], reply, ResearchThesis)
        if isinstance(output, ResearchFailureCode):
            return self.reask_or_fail(state, output)
        if not isinstance(output, ResearchThesis):
            raise ResearchInvariantError("unexpected thesis schema")
        self.emit(
            StageCompleted(
                call_id=_models(state)[-1].request.call_id,
                value=ThesisStage(output=output),
            )
        )
        return {**state, "thesis": output}

    async def critique(self, state: ResearchState) -> ResearchState:
        state, reply = await self.call(state, "critique")
        if reply is None:
            return state
        if not isinstance(reply, StructuredReply):
            return self.invalid_output(state, ResearchFailureCode.INVALID_MODEL_OUTPUT)
        output = validate_stage_output(state["trace"], reply, Critique)
        if isinstance(output, ResearchFailureCode):
            return self.reask_or_fail(state, output)
        if not isinstance(output, Critique):
            raise ResearchInvariantError("unexpected critique schema")
        self.emit(
            StageCompleted(
                call_id=_models(state)[-1].request.call_id,
                value=CritiqueStage(output=output),
            )
        )
        return {**state, "critique": output}

    async def final_assessment(self, state: ResearchState) -> ResearchState:
        state, reply = await self.call(state, "final_assessment")
        if reply is None:
            return state
        if not isinstance(reply, StructuredReply):
            return self.invalid_output(state, ResearchFailureCode.INVALID_MODEL_OUTPUT)
        output = validate_stage_output(state["trace"], reply, TradeDecision)
        if isinstance(output, ResearchFailureCode):
            return self.reask_or_fail(state, output)
        if not isinstance(output, TradeDecision):
            raise ResearchInvariantError("unexpected assessment schema")
        self.emit(
            StageCompleted(
                call_id=_models(state)[-1].request.call_id,
                value=AssessmentStage(output=output),
            )
        )
        return {**state, "assessment": output}

    def transition(
        self, state: ResearchState, source: str, target: str
    ) -> ResearchState:
        failure = state["failure"]
        record = NodeTransition(
            source=source,
            target=target,
            termination_reason=(failure.code.value if failure else "COMPLETED")
            if target == END
            else None,
        )
        self.emit(NodeTransitioned(record=record))
        return {**state, "trace": state["trace"] + (record,)}


def _research_inputs(
    request: OpportunityResearchRequest,
    service: MarketResearchService,
    model: ResearchModel,
    configuration: ResearchWorkflowConfig,
) -> tuple[
    OpportunityResearchRequest,
    ResearchWorkflowConfig,
    ProviderIdentity,
    OpportunityResearchContext,
]:
    request = OpportunityResearchRequest.model_validate(request)
    configuration = ResearchWorkflowConfig.model_validate(configuration)
    provider = ProviderIdentity.model_validate(model.identity)
    if provider.provider != configuration.model.provider:
        raise ValueError("configured provider differs from model adapter")
    bindings = service.source_bindings
    bound_pairs = {binding.pair for binding in bindings}
    context = OpportunityResearchContext(
        request=request,
        research_at=service.now,
        benchmarks=tuple(
            p
            for p in BENCHMARK_ORDER
            if p != request.opportunity.pair and p in bound_pairs
        ),
        source_bindings=bindings,
    )
    return request, configuration, provider, context


async def _run_opportunity_research(
    context: OpportunityResearchContext,
    service: MarketResearchService,
    model: ResearchModel,
    configuration: ResearchWorkflowConfig,
    provider: ProviderIdentity,
    *,
    code: CodeIdentity | None = None,
    journal: ResearchJournal | None = None,
) -> ResearchRunResult:
    """Fresh inference at one frozen simulation boundary; no persistence/retries."""
    code = CodeIdentity.model_validate(code) if code else current_code_identity()
    runtime = _ResearchRuntime(service, model, configuration, journal)
    state: ResearchState = {
        "context": context,
        "trace": (),
        "action": None,
        "investigation": None,
        "thesis": None,
        "critique": None,
        "assessment": None,
        "failure": None,
        "rejected": None,
    }
    state = runtime.transition(state, START, "investigate")
    graph = StateGraph(ResearchState)

    async def investigate(state: ResearchState) -> ResearchState:
        state = await runtime.investigate(state)
        target = (
            END
            if state["failure"]
            else "investigate"
            if state["rejected"]
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
        target = (
            END
            if state["failure"]
            else "draft_thesis"
            if state["rejected"]
            else "critique"
        )
        return runtime.transition(state, "draft_thesis", target)

    async def critique(state: ResearchState) -> ResearchState:
        state = await runtime.critique(state)
        target = (
            END
            if state["failure"]
            else "critique"
            if state["rejected"]
            else "final_assessment"
        )
        return runtime.transition(state, "critique", target)

    async def final_assessment(state: ResearchState) -> ResearchState:
        state = await runtime.final_assessment(state)
        state = runtime.guard_deadline(state)
        return runtime.transition(
            state,
            "final_assessment",
            "final_assessment" if state["rejected"] else END,
        )

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


async def run_opportunity_research(
    request: OpportunityResearchRequest,
    service: MarketResearchService,
    model: ResearchModel,
    configuration: ResearchWorkflowConfig,
    *,
    code: CodeIdentity | None = None,
) -> ResearchRunResult:
    """Existing in-memory research entrypoint; no persistence is implied."""
    _, configuration, provider, context = _research_inputs(
        request, service, model, configuration
    )
    return await _run_opportunity_research(
        context, service, model, configuration, provider, code=code
    )


async def run_durable_opportunity_research(
    request: OpportunityResearchRequest,
    service: MarketResearchService,
    model: ResearchModel,
    configuration: ResearchWorkflowConfig,
    *,
    store: ResearchStore,
    code: CodeIdentity | None = None,
) -> ResearchArtifact:
    """Commit lifecycle boundaries and atomically publish; never resume or retry."""
    if not isinstance(model, ObservableResearchModel):
        raise ValueError("durable research requires a provider observation boundary")
    _, configuration, provider, context = _research_inputs(
        request, service, model, configuration
    )
    header = ResearchRunHeader(
        context=context,
        configuration=configuration,
        code=CodeIdentity.model_validate(code) if code else current_code_identity(),
        provider=provider,
        langgraph_version=version("langgraph"),
    )
    journal = store.create_run(header)
    try:
        result = await _run_opportunity_research(
            context,
            service,
            model,
            configuration,
            provider,
            code=header.code,
            journal=journal,
        )
        return journal.finalize(result)
    except ResearchStorageError:
        raise
    except BaseException as error:
        if journal.healthy:
            # Preserve the original invariant exception/cancellation.
            with suppress(BaseException):
                journal.abort(
                    AbortDiagnostic(
                        reason="EXTERNAL_CANCELLATION"
                        if isinstance(error, asyncio.CancelledError)
                        else "INVARIANT_OR_PROGRAMMER_FAILURE"
                    )
                )
        raise
