"""Immutable research replay settings, admission projections, and provenance.

Point-in-time tools constrain data visibility, not model parametric knowledge.
Frozen artifacts reproduce assessments without establishing historical model purity.
"""

from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator

from crypteo.domain.market import NonNegativeDecimal, TradingPair
from crypteo.domain.market_research import HourlyDatetime, MarketSourceBinding
from crypteo.domain.research import DecisionOutcome, NonBlankText, Opportunity
from crypteo.domain.research_workflow import BENCHMARK_ORDER, ResearchWorkflowConfig
from crypteo.domain.risk import TradeProposal
from crypteo.experiment import (
    SCANNER_POLICY,
    CodeIdentity,
    DatasetIdentity,
    Digest,
    ReplayConfig,
    canonical_bytes,
    identity,
)
from crypteo.research_model import ContentIdentity, ModelContract

_SCANNER_POLICY = SCANNER_POLICY.removesuffix("; valid terms always selected.")


class SubjectToolIdentity(ModelContract):
    pair: TradingPair
    benchmarks: tuple[TradingPair, ...]
    definitions: tuple[ContentIdentity, ...]


class ResearchPolicyIdentity(ModelContract):
    configuration_digest: Digest
    workflow: Literal["bounded_research_v2"] = "bounded_research_v2"
    market_research: Literal["market_research_v1"] = "market_research_v1"
    model_view: Literal["compact_research_view_v1"] = "compact_research_view_v1"
    prompts: tuple[ContentIdentity, ...]
    schemas: tuple[ContentIdentity, ...]
    tools: tuple[SubjectToolIdentity, ...]


def research_policy(configuration: ResearchWorkflowConfig) -> ResearchPolicyIdentity:
    # Pure identities only: no workflow, provider client, or persistence imports.
    from crypteo.research_prompts import prompt_identity, structured_schema
    from crypteo.research_tools import tool_definitions

    stages = ("investigate", "draft_thesis", "critique", "final_assessment")
    offers: list[tuple[TradingPair, tuple[TradingPair, ...]]] = []
    for pair in TradingPair:
        a, b = (p for p in BENCHMARK_ORDER if p != pair)
        offers += [(pair, subset) for subset in ((), (a,), (b,), (a, b))]
    return ResearchPolicyIdentity(
        configuration_digest=identity(configuration),
        prompts=tuple(prompt_identity(stage) for stage in stages),
        schemas=tuple(structured_schema(stage).identity for stage in stages),
        tools=tuple(
            SubjectToolIdentity(
                pair=pair,
                benchmarks=benchmarks,
                definitions=tuple(
                    tool.identity for tool in tool_definitions(pair, benchmarks)
                ),
            )
            for pair, benchmarks in offers
        ),
    )


def dataset_source_bindings(
    datasets: tuple[DatasetIdentity, ...],
) -> tuple[MarketSourceBinding, ...]:
    return tuple(
        MarketSourceBinding(
            pair=item.pair,
            dataset_version=item.dataset_version,
            acquisition_id=item.acquisition_id,
        )
        for item in datasets
    )


def opportunity_context_digest(opportunity: Opportunity) -> str:
    return identity(
        {"purpose": "replay_opportunity_context_v1", "opportunity": opportunity}
    )


def research_run_id(replay_run_id: str, opportunity: Opportunity) -> str:
    return identity(
        {
            "purpose": "replay_research_invocation_v1",
            "replay_run_id": replay_run_id,
            "opportunity_id": opportunity.opportunity_id,
            "context_digest": opportunity_context_digest(opportunity),
        }
    )


class FreshReplayOrigin(ModelContract):
    """Enough fresh inputs to reconstruct its manifest from a compatible replay.

    The replay ID commits to the full originating manifest. Trial and cap restore
    the fresh settings in place of the frozen mapping; code is also checked against
    the artifact's producer. No research output enters that originating identity.
    """

    replay_run_id: Digest
    code: CodeIdentity
    trial_id: NonBlankText
    max_research_runs: int = Field(strict=True, gt=0)


