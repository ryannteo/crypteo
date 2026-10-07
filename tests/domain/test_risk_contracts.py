from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from crypteo.domain.risk import RiskConfig, RiskRejection, TradeProposal

PROPOSAL: dict[str, Any] = {
    "order_id": "proposal-1",
    "venue": "COINBASE",
    "pair": "BTC-USD",
    "timeframe": "1h",
    "decision_at": datetime(2026, 1, 1, tzinfo=UTC),
    "stop_price": "90",
    "target_price": "120",
}
RISK: dict[str, Any] = {"risk_fraction": "0.02", "max_holding_bars": 3}


@pytest.mark.parametrize("field", list(PROPOSAL))
def test_all_proposal_fields_required(field: str) -> None:
    data = PROPOSAL.copy()
    del data[field]
    with pytest.raises(ValidationError):
        TradeProposal.model_validate(data)


@pytest.mark.parametrize("field", list(RISK))
def test_all_risk_settings_required_without_defaults(field: str) -> None:
    data = RISK.copy()
    del data[field]
    with pytest.raises(ValidationError):
        RiskConfig.model_validate(data)


@pytest.mark.parametrize(
    "value", [0, -1, "1.00001", "NaN", "Infinity", "-Infinity", 0.01, True]
)
def test_risk_fraction_requires_exact_finite_proportion(value: Any) -> None:
    with pytest.raises(ValidationError):
        RiskConfig.model_validate(RISK | {"risk_fraction": value})


@pytest.mark.parametrize("value", [0, -1, True, "3", 3.0, 1.5])
def test_holding_limit_requires_strict_positive_integer(value: Any) -> None:
    with pytest.raises(ValidationError):
        RiskConfig.model_validate(RISK | {"max_holding_bars": value})


@pytest.mark.parametrize("field", ["stop_price", "target_price"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", 90.0, True, None])
def test_levels_require_exact_finite_values(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        TradeProposal.model_validate(PROPOSAL | {field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("order_id", ""),
        ("order_id", "  "),
        ("order_id", 123),
        ("venue", "OTHER"),
        ("pair", "BTC-USDT"),
        ("timeframe", "5m"),
        ("decision_at", datetime(2026, 1, 1)),
        ("decision_at", datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=8)))),
        ("decision_at", datetime(2026, 1, 1, 0, 1, tzinfo=UTC)),
        ("decision_at", datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)),
        ("decision_at", datetime(2026, 1, 1, 0, 0, 0, 1, tzinfo=UTC)),
        ("decision_at", "2026-01-01T00:00:00Z"),
        ("decision_at", 1767225600),
    ],
)
def test_proposal_rejects_malformed_identity_and_time(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        TradeProposal.model_validate(PROPOSAL | {field: value})
    with pytest.raises(ValidationError):
        TradeProposal.model_validate(
            TradeProposal.model_validate(PROPOSAL).model_copy(update={field: value})
        )


def test_proposal_boundary_accepts_zero_offset_and_round_trips_json() -> None:
    at = PROPOSAL["decision_at"].replace(tzinfo=timezone(timedelta(0), "zero"))
    proposal = TradeProposal.model_validate(PROPOSAL | {"decision_at": at})
    assert proposal.decision_at == at
    assert TradeProposal.model_validate_json(proposal.model_dump_json()) == proposal


@pytest.mark.parametrize(
    "field", ["quantity", "max_holding_bars", "decision", "direction", "risk_fraction"]
)
def test_proposal_has_no_research_decision_or_sizing_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        TradeProposal.model_validate(PROPOSAL | {field: "1"})


def test_frozen_contracts_round_trip_and_config_rejects_extras() -> None:
    proposal = TradeProposal.model_validate(PROPOSAL)
    risk = RiskConfig.model_validate(RISK)
    assert TradeProposal.model_validate_json(proposal.model_dump_json()) == proposal
    assert RiskConfig.model_validate_json(risk.model_dump_json()) == risk
    with pytest.raises(ValidationError):
        proposal.stop_price = Decimal(80)
    with pytest.raises(ValidationError):
        risk.risk_fraction = Decimal(1)
    with pytest.raises(ValidationError):
        RiskConfig.model_validate(RISK | {"gap_buffer": "0.1"})


def test_rejection_codes_are_closed_and_do_not_contain_an_order() -> None:
    data = {
        "proposal": PROPOSAL,
        "risk_config": RISK,
        "execution_config": {
            "entry_fee_rate": "0",
            "exit_fee_rate": "0",
            "adverse_slippage": "0",
            "price_increment": "0.1",
            "quantity_increment": "0.5",
        },
        "reason": "NO_CAPITAL",
    }
    result = RiskRejection.model_validate(data)
    assert "order" not in result.model_dump()
    with pytest.raises(ValidationError):
        result.reason = "PORTFOLIO_BUSY"
    with pytest.raises(ValidationError):
        RiskRejection.model_validate(data | {"reason": "ARBITRARY_REASON"})
