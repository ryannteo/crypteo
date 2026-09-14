"""Private exact execution prices and increment arithmetic for broker and risk."""

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
