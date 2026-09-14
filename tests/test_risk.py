import json
from datetime import UTC, datetime, timedelta
from decimal import ROUND_UP, Decimal, getcontext, localcontext
from fractions import Fraction
from typing import Any

import pytest
from pydantic import ValidationError

from crypteo.domain.execution import ExecutionConfig, OrderIntent, PortfolioSnapshot
from crypteo.domain.market import Candle, Timeframe, TradingPair, Venue
from crypteo.domain.risk import (
    RiskAuthorization,
    RiskConfig,
    RiskRejection,
    TradeProposal,
)
from crypteo.execution import SimulatedBroker
from crypteo.risk import RiskService
from crypteo.temporal import PointInTimeDataGateway, SimulationClock

D = Decimal
T = datetime(2026, 1, 1, 10, tzinfo=UTC)
HOUR = timedelta(hours=1)


def proposal(**changes: Any) -> TradeProposal:
    return TradeProposal.model_validate(
        {
            "order_id": "risk-1",
            "venue": Venue.COINBASE,
            "pair": TradingPair.BTC_USD,
            "timeframe": Timeframe.ONE_HOUR,
            "decision_at": T,
            "stop_price": "90",
            "target_price": "120",
        }
        | changes
    )


def config(**changes: Any) -> ExecutionConfig:
    return ExecutionConfig.model_validate(
        {
            "entry_fee_rate": "0",
            "exit_fee_rate": "0",
            "adverse_slippage": "0",
            "price_increment": "0.1",
            "quantity_increment": "0.5",
        }
        | changes
    )


def risk_config(risk: str = "0.02") -> RiskConfig:
    return RiskConfig(risk_fraction=D(risk), max_holding_bars=3)


def flat(cash: str = "1000") -> PortfolioSnapshot:
    return PortfolioSnapshot(initial_cash=D(cash), cash=D(cash))


def bar(
    hour: int = -1,
    *,
    open: str = "100",
    close: str = "100",
    low: str = "95",
    high: str = "110",
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
        close=D(close),
        low=D(low),
        high=D(high),
        volume=D(1),
    )


def service(
    *,
    candles: list[Candle] | None = None,
    risk: str = "0.02",
    settings: ExecutionConfig | None = None,
    now: datetime = T,
) -> RiskService:
    return RiskService(
        PointInTimeDataGateway(
            SimulationClock(now), [bar()] if candles is None else candles
        ),
        risk_config(risk),
        settings or config(),
    )


def approved(result: RiskAuthorization | RiskRejection) -> RiskAuthorization:
    assert isinstance(result, RiskAuthorization), result
    return result


def assert_rejected(result: RiskAuthorization | RiskRejection, reason: str) -> None:
    assert isinstance(result, RiskRejection)
    assert result.reason == reason
    assert result.proposal.order_id == "risk-1"
    assert RiskRejection.model_validate_json(result.model_dump_json()) == result


def test_cost_free_sizing_and_exact_order_mapping() -> None:
    candidate = proposal(pair=TradingPair.SOL_USD, order_id="supplied-id")
    settings = config()
    risk = RiskConfig(risk_fraction=D("0.02"), max_holding_bars=7)
    gateway = PointInTimeDataGateway(
        SimulationClock(T), [bar(pair=TradingPair.SOL_USD)]
    )
    result = approved(RiskService(gateway, risk, settings).authorize(candidate, flat()))
    assert result.order == OrderIntent(
        order_id="supplied-id",
        venue=Venue.COINBASE,
        pair=TradingPair.SOL_USD,
        timeframe=Timeframe.ONE_HOUR,
        decision_at=T,
        quantity=D(2),
        stop_price=D(90),
        target_price=D(120),
        max_holding_bars=7,
    )
    assert result.reference_price == result.estimated_entry_price == D(100)
    assert result.capital_basis == D(1000)
    assert result.risk_budget == result.estimated_total_loss == D(20)
    assert result.estimated_stop_execution_price == D(90)
    assert result.estimated_loss_per_unit == D(10)
    assert result.authorized_quantity == D(2)
    assert result.estimated_entry_cash == D(200)
    assert result.risk_config == risk
    assert result.execution_config == settings
    assert RiskAuthorization.model_validate_json(result.model_dump_json()) == result


