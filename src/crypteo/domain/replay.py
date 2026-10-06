"""Frozen, typed in-memory replay audit records; no durable journal or recovery."""

from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from crypteo.domain._execution_arithmetic import _add, _multiply
from crypteo.domain.execution import (
    ClosedTrade,
    DataGap,
    EntryRejection,
    Fill,
    OrderIntent,
    PortfolioSnapshot,
)
from crypteo.domain.market import (
    ExactDecimal,
    NonNegativeDecimal,
    Timeframe,
    TradingPair,
)
from crypteo.domain.replay_research import (
    ReplayResearchRecord,
    ResearchReplayManifest,
    ResearchReplayMetrics,
    ResearchSelection,
)
from crypteo.domain.research import Opportunity
from crypteo.domain.risk import RiskAuthorization, RiskRejection, TradeProposal
from crypteo.domain.time import UtcDatetime
from crypteo.domain.trade_terms import TradeTerms, TradeTermsRejection
from crypteo.experiment import ExperimentManifest, canonical_bytes, identity


class ScanSnapshot(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )
    pair: TradingPair
    opportunities: tuple[Opportunity, ...]

    @model_validator(mode="after")
    def validate_pair(self) -> Self:
        if any(item.pair != self.pair for item in self.opportunities):
            raise ValueError("scan opportunities must match requested pair")
        return self


DeliveryDisposition = Literal["ADMITTED", "NOT_CURRENT", "NOT_AVAILABLE", "DUPLICATE"]


def delivery_disposition(
    opportunity: Opportunity, at: datetime, seen: Mapping[str, Opportunity]
) -> DeliveryDisposition:
    previous = seen.get(opportunity.opportunity_id)
    if previous is not None:
        if previous.identity_key != opportunity.identity_key:
            raise ValueError("conflicting reuse of opportunity_id identity")
        return "DUPLICATE"
    if opportunity.observed_at != at:
        return "NOT_CURRENT"
    if opportunity.available_at > at:
        return "NOT_AVAILABLE"
    return "ADMITTED"


def proposal_from_terms(
    terms: TradeTerms, run_id: str, opportunity_id: str
) -> TradeProposal:
    return TradeProposal(
        order_id=identity(
            {"run_id": run_id, "opportunity_id": opportunity_id, "purpose": "order"}
        ),
        venue=terms.venue,
        pair=terms.pair,
        timeframe=terms.timeframe,
        decision_at=terms.decision_at,
        stop_price=terms.stop_price,
        target_price=terms.target_price,
    )


class Delivery(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )
    opportunity: Opportunity
    disposition: DeliveryDisposition


