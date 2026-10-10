"""Formula goldens and adversarial temporal/identity integration coverage."""

import inspect
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from itertools import permutations
from typing import Any

import pytest
from decimal_support import hostile_decimal_context

from crypteo.analytics import rolling_bollinger_statistics
from crypteo.coinbase_acquisition import CoinbaseAcquisitionRecord
from crypteo.coinbase_dataset import (
    CoinbaseCanonicalDataset,
    _build_dataset_version,
    _find_missing_open_times,
)
from crypteo.domain.market import Candle, DatasetManifest, Timeframe, TradingPair, Venue
from crypteo.domain.market_research import (
    BollingerPositionPayload,
    LevelsPayload,
    MarketResearchCapability,
    MarketResearchPolicy,
    MarketRole,
    MarketSourceBinding,
    MomentumPayload,
    RelativeStrengthPayload,
    ResearchToolSuccess,
    ResearchToolUnavailable,
    ResearchUnavailableReason,
    TrendPayload,
    UndefinedReason,
    VolatilityPayload,
    VolumeContextPayload,
)
from crypteo.market_research import MarketResearchService, build_market_research
from crypteo.temporal import PointInTimeDataGateway, SimulationClock

START = datetime(2022, 1, 1, tzinfo=UTC)
T = START + timedelta(hours=60)
BTC, ETH, SOL = TradingPair
CAPABILITIES = tuple(MarketResearchCapability)
POLICY = MarketResearchPolicy()
ORACLE = Context(prec=50, rounding=ROUND_HALF_EVEN)


def candle(hour: int, pair: TradingPair = BTC, **changes: Any) -> Candle:
    price = Decimal(100 + hour)
    end = START + timedelta(hours=hour + 1)
    return Candle.model_validate(
        dict(
            venue=Venue.COINBASE,
            pair=pair,
            timeframe=Timeframe.ONE_HOUR,
            open_time=end - timedelta(hours=1),
            close_time=end,
            available_at=end,
            open=price,
            high=price + 2,
            low=price - 2,
            close=price,
            volume=Decimal(hour + 1),
        )
        | changes
    )


def history(pair: TradingPair = BTC) -> tuple[Candle, ...]:
    return tuple(candle(i, pair) for i in range(60))


def dataset(
    candles: tuple[Candle, ...], pair: TradingPair = BTC
) -> CoinbaseCanonicalDataset:
    ordered = tuple(sorted(candles, key=lambda c: c.open_time))
    start = ordered[0].open_time if ordered else None
    end = ordered[-1].close_time if ordered else None
    gaps = _find_missing_open_times(ordered) if ordered else ()
    version = _build_dataset_version(
        pair=pair,
        start_time=start,
        end_time=end,
        missing_open_times=gaps,
        candles=ordered,
    )
    return CoinbaseCanonicalDataset(
        candles=candles,
        manifest=DatasetManifest(
            schema_version="1",
            dataset_version=version,
            source="coinbase_exchange_candles",
            venue=Venue.COINBASE,
            pair=pair,
            timeframe=Timeframe.ONE_HOUR,
            start_time=start,
            end_time=end,
            created_at=T + timedelta(days=10),
            row_count=len(candles),
            missing_open_times=gaps,
        ),
    )


def service(candles: tuple[Candle, ...] | None = None) -> MarketResearchService:
    return build_market_research(
        SimulationClock(T),
        [
            dataset(history() if candles is None else candles),
            dataset(history(ETH), ETH),
        ],
    )


def invoke(
    service: MarketResearchService,
    capability: MarketResearchCapability,
    pair: TradingPair = BTC,
    benchmark: TradingPair = ETH,
) -> ResearchToolSuccess | ResearchToolUnavailable:
    if capability is MarketResearchCapability.RELATIVE_STRENGTH:
        return service.relative_strength(pair, benchmark)
    result: ResearchToolSuccess | ResearchToolUnavailable = getattr(
        service, capability.value
    )(pair)
    return result


