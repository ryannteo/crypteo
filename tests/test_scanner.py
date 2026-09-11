import inspect
import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import ROUND_UP, Decimal, Inexact, Rounded, localcontext
from hashlib import sha256

import pytest

from crypteo.analytics import BollingerStatistics, rolling_bollinger_statistics
from crypteo.domain.market import Candle, Timeframe, TradingPair, Venue
from crypteo.domain.research import Opportunity
from crypteo.scanner import OpportunityScanner, _decimal_string
from crypteo.temporal import PointInTimeDataGateway, SimulationClock

BASE_TIME = datetime(2023, 1, 1, tzinfo=UTC)


def candle(
    hour: int,
    close: str = "100",
    *,
    pair: TradingPair = TradingPair.BTC_USD,
    available_at: datetime | None = None,
) -> Candle:
    open_time = BASE_TIME + timedelta(hours=hour)
    close_time = open_time + timedelta(hours=1)
    price = Decimal(close)
    return Candle(
        venue=Venue.COINBASE,
        pair=pair,
        timeframe=Timeframe.ONE_HOUR,
        open_time=open_time,
        close_time=close_time,
        available_at=available_at or close_time,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal("1"),
    )


def history(
    closes: Iterable[str],
    *,
    start: int = 0,
    pair: TradingPair = TradingPair.BTC_USD,
) -> tuple[Candle, ...]:
    return tuple(candle(i, close, pair=pair) for i, close in enumerate(closes, start))


def scanner_for(candles: tuple[Candle, ...]) -> OpportunityScanner:
    clock = SimulationClock(max((c.available_at for c in candles), default=BASE_TIME))
    return OpportunityScanner(PointInTimeDataGateway(clock, candles))


@pytest.mark.parametrize("count", [0, 1, 19])
def test_empty_or_insufficient_history(count: int) -> None:
    assert scanner_for(history(["100"] * count)).scan(pair=TradingPair.BTC_USD) == ()


@pytest.mark.parametrize("last_close", ["100", "110", "90"])
def test_first_valid_band_initializes_only(last_close: str) -> None:
    candles = history(["100"] * 19 + [last_close])
    statistics = rolling_bollinger_statistics(candles)[-1]
    assert statistics is not None
    if last_close == "90":
        assert candles[-1].close < statistics.lower_band
    else:
        assert candles[-1].close >= statistics.lower_band

    assert scanner_for(candles).scan(pair=TradingPair.BTC_USD) == ()


def test_earliest_crossover_is_twenty_first_contiguous_candle() -> None:
    candles = history(["100"] * 20 + ["90"])

    (opportunity,) = scanner_for(candles).scan(pair=TradingPair.BTC_USD)

    assert opportunity.observed_at == candles[20].close_time
    assert opportunity.available_at == candles[20].available_at
    assert opportunity.venue is Venue.COINBASE
    assert opportunity.pair is TradingPair.BTC_USD
    assert opportunity.timeframe is Timeframe.ONE_HOUR
    assert opportunity.trigger_type == "bollinger_lower_cross_v1"
    assert opportunity.summary == (
        "BTC-USD 1h close crossed below its lower Bollinger band."
    )
    # Independent 50-digit reference for 99.5 - 2 * sqrt(4.75).
    assert dict(opportunity.trigger_payload) == {
        "previous_close": "100",
        "previous_lower_band": "100",
        "current_close": "90",
        "current_lower_band": "95.141101056459326447763018016140384340862996074768",
    }


def test_own_bands_prevent_false_cross_when_band_falls() -> None:
    candles = history(["100"] * 19 + ["90", "80"])
    previous, current = rolling_bollinger_statistics(candles)[-2:]
    assert previous is not None and current is not None
    assert candles[-2].close < previous.lower_band
    assert candles[-2].close >= current.lower_band
    assert candles[-1].close < current.lower_band

    assert scanner_for(candles).scan(pair=TradingPair.BTC_USD) == ()


def test_own_bands_preserve_real_cross_when_band_rises() -> None:
    candles = history(["1"] + ["110"] * 18 + ["100", "99"])
    previous, current = rolling_bollinger_statistics(candles)[-2:]
    assert previous is not None and current is not None
    assert candles[-2].close >= previous.lower_band
    assert candles[-2].close < current.lower_band
    assert candles[-1].close < current.lower_band

    (opportunity,) = scanner_for(candles).scan(pair=TradingPair.BTC_USD)

    assert opportunity.observed_at == candles[-1].close_time


