"""Goldens, recomputation and temporal coverage for structure and history tools."""

from datetime import timedelta
from decimal import Decimal, localcontext
from random import Random
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError
from test_market_research import (
    BTC,
    CAPABILITIES,
    HOUR,
    ORACLE,
    T,
    assert_rejected,
    candle,
    candle_span,
    hours_before_t,
    located,
    replaced,
    service,
    shift_times,
    success,
)

from crypteo.domain.market import Candle
from crypteo.domain.market_research import (
    CandleStructurePayload,
    CrossingGroup,
    DailySection,
    FourHourSection,
    HigherTimeframePayload,
    LowerBandCrossingsPayload,
    MarketResearchCapability,
    OutcomeBaseline,
    ResearchRatio,
    ResearchToolSuccess,
    TrendPayload,
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
    assert payload.baseline_mean_true_range == 4
    assert payload.range_vs_baseline_mean_true_range == ResearchRatio(
        value=Decimal("2.5")
    )


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
    assert payload.range_vs_baseline_mean_true_range == ratio(span, 4)


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
    assert payload.range_vs_baseline_mean_true_range == ResearchRatio(value=Decimal(0))


def test_zero_mean_true_range_is_undefined() -> None:
    zero = ("100", "100", "100", "100")
    payload = structure(
        ("100", "106", "96", "103"), **{f"h{i}": zero for i in range(38, 59)}
    )
    assert payload.baseline_mean_true_range == 0
    assert payload.range_vs_baseline_mean_true_range == undefined(
        UndefinedReason.ZERO_MEAN_TRUE_RANGE
    )
    assert payload.close_location == ResearchRatio(value=Decimal("0.7"))


def test_mean_true_range_is_seeded_by_oldest_window_candle_only() -> None:
    current = ("100", "106", "96", "103")
    # Hour 38 only seeds hour 39's true range: max(4, |102 - 90|, |98 - 90|) = 12.
    seeded = structure(current, h38=("90", "92", "88", "90"))
    assert seeded.baseline_mean_true_range == Decimal("4.4")
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
        ("baseline_mean_true_range", Decimal(5)),
        (
            "range_vs_baseline_mean_true_range",
            undefined(UndefinedReason.ZERO_MEAN_TRUE_RANGE),
        ),
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
        # Window index j closes 599 - j hours before T.
        assert section.close_5_blocks_ago_close_time == hours_before_t(5 * step)
        assert section.range_high_close_time == hours_before_t(79)
        assert section.range_low_close_time == hours_before_t(83 if step == 4 else 84)
        assert (
            section.sma20_candles,
            section.previous_sma20_candles,
            section.range_candles,
        ) == (
            candle_span(20 * step - 1, 0, 20 * step),
            candle_span(25 * step - 1, 5 * step, 20 * step),
            candle_span(21 * step - 1, step, 20 * step),
        )


def test_four_hour_range_can_extend_below_and_above_the_daily_range() -> None:
    # The release puzzle: T-10h and T-12h lie in 4h blocks 1..20 (T-83h..T-4h)
    # but before daily block 1 (T-503h..T-24h), so only the 4h range sees them.
    payload = higher(j589=("1589", "5", "1590"), j587=("1587", "1586", "9000"))
    four_hour, daily = payload.four_hour, payload.daily
    assert (four_hour.range_low, four_hour.range_low_close_time) == (
        Decimal(5),
        hours_before_t(10),
    )
    assert (four_hour.range_high, four_hour.range_high_close_time) == (
        Decimal(9000),
        hours_before_t(12),
    )
    assert (daily.range_low, daily.range_low_close_time) == (
        Decimal(10),
        hours_before_t(84),
    )
    assert (daily.range_high, daily.range_high_close_time) == (
        Decimal(3000),
        hours_before_t(79),
    )
    assert daily.range_low > four_hour.range_low
    assert daily.range_high < four_hour.range_high


def test_range_extreme_ties_select_the_most_recent_candle() -> None:
    # Equal 4h extremes at T-69h and T-39h. More extreme equal daily ones at
    # T-499h and T-99h lie before 4h block 20, so each view resolves its own tie.
    tied = {f"j{j}": ("1500", "3", "7000") for j in (530, 560)}
    tied |= {f"j{j}": ("1500", "2", "8000") for j in (100, 500)}
    payload = higher(**tied)
    assert payload.four_hour.range_low_close_time == hours_before_t(39)
    assert payload.four_hour.range_high_close_time == hours_before_t(39)
    assert payload.daily.range_low_close_time == hours_before_t(99)
    assert payload.daily.range_high_close_time == hours_before_t(99)
    assert (payload.daily.range_low, payload.daily.range_high) == (
        Decimal(2),
        Decimal(8000),
    )


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


PENDING = {714: (UP,) * 4, 719: ()}


@pytest.mark.parametrize(
    "events,times,current",
    [
        ({}, (), False),
        ({713: EVENTS[713]}, (), False),
        ({719: ()}, (0,), True),
        ({716: (UP,) * 3}, (3,), False),
        (PENDING, (5, 0), True),
    ],
)
def test_pending_crossings_are_named_by_close_time(
    events: dict[int, tuple[tuple[str, str, str], ...]],
    times: tuple[int, ...],
    current: bool,
) -> None:
    result = crossings(events)
    payload = payload_of(result)
    assert payload.lookback_candles == candle_span(719, 0, 720)
    assert payload.pending_close_times == tuple(hours_before_t(h) for h in times)
    assert payload.pending_count == len(times)
    assert payload.current_candle_crossed is current
    # Window index w closes 719 - w hours before T; T-6h is sampled, not pending.
    assert payload.sample_count == (1 if 713 in events else 0)
    planted = crossings(events, (("80", "79", "81"),) + (UP,) * 8)
    assert planted.payload == result.payload
    assert planted.context.inputs[0].window_digest == (
        result.context.inputs[0].window_digest
    )
    assert planted.evidence.evidence_id == result.evidence.evidence_id


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"pending_count": 1}, "pending crossing"),
        ({"pending_close_times": (T, hours_before_t(5))}, "pending crossing"),
        ({"pending_close_times": (T, T)}, "pending crossing"),
        ({"pending_close_times": (hours_before_t(1), T)}, "pending crossing"),
        ({"pending_close_times": (hours_before_t(6), T)}, "pending crossing"),
        ({"current_candle_crossed": False}, "pending crossing"),
        (
            {"pending_close_times": (hours_before_t(5), T + timedelta(hours=1))},
            "pending crossing",
        ),
        ({"current_candle_crossed": 1}, "boolean"),
        ({"lookback_candles": candle_span(720, 0, 721)}, "fixed bar count"),
    ],
)
def test_pending_crossing_fields_reject_tampering(
    changes: dict[str, Any], match: str
) -> None:
    result = crossings(PENDING)
    payload = result.payload.model_dump() | changes
    with pytest.raises(ValidationError, match=match):
        LowerBandCrossingsPayload.model_validate(payload)
    assert_rejected(result, payload, "" if match == "boolean" else match)


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
    pending = tuple(o.observed_at for o in eligible if o not in sampled)
    assert payload.pending_close_times == pending
    assert payload.current_candle_crossed is any(o.observed_at == T for o in found)


