from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from crypteo.domain.execution import (
    DataGap,
    EntryRejection,
    ExecutionConfig,
    Fill,
    OrderIntent,
    PortfolioSnapshot,
)
from crypteo.domain.risk import TradeProposal

ORDER: dict[str, Any] = {
    "order_id": "authorized-1",
    "venue": "COINBASE",
    "pair": "BTC-USD",
    "timeframe": "1h",
    "decision_at": datetime(2026, 1, 1, tzinfo=UTC),
    "quantity": "0.5",
    "stop_price": "90",
    "target_price": "120",
    "max_holding_bars": 2,
}
CONFIG: dict[str, Any] = {
    "entry_fee_rate": "0.001",
    "exit_fee_rate": "0.002",
    "adverse_slippage": "0.005",
    "price_increment": "0.1",
    "quantity_increment": "0.5",
}


@pytest.mark.parametrize("field", list(CONFIG))
def test_every_execution_setting_is_explicitly_required(field: str) -> None:
    data = CONFIG.copy()
    del data[field]
    with pytest.raises(ValidationError):
        ExecutionConfig.model_validate(data)


@pytest.mark.parametrize("field", list(ORDER))
def test_every_authorized_term_is_explicitly_required(field: str) -> None:
    data = ORDER.copy()
    del data[field]
    with pytest.raises(ValidationError):
        OrderIntent.model_validate(data)


@pytest.mark.parametrize("field", ["quantity", "stop_price", "target_price"])
@pytest.mark.parametrize("value", [0, -1, "NaN", "Infinity", "-Infinity", 1.5, True])
def test_authorized_financial_terms_require_positive_exact_decimals(
    field: str, value: Any
) -> None:
    with pytest.raises(ValidationError):
        OrderIntent.model_validate(ORDER | {field: value})


@pytest.mark.parametrize("field", list(CONFIG))
@pytest.mark.parametrize("value", ["-0.01", "NaN", "Infinity", "-Infinity", 0.1, True])
def test_configuration_rejects_invalid_financial_values(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        ExecutionConfig.model_validate(CONFIG | {field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("price_increment", "0"),
        ("quantity_increment", "0"),
        ("entry_fee_rate", "1"),
        ("exit_fee_rate", "1"),
        ("adverse_slippage", "1"),
    ],
)
def test_increments_must_be_positive_and_rates_less_than_one(
    field: str, value: str
) -> None:
    with pytest.raises(ValidationError):
        ExecutionConfig.model_validate(CONFIG | {field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_holding_bars", 0),
        ("max_holding_bars", -1),
        ("max_holding_bars", True),
        ("max_holding_bars", "3"),
        ("max_holding_bars", 1.5),
        ("order_id", ""),
        ("order_id", "   "),
    ],
)
def test_holding_bars_require_a_positive_strict_integer_and_order_id_nonblank(
    field: str, value: Any
) -> None:
    with pytest.raises(ValidationError):
        OrderIntent.model_validate(ORDER | {field: value})


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 1, 1),
        datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8))),
        datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
        datetime(2026, 1, 1, 0, 0, 0, 1, tzinfo=UTC),
        "2026-01-01T00:00:00Z",
        1767225600,
    ],
)
def test_decision_time_requires_strict_utc_hourly_boundary(value: Any) -> None:
    with pytest.raises(ValidationError):
        OrderIntent.model_validate(ORDER | {"decision_at": value})
    with pytest.raises(ValidationError):
        OrderIntent.model_validate(
            OrderIntent.model_validate(ORDER).model_copy(update={"decision_at": value})
        )


def test_decision_boundary_accepts_zero_offset_and_round_trips_json() -> None:
    at = ORDER["decision_at"].replace(tzinfo=timezone(timedelta(0), "zero"))
    order = OrderIntent.model_validate(ORDER | {"decision_at": at})
    assert order.decision_at == at
    assert OrderIntent.model_validate_json(order.model_dump_json()) == order


@pytest.mark.parametrize("stop", ["120", "121"])
def test_long_stop_must_be_below_target(stop: str) -> None:
    with pytest.raises(ValidationError):
        OrderIntent.model_validate(ORDER | {"stop_price": stop})


