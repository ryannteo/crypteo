from datetime import UTC, datetime, timedelta
from decimal import (
    ROUND_DOWN,
    Decimal,
    Inexact,
    Rounded,
    getcontext,
    localcontext,
)
from typing import Any

import pytest
from pydantic import ValidationError

from crypteo.domain.execution import ExecutionConfig, OrderIntent, PortfolioSnapshot
from crypteo.domain.market import Candle, Timeframe, TradingPair, Venue
from crypteo.execution import SimulatedBroker
from crypteo.temporal import PointInTimeDataGateway, SimulationClock

T = datetime(2026, 1, 1, 10, tzinfo=UTC)
HOUR = timedelta(hours=1)
D = Decimal


def config(**changes: Any) -> ExecutionConfig:
    return ExecutionConfig.model_validate(
        {
            "entry_fee_rate": "0.01",
            "exit_fee_rate": "0.02",
            "adverse_slippage": "0.01",
            "price_increment": "0.1",
            "quantity_increment": "0.5",
        }
        | changes
    )


def order(**changes: Any) -> OrderIntent:
    return OrderIntent.model_validate(
        {
            "order_id": "order-1",
            "venue": Venue.COINBASE,
            "pair": TradingPair.BTC_USD,
            "timeframe": Timeframe.ONE_HOUR,
            "decision_at": T,
            "quantity": "2",
            "stop_price": "90",
            "target_price": "120",
            "max_holding_bars": 3,
        }
        | changes
    )


def bar(
    hour: int = 0,
    *,
    open: str = "100",
    high: str = "110",
    low: str = "95",
    close: str = "105",
    delay: timedelta = timedelta(0),
    pair: TradingPair = TradingPair.BTC_USD,
) -> Candle:
    start = T + hour * HOUR
    return Candle(
        venue=Venue.COINBASE,
        pair=pair,
        timeframe=Timeframe.ONE_HOUR,
        open_time=start,
        close_time=start + HOUR,
        available_at=start + HOUR + delay,
        open=D(open),
        high=D(high),
        low=D(low),
        close=D(close),
        volume=D(0),  # Full fills deliberately ignore volume.
    )


def broker_for(
    candles: list[Candle],
    *,
    cash: str = "1000",
    settings: ExecutionConfig | None = None,
) -> tuple[SimulatedBroker, SimulationClock]:
    clock = SimulationClock(T)
    broker = SimulatedBroker(
        PointInTimeDataGateway(clock, candles),
        settings or config(),
        initial_cash=D(cash),
    )
    return broker, clock


def assert_reconciled(state: PortfolioSnapshot) -> None:
    assert PortfolioSnapshot.model_validate(state) == state
    assert PortfolioSnapshot.model_validate(state.model_dump()) == state
    assert PortfolioSnapshot.model_validate_json(state.model_dump_json()) == state
    assert state.cash >= 0
    assert state.position_quantity >= 0
    assert state.cash == state.initial_cash + sum(
        (fill.cash_delta for fill in state.fills), D(0)
    )
    assert state.cumulative_fees == sum((fill.fee for fill in state.fills), D(0))
    assert state.cumulative_net_realized_pnl == sum(
        (trade.net_pnl for trade in state.closed_trades), D(0)
    )
    for trade in state.closed_trades:
        entry, exit = trade.entry_fill, trade.exit_fill
        assert entry.quantity == exit.quantity == trade.order.quantity
        assert trade.gross_pnl == entry.quantity * (
            exit.execution_price - entry.execution_price
        )
        assert trade.net_pnl == trade.gross_pnl - entry.fee - exit.fee
        assert trade.net_pnl == entry.cash_delta + exit.cash_delta
    if state.position is None:
        assert state.position_cost_basis == 0
        assert state.position_quantity == 0
        assert state.cash - state.initial_cash == state.cumulative_net_realized_pnl
    else:
        entry = state.position.entry_fill
        assert state.position_cost_basis == -entry.cash_delta
        assert state.position_quantity == entry.quantity


