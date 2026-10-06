"""Pure audit consistency checks; no market reads or execution simulation."""

from itertools import groupby

from crypteo._execution_costs import _execution_price
from crypteo.domain._execution_arithmetic import _multiply
from crypteo.domain.execution import (
    ClosedTrade,
    DataGap,
    EntryRejection,
    ExecutionConfig,
    Fill,
    OrderIntent,
    PortfolioSnapshot,
)
from crypteo.domain.replay import (
    BoundaryObservation,
    Delivery,
    RecordKind,
    ReplayPayload,
    ReplayRecord,
    ReplayResult,
    ScanSnapshot,
    delivery_disposition,
    proposal_from_terms,
)
from crypteo.domain.replay_research import (
    FreshResearchSettings,
    FrozenResearchSettings,
    ReplayResearchRecord,
    ResearchReplayManifest,
    ResearchSelection,
    validate_research_record,
)
from crypteo.domain.research import DecisionOutcome, Opportunity
from crypteo.domain.risk import RiskAuthorization, RiskRejection, TradeProposal
from crypteo.domain.trade_terms import TradeTerms, TradeTermsRejection
from crypteo.experiment import ASSET_PRIORITY


class _BoundaryRecords:
    """Consume the existing deterministic record grammar, including linkage."""

    def __init__(self, records: tuple[ReplayRecord, ...]) -> None:
        self.records = records
        self.index = 0

    @property
    def kind(self) -> RecordKind | None:
        return self.records[self.index].kind if self.index < len(self.records) else None

    def take(
        self, *kinds: RecordKind, opportunity_id: str | None = None
    ) -> ReplayPayload:
        if self.kind not in kinds:
            raise ValueError(f"missing or out-of-sequence lifecycle stage: {kinds}")
        record = self.records[self.index]
        if record.opportunity_id != opportunity_id:
            raise ValueError("lifecycle opportunity linkage mismatch")
        self.index += 1
        return record.payload


def _validate_preparation(
    result: ReplayResult,
    cursor: _BoundaryRecords,
    opportunity: Opportunity,
    state: PortfolioSnapshot,
) -> OrderIntent | None:
    oid = opportunity.opportunity_id
    config = result.manifest.replay
    research_mode = isinstance(result.manifest, ResearchReplayManifest)
    if isinstance(result.manifest, ResearchReplayManifest):
        research = cursor.take("RESEARCH", opportunity_id=oid)
        assert isinstance(research, ReplayResearchRecord)
        validate_research_record(research, opportunity, result.manifest)
        if research.outcome != DecisionOutcome.TRADE:
            return None
    terms = cursor.take("TERMS", "TERMS_REJECTION", opportunity_id=oid)
    assert isinstance(terms, (TradeTerms, TradeTermsRejection))
    if (terms.venue, terms.pair, terms.timeframe, terms.decision_at) != (
        opportunity.venue,
        opportunity.pair,
        opportunity.timeframe,
        opportunity.observed_at,
    ) or terms.config != config.trade_terms:
        raise ValueError("terms must match admitted opportunity and configuration")
    if isinstance(terms, TradeTermsRejection):
        return None
    selected = cursor.take(
        "RESEARCH_SELECTION" if research_mode else "BASELINE_SELECTION",
        opportunity_id=oid,
    )
    proposal = (
        selected.proposal if isinstance(selected, ResearchSelection) else selected
    )
    assert isinstance(proposal, TradeProposal)
    if proposal != proposal_from_terms(terms, result.run_id, oid):
        raise ValueError("proposal must match frozen terms and opportunity order ID")
    risk = cursor.take("RISK_AUTHORIZATION", "RISK_REJECTION", opportunity_id=oid)
    assert isinstance(risk, (RiskAuthorization, RiskRejection))
    if (
        risk.proposal != proposal
        or risk.risk_config != config.risk
        or risk.execution_config != config.execution
    ):
        raise ValueError("risk result must match proposal and replay configuration")
    if isinstance(risk, RiskRejection):
        if (
            (risk.reason == "PORTFOLIO_HALTED" and state.data_gap is None)
            or (
                risk.reason == "PORTFOLIO_BUSY"
                and state.pending is None
                and state.position is None
            )
            or (
                risk.reason == "REUSED_ORDER_ID"
                and not any(
                    order.order_id == proposal.order_id for order in state.orders
                )
            )
            or (risk.reason == "NO_CAPITAL" and state.cash != 0)
        ):
            raise ValueError("risk rejection contradicts retained portfolio state")
        return None
    if (
        state.pending is not None
        or state.position is not None
        or state.data_gap is not None
        or risk.capital_basis != state.cash
        or risk.reference_price != terms.reference_price
    ):
        raise ValueError("authorization contradicts reconciled portfolio or terms")
    order = cursor.take("SUBMISSION", opportunity_id=oid)
    assert isinstance(order, OrderIntent)
    if order != risk.order:
        raise ValueError("submission must match authorized order")
    return order


