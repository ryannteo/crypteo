"""Pure lifecycle grammar, canonical encoding and materialized projection."""

from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from pydantic import BaseModel

from crypteo.domain.research import (
    Critique,
    DecisionOutcome,
    ResearchThesis,
    TradeDecision,
)
from crypteo.domain.research_journal import (
    AssessmentStage,
    CritiqueStage,
    EventEnvelope,
    InvestigationStage,
    ModelCallFinished,
    ModelCallStarted,
    ModelRequestPrepared,
    ModelRequestRejected,
    ModelResponseObserved,
    NodeTransitioned,
    ResearchArtifact,
    ResearchRunHeader,
    RunAborted,
    RunFinished,
    RunStarted,
    StageCompleted,
    StageOutput,
    ThesisStage,
    ToolRequested,
    ToolResolved,
)
from crypteo.domain.research_workflow import (
    InvestigationCompletion,
    ModelCallRecord,
    ModelRequestRejection,
    NodeTransition,
    ResearchFailureCode,
    ResearchRunResult,
    ToolRecord,
    ToolRejection,
    TraceRecord,
)
from crypteo.research_model import (
    FailureReply,
    ModelReply,
    ModelRequest,
    ProviderMetadata,
    RefusalReply,
    StructuredReply,
    ToolReply,
    parse_json,
    render_json,
)
from crypteo.research_prompts import prompt_identity, prompt_messages, structured_schema
from crypteo.research_tools import bind_tool_request, tool_definitions
from crypteo.research_validation import (
    ResearchValidationError,
    offers_tools,
    output_rejection,
    request_rejection,
    research_data,
    validate_stage_output,
    validate_trace_relationships,
)


class ResearchStorageError(RuntimeError):
    """Durability/integrity failure, never a research conclusion. Do not retry a run."""


def canonical_bytes(value: BaseModel | dict[str, Any]) -> bytes:
    return render_json(value).encode("utf-8")


def framed_digest(label: str, *parts: bytes) -> str:
    digest = sha256()
    for part in (label.encode("ascii"), *parts):
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return "sha256:" + digest.hexdigest()


def event_checksum(envelope: EventEnvelope) -> str:
    body = (
        envelope.event.body
        if isinstance(envelope.event, ModelResponseObserved)
        else None
    )
    return framed_digest(
        "crypteo.research.event.v1",
        canonical_bytes(envelope),
        b"absent" if body is None else b"present",
        b"" if body is None else body,
    )


def ordered_digest(checksums: tuple[str, ...]) -> str:
    return framed_digest(
        "crypteo.research.run.v1", *(s.encode("ascii") for s in checksums)
    )


def artifact_checksum(artifact: ResearchArtifact) -> str:
    return framed_digest(
        "crypteo.research.artifact.v1",
        canonical_bytes(
            artifact.model_dump(mode="json", exclude={"artifact_checksum"})
        ),
    )


def decode_envelope(raw: bytes, body: bytes | None) -> EventEnvelope:
    parsed = parse_json(raw.decode("utf-8"))
    if not isinstance(parsed, dict) or not isinstance(parsed.get("event"), dict):
        raise ResearchValidationError("invalid event envelope")
    if parsed["event"].get("kind") == "MODEL_RESPONSE_OBSERVED":
        if body is None:
            raise ResearchValidationError("observation lacks body BLOB")
        # JSON validation is required for strict UTC/Decimal domain contracts.
        # Body stays opaque and never traverses the JSON encoder.
        parsed["event"]["body"] = ""
        envelope = EventEnvelope.model_validate_json(render_json(parsed))
        event = envelope.event
        assert isinstance(event, ModelResponseObserved)
        envelope = envelope.model_copy(
            update={"event": event.model_copy(update={"body": body})}
        )
    else:
        if body is not None:
            raise ResearchValidationError("unexpected response BLOB")
        envelope = EventEnvelope.model_validate_json(raw)
    if canonical_bytes(envelope) != raw:
        raise ResearchValidationError("noncanonical event JSON")
    return envelope