# Seeds with no recent crossing, crossings at T-4h and T, at T alone, at T-3h,
# and at T-3h and T.
SCANNER_SEEDS = (0, 1, 20, 42, 82)


@pytest.mark.parametrize("seed", SCANNER_SEEDS)
def test_pending_crossings_match_scanner_opportunities_near_t(seed: int) -> None:
    candles = random_walk(seed)
    payload = payload_of(success(service(candles), CROSSINGS))
    found = OpportunityScanner(
        PointInTimeDataGateway(SimulationClock(T), candles)
    ).scan(pair=BTC)
    recent = tuple(o.observed_at for o in found if o.observed_at > hours_before_t(6))
    assert payload.pending_close_times == recent
    assert payload.current_candle_crossed is (T in recent)


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


# ADR-015: the unconditional baseline and the pre-registered conditioned groups.
NEW_FIELDS = (
    "unconditional_baseline",
    "current_wide_range",
    "current_downtrend",
    "unclassified_count",
    "wide_range_crossings",
    "not_wide_range_crossings",
    "downtrend_crossings",
    "not_downtrend_crossings",
)
GROUPS = NEW_FIELDS[4:]


def oracle_crossings(candles: tuple[Candle, ...]) -> dict[str, Any]:
    """A plain recomputation of every new field over the 720 candles ending at T."""
    window = candles[-720:]
    assert window[-1].close_time == T
    c = [item.close for item in window]
    h = [item.high for item in window]
    lo = [item.low for item in window]
    with localcontext(ORACLE):

        def sma(j: int, n: int) -> Decimal:
            return sum(c[j - n + 1 : j + 1], Decimal(0)) / n

        def lower(j: int) -> Decimal:
            m = sma(j, 20)
            variance = sum(((x - m) ** 2 for x in c[j - 19 : j + 1]), Decimal(0)) / 20
            return m - 2 * variance.sqrt()

        def outcome(i: int) -> tuple[Decimal, Decimal, Decimal]:
            return (
                c[i + 6] / c[i] - 1,
                min(lo[i + 1 : i + 7]) / c[i] - 1,
                max(h[i + 1 : i + 7]) / c[i] - 1,
            )

        def wide(j: int) -> bool:
            ranges = [
                max(h[k] - lo[k], abs(h[k] - c[k - 1]), abs(lo[k] - c[k - 1]))
                for k in range(j - 20, j)
            ]
            return h[j] - lo[j] >= sum(ranges, Decimal(0)) / 20

        def down(j: int) -> bool:
            return sma(j, 20) < sma(j, 50)

        def mean(values: list[Decimal]) -> Decimal:
            return sum(values, Decimal(0)) / len(values)

        def fraction(count: int, total: int) -> dict[str, Any]:
            if not total:
                return {"value": None, "undefined_reason": UndefinedReason.ZERO_SAMPLE}
            return {"value": Decimal(count) / total, "undefined_reason": None}

        def group(indices: list[int]) -> dict[str, Any]:
            n = len(indices)
            reached = sum(
                any(h[k] >= sma(i, 20) for k in range(i + 1, i + 7)) for i in indices
            )
            outcomes = [outcome(i) for i in indices]
            positive = sum(close > 0 for close, _, _ in outcomes)
            return {
                "sample_count": n,
                "reached_sma20_count": reached,
                "positive_close_count": positive,
                "reached_sma20_fraction": fraction(reached, n),
                "positive_close_fraction": fraction(positive, n),
                "mean_close_return": mean([o[0] for o in outcomes]) if n else None,
                "mean_adverse_excursion": mean([o[1] for o in outcomes]) if n else None,
                "mean_favorable_excursion": (
                    mean([o[2] for o in outcomes]) if n else None
                ),
            }

        sampled = [
            i for i in range(20, 714) if c[i - 1] >= lower(i - 1) and c[i] < lower(i)
        ]
        classified = [i for i in sampled if i >= 49]
        baseline = [outcome(i) for i in range(20, 714)]
        closes = sorted(close for close, _, _ in baseline)
        positive = sum(close > 0 for close in closes)
        return {
            "unconditional_baseline": {
                "start_candles": {
                    "first_close_time": window[20].close_time,
                    "last_close_time": window[713].close_time,
                    "bar_count": 694,
                },
                "sample_count": 694,
                "positive_close_count": positive,
                "positive_close_fraction": fraction(positive, 694),
                "mean_close_return": mean([o[0] for o in baseline]),
                "median_close_return": (closes[346] + closes[347]) / 2,
                "mean_adverse_excursion": mean([o[1] for o in baseline]),
                "mean_favorable_excursion": mean([o[2] for o in baseline]),
            },
            "current_wide_range": wide(719),
            "current_downtrend": down(719),
            "unclassified_count": len(sampled) - len(classified),
            "wide_range_crossings": group([i for i in classified if wide(i)]),
            "not_wide_range_crossings": group([i for i in classified if not wide(i)]),
            "downtrend_crossings": group([i for i in classified if down(i)]),
            "not_downtrend_crossings": group([i for i in classified if not down(i)]),
            # The existing statistics, as one group of every sample.
            "all_samples": group(sampled),
        }