def test_repeated_below_band_closes_are_suppressed() -> None:
    candles = history(["100"] * 20 + ["90", "80", "70"])
    statistics = rolling_bollinger_statistics(candles)
    for item, result in zip(candles[20:], statistics[20:], strict=True):
        assert result is not None and item.close < result.lower_band

    (opportunity,) = scanner_for(candles).scan(pair=TradingPair.BTC_USD)

    assert opportunity.observed_at == candles[20].close_time


def test_equality_rearms_exactly_and_next_cross_has_distinct_id() -> None:
    # Four closes at 90 and sixteen at 100 give mean 98, std 4, lower band 90.
    candles = history(["100"] * 20 + ["90"] * 4 + ["80"])
    statistics = rolling_bollinger_statistics(candles)
    for i in range(20, 23):
        result = statistics[i]
        assert result is not None and candles[i].close < result.lower_band
    equality = statistics[23]
    assert equality is not None and candles[23].close == equality.lower_band
    assert len(scanner_for(candles[:24]).scan(pair=TradingPair.BTC_USD)) == 1

    first, second = scanner_for(candles).scan(pair=TradingPair.BTC_USD)

    assert (first.observed_at, second.observed_at) == (
        candles[20].close_time,
        candles[24].close_time,
    )
    assert second.trigger_payload["previous_close"] == "90"
    assert second.trigger_payload["previous_lower_band"] == "90"
    assert first.opportunity_id != second.opportunity_id


def test_above_band_close_rearms() -> None:
    candles = history(["100"] * 20 + ["90", "80", "100", "70"])
    rearm = rolling_bollinger_statistics(candles)[22]
    assert rearm is not None and candles[22].close > rearm.lower_band

    opportunities = scanner_for(candles).scan(pair=TradingPair.BTC_USD)

    assert [item.observed_at for item in opportunities] == [
        candles[20].close_time,
        candles[23].close_time,
    ]


@pytest.mark.parametrize("prior_below", [False, True])
@pytest.mark.parametrize("post_gap_count", [1, 19, 20, 21, 22])
def test_gap_resets_armed_and_suppressed_states(
    prior_below: bool, post_gap_count: int
) -> None:
    before = history(["100"] * 20 + (["90"] if prior_below else []))
    after = history(["100"] * 19 + ["90", "100", "80"], start=len(before) + 1)
    candles = before + after[:post_gap_count]
    results = rolling_bollinger_statistics(candles)
    assert results[len(before) : len(before) + 19] == (None,) * min(post_gap_count, 19)
    if post_gap_count >= 20:
        first_valid = results[len(before) + 19]
        assert first_valid is not None and after[19].close < first_valid.lower_band

    opportunities = scanner_for(candles).scan(pair=TradingPair.BTC_USD)

    expected = [before[-1].close_time] if prior_below else []
    if post_gap_count == 22:
        expected.append(after[21].close_time)
    assert [item.observed_at for item in opportunities] == expected


def test_first_valid_post_gap_at_band_then_below_can_trigger() -> None:
    before = history(["100"] * 20 + ["90"])
    after = history(["100"] * 20 + ["90"], start=22)

    opportunities = scanner_for(before + after).scan(pair=TradingPair.BTC_USD)

    assert [item.observed_at for item in opportunities] == [
        before[-1].close_time,
        after[-1].close_time,
    ]


@pytest.mark.parametrize("prior_below", [False, True])
def test_single_none_resets_comparison_without_bridging(
    monkeypatch: pytest.MonkeyPatch, prior_below: bool
) -> None:
    # Isolate the None contract independently of analytics' 19-candle gap reset.
    # A below-band first valid result must forget either previous relationship.
    candles = history(["90" if prior_below else "100", "100", "90", "100", "90"])

    def controlled_statistics(
        received: Iterable[Candle],
    ) -> tuple[BollingerStatistics | None, ...]:
        assert tuple(received) == candles
        return tuple(
            None
            if i == 1
            else BollingerStatistics(
                sma=Decimal("100"),
                population_std=Decimal("0"),
                lower_band=Decimal("100"),
                observed_at=item.close_time,
                available_at=item.available_at,
            )
            for i, item in enumerate(candles)
        )

    monkeypatch.setattr(
        "crypteo.scanner.rolling_bollinger_statistics", controlled_statistics
    )

    (opportunity,) = scanner_for(candles).scan(pair=TradingPair.BTC_USD)

    assert opportunity.observed_at == candles[4].close_time


