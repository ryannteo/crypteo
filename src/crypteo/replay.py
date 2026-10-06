"""Bounded deterministic scanner-only historical replay over explicit datasets."""

from collections.abc import Generator, Iterable

from pydantic import TypeAdapter

from crypteo.coinbase_acquisition import CoinbaseAcquisitionRecord
from crypteo.coinbase_dataset import (
    CoinbaseCanonicalDataset,
    CoinbaseHistoricalDataset,
    canonical_dataset_artifacts,
)
from crypteo.domain._execution_arithmetic import _add, _multiply
from crypteo.domain.execution import PortfolioSnapshot
from crypteo.domain.market import Candle, Timeframe, TradingPair
from crypteo.domain.replay import (
    PAYLOAD_KINDS,
    BoundaryObservation,
    Delivery,
    ReplayPayload,
    ReplayRecord,
    ReplayResult,
    ScanSnapshot,
    delivery_disposition,
    proposal_from_terms,
)
from crypteo.domain.replay_research import (
    ReplayResearchRecord,
    ResearchReplayManifest,
    ResearchSelection,
    validate_research_record,
)
from crypteo.domain.research import DecisionOutcome, Opportunity
from crypteo.domain.risk import RiskAuthorization
from crypteo.domain.trade_terms import TradeTerms, TradeTermsRejection
from crypteo.execution import SimulatedBroker
from crypteo.experiment import (
    ASSET_PRIORITY,
    CodeIdentity,
    DatasetIdentity,
    ExperimentManifest,
    ReplayConfig,
    build_manifest,
    current_code_identity,
)
from crypteo.replay_metrics import calculate_metrics, calculate_research_metrics
from crypteo.risk import RiskService
from crypteo.scanner import OpportunityScanner
from crypteo.temporal import PointInTimeDataGateway, SimulationClock
from crypteo.trade_terms import TradeTermsService

_CANDLES = TypeAdapter(tuple[Candle, ...])


def _inputs(
    assets: tuple[TradingPair, ...],
    datasets: Iterable[CoinbaseCanonicalDataset | CoinbaseHistoricalDataset],
) -> tuple[tuple[Candle, ...], tuple[DatasetIdentity, ...]]:
    by_pair: dict[TradingPair, tuple[tuple[Candle, ...], DatasetIdentity]] = {}
    for dataset in datasets:
        manifest, encoded = canonical_dataset_artifacts(
            dataset.candles, dataset.manifest
        )
        pair = manifest.pair
        if pair in by_pair:
            raise ValueError("supply exactly one canonical dataset per pair")
        acquisition = dataset.acquisition
        if acquisition is not None:
            acquisition = CoinbaseAcquisitionRecord.model_validate(
                acquisition.model_dump()
            )
            if acquisition.dataset_version != manifest.dataset_version:
                raise ValueError("acquisition dataset linkage mismatch")
        by_pair[pair] = (
            _CANDLES.validate_json(encoded),
            DatasetIdentity(
                pair=pair,
                dataset_version=manifest.dataset_version,
                acquisition_id=acquisition.acquisition_id if acquisition else None,
            ),
        )
    if set(by_pair) != set(assets):
        raise ValueError("supplied datasets must exactly match configured assets")
    return (
        tuple(candle for pair in assets for candle in by_pair[pair][0]),
        tuple(by_pair[pair][1] for pair in assets),
    )


def _observe(
    gateway: PointInTimeDataGateway, state: PortfolioSnapshot
) -> BoundaryObservation:
    mark = None
    equity = state.cash
    if state.data_gap is not None:
        return BoundaryObservation(
            at=gateway.now, portfolio=state, mark_price=None, marked_equity=None
        )
    if state.position is not None:
        order = state.position.order
        candle = next(
            (
                item
                for item in gateway.read_candles(
                    venue=order.venue,
                    pair=order.pair,
                    timeframe=order.timeframe,
                )
                if item.close_time == gateway.now
            ),
            None,
        )
        if candle is None:
            raise ValueError("reconciled open position lacks exact boundary mark")
        mark = candle.close
        equity = _add(state.cash, _multiply(state.position.quantity, mark))
    return BoundaryObservation(
        at=gateway.now, portfolio=state, mark_price=mark, marked_equity=equity
    )


def run_scanner_only(
    config: ReplayConfig,
    datasets: Iterable[CoinbaseCanonicalDataset | CoinbaseHistoricalDataset],
    *,
    code: CodeIdentity | None = None,
) -> ReplayResult:
    """Run hourly decisions in [start,end), then reconcile/observe once at end.

    Runtime alone owns the clock, delivery IDs, order IDs and append-only record
    sequence. Services share one gateway; broker alone owns financial state.
    Every valid-terms candidate is selected, authorized against the *current*
    snapshot, and immediately submitted on approval. Errors propagate; no partial
    result, retry, persistence, extension, cancellation or forced liquidation.

    ``code`` may explicitly identify a packaged build; by default identity comes
    from the executing source checkout. Inputs are validated in full, never
    fetched or trimmed; all historical consumers read exclusively via the gateway.
    """
    config = ReplayConfig.model_validate(config.model_dump())
    candles, dataset_ids = _inputs(config.assets, datasets)
    manifest = build_manifest(config, dataset_ids, code or current_code_identity())
    clock = SimulationClock(config.start)
    gateway = PointInTimeDataGateway(clock, candles)
    engine = _replay_engine(manifest, clock, gateway)
    try:
        while True:
            engine.send(None)
    except StopIteration as finished:
        result: ReplayResult = finished.value
        return result
    finally:
        engine.close()


