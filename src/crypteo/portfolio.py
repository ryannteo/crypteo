"""Pure, atomic portfolio ledger transitions."""

from crypteo.domain._execution_arithmetic import _add, _multiply, _subtract
from crypteo.domain.execution import ClosedTrade, Fill, PortfolioSnapshot, Position


def _enter(snapshot: PortfolioSnapshot, fill: Fill) -> PortfolioSnapshot:
    snapshot = PortfolioSnapshot.model_validate(snapshot)
    fill = Fill.model_validate(fill)
    order = snapshot.pending
    if order is None or snapshot.position is not None:
        raise ValueError("entry requires exactly one pending order and no position")
    if fill.side != "BUY" or fill.order_id != order.order_id:
        raise ValueError("entry fill must buy the pending order")
    if fill.quantity != order.quantity:
        raise ValueError("entry must fill the full authorized quantity")
    cost = _add(_multiply(fill.quantity, fill.execution_price), fill.fee)
    if fill.cash_delta != cost.copy_negate():
        raise ValueError("entry cash delta does not reconcile")
    cash = _add(snapshot.cash, fill.cash_delta)
    if cash < 0:
        raise ValueError("unaffordable entry")
    position = Position(
        order=order,
        entry_fill=fill,
        quantity=fill.quantity,
        cost_basis=cost,
        holding_bars=1,
        last_candle_open_time=fill.candle_open_time,
    )
    return PortfolioSnapshot.model_validate(
        snapshot.model_dump()
        | {
            "pending": None,
            "position": position,
            "cash": cash,
            "cumulative_fees": _add(snapshot.cumulative_fees, fill.fee),
            "fills": (*snapshot.fills, fill),
        }
    )


def _exit(snapshot: PortfolioSnapshot, fill: Fill) -> PortfolioSnapshot:
    snapshot = PortfolioSnapshot.model_validate(snapshot)
    fill = Fill.model_validate(fill)
    position = snapshot.position
    if position is None or snapshot.pending is not None:
        raise ValueError("exit requires an owned position and no pending order")
    if fill.side != "SELL" or fill.order_id != position.order.order_id:
        raise ValueError("exit fill must sell the owned order")
    if fill.quantity != position.quantity:
        raise ValueError("exit quantity must equal owned quantity")
    proceeds = _subtract(_multiply(fill.quantity, fill.execution_price), fill.fee)
    if fill.cash_delta != proceeds:
        raise ValueError("exit cash delta does not reconcile")
    gross = _multiply(
        fill.quantity,
        _subtract(fill.execution_price, position.entry_fill.execution_price),
    )
    net = _subtract(_subtract(gross, position.entry_fill.fee), fill.fee)
    if net != _subtract(proceeds, position.cost_basis):
        raise ValueError("closed trade cost basis does not reconcile")
    cash = _add(snapshot.cash, proceeds)
    realized = _add(snapshot.cumulative_net_realized_pnl, net)
    if cash < 0 or _subtract(cash, snapshot.initial_cash) != realized:
        raise ValueError("flat cash and realized PnL do not reconcile")
    trade = ClosedTrade(
        order=position.order,
        entry_fill=position.entry_fill,
        exit_fill=fill,
        holding_bars=position.holding_bars,
        gross_pnl=gross,
        net_pnl=net,
    )
    return PortfolioSnapshot.model_validate(
        snapshot.model_dump()
        | {
            "position": None,
            "cash": cash,
            "cumulative_fees": _add(snapshot.cumulative_fees, fill.fee),
            "cumulative_net_realized_pnl": realized,
            "fills": (*snapshot.fills, fill),
            "closed_trades": (*snapshot.closed_trades, trade),
        }
    )
