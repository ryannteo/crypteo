import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any

import pytest
from decimal_support import hostile_decimal_context
from pydantic import ValidationError

from crypteo.analytics import mean_true_range, rolling_bollinger_statistics
from crypteo.domain.market import Candle, Timeframe, TradingPair, Venue
from crypteo.domain.trade_terms import TradeTerms, TradeTermsConfig, TradeTermsRejection
from crypteo.temporal import PointInTimeDataGateway, SimulationClock
from crypteo.trade_terms import TradeTermsService

D = Decimal
T = datetime(2026, 1, 2, tzinfo=UTC)
HOUR = timedelta(hours=1)


def bar(hour: int, **changes: Any) -> Candle:
    start = T + hour * HOUR
    return Candle.model_validate(
        dict(
            venue=Venue.COINBASE,
            pair=TradingPair.BTC_USD,
            timeframe=Timeframe.ONE_HOUR,
            open_time=start,
            close_time=start + HOUR,
            available_at=start + HOUR,
            open="100",
            high="101",
            low="99",
            close="100",
            volume="1",
        )
        | changes
    )


def golden() -> list[Candle]:
    # 19 ranges of 2, then max(91-89, |91-100|, |89-100|) = 11.
    return [bar(i) for i in range(-21, -1)] + [
        bar(-1, open="90", high="91", low="89", close="90")
    ]


def service(
    candles: list[Candle] | None = None, *, tick: str = "0.25", now: datetime = T
) -> TradeTermsService:
    return TradeTermsService(
        PointInTimeDataGateway(
            SimulationClock(now), golden() if candles is None else candles
        ),
        TradeTermsConfig(price_increment=D(tick)),
    )


def terms(result: TradeTerms | TradeTermsRejection) -> TradeTerms:
    assert isinstance(result, TradeTerms), result
    return result


def rejected(result: TradeTerms | TradeTermsRejection, reason: str) -> None:
    assert isinstance(result, TradeTermsRejection)
    assert result.reason == reason
    assert result.decision_at == T
    assert TradeTermsRejection.model_validate_json(result.model_dump_json()) == result


def test_hand_checked_golden_and_sma_reuse() -> None:
    result = terms(service().generate(pair=TradingPair.BTC_USD))
    assert result.mean_true_range == D("2.45")
    assert result.reference_price == D(90)
    assert result.target_sma == D("99.5")
    assert result.reference_price - 2 * result.mean_true_range == D("85.1")
    assert str(result.stop_price) == "85.00"
    assert str(result.target_price) == "99.50"
    statistics = rolling_bollinger_statistics(golden())[-1]
    assert statistics is not None and result.target_sma == statistics.sma
    assert result.decision_at == T
    assert result.venue is Venue.COINBASE
    assert result.timeframe is Timeframe.ONE_HOUR


