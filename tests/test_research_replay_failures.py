"""Fail-closed research evaluation and adversarial temporal/artifact boundaries."""

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from research_support import tool
from test_replay import (
    BTC,
    CODE,
    ETH,
    H,
    T,
    bar,
    config,
    dataset,
    history,
    payloads,
)
from test_research_replay import WORKFLOW, ReplayModel, execute, fresh, frozen, inputs

from crypteo.domain.market_research import MarketSourceBinding
from crypteo.domain.replay import Delivery, ReplayResult
from crypteo.domain.replay_research import (
    FrozenResearchMapping,
    FrozenResearchSettings,
    ReplayResearchRecord,
    ResearchReplayManifest,
    fresh_replay_origin,
    opportunity_context_digest,
)
from crypteo.domain.research import Opportunity
from crypteo.domain.research_journal import (
    AbortDiagnostic,
    ResearchArtifact,
    ResearchRunHeader,
)
from crypteo.domain.research_workflow import (
    OpportunityResearchRequest,
    ResearchWorkflowConfig,
)
from crypteo.domain.risk import RiskAuthorization
from crypteo.domain.trade_terms import TradeTerms
from crypteo.experiment import build_research_manifest
from crypteo.market_research import MarketResearchService
from crypteo.replay import _inputs
from crypteo.research_journal import ResearchStorageError
from crypteo.research_model import (
    FailureReply,
    ModelReply,
    ModelRequest,
    ProviderFailureCode,
    RefusalReply,
    ResponseObserver,
    StructuredReply,
)
from crypteo.research_persistence import ResearchJournal, ResearchStore
from crypteo.research_replay import ResearchAdmissionError, run_research_replay
from crypteo.research_validation import ResearchInvariantError
from crypteo.research_workflow import run_durable_opportunity_research
from crypteo.temporal import PointInTimeDataGateway, SimulationClock
from crypteo.trade_terms import TradeTermsService


@pytest.mark.parametrize(
    "reply",
    [
        FailureReply(code=ProviderFailureCode.NETWORK, message="offline"),
        FailureReply(code=ProviderFailureCode.TIMEOUT, message="deadline"),
        RefusalReply(refusal="refused"),
        StructuredReply(content="not-json"),
    ],
)
def test_provider_interruption_aborts_and_remains_inspectable(
    tmp_path: Path,
    reply: ModelReply,
) -> None:
    class FailedModel(ReplayModel):
        async def invoke_observed(
            self, request: ModelRequest, observe: ResponseObserver
        ) -> ModelReply:
            self.requests.append(request)
            return reply

    store = ResearchStore(tmp_path / "r.sqlite")
    model = FailedModel()
    with pytest.raises(ResearchAdmissionError, match="not COMPLETED/MODEL"):
        execute(
            store,
            cfg=config(assets=(BTC, ETH), end=T + H),
            datasets=[dataset(pair=p) for p in (BTC, ETH)],
            model=model,
        )
    # Only an invalid structured reply is re-asked, once; it fails again.
    assert len(model.requests) == (2 if isinstance(reply, StructuredReply) else 1)
    with sqlite3.connect(store.path) as conn:
        run_ids = conn.execute(
            "SELECT research_run_id FROM research_runs_v1"
        ).fetchall()
    assert len(run_ids) == 1
    artifact = store.load_terminal(run_ids[0][0])
    assert artifact.result.status == "INTERRUPTED"
    assert artifact.result.assessment_origin == "RUNTIME"
    assert artifact.result.assessment.outcome == "ABSTAIN"
    opportunity = artifact.result.context.request.opportunity
    cfg = config(assets=(BTC, ETH), end=T + H)
    _, dataset_ids = _inputs(cfg.assets, [dataset(pair=p) for p in (BTC, ETH)])
    origin = fresh_replay_origin(
        build_research_manifest(cfg, dataset_ids, CODE, fresh())
    )
    settings = FrozenResearchSettings(
        configuration=WORKFLOW,
        mapping=(
            FrozenResearchMapping(
                opportunity_id=opportunity.opportunity_id,
                research_run_id=artifact.research_run_id,
                artifact_checksum=artifact.artifact_checksum,
                context_digest=opportunity_context_digest(opportunity),
                origin=origin,
            ),
        ),
    )
    with pytest.raises(ResearchAdmissionError, match="not COMPLETED/MODEL"):
        execute(
            store,
            cfg=config(assets=(BTC, ETH), end=T + H),
            datasets=[dataset(pair=p) for p in (BTC, ETH)],
            settings=settings,
        )


