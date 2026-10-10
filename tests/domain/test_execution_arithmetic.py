from collections.abc import Callable
from decimal import ROUND_DOWN, Decimal, Inexact, Rounded, getcontext, localcontext

import pytest

from crypteo.domain._execution_arithmetic import _add, _multiply, _subtract

Helper = Callable[[Decimal, Decimal], Decimal]

HELPERS = [
    pytest.param(_add, id="add"),
    pytest.param(_subtract, id="subtract"),
    pytest.param(_multiply, id="multiply"),
]

LARGE = Decimal("1" + "0" * 40)
TINY = Decimal("0." + "0" * 39 + "1")
WIDE = Decimal("1" + "0" * 30 + "1")

EXACT_CASES = [
    pytest.param(_add, LARGE, TINY, "1" + "0" * 40 + "." + "0" * 39 + "1", id="add"),
    pytest.param(
        _subtract, LARGE, TINY, "9" * 40 + "." + "9" * 39 + "9", id="subtract"
    ),
    pytest.param(
        _multiply,
        Decimal("123456789.123456789"),
        Decimal("987654321.987654321"),
        "121932631356500531.347203169112635269",
        id="multiply",
    ),
    # (10**31 + 1)**2 has 63 significant digits.
    pytest.param(
        _multiply, WIDE, WIDE, "1" + "0" * 30 + "2" + "0" * 30 + "1", id="multiply-wide"
    ),
]

NON_FINITE = [
    pytest.param(Decimal("NaN"), id="nan"),
    pytest.param(Decimal("sNaN"), id="snan"),
    pytest.param(Decimal("Infinity"), id="infinity"),
    pytest.param(Decimal("-Infinity"), id="negative-infinity"),
]


@pytest.mark.parametrize(("helper", "left", "right", "expected"), EXACT_CASES)
def test_helpers_are_exact_beyond_default_precision(
    helper: Helper, left: Decimal, right: Decimal, expected: str
) -> None:
    result = helper(left, right)
    assert result == Decimal(expected)
    assert str(result) == expected


@pytest.mark.parametrize(("helper", "left", "right", "expected"), EXACT_CASES)
def test_helpers_ignore_hostile_ambient_context(
    helper: Helper, left: Decimal, right: Decimal, expected: str
) -> None:
    with localcontext() as hostile:
        hostile.prec = 3
        hostile.rounding = ROUND_DOWN
        hostile.clear_traps()
        result = helper(left, right)
    assert result == Decimal(expected)
    assert str(result) == expected


@pytest.mark.parametrize("hostile", [False, True], ids=["default", "hostile"])
@pytest.mark.parametrize(("helper", "left", "right", "expected"), EXACT_CASES)
def test_helpers_preserve_ambient_context(
    helper: Helper, left: Decimal, right: Decimal, expected: str, hostile: bool
) -> None:
    with localcontext() as ambient:
        if hostile:
            ambient.prec = 3
            ambient.rounding = ROUND_DOWN
            ambient.clear_traps()
        ambient.clear_flags()
        prec, rounding, flags = ambient.prec, ambient.rounding, dict(ambient.flags)
        helper(left, right)
        caller = getcontext()
        assert caller is ambient
        assert (caller.prec, caller.rounding, dict(caller.flags)) == (
            prec,
            rounding,
            flags,
        )
        assert not caller.flags[Inexact]
        assert not caller.flags[Rounded]


@pytest.mark.parametrize("value", NON_FINITE)
@pytest.mark.parametrize("helper", HELPERS)
def test_helpers_reject_non_finite_left_operand(helper: Helper, value: Decimal) -> None:
    with pytest.raises(ValueError, match="ledger arithmetic requires finite decimals"):
        helper(value, Decimal("1"))


@pytest.mark.parametrize("value", NON_FINITE)
@pytest.mark.parametrize("helper", HELPERS)
def test_helpers_reject_non_finite_right_operand(
    helper: Helper, value: Decimal
) -> None:
    with pytest.raises(ValueError, match="ledger arithmetic requires finite decimals"):
        helper(Decimal("1"), value)


def test_subtract_of_equal_values_is_exact_unsigned_zero() -> None:
    result = _subtract(Decimal("1.10"), Decimal("1.1"))
    assert result == 0
    assert not result.is_signed()
    assert str(result) == "0.00"


def test_multiply_keeps_sign_and_exact_exponent() -> None:
    result = _multiply(Decimal("-0.5"), Decimal("0.2"))
    assert result == Decimal("-0.10")
    assert str(result) == "-0.10"
