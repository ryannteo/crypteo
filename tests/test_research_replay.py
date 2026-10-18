"""Model policy integration over the real replay, journal, and financial substrate."""

import asyncio
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from research_support import ScriptedModel, tool, workflow_config
from test_replay import (
    BTC,
    CODE,
    ETH,
    SOL,
    D,
    H,
    T,
    bar,
    config,
    dataset,
    history,
    payloads,
)

import crypteo.research_replay as integration
from crypteo.coinbase_dataset import CoinbaseCanonicalDataset
from crypteo.domain.execution import OrderIntent, PortfolioSnapshot
from crypteo.domain.market import TradingPair
from crypteo.domain.replay import Delivery, ReplayResult
from crypteo.domain.replay_research import (
    FreshResearchSettings,
    FrozenResearchMapping,
    FrozenResearchSettings,
    ReplayResearchRecord,
    opportunity_context_digest,
    research_run_id,
)
from crypteo.domain.research import Opportunity
from crypteo.domain.research_workflow import ModelCallRecord
from crypteo.domain.risk import RiskRejection, TradeProposal
from crypteo.domain.trade_terms import TradeTerms
from crypteo.execution import SimulatedBroker
from crypteo.experiment import ReplayConfig
from crypteo.market_research import MarketResearchService
from crypteo.replay import run_scanner_only
from crypteo.research_journal import ResearchStorageError
from crypteo.research_model import (
    ModelReply,
    ModelRequest,
    ResponseObserver,
)
from crypteo.research_persistence import ResearchStore
from crypteo.research_replay import (
    ResearchAdmissionError,
    ResearchBudgetExceeded,
    load_research_replay,
    run_research_replay,
)
from crypteo.risk import RiskService
from crypteo.scanner import OpportunityScanner
from crypteo.temporal import PointInTimeDataGateway
from crypteo.trade_terms import TradeTermsService

WORKFLOW = workflow_config()


class ReplayModel(ScriptedModel):
    def __init__(
        self,
        outcomes: dict[str, str] | None = None,
        hook: Callable[[ModelRequest], None] | None = None,
    ) -> None:
        super().__init__()
        self.outcomes = outcomes or {}
        self.hook = hook

    async def invoke_observed(
        self, request: ModelRequest, observe: ResponseObserver
    ) -> ModelReply:
        await asyncio.sleep(0)
        if self.hook is not None:
            self.hook(request)
        data = json.loads(request.messages[-1].content)["research_data"]
        pair = data["context"]["request"]["opportunity"]["pair"]
        self.outcome = self.outcomes.get(pair, "TRADE")
        if request.stage == "investigate" and not data["request_history"]:
            self.requests.append(request)
            return tool("bollinger_position")
        return await self.invoke(request)


def fresh(trial: str = "trial-1", cap: int = 3) -> FreshResearchSettings:
    return FreshResearchSettings(
        configuration=WORKFLOW, trial_id=trial, max_research_runs=cap
    )


def inputs() -> list[CoinbaseCanonicalDataset]:
    return [dataset(history() + (bar(0, "90"), bar(1, "95"), bar(2, "96")))]


def execute(
    store: ResearchStore,
    *,
    model: ReplayModel | None = None,
    settings: FreshResearchSettings | FrozenResearchSettings | None = None,
    cfg: ReplayConfig | None = None,
    datasets: list[CoinbaseCanonicalDataset] | None = None,
) -> ReplayResult:
    settings = settings or fresh()
    return asyncio.run(
        run_research_replay(
            cfg or config(),
            datasets if datasets is not None else inputs(),
            research=settings,
            store=store,
            model=(model or ReplayModel())
            if isinstance(settings, FreshResearchSettings)
            else model,
            code=CODE,
        )
    )


def frozen(result: ReplayResult) -> FrozenResearchSettings:
    return FrozenResearchSettings(
        configuration=WORKFLOW,
        mapping=tuple(
            FrozenResearchMapping(
                opportunity_id=item.opportunity_id,
                research_run_id=item.research_run_id,
                artifact_checksum=item.artifact_checksum,
                context_digest=item.context_digest,
                origin=item.origin,
            )
            for item in payloads(result, ReplayResearchRecord)
        ),
    )


def economics(result: ReplayResult) -> dict[str, Any]:
    def strip_ids(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: strip_ids(item) for key, item in value.items() if key != "order_id"
            }
        if isinstance(value, (tuple, list)):
            return [strip_ids(item) for item in value]
        return value

    return {
        "portfolio": strip_ids(result.final_portfolio.model_dump()),
        "metrics": result.metrics.model_dump(),
    }


