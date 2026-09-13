from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from crypteo.domain.execution import Fill, OrderIntent, PortfolioSnapshot
from crypteo.domain.market import Timeframe, TradingPair, Venue
from crypteo.portfolio import _enter, _exit

D = Decimal
T = datetime(2026, 1, 1, tzinfo=UTC)


def pending() -> PortfolioSnapshot:
    order = OrderIntent(
        order_id="one",
        venue=Venue.COINBASE,
        pair=TradingPair.BTC_USD,
        timeframe=Timeframe.ONE_HOUR,
        decision_at=T,
        quantity=D(2),
        stop_price=D(90),
        target_price=D(120),
        max_holding_bars=2,
    )
    return PortfolioSnapshot(
        initial_cash=D(1000), cash=D(1000), pending=order, orders=(order,)
    )


def entry() -> Fill:
    return Fill(
        order_id="one",
        side="BUY",
        reason="ENTRY",
        phase="OPEN",
        candle_open_time=T,
        candle_close_time=T + timedelta(hours=1),
        available_at=T + timedelta(hours=1),
        recorded_at=T + timedelta(hours=1),
        reference_price=D(100),
        execution_price=D(101),
        quantity=D(2),
        fee=D("2.02"),
        cash_delta=D("-204.02"),
    )


def exit_fill() -> Fill:
    return Fill.model_validate(
        entry().model_dump()
        | {
            "side": "SELL",
            "reason": "TARGET",
            "phase": "INTRABAR",
            "reference_price": D(120),
            "execution_price": D("118.8"),
            "fee": D("4.752"),
            "cash_delta": D("232.848"),
        }
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", D(1)),
        ("order_id", "other"),
        ("side", "SELL"),
        ("cash_delta", D("-202")),
    ],
)
def test_bad_entry_ledger_transition_is_atomic(field: str, value: object) -> None:
    state = pending()
    before = state.model_dump_json()
    fill = entry().model_copy(update={field: value})
    with pytest.raises(ValueError):
        _enter(state, fill)
    assert state.model_dump_json() == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", D(1)),
        ("quantity", D(3)),
        ("order_id", "other"),
        ("side", "BUY"),
        ("cash_delta", D("237.6")),
    ],
)
def test_bad_exit_ledger_transition_is_atomic(field: str, value: object) -> None:
    state = _enter(pending(), entry())
    before = state.model_dump_json()
    fill = exit_fill().model_copy(update={field: value})
    with pytest.raises(ValueError):
        _exit(state, fill)
    assert state.model_dump_json() == before


def test_ledger_full_lifecycle_and_illegal_repeated_transitions() -> None:
    original = pending()
    opened = _enter(original, entry())
    closed = _exit(opened, exit_fill())
    assert original.cash == 1000 and original.fills == ()
    assert opened.cash == D("795.98")
    assert opened.position_cost_basis == D("204.02")
    assert closed.cash == D("1028.828")
    assert closed.cumulative_fees == D("6.772")
    assert closed.closed_trades[0].gross_pnl == D("35.6")
    assert closed.closed_trades[0].net_pnl == D("28.828")
    assert closed.position_quantity == closed.position_cost_basis == 0
    with pytest.raises(ValueError):
        _enter(opened, entry())
    with pytest.raises(ValueError):
        _exit(closed, exit_fill())


def test_unaffordable_ledger_entry_preserves_pending_cash_and_history() -> None:
    state = pending().model_copy(update={"initial_cash": D(200), "cash": D(200)})
    with pytest.raises(ValueError, match="unaffordable"):
        _enter(state, entry())
    assert state.cash == 200 and state.fills == () and state.pending is not None
