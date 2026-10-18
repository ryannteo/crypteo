"""Goldens, recomputation and temporal coverage for structure and history tools."""

from datetime import timedelta
from decimal import Decimal, localcontext
from random import Random
from typing import Any

import pytest
from pydantic import ValidationError
from test_market_research import (
    BTC,
    ORACLE,
    T,
    candle,
    service,
    success,
)

from crypteo.domain.market import Candle
from crypteo.domain.market_research import (
    CandleStructurePayload,
    DailySection,
    FourHourSection,
    HigherTimeframePayload,
    LowerBandCrossingsPayload,
    MarketResearchCapability,
    ResearchRatio,
    ResearchToolSuccess,
    UndefinedReason,
)
from crypteo.scanner import OpportunityScanner
from crypteo.temporal import PointInTimeDataGateway, SimulationClock

STRUCTURE = MarketResearchCapability.CANDLE_STRUCTURE
HIGHER = MarketResearchCapability.HIGHER_TIMEFRAME
CROSSINGS = MarketResearchCapability.LOWER_BAND_CROSSINGS


def bar(hour: int, close: Any, low: Any = None, high: Any = None) -> Candle:
    price = Decimal(close)
    return candle(
        hour,
        open=price,
        close=price,
        low=price - 1 if low is None else Decimal(low),
        high=price + 1 if high is None else Decimal(high),
        volume="1",
    )


def ratio(numerator: Any, denominator: Any) -> ResearchRatio:
    with localcontext(ORACLE):
        return ResearchRatio(value=Decimal(numerator) / Decimal(denominator))


def undefined(reason: UndefinedReason) -> ResearchRatio:
    return ResearchRatio(value=None, undefined_reason=reason)


def returned(current: Any, previous: Any) -> Decimal:
    with localcontext(ORACLE):
        return Decimal(current) / Decimal(previous) - 1


# Candle structure: hours 0..59, the window is hours 38..59 and hour 59 ends at T.


def structure(
    current: tuple[str, str, str, str], **older: tuple[str, str, str, str]
) -> CandleStructurePayload:
    def ohlc(hour: int, values: tuple[str, str, str, str]) -> Candle:
        o, h, low, c = values
        return candle(hour, open=o, high=h, low=low, close=c)

    flat = ("100", "102", "98", "100")
    candles = tuple(ohlc(i, older.get(f"h{i}", flat)) for i in range(59)) + (
        ohlc(59, current),
    )
    payload = success(service(candles), STRUCTURE).payload
    assert isinstance(payload, CandleStructurePayload)
    return payload


def test_candle_structure_golden() -> None:
    payload = structure(("100", "106", "96", "103"))
    assert (payload.open, payload.high, payload.low, payload.close) == tuple(
        map(Decimal, (100, 106, 96, 103))
    )
    assert (
        payload.range,
        payload.body,
        payload.upper_wick,
        payload.lower_wick,
    ) == tuple(map(Decimal, (10, 3, 3, 4)))
    assert payload.body_fraction == ResearchRatio(value=Decimal("0.3"))
    assert payload.upper_wick_fraction == ResearchRatio(value=Decimal("0.3"))
    assert payload.lower_wick_fraction == ResearchRatio(value=Decimal("0.4"))
    assert payload.close_location == ResearchRatio(value=Decimal("0.7"))
    # Twenty prior true ranges of 4; the current candle's range is excluded.
    assert payload.previous_mean_true_range == 4
    assert payload.range_vs_mean_true_range == ResearchRatio(value=Decimal("2.5"))


@pytest.mark.parametrize(
    "ohlc,expected",
    [
        # Bearish: body is signed, fractions use its magnitude.
        (
            ("104", "105", "95", "99"),
            ("10", "-5", "1", "4", "0.5", "0.1", "0.4", "0.4"),
        ),
        # Doji: zero body inside a positive range.
        (("100", "103", "98", "100"), ("5", "0", "3", "2", "0", "0.6", "0.4", "0.4")),
        # Hammer-like: long lower wick, close near the high.
        (
            ("100", "101.5", "95", "101"),
            ("6.5", "1", "0.5", "5", "1/6.5", "0.5/6.5", "5/6.5", "6/6.5"),
        ),
    ],
)
def test_candle_structure_shapes(
    ohlc: tuple[str, str, str, str], expected: tuple[str, ...]
) -> None:
    payload = structure(ohlc)
    span, body, upper, lower, *fractions = expected
    assert (payload.range, payload.body, payload.upper_wick, payload.lower_wick) == (
        Decimal(span),
        Decimal(body),
        Decimal(upper),
        Decimal(lower),
    )
    actual = (
        payload.body_fraction,
        payload.upper_wick_fraction,
        payload.lower_wick_fraction,
        payload.close_location,
    )
    assert actual == tuple(
        ratio(*f.split("/")) if "/" in f else ratio(f, 1) for f in fractions
    )
    assert payload.range_vs_mean_true_range == ratio(span, 4)