class FrozenResearchMapping(ModelContract):
    opportunity_id: NonBlankText
    research_run_id: NonBlankText
    artifact_checksum: Digest
    context_digest: Digest
    origin: FreshReplayOrigin


class FreshResearchSettings(ModelContract):
    mode: Literal["FRESH_INFERENCE"] = "FRESH_INFERENCE"
    configuration: ResearchWorkflowConfig
    trial_id: NonBlankText
    max_research_runs: int = Field(strict=True, gt=0)


class FrozenResearchSettings(ModelContract):
    mode: Literal["FROZEN_RESEARCH"] = "FROZEN_RESEARCH"
    configuration: ResearchWorkflowConfig
    mapping: tuple[FrozenResearchMapping, ...]

    @field_validator("mapping")
    @classmethod
    def canonical_mapping(
        cls, value: tuple[FrozenResearchMapping, ...]
    ) -> tuple[FrozenResearchMapping, ...]:
        return tuple(sorted(value, key=lambda item: item.opportunity_id))

    @model_validator(mode="after")
    def validate_mapping(self) -> Self:
        for values in (
            [item.opportunity_id for item in self.mapping],
            [item.research_run_id for item in self.mapping],
        ):
            if len(values) != len(set(values)):
                raise ValueError(
                    "frozen mapping must have unique opportunities and runs"
                )
        return self


type ResearchSettings = Annotated[
    FreshResearchSettings | FrozenResearchSettings, Field(discriminator="mode")
]


class ResearchReplayManifest(ModelContract):
    """Research inputs only; fresh output artifact hashes never enter run identity."""

    schema_version: Literal["2"] = "2"
    experiment_mode: Literal["RESEARCH"] = "RESEARCH"
    code: CodeIdentity
    python_version: NonBlankText
    pydantic_version: NonBlankText
    replay: ReplayConfig
    datasets: tuple[DatasetIdentity, ...]
    source_bindings: tuple[MarketSourceBinding, ...]
    research: ResearchSettings
    policy: ResearchPolicyIdentity
    scanner_policy: str = _SCANNER_POLICY
    trade_terms_policy: Literal["mean_true_range20_stop2_sma20_target_floor_v1"] = (
        "mean_true_range20_stop2_sma20_target_floor_v1"
    )
    risk_policy: Literal["decision_time_long_risk_v1"] = "decision_time_long_risk_v1"
    execution_policy: Literal["completed_bar_reconciliation_v1"] = (
        "completed_bar_reconciliation_v1"
    )
    admission_policy: Literal["completed_model_research_first_v1"] = (
        "completed_model_research_first_v1"
    )
    failure_policy: Literal["abort_on_research_failure_v1"] = (
        "abort_on_research_failure_v1"
    )

    @model_validator(mode="after")
    def validate_inputs(self) -> Self:
        if tuple(item.pair for item in self.datasets) != self.replay.assets:
            raise ValueError("dataset identities must match canonical ordered assets")
        if self.source_bindings != dataset_source_bindings(self.datasets):
            raise ValueError("research sources must match replay datasets exactly")
        if self.scanner_policy != _SCANNER_POLICY:
            raise ValueError("unsupported scanner policy")
        if self.policy != research_policy(self.research.configuration):
            raise ValueError("unsupported research policy/configuration identities")
        if isinstance(self.research, FrozenResearchSettings):
            for mapped in self.research.mapping:
                validate_replay_origin(mapped.origin, self)
        return self

    @property
    def run_id(self) -> str:
        return identity(self)

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self)


def fresh_replay_origin(manifest: ResearchReplayManifest) -> FreshReplayOrigin:
    settings = manifest.research
    if not isinstance(settings, FreshResearchSettings):
        raise ValueError("origin allocation requires a fresh replay manifest")
    return FreshReplayOrigin(
        replay_run_id=manifest.run_id,
        code=manifest.code,
        trial_id=settings.trial_id,
        max_research_runs=settings.max_research_runs,
    )