class BoundaryObservation(BaseModel):
    """Post-reconciliation, post-submission view; mark uses the exact boundary close.

    A halted position is deliberately unvalued even if later candles are visible.
    Pending orders have not changed cash or created exposure.
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )
    at: UtcDatetime
    portfolio: PortfolioSnapshot
    mark_price: NonNegativeDecimal | None
    marked_equity: NonNegativeDecimal | None

    @model_validator(mode="after")
    def validate_mark(self) -> Self:
        state = self.portfolio
        if state.data_gap is not None:
            if self.mark_price is not None or self.marked_equity is not None:
                raise ValueError("halted portfolio must remain unvalued")
        elif state.position is None:
            if self.mark_price is not None or self.marked_equity != state.cash:
                raise ValueError("flat equity must equal cash")
        elif (
            self.mark_price is None
            or self.mark_price <= 0
            or self.marked_equity
            != _add(state.cash, _multiply(state.position.quantity, self.mark_price))
        ):
            raise ValueError("open equity must reconcile with position mark")
        return self


ReplayPayload = (
    ScanSnapshot
    | Delivery
    | TradeTerms
    | TradeTermsRejection
    | TradeProposal
    | RiskAuthorization
    | RiskRejection
    | OrderIntent
    | Fill
    | ClosedTrade
    | EntryRejection
    | DataGap
    | BoundaryObservation
    | ReplayResearchRecord
    | ResearchSelection
)
RecordKind = Literal[
    "SCAN",
    "DELIVERY",
    "TERMS",
    "TERMS_REJECTION",
    "BASELINE_SELECTION",
    "RESEARCH",
    "RESEARCH_SELECTION",
    "RISK_AUTHORIZATION",
    "RISK_REJECTION",
    "SUBMISSION",
    "FILL",
    "CLOSED_TRADE",
    "ENTRY_REJECTION",
    "DATA_GAP",
    "OBSERVATION",
]
PAYLOAD_KINDS: dict[type[BaseModel], RecordKind] = {
    ScanSnapshot: "SCAN",
    Delivery: "DELIVERY",
    TradeTerms: "TERMS",
    TradeTermsRejection: "TERMS_REJECTION",
    TradeProposal: "BASELINE_SELECTION",
    ReplayResearchRecord: "RESEARCH",
    ResearchSelection: "RESEARCH_SELECTION",
    RiskAuthorization: "RISK_AUTHORIZATION",
    RiskRejection: "RISK_REJECTION",
    OrderIntent: "SUBMISSION",
    Fill: "FILL",
    ClosedTrade: "CLOSED_TRADE",
    EntryRejection: "ENTRY_REJECTION",
    DataGap: "DATA_GAP",
    BoundaryObservation: "OBSERVATION",
}


class ReplayRecord(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )
    sequence: int = Field(strict=True, ge=0)
    at: UtcDatetime
    kind: RecordKind
    opportunity_id: str | None = None
    payload: ReplayPayload

    @model_validator(mode="after")
    def validate_kind(self) -> Self:
        if PAYLOAD_KINDS[type(self.payload)] != self.kind:
            raise ValueError("record kind must match typed payload")
        if self.kind not in ("SCAN", "OBSERVATION") and not self.opportunity_id:
            raise ValueError("candidate/lifecycle records require opportunity linkage")
        if self.kind in ("SCAN", "OBSERVATION") and self.opportunity_id is not None:
            raise ValueError("scan/observation records cannot have opportunity linkage")
        payload = self.payload
        if isinstance(payload, Delivery):
            opportunity = payload.opportunity
            if self.opportunity_id != opportunity.opportunity_id:
                raise ValueError("delivery opportunity linkage mismatch")
            fresh = opportunity.observed_at == self.at
            visible = opportunity.available_at <= self.at
            if (
                (payload.disposition == "ADMITTED" and not (fresh and visible))
                or (payload.disposition == "NOT_CURRENT" and fresh)
                or (payload.disposition == "NOT_AVAILABLE" and (not fresh or visible))
            ):
                raise ValueError("delivery disposition contradicts record time")
        if (
            isinstance(payload, ReplayResearchRecord)
            and self.opportunity_id != payload.opportunity_id
        ):
            raise ValueError("research opportunity linkage mismatch")
        payload_at = self.at
        if isinstance(payload, ReplayResearchRecord):
            payload_at = payload.research_at
        elif isinstance(payload, ResearchSelection):
            payload_at = payload.proposal.decision_at
        elif isinstance(
            payload, (TradeTerms, TradeTermsRejection, TradeProposal, OrderIntent)
        ):
            payload_at = payload.decision_at
        elif isinstance(payload, (RiskAuthorization, RiskRejection)):
            payload_at = payload.proposal.decision_at
        elif isinstance(payload, (Fill, EntryRejection)):
            payload_at = payload.recorded_at
        elif isinstance(payload, ClosedTrade):
            payload_at = payload.exit_fill.recorded_at
        elif isinstance(payload, DataGap):
            payload_at = payload.detected_at
        elif isinstance(payload, BoundaryObservation):
            payload_at = payload.at
        if self.at != payload_at:
            raise ValueError("record time must match payload processing/decision time")
        return self


class ReplayMetrics(BaseModel):
    """Returns/drawdown/win rate/exposure are fractions, never percentages.

    Ratio outputs round half-even to 50 significant digits. Money remains exact.
    Exposure counts each reconciled holding candle (including entry/exit) once;
    it makes no assertion about intrabar duration. An unresolved suffix makes
    full-period exposure and marked metrics unavailable, retaining known bars
    and prefix drawdown. Return on zero starting capital and empty win rate are
    undefined (None); an empty period has undefined exposure and zero drawdown.
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )
    starting_marked_equity: NonNegativeDecimal
    ending_marked_equity: NonNegativeDecimal | None
    realized_net_pnl: ExactDecimal
    realized_return: ExactDecimal | None
    marked_return: ExactDecimal | None
    maximum_drawdown: NonNegativeDecimal | None
    valid_prefix_maximum_drawdown: NonNegativeDecimal
    valuation_valid_through: UtcDatetime
    entry_count: int = Field(strict=True, ge=0)
    closed_trade_count: int = Field(strict=True, ge=0)
    wins: int = Field(strict=True, ge=0)
    losses: int = Field(strict=True, ge=0)
    breakevens: int = Field(strict=True, ge=0)
    win_rate: NonNegativeDecimal | None
    actual_fees: NonNegativeDecimal
    scheduled_bars: int = Field(strict=True, ge=0)
    known_exposure_bars: int = Field(strict=True, ge=0)
    hourly_bar_exposure: NonNegativeDecimal | None
    stop_count: int = Field(strict=True, ge=0)
    target_count: int = Field(strict=True, ge=0)
    time_count: int = Field(strict=True, ge=0)
    missing_entry_candle_count: int = Field(strict=True, ge=0)
    valuation_complete: bool
    exposure_complete: bool
    open_position: bool
    unresolved_data_gap: bool


