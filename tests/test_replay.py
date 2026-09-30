"""Temporal, lifecycle and independent financial goldens for scanner-only replay."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_UP, Decimal, localcontext
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from crypteo.coinbase_dataset import (
    COINBASE_DATASET_SOURCE,
    CoinbaseCanonicalDataset,
    _build_dataset_version,
)
from crypteo.domain.execution import ExecutionConfig, OrderIntent, PortfolioSnapshot
from crypteo.domain.market import Candle, DatasetManifest, Timeframe, TradingPair, Venue
from crypteo.domain.replay import (
    BoundaryObservation,
    Delivery,
    ReplayMetrics,
    ReplayResult,
)
from crypteo.domain.research import Opportunity
from crypteo.domain.risk import (
    RiskAuthorization,
    RiskConfig,
    RiskRejection,
    TradeProposal,
)
from crypteo.domain.trade_terms import TradeTerms, TradeTermsConfig, TradeTermsRejection
from crypteo.execution import SimulatedBroker
from crypteo.experiment import CodeIdentity, ReplayConfig
from crypteo.replay import run_scanner_only
from crypteo.replay_metrics import calculate_metrics
from crypteo.risk import RiskService
from crypteo.scanner import OpportunityScanner
from crypteo.temporal import PointInTimeDataGateway, SimulationClock
from crypteo.trade_terms import TradeTermsService

D = Decimal
T = datetime(2023, 1, 2, tzinfo=UTC)
H = timedelta(hours=1)
BTC, ETH, SOL = TradingPair
CODE = CodeIdentity(git_commit="a" * 40, source_digest="sha256:" + "b" * 64)


def bar(hour: int, price: str = "100", **changes: Any) -> Candle:
    return Candle.model_validate(
        dict(
            venue=Venue.COINBASE,
            pair=BTC,
            timeframe=Timeframe.ONE_HOUR,
            open_time=T + hour * H,
            close_time=T + (hour + 1) * H,
            available_at=T + (hour + 1) * H,
            open=price,
            close=price,
            high=str(D(price) + 1),
            low=str(D(price) - 1),
            volume="1",
        )
        | changes
    )


def history() -> tuple[Candle, ...]:
    return tuple(bar(i) for i in range(-21, -1)) + (bar(-1, "90"),)


def dataset(
    candles: tuple[Candle, ...] | None = None,
    *,
    pair: TradingPair = BTC,
) -> CoinbaseCanonicalDataset:
    candles = tuple(
        Candle.model_validate(c.model_dump() | {"pair": pair})
        for c in (history() if candles is None else candles)
    )
    ordered = tuple(sorted(candles, key=lambda c: c.open_time))
    start, end = (
        (ordered[0].open_time, ordered[-1].close_time) if ordered else (None, None)
    )
    missing = tuple(
        T + hour * H
        for hour in range(-100, 100)
        if start is not None
        and end is not None
        and start < T + hour * H < end - H
        and T + hour * H not in {c.open_time for c in ordered}
    )
    version = _build_dataset_version(
        pair=pair,
        start_time=start,
        end_time=end,
        missing_open_times=missing,
        candles=ordered,
    )
    manifest = DatasetManifest(
        schema_version="1",
        dataset_version=version,
        source=COINBASE_DATASET_SOURCE,
        venue=Venue.COINBASE,
        pair=pair,
        timeframe=Timeframe.ONE_HOUR,
        start_time=start,
        end_time=end,
        created_at=T + 100 * H,
        row_count=len(candles),
        missing_open_times=missing,
    )
    return CoinbaseCanonicalDataset(candles=candles, manifest=manifest)


def config(**changes: Any) -> ReplayConfig:
    return ReplayConfig.model_validate(
        dict(
            start=T,
            end=T + 3 * H,
            initial_cash=D(1000),
            assets=(BTC,),
            trade_terms=TradeTermsConfig(price_increment=D("0.1")),
            risk=RiskConfig(risk_fraction=D("0.02"), max_holding_bars=3),
            execution=ExecutionConfig(
                entry_fee_rate=D(0),
                exit_fee_rate=D(0),
                adverse_slippage=D(0),
                price_increment=D("0.1"),
                quantity_increment=D("0.5"),
            ),
        )
        | changes
    )


def run(candles: tuple[Candle, ...] | None = None, **changes: Any) -> ReplayResult:
    return run_scanner_only(config(**changes), [dataset(candles)], code=CODE)


def payloads[Payload](result: ReplayResult, cls: type[Payload]) -> list[Payload]:
    return [r.payload for r in result.records if isinstance(r.payload, cls)]


def test_next_open_reconciliation_and_exact_event_sequence() -> None:
    result = run(history() + (bar(0, "90"), bar(1, "95"), bar(2, "96")))
    first = [r.kind for r in result.records if r.at == T]
    assert first == [
        "SCAN",
        "DELIVERY",
        "TERMS",
        "BASELINE_SELECTION",
        "RISK_AUTHORIZATION",
        "SUBMISSION",
        "OBSERVATION",
    ]
    assert [r.kind for r in result.records if r.at == T + H] == [
        "FILL",
        "SCAN",
        "DELIVERY",
        "OBSERVATION",
    ]
    assert [r.kind for r in result.records if r.at == T + 3 * H] == [
        "FILL",
        "CLOSED_TRADE",
        "OBSERVATION",
    ]
    initial, entered, held, final = result.observations
    assert initial.portfolio.pending is not None and not initial.portfolio.fills
    fill = entered.portfolio.fills[0]
    assert fill.market_time == T
    assert fill.recorded_at == fill.available_at == T + H
    assert fill.execution_price == 90 and fill.quantity == 4
    assert held.mark_price == 95 and held.marked_equity == 1020
    assert final.at == result.manifest.replay.end
    assert final.portfolio.position is None and final.portfolio.pending is None
    assert result.final_portfolio.closed_trades[0].exit_fill.reason == "TIME"
    assert all(r.at <= result.manifest.replay.end for r in result.records)


def test_single_clock_hourly_process_then_all_scans_and_sequential_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace: list[tuple[str, datetime]] = []
    clocks: list[SimulationClock] = []
    gateways: list[PointInTimeDataGateway] = []
    original_init = SimulationClock.__init__
    original_advance = SimulationClock.advance_to
    original_process, original_scan = SimulatedBroker.process, OpportunityScanner.scan
    original_authorize, original_submit = RiskService.authorize, SimulatedBroker.submit

    def init(self: SimulationClock, initial_time: datetime) -> None:
        clocks.append(self)
        original_init(self, initial_time)

    def advance(self: SimulationClock, new_time: datetime) -> None:
        assert new_time == self.now + H
        trace.append(("advance", self.now))
        original_advance(self, new_time)

    def process(self: SimulatedBroker) -> PortfolioSnapshot:
        gateways.append(self._gateway)
        trace.append(("process", self._gateway.now))
        return original_process(self)

    def scan(self: OpportunityScanner, *, pair: TradingPair) -> tuple[Opportunity, ...]:
        gateways.append(self._gateway)
        trace.append((pair.value, self._gateway.now))
        return original_scan(self, pair=pair)

    def authorize(
        self: RiskService, proposal: TradeProposal, portfolio: PortfolioSnapshot
    ) -> RiskAuthorization | RiskRejection:
        gateways.append(self._gateway)
        trace.append(("risk", self._gateway.now))
        return original_authorize(self, proposal, portfolio)

    def submit(self: SimulatedBroker, order: OrderIntent) -> PortfolioSnapshot:
        trace.append(("submit", self._gateway.now))
        return original_submit(self, order)

    monkeypatch.setattr(SimulationClock, "__init__", init)
    monkeypatch.setattr(SimulationClock, "advance_to", advance)
    monkeypatch.setattr(SimulatedBroker, "process", process)
    monkeypatch.setattr(OpportunityScanner, "scan", scan)
    monkeypatch.setattr(RiskService, "authorize", authorize)
    monkeypatch.setattr(SimulatedBroker, "submit", submit)
    result = run_scanner_only(
        config(assets=(SOL, BTC, ETH), end=T + H),
        [dataset(pair=p) for p in (ETH, SOL, BTC)],
        code=CODE,
    )
    assert len(clocks) == 1 and clocks[0].now == T + H
    assert len({id(g) for g in gateways}) == 1
    assert trace == [
        (name, T)
        for name in [
            "process",
            "BTC-USD",
            "ETH-USD",
            "SOL-USD",
            "risk",
            "submit",
            "risk",
            "risk",
            "advance",
        ]
    ] + [("process", T + H)]
    assert [p.proposal.pair for p in payloads(result, RiskRejection)] == [ETH, SOL]
    assert {p.reason for p in payloads(result, RiskRejection)} == {"PORTFOLIO_BUSY"}
    assert len(payloads(result, TradeTerms)) == 3


def test_real_delayed_and_stale_snapshot_opportunities_are_never_backdated() -> None:
    candles = history()
    delayed = candles[:-1] + (
        Candle.model_validate(candles[-1].model_dump() | {"available_at": T + H}),
    )
    result = run(delayed)
    deliveries = payloads(result, Delivery)
    assert [d.disposition for d in deliveries] == ["NOT_CURRENT", "DUPLICATE"]
    assert deliveries[0].opportunity.observed_at == T
    assert not result.final_portfolio.orders
    stale = run(history(), start=T + H)
    assert payloads(stale, Delivery)[0].disposition == "NOT_CURRENT"
    assert not payloads(stale, TradeTerms)


def test_repeated_ids_never_retry_after_any_first_disposition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_scan = OpportunityScanner.scan

    def scan(self: OpportunityScanner, *, pair: TradingPair) -> tuple[Opportunity, ...]:
        original = original_scan(self, pair=pair)[0]
        now = self._gateway.now
        return tuple(
            Opportunity.model_validate(
                original.model_dump()
                | dict(
                    opportunity_id=oid,
                    observed_at=observed,
                    available_at=available,
                )
            )
            for oid, observed, available in [
                ("old", T - H, now),
                ("future", T + H, T + H),
                ("unavailable", T, now + H if now == T else now),
                ("valid", T, now),
                ("valid", T, now),
            ]
        )

    monkeypatch.setattr(OpportunityScanner, "scan", scan)
    result = run(end=T + 2 * H)
    dispositions = [
        (d.opportunity.opportunity_id, d.disposition)
        for d in payloads(result, Delivery)
    ]
    assert dispositions[:5] == [
        ("future", "NOT_CURRENT"),
        ("old", "NOT_CURRENT"),
        ("unavailable", "NOT_AVAILABLE"),
        ("valid", "ADMITTED"),
        ("valid", "DUPLICATE"),
    ]
    assert all(reason == "DUPLICATE" for _, reason in dispositions[5:])
    assert len(result.final_portfolio.orders) == 1


def test_same_pair_simultaneous_candidates_use_id_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = OpportunityScanner.scan

    def scan(self: OpportunityScanner, *, pair: TradingPair) -> tuple[Opportunity, ...]:
        opportunity = original(self, pair=pair)[0]
        return tuple(
            Opportunity.model_validate(
                opportunity.model_dump() | {"opportunity_id": oid}
            )
            for oid in ("z", "a", "m")
        )

    monkeypatch.setattr(OpportunityScanner, "scan", scan)
    result = run(end=T + H)
    assert [
        r.opportunity_id for r in result.records if r.kind == "BASELINE_SELECTION"
    ] == ["a", "m", "z"]
    assert [r.opportunity_id for r in result.records if r.kind == "SUBMISSION"] == ["a"]


@pytest.mark.parametrize("kind", ["terms", "risk", "entry"])
def test_rejections_remain_distinct(kind: str) -> None:
    cfg = config(end=T + H)
    candles = history()
    if kind == "terms":
        # The same close crossover remains, but ranges force an invalid stop.
        candles = tuple(
            Candle.model_validate(c.model_dump() | {"high": D(200), "low": D(1)})
            for c in candles
        )
    elif kind == "risk":
        cfg = config(
            end=T + H,
            execution=cfg.execution.model_copy(update={"entry_fee_rate": D("0.2")}),
        )
    result = run_scanner_only(cfg, [dataset(candles)], code=CODE)
    expected = {
        "terms": "TERMS_REJECTION",
        "risk": "RISK_REJECTION",
        "entry": "ENTRY_REJECTION",
    }[kind]
    assert [r.kind for r in result.records if r.kind.endswith("REJECTION")] == [
        expected
    ]
    if kind == "terms":
        assert payloads(result, TradeTermsRejection)[0].reason == "INVALID_RAW_STOP"
        assert not payloads(result, TradeProposal)
    elif kind == "risk":
        assert payloads(result, RiskRejection)[0].reason == "NON_POSITIVE_TARGET_REWARD"
    else:
        assert result.final_portfolio.rejections[0].reason == "MISSING_ENTRY_CANDLE"


@pytest.mark.parametrize("delay", [None, timedelta(microseconds=1)])
def test_missing_or_delayed_entry_never_rolls_forward(delay: timedelta | None) -> None:
    candles = history() + (bar(1, "90"), bar(2, "90"))
    if delay is not None:
        candles += (bar(0, "90", available_at=T + H + delay),)
    result = run(candles)
    assert len(result.final_portfolio.rejections) == 1
    assert not result.final_portfolio.fills and result.final_portfolio.pending is None
    assert result.metrics.missing_entry_candle_count == 1
    assert result.metrics.ending_marked_equity == 1000


@pytest.mark.parametrize("delay", [None, timedelta(microseconds=1)])
def test_holding_gap_keeps_schedule_but_never_values_or_resumes(
    delay: timedelta | None,
) -> None:
    candles = history() + (bar(0, "89"), bar(2, "96"), bar(3, "100"))
    if delay is not None:
        candles += (bar(1, "90", available_at=T + 2 * H + delay),)
    result = run(candles, end=T + 4 * H)
    assert [o.at for o in result.observations] == [T + i * H for i in range(5)]
    assert [o.marked_equity for o in result.observations] == [
        D(1000),
        D(1000),
        None,
        None,
        None,
    ]
    assert len([r for r in result.records if r.kind == "DATA_GAP"]) == 1
    assert len(result.final_portfolio.fills) == 1
    assert not result.final_portfolio.closed_trades
    assert result.final_portfolio.position == result.observations[1].portfolio.position
    metrics = result.metrics
    assert metrics.unresolved_data_gap and metrics.open_position
    assert not metrics.valuation_complete and not metrics.exposure_complete
    assert (
        metrics.ending_marked_equity
        is metrics.marked_return
        is metrics.maximum_drawdown
        is None
    )
    assert metrics.hourly_bar_exposure is None and metrics.known_exposure_bars == 1
    assert metrics.valuation_valid_through == T + H
    assert metrics.realized_net_pnl == 0 and metrics.realized_return == 0


@pytest.mark.parametrize("state", ["busy", "halted"])
def test_preparation_still_happens_before_busy_or_halted_risk(state: str) -> None:
    btc = dataset(
        history()
        + (bar(0, "90"),)
        + ((bar(1, "90"), bar(2, "90")) if state == "busy" else ())
    )
    # ETH's first crossover is at T+2, when BTC is open or newly halted.
    eth_history = tuple(bar(i) for i in range(-19, 1)) + (bar(1, "90"),)
    eth = dataset(eth_history, pair=ETH)
    sol = dataset(
        tuple(
            Candle.model_validate(c.model_dump() | {"high": D(200), "low": D(1)})
            for c in eth_history
        ),
        pair=SOL,
    )
    result = run_scanner_only(
        config(assets=(SOL, ETH, BTC)), [sol, btc, eth], code=CODE
    )
    relevant = [r for r in result.records if r.at == T + 2 * H]
    assert [
        r.kind
        for r in relevant
        if r.kind in ("TERMS", "TERMS_REJECTION", "RISK_REJECTION")
    ] == ["TERMS", "RISK_REJECTION", "TERMS_REJECTION"]
    assert payloads(result, RiskRejection)[0].reason == (
        "PORTFOLIO_BUSY" if state == "busy" else "PORTFOLIO_HALTED"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision_at", T - H),
        ("pair", ETH),
        ("config", TradeTermsConfig(price_increment=D("0.01"))),
    ],
)
def test_stale_or_mismatched_terms_are_invariant_errors(
    monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    original = TradeTermsService.generate

    def generate(
        self: TradeTermsService, *, pair: TradingPair
    ) -> TradeTerms | TradeTermsRejection:
        result = original(self, pair=pair)
        return result.model_copy(update={field: value})

    monkeypatch.setattr(TradeTermsService, "generate", generate)
    with pytest.raises(ValueError, match="stale or mismatched"):
        run()


@pytest.mark.parametrize("stage", ["generate", "authorize", "submit", "process"])
def test_unexpected_failures_raise_without_returning_partial_results(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise ArithmeticError("injected")

    owner = {
        "generate": TradeTermsService,
        "authorize": RiskService,
        "submit": SimulatedBroker,
        "process": SimulatedBroker,
    }[stage]
    monkeypatch.setattr(owner, stage, fail)
    with pytest.raises(ArithmeticError, match="injected"):
        run()


def test_final_pending_is_an_invariant_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SimulatedBroker, "process", lambda self: self.snapshot)
    with pytest.raises(ValueError, match="cannot remain pending"):
        run(end=T + H)


def test_actual_arithmetic_failure_terminates_replay() -> None:
    with pytest.raises(ValueError, match="execution price rounds to zero"):
        run(history() + (bar(0, "0.01", low="0.001", high="0.02"),), end=T + H)


def test_future_prices_cannot_change_preparation_or_sizing() -> None:
    results = [
        run(history() + (bar(0, price),), end=T + H) for price in ("90", "999999")
    ]
    for kind in (TradeTerms, RiskAuthorization):
        first, second = (payloads(r, kind)[0].model_dump() for r in results)
        # Dataset/run/order identities change; economic preparation must not.
        for data in (first, second):
            if kind is RiskAuthorization:
                data["proposal"].pop("order_id")
                data["order"].pop("order_id")
        assert first == second
    assert results[1].final_portfolio.rejections[0].reason == "UNAFFORDABLE"


def test_end_preserves_open_position_without_hypothetical_liquidation_fees() -> None:
    cfg = config(end=T + H)
    result = run(
        history() + (bar(0, "90", close="92", high="93"),),
        end=T + H,
        execution=cfg.execution.model_copy(
            update={"entry_fee_rate": D("0.01"), "exit_fee_rate": D("0.02")}
        ),
    )
    position = result.final_portfolio.position
    assert position is not None and result.final_portfolio.pending is None
    # 2.5 units; 225 notional, 2.25 actual entry fee, 230 marked holding.
    assert position.quantity == D("2.5")
    assert result.metrics.ending_marked_equity == D("1002.75")
    assert result.metrics.actual_fees == D("2.25")
    assert (
        result.metrics.realized_net_pnl == 0 and result.metrics.closed_trade_count == 0
    )
    assert result.metrics.marked_return == D("0.00275")
    assert result.metrics.hourly_bar_exposure == 1


@pytest.mark.parametrize("cash", [D(0), D(1000)])
@pytest.mark.parametrize("hours", [0, 3])
def test_zero_trades_zero_cash_and_empty_schedule(cash: Decimal, hours: int) -> None:
    result = run((), initial_cash=cash, end=T + hours * H)
    m = result.metrics
    assert len(result.observations) == hours + 1
    assert m.starting_marked_equity == m.ending_marked_equity == cash
    assert (
        m.entry_count == m.closed_trade_count == m.wins == m.losses == m.breakevens == 0
    )
    assert m.actual_fees == m.realized_net_pnl == m.maximum_drawdown == 0
    assert m.win_rate is None
    assert m.marked_return == m.realized_return == (D(0) if cash else None)
    assert m.hourly_bar_exposure == (D(0) if hours else None)
    assert m.valuation_complete and not m.open_position


def test_zero_cash_valid_candidate_reaches_risk() -> None:
    result = run(initial_cash=D(0))
    assert (
        len(payloads(result, TradeTerms)) == len(payloads(result, TradeProposal)) == 1
    )
    assert payloads(result, RiskRejection)[0].reason == "NO_CAPITAL"


def test_repeated_runs_canonical_order_decimal_spelling_and_json_round_trip() -> None:
    inputs = [
        dataset(history() + (bar(0, "90"), bar(1, "95"), bar(2, "96")), pair=p)
        for p in (BTC, ETH, SOL)
    ]
    first = run_scanner_only(config(assets=(BTC, SOL, ETH)), inputs, code=CODE)
    for _ in range(2):
        second = run_scanner_only(
            config(assets=(SOL, ETH, BTC), initial_cash=D("1000.00")),
            reversed(inputs),
            code=CODE,
        )
        assert first.canonical_bytes() == second.canonical_bytes()
        assert first.run_id == second.run_id
    restored = ReplayResult.model_validate_json(first.canonical_bytes())
    assert restored == first
    assert restored.canonical_bytes() == first.canonical_bytes()
    assert [r.sequence for r in first.records] == list(range(len(first.records)))
    assert first.manifest.replay.assets == (BTC, ETH, SOL)
    with pytest.raises(ValidationError):
        first.records[0].sequence = 99
    with pytest.raises(ValidationError):
        first.observations[0].portfolio.cash = D(0)
    proposal = payloads(first, TradeProposal)[0]
    oid = payloads(first, Delivery)[0].opportunity.opportunity_id
    envelope = (
        f'{{"opportunity_id":"{oid}","purpose":"order","run_id":"{first.run_id}"}}'
    ).encode()
    assert proposal.order_id == "sha256:" + sha256(envelope).hexdigest()
    assert (
        first.run_id == "sha256:" + sha256(first.manifest.canonical_bytes()).hexdigest()
    )


def test_hostile_decimal_context_isolated_for_entire_replay() -> None:
    candles = history() + (bar(0, "90"), bar(1, "95"), bar(2, "94"))
    cfg = config(
        execution=config().execution.model_copy(
            update={"entry_fee_rate": D("0.01"), "exit_fee_rate": D("0.02")}
        )
    )
    data = dataset(candles)
    expected = run_scanner_only(cfg, [data], code=CODE)
    with localcontext() as hostile:
        hostile.prec, hostile.Emax, hostile.Emin, hostile.clamp = 1, 1, -1, 1
        hostile.rounding = ROUND_UP
        for signal in hostile.traps:
            hostile.traps[signal] = True
        before = repr(hostile)
        result = run_scanner_only(cfg, [data], code=CODE)
        assert result.canonical_bytes() == expected.canonical_bytes()
        assert repr(hostile) == before


def test_metrics_golden_target_stop_time_win_loss_breakeven_and_drawdown() -> None:
    # Signal at T; target on entry bar. New signal at T+2; stop on entry bar.
    # New signal at T+4; unchanged entry/close and a one-bar time exit.
    candles = history() + (
        bar(0, "90", high="101", close="100"),
        bar(1, "80"),
        bar(2, "80", low="60", close="100", high="101"),
        bar(3, "70"),
        bar(4, "70"),
    )
    result = run(
        candles,
        end=T + 5 * H,
        risk=RiskConfig(risk_fraction=D("0.02"), max_holding_bars=1),
    )
    trades = result.final_portfolio.closed_trades
    assert [trade.exit_fill.reason for trade in trades] == ["TARGET", "STOP", "TIME"]
    # First: q=4, stop=85.1, target=99.5 -> +38.
    # Second: capital=1038; mean TR=3.9; stop=72.2; q=2.5 -> -19.5.
    # Third: entry 70 and time exit 70 -> zero (q=1).
    assert [t.order.quantity for t in trades] == [D(4), D("2.5"), D(1)]
    assert [t.net_pnl for t in trades] == [D(38), D("-19.5"), D(0)]
    assert [o.marked_equity for o in result.observations] == [
        D(1000),
        D(1038),
        D(1038),
        D("1018.5"),
        D("1018.5"),
        D("1018.5"),
    ]
    m = result.metrics
    assert m.starting_marked_equity == 1000 and m.ending_marked_equity == D("1018.5")
    assert m.realized_net_pnl == D("18.5")
    assert m.realized_return == m.marked_return == D("0.0185")
    assert m.entry_count == m.closed_trade_count == 3
    assert m.wins == m.losses == m.breakevens == 1
    assert m.stop_count == m.target_count == m.time_count == 1
    assert m.win_rate == D("0.33333333333333333333333333333333333333333333333333")
    assert m.maximum_drawdown == D(
        "0.018786127167630057803468208092485549132947976878613"
    )
    assert m.valid_prefix_maximum_drawdown == m.maximum_drawdown
    assert m.actual_fees == 0 and m.known_exposure_bars == 3
    assert m.hourly_bar_exposure == D("0.6")


def test_fees_marked_equity_and_drawdown_golden_includes_open_losses() -> None:
    cfg = config()
    # q=2.5: A=90.9, L=90.9 - 85.1*.98 = 7.502; floor(20/L/.5)*.5.
    # Entry cash=227.25, actual entry fee=2.25; peak equity=1010.25 at close 95.
    # Final time exit at 94: proceeds=230.30, exit fee=4.70, net=3.05.
    candles = history() + (
        bar(0, "90", close="95", high="96"),
        bar(1, "92"),
        bar(2, "94"),
    )
    result = run(
        candles,
        execution=cfg.execution.model_copy(
            update={"entry_fee_rate": D("0.01"), "exit_fee_rate": D("0.02")}
        ),
    )
    assert [o.marked_equity for o in result.observations] == [
        D(1000),
        D("1010.25"),
        D("1002.75"),
        D("1003.05"),
    ]
    m = result.metrics
    assert m.realized_net_pnl == D("3.05") and m.actual_fees == D("6.95")
    assert m.marked_return == m.realized_return == D("0.00305")
    assert m.maximum_drawdown == D(
        "0.0074239049740163325909428359317000742390497401633259"
    )
    assert m.entry_count == m.closed_trade_count == m.wins == m.time_count == 1
    assert m.hourly_bar_exposure == m.win_rate == 1


def test_halt_preserves_realized_metrics_and_nonzero_valid_prefix_drawdown() -> None:
    candles = history() + (
        bar(0, "90", high="101", close="100"),
        bar(1, "80"),
        bar(2, "80", close="79"),
        bar(4, "200"),
    )
    result = run(candles, end=T + 5 * H)
    m = result.metrics
    assert m.ending_marked_equity is m.maximum_drawdown is m.marked_return is None
    assert m.realized_net_pnl == 38 and m.realized_return == D("0.038")
    assert m.closed_trade_count == m.wins == m.target_count == 1
    assert m.entry_count == 2 and m.known_exposure_bars == 2
    assert m.valid_prefix_maximum_drawdown == D(
        "0.0024084778420038535645472061657032755298651252408478"
    )
    assert m.valuation_valid_through == T + 3 * H
    assert result.observations[-1].mark_price is None


def test_close_and_reject_reconcile_before_new_boundary_authorization() -> None:
    # At T+2 BTC's first trade times out and ETH's fresh candidate can use new cash.
    btc = dataset(history() + (bar(0, "90"), bar(1, "95")))
    eth = dataset(
        tuple(bar(i) for i in range(-19, 1)) + (bar(1, "90"), bar(2, "90")), pair=ETH
    )
    result = run_scanner_only(
        config(
            assets=(BTC, ETH),
            risk=RiskConfig(risk_fraction=D("0.02"), max_holding_bars=2),
        ),
        [eth, btc],
        code=CODE,
    )
    approvals = payloads(result, RiskAuthorization)
    assert [a.capital_basis for a in approvals] == [D(1000), D(1020)]
    assert [a.order.pair for a in approvals] == [BTC, ETH]
    at = [r.kind for r in result.records if r.at == T + 2 * H]
    assert at[:2] == ["FILL", "CLOSED_TRADE"]
    assert at.index("CLOSED_TRADE") < at.index("RISK_AUTHORIZATION")


def test_no_scan_at_end_even_if_boundary_has_fresh_opportunity() -> None:
    result = run(history(), start=T - H, end=T)
    assert not result.final_portfolio.orders
    assert not payloads(result, Delivery)
    assert [r.kind for r in result.records if r.at == T] == ["OBSERVATION"]


def test_order_ids_do_not_depend_on_preceding_success_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_scan = OpportunityScanner.scan
    first_id = True

    def scan(self: OpportunityScanner, *, pair: TradingPair) -> tuple[Opportunity, ...]:
        opportunity = original_scan(self, pair=pair)[0]
        ids = ("a", "b") if first_id else ("b",)
        return tuple(
            Opportunity.model_validate(
                opportunity.model_dump() | {"opportunity_id": oid}
            )
            for oid in ids
        )

    monkeypatch.setattr(OpportunityScanner, "scan", scan)
    first = run(end=T + H)
    first_id = False
    second = run(end=T + H)
    # Same manifest; b is busy-rejected in the first and authorized in the second.
    assert first.run_id == second.run_id
    assert (
        payloads(first, TradeProposal)[1].order_id
        == payloads(second, TradeProposal)[0].order_id
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"end": T - H},
        {"start": T + timedelta(minutes=1)},
        {"end": T + timedelta(seconds=1)},
        {"initial_cash": -1},
        {"initial_cash": 1.0},
        {"assets": ()},
        {"assets": (BTC, BTC)},
        {"start": T.replace(tzinfo=None)},
        {"trade_terms": TradeTermsConfig(price_increment=D("0.25"))},
    ],
)
def test_invalid_replay_configuration_raises(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        config(**changes)


@pytest.mark.parametrize(
    "case",
    ["missing", "extra", "duplicate", "forged", "invalid_candle", "invalid_config"],
)
def test_input_contracts_fail_before_replay(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    def no_clock(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("clock should not be constructed for invalid inputs")

    monkeypatch.setattr(SimulationClock, "__init__", no_clock)
    inputs = [dataset()]
    cfg = config()
    if case == "missing":
        inputs = []
    elif case == "extra":
        inputs.append(dataset(pair=ETH))
    elif case == "duplicate":
        inputs *= 2
    elif case == "forged":
        inputs = [
            replace(
                inputs[0],
                manifest=inputs[0].manifest.model_copy(
                    update={"dataset_version": "sha256:" + "f" * 64}
                ),
            )
        ]
    elif case == "invalid_candle":
        inputs = [
            replace(
                inputs[0],
                candles=(inputs[0].candles[0].model_copy(update={"close": D(-1)}),),
            )
        ]
    else:
        cfg = cfg.model_copy(update={"initial_cash": D(-1)})
    with pytest.raises(ValueError):
        run_scanner_only(cfg, inputs, code=CODE)


def test_unrelated_scanner_pair_is_an_invariant_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = OpportunityScanner.scan

    def scan(self: OpportunityScanner, *, pair: TradingPair) -> tuple[Opportunity, ...]:
        return tuple(
            Opportunity.model_validate(o.model_dump() | {"pair": ETH})
            for o in original(self, pair=pair)
        )

    monkeypatch.setattr(OpportunityScanner, "scan", scan)
    with pytest.raises(ValueError, match="different pair"):
        run()


def test_entry_rejection_precedes_authorization_of_new_pair_at_same_boundary() -> None:
    eth = dataset(
        tuple(bar(i) for i in range(-20, 0)) + (bar(0, "90"), bar(1, "90")), pair=ETH
    )
    result = run_scanner_only(
        config(assets=(ETH, BTC), end=T + 2 * H), [dataset(), eth], code=CODE
    )
    assert [a.order.pair for a in payloads(result, RiskAuthorization)] == [BTC, ETH]
    kinds = [r.kind for r in result.records if r.at == T + H]
    assert kinds[0] == "ENTRY_REJECTION"
    assert kinds.index("ENTRY_REJECTION") < kinds.index("RISK_AUTHORIZATION")
    assert result.final_portfolio.position is not None
    assert result.final_portfolio.position.order.pair == ETH


def test_canonical_persistence_to_replay_round_trip(tmp_path: Path) -> None:
    import json

    from crypteo.coinbase import CoinbaseCandleRequest, CoinbaseRawCandleResponse
    from crypteo.coinbase_dataset import compose_coinbase_dataset
    from crypteo.coinbase_persistence import CoinbaseLocalPersistence

    candles = history() + (bar(0, "90"),)
    body = json.dumps(
        [
            [
                int(c.open_time.timestamp()),
                int(c.low),
                int(c.high),
                int(c.open),
                int(c.close),
                int(c.volume),
            ]
            for c in candles
        ]
    ).encode()
    raw = CoinbaseRawCandleResponse(
        acquisition_started_at=T + 100 * H,
        request=CoinbaseCandleRequest(pair=BTC, start=T - 21 * H, end=T + H),
        body=body,
    )
    original = compose_coinbase_dataset(
        pair=BTC,
        raw_responses=[raw],
        created_at=T + 100 * H,
        requested_start=T - 21 * H,
        requested_end=T + H,
    )
    store = CoinbaseLocalPersistence(tmp_path)
    store.persist_raw(original.raw_responses)
    store.persist_dataset(original)
    store.persist_acquisition(original)
    loaded = store.load_dataset(
        original.manifest.dataset_version,
        acquisition_id=original.acquisition.acquisition_id,
    )
    first = run_scanner_only(config(end=T + H), [original])
    second = run_scanner_only(config(end=T + H), [loaded])
    assert first.canonical_bytes() == second.canonical_bytes()
    assert (
        first.manifest.datasets[0].acquisition_id == original.acquisition.acquisition_id
    )


def test_acquisition_linkage_is_revalidated_for_direct_input() -> None:
    from crypteo.coinbase_acquisition import CoinbaseAcquisitionRecord

    data = dataset()
    acquisition = CoinbaseAcquisitionRecord(
        schema_version="1",
        dataset_version="sha256:" + "f" * 64,
        created_at=T,
        requested_start=T,
        requested_end=T,
        capture_ids=(),
        missing_open_times=(),
    )
    with pytest.raises(ValueError, match="acquisition dataset linkage"):
        run_scanner_only(config(), [replace(data, acquisition=acquisition)], code=CODE)


def test_canonical_input_order_and_provenance_time_do_not_change_results() -> None:
    data = dataset(history() + (bar(0, "90"),))
    rearranged = replace(
        data,
        candles=tuple(reversed(data.candles)),
        manifest=data.manifest.model_copy(update={"created_at": T + 200 * H}),
    )
    first = run_scanner_only(config(end=T + H), [data], code=CODE)
    second = run_scanner_only(config(end=T + H), [rearranged], code=CODE)
    assert first.canonical_bytes() == second.canonical_bytes()


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision_at", T - H),
        ("pair", ETH),
        ("config", TradeTermsConfig(price_increment=D("0.01"))),
    ],
)
def test_mismatched_terms_rejection_is_an_invariant_error(
    monkeypatch: pytest.MonkeyPatch, field: str, value: Any
) -> None:
    def generate(self: TradeTermsService, *, pair: TradingPair) -> TradeTermsRejection:
        return TradeTermsRejection.model_validate(
            dict(
                venue=Venue.COINBASE,
                pair=pair,
                timeframe=Timeframe.ONE_HOUR,
                decision_at=self._gateway.now,
                config=config().trade_terms,
                reason="INVALID_RAW_STOP",
            )
            | {field: value}
        )

    monkeypatch.setattr(TradeTermsService, "generate", generate)
    with pytest.raises(ValueError, match="stale or mismatched"):
        run()


@pytest.mark.parametrize(
    "change", ["kind", "linkage", "missing_linkage", "time", "admission"]
)
def test_record_contract_rejects_false_kind_linkage_and_time(change: str) -> None:
    from crypteo.domain.replay import ReplayRecord

    result = run(end=T + H)
    record = next(r for r in result.records if r.kind == "DELIVERY")
    data = record.model_dump()
    if change == "kind":
        data["kind"] = "TERMS"
    elif change == "linkage":
        data["opportunity_id"] = "wrong"
    elif change == "missing_linkage":
        data["opportunity_id"] = None
    elif change == "time":
        record = result.records[-1]
        data = record.model_dump() | {"at": T}
    else:
        data["at"] = T + H
    with pytest.raises(ValidationError):
        ReplayRecord.model_validate(data)


@pytest.mark.parametrize(
    "change",
    ["id", "sequence", "schedule", "pending", "missing_final", "outside", "end_scan"],
)
def test_result_contract_cannot_describe_a_partial_or_invalid_completion(
    change: str,
) -> None:
    result = run(end=T + H)
    data = result.model_dump()
    if change == "id":
        data["run_id"] = "wrong"
    elif change == "sequence":
        data["records"][0]["sequence"] = 1
    elif change == "schedule":
        records = list(data["records"])
        records.pop(
            next(i for i, r in enumerate(records) if r["kind"] == "OBSERVATION")
        )
        for i, record in enumerate(records):
            record["sequence"] = i
        data["records"] = tuple(records)
    elif change == "pending":
        data["final_portfolio"] = result.observations[0].portfolio.model_dump()
    elif change == "missing_final":
        data["records"] = data["records"][:-1]
    elif change == "outside":
        data["records"][0]["at"] = T - H
    else:
        data["records"][0]["at"] = T + H
    with pytest.raises(ValidationError):
        ReplayResult.model_validate(data)


@pytest.mark.parametrize("state", ["flat", "open", "halted"])
def test_observation_contract_rejects_fabricated_equity(state: str) -> None:
    from crypteo.domain.replay import BoundaryObservation

    result = run(history() + (bar(0, "90"),), end=T + 2 * H)
    at = {"flat": 0, "open": 1, "halted": 2}[state]
    observation = result.observations[at]
    with pytest.raises(ValidationError):
        BoundaryObservation.model_validate(
            observation.model_dump() | {"marked_equity": D(1)}
        )


@pytest.mark.parametrize("field", ["observed_at", "trigger_type"])
@pytest.mark.parametrize("first", ["current", "old", "unavailable"])
def test_changed_opportunity_identity_is_an_invariant_error(
    monkeypatch: pytest.MonkeyPatch, field: str, first: str
) -> None:
    original = OpportunityScanner.scan

    def scan(self: OpportunityScanner, *, pair: TradingPair) -> tuple[Opportunity, ...]:
        opportunity = original(self, pair=pair)[0]
        observed = T - H if first == "old" else T
        available = T + H if first == "unavailable" else T
        changes: dict[str, Any] = {"observed_at": observed, "available_at": available}
        if self._gateway.now > T:
            changes[field] = T + H if field == "observed_at" else "changed_trigger_v2"
            changes["available_at"] = T + H
        return (Opportunity.model_validate(opportunity.model_dump() | changes),)

    monkeypatch.setattr(OpportunityScanner, "scan", scan)
    with pytest.raises(ValueError, match="conflicting reuse of opportunity_id"):
        run(end=T + 2 * H)


def test_cross_pair_same_id_is_an_invariant_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = OpportunityScanner.scan

    def scan(self: OpportunityScanner, *, pair: TradingPair) -> tuple[Opportunity, ...]:
        return tuple(
            Opportunity.model_validate(
                item.model_dump() | {"opportunity_id": "collision"}
            )
            for item in original(self, pair=pair)
        )

    monkeypatch.setattr(OpportunityScanner, "scan", scan)
    with pytest.raises(ValueError, match="conflicting reuse of opportunity_id"):
        run_scanner_only(
            config(assets=(BTC, ETH)), [dataset(pair=p) for p in (BTC, ETH)], code=CODE
        )


def test_snapshot_payload_and_availability_changes_preserve_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = OpportunityScanner.scan

    def scan(self: OpportunityScanner, *, pair: TradingPair) -> tuple[Opportunity, ...]:
        return tuple(
            Opportunity.model_validate(
                item.model_dump()
                | {
                    "available_at": self._gateway.now,
                    "trigger_payload": {"snapshot": self._gateway.now.isoformat()},
                    "summary": f"Snapshot at {self._gateway.now}",
                }
            )
            for item in original(self, pair=pair)
        )

    monkeypatch.setattr(OpportunityScanner, "scan", scan)
    result = run(end=T + 2 * H)
    assert [d.disposition for d in payloads(result, Delivery)] == [
        "ADMITTED",
        "DUPLICATE",
    ]
    assert ReplayResult.model_validate_json(result.canonical_bytes()) == result


def _lifecycle_result(kind: str) -> ReplayResult:
    if kind == "TERMS_REJECTION":
        return run(
            tuple(
                Candle.model_validate(c.model_dump() | {"high": D(200), "low": D(1)})
                for c in history()
            )
        )
    if kind == "RISK_REJECTION":
        return run(initial_cash=D(0))
    if kind == "ENTRY_REJECTION":
        return run()
    if kind == "DATA_GAP":
        return run(history() + (bar(0, "90"),))
    return run(history() + (bar(0, "90"), bar(1, "95"), bar(2, "94")))


def _reject_artifact(data: dict[str, Any], *, match: str | None = None) -> None:
    # Resequence to ensure mutations exercise lifecycle checks rather than the
    # pre-existing contiguous-sequence check, using the canonical JSON entrypoint.
    for index, record in enumerate(data["records"]):
        record["sequence"] = index
    with pytest.raises(ValidationError, match=match):
        ReplayResult.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    "kind",
    [
        "SCAN",
        "DELIVERY",
        "TERMS",
        "TERMS_REJECTION",
        "BASELINE_SELECTION",
        "RISK_AUTHORIZATION",
        "RISK_REJECTION",
        "SUBMISSION",
        "FILL",
        "CLOSED_TRADE",
        "ENTRY_REJECTION",
        "DATA_GAP",
        "OBSERVATION",
    ],
)
def test_canonical_artifact_rejects_deleted_lifecycle_record(kind: str) -> None:
    result = _lifecycle_result(kind)
    assert ReplayResult.model_validate_json(result.canonical_bytes()) == result
    data = json.loads(result.canonical_bytes())
    records = data["records"]
    records.pop(next(i for i, record in enumerate(records) if record["kind"] == kind))
    _reject_artifact(data)


@pytest.mark.parametrize(
    "kind",
    [
        "TERMS",
        "TERMS_REJECTION",
        "BASELINE_SELECTION",
        "RISK_AUTHORIZATION",
        "RISK_REJECTION",
        "SUBMISSION",
        "FILL",
        "CLOSED_TRADE",
        "ENTRY_REJECTION",
        "DATA_GAP",
    ],
)
def test_canonical_artifact_rejects_nonexistent_opportunity_linkage(kind: str) -> None:
    data = json.loads(_lifecycle_result(kind).canonical_bytes())
    record = next(r for r in data["records"] if r["kind"] == kind)
    record["opportunity_id"] = "nonexistent"
    _reject_artifact(data, match="opportunity linkage")


@pytest.mark.parametrize(
    "kind",
    [
        "TERMS",
        "BASELINE_SELECTION",
        "RISK_AUTHORIZATION",
        "SUBMISSION",
        "FILL",
        "CLOSED_TRADE",
    ],
)
def test_canonical_artifact_rejects_linkage_to_another_existing_opportunity(
    kind: str,
) -> None:
    candles = history() + (bar(0, "90", high="101"),)
    result = run_scanner_only(
        config(assets=(BTC, ETH), end=T + H),
        [dataset(candles, pair=p) for p in (BTC, ETH)],
        code=CODE,
    )
    data = json.loads(result.canonical_bytes())
    other_id = next(
        d.opportunity.opportunity_id
        for d in payloads(result, Delivery)
        if d.opportunity.pair == ETH
    )
    record = next(r for r in data["records"] if r["kind"] == kind)
    record["opportunity_id"] = other_id
    _reject_artifact(data, match="opportunity linkage")


@pytest.mark.parametrize(
    "before", ["TERMS", "BASELINE_SELECTION", "RISK_AUTHORIZATION"]
)
def test_canonical_artifact_rejects_submission_before_required_chain(
    before: str,
) -> None:
    data = json.loads(_lifecycle_result("SUBMISSION").canonical_bytes())
    records = data["records"]
    submission = records.pop(
        next(i for i, r in enumerate(records) if r["kind"] == "SUBMISSION")
    )
    records.insert(
        next(i for i, r in enumerate(records) if r["kind"] == before), submission
    )
    _reject_artifact(data, match="out-of-sequence lifecycle")


@pytest.mark.parametrize("field", ["stop_price", "target_price", "order_id", "pair"])
def test_canonical_artifact_rejects_proposal_differing_from_frozen_terms(
    field: str,
) -> None:
    data = json.loads(_lifecycle_result("BASELINE_SELECTION").canonical_bytes())
    proposal = next(
        r["payload"] for r in data["records"] if r["kind"] == "BASELINE_SELECTION"
    )
    proposal[field] = {
        "stop_price": "84",
        "target_price": "101",
        "order_id": "other",
        "pair": "ETH-USD",
    }[field]
    _reject_artifact(data, match="proposal must match frozen terms")


@pytest.mark.parametrize(
    "kind,field,value",
    [
        ("TERMS", "pair", "ETH-USD"),
        ("RISK_REJECTION", "proposal", None),
        (
            "RISK_REJECTION",
            "risk_config",
            {"risk_fraction": "0.5", "max_holding_bars": 3},
        ),
        ("SUBMISSION", "quantity", "1"),
    ],
)
def test_canonical_artifact_rejects_inconsistent_stage_mappings(
    kind: str, field: str, value: Any
) -> None:
    data = json.loads(_lifecycle_result(kind).canonical_bytes())
    payload = next(r["payload"] for r in data["records"] if r["kind"] == kind)
    payload[field] = (
        payload[field] | {"order_id": "other"} if field == "proposal" else value
    )
    _reject_artifact(data, match="must match")


@pytest.mark.parametrize("state", ["closed", "open", "rejected", "gap"])
def test_initial_observation_rejects_future_final_portfolio(state: str) -> None:
    result = (
        run(history() + (bar(0, "90"),), end=T + H)
        if state == "open"
        else _lifecycle_result(
            {
                "closed": "CLOSED_TRADE",
                "rejected": "ENTRY_REJECTION",
                "gap": "DATA_GAP",
            }[state]
        )
    )
    data = json.loads(result.canonical_bytes())
    initial = next(r["payload"] for r in data["records"] if r["kind"] == "OBSERVATION")
    final = data["records"][-1]["payload"]
    initial.update({k: v for k, v in final.items() if k != "at"})
    _reject_artifact(data, match="observation ledger")


@pytest.mark.parametrize("field", list(ReplayMetrics.model_fields))
def test_canonical_artifact_rejects_every_fabricated_metric(field: str) -> None:
    data = json.loads(_lifecycle_result("CLOSED_TRADE").canonical_bytes())
    value = data["metrics"][field]
    if field == "valuation_valid_through":
        replacement: Any = T.isoformat()
    elif isinstance(value, bool):
        replacement = not value
    elif isinstance(value, int):
        replacement = value + 1
    else:
        replacement = str(D(value) + 1) if value is not None else "0"
    data["metrics"][field] = replacement
    _reject_artifact(data, match="metrics must match authoritative")


@pytest.mark.parametrize("case", ["empty", "zero_cash", "gap", "open", "same_bar_exit"])
def test_legitimate_completion_remains_canonically_revalidatable(case: str) -> None:
    if case == "empty":
        result = run(end=T)
    elif case == "zero_cash":
        result = run(initial_cash=D(0))
    elif case == "gap":
        result = _lifecycle_result("DATA_GAP")
    else:
        result = run(
            history()
            + (bar(0, "90", high="101" if case == "same_bar_exit" else "91"),),
            end=T + H,
        )
    restored = ReplayResult.model_validate_json(result.canonical_bytes())
    assert restored == result
    assert restored.canonical_bytes() == result.canonical_bytes()


@pytest.mark.parametrize(
    "field",
    [
        "marked_return",
        "ending_marked_equity",
        "maximum_drawdown",
        "hourly_bar_exposure",
    ],
)
def test_gap_metrics_cannot_fabricate_missing_values(field: str) -> None:
    data = json.loads(_lifecycle_result("DATA_GAP").canonical_bytes())
    assert data["metrics"][field] is None
    data["metrics"][field] = "0"
    _reject_artifact(data, match="metrics must match authoritative")


@pytest.mark.parametrize("field", ["observed_at", "trigger_type"])
def test_canonical_artifact_rejects_reused_id_with_changed_identity(field: str) -> None:
    data = json.loads(run().canonical_bytes())
    for record in data["records"]:
        if record["at"] != (T + H).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        ):
            continue
        if record["kind"] == "SCAN":
            opportunities = record["payload"]["opportunities"]
        elif record["kind"] == "DELIVERY":
            opportunities = [record["payload"]["opportunity"]]
        else:
            continue
        for opportunity in opportunities:
            opportunity[field] = (
                (T - H).isoformat()
                if field == "observed_at"
                else "different_trigger_v2"
            )
    _reject_artifact(data, match="conflicting reuse of opportunity_id")


def test_canonical_artifact_rejects_cross_pair_id_collision() -> None:
    # Both candidates are stale, so the mutation needs no fabricated order chain.
    result = run_scanner_only(
        config(assets=(BTC, ETH), start=T + H),
        [dataset(pair=p) for p in (BTC, ETH)],
        code=CODE,
    )
    data = json.loads(result.canonical_bytes())
    collision = payloads(result, Delivery)[0].opportunity.opportunity_id
    for record in data["records"]:
        if record["kind"] == "SCAN":
            opportunities = record["payload"]["opportunities"]
        elif record["kind"] == "DELIVERY":
            opportunities = [record["payload"]["opportunity"]]
            record["opportunity_id"] = collision
        else:
            continue
        for opportunity in opportunities:
            opportunity["opportunity_id"] = collision
    _reject_artifact(data, match="conflicting reuse of opportunity_id")


@pytest.mark.parametrize(
    "kind", ["FILL", "CLOSED_TRADE", "ENTRY_REJECTION", "DATA_GAP"]
)
def test_canonical_artifact_rejects_lifecycle_payload_for_unsubmitted_order(
    kind: str,
) -> None:
    data = json.loads(_lifecycle_result(kind).canonical_bytes())
    payload = next(r["payload"] for r in data["records"] if r["kind"] == kind)
    if kind == "CLOSED_TRADE":
        for field in ("order", "entry_fill", "exit_fill"):
            payload[field]["order_id"] = "never-submitted"
    else:
        payload["order_id"] = "never-submitted"
    _reject_artifact(data, match="financial lifecycle requires a prior submission")


@pytest.mark.parametrize("offset", [-1, 1])
def test_observation_cannot_contain_stale_or_future_holding_history(
    offset: int,
) -> None:
    result = run(history() + (bar(0, "90"), bar(1, "90"), bar(2, "90")))
    data = json.loads(result.canonical_bytes())
    observation = [r["payload"] for r in data["records"] if r["kind"] == "OBSERVATION"][
        2
    ]
    position = observation["portfolio"]["position"]
    position["holding_bars"] += offset
    position["last_candle_open_time"] = (T + (1 + offset) * H).isoformat()
    _reject_artifact(data, match="inconsistent holding history")


def test_same_boundary_exit_fill_must_precede_closed_trade_record() -> None:
    data = json.loads(
        run(history() + (bar(0, "90", high="101"),), end=T + H).canonical_bytes()
    )
    records = data["records"]
    closed_index = next(i for i, r in enumerate(records) if r["kind"] == "CLOSED_TRADE")
    records[closed_index - 1], records[closed_index] = (
        records[closed_index],
        records[closed_index - 1],
    )
    _reject_artifact(data, match="reconciliation order")


def _balanced_fill_mutation(
    result: ReplayResult, side: str, changes: dict[str, str]
) -> dict[str, Any]:
    """Coordinate every retained fill copy, ledger, valuation and metric.

    Revalidate the accounting contracts separately so failures must come from
    cross-record/config checks, not an unbalanced cash or P&L mutation.
    """
    data: dict[str, Any] = json.loads(result.canonical_bytes())

    def rebalance(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                rebalance(item)
        elif isinstance(value, dict):
            for item in value.values():
                rebalance(item)
            if "execution_price" in value and value["side"] == side:
                value.update(changes)
                notional = D(value["quantity"]) * D(value["execution_price"])
                fee = D(value["fee"])
                value["cash_delta"] = str(
                    -(notional + fee) if side == "BUY" else notional - fee
                )
            if "gross_pnl" in value:
                entry, exit = value["entry_fill"], value["exit_fill"]
                gross = D(entry["quantity"]) * (
                    D(exit["execution_price"]) - D(entry["execution_price"])
                )
                value["gross_pnl"] = str(gross)
                value["net_pnl"] = str(gross - D(entry["fee"]) - D(exit["fee"]))
            if "initial_cash" in value and "fills" in value:
                value["cash"] = str(
                    D(value["initial_cash"])
                    + sum((D(f["cash_delta"]) for f in value["fills"]), D(0))
                )
                value["cumulative_fees"] = str(
                    sum((D(f["fee"]) for f in value["fills"]), D(0))
                )
                value["cumulative_net_realized_pnl"] = str(
                    sum((D(t["net_pnl"]) for t in value["closed_trades"]), D(0))
                )
                PortfolioSnapshot.model_validate_json(json.dumps(value))
            if "marked_equity" in value:
                assert value["portfolio"]["position"] is None
                value["marked_equity"] = value["portfolio"]["cash"]

    rebalance(data)
    portfolio = PortfolioSnapshot.model_validate_json(
        json.dumps(data["final_portfolio"])
    )
    observations = tuple(
        BoundaryObservation.model_validate_json(json.dumps(r["payload"]))
        for r in data["records"]
        if r["kind"] == "OBSERVATION"
    )
    data["metrics"] = calculate_metrics(
        result.manifest.replay, portfolio, observations
    ).model_dump(mode="json")
    return data


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_artifact_rejects_independent_fill_reference_under_zero_slippage(
    side: str,
) -> None:
    result = run(history() + (bar(0, "90", high="101"),), end=T + H)
    data = _balanced_fill_mutation(result, side, {"reference_price": "91"})
    assert (
        ReplayMetrics.model_validate_json(json.dumps(data["metrics"])) == result.metrics
    )
    _reject_artifact(
        data, match="execution price contradicts retained execution config"
    )


@pytest.mark.parametrize("side", ["BUY", "SELL"])
@pytest.mark.parametrize("rate", ["0", "0.01"])
def test_artifact_rejects_balanced_fee_inconsistent_with_config(
    side: str, rate: str
) -> None:
    execution = config().execution.model_copy(
        update={"entry_fee_rate": D(rate), "exit_fee_rate": D(rate)}
    )
    result = run(
        history() + (bar(0, "90", high="101"),), end=T + H, execution=execution
    )
    fill = next(f for f in result.final_portfolio.fills if f.side == side)
    data = _balanced_fill_mutation(result, side, {"fee": str(fill.fee + 1)})
    assert D(data["metrics"]["actual_fees"]) == result.metrics.actual_fees + 1
    assert D(data["final_portfolio"]["cash"]) == result.final_portfolio.cash - 1
    _reject_artifact(data, match="fill fee contradicts retained execution config")


@pytest.mark.parametrize("reason", ["STOP", "TARGET"])
@pytest.mark.parametrize("phase", ["OPEN", "INTRABAR"])
def test_artifact_rejects_balanced_exit_inconsistent_with_order_level(
    reason: str, phase: str
) -> None:
    result = run(history() + (bar(0, "90", high="101"),), end=T + H)
    order = result.final_portfolio.closed_trades[0].order
    reference = order.stop_price + 1 if reason == "STOP" else order.target_price - 1
    data = _balanced_fill_mutation(
        result,
        "SELL",
        {
            "reason": reason,
            "phase": phase,
            "reference_price": str(reference),
            "execution_price": str(reference),
        },
    )
    assert D(data["final_portfolio"]["cash"]) != result.final_portfolio.cash
    _reject_artifact(data, match="exit reference contradicts retained order level")


@pytest.mark.parametrize("phase", ["OPEN", "INTRABAR"])
def test_artifact_checks_same_candle_exit_against_retained_entry_open(
    phase: str,
) -> None:
    result = run(history() + (bar(0, "90", high="101"),), end=T + H)
    target = result.final_portfolio.closed_trades[0].order.target_price
    if phase == "OPEN":
        data = _balanced_fill_mutation(
            result,
            "SELL",
            {
                "phase": "OPEN",
                "reference_price": str(target),
                "execution_price": str(target),
            },
        )
    else:
        data = _balanced_fill_mutation(
            result,
            "BUY",
            {"reference_price": str(target), "execution_price": str(target)},
        )
    _reject_artifact(data, match="exit phase contradicts retained entry candle open")


@pytest.mark.parametrize(
    "reason", ["PORTFOLIO_BUSY", "PORTFOLIO_HALTED", "REUSED_ORDER_ID"]
)
@pytest.mark.parametrize("capital", ["0", "1000"])
def test_artifact_rejects_state_rejection_for_empty_portfolio(
    reason: str, capital: str
) -> None:
    execution = config().execution.model_copy(
        update={"entry_fee_rate": D("0.2") if capital == "1000" else D(0)}
    )
    result = run(initial_cash=D(capital), execution=execution)
    data = json.loads(result.canonical_bytes())
    risk = next(r["payload"] for r in data["records"] if r["kind"] == "RISK_REJECTION")
    assert risk["reason"] in ("NO_CAPITAL", "NON_POSITIVE_TARGET_REWARD")
    risk["reason"] = reason
    _reject_artifact(data, match="risk rejection contradicts retained portfolio state")


def test_artifact_rejects_no_capital_reason_when_cash_is_retained() -> None:
    result = run_scanner_only(
        config(assets=(BTC, ETH)), [dataset(pair=p) for p in (BTC, ETH)], code=CODE
    )
    data = json.loads(result.canonical_bytes())
    risk = next(r["payload"] for r in data["records"] if r["kind"] == "RISK_REJECTION")
    assert risk["reason"] == "PORTFOLIO_BUSY"
    risk["reason"] = "NO_CAPITAL"
    _reject_artifact(data, match="risk rejection contradicts retained portfolio state")


@pytest.mark.parametrize(
    "entry,exit",
    [
        (bar(0, "90", high="101"), None),  # Intrabar target.
        (bar(0, "90", low="80"), None),  # Intrabar stop.
        (bar(0, "105"), None),  # Same-bar opening target gap.
        (bar(0, "80"), None),  # Same-bar opening stop gap.
        (bar(0, "90"), bar(1, "105")),  # Later open is not the entry reference.
        (bar(0, "90"), bar(1, "80")),
    ],
)
def test_artifact_accepts_legitimate_exit_levels_with_adverse_execution(
    entry: Candle, exit: Candle | None
) -> None:
    execution = ExecutionConfig(
        entry_fee_rate=D("0.001"),
        exit_fee_rate=D("0.002"),
        adverse_slippage=D("0.005"),
        price_increment=D("0.1"),
        quantity_increment=D("0.5"),
    )
    candles = (entry,) if exit is None else (entry, exit)
    result = run(history() + candles, execution=execution, end=T + len(candles) * H)
    trade = result.final_portfolio.closed_trades[0]
    assert trade.exit_fill.execution_price < trade.exit_fill.reference_price
    assert trade.entry_fill.fee > 0 and trade.exit_fill.fee > 0
    assert ReplayResult.model_validate_json(result.canonical_bytes()) == result