ORACLE_SEEDS = (0, 1, 7, 20, 42, 82, 108)


def test_new_crossing_fields_match_an_independent_oracle() -> None:
    seen: set[tuple[str, bool]] = set()
    for seed in ORACLE_SEEDS:
        candles = random_walk(seed)
        payload = payload_of(success(service(candles), CROSSINGS))
        expected = oracle_crossings(candles)
        overall = expected.pop("all_samples")
        actual = payload.model_dump()
        for name, value in expected.items():
            assert actual[name] == value, (seed, name)
        assert {name: actual[name] for name in overall} == overall
        seen |= {(name, actual[name]["sample_count"] > 0) for name in GROUPS}
        seen |= {
            ("unclassified", payload.unclassified_count > 0),
            ("wide", payload.current_wide_range),
            ("downtrend", payload.current_downtrend),
        }
    # The seeds exercise every group, unclassified samples and both T features.
    assert seen >= {(name, True) for name in (*GROUPS, "unclassified")}
    assert seen >= {
        (name, value) for name in ("wide", "downtrend") for value in (True, False)
    }


def square_wave(w: int) -> str:
    """Closes alternate every 6 window indices, phased for 347 rises and falls."""
    return "100" if (w + 5) // 6 % 2 == 0 else "125"


def test_unconditional_baseline_golden() -> None:
    candles = tuple(bar(w - 660, square_wave(w), "90", "130") for w in range(-10, 720))
    result = success(service(candles), CROSSINGS)
    baseline = payload_of(result).unconditional_baseline
    # Every start closes 6 hours later at the other level: 100 -> 125 is +0.25 and
    # 125 -> 100 is -0.2, 347 times each. Lows 90 and highs 130 throughout.
    assert baseline.start_candles == candle_span(699, 6, 694)
    assert baseline.sample_count == 694
    assert baseline.positive_close_count == 347
    assert baseline.positive_close_fraction == ResearchRatio(value=Decimal("0.5"))
    # The even-count median averages the two middle values, -0.2 and 0.25.
    assert baseline.median_close_return == Decimal("0.025")
    assert baseline.mean_close_return == Decimal("0.025")
    assert baseline.mean_adverse_excursion == Decimal("-0.19")
    assert baseline.mean_favorable_excursion == Decimal("0.17")