def test_zero_range_candle_has_undefined_fractions() -> None:
    payload = structure(("100", "100", "100", "100"))
    assert (
        payload.range == payload.body == payload.upper_wick == payload.lower_wick == 0
    )
    for value in (
        payload.body_fraction,
        payload.upper_wick_fraction,
        payload.lower_wick_fraction,
        payload.close_location,
    ):
        assert value == undefined(UndefinedReason.ZERO_RANGE)
    assert payload.range_vs_mean_true_range == ResearchRatio(value=Decimal(0))


def test_zero_mean_true_range_is_undefined() -> None:
    zero = ("100", "100", "100", "100")
    payload = structure(
        ("100", "106", "96", "103"), **{f"h{i}": zero for i in range(38, 59)}
    )
    assert payload.previous_mean_true_range == 0
    assert payload.range_vs_mean_true_range == undefined(
        UndefinedReason.ZERO_MEAN_TRUE_RANGE
    )
    assert payload.close_location == ResearchRatio(value=Decimal("0.7"))


def test_mean_true_range_is_seeded_by_oldest_window_candle_only() -> None:
    current = ("100", "106", "96", "103")
    # Hour 38 only seeds hour 39's true range: max(4, |102 - 90|, |98 - 90|) = 12.
    seeded = structure(current, h38=("90", "92", "88", "90"))
    assert seeded.previous_mean_true_range == Decimal("4.4")
    assert structure(current, h37=("1", "1000", "1", "1")) == structure(current)


@pytest.mark.parametrize(
    "field,value",
    [
        ("range", Decimal(11)),
        ("body", Decimal(-3)),
        ("upper_wick", Decimal(2)),
        ("lower_wick", Decimal(5)),
        ("body_fraction", ResearchRatio(value=Decimal("0.31"))),
        ("upper_wick_fraction", ResearchRatio(value=Decimal("0.29"))),
        ("lower_wick_fraction", undefined(UndefinedReason.ZERO_RANGE)),
        ("close_location", ResearchRatio(value=Decimal("0.3"))),
        ("previous_mean_true_range", Decimal(5)),
        ("range_vs_mean_true_range", undefined(UndefinedReason.ZERO_MEAN_TRUE_RANGE)),
        ("high", Decimal(102)),
        ("low", Decimal(104)),
    ],
)
def test_candle_structure_rejects_tampered_fields(field: str, value: Any) -> None:
    payload = structure(("100", "106", "96", "103"))
    with pytest.raises(ValidationError):
        CandleStructurePayload.model_validate(payload.model_copy(update={field: value}))


# Higher timeframe: window index j is hour j - 540; block 0 is j 596..599 (4h).


def higher(**changes: tuple[str, str, str]) -> HigherTimeframePayload:
    spikes = {599: ("1599", "1", "5000"), 520: ("1520", "1519", "3000")}
    spikes |= {515: ("1515", "10", "1516")}
    spikes |= {int(k.removeprefix("j")): v for k, v in changes.items()}
    candles = tuple(
        bar(j - 540, *spikes.get(j, (str(1000 + j),))) for j in range(-10, 600)
    )
    payload = success(service(candles), HIGHER).payload
    assert isinstance(payload, HigherTimeframePayload)
    return payload


