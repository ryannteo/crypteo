"""Private exact execution prices and increment arithmetic for broker and risk."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from crypteo.domain._execution_arithmetic import _add, _multiply, _subtract
from crypteo.domain.execution import ExecutionConfig


class _UnexecutablePrice(ValueError):
    """Adverse execution rounds to a nonpositive price."""


def _increment_ratio(value: Decimal, increment: Decimal) -> tuple[int, int]:
    numerator, denominator = value.as_integer_ratio()
    step_numerator, step_denominator = increment.as_integer_ratio()
    return numerator * step_denominator, denominator * step_numerator


def _aligned(value: Decimal, increment: Decimal) -> bool:
    numerator, denominator = _increment_ratio(value, increment)
    return numerator % denominator == 0


def _floor_increment(value: Decimal, increment: Decimal) -> Decimal:
    """Floor to an arbitrary increment without Decimal division or quantize."""
    numerator, denominator = _increment_ratio(value, increment)
    return _multiply(Decimal(numerator // denominator), increment)


@dataclass(frozen=True)
class _ExecutionEstimate:
    price: Decimal
    cash_per_unit: Decimal


def _execution_estimate(
    reference: Decimal, side: Literal["BUY", "SELL"], config: ExecutionConfig
) -> _ExecutionEstimate:
    """Adverse execution and fee-inclusive cost (BUY) or proceeds (SELL)."""
    price = _execution_price(reference, side, config)
    factor = (
        _add(Decimal(1), config.entry_fee_rate)
        if side == "BUY"
        else _subtract(Decimal(1), config.exit_fee_rate)
    )
    return _ExecutionEstimate(price, _multiply(price, factor))


def _execution_price(
    reference: Decimal, side: Literal["BUY", "SELL"], config: ExecutionConfig
) -> Decimal:
    factor = (
        _add(Decimal(1), config.adverse_slippage)
        if side == "BUY"
        else _subtract(Decimal(1), config.adverse_slippage)
    )
    slipped = _multiply(reference, factor)
    numerator, denominator = _increment_ratio(slipped, config.price_increment)
    steps = (
        (numerator + denominator - 1) // denominator
        if side == "BUY"
        else numerator // denominator
    )
    if steps <= 0:
        raise _UnexecutablePrice(
            "execution price rounds to zero at configured increment"
        )
    return _multiply(Decimal(steps), config.price_increment)
