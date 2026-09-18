"""Immutable deterministic LONG levels, independent of research and execution."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from crypteo._execution_costs import _floor_increment
from crypteo.domain._execution_arithmetic import _multiply, _subtract
from crypteo.domain.market import PositiveDecimal, Timeframe, TradingPair, Venue
from crypteo.domain.time import UtcDatetime


class TradeTermsConfig(BaseModel):
    """Explicit tick size and the locked two-mean-true-range stop multiple."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    price_increment: PositiveDecimal
    stop_true_range_multiple: Annotated[PositiveDecimal, Field(ge=2, le=2)] = Decimal(
        "2"
    )


class TradeTerms(BaseModel):
    """Reconciled levels from runtime-owned statistics; no execution authority.

    Validation recomputes raw and aligned levels. Candle-derived statistics and
    their temporal visibility are established by TradeTermsService.
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    venue: Venue
    pair: TradingPair
    timeframe: Timeframe
    decision_at: UtcDatetime
    config: TradeTermsConfig
    reference_price: PositiveDecimal
    mean_true_range: PositiveDecimal
    target_sma: PositiveDecimal
    stop_price: PositiveDecimal
    target_price: PositiveDecimal

    @field_validator("decision_at")
    @classmethod
    def validate_boundary(cls, value: datetime) -> datetime:
        if value.minute or value.second or value.microsecond:
            raise ValueError("decision_at must be an hourly completed-bar boundary")
        return value

    @model_validator(mode="after")
    def validate_levels(self) -> Self:
        raw_stop = _subtract(
            self.reference_price,
            _multiply(self.mean_true_range, self.config.stop_true_range_multiple),
        )
        if not 0 < raw_stop < self.reference_price < self.target_sma:
            raise ValueError("invalid raw LONG levels")
        if not 0 < self.stop_price < self.reference_price < self.target_price:
            raise ValueError("invalid aligned LONG levels")
        if self.stop_price != _floor_increment(raw_stop, self.config.price_increment):
            raise ValueError("stop_price must equal the floored raw stop")
        if self.target_price != _floor_increment(
            self.target_sma, self.config.price_increment
        ):
            raise ValueError("target_price must equal the floored target SMA")
        return self


class TradeTermsRejection(BaseModel):
    """Ordinary unavailable history or unusable deterministic levels."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    venue: Venue
    pair: TradingPair
    timeframe: Timeframe
    decision_at: UtcDatetime
    config: TradeTermsConfig
    reason: Literal[
        "REFERENCE_UNAVAILABLE",
        "HISTORY_UNAVAILABLE",
        "ZERO_MEAN_TRUE_RANGE",
        "INVALID_RAW_STOP",
        "INVALID_RAW_TARGET",
        "INVALID_ALIGNED_LEVELS",
    ]