def test_no_same_bar_entry_then_exact_next_open_with_costs() -> None:
    broker, clock = broker_for([bar(-1, open="104"), bar()])
    submitted = broker.submit(order())
    assert broker.process() == submitted
    clock.advance_to(T + HOUR - timedelta(microseconds=1))
    assert broker.process() == submitted
    assert clock.now == T + HOUR - timedelta(microseconds=1)

    clock.advance_to(T + HOUR)
    state = broker.process()
    assert state.pending is None
    assert state.cash == D("795.98")
    assert state.cumulative_fees == D("2.02")
    assert state.position is not None
    assert state.position.holding_bars == 1
    assert state.position.cost_basis == D("204.02")
    fill = state.fills[0]
    assert (fill.reference_price, fill.execution_price, fill.quantity) == (
        D(100),
        D(101),
        D(2),
    )
    assert fill.cash_delta == D("-204.02")
    assert fill.phase == "OPEN"
    assert fill.market_time == fill.candle_open_time == T
    assert fill.candle_close_time == fill.available_at == fill.recorded_at == T + HOUR
    assert clock.now == T + HOUR
    assert_reconciled(state)


@pytest.mark.parametrize("delayed", [False, True])
@pytest.mark.parametrize("process_at_boundary", [False, True])
def test_missing_or_delayed_entry_is_terminal_without_rollover(
    delayed: bool, process_at_boundary: bool
) -> None:
    candles = [bar(1), bar(2)]
    if delayed:
        candles.append(bar(delay=timedelta(microseconds=1)))
    broker, clock = broker_for(candles)
    broker.submit(order())
    clock.advance_to(T + HOUR - timedelta(microseconds=1))
    assert broker.process().rejections == ()
    clock.advance_to(T + (HOUR if process_at_boundary else 3 * HOUR))
    rejected = broker.process()
    assert rejected.pending is None
    assert rejected.position is None
    assert rejected.fills == ()
    assert rejected.cash == 1000
    assert rejected.rejections[0].reason == "MISSING_ENTRY_CANDLE"
    assert len(rejected.rejections) == 1
    clock.advance_to(T + 4 * HOUR)
    assert broker.submit(order()) == rejected
    assert broker.process() == rejected
    assert_reconciled(rejected)


@pytest.mark.parametrize(
    ("cash", "accepted"), [("204.02", True), ("204.019999", False), ("0", False)]
)
def test_affordability_includes_fee_and_rejects_without_resizing(
    cash: str, accepted: bool
) -> None:
    broker, clock = broker_for([bar()], cash=cash)
    broker.submit(order())
    clock.advance_to(T + HOUR)
    state = broker.process()
    if accepted:
        assert state.cash == 0
        assert state.position_quantity == 2
    else:
        assert state.cash == D(cash)
        assert state.cumulative_fees == 0
        assert state.fills == ()
        assert state.position is None
        assert state.pending is None
        assert state.rejections[0].reason == "UNAFFORDABLE"
    assert broker.process() == state
    assert_reconciled(state)


@pytest.mark.parametrize("field", ["quantity", "stop_price", "target_price"])
def test_misaligned_terms_fail_atomically_without_consuming_id(field: str) -> None:
    broker, _ = broker_for([])
    before = broker.snapshot
    invalid = {"quantity": "2.1", "stop_price": "90.01", "target_price": "120.01"}
    with pytest.raises(ValueError, match=f"{field} must align"):
        broker.submit(order(**{field: invalid[field]}))
    assert broker.snapshot is before
    assert broker.submit(order()).pending == order()


def test_non_power_of_ten_increments_round_buy_up_and_sell_down() -> None:
    broker, clock = broker_for(
        [bar(open="100.01", close="105.01")],
        settings=config(price_increment="0.25", quantity_increment="0.25"),
    )
    broker.submit(order(quantity="1.25", max_holding_bars=1))
    clock.advance_to(T + HOUR)
    state = broker.process()
    buy, sell = state.fills
    assert buy.execution_price == D("101.25")
    assert sell.execution_price == D("103.75")
    assert buy.fee == D("1.265625")
    assert sell.fee == D("2.59375")
    assert state.closed_trades[0].gross_pnl == D("3.125")
    assert state.closed_trades[0].net_pnl == D("-0.734375")
    assert state.cash == D("999.265625")
    assert_reconciled(state)