def test_contracts_are_frozen_reject_extras_and_round_trip() -> None:
    intent = OrderIntent.model_validate(ORDER)
    settings = ExecutionConfig.model_validate(CONFIG)
    assert OrderIntent.model_validate_json(intent.model_dump_json()) == intent
    assert ExecutionConfig.model_validate_json(settings.model_dump_json()) == settings
    with pytest.raises(ValidationError):
        intent.quantity = Decimal(2)
    with pytest.raises(ValidationError):
        settings.price_increment = Decimal(2)
    with pytest.raises(ValidationError):
        OrderIntent.model_validate(ORDER | {"direction": "SHORT"})
    with pytest.raises(ValidationError):
        ExecutionConfig.model_validate(CONFIG | {"partial_fills": True})


@pytest.mark.parametrize("cash", ["-1", "NaN", "Infinity", 1.5, True])
def test_portfolio_initial_cash_requires_nonnegative_exact_value(cash: Any) -> None:
    with pytest.raises(ValidationError):
        PortfolioSnapshot(initial_cash=cash, cash=cash)


# Independent entry/exit ledger: 1000 - 204.02 + 232.848 = 1028.828.
# Quantity 2, entry 101, exit 118.8, fees 2.02 + 4.752, net PnL 28.828.
def execution_records() -> dict[str, Any]:
    t = datetime(2026, 1, 1, tzinfo=UTC)
    h = timedelta(hours=1)
    order = ORDER | {"quantity": "2", "max_holding_bars": 3}
    entry = {
        "order_id": order["order_id"],
        "side": "BUY",
        "reason": "ENTRY",
        "phase": "OPEN",
        "candle_open_time": t,
        "candle_close_time": t + h,
        "available_at": t + h,
        "recorded_at": t + h,
        "reference_price": "100",
        "execution_price": "101",
        "quantity": "2",
        "fee": "2.02",
        "cash_delta": "-204.02",
    }
    exit = entry | {
        "side": "SELL",
        "reason": "TARGET",
        "phase": "INTRABAR",
        "candle_open_time": t + h,
        "candle_close_time": t + 2 * h,
        "available_at": t + 2 * h,
        "recorded_at": t + 2 * h,
        "reference_price": "120",
        "execution_price": "118.8",
        "fee": "4.752",
        "cash_delta": "232.848",
    }
    position = {
        "order": order,
        "entry_fill": entry,
        "quantity": "2",
        "cost_basis": "204.02",
        "holding_bars": 1,
        "last_candle_open_time": t,
    }
    trade = {
        "order": order,
        "entry_fill": entry,
        "exit_fill": exit,
        "holding_bars": 2,
        "gross_pnl": "35.6",
        "net_pnl": "28.828",
    }
    rejection = {
        "order_id": order["order_id"],
        "reason": "MISSING_ENTRY_CANDLE",
        "recorded_at": t + h,
    }
    gap = {
        "order_id": order["order_id"],
        "missing_open_time": t + h,
        "expected_completion": t + 2 * h,
        "detected_at": t + 2 * h,
    }
    opened = {
        "initial_cash": "1000",
        "cash": "795.98",
        "orders": [order],
        "position": position,
        "fills": [entry],
        "cumulative_fees": "2.02",
    }
    closed = {
        "initial_cash": "1000",
        "cash": "1028.828",
        "orders": [order],
        "fills": [entry, exit],
        "closed_trades": [trade],
        "cumulative_fees": "6.772",
        "cumulative_net_realized_pnl": "28.828",
    }
    return {
        "order": order,
        "entry": entry,
        "exit": exit,
        "position": position,
        "trade": trade,
        "rejection": rejection,
        "gap": gap,
        "opened": opened,
        "closed": closed,
    }


@pytest.fixture(
    params=[
        (OrderIntent, "order"),
        (Fill, "entry"),
        (EntryRejection, "rejection"),
        (DataGap, "gap"),
        (TradeProposal, "order"),
    ]
)
def identified_record(request: pytest.FixtureRequest) -> BaseModel:
    model: type[BaseModel] = request.param[0]
    data = execution_records()[request.param[1]]
    return model.model_validate(
        {k: v for k, v in data.items() if k in model.model_fields}
    )