def test_independent_cost_inclusive_golden_sizing() -> None:
    result = approved(
        service(
            risk="0.1",
            settings=config(
                entry_fee_rate="0.01",
                exit_fee_rate="0.02",
                adverse_slippage="0.01",
            ),
        ).authorize(proposal(), flat())
    )
    # A = 101 * 1.01 = 102.01; U = 102.01 - 89.1 * .98 = 14.692.
    # Six and a half units fit; seven would cost 102.844 of the 100 budget.
    assert result.estimated_entry_price == D(101)
    assert result.estimated_stop_execution_price == D("89.1")
    assert result.estimated_loss_per_unit == D("14.692")
    assert result.risk_budget == D(100)
    assert result.authorized_quantity == D("6.5")
    assert result.estimated_entry_cash == D("663.065")
    assert result.estimated_total_loss == D("95.498")


@pytest.mark.parametrize(
    "cash,risk,quantity",
    [
        ("146.92", "0.1", "1"),
        ("146.919999999999999999999999999999", "0.1", "0.5"),
        ("102.01", "1", "1"),
        ("102.009999999999999999999999999999", "1", "0.5"),
    ],
)
def test_cost_inclusive_exact_and_just_below_boundaries(
    cash: str, risk: str, quantity: str
) -> None:
    result = approved(
        service(
            risk=risk,
            settings=config(
                entry_fee_rate="0.01", exit_fee_rate="0.02", adverse_slippage="0.01"
            ),
        ).authorize(proposal(), flat(cash))
    )
    assert result.authorized_quantity == D(quantity)


@pytest.mark.parametrize(
    "field,value",
    [
        ("authorized_quantity", D(1)),
        ("reference_price", D(90)),
        ("risk_budget", D(21)),
        ("estimated_entry_cash", D(199)),
        ("estimated_loss_per_unit", D(9)),
        ("estimated_total_loss", D(19)),
        ("risk_config", RiskConfig(risk_fraction=D("0.02"), max_holding_bars=4)),
        ("risk_config", RiskConfig(risk_fraction=D("0.01"), max_holding_bars=3)),
    ],
)
def test_authorization_revalidates_inconsistent_audit_totals(
    field: str, value: object
) -> None:
    result = approved(service().authorize(proposal(), flat()))
    with pytest.raises(ValidationError):
        RiskAuthorization.model_validate(result.model_copy(update={field: value}))


def test_authorization_and_nested_configuration_are_frozen() -> None:
    result = approved(service().authorize(proposal(), flat()))
    with pytest.raises(ValidationError):
        result.authorized_quantity = D(1)
    with pytest.raises(ValidationError):
        result.order.quantity = D(1)
    with pytest.raises(ValidationError):
        result.execution_config.entry_fee_rate = D("0.5")


def assert_invalid_authorization(data: dict[str, Any], match: str) -> None:
    # Both public construction and persisted JSON must enforce the same boundary.
    python_data = data | {
        "proposal": TradeProposal.model_validate_json(json.dumps(data["proposal"])),
        "order": OrderIntent.model_validate_json(json.dumps(data["order"])),
    }
    with pytest.raises(ValidationError, match=match):
        RiskAuthorization(**python_data)
    with pytest.raises(ValidationError, match=match):
        RiskAuthorization.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    "entry,stop,quantity,cash,loss_per_unit,total_loss,match",
    [
        ("100", "99", "2", "200", "1", "2", "adverse SELL"),
        ("95", "90", "2", "190", "5", "10", "adverse BUY"),
        # Coherent invented estimates conceal real nongapped losses of 100/40,
        # above the claimed budget of 20. Both quantities appear maximal.
        ("100", "99", "10", "1000", "1", "10", "adverse SELL"),
        ("95", "90", "4", "380", "5", "20", "adverse BUY"),
    ],
)
def test_invented_execution_estimates_rejected_even_with_coherent_totals(
    entry: str,
    stop: str,
    quantity: str,
    cash: str,
    loss_per_unit: str,
    total_loss: str,
    match: str,
) -> None:
    data = approved(service().authorize(proposal(), flat())).model_dump(mode="json")
    data.update(
        estimated_entry_price=entry,
        estimated_stop_execution_price=stop,
        authorized_quantity=quantity,
        estimated_entry_cash=cash,
        estimated_loss_per_unit=loss_per_unit,
        estimated_total_loss=total_loss,
    )
    data["order"]["quantity"] = quantity
    assert_invalid_authorization(data, match)


