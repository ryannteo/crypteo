"""Deterministic next-open LONG execution using only gateway-visible candles."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from crypteo.domain._execution_arithmetic import _add, _multiply, _subtract
from crypteo.domain.execution import (
    DataGap,
    EntryRejection,
    ExecutionConfig,
    Fill,
    OrderIntent,
    PortfolioSnapshot,
    Position,
)
from crypteo.domain.market import Candle
from crypteo.portfolio import _enter, _exit
from crypteo.temporal import PointInTimeDataGateway


def _increment_ratio(value: Decimal, increment: Decimal) -> tuple[int, int]:
    numerator, denominator = value.as_integer_ratio()
    step_numerator, step_denominator = increment.as_integer_ratio()
    return numerator * step_denominator, denominator * step_numerator


def _aligned(value: Decimal, increment: Decimal) -> bool:
    numerator, denominator = _increment_ratio(value, increment)
    return numerator % denominator == 0


def _execution_price(
    reference: Decimal, side: Literal["BUY", "SELL"], config: ExecutionConfig
) -> Decimal:
    factor = (
        _add(Decimal(1), config.adverse_slippage)
        if side == "BUY"
        else _subtract(Decimal(1), config.adverse_slippage)
    )
    slipped = _multiply(reference, factor)
    numerator, denominator = _increment_ratio(slipped, config.price_increment)
    steps = (
        (numerator + denominator - 1) // denominator
        if side == "BUY"
        else numerator // denominator
    )
    if steps <= 0:
        raise ValueError("execution price rounds to zero at configured increment")
    return _multiply(Decimal(steps), config.price_increment)


def _fill(
    order: OrderIntent,
    candle: Candle,
    config: ExecutionConfig,
    now: datetime,
    *,
    reason: Literal["ENTRY", "STOP", "TARGET", "TIME"],
    phase: Literal["OPEN", "INTRABAR", "CLOSE"],
    reference: Decimal,
) -> Fill:
    side: Literal["BUY", "SELL"] = "BUY" if reason == "ENTRY" else "SELL"
    price = _execution_price(reference, side, config)
    notional = _multiply(order.quantity, price)
    fee = _multiply(
        notional, config.entry_fee_rate if side == "BUY" else config.exit_fee_rate
    )
    cash_delta = (
        _add(notional, fee).copy_negate() if side == "BUY" else _subtract(notional, fee)
    )
    return Fill(
        order_id=order.order_id,
        side=side,
        reason=reason,
        phase=phase,
        candle_open_time=candle.open_time,
        candle_close_time=candle.close_time,
        available_at=candle.available_at,
        recorded_at=now,
        reference_price=reference,
        execution_price=price,
        quantity=order.quantity,
        fee=fee,
        cash_delta=cash_delta,
    )


def _exit_fill(
    position: Position, candle: Candle, config: ExecutionConfig, now: datetime
) -> Fill | None:
    order = position.order
    reason: Literal["STOP", "TARGET", "TIME"]
    phase: Literal["OPEN", "INTRABAR", "CLOSE"]
    if candle.open <= order.stop_price:
        reason, phase, reference = "STOP", "OPEN", candle.open
    elif candle.open >= order.target_price:
        reason, phase, reference = "TARGET", "OPEN", candle.open
    elif candle.low <= order.stop_price:
        reason, phase, reference = "STOP", "INTRABAR", order.stop_price
    elif candle.high >= order.target_price:
        reason, phase, reference = "TARGET", "INTRABAR", order.target_price
    elif position.holding_bars >= order.max_holding_bars:
        reason, phase, reference = "TIME", "CLOSE", candle.close
    else:
        return None
    return _fill(
        order, candle, config, now, reason=reason, phase=phase, reference=reference
    )


class SimulatedBroker:
    """One pending OR open trade, with immutable snapshots and full fills only.

    Runtime supplies already-authorized terms and advances the gateway's clock.
    submit requires decision_at == gateway.now; no stale/backdated submission.
    process consumes due candles chronologically without advancing time. A candle
    unavailable at its expected close is missing even if first processed later.
    This prevents late data from changing terminal outcomes with call cadence.
    Each call commits once; exceptions leave the entire prior snapshot intact.
    """

    def __init__(
        self,
        gateway: PointInTimeDataGateway,
        config: ExecutionConfig,
        *,
        initial_cash: Decimal,
    ) -> None:
        self._gateway = gateway
        self._config = ExecutionConfig.model_validate(config.model_dump())
        self._snapshot = PortfolioSnapshot(initial_cash=initial_cash, cash=initial_cash)

    @property
    def snapshot(self) -> PortfolioSnapshot:
        return self._snapshot

    def submit(self, order: OrderIntent) -> PortfolioSnapshot:
        """Register terms once; identical repeats are no-ops, conflicts raise."""
        order = OrderIntent.model_validate(order.model_dump())
        state = self._snapshot
        for previous in state.orders:
            if previous.order_id == order.order_id:
                if previous != order:
                    raise ValueError("conflicting reuse of order_id")
                return state
        if state.data_gap is not None:
            raise ValueError("portfolio halted by unresolved data gap")
        if state.pending is not None or state.position is not None:
            raise ValueError("portfolio already has a pending or open trade")
        if order.decision_at != self._gateway.now:
            raise ValueError("decision_at must equal current gateway time")
        for value, increment, name in (
            (order.quantity, self._config.quantity_increment, "quantity"),
            (order.stop_price, self._config.price_increment, "stop_price"),
            (order.target_price, self._config.price_increment, "target_price"),
        ):
            if not _aligned(value, increment):
                raise ValueError(f"{name} must align to its increment")
        self._snapshot = PortfolioSnapshot.model_validate(
            state.model_dump() | {"pending": order, "orders": (*state.orders, order)}
        )
        return self._snapshot

    def process(self) -> PortfolioSnapshot:
        """Record due fills or a terminal missing-data outcome, atomically."""
        state = self._snapshot
        if state.data_gap is not None:
            return state
        order = state.pending or (state.position.order if state.position else None)
        if order is None:
            return state
        now = self._gateway.now
        candles = {
            candle.open_time: candle
            for candle in self._gateway.read_candles(
                venue=order.venue, pair=order.pair, timeframe=order.timeframe
            )
        }
        while state.pending is not None or state.position is not None:
            expected_open = (
                state.position.last_candle_open_time + order.timeframe.duration
                if state.position
                else order.decision_at
            )
            completion = expected_open + order.timeframe.duration
            if completion > now:
                break
            candle = candles.get(expected_open)
            if candle is None or candle.available_at > completion:
                if state.pending:
                    state = self._reject(state, "MISSING_ENTRY_CANDLE", now)
                else:
                    state = PortfolioSnapshot.model_validate(
                        state.model_dump()
                        | {
                            "data_gap": DataGap(
                                order_id=order.order_id,
                                missing_open_time=expected_open,
                                expected_completion=completion,
                                detected_at=now,
                            )
                        }
                    )
                break
            if state.pending:
                entry = _fill(
                    order,
                    candle,
                    self._config,
                    now,
                    reason="ENTRY",
                    phase="OPEN",
                    reference=candle.open,
                )
                if _add(state.cash, entry.cash_delta) < 0:
                    state = self._reject(state, "UNAFFORDABLE", now)
                    break
                state = _enter(state, entry)
            elif state.position:
                state = PortfolioSnapshot.model_validate(
                    state.model_dump()
                    | {
                        "position": Position.model_validate(
                            state.position.model_dump()
                            | {
                                "holding_bars": state.position.holding_bars + 1,
                                "last_candle_open_time": candle.open_time,
                            }
                        )
                    }
                )
            assert state.position is not None
            exit_fill = _exit_fill(state.position, candle, self._config, now)
            if exit_fill is not None:
                state = _exit(state, exit_fill)
        self._snapshot = state
        return state

    @staticmethod
    def _reject(
        state: PortfolioSnapshot,
        reason: Literal["MISSING_ENTRY_CANDLE", "UNAFFORDABLE"],
        now: datetime,
    ) -> PortfolioSnapshot:
        assert state.pending is not None
        rejection = EntryRejection(
            order_id=state.pending.order_id, reason=reason, recorded_at=now
        )
        return PortfolioSnapshot.model_validate(
            state.model_dump()
            | {"pending": None, "rejections": (*state.rejections, rejection)}
        )
