"""Validated market-data value objects for Crypteo V1."""

from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from crypteo.domain.time import UtcDatetime


class Venue(StrEnum):
    """Canonical market venue identifiers supported by V1."""

    COINBASE = "COINBASE"


class TradingPair(StrEnum):
    """Coinbase spot trading pairs supported by V1."""

    BTC_USD = "BTC-USD"
    ETH_USD = "ETH-USD"
    SOL_USD = "SOL-USD"


class Timeframe(StrEnum):
    """Normalized candle timeframes supported by V1."""

    ONE_HOUR = "1h"

    @property
    def duration(self) -> timedelta:
        """Return the exact duration of this timeframe."""
        return timedelta(hours=1)


def _reject_float(value: Any) -> Any:
    if isinstance(value, float):
        raise ValueError("financial values must not be provided as float")
    return value


ExactDecimal = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    Field(allow_inf_nan=False),
]
PositiveDecimal = Annotated[ExactDecimal, Field(gt=0)]
NonNegativeDecimal = Annotated[ExactDecimal, Field(ge=0)]


class Candle(BaseModel):
    """An immutable normalized OHLCV record covering ``[open_time, close_time)``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    venue: Venue
    pair: TradingPair
    timeframe: Timeframe
    open_time: UtcDatetime
    close_time: UtcDatetime
    available_at: UtcDatetime
    open: PositiveDecimal
    high: PositiveDecimal
    low: PositiveDecimal
    close: PositiveDecimal
    volume: NonNegativeDecimal

    @field_validator("open_time")
    @classmethod
    def validate_open_time_alignment(cls, value: datetime) -> datetime:
        if value.minute != 0 or value.second != 0 or value.microsecond != 0:
            raise ValueError("open_time must align to the beginning of an hour")
        return value

    @model_validator(mode="after")
    def validate_interval_and_prices(self) -> Self:
        if self.close_time != self.open_time + self.timeframe.duration:
            raise ValueError("close_time must be exactly one timeframe after open_time")
        if self.available_at < self.close_time:
            raise ValueError("available_at must be at or after close_time")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be at least open, close, and low")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be at most open, close, and high")
        return self


class DatasetManifest(BaseModel):
    """Metadata for one normalized dataset covering ``[start_time, end_time)``.

    ``start_time`` is the first candle's open time. ``end_time`` is the final
    candle's close time, not its open time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1"]
    dataset_version: str
    source: str
    venue: Venue
    pair: TradingPair
    timeframe: Timeframe
    start_time: UtcDatetime
    end_time: UtcDatetime
    created_at: UtcDatetime
    row_count: int = Field(strict=True, ge=0)
    missing_open_times: tuple[UtcDatetime, ...] = ()

    @field_validator("dataset_version", "source")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identifying and version fields must be non-empty")
        return value

    @model_validator(mode="after")
    def validate_coverage(self) -> Self:
        if self.end_time < self.start_time:
            raise ValueError("end_time must be at or after start_time")
        if self.row_count == 0:
            if self.missing_open_times:
                raise ValueError("an empty dataset must not contain missing open times")
            return self
        if self.end_time == self.start_time:
            raise ValueError("a non-empty dataset must have a non-empty interval")
        for boundary in (self.start_time, self.end_time):
            if (
                boundary.minute != 0
                or boundary.second != 0
                or boundary.microsecond != 0
            ):
                raise ValueError("dataset boundaries must align to the timeframe")
        coverage = self.end_time - self.start_time
        if coverage % self.timeframe.duration != timedelta(0):
            raise ValueError("dataset coverage must contain integral timeframe slots")
        if len(self.missing_open_times) != len(set(self.missing_open_times)):
            raise ValueError("missing_open_times must be unique")
        if self.missing_open_times != tuple(sorted(self.missing_open_times)):
            raise ValueError("missing_open_times must be strictly ascending")
        final_open_time = self.end_time - self.timeframe.duration
        for missing_open_time in self.missing_open_times:
            if (
                missing_open_time.minute != 0
                or missing_open_time.second != 0
                or missing_open_time.microsecond != 0
            ):
                raise ValueError("missing open times must align to the timeframe")
            if not self.start_time < missing_open_time < final_open_time:
                raise ValueError("missing open times must be within dataset coverage")
        expected_slots = coverage // self.timeframe.duration
        if expected_slots != self.row_count + len(self.missing_open_times):
            raise ValueError(
                "row count and missing open times must account for coverage"
            )
        return self