def replace_window(
    candles: tuple[Candle, ...], **replacements: tuple[str, str, str]
) -> tuple[Candle, ...]:
    """Replace the candles at window indices named w<index> (closes, lows, highs)."""
    changed = list(candles)
    for name, values in replacements.items():
        w = int(name.removeprefix("w"))
        changed[w - 720] = bar(w - 660, *values)
    return tuple(changed)


def current_features(candles: tuple[Candle, ...]) -> tuple[bool, bool]:
    svc = service(candles)
    payload = payload_of(success(svc, CROSSINGS))
    trend = success(svc, MarketResearchCapability.TREND).payload
    candle_structure = success(svc, STRUCTURE).payload
    assert isinstance(trend, TrendPayload)
    assert isinstance(candle_structure, CandleStructurePayload)
    # The features agree with the trend and candle-structure tools at the same T.
    assert payload.current_downtrend is (trend.sma20 < trend.sma50)
    assert payload.current_wide_range is (
        candle_structure.range >= candle_structure.baseline_mean_true_range
    )
    return payload.current_wide_range, payload.current_downtrend


FLAT = crossing_history({})
# C(698) = 90 seeds the true range of candle 699: max(2, 101 - 90) = 11, so the
# mean true range of candles 699..718 is (19 * 2 + 11) / 20 = 2.45.
SEEDED = replace_window(FLAT, w698=("90", "89", "91"))


@pytest.mark.parametrize(
    "candles,wide",
    [
        (FLAT, True),
        (replace_window(FLAT, w719=("100", "99.01", "101")), False),
        (replace_window(SEEDED, w719=("100", "98.55", "101")), True),
        (replace_window(SEEDED, w719=("100", "98.56", "101")), False),
        # A zero mean true range makes any candle wide, even a zero-range one.
        (tuple(bar(w - 660, "100", "100", "100") for w in range(-10, 720)), True),
    ],
)
def test_wide_range_includes_a_range_equal_to_the_mean_true_range(
    candles: tuple[Candle, ...], wide: bool
) -> None:
    assert current_features(candles)[0] is wide


