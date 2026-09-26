"""Private, caller-independent arithmetic for fixed market research ratios."""

from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    Underflow,
    localcontext,
)

_CONTEXT = Context(
    prec=50,
    rounding=ROUND_HALF_EVEN,
    Emin=-999999,
    Emax=999999,
    capitals=1,
    clamp=0,
    flags=[],
    traps=[InvalidOperation, DivisionByZero, Overflow, Underflow],
)


def _divide(numerator: Decimal, denominator: Decimal) -> Decimal:
    with localcontext(_CONTEXT):
        return numerator / denominator


def _return(current: Decimal, previous: Decimal) -> Decimal:
    with localcontext(_CONTEXT):
        return current / previous - 1


def _difference(left: Decimal, right: Decimal) -> Decimal:
    with localcontext(_CONTEXT):
        return left - right


def _mean(values: tuple[Decimal, ...]) -> Decimal:
    with localcontext(_CONTEXT):
        return sum(values, Decimal(0)) / len(values)


def _bands(sma: Decimal, std: Decimal) -> tuple[Decimal, Decimal]:
    with localcontext(_CONTEXT):
        return sma - 2 * std, sma + 2 * std