def test_higher_timeframe_golden() -> None:
    payload = higher()
    # 4h block k closes at 1599 - 4k; blocks 1..20 span j 516..595.
    # 24h block k closes at 1599 - 24k; blocks 1..20 span j 96..575.
    sections = ((payload.four_hour, 4, Decimal(1515)), (payload.daily, 24, Decimal(10)))
    for section, step, low in sections:
        sma20, previous = 1599 - Decimal("9.5") * step, 1599 - Decimal("14.5") * step
        assert section.block_hours == step
        assert (section.close, section.sma20, section.previous_sma20) == (
            Decimal(1599),
            sma20,
            previous,
        )
        assert section.close_5_blocks_ago == 1599 - 5 * step
        assert section.close_vs_sma20 == returned(1599, sma20)
        assert section.sma20_change == returned(sma20, previous)
        assert section.return_5_blocks == returned(1599, 1599 - 5 * step)
        # The current block's 5000 high and 1 low are excluded from the range.
        assert (section.range_high, section.range_low) == (Decimal(3000), low)
        assert section.range_high_distance == returned(1599, 3000)
        assert section.range_low_distance == returned(1599, low)


def test_higher_timeframe_blocks_end_at_t() -> None:
    original = higher()
    # The candle ending at T - 4h (j 595) closes 4h block 1, not block 0.
    moved = higher(j595=("1695", "1694", "1696"))
    assert moved.four_hour.close == original.four_hour.close == 1599
    assert moved.four_hour.sma20 == original.four_hour.sma20 + 5
    assert moved.four_hour.previous_sma20 == original.four_hour.previous_sma20
    assert moved.four_hour.return_5_blocks == original.four_hour.return_5_blocks
    assert moved.daily == original.daily
    # The candle ending at T - 3h is inside block 0 but not its close.
    assert higher(j596=("1700", "1699", "1701")) == original


@pytest.mark.parametrize(
    "field",
    [
        "close_vs_sma20",
        "sma20_change",
        "return_5_blocks",
        "range_high_distance",
        "range_low_distance",
    ],
)
@pytest.mark.parametrize("section", [FourHourSection, DailySection])
def test_higher_timeframe_rejects_tampered_ratios(
    section: type[FourHourSection | DailySection], field: str
) -> None:
    payload = higher()
    original = payload.four_hour if section is FourHourSection else payload.daily
    with pytest.raises(ValidationError):
        section.model_validate(original.model_copy(update={field: Decimal("0.5")}))


def test_higher_timeframe_rejects_inverted_range_and_wrong_block_size() -> None:
    four_hour = higher().four_hour
    inverted = four_hour.model_copy(
        update={
            "range_high": Decimal(1515),
            "range_low": Decimal(3000),
            "range_high_distance": returned(1599, 1515),
            "range_low_distance": returned(1599, 3000),
        }
    )
    with pytest.raises(ValidationError, match="reconcile"):
        FourHourSection.model_validate(inverted)
    with pytest.raises(ValidationError):
        FourHourSection.model_validate(four_hour.model_dump() | {"block_hours": 24})
    with pytest.raises(ValidationError):
        DailySection.model_validate(four_hour.model_dump())


def test_higher_timeframe_sections_share_the_current_close() -> None:
    payload = higher()
    daily = payload.daily
    close = Decimal(1600)
    shifted = daily.model_copy(
        update={
            "close": close,
            "close_vs_sma20": returned(close, daily.sma20),
            "return_5_blocks": returned(close, daily.close_5_blocks_ago),
            "range_high_distance": returned(close, daily.range_high),
            "range_low_distance": returned(close, daily.range_low),
        }
    )
    DailySection.model_validate(shifted)
    with pytest.raises(ValidationError, match="share the current close"):
        HigherTimeframePayload.model_validate(
            payload.model_copy(update={"daily": shifted})
        )


# Lower-band crossings: window index w is hour w - 660 and w 719 ends at T.
# From flat closes of 100, a close of 80 crosses below the band (SMA20 = 99).

UP = ("100", "99", "101")
EVENTS: dict[int, tuple[tuple[str, str, str], ...]] = {
    100: (UP, UP, ("100", "70", "101"), UP, UP, ("92", "91", "93")),
    200: (("96", "95", "97"),) * 5 + (("96", "95", "98"),),
    300: (
        ("79", "78", "80"),
        ("78", "77", "79"),
        ("77", "76", "78"),
        ("76", "75", "77"),
        ("75", "74", "76"),
        ("76", "72", "77"),
    ),
    713: (UP,) * 6,
}