class ReplayResult(BaseModel):
    """A normally completed schedule, possibly with an unresolved holding gap."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )
    manifest: Annotated[
        ExperimentManifest | ResearchReplayManifest,
        Field(discriminator="schema_version"),
    ]
    run_id: str
    records: tuple[ReplayRecord, ...]
    final_portfolio: PortfolioSnapshot
    metrics: ReplayMetrics
    research_metrics: ResearchReplayMetrics | None = None

    @model_serializer(mode="wrap")
    def serialize_result(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.research_metrics is None:
            data.pop("research_metrics", None)
        return data

    @model_validator(mode="after")
    def validate_completion(self) -> Self:
        if self.run_id != self.manifest.run_id:
            raise ValueError("run_id must match manifest")
        if self.final_portfolio.pending is not None:
            raise ValueError("completed replay cannot remain pending")
        config = self.manifest.replay
        if self.final_portfolio.initial_cash != config.initial_cash:
            raise ValueError("portfolio initial cash must match replay configuration")
        if tuple(record.sequence for record in self.records) != tuple(
            range(len(self.records))
        ):
            raise ValueError("record sequences must be contiguous")
        if any(
            left.at > right.at
            for left, right in zip(self.records, self.records[1:], strict=False)
        ):
            raise ValueError("record times must be nondecreasing")
        for record in self.records:
            if not config.start <= record.at <= config.end:
                raise ValueError("record time outside replay period")
            if record.at == config.end and record.kind not in (
                "FILL",
                "ENTRY_REJECTION",
                "CLOSED_TRADE",
                "DATA_GAP",
                "OBSERVATION",
            ):
                raise ValueError(
                    "final boundary permits only reconciliation/observation"
                )
        duration = Timeframe.ONE_HOUR.duration
        if tuple(observation.at for observation in self.observations) != tuple(
            config.start + index * duration
            for index in range((config.end - config.start) // duration + 1)
        ):
            raise ValueError(
                "observations must cover every hourly boundary through end"
            )
        if not self.records or not isinstance(
            self.records[-1].payload, BoundaryObservation
        ):
            raise ValueError("completed replay requires final observation")
        final = self.records[-1].payload
        if (
            final.at != self.manifest.replay.end
            or final.portfolio != self.final_portfolio
        ):
            raise ValueError("final observation must match end and final portfolio")
        # Local imports keep the pure calculations/checks independent of model
        # construction while allowing the canonical model to enforce them.
        from crypteo.replay_metrics import calculate_metrics, calculate_research_metrics
        from crypteo.replay_validation import validate_lifecycle

        validate_lifecycle(self)
        if isinstance(self.manifest, ResearchReplayManifest):
            if self.research_metrics != calculate_research_metrics(self.records):
                raise ValueError("research metrics must match replay records")
        elif self.research_metrics is not None:
            raise ValueError("baseline cannot have research metrics")
        if self.metrics != calculate_metrics(
            config, self.final_portfolio, self.observations
        ):
            raise ValueError("metrics must match authoritative replay observations")
        return self

    @property
    def observations(self) -> tuple[BoundaryObservation, ...]:
        return tuple(
            record.payload
            for record in self.records
            if isinstance(record.payload, BoundaryObservation)
        )

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self)