@pytest.mark.parametrize(
    "candles,downtrend",
    [
        # Equal SMA20 and SMA50 is not a downtrend: both 100, then both 101.
        (FLAT, False),
        (
            replace_window(
                FLAT, w670=("130", "129", "131"), w719=("120", "119", "121")
            ),
            False,
        ),
        (replace_window(FLAT, w719=("99", "98", "100")), True),
        (replace_window(FLAT, w719=("101", "100", "102")), False),
    ],
)
def test_downtrend_requires_sma20_strictly_below_sma50(
    candles: tuple[Candle, ...], downtrend: bool
) -> None:
    assert current_features(candles)[1] is downtrend


@pytest.mark.parametrize("seed", ORACLE_SEEDS)
def test_current_features_agree_with_trend_and_candle_structure(seed: int) -> None:
    current_features(random_walk(seed))


def empty_group() -> dict[str, Any]:
    zero = undefined(UndefinedReason.ZERO_SAMPLE).model_dump()
    return {
        "sample_count": 0,
        "reached_sma20_count": 0,
        "positive_close_count": 0,
        "reached_sma20_fraction": zero,
        "positive_close_fraction": zero,
        "mean_close_return": None,
        "mean_adverse_excursion": None,
        "mean_favorable_excursion": None,
    }


# The 80 crossing candle has range 2 and the 20 flat candles before it a mean true
# range of 2, so it is wide; SMA20 99 is below SMA50 99.6, so it is a downtrend.
ONE_SAMPLE = {
    "sample_count": 1,
    "reached_sma20_count": 1,
    "positive_close_count": 1,
    "reached_sma20_fraction": ResearchRatio(value=Decimal(1)).model_dump(),
    "positive_close_fraction": ResearchRatio(value=Decimal(1)).model_dump(),
    "mean_close_return": Decimal("0.25"),
    "mean_adverse_excursion": Decimal("0.2375"),
    "mean_favorable_excursion": Decimal("0.2625"),
}


@pytest.mark.parametrize("w,classified", [(48, False), (49, True), (713, True)])
def test_sampled_crossings_are_classified_from_window_index_49(
    w: int, classified: bool
) -> None:
    candles = crossing_history({w: (UP,) * 6})
    # Window index w closes 719 - w hours before T: 48 is T-671h, 49 is T-670h.
    assert candles[w - 720].close_time == hours_before_t(719 - w)
    payload = payload_of(success(service(candles), CROSSINGS)).model_dump()
    assert (payload["sample_count"], payload["pending_count"]) == (1, 0)
    assert payload["unclassified_count"] == (0 if classified else 1)
    member = ONE_SAMPLE if classified else empty_group()
    assert payload["wide_range_crossings"] == payload["downtrend_crossings"] == member
    assert payload["not_wide_range_crossings"] == empty_group()
    assert payload["not_downtrend_crossings"] == empty_group()


def test_pending_crossing_at_t_is_in_no_group() -> None:
    payload = payload_of(crossings({48: (UP,) * 6, 400: (UP,) * 6, 719: ()}))
    assert (payload.sample_count, payload.pending_count) == (2, 1)
    assert payload.current_candle_crossed is True
    assert payload.unclassified_count == 1
    dumped = payload.model_dump()
    assert dumped["wide_range_crossings"] == dumped["downtrend_crossings"] == ONE_SAMPLE
    assert dumped["not_wide_range_crossings"] == empty_group()
    assert dumped["not_downtrend_crossings"] == empty_group()


def narrow_crossings() -> tuple[Candle, ...]:
    """EVENTS with each crossing candle's range 1, below the mean true range 2."""
    return replace_window(
        crossing_history(EVENTS), **{f"w{w}": ("80", "79.5", "80.5") for w in EVENTS}
    )


def uptrend_crossing() -> tuple[Candle, ...]:
    """A drop below the band from a steady rise; SMA20 stays above SMA50."""
    closes = {w: str(200 + w) for w in range(-10, 720)} | {400: "570"}
    return tuple(bar(w - 660, close) for w, close in sorted(closes.items()))