@pytest.mark.parametrize("outcome", ["TRADE", "NO_TRADE", "ABSTAIN"])
def test_real_durable_outcomes_and_metrics(tmp_path: Path, outcome: str) -> None:
    store = ResearchStore(tmp_path / "research.sqlite")
    model = ReplayModel({BTC.value: outcome})
    result = execute(store, model=model)
    records = payloads(result, ReplayResearchRecord)
    assert len(records) == 1
    record = records[0]
    artifact = store.load_terminal(record.research_run_id)
    assert artifact.result.assessment.outcome == outcome
    assert record.artifact_checksum == artifact.artifact_checksum
    assert record.research_at == T
    assert artifact.result.code == CODE
    assert len(model.requests) == 5
    metrics = result.research_metrics
    assert metrics is not None
    assert metrics.opportunities_assessed == 1
    assert (metrics.trade_count, metrics.no_trade_count, metrics.abstain_count) == (
        int(outcome == "TRADE"),
        int(outcome == "NO_TRADE"),
        int(outcome == "ABSTAIN"),
    )
    assert metrics.entry_count == metrics.submission_count == int(outcome == "TRADE")
    assert metrics.terms_rejection_count == metrics.risk_rejection_count == 0
    assert metrics.risk_rejection_reasons == ()
    assert metrics.research_to_entry_conversion == int(outcome == "TRADE")
    assert metrics.trade_to_entry_conversion == (D(1) if outcome == "TRADE" else None)
    assert "BASELINE_SELECTION" not in [r.kind for r in result.records]
    if outcome == "TRADE":
        assert result.final_portfolio.fills[0].market_time == T
        assert result.final_portfolio.fills[0].recorded_at == T + H
        assert [r.kind for r in result.records if r.at == T] == [
            "SCAN",
            "DELIVERY",
            "RESEARCH",
            "TERMS",
            "RESEARCH_SELECTION",
            "RISK_AUTHORIZATION",
            "SUBMISSION",
            "OBSERVATION",
        ]
    else:
        assert [r.kind for r in result.records if r.at == T] == [
            "SCAN",
            "DELIVERY",
            "RESEARCH",
            "OBSERVATION",
        ]
    assert load_research_replay(result.canonical_bytes(), store=store) == result


@pytest.mark.parametrize("outcome", ["NO_TRADE", "ABSTAIN"])
def test_nontrade_cannot_call_financial_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("nontrade reached financial preparation")

    for owner, name in [
        (TradeTermsService, "generate"),
        (RiskService, "authorize"),
        (SimulatedBroker, "submit"),
    ]:
        monkeypatch.setattr(owner, name, forbidden)
    result = execute(
        ResearchStore(tmp_path / "r.sqlite"), model=ReplayModel({BTC.value: outcome})
    )
    assert not result.final_portfolio.orders