def test_history_ending_before_cross_has_no_lookahead() -> None:
    candles = history(["100"] * 20 + ["90"])

    assert scanner_for(candles[:-1]).scan(pair=TradingPair.BTC_USD) == ()
    assert len(scanner_for(candles).scan(pair=TradingPair.BTC_USD)) == 1


def test_pair_scans_are_independent_on_shared_scanner() -> None:
    candles = tuple(
        item
        for offset, pair in enumerate(TradingPair)
        for item in history(["100"] * (20 + offset) + ["90"], pair=pair)
    )
    scanner = scanner_for(tuple(reversed(candles)))

    for _ in range(2):
        for offset, pair in reversed(list(enumerate(TradingPair))):
            (opportunity,) = scanner.scan(pair=pair)
            assert opportunity.pair is pair
            assert opportunity.observed_at == BASE_TIME + timedelta(hours=21 + offset)
            assert opportunity.summary == (
                f"{pair.value} 1h close crossed below its lower Bollinger band."
            )


def test_identical_snapshots_and_fresh_scanners_are_byte_and_field_equivalent() -> None:
    candles = history(["100"] * 20 + ["90", "100", "80"])
    scanner = scanner_for(candles)
    first = scanner.scan(pair=TradingPair.BTC_USD)
    assert len(first) == 2

    for snapshot in (
        scanner.scan(pair=TradingPair.BTC_USD),
        scanner_for(tuple(reversed(candles))).scan(pair=TradingPair.BTC_USD),
    ):
        assert snapshot == first
        assert [item.model_dump() for item in snapshot] == [
            item.model_dump() for item in first
        ]
        assert [item.model_dump_json().encode() for item in snapshot] == [
            item.model_dump_json().encode() for item in first
        ]


@pytest.mark.parametrize("pair", list(TradingPair))
@pytest.mark.parametrize("start", [0, 1])
def test_id_is_hash_of_exact_canonical_identity(pair: TradingPair, start: int) -> None:
    candles = history(["100"] * 20 + ["90"], start=start, pair=pair)
    (opportunity,) = scanner_for(candles).scan(pair=pair)
    # Spell out the canonical JSON independently of the scanner serializer.
    canonical = (
        f'{{"observed_at":"2023-01-01T{21 + start}:00:00.000000Z",'
        f'"pair":"{pair.value}","timeframe":"1h",'
        '"trigger_type":"bollinger_lower_cross_v1","venue":"COINBASE"}'
    )

    assert (
        opportunity.opportunity_id == f"sha256:{sha256(canonical.encode()).hexdigest()}"
    )
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", opportunity.opportunity_id)


def test_ids_change_with_pair_or_observed_time() -> None:
    ids = {
        scanner_for(history(["100"] * 20 + ["90"], start=start, pair=pair))
        .scan(pair=pair)[0]
        .opportunity_id
        for pair in TradingPair
        for start in (0, 1)
    }

    assert len(ids) == 6


def test_id_excludes_payload_and_availability() -> None:
    immediate = history(["100"] * 20 + ["90"])
    delayed_at = BASE_TIME + timedelta(hours=30)
    delayed = history(["200"] * 20) + (candle(20, "180", available_at=delayed_at),)
    (first,) = scanner_for(immediate).scan(pair=TradingPair.BTC_USD)
    (second,) = scanner_for(delayed).scan(pair=TradingPair.BTC_USD)

    assert first.opportunity_id == second.opportunity_id
    assert first.trigger_payload != second.trigger_payload
    assert first.available_at != second.available_at


@pytest.mark.parametrize("delay", [timedelta(0), timedelta(hours=2)])
def test_future_crossing_is_hidden_until_exact_visibility_boundary(
    delay: timedelta,
) -> None:
    boundary = BASE_TIME + timedelta(hours=21) + delay
    candles = history(["100"] * 20) + (candle(20, "90", available_at=boundary),)
    clock = SimulationClock(BASE_TIME + timedelta(hours=20))
    scanner = OpportunityScanner(PointInTimeDataGateway(clock, candles))
    assert scanner.scan(pair=TradingPair.BTC_USD) == ()

    clock.advance_to(boundary - timedelta(microseconds=1))
    assert scanner.scan(pair=TradingPair.BTC_USD) == ()
    clock.advance_to(boundary)

    (opportunity,) = scanner.scan(pair=TradingPair.BTC_USD)
    assert opportunity.observed_at == candles[-1].close_time
    assert opportunity.available_at == boundary == clock.now