def validate_replay_origin(
    origin: FreshReplayOrigin, manifest: ResearchReplayManifest
) -> None:
    """Recompute the originating fresh identity using this replay's locked inputs."""
    if origin.code != manifest.code:
        raise ValueError("originating replay CodeIdentity mismatch")
    original_inputs = manifest.model_dump() | {
        "research": FreshResearchSettings(
            configuration=manifest.research.configuration,
            trial_id=origin.trial_id,
            max_research_runs=origin.max_research_runs,
        ).model_dump()
    }
    if origin.replay_run_id != identity(original_inputs):
        raise ValueError("originating replay configuration/manifest mismatch")


class ReplayResearchRecord(ModelContract):
    opportunity_id: NonBlankText
    context_digest: Digest
    research_run_id: NonBlankText
    artifact_schema_version: Literal["1"] = "1"
    artifact_checksum: Digest
    terminal_sequence: int = Field(strict=True, ge=0)
    ordered_journal_digest: Digest
    research_at: HourlyDatetime
    status: Literal["COMPLETED"] = "COMPLETED"
    assessment_origin: Literal["MODEL"] = "MODEL"
    outcome: DecisionOutcome
    confidence: int | None = Field(default=None, strict=True, ge=0, le=100)
    policy_digest: Digest
    origin: FreshReplayOrigin

    @model_validator(mode="after")
    def validate_confidence(self) -> Self:
        if self.outcome != DecisionOutcome.ABSTAIN and self.confidence is None:
            raise ValueError("TRADE and NO_TRADE require model confidence")
        return self


class ResearchSelection(ModelContract):
    """Deterministic proposal following an admitted model TRADE."""

    proposal: TradeProposal


class RiskRejectionCount(ModelContract):
    reason: NonBlankText
    count: int = Field(strict=True, gt=0)


class ResearchReplayMetrics(ModelContract):
    opportunities_assessed: int = Field(strict=True, ge=0)
    trade_count: int = Field(strict=True, ge=0)
    no_trade_count: int = Field(strict=True, ge=0)
    abstain_count: int = Field(strict=True, ge=0)
    terms_rejection_count: int = Field(strict=True, ge=0)
    risk_rejection_count: int = Field(strict=True, ge=0)
    risk_rejection_reasons: tuple[RiskRejectionCount, ...]
    submission_count: int = Field(strict=True, ge=0)
    entry_count: int = Field(strict=True, ge=0)
    research_to_entry_conversion: NonNegativeDecimal | None
    trade_to_entry_conversion: NonNegativeDecimal | None


def validate_research_record(
    record: ReplayResearchRecord,
    opportunity: Opportunity,
    manifest: ResearchReplayManifest,
) -> None:
    """Pure association checks; external artifact existence needs the store loader."""
    if (
        record.opportunity_id != opportunity.opportunity_id
        or record.context_digest != opportunity_context_digest(opportunity)
        or record.research_at != opportunity.observed_at
        or opportunity.available_at > record.research_at
        or record.policy_digest != identity(manifest.policy)
    ):
        raise ValueError("research record differs from opportunity/time/policy")
    settings = manifest.research
    if isinstance(settings, FreshResearchSettings):
        if record.origin != fresh_replay_origin(manifest):
            raise ValueError("research origin differs from current fresh replay")
    else:
        validate_replay_origin(record.origin, manifest)
        mapped = next(
            (
                m
                for m in settings.mapping
                if m.opportunity_id == opportunity.opportunity_id
            ),
            None,
        )
        if mapped is None or (
            mapped.research_run_id != record.research_run_id
            or mapped.context_digest != record.context_digest
            or mapped.artifact_checksum != record.artifact_checksum
            or mapped.origin != record.origin
        ):
            raise ValueError("research record differs from frozen mapping")
    if record.research_run_id != research_run_id(
        record.origin.replay_run_id, opportunity
    ):
        raise ValueError("research run ID differs from originating replay invocation")