@pytest.mark.parametrize("value", ["order", " order \t", b"order", bytearray(b"order")])
def test_order_identifier_preserves_whitespace_and_string_coercion(
    identified_record: BaseModel, value: str | bytes | bytearray
) -> None:
    model = type(identified_record)
    data = identified_record.model_dump() | {"order_id": value}
    expected = value if isinstance(value, str) else value.decode()
    for result in (
        model(**data),
        model.model_validate(data),
        model.model_validate(identified_record.model_copy(update={"order_id": value})),
    ):
        assert result.model_dump()["order_id"] == expected
        assert model.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    "value", ["", " \t\n", "\u00a0", 1, 1.5, True, None, [], b"\xff"]
)
def test_order_identifier_rejects_invalid_values_on_reconstruction(
    identified_record: BaseModel, value: Any
) -> None:
    model = type(identified_record)
    data = identified_record.model_dump() | {"order_id": value}
    with pytest.raises(ValidationError) as error:
        model(**data)
    assert error.value.errors()[0]["loc"] == ("order_id",)
    if isinstance(value, str):
        assert (
            error.value.errors()[0]["msg"] == "Value error, order_id must be non-empty"
        )
    with pytest.raises(ValidationError):
        model.model_validate(data)
    with pytest.raises(ValidationError):
        model.model_validate(identified_record.model_copy(update={"order_id": value}))


@pytest.mark.parametrize(
    "record,change",
    [
        ("entry", {"order_id": " "}),
        ("entry", {"side": "SELL"}),
        ("entry", {"phase": "INTRABAR"}),
        ("entry", {"reason": "STOP"}),
        ("entry", {"reason": "TARGET"}),
        ("entry", {"reason": "TIME"}),
        ("entry", {"execution_price": "0"}),
        ("entry", {"cash_delta": "-202"}),
        ("entry", {"candle_close_time": datetime(2026, 1, 1, tzinfo=UTC)}),
        ("entry", {"candle_close_time": datetime(2025, 12, 31, 23, tzinfo=UTC)}),
        ("entry", {"available_at": datetime(2026, 1, 1, tzinfo=UTC)}),
        ("entry", {"recorded_at": datetime(2026, 1, 1, tzinfo=UTC)}),
        ("exit", {"reason": "TIME"}),
        ("exit", {"phase": "CLOSE"}),
        ("exit", {"side": "BUY"}),
        ("exit", {"cash_delta": "237.6"}),
        ("exit", {"cash_delta": "-232.848"}),
    ],
)
def test_entry_and_exit_fills_reject_contradictory_direct_construction(
    record: str, change: dict[str, Any]
) -> None:
    from crypteo.domain.execution import Fill

    with pytest.raises(ValidationError):
        Fill(**(execution_records()[record] | change))


@pytest.mark.parametrize("model_name", ["rejection", "gap"])
@pytest.mark.parametrize("order_id", ["", "   "])
def test_rejection_and_gap_reject_blank_ids(model_name: str, order_id: str) -> None:
    from crypteo.domain.execution import DataGap, EntryRejection

    model = EntryRejection if model_name == "rejection" else DataGap
    with pytest.raises(ValidationError):
        model(**(execution_records()[model_name] | {"order_id": order_id}))


@pytest.mark.parametrize(
    "change",
    [
        {"expected_completion": datetime(2026, 1, 1, 1, tzinfo=UTC)},
        {"expected_completion": datetime(2026, 1, 1, tzinfo=UTC)},
        {"detected_at": datetime(2026, 1, 1, 1, tzinfo=UTC)},
    ],
)
def test_gap_rejects_impossible_times(change: dict[str, Any]) -> None:
    from crypteo.domain.execution import DataGap

    with pytest.raises(ValidationError):
        DataGap(**(execution_records()["gap"] | change))


@pytest.mark.parametrize(
    "change",
    [
        {"quantity": "1"},
        {"cost_basis": "202"},
        {"holding_bars": 2},
        {
            "holding_bars": 4,
            "last_candle_open_time": datetime(2026, 1, 1, 3, tzinfo=UTC),
        },
        {"last_candle_open_time": datetime(2025, 12, 31, 23, tzinfo=UTC)},
        {"last_candle_open_time": datetime(2026, 1, 1, 0, 1, tzinfo=UTC)},
    ],
)
def test_position_rejects_inconsistent_quantity_cost_or_history(
    change: dict[str, Any],
) -> None:
    from crypteo.domain.execution import Position

    with pytest.raises(ValidationError):
        Position(**(execution_records()["position"] | change))


