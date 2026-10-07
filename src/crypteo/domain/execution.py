"""Immutable contracts for already-authorized LONG simulated execution."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from crypteo.domain._execution_arithmetic import _add, _multiply, _subtract
from crypteo.domain.market import (
    ExactDecimal,
    NonNegativeDecimal,
    PositiveDecimal,
    Timeframe,
    TradingPair,
    Venue,
)
from crypteo.domain.time import UtcDatetime, _require_hourly_decision


def _require_order_id(value: str) -> str:
    if not value.strip():
        raise ValueError("order_id must be non-empty")
    return value


class OrderIntent(BaseModel):
    """Fixed LONG terms; decision_at is the current completed-bar boundary.

    No sizing or level generation occurs here. The eligible entry candle opens
    at decision_at, immediately after the decision's completed candle.
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    order_id: str
    venue: Venue
    pair: TradingPair
    timeframe: Timeframe
    decision_at: UtcDatetime
    quantity: PositiveDecimal
    stop_price: PositiveDecimal
    target_price: PositiveDecimal
    max_holding_bars: int = Field(strict=True, gt=0)

    @field_validator("order_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _require_order_id(value)

    @field_validator("decision_at")
    @classmethod
    def validate_boundary(cls, value: datetime) -> datetime:
        return _require_hourly_decision(value)

    @model_validator(mode="after")
    def validate_levels(self) -> Self:
        if self.stop_price >= self.target_price:
            raise ValueError("LONG stop_price must be below target_price")
        return self


class ExecutionConfig(BaseModel):
    """Explicit costs and increments; rates are proportions, not basis points."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    entry_fee_rate: Annotated[NonNegativeDecimal, Field(lt=1)]
    exit_fee_rate: Annotated[NonNegativeDecimal, Field(lt=1)]
    adverse_slippage: Annotated[NonNegativeDecimal, Field(lt=1)]
    price_increment: PositiveDecimal
    quantity_increment: PositiveDecimal


class Fill(BaseModel):
    """One full fill, also the cash ledger entry.

    OPEN/CLOSE identify known market boundaries. INTRABAR identifies only the
    candle interval, never a fabricated timestamp. available_at is when candle
    evidence became knowable; recorded_at is when the broker processed it.
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    order_id: str
    side: Literal["BUY", "SELL"]
    reason: Literal["ENTRY", "STOP", "TARGET", "TIME"]
    phase: Literal["OPEN", "INTRABAR", "CLOSE"]
    candle_open_time: UtcDatetime
    candle_close_time: UtcDatetime
    available_at: UtcDatetime
    recorded_at: UtcDatetime
    reference_price: PositiveDecimal
    execution_price: PositiveDecimal
    quantity: PositiveDecimal
    fee: NonNegativeDecimal
    cash_delta: ExactDecimal

    @field_validator("order_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _require_order_id(value)

    @model_validator(mode="after")
    def validate_fill(self) -> Self:
        allowed = {
            "ENTRY": (("BUY", "OPEN"),),
            "STOP": (("SELL", "OPEN"), ("SELL", "INTRABAR")),
            "TARGET": (("SELL", "OPEN"), ("SELL", "INTRABAR")),
            "TIME": (("SELL", "CLOSE"),),
        }
        if (self.side, self.phase) not in allowed[self.reason]:
            raise ValueError("fill reason, side and phase contradict each other")
        if not self.candle_open_time < self.candle_close_time:
            raise ValueError("fill candle interval must be positive")
        if self.available_at < self.candle_close_time:
            raise ValueError("fill availability cannot precede candle completion")
        if self.recorded_at < self.available_at:
            raise ValueError("fill recording cannot precede availability")
        notional = _multiply(self.quantity, self.execution_price)
        expected = (
            _add(notional, self.fee).copy_negate()
            if self.side == "BUY"
            else _subtract(notional, self.fee)
        )
        if self.cash_delta != expected:
            raise ValueError("fill cash_delta does not reconcile")
        return self

    @property
    def market_time(self) -> datetime | None:
        """Exact time only where the candle establishes a market boundary."""
        if self.phase == "OPEN":
            return self.candle_open_time
        if self.phase == "CLOSE":
            return self.candle_close_time
        return None


class Position(BaseModel):
    """Owned LONG quantity and entry cost, including the entry fee."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    order: OrderIntent
    entry_fill: Fill
    quantity: PositiveDecimal
    cost_basis: NonNegativeDecimal
    holding_bars: int = Field(strict=True, gt=0)
    last_candle_open_time: UtcDatetime

    @model_validator(mode="after")
    def validate_position(self) -> Self:
        _validate_entry(self.order, self.entry_fill)
        if self.quantity != self.order.quantity:
            raise ValueError("position quantity must equal authorized quantity")
        if self.cost_basis != self.entry_fill.cash_delta.copy_negate():
            raise ValueError("position cost basis does not reconcile with entry")
        _validate_holding(self.order, self.holding_bars, self.last_candle_open_time)
        return self


class ClosedTrade(BaseModel):
    """Reconciled entry/exit pair and exact realized PnL."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    order: OrderIntent
    entry_fill: Fill
    exit_fill: Fill
    holding_bars: int = Field(strict=True, gt=0)
    gross_pnl: ExactDecimal
    net_pnl: ExactDecimal

    @model_validator(mode="after")
    def validate_trade(self) -> Self:
        _validate_entry(self.order, self.entry_fill)
        exit = self.exit_fill
        if exit.order_id != self.order.order_id or exit.side != "SELL":
            raise ValueError("trade exit must sell the same authorized order")
        if exit.quantity != self.order.quantity:
            raise ValueError("trade exit must sell the full authorized quantity")
        _validate_holding(self.order, self.holding_bars, exit.candle_open_time)
        if (
            exit.candle_close_time
            != exit.candle_open_time + self.order.timeframe.duration
        ):
            raise ValueError("exit candle must match the authorized timeframe")
        if (
            exit.available_at < self.entry_fill.available_at
            or exit.recorded_at < self.entry_fill.recorded_at
        ):
            raise ValueError("exit cannot be known or recorded before entry")
        if exit.reason == "TIME" and self.holding_bars != self.order.max_holding_bars:
            raise ValueError("time exit must occur on the final authorized holding bar")
        gross = _multiply(
            self.order.quantity,
            _subtract(exit.execution_price, self.entry_fill.execution_price),
        )
        net = _subtract(_subtract(gross, self.entry_fill.fee), exit.fee)
        if self.gross_pnl != gross or self.net_pnl != net:
            raise ValueError("trade gross or net PnL does not reconcile with fills")
        return self


class EntryRejection(BaseModel):
    """A terminal entry outcome; resubmission cannot revive this order ID."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    order_id: str
    reason: Literal["MISSING_ENTRY_CANDLE", "UNAFFORDABLE"]
    recorded_at: UtcDatetime

    @field_validator("order_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _require_order_id(value)


class DataGap(BaseModel):
    """Unresolved holding interval; all further financial processing halts."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    order_id: str
    missing_open_time: UtcDatetime
    expected_completion: UtcDatetime
    detected_at: UtcDatetime

    @field_validator("order_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _require_order_id(value)

    @model_validator(mode="after")
    def validate_times(self) -> Self:
        if self.expected_completion <= self.missing_open_time:
            raise ValueError("gap completion must follow missing open time")
        if self.detected_at < self.expected_completion:
            raise ValueError("gap cannot be detected before expected completion")
        return self


class PortfolioSnapshot(BaseModel):
    """Immutable portfolio view with append-only orders, fills and outcomes."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    initial_cash: NonNegativeDecimal
    cash: NonNegativeDecimal
    cumulative_fees: NonNegativeDecimal = Decimal(0)
    cumulative_net_realized_pnl: ExactDecimal = Decimal(0)
    orders: tuple[OrderIntent, ...] = ()
    pending: OrderIntent | None = None
    position: Position | None = None
    fills: tuple[Fill, ...] = ()
    closed_trades: tuple[ClosedTrade, ...] = ()
    rejections: tuple[EntryRejection, ...] = ()
    data_gap: DataGap | None = None

    @model_validator(mode="after")
    def validate_portfolio(self) -> Self:
        if self.pending is not None and self.position is not None:
            raise ValueError("pending and position cannot coexist")
        orders = {order.order_id: order for order in self.orders}
        trades = {trade.order.order_id: trade for trade in self.closed_trades}
        rejections = {rejection.order_id: rejection for rejection in self.rejections}
        if (
            len(orders) != len(self.orders)
            or len(trades) != len(self.closed_trades)
            or len(rejections) != len(self.rejections)
        ):
            raise ValueError("duplicate lifecycle order IDs")
        active = self.pending or (self.position.order if self.position else None)
        outcome_ids = [*trades, *rejections]
        if active is not None:
            outcome_ids.append(active.order_id)
            if not self.orders or active != self.orders[-1]:
                raise ValueError("active order must be the last order in history")
        if len(outcome_ids) != len(set(outcome_ids)) or set(outcome_ids) != set(orders):
            raise ValueError(
                "each historical order must have exactly one lifecycle state"
            )

        expected_fills: list[Fill] = []
        expected_trades: list[ClosedTrade] = []
        expected_rejections: list[EntryRejection] = []
        previous_completion: datetime | None = None
        for order in self.orders:
            if (
                previous_completion is not None
                and order.decision_at < previous_completion
            ):
                raise ValueError("order decision precedes prior lifecycle completion")
            if order.order_id in trades:
                trade = trades[order.order_id]
                if trade.order != order:
                    raise ValueError("trade terms differ from order history")
                expected_trades.append(trade)
                expected_fills.extend((trade.entry_fill, trade.exit_fill))
                previous_completion = trade.exit_fill.recorded_at
            elif order.order_id in rejections:
                rejection = rejections[order.order_id]
                if rejection.recorded_at < order.decision_at + order.timeframe.duration:
                    raise ValueError(
                        "entry rejection precedes eligible candle completion"
                    )
                expected_rejections.append(rejection)
                previous_completion = rejection.recorded_at
            elif self.position is not None:
                expected_fills.append(self.position.entry_fill)
        if (
            self.fills != tuple(expected_fills)
            or self.closed_trades != tuple(expected_trades)
            or self.rejections != tuple(expected_rejections)
        ):
            raise ValueError(
                "ledger records must match lifecycle history exactly in order"
            )

        cash = self.initial_cash
        fees = Decimal(0)
        for fill in self.fills:
            cash = _add(cash, fill.cash_delta)
            fees = _add(fees, fill.fee)
            if cash < 0:
                raise ValueError("historical fill leaves negative cash")
        realized = Decimal(0)
        for trade in self.closed_trades:
            realized = _add(realized, trade.net_pnl)
        if self.cash != cash or self.cumulative_fees != fees:
            raise ValueError("cash or cumulative fees do not reconcile with ledger")
        if self.cumulative_net_realized_pnl != realized:
            raise ValueError("cumulative realized PnL does not reconcile with trades")
        if (
            _subtract(_add(self.cash, self.position_cost_basis), self.initial_cash)
            != realized
        ):
            raise ValueError("flat/open portfolio accounting does not reconcile")
        if self.data_gap is not None:
            position = self.position
            gap = self.data_gap
            if position is None or gap.order_id != position.order.order_id:
                raise ValueError("data gap must belong to the open position")
            duration = position.order.timeframe.duration
            if (
                gap.missing_open_time != position.last_candle_open_time + duration
                or gap.expected_completion != gap.missing_open_time + duration
                or gap.detected_at < position.entry_fill.recorded_at
                or position.holding_bars >= position.order.max_holding_bars
            ):
                raise ValueError("data gap does not follow position holding history")
        return self

    @property
    def position_quantity(self) -> Decimal:
        return self.position.quantity if self.position else Decimal(0)

    @property
    def position_cost_basis(self) -> Decimal:
        return self.position.cost_basis if self.position else Decimal(0)


def _validate_entry(order: OrderIntent, entry: Fill) -> None:
    if entry.order_id != order.order_id or entry.reason != "ENTRY":
        raise ValueError("entry fill must be the authorized order's entry")
    if entry.quantity != order.quantity:
        raise ValueError("entry must fill the full authorized quantity")
    if (
        entry.candle_open_time != order.decision_at
        or entry.candle_close_time != order.decision_at + order.timeframe.duration
    ):
        raise ValueError("entry candle must match the eligible decision boundary")


def _validate_holding(order: OrderIntent, bars: int, last_open: datetime) -> None:
    if bars > order.max_holding_bars:
        raise ValueError("holding history exceeds authorized maximum")
    if last_open != order.decision_at + (bars - 1) * order.timeframe.duration:
        raise ValueError("holding bars and candle timeline do not reconcile")
