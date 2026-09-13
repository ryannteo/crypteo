"""Exact Decimal operations shared by execution contracts and ledger transitions."""

from decimal import (
    MAX_EMAX,
    MIN_EMIN,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    Inexact,
    InvalidOperation,
    Overflow,
)


def _context(left: Decimal, right: Decimal) -> Context:
    """Provision exact add/subtract/product precision, never silently round money.

    Policy is fixed and independent of ambient precision, rounding, exponent
    limits, flags and traps. Precision grows with operand widths so even large
    cash plus tiny fees remain exact, without an arbitrary financial digit cap.
    """
    left_exponent = left.as_tuple().exponent
    right_exponent = right.as_tuple().exponent
    if not isinstance(left_exponent, int) or not isinstance(right_exponent, int):
        raise ValueError("ledger arithmetic requires finite decimals")
    precision = (
        max(
            len(left.as_tuple().digits) + len(right.as_tuple().digits),
            max(left.adjusted(), right.adjusted()) - min(left_exponent, right_exponent),
        )
        + 2
    )
    return Context(
        prec=precision,
        rounding=ROUND_HALF_EVEN,
        Emin=MIN_EMIN,
        Emax=MAX_EMAX,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[Inexact, InvalidOperation, Overflow],
    )


def _add(left: Decimal, right: Decimal) -> Decimal:
    return _context(left, right).add(left, right)


def _subtract(left: Decimal, right: Decimal) -> Decimal:
    return _context(left, right).subtract(left, right)


def _multiply(left: Decimal, right: Decimal) -> Decimal:
    return _context(left, right).multiply(left, right)