@pytest.mark.parametrize(
    "change",
    [
        {"order_id": "different"},
        {"quantity": "1", "cash_delta": "-103.02"},
        {"candle_open_time": datetime(2025, 12, 31, 23, tzinfo=UTC)},
        {"candle_open_time": datetime(2026, 1, 1, 0, 30, tzinfo=UTC)},
    ],
)
@pytest.mark.parametrize("record_name", ["position", "trade"])
def test_lifecycle_rejects_entry_not_matching_authorized_terms(
    change: dict[str, Any],
    record_name: str,
) -> None:
    from crypteo.domain.execution import ClosedTrade, Fill, Position

    records = execution_records()
    entry = Fill(**(records["entry"] | change))
    model = Position if record_name == "position" else ClosedTrade
    with pytest.raises(ValidationError):
        model(**(records[record_name] | {"entry_fill": entry}))


@pytest.mark.parametrize(
    "change",
    [
        {"gross_pnl": "35.61"},
        {"net_pnl": "35.6"},
        {"holding_bars": 1},
        {"holding_bars": 3},
    ],
)
def test_closed_trade_rejects_inconsistent_pnl_and_holding_bars(
    change: dict[str, Any],
) -> None:
    from crypteo.domain.execution import ClosedTrade

    with pytest.raises(ValidationError):
        ClosedTrade(**(execution_records()["trade"] | change))


@pytest.mark.parametrize(
    "change",
    [
        {"order_id": "different"},
        {"quantity": "1", "cash_delta": "114.048"},
        {"reason": "TIME", "phase": "CLOSE"},
        {
            "candle_open_time": datetime(2025, 12, 31, 23, tzinfo=UTC),
            "candle_close_time": datetime(2026, 1, 1, tzinfo=UTC),
        },
        {"candle_open_time": datetime(2026, 1, 1, 1, 30, tzinfo=UTC)},
        {"candle_close_time": datetime(2026, 1, 1, 1, 30, tzinfo=UTC)},
    ],
)
def test_closed_trade_rejects_wrong_exit_identity_quantity_and_time(
    change: dict[str, Any],
) -> None:
    from crypteo.domain.execution import ClosedTrade, Fill

    records = execution_records()
    exit = Fill(**(records["exit"] | change))
    with pytest.raises(ValidationError):
        ClosedTrade(**(records["trade"] | {"exit_fill": exit}))


def test_closed_trade_rejects_exit_recorded_before_late_entry() -> None:
    from crypteo.domain.execution import ClosedTrade, Fill

    records = execution_records()
    entry = Fill(
        **(records["entry"] | {"recorded_at": datetime(2026, 1, 1, 3, tzinfo=UTC)})
    )
    with pytest.raises(ValidationError):
        ClosedTrade(**(records["trade"] | {"entry_fill": entry}))


@pytest.mark.parametrize("record_name", ["opened", "closed"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("cash", "1000"),
        ("initial_cash", "999"),
        ("cumulative_fees", "0"),
        ("cumulative_net_realized_pnl", "999"),
        ("orders", []),
        ("fills", []),
    ],
)
def test_snapshot_rejects_unreconciled_totals_or_missing_history(
    record_name: str,
    field: str,
    value: Any,
) -> None:
    with pytest.raises(ValidationError):
        PortfolioSnapshot(**(execution_records()[record_name] | {field: value}))