def success(
    service: MarketResearchService, capability: MarketResearchCapability
) -> ResearchToolSuccess:
    result = invoke(service, capability)
    assert isinstance(result, ResearchToolSuccess)
    return result


def test_exact_trend_golden() -> None:
    payload = success(service(), MarketResearchCapability.TREND).payload
    assert isinstance(payload, TrendPayload)
    assert payload.close == 159
    assert payload.sma20 == Decimal("149.5")
    assert payload.sma50 == Decimal("134.5")
    assert payload.close_vs_sma20 == Decimal(
        "0.0635451505016722408026755852842809364548494983278"
    )
    assert payload.close_vs_sma50 == Decimal(
        "0.1821561338289962825278810408921933085501858736059"
    )
    assert payload.sma20_vs_sma50 == Decimal(
        "0.1115241635687732342007434944237918215613382899628"
    )


def test_exact_momentum_golden() -> None:
    payload = success(service(), MarketResearchCapability.MOMENTUM).payload
    assert isinstance(payload, MomentumPayload)
    with localcontext(ORACLE):
        assert payload.return_1h == Decimal(159) / 158 - 1
        assert payload.return_6h == Decimal(159) / 153 - 1
        assert payload.return_24h == Decimal(159) / 135 - 1
        assert payload.previous_return_6h == Decimal(153) / 147 - 1
        assert payload.return_change_6h == (Decimal(159) / 153 - 1) - (
            Decimal(153) / 147 - 1
        )


def test_true_ranges_are_nonoverlapping_and_normalized_by_own_ending_closes() -> None:
    # Required indices 19..59: seed high/low cannot affect the means. TR20=100,
    # TR21..39=4, TR40=50, TR41..59=4. Each 20-range mean includes its own edge.
    candles = list(history())
    candles[19] = candle(19, high="10000", low="1")
    candles[20] = candle(20, high="219", low="119")
    candles[40] = candle(40, high="189", low="139")
    payload = success(
        service(tuple(candles)), MarketResearchCapability.VOLATILITY
    ).payload
    assert isinstance(payload, VolatilityPayload)
    assert payload.current_mean_true_range == Decimal("6.3")
    assert payload.previous_mean_true_range == Decimal("8.8")
    assert payload.current_ending_close == 159
    assert payload.previous_ending_close == 139
    with localcontext(ORACLE):
        assert payload.current_normalized_range == Decimal("6.3") / 159
        assert payload.previous_normalized_range == Decimal("8.8") / 139
        assert payload.normalized_range_ratio.value == (Decimal("6.3") / 159) / (
            Decimal("8.8") / 139
        )


def test_true_range_uses_previous_close_gaps_and_preserves_more_than_fifty_digits() -> (
    None
):
    huge = "1" + "0" * 70 + ".0000000000000000000000000000001"
    candles = tuple(
        candle(i, open="100", close="100", high=huge, low="1") for i in range(60)
    )
    payload = success(service(candles), MarketResearchCapability.VOLATILITY).payload
    assert isinstance(payload, VolatilityPayload)
    # Exact terminating true-range arithmetic, not a rounded research average.
    from fractions import Fraction

    assert Fraction(payload.current_mean_true_range) == Fraction(huge) - 1
    gaps = tuple(
        candle(
            i,
            open="10" if i % 2 else "100",
            close="10" if i % 2 else "100",
            high="10" if i % 2 else "100",
            low="10" if i % 2 else "100",
        )
        for i in range(60)
    )
    gap_payload = success(service(gaps), MarketResearchCapability.VOLATILITY).payload
    assert isinstance(gap_payload, VolatilityPayload)
    assert (
        gap_payload.current_mean_true_range
        == gap_payload.previous_mean_true_range
        == 90
    )


def test_bollinger_exact_golden_and_scanner_identical() -> None:
    candles = tuple(
        candle(i, close="99" if i % 2 == 0 else "101", open="100", low="98", high="102")
        for i in range(60)
    )
    payload = success(
        service(candles), MarketResearchCapability.BOLLINGER_POSITION
    ).payload
    assert isinstance(payload, BollingerPositionPayload)
    stats = rolling_bollinger_statistics(candles)[-1]
    assert stats is not None
    assert payload.sma20 == stats.sma == 100
    assert payload.population_std == stats.population_std == 1
    assert payload.lower_band == stats.lower_band == 98
    assert payload.upper_band == 102
    assert payload.percent_b.value == Decimal("0.75")
    assert payload.bandwidth_fraction == Decimal("0.04")