@pytest.mark.parametrize(
    "candles,empty",
    [
        (
            crossing_history(EVENTS),
            {"not_wide_range_crossings", "not_downtrend_crossings"},
        ),
        (narrow_crossings(), {"wide_range_crossings", "not_downtrend_crossings"}),
        (uptrend_crossing(), {"not_wide_range_crossings", "downtrend_crossings"}),
        (crossing_history({}), set(GROUPS)),
        (crossing_history({716: (UP,) * 3}), set(GROUPS)),
    ],
)
def test_groups_without_samples_have_undefined_fractions_and_null_aggregates(
    candles: tuple[Candle, ...], empty: set[str]
) -> None:
    payload = payload_of(success(service(candles), CROSSINGS))
    dumped = payload.model_dump()
    assert {name for name in GROUPS if dumped[name] == empty_group()} == empty
    # Each non-empty group holds every sample, so it repeats the overall statistics.
    overall = {name: dumped[name] for name in ONE_SAMPLE}
    for name in set(GROUPS) - empty:
        assert dumped[name] == overall
    assert payload.unclassified_count == 0


def test_planted_future_candles_leave_baseline_and_groups_identical() -> None:
    for seed in ORACLE_SEEDS[:3]:
        candles = random_walk(seed)
        original = success(service(candles), CROSSINGS)
        # Extreme future candles would flip every feature and outcome if read.
        planted = candles + tuple(
            candle(hour, open="1", close="1", low="1", high="999999", volume="1")
            for hour in range(60, 80)
        )
        changed = success(service(planted), CROSSINGS)
        assert changed.payload == original.payload
        assert changed.context.inputs[0].window_digest == (
            original.context.inputs[0].window_digest
        )
        assert changed.evidence.evidence_id == original.evidence.evidence_id
        baseline = payload_of(changed).unconditional_baseline
        assert baseline.start_candles == candle_span(699, 6, 694)


def group_dump(count: int, reached: int, positive: int) -> dict[str, Any]:
    """A self-consistent group, to tamper only with the payload's relations."""
    return CrossingGroup(
        sample_count=count,
        reached_sma20_count=reached,
        positive_close_count=positive,
        reached_sma20_fraction=ratio(reached, count),
        positive_close_fraction=ratio(positive, count),
        mean_close_return=Decimal(0),
        mean_adverse_excursion=Decimal(0),
        mean_favorable_excursion=Decimal(0),
    ).model_dump()


GROUPS_REJECTED = "crossing groups do not reconcile"


# EVENTS: four classified samples (reached 2, positive 3), all wide and downtrend.
@pytest.mark.parametrize(
    "changes,match",
    [
        ({("not_wide_range_crossings",): group_dump(1, 0, 0)}, GROUPS_REJECTED),
        ({("wide_range_crossings",): group_dump(3, 2, 3)}, GROUPS_REJECTED),
        ({("downtrend_crossings",): group_dump(4, 1, 3)}, GROUPS_REJECTED),
        ({("not_downtrend_crossings",): group_dump(1, 1, 1)}, GROUPS_REJECTED),
        (
            {
                ("wide_range_crossings",): group_dump(4, 1, 3),
                ("downtrend_crossings",): group_dump(4, 1, 3),
            },
            GROUPS_REJECTED,
        ),
        (
            {
                ("wide_range_crossings",): group_dump(4, 2, 2),
                ("downtrend_crossings",): group_dump(4, 2, 2),
            },
            GROUPS_REJECTED,
        ),
        ({("unclassified_count",): 1}, GROUPS_REJECTED),
        ({("unclassified_count",): 5}, GROUPS_REJECTED),
        ({("wide_range_crossings", "reached_sma20_count"): 5}, "crossing counts"),
        ({("downtrend_crossings", "positive_close_count"): 5}, "crossing counts"),
        (
            {("wide_range_crossings", "positive_close_fraction"): ratio(2, 4)},
            "crossing counts",
        ),
        (
            {
                ("not_wide_range_crossings", "reached_sma20_fraction"): ResearchRatio(
                    value=Decimal(0)
                )
            },
            "crossing counts",
        ),
        (
            {("not_wide_range_crossings", "mean_close_return"): Decimal(0)},
            "exactly with samples",
        ),
        (
            {("downtrend_crossings", "mean_favorable_excursion"): None},
            "exactly with samples",
        ),
        ({("unconditional_baseline", "sample_count"): 693}, "Input should be 694"),
        (
            {("unconditional_baseline", "positive_close_count"): 695},
            "baseline counts",
        ),
        (
            {
                ("unconditional_baseline", "positive_close_fraction"): ResearchRatio(
                    value=Decimal(0)
                )
            },
            "baseline counts",
        ),
        ({("current_wide_range",): 1}, "valid boolean"),
        ({("current_downtrend",): "false"}, "valid boolean"),
        ({("unclassified_count",): -1}, "greater than or equal to 0"),
        ({("unclassified_count",): True}, "valid integer"),
    ],
)
def test_conditioned_crossing_fields_reject_tampering(
    changes: dict[tuple[str, ...], Any], match: str
) -> None:
    result = crossings(EVENTS)
    payload = result.payload.model_dump()
    for path, value in changes.items():
        payload = replaced(payload, path, value)
    with pytest.raises(ValidationError, match=match):
        LowerBandCrossingsPayload.model_validate(payload)
    assert_rejected(result, payload, match)