@pytest.mark.parametrize(
    "risk,quantity,cash,loss,match",
    [
        ("0.02", "1.9", "190", "19", "quantity must align"),
        ("0.02", "1.5", "150", "15", "must be maximal"),
        ("0.02", "2.5", "250", "25", "exceeds estimated risk"),
        ("0.5", "9.5", "950", "95", "must be maximal"),
        ("0.5", "10.5", "1050", "105", "exceeds estimated risk or cash"),
    ],
)
def test_coherent_but_unexecutable_or_nonmaximal_quantities_rejected(
    risk: str, quantity: str, cash: str, loss: str, match: str
) -> None:
    data = approved(service(risk=risk).authorize(proposal(), flat())).model_dump(
        mode="json"
    )
    data.update(
        authorized_quantity=quantity,
        estimated_entry_cash=cash,
        estimated_total_loss=loss,
    )
    data["order"]["quantity"] = quantity
    assert_invalid_authorization(data, match)


@pytest.mark.parametrize(
    "field,value", [("stop_price", "90.01"), ("target_price", "120.01")]
)
def test_authorization_rejects_misaligned_levels_even_when_order_matches(
    field: str, value: str
) -> None:
    data = approved(service().authorize(proposal(), flat())).model_dump(mode="json")
    data["proposal"][field] = data["order"][field] = value
    assert_invalid_authorization(data, f"{field} must align")


@pytest.mark.parametrize(
    "field,value",
    [
        ("order_id", "different"),
        ("pair", "ETH-USD"),
        ("decision_at", "2026-01-01T11:00:00Z"),
        ("stop_price", "89"),
        ("target_price", "121"),
        ("max_holding_bars", 4),
        ("quantity", "1.5"),
    ],
)
def test_order_must_match_authorization_inputs(field: str, value: object) -> None:
    result = approved(service().authorize(proposal(), flat()))
    data = result.model_dump(mode="json")
    data["order"][field] = value
    # JSON datetime strings are only accepted by JSON validation, so use a
    # validated order for direct Python construction.
    order = OrderIntent.model_validate_json(json.dumps(data["order"]))
    with pytest.raises(ValidationError, match="order must match"):
        RiskAuthorization(**(result.model_dump() | {"order": order}))
    with pytest.raises(ValidationError, match="order must match"):
        RiskAuthorization.model_validate_json(json.dumps(data))


def test_authorization_rejects_stop_that_shared_sell_pricing_rounds_to_zero() -> None:
    data = approved(service().authorize(proposal(), flat())).model_dump(mode="json")
    data["proposal"]["stop_price"] = data["order"]["stop_price"] = "1"
    data["execution_config"].update(price_increment="1", adverse_slippage="0.01")
    assert_invalid_authorization(data, "execution price rounds to zero")


@pytest.mark.parametrize("risk", ["0.02", "0.5"])
def test_maximal_cost_inclusive_authorizations_round_trip(risk: str) -> None:
    result = approved(
        service(
            risk=risk,
            settings=config(
                entry_fee_rate="0.01",
                exit_fee_rate="0.02",
                adverse_slippage="0.01",
                quantity_increment="0.3",
            ),
        ).authorize(proposal(), flat())
    )
    assert result.proposal == proposal()
    assert RiskAuthorization(**result.model_dump()) == result
    assert RiskAuthorization.model_validate(result) == result
    assert RiskAuthorization.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("reuse_id", [False, True])
