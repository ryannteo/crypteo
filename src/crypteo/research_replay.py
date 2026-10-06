"""Sequential fresh/frozen research drivers for the shared historical replay.

Durable artifacts authorize only research selection. Point-in-time tool access
cannot establish historical purity of the model's parametric knowledge.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol

from pydantic import TypeAdapter

from crypteo.coinbase_dataset import CoinbaseCanonicalDataset, CoinbaseHistoricalDataset
from crypteo.domain.replay import Delivery, ReplayResult
from crypteo.domain.replay_research import (
    FreshResearchSettings,
    FrozenResearchSettings,
    ReplayResearchRecord,
    ResearchReplayManifest,
    ResearchSettings,
    fresh_replay_origin,
    opportunity_context_digest,
    research_run_id,
    validate_research_record,
)
from crypteo.domain.research import Opportunity
from crypteo.domain.research_journal import ResearchArtifact
from crypteo.domain.research_workflow import OpportunityResearchRequest
from crypteo.experiment import (
    CodeIdentity,
    ReplayConfig,
    build_research_manifest,
    current_code_identity,
    identity,
)
from crypteo.market_research import MarketResearchService
from crypteo.replay import _inputs, _replay_engine
from crypteo.research_model import ObservableResearchModel
from crypteo.research_persistence import ResearchStore
from crypteo.research_validation import ResearchInvariantError
from crypteo.research_workflow import run_durable_opportunity_research
from crypteo.temporal import PointInTimeDataGateway, SimulationClock

_SETTINGS: TypeAdapter[ResearchSettings] = TypeAdapter(ResearchSettings)


class ResearchReplayError(RuntimeError):
    """Evaluation aborted; never a partial successful replay or model conclusion."""


class ResearchBudgetExceeded(ResearchReplayError):
    """The next candidate was refused before registration or inference."""


class ResearchAdmissionError(ResearchReplayError):
    """Terminal research is incompatible with this evaluation candidate."""


class _ResearchSource(Protocol):
    async def evaluate(self, opportunity: Opportunity) -> ResearchArtifact: ...


class _FreshSource:
    def __init__(
        self,
        manifest: ResearchReplayManifest,
        service: MarketResearchService,
        store: ResearchStore,
        model: ObservableResearchModel,
    ) -> None:
        self.manifest, self.service, self.store, self.model = (
            manifest,
            service,
            store,
            model,
        )
        self.reserved = 0

    async def evaluate(self, opportunity: Opportunity) -> ResearchArtifact:
        settings = self.manifest.research
        assert isinstance(settings, FreshResearchSettings)
        if self.reserved >= settings.max_research_runs:
            raise ResearchBudgetExceeded("max_research_runs exhausted before next run")
        self.reserved += 1
        run_id = research_run_id(self.manifest.run_id, opportunity)
        artifact = await run_durable_opportunity_research(
            OpportunityResearchRequest(research_run_id=run_id, opportunity=opportunity),
            self.service,
            self.model,
            settings.configuration,
            store=self.store,
            code=self.manifest.code,
        )
        # Publication alone is not permission to bypass the durable consumer boundary.
        loaded = self.store.load_terminal(run_id)
        if loaded != artifact:
            raise ResearchAdmissionError("published artifact differs from durable load")
        return loaded


class _FrozenSource:
    def __init__(self, settings: FrozenResearchSettings, store: ResearchStore) -> None:
        self.mapping = {item.opportunity_id: item for item in settings.mapping}
        self.store = store

    async def evaluate(self, opportunity: Opportunity) -> ResearchArtifact:
        mapped = self.mapping.get(opportunity.opportunity_id)
        if mapped is None:
            raise ResearchAdmissionError("missing frozen opportunity mapping")
        return self.store.load_terminal(mapped.research_run_id)


def _admit_artifact(
    artifact: ResearchArtifact,
    opportunity: Opportunity,
    at: datetime,
    manifest: ResearchReplayManifest,
) -> ReplayResearchRecord:
    """Replay association only; the caller must first use store.load_terminal()."""
    result = artifact.result
    context = result.context
    opportunity = Opportunity.model_validate(opportunity)
    retained_opportunity = Opportunity.model_validate(context.request.opportunity)
    candidate_digest = opportunity_context_digest(opportunity)
    retained_digest = opportunity_context_digest(retained_opportunity)
    if candidate_digest != retained_digest:
        raise ResearchAdmissionError("canonical opportunity context digest mismatch")
    if (
        context.request.research_run_id != artifact.research_run_id
        or context.research_at != at
        or opportunity.observed_at != at
        or opportunity.available_at > at
        or context.source_bindings != manifest.source_bindings
        or result.configuration != manifest.research.configuration
    ):
        raise ResearchAdmissionError(
            "research context/time/sources/configuration mismatch"
        )
    if result.status != "COMPLETED" or result.assessment_origin != "MODEL":
        failure = result.failure.code.value if result.failure else "INVALID_TERMINAL"
        raise ResearchAdmissionError(f"research is not COMPLETED/MODEL: {failure}")
    settings = manifest.research
    if isinstance(settings, FreshResearchSettings):
        origin = fresh_replay_origin(manifest)
    else:
        mapped = next(
            (
                item
                for item in settings.mapping
                if item.opportunity_id == opportunity.opportunity_id
            ),
            None,
        )
        if mapped is None or mapped.context_digest != retained_digest:
            raise ResearchAdmissionError("frozen mapping context digest mismatch")
        origin = mapped.origin
    if result.code != origin.code:
        raise ResearchAdmissionError(
            "research artifact code differs from originating replay"
        )
    record = ReplayResearchRecord(
        opportunity_id=opportunity.opportunity_id,
        context_digest=retained_digest,
        research_run_id=artifact.research_run_id,
        artifact_schema_version=artifact.artifact_schema_version,
        artifact_checksum=artifact.artifact_checksum,
        terminal_sequence=artifact.terminal_sequence,
        ordered_journal_digest=artifact.ordered_journal_digest,
        research_at=context.research_at,
        outcome=result.assessment.outcome,
        confidence=result.assessment.confidence,
        policy_digest=identity(manifest.policy),
        origin=origin,
    )
    try:
        validate_research_record(record, opportunity, manifest)
    except ValueError as error:
        raise ResearchAdmissionError(str(error)) from error
    return record


async def run_research_replay(
    config: ReplayConfig,
    datasets: Iterable[CoinbaseCanonicalDataset | CoinbaseHistoricalDataset],
    *,
    research: ResearchSettings,
    store: ResearchStore,
    model: ObservableResearchModel | None = None,
    code: CodeIdentity | None = None,
) -> ReplayResult:
    """Evaluate every admitted candidate at frozen T; failures return no result.

    Fresh calls require an explicit trial, cap, observable model, and durable store.
    Frozen calls forbid a model and require exact mapping coverage. Neither source
    receives portfolio state or controls the replay clock. No automatic retries.
    """
    config = ReplayConfig.model_validate(config.model_dump())
    settings = _SETTINGS.validate_python(research)
    if not isinstance(store, ResearchStore):
        raise ValueError("research replay requires a caller-owned ResearchStore")
    if isinstance(settings, FreshResearchSettings):
        if not isinstance(model, ObservableResearchModel):
            raise ValueError("fresh replay requires an observable model")
        if model.identity.provider != settings.configuration.model.provider:
            raise ValueError("configured provider differs from model adapter")
    elif model is not None:
        raise ValueError("frozen replay forbids a model")
    candles, dataset_ids = _inputs(config.assets, datasets)
    manifest = build_research_manifest(
        config, dataset_ids, code or current_code_identity(), settings
    )
    clock = SimulationClock(config.start)
    gateway = PointInTimeDataGateway(clock, candles)
    source: _ResearchSource
    if isinstance(settings, FreshResearchSettings):
        assert model is not None
        source = _FreshSource(
            manifest,
            MarketResearchService(gateway, manifest.source_bindings),
            store,
            model,
        )
    else:
        source = _FrozenSource(settings, store)
    engine = _replay_engine(manifest, clock, gateway)
    selection: ReplayResearchRecord | None = None
    try:
        while True:
            try:
                opportunity = engine.send(selection)
            except StopIteration as finished:
                result: ReplayResult = finished.value
                return result
            at = gateway.now
            if opportunity.observed_at != at or opportunity.available_at > at:
                raise ResearchInvariantError(
                    "research candidate is not current/available"
                )
            try:
                artifact = await source.evaluate(opportunity)
                selection = _admit_artifact(artifact, opportunity, at, manifest)
                if gateway.now != at:
                    raise ResearchInvariantError("replay clock moved during research")
            except BaseException as error:
                run_id = (
                    research_run_id(manifest.run_id, opportunity)
                    if isinstance(settings, FreshResearchSettings)
                    else next(
                        (
                            item.research_run_id
                            for item in settings.mapping
                            if item.opportunity_id == opportunity.opportunity_id
                        ),
                        "unmapped",
                    )
                )
                error.add_note(
                    f"replay={manifest.run_id}; "
                    f"opportunity={opportunity.opportunity_id}; "
                    f"research_run={run_id}; boundary={at.isoformat()}"
                )
                raise
    finally:
        engine.close()


def validate_research_links(
    result: ReplayResult, *, store: ResearchStore
) -> ReplayResult:
    """Required external validation for evaluation of imported research results.

    Pure ReplayResult parsing checks internal consistency only. This additionally
    resolves journal-backed references, without inference or research market reads.
    """
    result = ReplayResult.model_validate(result.model_dump())
    manifest = result.manifest
    if not isinstance(manifest, ResearchReplayManifest):
        raise ValueError("external research validation requires a research replay")
    opportunities = {
        record.payload.opportunity.opportunity_id: record.payload.opportunity
        for record in result.records
        if isinstance(record.payload, Delivery)
        and record.payload.disposition == "ADMITTED"
    }
    for record in result.records:
        if isinstance(record.payload, ReplayResearchRecord):
            artifact = store.load_terminal(record.payload.research_run_id)
            expected = _admit_artifact(
                artifact,
                opportunities[record.payload.opportunity_id],
                record.at,
                manifest,
            )
            if expected != record.payload:
                raise ResearchAdmissionError(
                    "compact research projection differs from artifact"
                )
    return result


def load_research_replay(encoded: bytes | str, *, store: ResearchStore) -> ReplayResult:
    """Import for evaluation with mandatory durable external linkage validation."""
    return validate_research_links(
        ReplayResult.model_validate_json(encoded), store=store
    )