@pytest.mark.parametrize(
    "case",
    [
        "pending_and_open",
        "unlinked_pending",
        "different_active_terms",
        "duplicate_orders",
        "orphan_order",
        "extra_fill",
        "duplicate_trade",
        "extra_trade",
        "different_trade_terms",
        "missing_trade",
        "rejected_and_traded",
        "duplicate_rejection",
        "unlinked_rejection",
        "flat_with_gap",
        "wrong_gap_id",
        "gap_skips_candle",
        "early_rejection",
        "missing_position",
        "reversed_fills",
        "historical_negative_cash",
    ],
)
def test_snapshot_rejects_contradictory_lifecycle_records(case: str) -> None:
    records = execution_records()
    opened, closed = records["opened"], records["closed"]
    rejection = records["rejection"]
    other_order = records["order"] | {"order_id": "other"}
    rejected = {
        "initial_cash": "1000",
        "cash": "1000",
        "orders": [records["order"]],
        "rejections": [rejection],
    }
    cases = {
        "pending_and_open": opened | {"pending": records["order"]},
        "unlinked_pending": {
            "initial_cash": "1000",
            "cash": "1000",
            "pending": other_order,
        },
        "different_active_terms": opened
        | {"orders": [records["order"] | {"target_price": "125"}]},
        "duplicate_orders": closed | {"orders": [records["order"], records["order"]]},
        "orphan_order": closed | {"orders": [records["order"], other_order]},
        "extra_fill": closed | {"fills": [*closed["fills"], records["entry"]]},
        "duplicate_trade": closed
        | {"closed_trades": [records["trade"], records["trade"]]},
        "extra_trade": opened | {"closed_trades": [records["trade"]]},
        "different_trade_terms": closed
        | {"orders": [records["order"] | {"target_price": "125"}]},
        "missing_trade": closed | {"closed_trades": []},
        "rejected_and_traded": closed | {"rejections": [rejection]},
        "duplicate_rejection": rejected | {"rejections": [rejection, rejection]},
        "unlinked_rejection": rejected
        | {"rejections": [rejection | {"order_id": "other"}]},
        "flat_with_gap": closed | {"data_gap": records["gap"]},
        "wrong_gap_id": opened | {"data_gap": records["gap"] | {"order_id": "other"}},
        "gap_skips_candle": opened
        | {
            "data_gap": records["gap"]
            | {"missing_open_time": datetime(2026, 1, 1, tzinfo=UTC)}
        },
        "early_rejection": rejected
        | {
            "rejections": [
                rejection | {"recorded_at": datetime(2026, 1, 1, tzinfo=UTC)}
            ]
        },
        "missing_position": opened | {"position": None},
        "reversed_fills": closed | {"fills": list(reversed(closed["fills"]))},
        "historical_negative_cash": closed | {"initial_cash": "200", "cash": "228.828"},
    }
    with pytest.raises(ValidationError):
        PortfolioSnapshot(**cases[case])


def test_snapshot_rejects_overlapping_sequential_trades_despite_balanced_totals() -> (
    None
):
    records = execution_records()
    other = records["trade"] | {
        "order": records["order"] | {"order_id": "other"},
        "entry_fill": records["entry"] | {"order_id": "other"},
        "exit_fill": records["exit"] | {"order_id": "other"},
    }
    with pytest.raises(ValidationError, match="prior lifecycle completion"):
        PortfolioSnapshot(
            **(
                records["closed"]
                | {
                    "orders": [records["order"], other["order"]],
                    "closed_trades": [records["trade"], other],
                    "fills": [
                        records["entry"],
                        records["exit"],
                        other["entry_fill"],
                        other["exit_fill"],
                    ],
                    "cash": "1057.656",
                    "cumulative_fees": "13.544",
                    "cumulative_net_realized_pnl": "57.656",
                }
            )
        )


def test_nested_unchecked_model_copies_are_revalidated() -> None:
    from crypteo.domain.execution import ClosedTrade, Fill, Position

    records = execution_records()
    bad_fill = Fill(**records["entry"]).model_copy(update={"side": "SELL"})
    with pytest.raises(ValidationError):
        Position(**(records["position"] | {"entry_fill": bad_fill}))
    bad_position = Position(**records["position"]).model_copy(
        update={"cost_basis": Decimal(0)}
    )
    with pytest.raises(ValidationError):
        PortfolioSnapshot(**(records["opened"] | {"position": bad_position}))
    bad_trade = ClosedTrade(**records["trade"]).model_copy(
        update={"net_pnl": Decimal(0)}
    )
    with pytest.raises(ValidationError):
        PortfolioSnapshot(**(records["closed"] | {"closed_trades": [bad_trade]}))


def test_valid_records_reconcile_and_round_trip_under_hostile_decimal_context() -> None:
    from decimal import Inexact, Rounded, localcontext

    from crypteo.domain.execution import (
        ClosedTrade,
        DataGap,
        EntryRejection,
        Fill,
        Position,
    )

    records = execution_records()
    with localcontext() as context:
        context.prec = 2
        context.Emax = 2
        context.Emin = -2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        context.clear_flags()
        for model, name in (
            (Fill, "entry"),
            (Fill, "exit"),
            (Position, "position"),
            (ClosedTrade, "trade"),
            (EntryRejection, "rejection"),
            (DataGap, "gap"),
            (PortfolioSnapshot, "opened"),
            (PortfolioSnapshot, "closed"),
        ):
            instance = model.model_validate(records[name])
            assert model.model_validate(instance) == instance
            assert model.model_validate_json(instance.model_dump_json()) == instance
        assert not any(context.flags.values())