def _validate_fill_economics(fill: Fill, config: ExecutionConfig) -> None:
    if fill.execution_price != _execution_price(
        fill.reference_price, fill.side, config
    ):
        raise ValueError("fill execution price contradicts retained execution config")
    rate = config.entry_fee_rate if fill.side == "BUY" else config.exit_fee_rate
    if fill.fee != _multiply(_multiply(fill.quantity, fill.execution_price), rate):
        raise ValueError("fill fee contradicts retained execution config")


def _validate_exit_levels(trade: ClosedTrade) -> None:
    order, entry, exit = trade.order, trade.entry_fill, trade.exit_fill
    if exit.reason in ("STOP", "TARGET"):
        level = order.stop_price if exit.reason == "STOP" else order.target_price
        if (exit.phase == "INTRABAR" and exit.reference_price != level) or (
            exit.phase == "OPEN"
            and (
                (exit.reason == "STOP" and exit.reference_price > level)
                or (exit.reason == "TARGET" and exit.reference_price < level)
            )
        ):
            raise ValueError("exit reference contradicts retained order level")
    # The entry retains the candle open only for same-candle exits. Later
    # candle opens and high/low paths are not retained and cannot be inferred.
    if entry.candle_open_time == exit.candle_open_time and (
        (exit.phase == "OPEN" and exit.reference_price != entry.reference_price)
        or (
            exit.phase != "OPEN"
            and not order.stop_price < entry.reference_price < order.target_price
        )
    ):
        raise ValueError("exit phase contradicts retained entry candle open")


def _validate_observation_time(
    observation: BoundaryObservation, previous: PortfolioSnapshot
) -> None:
    state, at = observation.portfolio, observation.at
    if state.pending is not None and state.pending.decision_at != at:
        raise ValueError("pending entry must be reconciled at its next boundary")
    if previous.data_gap is not None and state != previous:
        raise ValueError("halted portfolio history cannot change")
    position = state.position
    if position is not None:
        duration = position.order.timeframe.duration
        completed_at = position.last_candle_open_time + duration
        expected = state.data_gap.missing_open_time if state.data_gap else at
        if (
            completed_at != expected
            or position.holding_bars >= position.order.max_holding_bars
        ):
            raise ValueError("observation position has inconsistent holding history")