@pytest.mark.parametrize(
    "tick", ["0.25", "0.3", "2.5", "0.0003", "0.000000000000000000000000000003"]
)
def test_arbitrary_increment_floor_matches_independent_rational_oracle(
    tick: str,
) -> None:
    result = terms(service(tick=tick).generate(pair=TradingPair.BTC_USD))
    step = Fraction(tick)
    for raw, aligned in (("85.1", result.stop_price), ("99.5", result.target_price)):
        assert Fraction(aligned) == (Fraction(raw) // step) * step
        assert Fraction(aligned) <= Fraction(raw) < Fraction(aligned) + step


@pytest.mark.parametrize("missing", range(21))
@pytest.mark.parametrize("delayed", [False, True])
def test_every_required_candle_must_be_visible_without_fallback(
    missing: int, delayed: bool
) -> None:
    candles = golden()
    if delayed:
        candles[missing] = Candle.model_validate(
            candles[missing].model_dump()
            | {"available_at": T + timedelta(microseconds=1)}
        )
    else:
        candles.pop(missing)
    # Plenty of older history must not replace even one missing required bar.
    candles = [bar(i) for i in range(-60, -21)] + candles
    rejected(
        service(candles).generate(pair=TradingPair.BTC_USD),
        "REFERENCE_UNAVAILABLE" if missing == 20 else "HISTORY_UNAVAILABLE",
    )


@pytest.mark.parametrize("count", [0, 1, 19, 20])
def test_no_reduced_window(count: int) -> None:
    candles = golden()[-count:] if count else []
    rejected(
        service(candles).generate(pair=TradingPair.BTC_USD),
        "HISTORY_UNAVAILABLE" if count else "REFERENCE_UNAVAILABLE",
    )


def test_delayed_internal_data_visible_exactly_at_t_is_usable() -> None:
    candles = golden()
    candles[0] = Candle.model_validate(candles[0].model_dump() | {"available_at": T})
    assert service(candles).generate(pair=TradingPair.BTC_USD) == service().generate(
        pair=TradingPair.BTC_USD
    )


def test_delayed_reference_never_uses_old_decision_time() -> None:
    candles = golden()
    candles[-1] = Candle.model_validate(
        candles[-1].model_dump() | {"available_at": T + HOUR}
    )
    clock = SimulationClock(T)
    gate = TradeTermsService(
        PointInTimeDataGateway(clock, candles),
        TradeTermsConfig(price_increment=D("0.25")),
    )
    rejected(gate.generate(pair=TradingPair.BTC_USD), "REFERENCE_UNAVAILABLE")
    clock.advance_to(T + HOUR)
    result = gate.generate(pair=TradingPair.BTC_USD)
    assert isinstance(result, TradeTermsRejection)
    assert result.reason == "REFERENCE_UNAVAILABLE" and result.decision_at == clock.now


@pytest.mark.parametrize("next_price", ["1", "90", "999999"])
def test_future_next_open_other_pair_and_old_history_cannot_change_terms(
    next_price: str,
) -> None:
    candles = golden() + [
        bar(0, open=next_price, high="999999", low="1"),
        bar(1, high="999999", low="1"),
        bar(-1, pair=TradingPair.ETH_USD, close="999999", high="999999"),
        bar(-22, close="999999", high="999999"),
    ]
    clock = SimulationClock(T)
    gate = TradeTermsService(
        PointInTimeDataGateway(clock, reversed(candles)),
        TradeTermsConfig(price_increment=D("0.25")),
    )
    assert gate.generate(pair=TradingPair.BTC_USD) == service().generate(
        pair=TradingPair.BTC_USD
    )
    assert clock.now == T


@pytest.mark.parametrize("pair", list(TradingPair))
def test_pair_is_the_only_generation_input_and_market_is_isolated(
    pair: TradingPair,
) -> None:
    candles = [Candle.model_validate(c.model_dump() | {"pair": pair}) for c in golden()]
    assert terms(service(candles).generate(pair=pair)).pair is pair
    assert set(inspect.signature(TradeTermsService).parameters) == {"gateway", "config"}
    parameters = inspect.signature(TradeTermsService.generate).parameters
    assert set(parameters) == {"self", "pair"}
    assert parameters["pair"].kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize(
    "case,reason",
    [
        ("zero", "ZERO_MEAN_TRUE_RANGE"),
        ("zero_stop", "INVALID_RAW_STOP"),
        ("negative_stop", "INVALID_RAW_STOP"),
        ("equal_target", "INVALID_RAW_TARGET"),
        ("lower_target", "INVALID_RAW_TARGET"),
        ("target_collapsed", "INVALID_ALIGNED_LEVELS"),
        ("stop_collapsed", "INVALID_ALIGNED_LEVELS"),
    ],
)
def test_invalid_and_collapsed_levels_are_rejected_without_adjustment(
    case: str, reason: str
) -> None:
    candles, tick = golden(), "0.25"
    if case == "zero":
        candles = [bar(i, high="100", low="100") for i in range(-21, 0)]
    elif case in ("zero_stop", "negative_stop"):
        candles = [
            Candle.model_validate(
                c.model_dump()
                | {"high": "112.5" if case == "zero_stop" else "200", "low": "67.5"}
            )
            for c in candles
        ]
    elif case == "equal_target":
        candles = [bar(i) for i in range(-21, 0)]
    elif case == "lower_target":
        candles[-1] = bar(-1, close="110", high="110")
    elif case == "target_collapsed":
        tick = "10"
    else:
        # Mean range 44.25 gives raw stop 1.50, target remains 99.5.
        candles[:-1] = [bar(i, high="123", low="77") for i in range(-21, -1)]
        tick = "5"
    rejected(service(candles, tick=tick).generate(pair=TradingPair.BTC_USD), reason)


def test_terms_rejection_precedence() -> None:
    zero = [bar(i, high="100", low="100") for i in range(-21, 0)]
    rejected(
        service(zero[1:]).generate(pair=TradingPair.BTC_USD), "HISTORY_UNAVAILABLE"
    )
    rejected(service(zero).generate(pair=TradingPair.BTC_USD), "ZERO_MEAN_TRUE_RANGE")
    # Both raw levels invalid and tick unusable: stop takes precedence.
    invalid = [bar(i, high="200", low="1") for i in range(-21, 0)]
    rejected(
        service(invalid, tick="1000").generate(pair=TradingPair.BTC_USD),
        "INVALID_RAW_STOP",
    )
    rejected(
        service([bar(i) for i in range(-21, 0)], tick="1000").generate(
            pair=TradingPair.BTC_USD
        ),
        "INVALID_RAW_TARGET",
    )


def test_hostile_context_repeatability_and_contract_revalidation() -> None:
    expected = terms(service(tick="0.3").generate(pair=TradingPair.BTC_USD))
    with hostile_decimal_context() as hostile:
        before = repr(hostile)
        gate = service(tick="0.3")
        for _ in range(3):
            result = terms(gate.generate(pair=TradingPair.BTC_USD))
            assert result.model_dump_json() == expected.model_dump_json()
            assert TradeTerms.model_validate(result) == expected
            assert TradeTerms.model_validate_json(result.model_dump_json()) == expected
        assert repr(hostile) == before


@pytest.mark.parametrize(
    "field,value", [("close", D("NaN")), ("high", D(1)), ("volume", D(-1))]
)
def test_corrupt_visible_candles_raise_even_with_incomplete_history(
    field: str, value: Decimal
) -> None:
    corrupt = bar(-2).model_copy(update={field: value})
    with pytest.raises(ValidationError):
        service([corrupt]).generate(pair=TradingPair.BTC_USD)


def test_malformed_pair_and_unchecked_configuration_raise() -> None:
    with pytest.raises(ValidationError):
        service([]).generate(pair="BTC-USDT")  # type: ignore[arg-type]
    config = TradeTermsConfig(price_increment=D("0.25"))
    for changes in ({"price_increment": D(0)}, {"stop_true_range_multiple": D(3)}):
        with pytest.raises(ValidationError):
            TradeTermsService(
                PointInTimeDataGateway(SimulationClock(T), []),
                config.model_copy(update=changes),
            )


def test_off_hour_clock_has_no_exact_reference() -> None:
    result = service(now=T + timedelta(microseconds=1)).generate(
        pair=TradingPair.BTC_USD
    )
    assert (
        isinstance(result, TradeTermsRejection)
        and result.reason == "REFERENCE_UNAVAILABLE"
    )


def test_true_range_uses_first_close_but_not_first_range() -> None:
    candles = golden()
    candles[0] = bar(-21, high="999999", low="0.0001")
    assert mean_true_range(candles) == D("2.45")
    candles[0] = bar(-21, open="110", high="110", low="110", close="110")
    # First TR changes from 2 to 11: (49 + 9) / 20 = 2.9.
    assert mean_true_range(candles) == D("2.9")
