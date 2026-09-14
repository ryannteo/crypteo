"""Stateless decision-time LONG risk authorization and exact position sizing."""

from decimal import Decimal
from functools import partial

from crypteo._execution_costs import (
    _aligned,
    _execution_price,
    _increment_ratio,
    _UnexecutablePrice,
)
from crypteo.domain._execution_arithmetic import _add, _multiply, _subtract
from crypteo.domain.execution import ExecutionConfig, OrderIntent, PortfolioSnapshot
from crypteo.domain.risk import (
    RiskAuthorization,
    RiskConfig,
    RiskRejection,
    TradeProposal,
)
from crypteo.temporal import PointInTimeDataGateway


class RiskService:
    """Authorize estimates from the exact current completed candle's close.

    The caller owns clock advancement and submission. Authorization neither
    reserves cash nor consumes IDs. Actual next-open affordability and gapped
    exit losses remain Slice 8 execution outcomes, without resizing.
    """

    def __init__(
        self,
        gateway: PointInTimeDataGateway,
        risk_config: RiskConfig,
        execution_config: ExecutionConfig,
    ) -> None:
        self._gateway = gateway
        self._risk_config = RiskConfig.model_validate(risk_config.model_dump())
        self._execution_config = ExecutionConfig.model_validate(
            execution_config.model_dump()
        )

    def authorize(
        self, proposal: TradeProposal, portfolio: PortfolioSnapshot
    ) -> RiskAuthorization | RiskRejection:
        """Raise on bad inputs; otherwise return a stable authorization/rejection.

        Input checks precede ordinary rejections. Rejection precedence is halted,
        reused ID, busy, missing reference, invalid levels, unexecutable stop,
        no capital, then quantity below one increment.
        """
        proposal = TradeProposal.model_validate(proposal.model_dump())
        portfolio = PortfolioSnapshot.model_validate(portfolio.model_dump())
        now = self._gateway.now
        if proposal.decision_at != now:
            raise ValueError("decision_at must equal current gateway time")
        if (
            any(order.decision_at > now for order in portfolio.orders)
            or any(fill.recorded_at > now for fill in portfolio.fills)
            or any(rejection.recorded_at > now for rejection in portfolio.rejections)
            or (portfolio.data_gap is not None and portfolio.data_gap.detected_at > now)
            or (
                portfolio.position is not None
                and portfolio.position.last_candle_open_time
                + portfolio.position.order.timeframe.duration
                > now
            )
        ):
            raise ValueError("portfolio contains history after current gateway time")
        config = self._execution_config
        for name in ("stop_price", "target_price"):
            if not _aligned(getattr(proposal, name), config.price_increment):
                raise ValueError(f"{name} must align to price_increment")

        reject = partial(
            RiskRejection,
            proposal=proposal,
            risk_config=self._risk_config,
            execution_config=config,
        )
        if portfolio.data_gap is not None:
            return reject(reason="PORTFOLIO_HALTED")
        if any(order.order_id == proposal.order_id for order in portfolio.orders):
            return reject(reason="REUSED_ORDER_ID")
        if portfolio.pending is not None or portfolio.position is not None:
            return reject(reason="PORTFOLIO_BUSY")

        reference = next(
            (
                candle.close
                for candle in self._gateway.read_candles(
                    venue=proposal.venue,
                    pair=proposal.pair,
                    timeframe=proposal.timeframe,
                )
                if candle.close_time == proposal.decision_at
            ),
            None,
        )
        if reference is None:
            return reject(reason="REFERENCE_UNAVAILABLE")
        if not 0 < proposal.stop_price < reference < proposal.target_price:
            return reject(reason="INVALID_LONG_LEVELS")

        entry = _execution_price(reference, "BUY", config)
        try:
            stop = _execution_price(proposal.stop_price, "SELL", config)
        except _UnexecutablePrice:
            return reject(reason="UNEXECUTABLE_STOP")
        cash_per_unit = _multiply(entry, _add(Decimal(1), config.entry_fee_rate))
        loss_per_unit = _subtract(
            cash_per_unit, _multiply(stop, _subtract(Decimal(1), config.exit_fee_rate))
        )
        capital = portfolio.cash
        if capital == 0:
            return reject(reason="NO_CAPITAL")
        budget = _multiply(capital, self._risk_config.risk_fraction)

        # Count whole increments via exact integer ratios. Decimal division may
        # repeat or round across a boundary even with very generous precision.
        risk_n, risk_d = _increment_ratio(
            budget, _multiply(loss_per_unit, config.quantity_increment)
        )
        cash_n, cash_d = _increment_ratio(
            capital, _multiply(cash_per_unit, config.quantity_increment)
        )
        steps = min(risk_n // risk_d, cash_n // cash_d)
        if steps < 1:
            return reject(reason="QUANTITY_BELOW_INCREMENT")
        quantity = _multiply(Decimal(steps), config.quantity_increment)
        return RiskAuthorization(
            proposal=proposal,
            order=OrderIntent(
                **proposal.model_dump(),
                quantity=quantity,
                max_holding_bars=self._risk_config.max_holding_bars,
            ),
            risk_config=self._risk_config,
            execution_config=config,
            reference_price=reference,
            capital_basis=capital,
            risk_budget=budget,
            estimated_entry_price=entry,
            estimated_stop_execution_price=stop,
            estimated_loss_per_unit=loss_per_unit,
            authorized_quantity=quantity,
            estimated_entry_cash=_multiply(quantity, cash_per_unit),
            estimated_total_loss=_multiply(quantity, loss_per_unit),
        )