@pytest.mark.parametrize(
    ("prices", "reason", "phase", "reference", "execution", "net"),
    [
        ({"low": "90"}, "STOP", "INTRABAR", "90", "89.1", "-29.384"),
        ({"high": "120"}, "TARGET", "INTRABAR", "120", "118.8", "28.828"),
        (
            {"open": "80", "low": "75", "high": "100", "close": "95"},
            "STOP",
            "OPEN",
            "80",
            "79.2",
            "-48.788",
        ),
        (
            {"open": "130", "high": "135", "low": "125", "close": "132"},
            "TARGET",
            "OPEN",
            "130",
            "128.7",
            "48.232",
        ),
        (
            {"low": "80", "high": "125"},
            "STOP",
            "INTRABAR",
            "90",
            "89.1",
            "-29.384",
        ),
        (
            {"open": "130", "high": "135", "low": "80", "close": "100"},
            "TARGET",
            "OPEN",
            "130",
            "128.7",
            "48.232",
        ),
        (
            {"open": "80", "high": "135", "low": "75", "close": "100"},
            "STOP",
            "OPEN",
            "80",
            "79.2",
            "-48.788",
        ),
        ({}, "TIME", "CLOSE", "105", "103.9", "-0.376"),
    ],
)
def test_exit_scenarios_and_final_bar_priority(
    prices: dict[str, Any],
    reason: str,
    phase: str,
    reference: str,
    execution: str,
    net: str,
) -> None:
    broker, clock = broker_for([bar(), bar(1, **prices)])
    broker.submit(order(max_holding_bars=2))
    clock.advance_to(T + HOUR)
    opened = broker.process()
    assert opened.position is not None
    clock.advance_to(T + 2 * HOUR)
    state = broker.process()
    trade = state.closed_trades[0]
    fill = trade.exit_fill
    assert fill.reason == reason
    assert fill.phase == phase
    assert fill.reference_price == D(reference)
    assert fill.execution_price == D(execution)
    assert trade.net_pnl == D(net)
    assert trade.holding_bars == 2
    assert fill.market_time == (
        None if phase == "INTRABAR" else T + (2 * HOUR if phase == "CLOSE" else HOUR)
    )
    assert fill.available_at == fill.recorded_at == T + 2 * HOUR
    assert state.fills[:1] == opened.fills
    assert_reconciled(state)


@pytest.mark.parametrize(
    ("prices", "reason", "entry_price", "exit_price", "net"),
    [
        (
            {"open": "80", "low": "75", "high": "125", "close": "100"},
            "STOP",
            "80.8",
            "79.2",
            "-7.984",
        ),
        (
            {"open": "130", "low": "75", "high": "135", "close": "100"},
            "TARGET",
            "131.3",
            "128.7",
            "-12.974",
        ),
    ],
)
def test_entry_beyond_authorized_levels_enters_then_exits_same_open(
    prices: dict[str, Any], reason: str, entry_price: str, exit_price: str, net: str
) -> None:
    broker, clock = broker_for([bar(**prices)])
    broker.submit(order())
    clock.advance_to(T + HOUR)
    state = broker.process()
    entry, exit = state.fills
    assert entry.reason == "ENTRY"
    assert exit.reason == reason
    assert entry.phase == exit.phase == "OPEN"
    assert entry.market_time == exit.market_time == T
    assert entry.execution_price == D(entry_price)
    assert exit.execution_price == D(exit_price)
    assert entry.fee > 0 and exit.fee > 0
    assert state.closed_trades[0].holding_bars == 1
    assert state.closed_trades[0].net_pnl == D(net)
    assert_reconciled(state)


@pytest.mark.parametrize("holding_bars", [1, 3])
def test_time_exit_counts_entry_candle_as_one(holding_bars: int) -> None:
    broker, clock = broker_for([bar(i) for i in range(4)])
    broker.submit(order(max_holding_bars=holding_bars))
    for i in range(1, holding_bars):
        clock.advance_to(T + i * HOUR)
        assert broker.process().closed_trades == ()
    clock.advance_to(T + holding_bars * HOUR)
    state = broker.process()
    assert state.closed_trades[0].holding_bars == holding_bars
    assert state.fills[-1].reason == "TIME"
    assert state.fills[-1].market_time == T + holding_bars * HOUR
    assert state.cash == D("999.624")
    assert_reconciled(state)