def _replay_engine(
    manifest: ExperimentManifest | ResearchReplayManifest,
    clock: SimulationClock,
    gateway: PointInTimeDataGateway,
) -> Generator[Opportunity, ReplayResearchRecord | None, ReplayResult]:
    """The single hourly lifecycle; drivers supply selection at candidate yields."""
    config = manifest.replay
    run_id = manifest.run_id
    scanner = OpportunityScanner(gateway)
    terms_service = TradeTermsService(gateway, config.trade_terms)
    risk = RiskService(gateway, config.risk, config.execution)
    broker = SimulatedBroker(
        gateway, config.execution, initial_cash=config.initial_cash
    )
    seen: dict[str, Opportunity] = {}
    order_opportunities: dict[str, str] = {}
    records: list[ReplayRecord] = []
    observations: list[BoundaryObservation] = []

    def record(payload: ReplayPayload, opportunity_id: str | None = None) -> None:
        records.append(
            ReplayRecord(
                sequence=len(records),
                at=gateway.now,
                kind=PAYLOAD_KINDS[type(payload)],
                opportunity_id=opportunity_id,
                payload=payload,
            )
        )

    while True:
        before = broker.snapshot
        state = broker.process()
        # One hourly step and one active trade make this order chronological:
        # entry then exit fills, terminal entry rejection, closed trade, gap.
        for fill in state.fills[len(before.fills) :]:
            record(fill, order_opportunities[fill.order_id])
        for rejection in state.rejections[len(before.rejections) :]:
            record(rejection, order_opportunities[rejection.order_id])
        for trade in state.closed_trades[len(before.closed_trades) :]:
            record(trade, order_opportunities[trade.order.order_id])
        if state.data_gap is not None and before.data_gap is None:
            record(state.data_gap, order_opportunities[state.data_gap.order_id])

        if gateway.now < config.end:
            candidates: list[Opportunity] = []
            for pair in config.assets:
                snapshot = tuple(
                    Opportunity.model_validate(item.model_dump())
                    for item in scanner.scan(pair=pair)
                )
                if any(item.pair != pair for item in snapshot):
                    raise ValueError("scanner returned a different pair")
                record(ScanSnapshot(pair=pair, opportunities=snapshot))
                candidates.extend(snapshot)
            candidates.sort(
                key=lambda item: (ASSET_PRIORITY.index(item.pair), item.opportunity_id)
            )
            admitted: list[Opportunity] = []
            for opportunity in candidates:
                oid = opportunity.opportunity_id
                disposition = delivery_disposition(opportunity, gateway.now, seen)
                seen.setdefault(oid, opportunity)  # First disposition is terminal.
                record(Delivery(opportunity=opportunity, disposition=disposition), oid)
                if disposition == "ADMITTED":
                    admitted.append(opportunity)
            for opportunity in admitted:
                oid = opportunity.opportunity_id
                at = gateway.now
                selection = yield opportunity
                if gateway.now != at:
                    raise ValueError(
                        "authoritative replay clock moved during selection"
                    )
                if isinstance(manifest, ResearchReplayManifest):
                    if selection is None:
                        raise ValueError(
                            "research selection requires validated research"
                        )
                    selection = ReplayResearchRecord.model_validate(selection)
                    validate_research_record(selection, opportunity, manifest)
                    record(selection, oid)
                    if selection.outcome != DecisionOutcome.TRADE:
                        continue
                elif selection is not None:
                    raise ValueError("baseline cannot consume research selection")
                generated = terms_service.generate(pair=opportunity.pair)
                terms = type(generated).model_validate(generated.model_dump())
                if (
                    (terms.venue, terms.pair, terms.timeframe)
                    != (opportunity.venue, opportunity.pair, opportunity.timeframe)
                    or terms.decision_at != opportunity.observed_at
                    or terms.decision_at != gateway.now
                    or terms.config != config.trade_terms
                ):
                    raise ValueError("stale or mismatched trade terms")
                record(terms, oid)
                if isinstance(terms, TradeTermsRejection):
                    continue
                assert isinstance(terms, TradeTerms)
                proposal = proposal_from_terms(terms, run_id, oid)
                record(
                    ResearchSelection(proposal=proposal)
                    if isinstance(manifest, ResearchReplayManifest)
                    else proposal,
                    oid,
                )
                authorization = risk.authorize(proposal, broker.snapshot)
                record(authorization, oid)
                if isinstance(authorization, RiskAuthorization):
                    broker.submit(authorization.order)
                    order_opportunities[authorization.order.order_id] = oid
                    record(authorization.order, oid)
        observation = _observe(gateway, broker.snapshot)
        observations.append(observation)
        record(observation)
        if gateway.now == config.end:
            break
        clock.advance_to(gateway.now + Timeframe.ONE_HOUR.duration)

    if broker.snapshot.pending is not None:
        raise ValueError(
            "completed replay cannot remain pending after final reconciliation"
        )
    return ReplayResult(
        manifest=manifest,
        run_id=run_id,
        records=tuple(records),
        final_portfolio=broker.snapshot,
        metrics=calculate_metrics(config, broker.snapshot, tuple(observations)),
        research_metrics=calculate_research_metrics(tuple(records))
        if isinstance(manifest, ResearchReplayManifest)
        else None,
    )