@pytest.mark.parametrize("point", ["create_run", "append", "finalize", "load_terminal"])
def test_storage_errors_propagate_without_later_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    point: str,
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    model = ReplayModel()
    failure = ResearchStorageError("injected storage failure")

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise failure

    monkeypatch.setattr(
        ResearchStore if point in ("create_run", "load_terminal") else ResearchJournal,
        point,
        fail,
    )
    with pytest.raises(ResearchStorageError) as caught:
        execute(
            store,
            cfg=config(assets=(BTC, ETH), end=T + H),
            datasets=[dataset(pair=p) for p in (BTC, ETH)],
            model=model,
        )
    assert caught.value is failure
    assert len(model.requests) == (5 if point in ("finalize", "load_terminal") else 0)


def test_uncertain_terminal_publication_never_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    original = ResearchJournal.finalize
    artifacts: list[ResearchArtifact] = []

    def lost_ack(self: ResearchJournal, result: Any) -> ResearchArtifact:
        artifacts.append(original(self, result))
        raise ResearchStorageError("lost acknowledgement")

    monkeypatch.setattr(ResearchJournal, "finalize", lost_ack)
    model = ReplayModel()
    with pytest.raises(ResearchStorageError, match="acknowledgement"):
        execute(store, model=model)
    assert len(artifacts) == 1
    assert store.load_terminal(artifacts[0].research_run_id) == artifacts[0]
    assert len(model.requests) == 5


@pytest.mark.parametrize(
    "error", [ArithmeticError("programmer"), ResearchInvariantError("invariant")]
)
def test_invariant_and_cancellation_preserve_original_exception(
    tmp_path: Path, error: BaseException
) -> None:
    class FailedModel(ReplayModel):
        async def invoke_observed(
            self, request: ModelRequest, observe: ResponseObserver
        ) -> ModelReply:
            self.requests.append(request)
            raise error

    store = ResearchStore(tmp_path / "r.sqlite")
    model = FailedModel()
    with pytest.raises(type(error)) as caught:
        execute(store, model=model)
    assert caught.value is error
    assert len(model.requests) == 1
    run_id = model.requests[0].call_id.rsplit("/model/", 1)[0]
    assert store.inspect_run(run_id).state == "ABORTED"


@pytest.mark.parametrize("when", ["inference", "publication", "loading"])
def test_clock_movement_never_reaches_financial_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    when: str,
) -> None:
    clocks: list[SimulationClock] = []
    original_init = SimulationClock.__init__
    original_finalize = ResearchJournal.finalize
    original_load = ResearchStore.load_terminal

    def initialize(self: SimulationClock, initial_time: Any) -> None:
        clocks.append(self)
        original_init(self, initial_time)

    def finalize(self: ResearchJournal, result: Any) -> ResearchArtifact:
        artifact = original_finalize(self, result)
        if when == "publication":
            clocks[0].advance_to(T + H)
        return artifact

    def load(self: ResearchStore, run_id: str) -> ResearchArtifact:
        artifact = original_load(self, run_id)
        if when == "loading":
            clocks[0].advance_to(T + H)
        return artifact

    def hook(request: ModelRequest) -> None:
        assert clocks[0].now == T
        if when == "inference":
            clocks[0].advance_to(T + H)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("clock movement reached terms")

    monkeypatch.setattr(SimulationClock, "__init__", initialize)
    monkeypatch.setattr(ResearchJournal, "finalize", finalize)
    monkeypatch.setattr(ResearchStore, "load_terminal", load)
    monkeypatch.setattr(TradeTermsService, "generate", forbidden)
    with pytest.raises(ResearchInvariantError, match="clock moved"):
        execute(ResearchStore(tmp_path / "r.sqlite"), model=ReplayModel(hook=hook))
    assert len(clocks) == 1