@pytest.mark.parametrize("delay", [None, timedelta(microseconds=1)])
@pytest.mark.parametrize("process_at_boundary", [False, True])
def test_holding_gap_halts_without_inventing_an_exit_or_later_recovery(
    delay: timedelta | None, process_at_boundary: bool
) -> None:
    candles = [bar(), bar(2, low="80", high="125")]
    if delay is not None:
        candles.append(bar(1, low="80", delay=delay))
    broker, clock = broker_for(candles)
    broker.submit(order())
    clock.advance_to(T + HOUR)
    opened = broker.process()
    clock.advance_to(T + (2 if process_at_boundary else 3) * HOUR)
    halted = broker.process()
    assert halted.data_gap is not None
    assert halted.data_gap.missing_open_time == T + HOUR
    assert halted.data_gap.expected_completion == T + 2 * HOUR
    assert halted.data_gap.detected_at == clock.now
    assert halted.position == opened.position
    assert halted.cash == opened.cash
    assert halted.fills == opened.fills
    assert halted.closed_trades == ()
    clock.advance_to(T + 5 * HOUR)
    assert broker.process() is halted
    assert broker.submit(order()) is halted
    with pytest.raises(ValueError, match="halted"):
        broker.submit(order(order_id="new", decision_at=clock.now))
    assert broker.snapshot is halted
    assert_reconciled(halted)


def test_sequential_profitable_and_losing_trades_reconcile_and_are_idempotent() -> None:
    broker, clock = broker_for([bar(high="125"), bar(1, low="80")])
    first = order()
    submitted = broker.submit(first)
    assert broker.submit(first) is submitted
    clock.advance_to(T + HOUR)
    first_closed = broker.process()
    assert first_closed.cash == D("1028.828")
    assert broker.submit(first) is first_closed
    assert broker.process() is first_closed
    second = order(order_id="second", decision_at=clock.now)
    broker.submit(second)
    assert broker.submit(first).pending == second
    clock.advance_to(T + 2 * HOUR)
    state = broker.process()
    assert len(state.orders) == 2
    assert len(state.fills) == 4
    assert len(state.closed_trades) == 2
    assert state.cash == D("999.444")
    assert state.cumulative_fees == D("12.356")
    assert state.cumulative_net_realized_pnl == D("-0.556")
    assert state.closed_trades[:1] == first_closed.closed_trades
    assert broker.process() is state
    assert broker.submit(second) is state
    assert_reconciled(state)


@pytest.mark.parametrize("stage", ["pending", "open", "closed", "rejected"])
def test_conflicting_order_id_fails_explicitly_at_every_stage(stage: str) -> None:
    broker, clock = broker_for(
        [] if stage == "rejected" else [bar(high="125" if stage == "closed" else "110")]
    )
    broker.submit(order())
    if stage != "pending":
        clock.advance_to(T + HOUR)
        broker.process()
    before = broker.snapshot
    with pytest.raises(ValueError, match="conflicting reuse"):
        broker.submit(order(quantity="1"))
    assert broker.snapshot is before


@pytest.mark.parametrize("opened", [False, True])
def test_only_one_pending_or_open_trade_without_permanently_banning_next_trade(
    opened: bool,
) -> None:
    broker, clock = broker_for([bar(), bar(1, high="125")])
    broker.submit(order())
    if opened:
        clock.advance_to(T + HOUR)
        broker.process()
    before = broker.snapshot
    with pytest.raises(ValueError, match="pending or open"):
        broker.submit(order(order_id="second", decision_at=clock.now))
    assert broker.snapshot is before
    clock.advance_to(T + 2 * HOUR)
    broker.process()
    assert broker.submit(order(order_id="second", decision_at=clock.now)).pending


@pytest.mark.parametrize("offset", [-1, 1])
def test_no_stale_or_future_submission(offset: int) -> None:
    broker, _ = broker_for([bar(-1), bar()])
    before = broker.snapshot
    with pytest.raises(ValueError, match="current gateway time"):
        broker.submit(order(decision_at=T + offset * HOUR))
    assert broker.snapshot is before


def test_late_processing_keeps_market_availability_and_recording_times_separate() -> (
    None
):
    broker, clock = broker_for([bar(), bar(1, high="125")])
    broker.submit(order())
    clock.advance_to(T + 4 * HOUR)
    state = broker.process()
    assert state.fills[0].market_time == T
    assert state.fills[0].available_at == T + HOUR
    assert state.fills[1].available_at == T + 2 * HOUR
    assert all(fill.recorded_at == T + 4 * HOUR for fill in state.fills)
    assert clock.now == T + 4 * HOUR
    assert state.data_gap is None  # Already flat before later missing candles.
    assert_reconciled(state)