def validate_lifecycle(result: ReplayResult) -> None:
    """Check stage chains and ledger prefixes using the existing typed contracts.

    PortfolioSnapshot reconciles cash, fees, realized proceeds and order outcomes.
    We only bind those ledgers to records already processed at each observation;
    this deliberately does not recalculate candle signals or broker execution.
    """
    config = result.manifest.replay
    seen: dict[str, Opportunity] = {}
    order_opportunities: dict[str, str] = {}
    orders: list[OrderIntent] = []
    fills: list[Fill] = []
    trades: list[ClosedTrade] = []
    rejections: list[EntryRejection] = []
    gap: DataGap | None = None
    previous = PortfolioSnapshot(
        initial_cash=config.initial_cash, cash=config.initial_cash
    )
    financial_kinds: tuple[RecordKind, ...] = (
        "FILL",
        "ENTRY_REJECTION",
        "CLOSED_TRADE",
        "DATA_GAP",
    )
    for at, grouped in groupby(result.records, key=lambda record: record.at):
        records = tuple(grouped)
        observation = records[-1].payload
        if not isinstance(observation, BoundaryObservation):
            raise ValueError("each boundary must end with its observation")
        state = observation.portfolio
        cursor = _BoundaryRecords(records)
        last_financial_stage = -1
        while cursor.kind in financial_kinds:
            record = records[cursor.index]
            payload = record.payload
            assert isinstance(payload, (Fill, EntryRejection, ClosedTrade, DataGap))
            order_id = (
                payload.order.order_id
                if isinstance(payload, ClosedTrade)
                else payload.order_id
            )
            if order_id not in order_opportunities:
                raise ValueError("financial lifecycle requires a prior submission")
            cursor.take(record.kind, opportunity_id=order_opportunities[order_id])
            stage = financial_kinds.index(record.kind)
            if stage < last_financial_stage or gap is not None:
                raise ValueError("financial records contradict reconciliation order")
            last_financial_stage = stage
            if isinstance(payload, Fill):
                if payload.candle_close_time != at:
                    raise ValueError("fill must reconcile at its candle completion")
                _validate_fill_economics(payload, config.execution)
                fills.append(payload)
            elif isinstance(payload, ClosedTrade):
                _validate_exit_levels(payload)
                trades.append(payload)
            elif isinstance(payload, EntryRejection):
                rejected_order = next(
                    order for order in orders if order.order_id == order_id
                )
                if at != rejected_order.decision_at + rejected_order.timeframe.duration:
                    raise ValueError(
                        "entry rejection must occur at eligible completion"
                    )
                rejections.append(payload)
            else:
                if payload.expected_completion != at:
                    raise ValueError("gap must be detected at expected completion")
                gap = payload
        if (
            state.initial_cash != config.initial_cash
            or state.fills != tuple(fills)
            or state.closed_trades != tuple(trades)
            or state.rejections != tuple(rejections)
            or state.data_gap != gap
        ):
            raise ValueError(
                "observation ledger must match preceding lifecycle records"
            )
        _validate_observation_time(observation, previous)
        # Submissions do not change cash. Removing only this boundary's pending
        # order gives the reconciled state against which preparation took place.
        reconciled = PortfolioSnapshot.model_validate(
            state.model_dump()
            | {
                "orders": tuple(orders),
                "pending": None
                if state.pending and state.pending.decision_at == at
                else state.pending,
            }
        )
        if at < config.end:
            candidates: list[Opportunity] = []
            for pair in config.assets:
                scan = cursor.take("SCAN")
                assert isinstance(scan, ScanSnapshot)
                if scan.pair != pair:
                    raise ValueError(
                        "scans must cover configured assets in priority order"
                    )
                candidates.extend(scan.opportunities)
            candidates.sort(
                key=lambda item: (ASSET_PRIORITY.index(item.pair), item.opportunity_id)
            )
            admitted: list[Opportunity] = []
            for opportunity in candidates:
                oid = opportunity.opportunity_id
                delivery = cursor.take("DELIVERY", opportunity_id=oid)
                assert isinstance(delivery, Delivery)
                disposition = delivery_disposition(opportunity, at, seen)
                if (
                    delivery.opportunity != opportunity
                    or delivery.disposition != disposition
                ):
                    raise ValueError("delivery must match scan and first-seen identity")
                seen.setdefault(oid, opportunity)
                if disposition == "ADMITTED":
                    admitted.append(opportunity)
            for opportunity in admitted:
                order = _validate_preparation(result, cursor, opportunity, reconciled)
                if order is not None:
                    orders.append(order)
                    order_opportunities[order.order_id] = opportunity.opportunity_id
                    reconciled = PortfolioSnapshot.model_validate(
                        reconciled.model_dump()
                        | {"orders": tuple(orders), "pending": order}
                    )
        cursor.take("OBSERVATION")
        if cursor.kind is not None or state != reconciled:
            raise ValueError(
                "observation must follow and match complete lifecycle chain"
            )
        previous = state

    if isinstance(result.manifest, ResearchReplayManifest):
        research_records = tuple(
            record.payload
            for record in result.records
            if isinstance(record.payload, ReplayResearchRecord)
        )
        runs = tuple(record.research_run_id for record in research_records)
        if len(runs) != len(set(runs)):
            raise ValueError("research run reused for multiple candidates")
        settings = result.manifest.research
        if isinstance(settings, FreshResearchSettings):
            if len(research_records) > settings.max_research_runs:
                raise ValueError("research records exceed replay budget")
        elif isinstance(settings, FrozenResearchSettings) and {
            record.opportunity_id for record in research_records
        } != {item.opportunity_id for item in settings.mapping}:
            raise ValueError("frozen mapping must cover exactly admitted candidates")