def test_baseline_fresh_frozen_economics_and_zero_frozen_research(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    baseline = run_scanner_only(config(), inputs(), code=CODE)
    captured = execute(store)
    settings = frozen(captured)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("frozen replay attempted research")

    monkeypatch.setattr(integration, "run_durable_opportunity_research", forbidden)
    monkeypatch.setattr(MarketResearchService, "__init__", forbidden)
    monkeypatch.setattr(ReplayModel, "invoke_observed", forbidden)
    first, second = execute(store, settings=settings), execute(store, settings=settings)
    assert first.canonical_bytes() == second.canonical_bytes()
    assert captured.run_id != first.run_id != baseline.run_id
    assert economics(baseline) == economics(captured) == economics(first)
    assert captured.research_metrics == first.research_metrics
    assert payloads(captured, ReplayResearchRecord) == payloads(
        first, ReplayResearchRecord
    )
    assert load_research_replay(first.canonical_bytes(), store=store) == first
    assert "research_metrics" not in baseline.model_dump()
    assert baseline.manifest.schema_version == "1"


def test_single_pair_window_offers_no_relative_strength_and_reproduces(
    tmp_path: Path,
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    captured = execute(store)
    records = payloads(captured, ReplayResearchRecord)
    assert len(records) == 1
    result = store.load_terminal(records[0].research_run_id).result
    assert result.context.benchmarks == ()
    investigations = [
        r.request
        for r in result.trace
        if isinstance(r, ModelCallRecord) and r.request.stage == "investigate"
    ]
    assert investigations and all(r.tools for r in investigations)
    assert all(
        "relative_strength" not in [t.name for t in r.tools] for r in investigations
    )
    first = execute(store, settings=frozen(captured))
    assert payloads(first, ReplayResearchRecord) == records
    assert economics(first) == economics(captured)
    assert execute(store, settings=frozen(captured)).canonical_bytes() == (
        first.canonical_bytes()
    )


@pytest.mark.parametrize("case", ["terms", "risk", "entry"])
def test_trade_rejections_are_distinct(tmp_path: Path, case: str) -> None:
    cfg = config(end=T + H)
    candles = history()
    if case == "terms":
        candles = tuple(
            c.model_copy(update={"high": D(200), "low": D(1)}) for c in candles
        )
    elif case == "risk":
        cfg = config(
            end=T + H,
            execution=cfg.execution.model_copy(update={"entry_fee_rate": D("0.2")}),
        )
    result = execute(
        ResearchStore(tmp_path / "r.sqlite"), cfg=cfg, datasets=[dataset(candles)]
    )
    metrics = result.research_metrics
    assert metrics is not None and metrics.trade_count == 1
    assert metrics.terms_rejection_count == int(case == "terms")
    assert metrics.risk_rejection_count == int(case == "risk")
    assert metrics.submission_count == int(case == "entry")
    assert metrics.entry_count == 0
    assert metrics.trade_to_entry_conversion == 0
    if case == "risk":
        assert [(r.reason, r.count) for r in metrics.risk_rejection_reasons] == [
            ("NON_POSITIVE_TARGET_REWARD", 1)
        ]
    if case == "entry":
        assert result.metrics.missing_entry_candle_count == 1


def test_one_gateway_sequential_submission_and_no_portfolio_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    gateways: list[PointInTimeDataGateway] = []
    original_generate = TradeTermsService.generate
    original_authorize = RiskService.authorize
    original_submit = SimulatedBroker.submit
    original_research = MarketResearchService.bollinger_position
    original_scan = OpportunityScanner.scan

    def generate(self: TradeTermsService, *, pair: TradingPair) -> Any:
        gateways.append(self._gateway)
        events.append("terms:" + pair.value)
        return original_generate(self, pair=pair)

    def authorize(
        self: RiskService, proposal: TradeProposal, portfolio: PortfolioSnapshot
    ) -> Any:
        gateways.append(self._gateway)
        events.append("risk:" + proposal.pair.value)
        if proposal.pair != BTC:
            assert portfolio.pending is not None
        return original_authorize(self, proposal, portfolio)

    def submit(self: SimulatedBroker, order: OrderIntent) -> Any:
        gateways.append(self._gateway)
        events.append("submit:" + order.pair.value)
        return original_submit(self, order)

    def research(self: MarketResearchService, pair: TradingPair) -> Any:
        gateways.append(self._gateway)
        assert self.now == T
        return original_research(self, pair)

    def scan(self: OpportunityScanner, *, pair: TradingPair) -> Any:
        gateways.append(self._gateway)
        return original_scan(self, pair=pair)

    def inspect(request: ModelRequest) -> None:
        data = json.loads(request.messages[-1].content)["research_data"]
        context = data["context"]
        assert set(context) == {
            "request",
            "research_at",
            "benchmarks",
            "source_bindings",
        }
        assert set(context["request"]) == {"opportunity", "research_run_id"}
        assert not {
            "portfolio",
            "cash",
            "position",
            "pending",
            "outcomes",
            "opportunities",
        } & set(data)
        pair = context["request"]["opportunity"]["pair"]
        if request.stage == "final_assessment":
            events.append("assessment:" + pair)

    monkeypatch.setattr(TradeTermsService, "generate", generate)
    monkeypatch.setattr(RiskService, "authorize", authorize)
    monkeypatch.setattr(SimulatedBroker, "submit", submit)
    monkeypatch.setattr(MarketResearchService, "bollinger_position", research)
    monkeypatch.setattr(OpportunityScanner, "scan", scan)
    result = execute(
        ResearchStore(tmp_path / "r.sqlite"),
        cfg=config(assets=(SOL, BTC, ETH), end=T + H),
        datasets=[dataset(pair=p) for p in (SOL, ETH, BTC)],
        model=ReplayModel(hook=inspect),
    )
    assert len({id(g) for g in gateways}) == 1
    assert events == [
        "assessment:BTC-USD",
        "terms:BTC-USD",
        "risk:BTC-USD",
        "submit:BTC-USD",
        "assessment:ETH-USD",
        "terms:ETH-USD",
        "risk:ETH-USD",
        "assessment:SOL-USD",
        "terms:SOL-USD",
        "risk:SOL-USD",
    ]
    assert len(payloads(result, ReplayResearchRecord)) == 3
    assert result.research_metrics is not None
    assert [
        (r.reason, r.count) for r in result.research_metrics.risk_rejection_reasons
    ] == [("PORTFOLIO_BUSY", 2)]


@pytest.mark.parametrize("outcome", ["TRADE", "NO_TRADE", "ABSTAIN"])
def test_cap_counts_every_candidate_and_stops_before_registration(
    tmp_path: Path,
    outcome: str,
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    model = ReplayModel({p.value: outcome for p in TradingPair})
    with pytest.raises(ResearchBudgetExceeded) as failure:
        execute(
            store,
            cfg=config(assets=(BTC, ETH, SOL), end=T + H),
            datasets=[dataset(pair=p) for p in TradingPair],
            model=model,
            settings=fresh(cap=2),
        )
    assert len(model.requests) == 10
    assert "research_run=" in failure.value.__notes__[0]
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT count(*) FROM research_runs_v1").fetchone() == (2,)
        assert conn.execute(
            "SELECT count(*) FROM research_artifacts_v1"
        ).fetchone() == (2,)


def test_explicit_identity_distinct_trials_and_duplicate_registration(
    tmp_path: Path,
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    first = execute(store, settings=fresh(cap=1))
    model = ReplayModel()
    with pytest.raises(ResearchStorageError):
        execute(store, settings=fresh(cap=1), model=model)
    assert not model.requests
    second = execute(store, settings=fresh("trial-2", 1))
    a, b = (
        payloads(first, ReplayResearchRecord)[0],
        payloads(second, ReplayResearchRecord)[0],
    )
    assert a.research_run_id != b.research_run_id and first.run_id != second.run_id
    op = payloads(first, Delivery)[0].opportunity
    assert a.research_run_id == research_run_id(first.run_id, op)
    assert a.context_digest == opportunity_context_digest(op)


@pytest.mark.parametrize(
    "changes",
    [
        {"max_research_runs": 0},
        {"max_research_runs": -1},
        {"max_research_runs": True},
        {"max_research_runs": "1"},
        {"max_research_runs": None},
        {"trial_id": " "},
    ],
)
def test_invalid_fresh_settings(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        FreshResearchSettings.model_validate(fresh().model_dump() | changes)


@pytest.mark.parametrize("missing", ["trial_id", "max_research_runs", "configuration"])
def test_fresh_required_settings(missing: str) -> None:
    data = fresh().model_dump()
    del data[missing]
    with pytest.raises(ValidationError):
        FreshResearchSettings.model_validate(data)


def test_model_and_store_mode_requirements(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    with pytest.raises(ValueError, match="observable model"):
        asyncio.run(
            run_research_replay(
                config(), inputs(), research=fresh(), store=store, code=CODE
            )
        )
    settings = FrozenResearchSettings(configuration=WORKFLOW, mapping=())
    with pytest.raises(ValueError, match="forbids a model"):
        asyncio.run(
            run_research_replay(
                config(),
                inputs(),
                research=settings,
                store=store,
                model=ReplayModel(),
                code=CODE,
            )
        )
    invalid_store: Any = None
    with pytest.raises(ValueError, match="ResearchStore"):
        asyncio.run(
            run_research_replay(
                config(),
                inputs(),
                research=fresh(),
                store=invalid_store,
                model=ReplayModel(),
                code=CODE,
            )
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("research_run_id", "missing"),
        ("artifact_checksum", "sha256:" + "0" * 64),
        ("context_digest", "sha256:" + "0" * 64),
        ("opportunity_id", "wrong"),
    ],
)
def test_wrong_frozen_mapping(tmp_path: Path, field: str, value: str) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    settings = frozen(execute(store))
    mapping = FrozenResearchMapping.model_validate(
        settings.mapping[0].model_dump() | {field: value}
    )
    changed = FrozenResearchSettings(configuration=WORKFLOW, mapping=(mapping,))
    with pytest.raises((ResearchAdmissionError, ResearchStorageError)):
        execute(store, settings=changed)


def test_missing_unused_duplicate_mapping_and_source_mismatch(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    settings = frozen(execute(store))
    with pytest.raises(ResearchAdmissionError, match="missing"):
        execute(
            store, settings=FrozenResearchSettings(configuration=WORKFLOW, mapping=())
        )
    with pytest.raises(ValueError, match="cover exactly"):
        execute(
            store,
            settings=settings.model_copy(
                update={
                    "mapping": settings.mapping
                    + (
                        settings.mapping[0].model_copy(
                            update={
                                "opportunity_id": "unused",
                                "research_run_id": "unused",
                            }
                        ),
                    )
                }
            ),
        )
    with pytest.raises(ValidationError, match="unique"):
        FrozenResearchSettings(configuration=WORKFLOW, mapping=settings.mapping * 2)
    with pytest.raises(ValueError, match="originating replay configuration"):
        execute(
            store,
            settings=settings,
            datasets=[dataset(history() + (bar(0, "999"), bar(1), bar(2)))],
        )


def test_empty_research_schedule(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    model = ReplayModel()
    result = execute(store, cfg=config(end=T), model=model)
    assert not model.requests
    assert result.research_metrics is not None
    assert result.research_metrics.opportunities_assessed == 0
    assert result.research_metrics.research_to_entry_conversion is None
    assert result.research_metrics.trade_to_entry_conversion is None
    assert (
        execute(store, settings=frozen(result), cfg=config(end=T)).metrics
        == result.metrics
    )


@pytest.mark.parametrize("state", ["busy", "halted"])
def test_research_continues_for_open_or_halted_portfolio(
    tmp_path: Path, state: str
) -> None:
    btc = dataset(
        history()
        + (bar(0, "90"),)
        + ((bar(1, "90"), bar(2, "90")) if state == "busy" else ())
    )
    eth = dataset(tuple(bar(i) for i in range(-19, 1)) + (bar(1, "90"),), pair=ETH)
    datasets = [btc, eth]
    cfg = config(assets=(BTC, ETH))
    baseline = run_scanner_only(cfg, datasets, code=CODE)
    result = execute(
        ResearchStore(tmp_path / "r.sqlite"),
        cfg=cfg,
        datasets=datasets,
        settings=fresh(cap=2),
    )
    assert economics(result) == economics(baseline)
    assert len(payloads(result, ReplayResearchRecord)) == 2
    assert payloads(result, RiskRejection)[0].reason == (
        "PORTFOLIO_BUSY" if state == "busy" else "PORTFOLIO_HALTED"
    )
    assert result.observations[2].portfolio.position is not None
    assert (result.final_portfolio.position is not None) == (state == "halted")
    assert result.metrics.unresolved_data_gap == (state == "halted")


def test_no_research_at_end_or_for_delayed_old_discovery(tmp_path: Path) -> None:
    # Missing trigger dependency suppresses its discovery at T. At T+1 it is old.
    candles = tuple(
        c.model_copy(update={"available_at": T + H}) if c.open_time == T - 10 * H else c
        for c in history()
    )
    model = ReplayModel()
    result = execute(
        ResearchStore(tmp_path / "r.sqlite"),
        cfg=config(end=T + 2 * H),
        datasets=[dataset(candles + (bar(0),))],
        model=model,
    )
    assert not model.requests
    assert "NOT_CURRENT" in [d.disposition for d in payloads(result, Delivery)]
    assert [r.kind for r in result.records if r.at == T + 2 * H] == ["OBSERVATION"]


def test_future_scanner_opportunity_never_reaches_research(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = OpportunityScanner.scan

    def scan(self: OpportunityScanner, *, pair: TradingPair) -> tuple[Opportunity, ...]:
        return tuple(
            item.model_copy(update={"observed_at": T + H, "available_at": T + H})
            for item in original(self, pair=pair)
        )

    monkeypatch.setattr(OpportunityScanner, "scan", scan)
    model = ReplayModel()
    result = execute(
        ResearchStore(tmp_path / "r.sqlite"), cfg=config(end=T + H), model=model
    )
    assert not model.requests
    assert payloads(result, Delivery)[0].disposition == "NOT_CURRENT"


def test_fresh_nondeterminism_changes_selection_only(tmp_path: Path) -> None:
    # A different conclusion changes whether preparation occurs, never its arithmetic.
    store = ResearchStore(tmp_path / "r.sqlite")
    yes = execute(store, settings=fresh("yes"))
    no = execute(
        store, settings=fresh("no"), model=ReplayModel({BTC.value: "NO_TRADE"})
    )
    yes_again = execute(store, settings=fresh("yes-again"))
    assert economics(yes) == economics(yes_again)
    assert payloads(yes, TradeTerms) == payloads(yes_again, TradeTerms)
    assert not no.final_portfolio.orders


def test_unchecked_settings_revalidated_before_inference(tmp_path: Path) -> None:
    model = ReplayModel()
    with pytest.raises(ValidationError):
        execute(
            ResearchStore(tmp_path / "r.sqlite"),
            settings=fresh().model_copy(update={"max_research_runs": True}),
            model=model,
        )
    assert not model.requests