def test_delayed_internal_candle_is_a_gap_until_available() -> None:
    delayed_at = BASE_TIME + timedelta(hours=22, microseconds=1)
    candles = tuple(
        candle(i, available_at=delayed_at if i == 10 else None) for i in range(20)
    ) + (candle(20, "90"),)
    clock = SimulationClock(delayed_at - timedelta(microseconds=1))
    scanner = OpportunityScanner(PointInTimeDataGateway(clock, candles))
    assert scanner.scan(pair=TradingPair.BTC_USD) == ()

    clock.advance_to(delayed_at)

    (opportunity,) = scanner.scan(pair=TradingPair.BTC_USD)
    assert opportunity.observed_at == candles[-1].close_time
    assert opportunity.available_at == delayed_at


def test_availability_includes_dependency_only_in_previous_window() -> None:
    delayed_at = BASE_TIME + timedelta(hours=30)
    candles = (candle(0, available_at=delayed_at),) + history(
        ["100"] * 19 + ["90"], start=1
    )
    previous, current = rolling_bollinger_statistics(candles)[-2:]
    assert previous is not None and current is not None
    assert previous.available_at == delayed_at
    assert current.available_at == candles[-1].close_time < delayed_at
    clock = SimulationClock(delayed_at - timedelta(microseconds=1))
    scanner = OpportunityScanner(PointInTimeDataGateway(clock, candles))
    assert scanner.scan(pair=TradingPair.BTC_USD) == ()

    clock.advance_to(delayed_at)

    (opportunity,) = scanner.scan(pair=TradingPair.BTC_USD)
    assert opportunity.observed_at == current.observed_at
    assert opportunity.available_at == previous.available_at


def test_public_api_has_no_temporal_overrides() -> None:
    constructor = inspect.signature(OpportunityScanner).parameters
    parameters = inspect.signature(OpportunityScanner.scan).parameters

    assert set(constructor) == {"gateway"}
    assert set(parameters) == {"self", "pair"}
    assert parameters["pair"].kind is inspect.Parameter.KEYWORD_ONLY
    assert all(
        parameter.kind
        not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        for parameter in (*constructor.values(), *parameters.values())
    )


def test_opportunity_json_round_trip() -> None:
    (opportunity,) = scanner_for(history(["100"] * 20 + ["90"])).scan(
        pair=TradingPair.BTC_USD
    )
    serialized = opportunity.model_dump_json()

    restored = Opportunity.model_validate_json(serialized)

    assert restored == opportunity
    assert restored.model_dump_json() == serialized
    assert all(
        isinstance(value, str)
        for value in json.loads(serialized)["trigger_payload"].values()
    )


@pytest.mark.parametrize(
    "value,expected",
    [
        ("0", "0"),
        ("-0.000", "0"),
        ("0E+10", "0"),
        ("0E-10", "0"),
        ("100.000", "100"),
        ("1.2300", "1.23"),
        ("1E+3", "1000"),
        ("1E-7", "0.0000001"),
        ("-1.2300", "-1.23"),
    ],
)
def test_canonical_decimal_strings(value: str, expected: str) -> None:
    assert _decimal_string(Decimal(value)) == expected


def test_payload_is_exact_and_independent_of_caller_decimal_context() -> None:
    price = "100.12345678901234567890123456789"
    candles = history([price] * 20 + ["90.0000"])
    expected = scanner_for(candles).scan(pair=TradingPair.BTC_USD)
    with localcontext() as context:
        context.prec = 6
        context.rounding = ROUND_UP
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        before = repr(context)

        result = scanner_for(candles).scan(pair=TradingPair.BTC_USD)

        assert result == expected
        assert result[0].model_dump_json() == expected[0].model_dump_json()
        assert result[0].trigger_payload["previous_close"] == price
        assert result[0].trigger_payload["previous_lower_band"] == price
        assert result[0].trigger_payload["current_close"] == "90"
        assert repr(context) == before