def crossing_history(
    events: dict[int, tuple[tuple[str, str, str], ...]],
    after: tuple[tuple[str, str, str], ...] = (),
) -> tuple[Candle, ...]:
    bars: dict[int, tuple[str, ...]] = {
        w: ("100",) for w in range(-10, 720 + len(after))
    }
    for w, outcome in events.items():
        bars[w] = ("80",)
        bars |= {w + k: values for k, values in enumerate(outcome, 1)}
    bars |= {720 + k: values for k, values in enumerate(after)}
    return tuple(bar(w - 660, *values) for w, values in sorted(bars.items()))


def crossings(
    events: dict[int, tuple[tuple[str, str, str], ...]],
    after: tuple[tuple[str, str, str], ...] = (),
) -> ResearchToolSuccess:
    return success(service(crossing_history(events, after)), CROSSINGS)


def payload_of(result: ResearchToolSuccess) -> LowerBandCrossingsPayload:
    assert isinstance(result.payload, LowerBandCrossingsPayload)
    return result.payload


def test_lower_band_crossings_even_sample_golden() -> None:
    # Close returns 0.15, 0.2, -0.05, 0.25; adverse -0.125, 0.1875, -0.1,
    # 0.2375; favorable 0.2625, 0.225, 0, 0.2625. SMA20 99 reached twice.
    payload = payload_of(crossings(EVENTS))
    assert (payload.lookback_bars, payload.horizon_bars) == (720, 6)
    assert (payload.sample_count, payload.pending_count) == (4, 0)
    assert (payload.reached_sma20_count, payload.positive_close_count) == (2, 3)
    assert payload.reached_sma20_fraction == ResearchRatio(value=Decimal("0.5"))
    assert payload.positive_close_fraction == ResearchRatio(value=Decimal("0.75"))
    assert payload.mean_close_return == Decimal("0.1375")
    assert payload.median_close_return == Decimal("0.175")
    assert (payload.min_close_return, payload.max_close_return) == (
        Decimal("-0.05"),
        Decimal("0.25"),
    )
    assert payload.mean_adverse_excursion == Decimal("0.05")
    assert payload.mean_favorable_excursion == Decimal("0.1875")


def test_lower_band_crossings_odd_sample_golden() -> None:
    payload = payload_of(crossings({w: EVENTS[w] for w in (100, 200, 300)}))
    assert (payload.sample_count, payload.pending_count) == (3, 0)
    assert payload.reached_sma20_fraction == ratio(1, 3)
    assert payload.positive_close_fraction == ratio(2, 3)
    assert payload.mean_close_return == Decimal("0.1")
    assert payload.median_close_return == Decimal("0.15")
    assert (payload.min_close_return, payload.max_close_return) == (
        Decimal("-0.05"),
        Decimal("0.2"),
    )
    assert payload.mean_adverse_excursion == Decimal("-0.0125")
    assert payload.mean_favorable_excursion == Decimal("0.1625")


def test_crossing_whose_outcome_ends_exactly_at_t_is_sampled() -> None:
    payload = payload_of(crossings({713: EVENTS[713]}))
    assert (payload.sample_count, payload.pending_count) == (1, 0)
    assert payload.mean_close_return == payload.median_close_return == Decimal("0.25")
    assert payload.reached_sma20_fraction == ResearchRatio(value=Decimal(1))


def test_crossing_three_hours_before_t_is_pending_and_future_is_invisible() -> None:
    events = {400: (UP,) * 6, 716: (UP,) * 3}
    original = crossings(events)
    payload = payload_of(original)
    assert (payload.sample_count, payload.pending_count) == (1, 1)
    assert payload.mean_close_return == Decimal("0.25")
    # Candles after T would complete the pending outcome; none may contribute.
    for after in ((("1", "1", "2"),) * 6, (("500", "499", "1000"),) * 6):
        changed = crossings(events, after)
        assert changed.payload == original.payload
        assert changed.evidence.evidence_id == original.evidence.evidence_id


def test_planted_future_candles_leave_crossing_evidence_identical() -> None:
    original = crossings(EVENTS)
    planted = crossings(EVENTS, (("80", "79", "81"),) + (UP,) * 8)
    assert planted.payload == original.payload
    assert planted.context.inputs[0].window_digest == (
        original.context.inputs[0].window_digest
    )
    assert planted.evidence.evidence_id == original.evidence.evidence_id


