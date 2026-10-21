"""Closed, versioned research lifecycle contracts; no storage or execution."""

from typing import Annotated, Literal

from pydantic import Field

from crypteo.domain.market_research import Digest
from crypteo.domain.research import (
    Critique,
    NonBlankText,
    ResearchThesis,
    TradeDecision,
)
from crypteo.domain.research_workflow import (
    AssessmentOrigin,
    InvestigationCompletion,
    ModelCallRecord,
    ModelRequestRejection,
    NodeTransition,
    ResearchContext,
    ResearchFailure,
    ResearchRunResult,
    ResearchRunStatus,
    ResearchWorkflowConfig,
    ToolRecord,
)
from crypteo.experiment import CodeIdentity
from crypteo.research_model import (
    ModelContract,
    ModelRequest,
    ProviderIdentity,
    ToolRequest,
)

Sequence = Annotated[int, Field(strict=True, ge=0)]
Stage = Literal["investigate", "draft_thesis", "critique", "final_assessment"]


class ResearchRunHeader(ModelContract):
    schema_version: Literal["1"] = "1"
    context: ResearchContext
    configuration: ResearchWorkflowConfig
    code: CodeIdentity
    provider: ProviderIdentity
    langgraph_version: NonBlankText

    @property
    def research_run_id(self) -> str:
        return self.context.request.research_run_id


class RunStarted(ModelContract):
    kind: Literal["RUN_STARTED"] = "RUN_STARTED"
    header: ResearchRunHeader


class ModelRequestPrepared(ModelContract):
    kind: Literal["MODEL_REQUEST_PREPARED"] = "MODEL_REQUEST_PREPARED"
    request: ModelRequest


class ModelRequestRejected(ModelContract):
    kind: Literal["MODEL_REQUEST_REJECTED"] = "MODEL_REQUEST_REJECTED"
    record: ModelRequestRejection


class ModelCallStarted(ModelContract):
    kind: Literal["MODEL_CALL_STARTED"] = "MODEL_CALL_STARTED"
    call_id: NonBlankText


class ModelResponseObserved(ModelContract):
    kind: Literal["MODEL_RESPONSE_OBSERVED"] = "MODEL_RESPONSE_OBSERVED"
    call_id: NonBlankText
    # Bytes are stored separately as a BLOB, included in the event checksum.
    body: bytes = Field(strict=True, exclude=True)
    http_status: Annotated[int, Field(strict=True, ge=100, le=599)]
    request_id: NonBlankText | None = None
    text_encoding: NonBlankText = "utf-8"


class ModelCallFinished(ModelContract):
    kind: Literal["MODEL_CALL_FINISHED"] = "MODEL_CALL_FINISHED"
    record: ModelCallRecord


class ToolRequested(ModelContract):
    kind: Literal["TOOL_REQUESTED"] = "TOOL_REQUESTED"
    attempt_id: NonBlankText
    model_call_id: NonBlankText
    requests: tuple[ToolRequest, ...]


class ToolResolved(ModelContract):
    kind: Literal["TOOL_RESOLVED"] = "TOOL_RESOLVED"
    record: ToolRecord


class InvestigationStage(ModelContract):
    stage: Literal["investigate"] = "investigate"
    output: InvestigationCompletion


class ThesisStage(ModelContract):
    stage: Literal["draft_thesis"] = "draft_thesis"
    output: ResearchThesis


class CritiqueStage(ModelContract):
    stage: Literal["critique"] = "critique"
    output: Critique


class AssessmentStage(ModelContract):
    stage: Literal["final_assessment"] = "final_assessment"
    output: TradeDecision


StageOutput = Annotated[
    InvestigationStage | ThesisStage | CritiqueStage | AssessmentStage,
    Field(discriminator="stage"),
]


class StageCompleted(ModelContract):
    kind: Literal["STAGE_COMPLETED"] = "STAGE_COMPLETED"
    call_id: NonBlankText
    value: StageOutput


class NodeTransitioned(ModelContract):
    kind: Literal["NODE_TRANSITION"] = "NODE_TRANSITION"
    record: NodeTransition


class RunFinished(ModelContract):
    kind: Literal["RUN_FINISHED"] = "RUN_FINISHED"
    status: ResearchRunStatus
    assessment_origin: AssessmentOrigin
    assessment: TradeDecision
    failure: ResearchFailure | None = None


class AbortDiagnostic(ModelContract):
    # Deliberately no arbitrary exception text or dumps.
    reason: Literal["EXTERNAL_CANCELLATION", "INVARIANT_OR_PROGRAMMER_FAILURE"]


class RunAborted(ModelContract):
    kind: Literal["RUN_ABORTED"] = "RUN_ABORTED"
    diagnostic: AbortDiagnostic


LifecycleEvent = Annotated[
    RunStarted
    | ModelRequestPrepared
    | ModelRequestRejected
    | ModelCallStarted
    | ModelResponseObserved
    | ModelCallFinished
    | ToolRequested
    | ToolResolved
    | StageCompleted
    | NodeTransitioned
    | RunFinished
    | RunAborted,
    Field(discriminator="kind"),
]


class EventEnvelope(ModelContract):
    schema_version: Literal["1"] = "1"
    research_run_id: NonBlankText
    sequence: Sequence
    event: LifecycleEvent


class EventReceipt(ModelContract):
    research_run_id: NonBlankText
    sequence: Sequence
    checksum: Digest


class ResearchArtifact(ModelContract):
    artifact_schema_version: Literal["1"] = "1"
    research_run_id: NonBlankText
    terminal_sequence: Sequence
    ordered_journal_digest: Digest
    result: ResearchRunResult
    artifact_checksum: Digest


class ResearchRunInspection(ModelContract):
    header: ResearchRunHeader
    state: Literal["INCOMPLETE", "COMPLETED", "INTERRUPTED", "ABORTED"]
    events: tuple[EventEnvelope, ...]
    receipts: tuple[EventReceipt, ...]
    pending_boundaries: tuple[NonBlankText, ...]
    artifact: ResearchArtifact | None = None