@pytest.mark.parametrize("last", ["1", "1000"])
def test_percent_b_is_not_clipped_and_irrational_stats_match_scanner(last: str) -> None:
    candles = tuple(
        candle(i, open="100", close=last if i == 59 else "100", high="1000", low="1")
        for i in range(60)
    )
    payload = success(
        service(candles), MarketResearchCapability.BOLLINGER_POSITION
    ).payload
    assert isinstance(payload, BollingerPositionPayload)
    stats = rolling_bollinger_statistics(candles)[-1]
    assert stats is not None
    assert (payload.sma20, payload.population_std, payload.lower_band) == (
        stats.sma,
        stats.population_std,
        stats.lower_band,
    )
    assert payload.percent_b.value is not None
    assert payload.percent_b.value < 0 if last == "1" else payload.percent_b.value > 1


def test_recent_levels_exclude_current_and_resolve_ties_to_most_recent() -> None:
    candles = tuple(
        candle(i, open="100", close="100", high="120", low="80") for i in range(60)
    )
    candles = candles[:-1] + (
        candle(59, open="100", close="100", high="1000", low="1"),
    )
    payload = success(service(candles), MarketResearchCapability.LEVELS).payload
    assert isinstance(payload, LevelsPayload)
    assert payload.recent_high == 120
    assert payload.recent_low == 80
    assert (
        payload.recent_high_close_time
        == payload.recent_low_close_time
        == T - timedelta(hours=1)
    )
    with localcontext(ORACLE):
        assert payload.recent_high_distance == Decimal(100) / Decimal(120) - 1
        assert payload.recent_low_distance == Decimal(100) / Decimal(80) - 1
    assert payload.recent_low_distance == Decimal("0.25")


def test_volume_uses_previous_24_base_volumes() -> None:
    payload = success(service(), MarketResearchCapability.VOLUME_CONTEXT).payload
    assert isinstance(payload, VolumeContextPayload)
    assert payload.current_base_volume == 60
    assert payload.previous_mean_base_volume == Decimal("47.5")
    with localcontext(ORACLE):
        assert payload.volume_ratio.value == Decimal(60) / Decimal("47.5")


@pytest.mark.parametrize("pair,benchmark", tuple(permutations(TradingPair, 2)))
def test_all_six_ordered_relative_comparisons(
    pair: TradingPair, benchmark: TradingPair
) -> None:
    histories = {
        p: tuple(
            candle(i, p, close=Decimal(100 + i * (n + 1)), high="1000", low="1")
            for i in range(60)
        )
        for n, p in enumerate(TradingPair)
    }
    svc = build_market_research(
        SimulationClock(T), [dataset(c, p) for p, c in histories.items()]
    )
    result = svc.relative_strength(pair, benchmark)
    assert isinstance(result, ResearchToolSuccess)
    payload = result.payload
    assert isinstance(payload, RelativeStrengthPayload)
    with localcontext(ORACLE):
        for hours in (6, 24):
            subject_return = (
                histories[pair][-1].close / histories[pair][-1 - hours].close - 1
            )
            benchmark_return = (
                histories[benchmark][-1].close / histories[benchmark][-1 - hours].close
                - 1
            )
            assert getattr(payload, f"subject_return_{hours}h") == subject_return
            assert getattr(payload, f"benchmark_return_{hours}h") == benchmark_return
            assert (
                getattr(payload, f"spread_{hours}h")
                == subject_return - benchmark_return
            )
    assert [(i.source.role, i.source.pair) for i in result.context.inputs] == [
        (MarketRole.SUBJECT, pair),
        (MarketRole.BENCHMARK, benchmark),
    ]