def test_future_holding_completion_raises_before_ordinary_rejection(
    reuse_id: bool,
) -> None:
    clock = SimulationClock(T)
    gateway = PointInTimeDataGateway(clock, [bar(), bar(0)])
    broker = SimulatedBroker(gateway, config(), initial_cash=D(1000))
    gate = RiskService(gateway, risk_config(), config())
    broker.submit(approved(gate.authorize(proposal(), broker.snapshot)).order)
    clock.advance_to(T + HOUR)
    opened = broker.process()
    assert opened.position is not None
    # Ledger timestamps remain <= now. Only the holding cursor is corrupted.
    position = opened.position.model_copy(
        update={
            "holding_bars": 2,
            "last_candle_open_time": T + HOUR,
        }
    )
    future = PortfolioSnapshot.model_validate(
        opened.model_dump() | {"position": position}
    )
    before = future.model_dump_json()
    candidate = proposal(
        order_id="risk-1" if reuse_id else "new", decision_at=clock.now
    )
    with pytest.raises(ValueError, match="history after current gateway time"):
        gate.authorize(candidate, future)
    assert future.model_dump_json() == before
    assert broker.snapshot is opened and clock.now == T + HOUR
    ordinary = gate.authorize(candidate, opened)
    assert isinstance(ordinary, RiskRejection)
    assert ordinary.reason == ("REUSED_ORDER_ID" if reuse_id else "PORTFOLIO_BUSY")


@pytest.mark.parametrize(
    "risk,cash,quantity",
    [
        ("0.02", "1000", "2"),  # Exact risk boundary.
        ("0.019999999999999999999999999999999999", "1000", "1.5"),
        ("0.5", "1000", "10"),  # Cash binds before risk.
        ("0.5", "999.99999999999999999999999999999999", "9.5"),
        ("0.1", "1000", "10"),  # Both constraints bind.
        ("1", "50", "0.5"),  # Exactly one cash increment.
        ("0.005", "1000", "0.5"),  # Exactly one risk increment.
    ],
)
def test_exact_risk_cash_and_one_increment_below_boundaries(
    risk: str, cash: str, quantity: str
) -> None:
    result = approved(service(risk=risk).authorize(proposal(), flat(cash)))
    assert result.authorized_quantity == D(quantity)


@pytest.mark.parametrize(
    "cash,risk,reason",
    [
        ("0", "0.02", "NO_CAPITAL"),
        ("1", "0.02", "QUANTITY_BELOW_INCREMENT"),
        ("49.999999999999999999999999999999999", "1", "QUANTITY_BELOW_INCREMENT"),
        ("1000", "0.004999999999999999999999999999999", "QUANTITY_BELOW_INCREMENT"),
    ],
)
def test_no_capital_or_sub_increment_quantity_rejects(
    cash: str, risk: str, reason: str
) -> None:
    assert_rejected(service(risk=risk).authorize(proposal(), flat(cash)), reason)


def test_non_power_of_ten_price_and_quantity_increments() -> None:
    result = approved(
        service(
            risk="0.1",
            candles=[bar(close="100.01")],
            settings=config(
                entry_fee_rate="0.01",
                exit_fee_rate="0.02",
                adverse_slippage="0.01",
                price_increment="0.25",
                quantity_increment="0.3",
            ),
        ).authorize(proposal(), flat())
    )
    assert result.reference_price == D("100.01")
    assert result.estimated_entry_price == D("101.25")
    assert result.estimated_stop_execution_price == D("89")
    assert result.estimated_loss_per_unit == D("15.0425")
    assert result.authorized_quantity == D("6.6")
    assert result.estimated_entry_cash == D("674.9325")
    assert result.estimated_total_loss == D("99.2805")