def run_scenario() -> PortfolioSnapshot:
    broker, clock = broker_for([bar(), bar(1, high="125")])
    broker.submit(order())
    clock.advance_to(T + HOUR)
    broker.process()
    clock.advance_to(T + 2 * HOUR)
    return broker.process()


def test_repeated_runs_have_identical_serialized_results() -> None:
    results = [run_scenario().model_dump_json() for _ in range(3)]
    assert results[0] == results[1] == results[2]
    assert PortfolioSnapshot.model_validate_json(results[0]) == run_scenario()


def test_hostile_decimal_context_cannot_change_execution_or_caller_context() -> None:
    expected = run_scenario()
    with localcontext() as hostile:
        hostile.prec = 2
        hostile.rounding = ROUND_DOWN
        hostile.Emax = 2
        hostile.Emin = -2
        hostile.clamp = 1
        hostile.traps[Inexact] = True
        hostile.traps[Rounded] = True
        hostile.clear_flags()
        before = hostile.copy()
        assert run_scenario() == expected
        after = getcontext()
        for field in ("prec", "rounding", "Emax", "Emin", "clamp", "flags", "traps"):
            assert getattr(after, field) == getattr(before, field)


def test_tiny_fees_against_large_cash_are_not_rounded_away() -> None:
    cash = "1" + "0" * 100
    broker, clock = broker_for(
        [bar(high="125")],
        cash=cash,
        settings=config(entry_fee_rate="0.000000000000000000000000000001"),
    )
    broker.submit(order())
    clock.advance_to(T + HOUR)
    state = broker.process()
    assert state.fills[0].fee == D("0.000000000000000000000000000202")
    assert state.closed_trades[0].net_pnl == D("30.847999999999999999999999999798")
    with localcontext() as context:
        context.prec = 200
        assert_reconciled(state)


def test_planted_future_and_other_market_prices_cannot_affect_current_state() -> None:
    ordinary, clock_a = broker_for([bar()])
    planted, clock_b = broker_for(
        [
            bar(),
            bar(1, open="100000", high="999999", low="1", close="2"),
            bar(0, open="1", low="1", high="2", close="2", pair=TradingPair.ETH_USD),
        ]
    )
    for broker, clock in ((ordinary, clock_a), (planted, clock_b)):
        broker.submit(order())
        assert broker.process().fills == ()
        clock.advance_to(T + HOUR)
        broker.process()
    assert ordinary.snapshot == planted.snapshot


def test_snapshots_are_immutable_and_preserve_prior_state() -> None:
    broker, clock = broker_for([bar(high="125")])
    initial = broker.snapshot
    submitted = broker.submit(order())
    clock.advance_to(T + HOUR)
    closed = broker.process()
    assert initial.orders == ()
    assert initial.fills == ()
    assert submitted.pending is not None and submitted.fills == ()
    assert closed.pending is None and len(closed.fills) == 2
    with pytest.raises(ValidationError):
        closed.cash = D(0)
    with pytest.raises(ValidationError):
        closed.fills[0].fee = D(0)


def test_zero_rounded_sell_price_rejects_entire_entry_exit_call_atomically() -> None:
    broker, clock = broker_for(
        [bar(open="0.1", low="0.1", high="0.2", close="0.2")],
        settings=config(
            entry_fee_rate="0",
            exit_fee_rate="0",
            adverse_slippage="0",
            price_increment="1",
        ),
    )
    broker.submit(order(stop_price="1", target_price="2"))
    clock.advance_to(T + HOUR)
    before = broker.snapshot
    for _ in range(2):
        with pytest.raises(ValueError, match="execution price rounds to zero"):
            broker.process()
        assert broker.snapshot is before
    assert before.fills == ()
    assert before.cash == 1000
    assert before.cumulative_fees == 0
    assert_reconciled(before)


@pytest.mark.parametrize("open_price,reason", [("90", "STOP"), ("120", "TARGET")])
def test_open_exactly_at_trigger_has_opening_priority(
    open_price: str, reason: str
) -> None:
    broker, clock = broker_for(
        [bar(), bar(1, open=open_price, low="80", high="130", close="100")]
    )
    broker.submit(order())
    clock.advance_to(T + 2 * HOUR)
    state = broker.process()
    assert state.fills[-1].reason == reason
    assert state.fills[-1].phase == "OPEN"
    assert state.fills[-1].reference_price == D(open_price)
    assert_reconciled(state)


