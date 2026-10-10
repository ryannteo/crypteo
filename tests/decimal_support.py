"""Hostile ambient Decimal context for proving code ignores the caller's context."""

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import ROUND_UP, Context, localcontext


@contextmanager
def hostile_decimal_context() -> Iterator[Context]:
    with localcontext() as hostile:
        hostile.prec, hostile.Emax, hostile.Emin, hostile.clamp = 1, 1, -1, 1
        hostile.rounding = ROUND_UP
        for signal in hostile.traps:
            hostile.traps[signal] = True
        hostile.clear_flags()
        yield hostile