@pytest.mark.parametrize("step", ["0.0003", "0.3", "2.5"])
@pytest.mark.parametrize("risk", ["0.01987654321", "0.999"])
@pytest.mark.parametrize("fees", ["0", "0.01234567890123456789"])
def test_independent_rational_oracle_proves_constraints_and_maximality(
    step: str, risk: str, fees: str
) -> None:
    settings = config(
        entry_fee_rate=fees,
        exit_fee_rate="0.0234567890123456789",
        adverse_slippage="0.00789123456789",
        price_increment="0.25",
        quantity_increment=step,
    )
    result = approved(
        service(risk=risk, settings=settings, candles=[bar(close="100.03")]).authorize(
            proposal(), flat("12345.67890123456789")
        )
    )
    f = Fraction
    increment = f("0.25")
    buy_steps = f("100.03") * (1 + f(settings.adverse_slippage)) / increment
    entry = (-(-buy_steps.numerator // buy_steps.denominator)) * increment
    sell_steps = f(90) * (1 - f(settings.adverse_slippage)) / increment
    stop = (sell_steps.numerator // sell_steps.denominator) * increment
    cash_per_unit = entry * (1 + f(fees))
    loss_per_unit = cash_per_unit - stop * (1 - f(settings.exit_fee_rate))
    capital = f("12345.67890123456789")
    budget = capital * f(risk)
    quantity = f(result.authorized_quantity)
    assert f(result.estimated_entry_price) == entry
    assert f(result.estimated_stop_execution_price) == stop
    assert f(result.estimated_loss_per_unit) == loss_per_unit
    assert f(result.estimated_entry_cash) == quantity * cash_per_unit <= capital
    assert f(result.estimated_total_loss) == quantity * loss_per_unit <= budget
    assert (quantity / f(step)).denominator == 1
    next_quantity = quantity + f(step)
    assert (
        next_quantity * cash_per_unit > capital
        or next_quantity * loss_per_unit > budget
    )


@pytest.mark.parametrize(
    "stop,target",
    [
        ("100", "120"),
        ("101", "120"),
        ("90", "100"),
        ("90", "99"),
        ("120", "90"),
        ("100", "100"),
        ("0", "120"),
        ("-1", "120"),
        ("90", "0"),
    ],
)
def test_invalid_long_relationship_is_an_ordinary_rejection(
    stop: str, target: str
) -> None:
    assert_rejected(
        service().authorize(proposal(stop_price=stop, target_price=target), flat()),
        "INVALID_LONG_LEVELS",
    )


@pytest.mark.parametrize("field", ["stop_price", "target_price"])
def test_misaligned_strategy_level_is_an_error_even_without_reference(
    field: str,
) -> None:
    for candles in ([], [bar()]):
        with pytest.raises(ValueError, match=f"{field} must align"):
            service(candles=candles, settings=config(price_increment="0.25")).authorize(
                proposal(**{field: "90.1"}), flat()
            )


def test_estimated_sell_rounding_to_zero_rejects() -> None:
    assert_rejected(
        service(
            settings=config(price_increment="1", adverse_slippage="0.01")
        ).authorize(proposal(stop_price="1"), flat()),
        "UNEXECUTABLE_STOP",
    )


@pytest.mark.parametrize(
    "candles",
    [
        [],
        [bar(-2)],
        [bar(delay=timedelta(microseconds=1))],
        [bar(-2), bar(0)],
        [bar(pair=TradingPair.ETH_USD)],
    ],
)
def test_exact_reference_required_without_fallback(candles: list[Candle]) -> None:
    assert_rejected(
        service(candles=candles).authorize(proposal(), flat()), "REFERENCE_UNAVAILABLE"
    )


def test_delayed_reference_cannot_be_retroactively_authorized() -> None:
    clock = SimulationClock(T)
    gateway = PointInTimeDataGateway(clock, [bar(delay=timedelta(microseconds=1))])
    gate = RiskService(gateway, risk_config(), config())
    assert_rejected(gate.authorize(proposal(), flat()), "REFERENCE_UNAVAILABLE")
    clock.advance_to(T + HOUR)
    with pytest.raises(ValueError, match="current gateway time"):
        gate.authorize(proposal(), flat())
    assert_rejected(
        gate.authorize(proposal(decision_at=clock.now), flat()), "REFERENCE_UNAVAILABLE"
    )


def test_future_and_other_market_candles_do_not_change_authorization_or_clock() -> None:
    expected = service().authorize(proposal(), flat())
    for next_open in ("1", "100", "999999"):
        clock = SimulationClock(T)
        gateway = PointInTimeDataGateway(
            clock,
            [
                bar(),
                bar(0, open=next_open, low="1", high="999999"),
                bar(1, open="999999", low="1", high="999999"),
                bar(close="1", low="1", pair=TradingPair.ETH_USD),
            ],
        )
        assert gateway.read_candles(
            venue=Venue.COINBASE, pair=TradingPair.BTC_USD, timeframe=Timeframe.ONE_HOUR
        ) == (bar(),)
        assert (
            RiskService(gateway, risk_config(), config()).authorize(proposal(), flat())
            == expected
        )
        assert clock.now == gateway.now == T


@pytest.mark.parametrize("offset", [-1, 1])
def test_invalid_temporal_invocation_is_an_error(offset: int) -> None:
    with pytest.raises(ValueError, match="current gateway time"):
        service().authorize(proposal(decision_at=T + offset * HOUR), flat())


def test_repeated_authorization_does_not_reserve_cash_or_consume_ids() -> None:
    gate = service()
    state = flat()
    before = state.model_dump_json()
    results = [gate.authorize(proposal(), state).model_dump_json() for _ in range(3)]
    assert results[0] == results[1] == results[2]
    assert results[0] == service().authorize(proposal(), state).model_dump_json()
    assert state.model_dump_json() == before
    assert state.orders == () and state.pending is None
    assert (
        approved(gate.authorize(proposal(order_id="another"), state)).order.order_id
        == "another"
    )


def test_hostile_decimal_context_preserves_results_and_caller_context() -> None:
    settings = config(
        entry_fee_rate="0.000000000000000000000000000001",
        exit_fee_rate="0.02",
        adverse_slippage="0.01",
        quantity_increment="0.3",
    )
    cash = "1" + "0" * 100
    expected = service(settings=settings).authorize(proposal(), flat(cash))
    with localcontext() as hostile:
        hostile.prec = 1
        hostile.rounding = ROUND_UP
        hostile.Emax = 1
        hostile.Emin = -1
        hostile.clamp = 1
        for signal in hostile.traps:
            hostile.traps[signal] = True
        hostile.clear_flags()
        before = hostile.copy()
        result = service(settings=settings).authorize(proposal(), flat(cash))
        assert result == expected
        assert (
            RiskAuthorization.model_validate_json(result.model_dump_json()) == expected
        )
        for field in ("prec", "rounding", "Emax", "Emin", "clamp", "flags", "traps"):
            assert getattr(getcontext(), field) == getattr(before, field)


@pytest.mark.parametrize("stage", ["pending", "open", "halted", "closed", "rejected"])
def test_portfolio_policy_and_reused_ids_at_every_lifecycle_stage(stage: str) -> None:
    candles = [bar()]
    if stage != "rejected":
        candles.append(bar(0, high="120" if stage == "closed" else "110"))
    clock = SimulationClock(T)
    gateway = PointInTimeDataGateway(clock, candles)
    broker = SimulatedBroker(gateway, config(), initial_cash=D(1000))
    gate = RiskService(gateway, risk_config(), config())
    broker.submit(approved(gate.authorize(proposal(), broker.snapshot)).order)
    if stage != "pending":
        clock.advance_to(T + (2 if stage == "halted" else 1) * HOUR)
        broker.process()
    state = broker.snapshot
    before = state.model_dump_json()
    assert_rejected(
        gate.authorize(proposal(decision_at=clock.now), state),
        "PORTFOLIO_HALTED" if stage == "halted" else "REUSED_ORDER_ID",
    )
    result = gate.authorize(proposal(order_id="new", decision_at=clock.now), state)
    if stage in ("pending", "open", "halted"):
        assert isinstance(result, RiskRejection)
        assert result.reason == (
            "PORTFOLIO_HALTED" if stage == "halted" else "PORTFOLIO_BUSY"
        )
    elif stage == "closed":
        assert approved(result).capital_basis == D(1040)
        assert approved(result).risk_budget == D("20.8")
    else:
        assert isinstance(result, RiskRejection)
        assert result.reason == "REFERENCE_UNAVAILABLE"
    assert broker.snapshot is state
    assert state.model_dump_json() == before


@pytest.mark.parametrize("case", ["cash", "nested_order", "future_history"])
def test_corrupt_or_future_snapshot_is_an_error_before_rejection(case: str) -> None:
    intent = approved(service().authorize(proposal(), flat())).order
    if case == "cash":
        state = flat().model_copy(update={"cash": D(999)})
    elif case == "nested_order":
        invalid = intent.model_copy(update={"quantity": D(-1)})
        state = flat().model_copy(update={"orders": (invalid,), "pending": invalid})
    else:
        future = OrderIntent.model_validate(
            intent.model_dump() | {"decision_at": T + HOUR}
        )
        state = PortfolioSnapshot(
            initial_cash=D(1000), cash=D(1000), orders=(future,), pending=future
        )
    with pytest.raises(ValueError):
        service().authorize(proposal(), state)


def test_service_revalidates_unchecked_proposals_and_configs() -> None:
    with pytest.raises(ValidationError):
        service().authorize(
            proposal().model_copy(update={"stop_price": D("NaN")}), flat()
        )
    gateway = PointInTimeDataGateway(SimulationClock(T), [bar()])
    with pytest.raises(ValidationError):
        RiskService(
            gateway, risk_config().model_copy(update={"risk_fraction": D(2)}), config()
        )
    with pytest.raises(ValidationError):
        RiskService(
            gateway,
            risk_config(),
            config().model_copy(update={"price_increment": D(0)}),
        )


@pytest.mark.parametrize(
    "next_open,exit_open", [("100", "100"), ("105", "100"), ("100", "80")]
)
def test_execution_reconciles_estimate_only_without_entry_or_stop_gap(
    next_open: str, exit_open: str
) -> None:
    settings = config(
        entry_fee_rate="0.01", exit_fee_rate="0.02", adverse_slippage="0.01"
    )
    clock = SimulationClock(T)
    gateway = PointInTimeDataGateway(
        clock, [bar(), bar(0, open=next_open), bar(1, open=exit_open, low="75")]
    )
    gate = RiskService(gateway, risk_config("0.1"), settings)
    broker = SimulatedBroker(gateway, settings, initial_cash=D(1000))
    auth = approved(gate.authorize(proposal(), broker.snapshot))
    assert auth == service(risk="0.1", settings=settings).authorize(proposal(), flat())
    frozen = auth.model_dump_json()
    broker.submit(auth.order)
    clock.advance_to(T + 2 * HOUR)
    closed = broker.process()
    trade = closed.closed_trades[0]
    assert trade.order == auth.order
    assert trade.entry_fill.quantity == trade.exit_fill.quantity == D("6.5")
    assert trade.exit_fill.reason == "STOP"
    if next_open == exit_open == "100":
        assert trade.entry_fill.execution_price == auth.estimated_entry_price
        assert trade.exit_fill.execution_price == auth.estimated_stop_execution_price
        assert trade.entry_fill.cash_delta == auth.estimated_entry_cash.copy_negate()
        assert trade.net_pnl == auth.estimated_total_loss.copy_negate() == D("-95.498")
        assert closed.cash == D("904.502")
    else:
        assert trade.net_pnl.copy_negate() > auth.risk_budget
        assert trade.net_pnl == (
            D("-128.9795") if next_open == "105" else D("-158.561")
        )
    assert auth.model_dump_json() == frozen


@pytest.mark.parametrize("next_open,accepted", [("100", True), ("100.01", False)])
def test_entry_gap_affordability_remains_broker_owned_without_resizing(
    next_open: str, accepted: bool
) -> None:
    clock = SimulationClock(T)
    gateway = PointInTimeDataGateway(clock, [bar(), bar(0, open=next_open)])
    settings = config(entry_fee_rate="0.01")
    broker = SimulatedBroker(gateway, settings, initial_cash=D(1010))
    auth = approved(
        RiskService(gateway, risk_config("0.5"), settings).authorize(
            proposal(), broker.snapshot
        )
    )
    assert auth.authorized_quantity == D(10)
    assert auth.estimated_entry_cash == auth.capital_basis == D(1010)
    assert auth == service(risk="0.5", settings=settings).authorize(
        proposal(), flat("1010")
    )
    broker.submit(auth.order)
    clock.advance_to(T + HOUR)
    state = broker.process()
    if accepted:
        assert state.position_quantity == D(10) and state.cash == 0
    else:
        assert state.rejections[0].reason == "UNAFFORDABLE"
        assert state.cash == D(1010) and state.fills == ()
    assert state.orders == (auth.order,)