@pytest.mark.parametrize("pair", list(TradingPair))
def test_self_comparison_raises(pair: TradingPair) -> None:
    with pytest.raises(ValueError, match="self-comparison"):
        service().relative_strength(pair, pair)


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_fixed_api_and_window(capability: MarketResearchCapability) -> None:
    method = getattr(MarketResearchService, capability.value)
    assert set(inspect.signature(method).parameters) == (
        {"self", "pair", "benchmark"}
        if capability is MarketResearchCapability.RELATIVE_STRENGTH
        else {"self", "pair"}
    )
    result = success(service(), capability)
    assert result.context.window.bar_count == POLICY.required_bars(capability)
    assert result.context.window.end == T
    assert result.context.window.start == T - timedelta(
        hours=POLICY.required_bars(capability)
    )
    assert result.evidence.observed_at == result.evidence.available_at == T


SLOTS = [
    (capability, i)
    for capability in CAPABILITIES
    for i in range(POLICY.required_bars(capability))
]


@pytest.mark.parametrize("capability,slot", SLOTS)
@pytest.mark.parametrize("delayed", [False, True])
def test_every_required_slot_is_mandatory_without_older_fallback(
    capability: MarketResearchCapability, slot: int, delayed: bool
) -> None:
    missing = 60 - POLICY.required_bars(capability) + slot
    candles = tuple(
        candle(
            i,
            available_at=T + timedelta(microseconds=1)
            if i == missing
            else START + timedelta(hours=i + 1),
        )
        for i in range(60)
        if delayed or i != missing
    )
    result = invoke(service(candles), capability)
    assert isinstance(result, ResearchToolUnavailable)
    assert result.reason is (
        ResearchUnavailableReason.REFERENCE_UNAVAILABLE
        if missing == 59
        else ResearchUnavailableReason.HISTORY_GAP
    )
    assert result.role is MarketRole.SUBJECT


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_short_leading_history(capability: MarketResearchCapability) -> None:
    count = POLICY.required_bars(capability)
    result = invoke(service(history()[-count + 1 :]), capability)
    assert isinstance(result, ResearchToolUnavailable)
    assert result.reason is ResearchUnavailableReason.INSUFFICIENT_HISTORY


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_all_dependencies_may_become_available_exactly_at_t(
    capability: MarketResearchCapability,
) -> None:
    candles = tuple(candle(i, available_at=T) for i in range(60))
    result = success(service(candles), capability)
    assert (
        result.evidence.available_at
        == max(c.available_at for c in candles[-POLICY.required_bars(capability) :])
        == T
    )


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_future_and_noncontributing_data_do_not_change_identity(
    capability: MarketResearchCapability,
) -> None:
    original = success(service(), capability)
    changed = success(
        service(history() + (candle(60, high="999999", close="999999"),)), capability
    )
    assert changed.payload == original.payload
    assert [i.window_digest for i in changed.context.inputs] == [
        i.window_digest for i in original.context.inputs
    ]
    assert changed.evidence.evidence_id == original.evidence.evidence_id
    assert (
        changed.context.inputs[0].source.dataset_version
        != original.context.inputs[0].source.dataset_version
    )
    old_changed = (candle(0, high="999999", close="999999"),) + history()[1:]
    assert (
        success(service(old_changed), capability).evidence.evidence_id
        == original.evidence.evidence_id
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("open", "159.1"),
        ("high", "162"),
        ("low", "156"),
        ("close", "159.1"),
        ("volume", "0"),
    ],
)
@pytest.mark.parametrize("capability", CAPABILITIES)
def test_every_contributing_candle_field_affects_identity(
    capability: MarketResearchCapability, field: str, value: str
) -> None:
    original = success(service(), capability)
    changes: dict[str, Any] = {field: value}
    changed = success(service(history()[:-1] + (candle(59, **changes),)), capability)
    assert (
        changed.context.inputs[0].window_digest
        != original.context.inputs[0].window_digest
    )
    assert changed.evidence.evidence_id != original.evidence.evidence_id