@pytest.mark.parametrize("events", [{}, {716: (UP,) * 3}])
def test_zero_samples_give_none_aggregates(
    events: dict[int, tuple[tuple[str, str, str], ...]],
) -> None:
    payload = payload_of(crossings(events))
    assert payload.sample_count == 0
    assert payload.pending_count == len(events)
    assert payload.reached_sma20_count == payload.positive_close_count == 0
    for fraction in (payload.reached_sma20_fraction, payload.positive_close_fraction):
        assert fraction == undefined(UndefinedReason.ZERO_SAMPLE)
    assert (
        payload.mean_close_return,
        payload.median_close_return,
        payload.min_close_return,
        payload.max_close_return,
        payload.mean_adverse_excursion,
        payload.mean_favorable_excursion,
    ) == (None,) * 6


def random_walk(seed: int) -> tuple[Candle, ...]:
    rng, close, candles = Random(seed), 10_000, []
    for hour in range(-700, 60):
        open_, close = close, max(2_000, close + rng.randint(-250, 240))
        candles.append(
            candle(
                hour,
                open=Decimal(open_) / 100,
                close=Decimal(close) / 100,
                high=Decimal(max(open_, close) + rng.randint(0, 150)) / 100,
                low=Decimal(min(open_, close) - rng.randint(0, 150)) / 100,
                volume="1",
            )
        )
    return tuple(candles)


def test_crossings_match_scanner_over_visible_history() -> None:
    candles = random_walk(108)
    result = success(service(candles), CROSSINGS)
    payload = payload_of(result)
    start = result.context.window.start
    found = OpportunityScanner(
        PointInTimeDataGateway(SimulationClock(T), candles)
    ).scan(pair=BTC)
    # Window index 20 is the first with two defined in-window bands.
    eligible = [o for o in found if o.observed_at >= start + timedelta(hours=21)]
    sampled = [o for o in eligible if o.observed_at <= T - timedelta(hours=6)]
    assert len(found) > len(eligible) > len(sampled) > 0
    # The scanner sees a crossing at window index 19 using older history.
    assert any(
        start + timedelta(hours=19) <= o.observed_at < start + timedelta(hours=21)
        for o in found
    )
    assert payload.sample_count == len(sampled)
    assert payload.pending_count == len(eligible) - len(sampled)
    closes = {c.close_time: c.close for c in candles}
    expected = sorted(
        returned(closes[o.observed_at + timedelta(hours=6)], closes[o.observed_at])
        for o in sampled
    )
    assert (payload.min_close_return, payload.max_close_return) == (
        expected[0],
        expected[-1],
    )
    with localcontext(ORACLE):
        assert payload.mean_close_return == sum(expected, Decimal(0)) / len(expected)


@pytest.mark.parametrize(
    "changes",
    [
        {"reached_sma20_count": 5},
        {"positive_close_count": 5},
        {"reached_sma20_fraction": ResearchRatio(value=Decimal("0.25"))},
        {"positive_close_fraction": undefined(UndefinedReason.ZERO_SAMPLE)},
        {"mean_close_return": None},
        {"mean_favorable_excursion": None},
        {"median_close_return": Decimal("0.3")},
        {"min_close_return": Decimal("0.2")},
        {"max_close_return": Decimal("0.1")},
        {"mean_close_return": Decimal("-0.06")},
        {"sample_count": 0},
        {"sample_count": True},
        {"pending_count": -1},
        {"lookback_bars": 600},
        {"horizon_bars": 24},
    ],
)
def test_lower_band_crossings_rejects_tampered_fields(changes: dict[str, Any]) -> None:
    payload = payload_of(crossings(EVENTS))
    with pytest.raises(ValidationError):
        LowerBandCrossingsPayload.model_validate(payload.model_dump() | changes)


def test_zero_sample_payload_rejects_aggregates() -> None:
    payload = payload_of(crossings({}))
    with pytest.raises(ValidationError, match="exactly with samples"):
        LowerBandCrossingsPayload.model_validate(
            payload.model_dump() | {"mean_adverse_excursion": Decimal(0)}
        )


@pytest.mark.parametrize("capability", [STRUCTURE, HIGHER, CROSSINGS])
def test_new_payloads_round_trip_through_json(
    capability: MarketResearchCapability,
) -> None:
    original = (
        crossings(EVENTS) if capability is CROSSINGS else success(service(), capability)
    )
    assert ResearchToolSuccess.model_validate_json(original.model_dump_json()) == (
        original
    )
    assert (
        type(original.payload).model_validate_json(original.payload.model_dump_json())
        == original.payload
    )