def test_baseline_start_candles_are_anchored_to_t() -> None:
    result = crossings(EVENTS)
    payload = result.payload.model_dump()
    path = ("unconditional_baseline", "start_candles")
    for delta in (HOUR, -HOUR):
        shifted = replaced(payload, path, shift_times(located(payload, path), delta))
        LowerBandCrossingsPayload.model_validate(shifted)
        assert_rejected(result, shifted, "not anchored to T")


@pytest.mark.parametrize("field", NEW_FIELDS)
def test_payload_without_a_new_field_is_rejected(field: str) -> None:
    result = crossings(EVENTS)
    payload = result.payload.model_dump()
    del payload[field]
    assert_rejected(result, payload, "Field required")


@pytest.mark.parametrize(
    "path", [(), ("unconditional_baseline",), ("wide_range_crossings",)]
)
def test_crossing_contracts_reject_extra_fields(path: tuple[str, ...]) -> None:
    result = crossings(EVENTS)
    payload = result.payload.model_dump()
    extended = located(payload, path) | {"sample_weight": Decimal(1)}
    forged = replaced(payload, path, extended) if path else extended
    assert_rejected(result, forged, "Extra inputs are not permitted")


def test_crossing_contracts_round_trip_and_revalidate_instances() -> None:
    payload = payload_of(crossings(PENDING | {400: (UP,) * 6}))
    models: tuple[BaseModel, ...] = (
        payload,
        payload.unconditional_baseline,
        payload.wide_range_crossings,
        payload.not_wide_range_crossings,
    )
    for model in models:
        kind = type(model)
        assert kind.model_validate_json(model.model_dump_json()) == model
        assert kind.model_validate(model.model_dump()) == model
        assert kind.model_validate(model) == model
    forged_group = CrossingGroup.model_construct(
        **payload.wide_range_crossings.model_dump() | {"reached_sma20_count": 2}
    )
    forged_baseline = OutcomeBaseline.model_construct(
        **payload.unconditional_baseline.model_dump() | {"positive_close_count": 0}
    )
    with pytest.raises(ValidationError, match="crossing counts"):
        CrossingGroup.model_validate(forged_group)
    with pytest.raises(ValidationError, match="baseline counts"):
        OutcomeBaseline.model_validate(forged_baseline)
    with pytest.raises(ValidationError, match="crossing counts"):
        LowerBandCrossingsPayload.model_validate(
            payload.model_dump() | {"wide_range_crossings": forged_group}
        )


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_payloads_round_trip_through_json(
    capability: MarketResearchCapability,
) -> None:
    original = (
        crossings(PENDING)
        if capability is CROSSINGS
        else success(service(), capability)
    )
    assert ResearchToolSuccess.model_validate_json(original.model_dump_json()) == (
        original
    )
    assert (
        type(original.payload).model_validate_json(original.payload.model_dump_json())
        == original.payload
    )
    assert type(original.payload).model_validate(original.payload) == original.payload
    assert ResearchToolSuccess.model_validate(original) == original


@pytest.mark.parametrize(
    "renames",
    [
        {"baseline_mean_true_range": "previous_mean_true_range"},
        {"range_vs_baseline_mean_true_range": "range_vs_mean_true_range"},
    ],
)
def test_candle_structure_rejects_previous_field_names(renames: dict[str, str]) -> None:
    result = success(service(), STRUCTURE)
    payload = result.payload.model_dump()
    renamed = {renames.get(key, key): value for key, value in payload.items()}
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CandleStructurePayload.model_validate(renamed)
    assert_rejected(result, renamed, "Extra inputs are not permitted")
    assert_rejected(
        result,
        payload | {old: payload[new] for new, old in renames.items()},
        "Extra inputs",
    )
