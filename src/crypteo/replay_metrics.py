"""Core scanner-only replay metrics from observed marks and the actual ledger."""

from collections import Counter
from decimal import (
    MAX_EMAX,
    MIN_EMIN,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
)
from fractions import Fraction

from crypteo.domain._execution_arithmetic import _subtract
from crypteo.domain.execution import Fill, PortfolioSnapshot
from crypteo.domain.market import Timeframe
from crypteo.domain.replay import BoundaryObservation, ReplayMetrics, ReplayRecord
from crypteo.domain.replay_research import (
    ReplayResearchRecord,
    ResearchReplayMetrics,
    RiskRejectionCount,
)
from crypteo.domain.risk import RiskRejection
from crypteo.experiment import ReplayConfig


def _decimal_ratio(value: Fraction) -> Decimal:
    return Context(
        prec=50,
        rounding=ROUND_HALF_EVEN,
        Emin=MIN_EMIN,
        Emax=MAX_EMAX,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[DivisionByZero, InvalidOperation, Overflow],
    ).divide(Decimal(value.numerator), Decimal(value.denominator))


def _ratio(numerator: Decimal | int, denominator: Decimal | int) -> Decimal | None:
    return (
        _decimal_ratio(Fraction(numerator) / Fraction(denominator))
        if denominator
        else None
    )


def calculate_metrics(
    config: ReplayConfig,
    state: PortfolioSnapshot,
    observations: tuple[BoundaryObservation, ...],
) -> ReplayMetrics:
    peak = config.initial_cash
    drawdown = Fraction(0)
    valid_through = config.start
    for observation in observations:
        equity = observation.marked_equity
        if equity is None:
            break
        peak = max(peak, equity)
        if peak:
            drawdown = max(drawdown, Fraction(_subtract(peak, equity)) / Fraction(peak))
        valid_through = observation.at
    complete = state.data_gap is None
    ending = observations[-1].marked_equity
    trades = state.closed_trades
    wins = sum(trade.net_pnl > 0 for trade in trades)
    known_bars = sum(trade.holding_bars for trade in trades)
    if state.position is not None:
        known_bars += state.position.holding_bars
    scheduled = (config.end - config.start) // Timeframe.ONE_HOUR.duration
    prefix_drawdown = _decimal_ratio(drawdown)
    return ReplayMetrics(
        starting_marked_equity=config.initial_cash,
        ending_marked_equity=ending,
        realized_net_pnl=state.cumulative_net_realized_pnl,
        realized_return=_ratio(state.cumulative_net_realized_pnl, config.initial_cash),
        marked_return=_ratio(
            _subtract(ending, config.initial_cash), config.initial_cash
        )
        if ending is not None
        else None,
        maximum_drawdown=prefix_drawdown if complete else None,
        valid_prefix_maximum_drawdown=prefix_drawdown,
        valuation_valid_through=valid_through,
        entry_count=sum(fill.reason == "ENTRY" for fill in state.fills),
        closed_trade_count=len(trades),
        wins=wins,
        losses=sum(trade.net_pnl < 0 for trade in trades),
        breakevens=sum(trade.net_pnl == 0 for trade in trades),
        win_rate=_ratio(wins, len(trades)),
        actual_fees=state.cumulative_fees,
        scheduled_bars=scheduled,
        known_exposure_bars=known_bars,
        hourly_bar_exposure=_ratio(known_bars, scheduled) if complete else None,
        stop_count=sum(trade.exit_fill.reason == "STOP" for trade in trades),
        target_count=sum(trade.exit_fill.reason == "TARGET" for trade in trades),
        time_count=sum(trade.exit_fill.reason == "TIME" for trade in trades),
        missing_entry_candle_count=sum(
            r.reason == "MISSING_ENTRY_CANDLE" for r in state.rejections
        ),
        valuation_complete=complete,
        exposure_complete=complete,
        open_position=state.position is not None,
        unresolved_data_gap=not complete,
    )


def calculate_research_metrics(
    records: tuple[ReplayRecord, ...],
) -> ResearchReplayMetrics:
    """Factual counts over the validated research replay lifecycle."""
    assessments = tuple(
        r.payload for r in records if isinstance(r.payload, ReplayResearchRecord)
    )
    trades = sum(r.outcome == "TRADE" for r in assessments)
    reasons = Counter(
        r.payload.reason for r in records if isinstance(r.payload, RiskRejection)
    )
    entries = sum(
        isinstance(r.payload, Fill) and r.payload.reason == "ENTRY" for r in records
    )
    return ResearchReplayMetrics(
        opportunities_assessed=len(assessments),
        trade_count=trades,
        no_trade_count=sum(r.outcome == "NO_TRADE" for r in assessments),
        abstain_count=sum(r.outcome == "ABSTAIN" for r in assessments),
        terms_rejection_count=sum(r.kind == "TERMS_REJECTION" for r in records),
        risk_rejection_count=sum(reasons.values()),
        risk_rejection_reasons=tuple(
            RiskRejectionCount(reason=key, count=reasons[key])
            for key in sorted(reasons)
        ),
        submission_count=sum(r.kind == "SUBMISSION" for r in records),
        entry_count=entries,
        research_to_entry_conversion=_ratio(entries, len(assessments)),
        trade_to_entry_conversion=_ratio(entries, trades),
    )