@dataclass(frozen=True)
class ProviderCodec:
    """Pure wire functions an adapter module shares with journal reconstruction."""

    render_request: Callable[[ModelRequest], dict[str, Any]]
    decode_response_entity: Callable[[bytes, ProviderMetadata], ModelReply]
    decode_http_error: Callable[[bytes, int, str | None, str], FailureReply]


def _anthropic_codec() -> ProviderCodec:
    from crypteo import anthropic_research

    return ProviderCodec(
        anthropic_research.render_anthropic_request,
        anthropic_research.decode_response_entity,
        anthropic_research.decode_http_error,
    )


# Adapter modules import provider SDKs, so each loads only when its runs are read.
PROVIDER_CODECS: dict[str, Callable[[], ProviderCodec]] = {
    "anthropic": _anthropic_codec,
}


def provider_codec(header: ResearchRunHeader) -> ProviderCodec | None:
    load = PROVIDER_CODECS.get(header.provider.provider)
    return None if load is None else load()


def validate_prepared(request: ModelRequest, header: ResearchRunHeader) -> None:
    if request.prepared_input_json is None:
        raise ResearchValidationError("prepared request lacks provider body")
    codec = provider_codec(header)
    if codec is not None:
        expected = codec.render_request(request)
    else:
        # Provider-neutral adapters can use the normalized project request encoding.
        expected = request.model_dump(mode="json") | {"prepared_input_json": None}
    if canonical_bytes(parse_json(request.prepared_input_json)) != canonical_bytes(
        expected
    ):
        raise ResearchValidationError("prepared body differs from normalized request")


def validate_observation(
    request: ModelRequest,
    observed: ModelResponseObserved | None,
    record: ModelCallRecord,
    header: ResearchRunHeader,
) -> None:
    reply = record.reply
    if reply.metadata.request_json not in (None, request.prepared_input_json):
        raise ResearchValidationError(
            "reply request body differs from prepared request"
        )
    codec = provider_codec(header)
    if observed is None:
        if codec is not None and not (
            isinstance(reply, FailureReply)
            and reply.code in ("PROVIDER_NETWORK", "MODEL_TIMEOUT")
        ):
            raise ResearchValidationError("provider reply requires an observed entity")
        if (
            reply.metadata.response_json is not None
            or reply.metadata.http_status is not None
        ):
            raise ResearchValidationError("normalized response lacks prior observation")
        return
    if isinstance(reply, FailureReply) and reply.code == "MODEL_TIMEOUT":
        # The entity may commit just before the software deadline expires.
        if (
            reply.message != "software model-call deadline exceeded"
            or reply.metadata
            != ProviderMetadata(request_json=request.prepared_input_json)
        ):
            raise ResearchValidationError(
                "observed entity can only be superseded by software timeout"
            )
        return
    if codec is None:
        # A byte/string comparison cannot establish that a normalized reply was
        # decoded from that entity, so providers without a codec cannot observe.
        raise ResearchValidationError("unsupported observable provider encoding")
    if observed.text_encoding != "utf-8":
        raise ResearchValidationError("provider response encoding must be UTF-8")
    if observed.http_status >= 400:
        if (
            request.prepared_input_json is None
            or codec.decode_http_error(
                observed.body,
                observed.http_status,
                observed.request_id,
                request.prepared_input_json,
            )
            != reply
        ):
            raise ResearchValidationError("HTTP error differs from normalized reply")
        return
    metadata = ProviderMetadata(
        request_json=request.prepared_input_json,
        request_id=observed.request_id,
        http_status=observed.http_status,
    )
    if codec.decode_response_entity(observed.body, metadata) != reply:
        raise ResearchValidationError(
            "observable provider body differs from normalized reply"
        )