def test_future_candles_cannot_change_evidence_terms_or_sizing(tmp_path: Path) -> None:
    results: list[ReplayResult] = []
    evidence: list[Any] = []
    for index, price in enumerate(("90", "999999")):
        store = ResearchStore(tmp_path / f"{index}.sqlite")
        result = execute(
            store,
            cfg=config(end=T + H),
            datasets=[dataset(history() + (bar(0, price),))],
        )
        results.append(result)
        artifact = store.load_terminal(
            payloads(result, ReplayResearchRecord)[0].research_run_id
        )
        data = json.loads(
            next(
                r.request.messages[-1].content
                for r in artifact.result.trace
                if r.kind == "model" and r.request.stage == "draft_thesis"
            )
        )
        item = data["research_data"]["evidence"][0]
        evidence.append(
            (
                item["evidence_id"],
                item["payload"],
                item["observed_at"],
                item["available_at"],
            )
        )
    assert evidence[0] == evidence[1]
    assert payloads(results[0], TradeTerms) == payloads(results[1], TradeTerms)
    for result in results:
        auth = payloads(result, RiskAuthorization)[0]
        assert auth.reference_price == 90
        assert auth.authorized_quantity == 4
    assert results[1].final_portfolio.rejections[0].reason == "UNAFFORDABLE"


def test_delayed_research_history_is_unavailable_at_t(tmp_path: Path) -> None:
    class DelayedToolModel(ReplayModel):
        async def invoke_observed(
            self, request: ModelRequest, observe: ResponseObserver
        ) -> ModelReply:
            data = json.loads(request.messages[-1].content)["research_data"]
            if request.stage == "investigate" and not data["request_history"]:
                self.requests.append(request)
                return tool("trend")
            return await super().invoke_observed(request, observe)

    candles = tuple(bar(i) for i in range(-60, -1)) + (bar(-1, "90"), bar(0, "90"))
    candles = tuple(
        c.model_copy(update={"available_at": T + H}) if c.open_time == T - 40 * H else c
        for c in candles
    )
    store = ResearchStore(tmp_path / "r.sqlite")
    model = DelayedToolModel({BTC.value: "ABSTAIN"})
    result = execute(
        store, cfg=config(end=T + H), datasets=[dataset(candles)], model=model
    )
    record = payloads(result, ReplayResearchRecord)[0]
    artifact = store.load_terminal(record.research_run_id)
    tools = [r for r in artifact.result.trace if r.kind == "tool"]
    assert len(tools) == 1 and tools[0].result is not None
    assert tools[0].result.status == "unavailable"
    assert tools[0].result.reason == "HISTORY_GAP"


@pytest.mark.parametrize(
    "arguments",
    [{"cutoff": "2099-01-01"}, {"window": 1}, {"pair": "SOL-USD"}, {"venue": "other"}],
)
def test_model_time_subject_overrides_abort_without_tool_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: dict[str, Any],
) -> None:
    class OverrideModel(ReplayModel):
        async def invoke_observed(
            self, request: ModelRequest, observe: ResponseObserver
        ) -> ModelReply:
            self.requests.append(request)
            return tool("trend", arguments)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("invalid arguments reached research data")

    monkeypatch.setattr(MarketResearchService, "trend", forbidden)
    with pytest.raises(ResearchAdmissionError, match="INVALID_TOOL_REQUEST"):
        execute(ResearchStore(tmp_path / "r.sqlite"), model=OverrideModel())


