from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from crypteo.domain.trade_terms import TradeTerms, TradeTermsConfig, TradeTermsRejection

D = Decimal
CONFIG = {"price_increment": "0.25", "stop_true_range_multiple": "2"}
TERMS: dict[str, Any] = {
    "venue": "COINBASE",
    "pair": "BTC-USD",
    "timeframe": "1h",
    "decision_at": datetime(2026, 1, 1, tzinfo=UTC),
    "config": CONFIG,
    "reference_price": "90",
    "mean_true_range": "2.45",
    "target_sma": "99.5",
    "stop_price": "85.00",
    "target_price": "99.50",
}


@pytest.mark.parametrize("field", list(TERMS))
def test_all_terms_fields_required(field: str) -> None:
    data = TERMS.copy()
    del data[field]
    with pytest.raises(ValidationError):
        TradeTerms.model_validate(data)


@pytest.mark.parametrize(
    "field",
    ["reference_price", "mean_true_range", "target_sma", "stop_price", "target_price"],
)
@pytest.mark.parametrize(
    "value", [0, -1, "NaN", "Infinity", "-Infinity", 1.5, True, None]
)
def test_financial_fields_require_positive_exact_finite_values(
    field: str, value: Any
) -> None:
    with pytest.raises(ValidationError):
        TradeTerms.model_validate(TERMS | {field: value})


@pytest.mark.parametrize("value", [0, -1, "NaN", "Infinity", 0.25, True, None])
def test_price_increment_validation(value: Any) -> None:
    with pytest.raises(ValidationError):
        TradeTermsConfig.model_validate(CONFIG | {"price_increment": value})


@pytest.mark.parametrize(
    "value",
    [
        "1.99999999999999999999999999",
        "2.00000000000000000000000001",
        0,
        3,
        "NaN",
        2.0,
        True,
    ],
)
def test_multiple_is_exactly_two(value: Any) -> None:
    with pytest.raises(ValidationError):
        TradeTermsConfig.model_validate(CONFIG | {"stop_true_range_multiple": value})


def test_config_requires_tick_and_defaults_only_the_locked_multiple() -> None:
    assert TradeTermsConfig(price_increment=D("0.25")).stop_true_range_multiple == D(2)
    with pytest.raises(ValidationError):
        TradeTermsConfig.model_validate({})


@pytest.mark.parametrize(
    "field,value",
    [
        ("venue", "OTHER"),
        ("pair", "BTC-USDT"),
        ("timeframe", "5m"),
        ("decision_at", datetime(2026, 1, 1)),
        ("decision_at", datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8)))),
        ("decision_at", datetime(2026, 1, 1, 0, 1, tzinfo=UTC)),
        ("decision_at", datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)),
        ("decision_at", datetime(2026, 1, 1, 0, 0, 0, 1, tzinfo=UTC)),
        ("decision_at", "2026-01-01T00:00:00Z"),
    ],
)
def test_identity_and_time_validation(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        TradeTerms.model_validate(TERMS | {field: value})


@pytest.mark.parametrize(
    "change",
    [
        {"stop_price": D("84.75")},  # Aligned and plausible, but wrong formula.
        {"stop_price": D("85.1")},  # Raw level was not floored.
        {"target_price": D("99.25")},
        {"target_price": D("99.51")},
        {"mean_true_range": D("3")},
        {"target_sma": D("100")},
        {"reference_price": D("91")},
        {"mean_true_range": D("45")},
        {"target_sma": D("90")},
        {"stop_price": D("90")},
        {"target_price": D("90")},
        {"config": TradeTermsConfig(price_increment=D("0.3"))},
    ],
)
def test_forged_terms_rejected_in_construction_instance_and_json(
    change: dict[str, Any],
) -> None:
    valid = TradeTerms.model_validate(TERMS)
    forged = valid.model_copy(update=change)
    with pytest.raises(ValidationError):
        TradeTerms.model_validate(valid.model_dump() | change)
    with pytest.raises(ValidationError):
        TradeTerms.model_validate(forged)
    with pytest.raises(ValidationError):
        TradeTerms.model_validate_json(forged.model_dump_json())


def test_unchecked_nested_config_is_revalidated() -> None:
    valid = TradeTerms.model_validate(TERMS)
    for changes in ({"price_increment": D(0)}, {"stop_true_range_multiple": D(3)}):
        forged = valid.config.model_copy(update=changes)
        with pytest.raises(ValidationError):
            TradeTerms.model_validate(valid.model_copy(update={"config": forged}))


@pytest.mark.parametrize(
    "extra",
    [
        "opportunity",
        "decision",
        "portfolio",
        "quantity",
        "fee",
        "order_id",
        "model",
        "raw_stop_price",
    ],
)
def test_terms_do_not_grow_unrequested_fields(extra: str) -> None:
    with pytest.raises(ValidationError):
        TradeTerms.model_validate(TERMS | {extra: "unexpected"})


def test_contracts_are_frozen_and_round_trip_with_closed_rejection_codes() -> None:
    valid = TradeTerms.model_validate(TERMS)
    assert TradeTerms.model_validate_json(valid.model_dump_json()) == valid
    with pytest.raises(ValidationError):
        valid.stop_price = D(80)
    with pytest.raises(ValidationError):
        valid.config.price_increment = D(1)
    with pytest.raises(ValidationError):
        TradeTermsConfig.model_validate(CONFIG | {"fallback": True})
    data = {
        key: TERMS[key]
        for key in ("venue", "pair", "timeframe", "decision_at", "config")
    }
    rejection = TradeTermsRejection.model_validate(
        data | {"reason": "HISTORY_UNAVAILABLE"}
    )
    assert (
        TradeTermsRejection.model_validate_json(rejection.model_dump_json())
        == rejection
    )
    with pytest.raises(ValidationError):
        rejection.reason = "REFERENCE_UNAVAILABLE"
    with pytest.raises(ValidationError):
        TradeTermsRejection.model_validate(data | {"reason": "FALLBACK"})