def test_dependency_availability_changes_digest_but_evidence_remains_at_t() -> None:
    original = success(service(), MarketResearchCapability.TREND)
    candles = list(history())
    candles[15] = candle(15, available_at=T)
    changed = success(service(tuple(candles)), MarketResearchCapability.TREND)
    assert changed.payload == original.payload
    assert changed.evidence.available_at == original.evidence.available_at == T
    assert changed.evidence.evidence_id != original.evidence.evidence_id


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_repeated_outputs_and_hostile_decimal_contexts(
    capability: MarketResearchCapability,
) -> None:
    svc = service()
    expected = success(svc, capability)
    for _ in range(2):
        assert success(svc, capability).model_dump_json() == expected.model_dump_json()
    with hostile_decimal_context() as hostile:
        before = repr(hostile)
        actual = success(svc, capability)
        assert actual == expected
        assert (
            ResearchToolSuccess.model_validate_json(actual.model_dump_json()) == actual
        )
        assert repr(hostile) == before


@pytest.mark.parametrize("capability", CAPABILITIES)
def test_source_input_order_and_decimal_spelling_independence(
    capability: MarketResearchCapability,
) -> None:
    btc, eth = dataset(history()), dataset(history(ETH), ETH)
    expected = success(
        build_market_research(SimulationClock(T), [btc, eth]), capability
    )
    equivalent = tuple(
        Candle.model_validate(
            c.model_dump()
            | {
                name: format(getattr(c, name), ".5f")
                for name in ("open", "high", "low", "close", "volume")
            }
        )
        for c in reversed(btc.candles)
    )
    actual = success(
        build_market_research(
            SimulationClock(T),
            [
                replace(eth, candles=tuple(reversed(eth.candles))),
                replace(btc, candles=equivalent),
            ],
        ),
        capability,
    )
    assert actual == expected
    assert actual.model_dump_json() == expected.model_dump_json()


def test_acquisition_only_changes_provenance() -> None:
    original = dataset(history())
    acquisition = CoinbaseAcquisitionRecord(
        schema_version="1",
        dataset_version=original.manifest.dataset_version,
        created_at=T,
        requested_start=START,
        requested_end=T,
        capture_ids=(),
        missing_open_times=(),
    )
    other = acquisition.model_copy(update={"created_at": T + timedelta(days=1)})
    first = success(
        build_market_research(
            SimulationClock(T), [replace(original, acquisition=acquisition)]
        ),
        MarketResearchCapability.TREND,
    )
    second = success(
        build_market_research(
            SimulationClock(T), [replace(original, acquisition=other)]
        ),
        MarketResearchCapability.TREND,
    )
    assert first.evidence.evidence_id == second.evidence.evidence_id
    assert (
        first.context.inputs[0].window_digest == second.context.inputs[0].window_digest
    )
    assert (
        first.context.inputs[0].source.acquisition_id
        != second.context.inputs[0].source.acquisition_id
    )


def test_subject_benchmark_swap_changes_identity_even_for_equal_returns() -> None:
    svc = service()
    first, second = svc.relative_strength(BTC, ETH), svc.relative_strength(ETH, BTC)
    assert isinstance(first, ResearchToolSuccess) and isinstance(
        second, ResearchToolSuccess
    )
    assert first.payload == second.payload
    assert first.evidence.evidence_id != second.evidence.evidence_id


@pytest.mark.parametrize("slot", range(25))
def test_relative_requires_every_benchmark_slot(slot: int) -> None:
    candles = history(ETH)
    missing = 35 + slot
    svc = build_market_research(
        SimulationClock(T),
        [dataset(history()), dataset(candles[:missing] + candles[missing + 1 :], ETH)],
    )
    result = svc.relative_strength(BTC, ETH)
    assert isinstance(result, ResearchToolUnavailable)
    assert result.role is MarketRole.BENCHMARK
    assert result.reason is (
        ResearchUnavailableReason.REFERENCE_UNAVAILABLE
        if slot == 24
        else ResearchUnavailableReason.HISTORY_GAP
    )