def test_duplicate_processing_while_open_does_not_count_another_holding_bar() -> None:
    broker, clock = broker_for([bar(), bar(1), bar(2)])
    intent = order()
    broker.submit(intent)
    clock.advance_to(T + HOUR)
    opened = broker.process()
    for _ in range(3):
        assert broker.submit(intent) is opened
        assert broker.process() is opened
    assert opened.position is not None
    assert opened.position.holding_bars == 1
    clock.advance_to(T + 2 * HOUR)
    second = broker.process()
    assert second.position is not None and second.position.holding_bars == 2
    assert second.fills == opened.fills
    assert broker.process() is second


def test_processing_failure_rolls_back_entire_call_and_retry_charges_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from crypteo.domain.execution import Fill

    broker, clock = broker_for([bar(), bar(1, high="125")])
    submitted = broker.submit(order())
    clock.advance_to(T + 2 * HOUR)

    def fail_exit(state: PortfolioSnapshot, fill: Fill) -> PortfolioSnapshot:
        assert state.position is not None
        assert state.position.holding_bars == 2
        assert len(state.fills) == 1
        raise ArithmeticError("injected failure after entry and holding updates")

    with monkeypatch.context() as patch:
        patch.setattr("crypteo.execution._exit", fail_exit)
        with pytest.raises(ArithmeticError, match="injected failure"):
            broker.process()
    assert broker.snapshot is submitted
    assert submitted.fills == () and submitted.cash == 1000
    state = broker.process()
    assert len(state.fills) == 2
    assert state.cash == D("1028.828")
    assert broker.process() is state
    assert_reconciled(state)


def test_rejected_entry_does_not_prevent_a_new_authorized_order() -> None:
    broker, clock = broker_for([bar(1)])
    broker.submit(order())
    clock.advance_to(T + HOUR)
    rejected = broker.process()
    assert rejected.rejections[0].reason == "MISSING_ENTRY_CANDLE"
    broker.submit(order(order_id="second", decision_at=clock.now, max_holding_bars=1))
    clock.advance_to(T + 2 * HOUR)
    state = broker.process()
    assert len(state.closed_trades) == 1
    assert state.closed_trades[0].order.order_id == "second"
    assert state.rejections == rejected.rejections
    assert_reconciled(state)


def test_zero_rounded_sell_preserves_previously_open_position_atomically() -> None:
    broker, clock = broker_for(
        [bar(), bar(1, open="0.1", low="0.1", high="0.2", close="0.2")],
        settings=config(price_increment="1", adverse_slippage="0"),
    )
    broker.submit(order())
    clock.advance_to(T + HOUR)
    opened = broker.process()
    assert opened.position is not None
    clock.advance_to(T + 2 * HOUR)
    for _ in range(2):
        with pytest.raises(ValueError, match="execution price rounds to zero"):
            broker.process()
        assert broker.snapshot is opened
    assert opened.position.holding_bars == 1
    assert len(opened.fills) == 1
    assert opened.closed_trades == ()
    assert_reconciled(opened)


def test_transition_record_validation_failure_preserves_previous_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from crypteo import execution
    from crypteo.domain.execution import Fill, Position

    broker, clock = broker_for([bar(), bar(1, high="125")])
    broker.submit(order())
    clock.advance_to(T + HOUR)
    opened = broker.process()
    clock.advance_to(T + 2 * HOUR)
    original_exit_fill = execution._exit_fill

    def invalid_exit_fill(
        position: Position,
        candle: Candle,
        config: ExecutionConfig,
        now: datetime,
    ) -> Fill | None:
        fill = original_exit_fill(position, candle, config, now)
        assert fill is not None
        return fill.model_copy(update={"cash_delta": D("999")})

    with monkeypatch.context() as patch:
        patch.setattr(execution, "_exit_fill", invalid_exit_fill)
        with pytest.raises(ValidationError, match="cash_delta does not reconcile"):
            broker.process()
    assert broker.snapshot is opened
    closed = broker.process()
    assert closed.cash == D("1028.828")
    assert len(closed.fills) == 2
    assert_reconciled(closed)
