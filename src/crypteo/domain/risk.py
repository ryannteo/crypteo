"""Immutable deterministic LONG proposals, risk settings and sizing results."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from crypteo._execution_costs import _aligned, _execution_estimate
from crypteo.domain._execution_arithmetic import _add, _multiply, _subtract
from crypteo.domain.execution import ExecutionConfig, OrderIntent, _require_order_id
from crypteo.domain.market import (
    ExactDecimal,
    PositiveDecimal,
    Timeframe,
    TradingPair,
    Venue,
)
from crypteo.domain.time import UtcDatetime, _require_hourly_decision


class TradeProposal(BaseModel):
    """Runtime context and supplied levels, independent of research decisions.

    Finite levels are inputs to the risk gate; their LONG relationship is an
    authorization check. No strategy levels or quantities are generated here.
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    order_id: str
    venue: Venue
    pair: TradingPair
    timeframe: Timeframe
    decision_at: UtcDatetime
    stop_price: ExactDecimal
    target_price: ExactDecimal

    @field_validator("order_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _require_order_id(value)

    @field_validator("decision_at")
    @classmethod
    def validate_boundary(cls, value: datetime) -> datetime:
        return _require_hourly_decision(value)


class RiskConfig(BaseModel):
    """Explicit cash risk proportion and holding limit; no numeric defaults."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    risk_fraction: Annotated[PositiveDecimal, Field(le=1)]
    max_holding_bars: int = Field(strict=True, gt=0)


class RiskAuthorization(BaseModel):
    """Decision-time estimates, not a bound on realized gapped execution losses."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    proposal: TradeProposal
    order: OrderIntent
    risk_config: RiskConfig
    execution_config: ExecutionConfig
    reference_price: PositiveDecimal
    capital_basis: PositiveDecimal
    risk_budget: PositiveDecimal
    estimated_entry_price: PositiveDecimal
    estimated_stop_execution_price: PositiveDecimal
    estimated_target_execution_price: PositiveDecimal
    estimated_net_target_reward_per_unit: PositiveDecimal
    estimated_loss_per_unit: PositiveDecimal
    authorized_quantity: PositiveDecimal
    estimated_entry_cash: PositiveDecimal
    estimated_total_loss: PositiveDecimal

    @model_validator(mode="after")
    def validate_execution_and_sizing(self) -> Self:
        expected_order = OrderIntent(
            **self.proposal.model_dump(),
            quantity=self.authorized_quantity,
            max_holding_bars=self.risk_config.max_holding_bars,
        )
        if self.order != expected_order:
            raise ValueError(
                "order must match proposal, quantity and risk configuration"
            )
        config = self.execution_config
        for value, increment, name in (
            (self.proposal.stop_price, config.price_increment, "stop_price"),
            (self.proposal.target_price, config.price_increment, "target_price"),
            (self.authorized_quantity, config.quantity_increment, "quantity"),
        ):
            if not _aligned(value, increment):
                raise ValueError(f"{name} must align to its increment")
        if (
            not 0
            < self.order.stop_price
            < self.reference_price
            < self.order.target_price
        ):
            raise ValueError("reference must lie between LONG levels")
        entry = _execution_estimate(self.reference_price, "BUY", config)
        stop = _execution_estimate(self.proposal.stop_price, "SELL", config)
        target = _execution_estimate(self.proposal.target_price, "SELL", config)
        if self.estimated_entry_price != entry.price:
            raise ValueError("estimated entry price must match adverse BUY execution")
        if self.estimated_stop_execution_price != stop.price:
            raise ValueError("estimated stop price must match adverse SELL execution")
        if self.estimated_target_execution_price != target.price:
            raise ValueError("estimated target price must match adverse SELL execution")
        if self.estimated_net_target_reward_per_unit != _subtract(
            target.cash_per_unit, entry.cash_per_unit
        ):
            raise ValueError("estimated net target reward per unit does not reconcile")
        if self.risk_budget != _multiply(
            self.capital_basis, self.risk_config.risk_fraction
        ):
            raise ValueError("risk budget does not reconcile")
        cash_per_unit = entry.cash_per_unit
        if self.estimated_entry_cash != _multiply(
            self.authorized_quantity, cash_per_unit
        ):
            raise ValueError("estimated entry cash does not reconcile")
        if self.estimated_loss_per_unit != _subtract(
            cash_per_unit,
            stop.cash_per_unit,
        ):
            raise ValueError("estimated loss per unit does not reconcile")
        if self.estimated_total_loss != _multiply(
            self.authorized_quantity, self.estimated_loss_per_unit
        ):
            raise ValueError("estimated total loss does not reconcile")
        if (
            self.estimated_total_loss > self.risk_budget
            or self.estimated_entry_cash > self.capital_basis
        ):
            raise ValueError("authorization exceeds estimated risk or cash constraint")
        next_quantity = _add(self.authorized_quantity, config.quantity_increment)
        if (
            _multiply(next_quantity, self.estimated_loss_per_unit) <= self.risk_budget
            and _multiply(next_quantity, cash_per_unit) <= self.capital_basis
        ):
            raise ValueError("authorized quantity must be maximal")
        return self


class RiskRejection(BaseModel):
    """Ordinary non-authorization; does not consume an ID or change a portfolio."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    proposal: TradeProposal
    risk_config: RiskConfig
    execution_config: ExecutionConfig
    reason: Literal[
        "PORTFOLIO_HALTED",
        "PORTFOLIO_BUSY",
        "REUSED_ORDER_ID",
        "REFERENCE_UNAVAILABLE",
        "INVALID_LONG_LEVELS",
        "UNEXECUTABLE_STOP",
        "NON_POSITIVE_TARGET_REWARD",
        "NO_CAPITAL",
        "QUANTITY_BELOW_INCREMENT",
    ]