def test_relative_has_no_latest_common_time_or_forward_fill() -> None:
    svc = build_market_research(
        SimulationClock(T), [dataset(history()), dataset(history(ETH)[:-1], ETH)]
    )
    result = svc.relative_strength(BTC, ETH)
    assert isinstance(result, ResearchToolUnavailable)
    assert result.reason is ResearchUnavailableReason.REFERENCE_UNAVAILABLE


@pytest.mark.parametrize("kind", ["range", "volume", "bands"])
def test_zero_denominators_have_explicit_undefined_reasons(kind: str) -> None:
    candles = tuple(
        candle(i, open="100", high="100", low="100", close="100", volume="0")
        for i in range(60)
    )
    capability = {
        "range": MarketResearchCapability.VOLATILITY,
        "volume": MarketResearchCapability.VOLUME_CONTEXT,
        "bands": MarketResearchCapability.BOLLINGER_POSITION,
    }[kind]
    payload = success(service(candles), capability).payload
    if isinstance(payload, VolatilityPayload):
        assert payload.current_mean_true_range == payload.previous_mean_true_range == 0
        ratio, reason = (
            payload.normalized_range_ratio,
            UndefinedReason.ZERO_PREVIOUS_NORMALIZED_RANGE,
        )
    elif isinstance(payload, VolumeContextPayload):
        ratio, reason = payload.volume_ratio, UndefinedReason.ZERO_BASELINE_VOLUME
    else:
        assert isinstance(payload, BollingerPositionPayload)
        assert payload.bandwidth_fraction == 0
        ratio, reason = payload.percent_b, UndefinedReason.ZERO_BAND_WIDTH
    assert ratio.value is None and ratio.undefined_reason is reason


def test_zero_current_range_and_volume_are_defined_with_positive_baselines() -> None:
    candles = tuple(
        candle(
            i,
            open="100",
            close="100",
            high="102" if i < 40 else "100",
            low="98" if i < 40 else "100",
            volume="1" if i < 59 else "0",
        )
        for i in range(60)
    )
    volatility = success(service(candles), MarketResearchCapability.VOLATILITY).payload
    volume = success(service(candles), MarketResearchCapability.VOLUME_CONTEXT).payload
    assert isinstance(volatility, VolatilityPayload) and isinstance(
        volume, VolumeContextPayload
    )
    assert volatility.normalized_range_ratio.value == volume.volume_ratio.value == 0
    assert (
        volatility.normalized_range_ratio.undefined_reason
        is volume.volume_ratio.undefined_reason
        is None
    )


def test_source_unavailable_and_empty_bound_source_are_distinct() -> None:
    unbound = build_market_research(SimulationClock(T), [])
    empty = build_market_research(SimulationClock(T), [dataset(())])
    for svc, reason in [
        (unbound, ResearchUnavailableReason.SOURCE_UNAVAILABLE),
        (empty, ResearchUnavailableReason.REFERENCE_UNAVAILABLE),
    ]:
        result = svc.trend(BTC)
        assert isinstance(result, ResearchToolUnavailable)
        assert result.reason is reason
    result = build_market_research(
        SimulationClock(T), [dataset(history())]
    ).relative_strength(BTC, ETH)
    assert isinstance(result, ResearchToolUnavailable)
    assert result.role is MarketRole.BENCHMARK
    assert result.reason is ResearchUnavailableReason.SOURCE_UNAVAILABLE


@pytest.mark.parametrize(
    "offset", [timedelta(minutes=1), timedelta(seconds=1), timedelta(microseconds=1)]
)
def test_gateway_time_must_be_hourly(offset: timedelta) -> None:
    svc = build_market_research(SimulationClock(T + offset), [dataset(history())])
    with pytest.raises(ValueError, match="hourly UTC"):
        svc.trend(BTC)


def direct_service(gateway: PointInTimeDataGateway) -> MarketResearchService:
    return MarketResearchService(
        gateway,
        [
            MarketSourceBinding(pair=p, dataset_version="sha256:" + "a" * 64)
            for p in (BTC, ETH)
        ],
    )