@dataclass
class JournalProjection:
    header: ResearchRunHeader
    trace: tuple[TraceRecord, ...] = ()
    stages: dict[str, StageOutput] = field(default_factory=dict)
    node: str = "__start__"
    prepared: ModelRequest | None = None
    started: bool = False
    observed: ModelResponseObserved | None = None
    pending_tool: ToolRequested | None = None
    last_call: ModelCallRecord | None = None
    node_work: bool = False
    calls: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    terminal: RunFinished | RunAborted | None = None
    result: ResearchRunResult | None = None

    @property
    def pending(self) -> tuple[str, ...]:
        pending: list[str] = []
        if self.prepared is not None:
            boundary = (
                "RESPONSE_OBSERVED"
                if self.observed
                else "CALL_STARTED"
                if self.started
                else "REQUEST_PREPARED"
            )
            pending.append(f"{boundary}:{self.prepared.call_id}")
        if self.pending_tool is not None:
            pending.append(f"TOOL_REQUESTED:{self.pending_tool.attempt_id}")
        if self.terminal is None:
            pending.append(f"NODE:{self.node}")
        return tuple(pending)

    @property
    def work(self) -> list[ModelCallRecord | ModelRequestRejection | ToolRecord]:
        return [
            r
            for r in self.trace
            if isinstance(r, (ModelCallRecord, ModelRequestRejection, ToolRecord))
        ]

    def rejected_call(self) -> ModelCallRecord | None:
        """Invalid structured first reply that the next call must re-ask, if any."""
        records = self.work
        record = records[-1] if records else None
        if not isinstance(record, ModelCallRecord) or (
            request_rejection(record.request) is not None
        ):
            return None
        reasks = sum(
            isinstance(r, ModelCallRecord) and request_rejection(r.request) is not None
            for r in self.trace
        )
        if reasks >= 2 or self.calls >= 14:
            return None
        return record if output_rejection(self.trace, record) is not None else None

    def validate_request(self, request: ModelRequest) -> None:
        config, context = self.header.configuration, self.header.context
        if request.stage != self.node or self.node_work:
            raise ResearchValidationError("model request out of stage order")
        if (
            request.call_id
            != f"{self.header.research_run_id}/model/{self.calls + 1:04d}"
        ):
            raise ResearchValidationError("model call identity/sequence mismatch")
        rejected = self.rejected_call()
        count = self.counts.get(request.stage, 0)
        if self.calls >= 14 or (
            rejected is None and count >= (9 if request.stage == "investigate" else 1)
        ):
            raise ResearchValidationError("model call budget exceeded")
        data = research_data(
            context,
            self.trace,
            config,
            request.stage,
            {stage: value.output for stage, value in self.stages.items()},
            rejected,
        )
        expected = ModelRequest(
            call_id=request.call_id,
            stage=request.stage,
            configuration=config.model,
            messages=prompt_messages(request.stage, data),
            prompt_identity=prompt_identity(request.stage),
            tools=tool_definitions(context.request.opportunity.pair, context.benchmarks)
            if offers_tools(request.stage, data)
            else (),
            output_schema=structured_schema(request.stage),
            max_output_tokens=config.investigation_output_cap
            if request.stage == "investigate"
            else config.synthesis_output_cap,
            prepared_input_json=request.prepared_input_json,
        )
        if request != expected:
            raise ResearchValidationError(
                "request differs from accumulated context/policy"
            )
        validate_prepared(request, self.header)

    def forced_failure(self) -> str | None:
        """Failure implied by retained work, independent of wall-clock timing."""
        records = self.work
        if not records:
            return None
        record = records[-1]
        if isinstance(record, ModelRequestRejection):
            return record.failure.code.value
        if isinstance(record, ToolRecord):
            if record.rejection is not None:
                if not record.admitted:
                    return "TOOL_BUDGET_EXHAUSTED"
                invalid = sum(
                    isinstance(r, ToolRecord)
                    and r.rejection is not None
                    and r.rejection.code == "INVALID_TOOL_REQUEST"
                    for r in self.trace
                )
                if invalid >= 2:
                    return "INVALID_TOOL_REQUEST"
            return None
        reply = record.reply
        if isinstance(reply, FailureReply):
            return reply.code.value
        if isinstance(reply, RefusalReply):
            return "REFUSAL"
        if isinstance(reply, ToolReply):
            return (
                None
                if record.request.stage == "investigate"
                else "INVALID_MODEL_OUTPUT"
            )
        schemas: dict[str, type[BaseModel]] = {
            "investigate": InvestigationCompletion,
            "draft_thesis": ResearchThesis,
            "critique": Critique,
            "final_assessment": TradeDecision,
        }
        output = validate_stage_output(self.trace, reply, schemas[record.request.stage])
        if not isinstance(output, ResearchFailureCode) or (
            self.rejected_call() is not None
        ):
            return None
        return output.value

    def apply(self, envelope: EventEnvelope) -> None:
        event = envelope.event
        if self.terminal is not None:
            raise ResearchValidationError("event after terminal")
        if isinstance(event, RunAborted):
            self.terminal = event
            return
        if isinstance(event, ModelRequestPrepared):
            if self.prepared is not None or self.pending_tool is not None:
                raise ResearchValidationError("overlapping request")
            self.validate_request(event.request)
            self.prepared = event.request
            return
        if isinstance(event, ModelCallStarted):
            if (
                self.prepared is None
                or self.started
                or event.call_id != self.prepared.call_id
            ):
                raise ResearchValidationError(
                    "call start lacks unique prepared request"
                )
            prepared_body = self.prepared.prepared_input_json
            if prepared_body is None:
                raise ResearchValidationError("missing prepared body")
            if (
                len(prepared_body.encode("utf-8"))
                > self.header.configuration.max_model_input_bytes
            ):
                raise ResearchValidationError("oversized request was invoked")
            self.started = True
            return
        if isinstance(event, ModelResponseObserved):
            if (
                self.prepared is None
                or not self.started
                or self.observed is not None
                or event.call_id != self.prepared.call_id
            ):
                raise ResearchValidationError("observation lacks unique started call")
            self.observed = event
            return
        if isinstance(event, (ModelCallFinished, ModelRequestRejected)):
            if self.prepared is None or event.record.request != self.prepared:
                raise ResearchValidationError("request/reply pairing mismatch")
            if isinstance(event, ModelCallFinished):
                if not self.started:
                    raise ResearchValidationError("model finish before start")
                validate_observation(
                    self.prepared, self.observed, event.record, self.header
                )
                self.last_call = event.record
                self.calls += 1
                if request_rejection(self.prepared) is None:
                    self.counts[self.prepared.stage] = (
                        self.counts.get(self.prepared.stage, 0) + 1
                    )
            elif (
                self.started
                or event.record.failure.code
                not in ("INPUT_BUDGET_EXHAUSTED", "WORKFLOW_TIMEOUT")
                or event.record.failure.call_id not in (None, self.prepared.call_id)
            ):
                raise ResearchValidationError("invalid prepared request rejection")
            if isinstance(event, ModelRequestRejected):
                body = self.prepared.prepared_input_json
                oversized = (
                    body is not None
                    and len(body.encode("utf-8"))
                    > self.header.configuration.max_model_input_bytes
                )
                if (event.record.failure.code == "INPUT_BUDGET_EXHAUSTED") != oversized:
                    raise ResearchValidationError(
                        "input rejection contradicts prepared body size"
                    )
            self.trace += (event.record,)
            self.prepared, self.observed, self.started = None, None, False
            self.node_work = True
        elif isinstance(event, ToolRequested):
            tools = tuple(r for r in self.trace if isinstance(r, ToolRecord))
            if (
                self.node != "execute_tool"
                or self.node_work
                or self.pending_tool is not None
                or self.last_call is None
                or not isinstance(self.last_call.reply, ToolReply)
                or event.model_call_id != self.last_call.request.call_id
                or event.requests != self.last_call.reply.requests
                or event.attempt_id
                != f"{self.header.research_run_id}/tool/{len(tools) + 1:04d}"
            ):
                raise ResearchValidationError("tool request/model identity mismatch")
            self.pending_tool = event
            return
        elif isinstance(event, ToolResolved):
            pending, record = self.pending_tool, event.record
            if pending is None or (
                record.attempt_id,
                record.model_call_id,
                record.requests,
            ) != (pending.attempt_id, pending.model_call_id, pending.requests):
                raise ResearchValidationError("tool resolution lacks matching request")
            history = tuple(r for r in self.trace if isinstance(r, ToolRecord))
            admitted = sum(r.admitted for r in history) < 8
            bound = (
                bind_tool_request(
                    record.requests,
                    self.header.context.request.opportunity.pair,
                    self.header.context.benchmarks,
                )
                if admitted
                else ToolRejection(
                    code="TOOL_BUDGET_EXHAUSTED",
                    message="another tool request after eight admitted attempts",
                )
            )
            if record.admitted != admitted:
                raise ResearchValidationError("tool admission differs from budget")
            if isinstance(bound, ToolRejection):
                if record.rejection != bound or record.query is not None:
                    raise ResearchValidationError(
                        "tool rejection differs from bound request"
                    )
            else:
                cached = next(
                    (r for r in history if r.query == bound and r.result is not None),
                    None,
                )
                if (
                    record.query != bound
                    or record.rejection is not None
                    or record.cached_from != (cached.attempt_id if cached else None)
                ):
                    raise ResearchValidationError(
                        "tool binding/cache differs from request history"
                    )
            self.trace += (record,)
            self.pending_tool = None
            self.node_work = True
        elif isinstance(event, StageCompleted):
            call = self.last_call
            if (
                call is None
                or self.node != event.value.stage
                or not self.node_work
                or call.request.call_id != event.call_id
                or call.request.stage != event.value.stage
                or not isinstance(call.reply, StructuredReply)
                or event.value.stage in self.stages
            ):
                raise ResearchValidationError("stage lacks producing model reply")
            if (
                validate_stage_output(self.trace, call.reply, type(event.value.output))
                != event.value.output
            ):
                raise ResearchValidationError(
                    "stage output differs from producing reply"
                )
            self.stages[event.value.stage] = event.value
        elif isinstance(event, NodeTransitioned):
            transition = event.record
            forced = self.forced_failure()
            if transition.target != "__end__" and forced is not None:
                raise ResearchValidationError(
                    "workflow advanced after mandatory interruption"
                )
            if transition.target == "__end__":
                reason = transition.termination_reason
                if reason == "COMPLETED":
                    if (
                        self.node != "final_assessment"
                        or "final_assessment" not in self.stages
                        or forced is not None
                    ):
                        raise ResearchValidationError(
                            "completion before all validated stages"
                        )
                elif reason != "WORKFLOW_TIMEOUT" and reason != forced:
                    raise ResearchValidationError(
                        "termination contradicts retained failure cause"
                    )
            if self.prepared is not None or self.pending_tool is not None:
                raise ResearchValidationError("transition with unresolved work")
            if transition.source == "return":
                previous = self.trace[-1] if self.trace else None
                if (
                    not isinstance(previous, NodeTransition)
                    or previous.termination_reason != "COMPLETED"
                    or previous.source != "final_assessment"
                    or self.node != "__end__"
                    or transition.target != "__end__"
                    or transition.termination_reason != "WORKFLOW_TIMEOUT"
                ):
                    raise ResearchValidationError("invalid teardown transition")
            elif self.node == "__end__" or transition.source != self.node:
                raise ResearchValidationError("transition source mismatch")
            elif self.node == "__start__" and transition.target != "investigate":
                raise ResearchValidationError(
                    "initial transition must enter investigation"
                )
            elif transition.target != "__end__":
                reask = self.rejected_call() is not None
                target = (
                    self.node
                    if reask
                    else {
                        "__start__": "investigate",
                        "execute_tool": "investigate",
                        "draft_thesis": "critique",
                        "critique": "final_assessment",
                        "investigate": "draft_thesis"
                        if "investigate" in self.stages
                        else "execute_tool",
                    }.get(self.node)
                )
                if (
                    transition.target != target
                    or transition.termination_reason is not None
                ):
                    raise ResearchValidationError("invalid stage transition")
                if self.node != "__start__" and not self.node_work:
                    raise ResearchValidationError("transition without node work")
                if (
                    self.node in ("draft_thesis", "critique")
                    and self.node not in self.stages
                    and not reask
                ):
                    raise ResearchValidationError("transition without validated stage")
                if (
                    self.node == "investigate"
                    and target == "execute_tool"
                    and (
                        self.last_call is None
                        or not isinstance(self.last_call.reply, ToolReply)
                    )
                ):
                    raise ResearchValidationError("tool transition without tool reply")
            elif transition.termination_reason is None:
                raise ResearchValidationError("termination lacks reason")
            self.trace += (transition,)
            self.node, self.node_work = transition.target, False
        elif isinstance(event, RunFinished):
            if (event.status == "COMPLETED") != (event.assessment_origin == "MODEL"):
                raise ResearchValidationError("terminal status/origin mismatch")
            if event.status == "COMPLETED" and event.failure is not None:
                raise ResearchValidationError("completed run cannot contain failure")
            if (
                self.node != "__end__"
                or self.prepared is not None
                or self.pending_tool is not None
            ):
                raise ResearchValidationError("finish before graph termination")
            last = self.trace[-1]
            reason = event.failure.code.value if event.failure else "COMPLETED"
            if getattr(last, "termination_reason", None) != reason:
                raise ResearchValidationError(
                    "terminal failure differs from graph termination"
                )
            if event.status == "COMPLETED":
                assessment = self.stages.get("final_assessment")
                if (
                    not isinstance(assessment, AssessmentStage)
                    or assessment.output != event.assessment
                ):
                    raise ResearchValidationError(
                        "terminal assessment differs from stage"
                    )
            else:
                failure = event.failure
                if failure is None or event.assessment != TradeDecision(
                    outcome=DecisionOutcome.ABSTAIN,
                    rationale=failure.message,
                    abstention_reasons=(failure.abstention_reason,),
                ):
                    raise ResearchValidationError(
                        "runtime assessment differs from failure"
                    )
                records = self.work
                if failure.code != "WORKFLOW_TIMEOUT":
                    producing = records[-1] if records else None
                    call_id = (
                        producing.model_call_id
                        if isinstance(producing, ToolRecord)
                        else producing.request.call_id
                        if producing is not None
                        else None
                    )
                    if failure.call_id != call_id:
                        raise ResearchValidationError(
                            "failure call identity differs from cause"
                        )
                    if (
                        isinstance(producing, ModelRequestRejection)
                        and failure != producing.failure
                    ):
                        raise ResearchValidationError(
                            "terminal failure differs from request rejection"
                        )
                    if (
                        isinstance(producing, ModelCallRecord)
                        and isinstance(producing.reply, FailureReply)
                        and failure.message != producing.reply.message
                    ):
                        raise ResearchValidationError(
                            "terminal failure differs from provider failure"
                        )
                if failure.call_id is not None and failure.call_id not in {
                    r.request.call_id
                    for r in self.trace
                    if isinstance(r, (ModelCallRecord, ModelRequestRejection))
                }:
                    raise ResearchValidationError("failure refers to absent call")
            investigation = self.stages.get("investigate")
            thesis = self.stages.get("draft_thesis")
            critique = self.stages.get("critique")
            self.result = ResearchRunResult(
                **self.header.model_dump(exclude={"schema_version"}),
                status=event.status,
                assessment_origin=event.assessment_origin,
                trace=self.trace,
                investigation=investigation.output
                if isinstance(investigation, InvestigationStage)
                else None,
                thesis=thesis.output if isinstance(thesis, ThesisStage) else None,
                critique=critique.output
                if isinstance(critique, CritiqueStage)
                else None,
                assessment=event.assessment,
                failure=event.failure,
            )
            self.terminal = event
        else:
            raise ResearchValidationError("unexpected lifecycle event")
        validate_trace_relationships(self.header.context, self.trace)


def project(events: tuple[EventEnvelope, ...]) -> JournalProjection:
    if not events or not isinstance(events[0].event, RunStarted):
        raise ResearchValidationError("journal must begin with RUN_STARTED")
    header = events[0].event.header
    if header.provider.provider != header.configuration.model.provider:
        raise ResearchValidationError("header provider mismatch")
    projection = JournalProjection(header)
    for index, envelope in enumerate(events):
        if (
            envelope.sequence != index
            or envelope.research_run_id != header.research_run_id
        ):
            raise ResearchValidationError("journal sequence/run identity mismatch")
        if index:
            projection.apply(envelope)
    return projection