@pytest.mark.parametrize(
    "change",
    ["summary", "payload", "trigger", "pair", "time", "source", "configuration"],
)
def test_valid_but_incompatible_artifact_rejected(tmp_path: Path, change: str) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    captured = execute(store)
    assert isinstance(captured.manifest, ResearchReplayManifest)
    original = payloads(captured, Delivery)[0].opportunity
    op_data = original.model_dump()
    bindings = captured.manifest.source_bindings
    configuration = WORKFLOW
    if change == "summary":
        op_data["summary"] = "Different research context"
    elif change == "payload":
        op_data["trigger_payload"] = {"different": "context"}
    elif change == "trigger":
        op_data["trigger_type"] = "another_trigger_v1"
    elif change == "pair":
        op_data["pair"] = ETH
    elif change == "time":
        op_data["observed_at"] = op_data["available_at"] = T - H
    elif change == "source":
        bindings = (
            MarketSourceBinding(pair=BTC, dataset_version="sha256:" + "0" * 64),
        )
    else:
        configuration = ResearchWorkflowConfig(
            model=WORKFLOW.model.model_copy(update={"model": "different-model"})
        )
    opportunity = Opportunity.model_validate(op_data)
    svc = MarketResearchService(
        PointInTimeDataGateway(
            SimulationClock(opportunity.observed_at), inputs()[0].candles
        ),
        bindings,
    )
    artifact = asyncio.run(
        run_durable_opportunity_research(
            OpportunityResearchRequest(
                research_run_id="incompatible", opportunity=opportunity
            ),
            svc,
            ReplayModel({BTC.value: "ABSTAIN", ETH.value: "ABSTAIN"}),
            configuration,
            store=store,
            code=CODE,
        )
    )
    assert artifact.result.status == "COMPLETED"
    settings = FrozenResearchSettings(
        configuration=WORKFLOW,
        mapping=(
            FrozenResearchMapping(
                opportunity_id=original.opportunity_id,
                context_digest=opportunity_context_digest(original),
                origin=fresh_replay_origin(captured.manifest),
                research_run_id=artifact.research_run_id,
                artifact_checksum=artifact.artifact_checksum,
            ),
        ),
    )
    with pytest.raises(ResearchAdmissionError, match="mismatch"):
        execute(store, settings=settings)


@pytest.mark.parametrize("terminal", ["INCOMPLETE", "ABORTED"])
def test_frozen_nonterminal_history_rejected(tmp_path: Path, terminal: str) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    captured = execute(store)
    record = payloads(captured, ReplayResearchRecord)[0]
    inspection = store.inspect_run(record.research_run_id)
    data = inspection.header.model_dump()
    data["context"]["request"]["research_run_id"] = "unfinished"
    writer = store.create_run(ResearchRunHeader.model_validate(data))
    if terminal == "ABORTED":
        writer.abort(AbortDiagnostic(reason="EXTERNAL_CANCELLATION"))
    settings = frozen(captured)
    mapping = settings.mapping[0].model_copy(update={"research_run_id": "unfinished"})
    with pytest.raises(ResearchStorageError, match="no terminal"):
        execute(
            store,
            settings=FrozenResearchSettings(configuration=WORKFLOW, mapping=(mapping,)),
        )


def test_frozen_tampered_journal_rejected(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    settings = frozen(execute(store))
    with sqlite3.connect(store.path) as conn:
        trigger = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='research_events_v1_update'"
        ).fetchone()[0]
        conn.execute("DROP TRIGGER research_events_v1_update")
        conn.execute(
            "UPDATE research_events_v1 SET checksum=? WHERE sequence=0",
            ("sha256:" + "0" * 64,),
        )
        conn.execute(trigger)
    with pytest.raises(ResearchStorageError, match="checksum"):
        execute(store, settings=settings)


def test_external_task_cancellation_propagates_and_closes_research(
    tmp_path: Path,
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    model = ReplayModel()

    async def scenario() -> None:
        started = asyncio.Event()

        class WaitingModel(ReplayModel):
            async def invoke_observed(
                self, request: ModelRequest, observe: ResponseObserver
            ) -> ModelReply:
                model.requests.append(request)
                started.set()
                await asyncio.Event().wait()
                raise AssertionError("cancelled provider resumed")

        task = asyncio.create_task(
            run_research_replay(
                config(),
                inputs(),
                research=fresh(),
                store=store,
                model=WaitingModel(),
                code=CODE,
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert len(model.requests) == 1
    run_id = model.requests[0].call_id.rsplit("/model/", 1)[0]
    inspection = store.inspect_run(run_id)
    assert inspection.state == "ABORTED"
    assert inspection.artifact is None