@pytest.mark.parametrize(
    "case",
    [
        "future",
        "unavailable",
        "wrong_pair",
        "duplicate",
        "reverse",
        "corrupt",
        "misaligned",
    ],
)
def test_gateway_output_is_revalidated(case: str) -> None:
    candles = history()
    if case == "future":
        candles += (candle(60),)
    elif case == "unavailable":
        candles = candles[:-1] + (
            candle(59, available_at=T + timedelta(microseconds=1)),
        )
    elif case == "wrong_pair":
        candles = candles[:-1] + (candle(59, ETH),)
    elif case == "duplicate":
        candles += (candles[-1],)
    elif case == "reverse":
        candles = tuple(reversed(candles))
    elif case == "corrupt":
        candles = candles[:-1] + (candles[-1].model_copy(update={"high": Decimal(1)}),)
    else:
        candles = candles[:-1] + (
            candles[-1].model_copy(update={"open_time": T - timedelta(minutes=30)}),
        )

    class BadGateway(PointInTimeDataGateway):
        def read_candles(
            self, *, venue: Venue, pair: TradingPair, timeframe: Timeframe
        ) -> tuple[Candle, ...]:
            return candles

    with pytest.raises(ValueError):
        direct_service(BadGateway(SimulationClock(T), [])).trend(BTC)


@pytest.mark.parametrize("advance_read", [1, 2])
def test_clock_change_between_cross_asset_reads_raises(advance_read: int) -> None:
    clock = SimulationClock(T)

    class AdvancingGateway(PointInTimeDataGateway):
        calls = 0

        def read_candles(
            self, *, venue: Venue, pair: TradingPair, timeframe: Timeframe
        ) -> tuple[Candle, ...]:
            result = super().read_candles(venue=venue, pair=pair, timeframe=timeframe)
            self.calls += 1
            if self.calls == advance_read:
                clock.advance_to(T + timedelta(hours=1))
            return result

    with pytest.raises(ValueError, match="clock changed"):
        direct_service(
            AdvancingGateway(clock, history() + history(ETH))
        ).relative_strength(BTC, ETH)


@pytest.mark.parametrize(
    "case", ["content", "manifest", "duplicate", "acquisition", "forged_acquisition"]
)
def test_assembly_rejects_corrupt_canonical_or_provenance_inputs(case: str) -> None:
    original = dataset(history())
    if case == "content":
        inputs = [
            replace(original, candles=history()[:-1] + (candle(59, close="160"),))
        ]
    elif case == "manifest":
        inputs = [
            replace(
                original, manifest=original.manifest.model_copy(update={"row_count": 1})
            )
        ]
    elif case == "duplicate":
        inputs = [original, original]
    else:
        acquisition = CoinbaseAcquisitionRecord(
            schema_version="1",
            dataset_version="sha256:" + "b" * 64,
            created_at=T,
            requested_start=START,
            requested_end=T,
            capture_ids=(),
            missing_open_times=(),
        )
        if case == "forged_acquisition":
            acquisition = acquisition.model_copy(
                update={"created_at": T.replace(tzinfo=None)}
            )
        inputs = [replace(original, acquisition=acquisition)]
    with pytest.raises(ValueError):
        build_market_research(SimulationClock(T), inputs)


def test_provenance_is_canonical_schema_and_complete_typed_context() -> None:
    result = success(service(), MarketResearchCapability.RELATIVE_STRENGTH)
    envelope = json.loads(result.evidence.model_dump_json())
    assert envelope["source"] == "coinbase_exchange_candles"
    provenance = envelope["provenance"]
    assert provenance["producer"] == "market_research_v1.relative_strength"
    assert provenance["source_version"] == "1"
    params = provenance["query_params"]
    assert params["policy"] == {"policy_id": "market_research_v1"}
    assert params["window"]["end"] == "2022-01-03T12:00:00.000000Z"
    assert [i["source"]["role"] for i in params["inputs"]] == ["subject", "benchmark"]
    for item in params["inputs"]:
        assert set(item["source"]) == {
            "role",
            "venue",
            "pair",
            "timeframe",
            "source",
            "source_schema_version",
            "dataset_version",
            "acquisition_id",
        }
        assert item["window_digest"].startswith("sha256:")


def test_semantic_identity_matches_independent_canonical_encoding() -> None:
    from hashlib import sha256

    def digest(value: dict[str, Any]) -> str:
        return (
            "sha256:"
            + sha256(
                json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )

    def timestamp(hour: int) -> str:
        return (START + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    market = {"venue": "COINBASE", "pair": "BTC-USD", "timeframe": "1h"}
    window = {
        "start": timestamp(10),
        "end": timestamp(60),
        "bar_count": 50,
        "timeframe": "1h",
    }
    rows = [
        market
        | {
            "open_time": timestamp(i),
            "close_time": timestamp(i + 1),
            "available_at": timestamp(i + 1),
            "open": str(100 + i),
            "high": str(102 + i),
            "low": str(98 + i),
            "close": str(100 + i),
            "volume": str(i + 1),
        }
        for i in range(10, 60)
    ]
    window_digest = digest(
        {
            "identity_encoding_version": "1",
            "market": market,
            "window": window,
            "candles": rows,
        }
    )
    payload = {
        "capability": "trend",
        "close": "159",
        "sma20": "149.5",
        "sma50": "134.5",
        "close_vs_sma20": "0.0635451505016722408026755852842809364548494983278",
        "close_vs_sma50": "0.1821561338289962825278810408921933085501858736059",
        "sma20_vs_sma50": "0.1115241635687732342007434944237918215613382899628",
    }
    expected_id = digest(
        {
            "identity_encoding_version": "1",
            "policy": {"policy_id": "market_research_v1"},
            "capability": "trend",
            "observed_at": timestamp(60),
            "window": window,
            "inputs": [market | {"role": "subject", "window_digest": window_digest}],
            "payload": payload,
        }
    )
    actual = success(service(), MarketResearchCapability.TREND)
    assert actual.context.inputs[0].window_digest == window_digest
    assert actual.evidence.evidence_id == expected_id


def test_oldest_prior_level_and_volume_slot_contributes() -> None:
    candles = tuple(
        candle(i, open="100", close="100", high="102", low="98", volume="1")
        for i in range(60)
    )
    candles = (
        candles[:35]
        + (candle(35, open="100", close="100", high="150", low="50", volume="25"),)
        + candles[36:]
    )
    svc = service(candles)
    levels = success(svc, MarketResearchCapability.LEVELS).payload
    volume = success(svc, MarketResearchCapability.VOLUME_CONTEXT).payload
    assert isinstance(levels, LevelsPayload) and isinstance(
        volume, VolumeContextPayload
    )
    assert levels.recent_high == 150 and levels.recent_low == 50
    assert (
        levels.recent_high_close_time
        == levels.recent_low_close_time
        == START + timedelta(hours=36)
    )
    assert volume.previous_mean_base_volume == 2
    assert volume.volume_ratio.value == Decimal("0.5")


def test_assembly_keeps_the_runtime_clock_authoritative() -> None:
    clock = SimulationClock(T)
    svc = build_market_research(clock, [dataset(history() + (candle(60),))])
    first = success(svc, MarketResearchCapability.TREND)
    assert clock.now == T
    clock.advance_to(T + timedelta(hours=1))
    second = success(svc, MarketResearchCapability.TREND)
    assert second.evidence.observed_at == clock.now
    assert second.evidence.evidence_id != first.evidence.evidence_id


def test_invalid_returned_history_raises_before_missing_history_outcome() -> None:
    class CorruptBenchmark(PointInTimeDataGateway):
        def read_candles(
            self, *, venue: Venue, pair: TradingPair, timeframe: Timeframe
        ) -> tuple[Candle, ...]:
            if pair is BTC:
                return ()
            return (candle(59, ETH).model_copy(update={"close": Decimal(0)}),)

    with pytest.raises(ValueError):
        direct_service(CorruptBenchmark(SimulationClock(T), [])).relative_strength(
            BTC, ETH
        )
