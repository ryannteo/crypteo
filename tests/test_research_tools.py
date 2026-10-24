"""Tool identity snapshots and read-only deterministic service access."""

from itertools import combinations

import pytest
from pydantic import ValidationError
from research_support import BTC, ETH, SOL, T, service, tool, workflow_config

from crypteo.domain.market import TradingPair
from crypteo.domain.market_research import (
    MarketResearchCapability,
    MarketResearchPolicy,
    MarketResearchQuery,
    ResearchToolSuccess,
    ResearchToolUnavailable,
)
from crypteo.domain.replay_research import research_policy
from crypteo.domain.research_workflow import BENCHMARK_ORDER, ToolRejection
from crypteo.research_tools import (
    TOOL_DESCRIPTIONS,
    TOOL_VERSION,
    bind_tool_request,
    dispatch_tool,
    tool_definitions,
)

# Pinned digests of the previous (v4) tool version; every v5 description changed.
V4_DIGESTS = {
    "trend": "c82ce9d8d550c9eaec0538a25ab1b2a0ddb6ea078312b0f0ea2d5dc4799832fa",
    "momentum": "c285f89a3f66a0d9369c80020160e3125fc61bddfa0e244b0623f5e243052527",
    "volatility": "bf2c13e00d2dc3817f747a1b2172d2d2c660f6ad0a53343bbff4f5b6bd782754",
    "bollinger_position": (
        "2ce528d4ff1ee46edb66400cf8a3122386c93d666eb019ef1dcfecbed0ffba6f"
    ),
    "levels": "758f241968e9fa5b3ac066a8a51ee419da10764a505e4b1ebad9b7c040af6c3b",
    "volume_context": (
        "7cfcd4a8d2474c18dcd7a7b4ba011d6f5925cffc96f1bd4e72b12f32c5c10db1"
    ),
    "candle_structure": (
        "d9a309f73ee82c880255746024ede40bbccf67e04721b9498120d337743daf18"
    ),
    "higher_timeframe": (
        "bf9f4be59991949bdeba8845050aa465c12ec169a8d769079ecacac873939a5b"
    ),
    "lower_band_crossings": (
        "e198511f1cc7c38926c9817d8a682ab30e9450b20c75f54605bd7ea8b2c15ada"
    ),
    "relative_strength": (
        "46a313cda8bdbe314a3d64119ffde3c0aaadeb92169c5f3e0e95a47ab35c55ca"
    ),
}
SINGLE_ASSET_DIGESTS = {
    "trend": "c51d443913434092414fa8a868266322db65c0ddd05a7fb3179fe69d01e53441",
    "momentum": "cb6f096582112cd14cb859bb2643cb299c3c745853b8cb9c6f4f9f29d604ebd7",
    "volatility": "b557775e204a34298bf24aa931a5bac0237ebe04a32a2fd25fee743f062ea6f0",
    "bollinger_position": (
        "9e6dc6ed95979f3d2161c66c3b48d8be5a43bed8b724d6a4ba338c66bd864a52"
    ),
    "levels": "9026626fbb3a952045daebc7d4f210b5d62b4c06619c8e900572d265fb6403af",
    "volume_context": (
        "bde6b2cf1429eea7e8a91c01263c5aec6c06b029e04b5f57ddb9af4571e59b31"
    ),
    "candle_structure": (
        "9cfa6a801ecf25319611aa758b347fa58eddc902d83ba98c55b38caee7d2c65f"
    ),
    "higher_timeframe": (
        "1a31996a4aa104319e9f4ceeb2361e404535e2b9f9132b4882f3e1154b9110cd"
    ),
    "lower_band_crossings": (
        "b4cb962ca00f9b105e4c4d4aa818bbdec6571cc7bc5f9bf3b7222e2c5e2ddfba"
    ),
}
RELATIVE_STRENGTH_DIGEST = (
    "195ac8cb7f628612a8f35c97b3d91e721d2f4617f8a855ffe14c945d69d27dcd"
)
# The v5 description text, verbatim.
SUFFIX = (
    "Completed Coinbase hourly candles visible at T; venue, timeframe, window and "
    "cutoff are runtime-owned. T-kh is the candle closing k hours before T; each "
    "*_candles payload field gives the first and last close time and the count of "
    "the candles it covers."
)
DESCRIPTIONS = {
    "trend": (
        "Close at T, SMA20 of the closes of candles T-19h..T (sma20_candles) "
        "and SMA50 of candles T-49h..T (sma50_candles), and close/SMA20-1, "
        "close/SMA50-1 and SMA20/SMA50-1."
    ),
    "momentum": (
        "Close-to-close returns: return_1h (close at T vs T-1h), return_6h (T "
        "vs T-6h), return_24h (T vs T-24h), previous_return_6h (T-6h vs "
        "T-12h), and return_6h minus previous_return_6h."
    ),
    "volatility": (
        "Mean true range (intrabar high/low and previous close) of candles "
        "T-19h..T (current_candles) and of candles T-39h..T-20h "
        "(previous_candles), each divided by the close of its last candle (T "
        "and T-20h), and current/previous. Arithmetic means, not Wilder ATR."
    ),
    "bollinger_position": (
        "The scanner's Bollinger bands over the closes of candles T-19h..T "
        "(band_candles; population standard deviation, +/-2 sigma): SMA20, "
        "bands, unclipped percent_b of the close at T, and band width as a "
        "fraction of SMA20."
    ),
    "levels": (
        "Highest intrabar high and lowest intrabar low of candles T-24h..T-1h "
        "(level_candles; the candle ending at T is excluded), the close time of "
        "the candle holding each (the most recent on ties), and close at "
        "T/level-1 for each. Observational levels, not support/resistance or "
        "trade terms."
    ),
    "volume_context": (
        "Base-asset volume of the candle ending at T, mean volume of candles "
        "T-24h..T-1h (baseline_candles), and their ratio."
    ),
    "candle_structure": (
        "Anatomy of the candle ending at T: OHLC, range, signed body, upper and "
        "lower wicks, their fractions of the range, and close_location, the "
        "close's position in the range as a fraction (0 at the low, 1 at the "
        "high), not a price distance; plus the range relative to "
        "baseline_mean_true_range, the mean true range of candles T-20h..T-1h "
        "(baseline_candles). Volatility's current mean true range differs: it "
        "covers candles T-19h..T."
    ),
    "higher_timeframe": (
        "4-hour and daily views built from 25 consecutive blocks of hourly "
        "candles ending at T (block 0 newest, not calendar-aligned; 600-candle "
        "window). Per view: SMA20 of the closes of blocks 0-19 (sma20_candles) "
        "and of blocks 5-24 (previous_sma20_candles), close at T vs SMA20, "
        "SMA20 change, the return since the close of block 5 "
        "(close_5_blocks_ago_close_time: T-20h for 4-hour, T-120h for daily), "
        "and the highest high and lowest low of blocks 1-20 with the close time "
        "of the candle holding each (the most recent on ties) and distances. "
        "Blocks 1-20 cover candles T-83h..T-4h for 4-hour and T-503h..T-24h for "
        "daily (range_candles), so the 4-hour range includes candles T-23h..T-4h "
        "that the daily range excludes, and its high or low can lie outside the "
        "daily range."
    ),
    "lower_band_crossings": (
        "History of this pair's hourly closes crossing below the lower "
        "Bollinger band (scanner rule) in candles T-719h..T (lookback_candles). "
        "Crossings at T-6h or earlier, whose next 6 candles all closed by T, are "
        "sampled: counts and fractions reaching the trigger's SMA20 intrabar and "
        "closing higher after 6 hours, close-return mean, median and range, and "
        "mean adverse and favorable excursions. Crossings at T-5h..T are pending "
        "and not sampled: pending_count counts them, pending_close_times lists "
        "their close times, and current_candle_crossed is true exactly when the "
        "candle ending at T is one of them."
    ),
    "relative_strength": (
        "Subject minus benchmark close-to-close returns over 6h (close at T vs "
        "T-6h) and 24h (T vs T-24h), from aligned candles of both pairs. Offered "
        "only for benchmarks with a bound source."
    ),
}


def digests(benchmarks: tuple[TradingPair, ...]) -> dict[str, str]:
    return {
        t.name: t.identity.digest.removeprefix("sha256:")
        for t in tool_definitions(BTC, benchmarks)
    }


def test_v5_tool_identity_snapshots() -> None:
    current = digests((ETH, SOL))
    assert current == SINGLE_ASSET_DIGESTS | {
        "relative_strength": RELATIVE_STRENGTH_DIGEST
    }
    # Without a bound benchmark, relative strength is not offered at all.
    assert digests(()) == SINGLE_ASSET_DIGESTS
    assert all(current[name] != V4_DIGESTS[name] for name in V4_DIGESTS)
    assert TOOL_VERSION == "bounded_market_tools_v5"
    assert {t.identity.version for t in tool_definitions(BTC, ())} == {TOOL_VERSION}


def test_policy_identity_moves_only_tools_and_market_research() -> None:
    policy = research_policy(workflow_config())
    assert policy.market_research == "market_research_v3"
    assert (policy.workflow, policy.model_view) == (
        "bounded_research_v2",
        "compact_research_view_v2",
    )
    # Prompt and schema identities are those pinned before v5.
    assert [p.digest.removeprefix("sha256:") for p in policy.prompts] == [
        "850412dde6c09ce5b9ba74c1deec75cbd5a05a6ab766978aac6c91d530294172",
        "991afdb1f18f04a095cdaff07577e0a445ac9885a3deaf01685a7e2d1b791792",
        "9f3261966e7fb217c9de52cb4c00236d344081aee7afc9f8995c82f3665dbd74",
        "3445cbadac61157d7a500c6ac396a21c3f6429bc7edaa98a4cfda37300d06865",
    ]
    assert [s.digest.removeprefix("sha256:") for s in policy.schemas] == [
        "10a1bb6d0fbfb18dfac09e4864265aeafccc228c3f9765fea0e3fb6f14bdb84f",
        "7a62eaa5f8f91a3a94a93b32477db6c287f1a2d586258f071a8fdccc9472da26",
        "498c2f78279c95bd030323aa8411a16a7399c61c841480653b47e9e122ed2664",
        "54f953c92fd301620f314d8fefb2b2ed037f0aa812c4bf4e39a8a6264d40adcc",
    ]
    assert {d.version for offer in policy.tools for d in offer.definitions} == {
        TOOL_VERSION
    }


def test_tool_order_and_single_pair_offer() -> None:
    order = [
        "trend",
        "momentum",
        "volatility",
        "bollinger_position",
        "levels",
        "volume_context",
        "candle_structure",
        "higher_timeframe",
        "lower_band_crossings",
        "relative_strength",
    ]
    assert [c.value for c in MarketResearchCapability] == order
    assert [t.name for t in tool_definitions(BTC, (ETH, SOL))] == order
    assert [t.name for t in tool_definitions(BTC, ())] == order[:-1]
    assert len(tool_definitions(BTC, ())) == 9
    assert {
        c.value: MarketResearchPolicy().required_bars(c)
        for c in MarketResearchCapability
    } == dict(zip(order, [50, 25, 41, 20, 25, 25, 22, 600, 720, 25], strict=True))


@pytest.mark.parametrize("subject", tuple(TradingPair))
@pytest.mark.parametrize("size", [0, 1, 2])
def test_tools_offer_relative_strength_only_for_given_benchmarks(
    subject: TradingPair, size: int
) -> None:
    others = tuple(p for p in BENCHMARK_ORDER if p != subject)
    for benchmarks in combinations(others, size):
        definitions = tool_definitions(subject, benchmarks)
        names = [c.value for c in MarketResearchCapability]
        assert [t.name for t in definitions] == (names if benchmarks else names[:-1])
        for t in definitions:
            parameters = t.model_dump(mode="json")["parameters"]
            if t.name == "relative_strength":
                assert parameters["properties"]["benchmark"]["enum"] == [
                    p.value for p in benchmarks
                ]
                assert parameters["required"] == ["benchmark"]
            else:
                assert parameters == {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                }


@pytest.mark.parametrize(
    "benchmarks,message",
    [
        ((BTC,), "subject"),
        ((ETH, BTC), "subject"),
        ((ETH, ETH), "repeat"),
        ((SOL, ETH), "BENCHMARK_ORDER"),
    ],
)
def test_invalid_benchmark_offers_raise(
    benchmarks: tuple[TradingPair, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        tool_definitions(BTC, benchmarks)
    with pytest.raises(ValueError, match=message):
        bind_tool_request(tool().requests, BTC, benchmarks)


def span_fields(value: object) -> set[str]:
    if isinstance(value, dict):
        return {k for k in value if k.endswith("_candles")} | {
            name for item in value.values() for name in span_fields(item)
        }
    return set()


@pytest.mark.parametrize("capability", MarketResearchCapability)
def test_descriptions_are_the_v5_text_and_name_every_span(
    capability: MarketResearchCapability,
) -> None:
    assert set(TOOL_DESCRIPTIONS) == set(MarketResearchCapability)
    definition = next(
        t for t in tool_definitions(BTC, (ETH,)) if t.name == capability.value
    )
    assert TOOL_DESCRIPTIONS[capability] == DESCRIPTIONS[capability.value]
    assert definition.description == f"{DESCRIPTIONS[capability.value]} {SUFFIX}"
    assert definition.description.isascii() and "  " not in definition.description
    query = bind_tool_request(
        tool(
            capability.value,
            {"benchmark": ETH.value}
            if capability is MarketResearchCapability.RELATIVE_STRENGTH
            else {},
        ).requests,
        BTC,
        (ETH,),
    )
    assert isinstance(query, MarketResearchQuery)
    result = dispatch_tool(service(hours=721), query)
    assert isinstance(result, ResearchToolSuccess)
    # Each payload span is named in its description by its field name.
    for name in span_fields(result.payload.model_dump()):
        assert f"({name}" in definition.description


@pytest.mark.parametrize(
    "benchmarks,requested,message",
    [
        ((), "ETH-USD", "relative_strength is not offered without a bound benchmark"),
        ((), "SOL-USD", "relative_strength is not offered without a bound benchmark"),
        ((ETH,), "SOL-USD", "relative_strength requires only a permitted benchmark"),
        ((SOL,), "ETH-USD", "relative_strength requires only a permitted benchmark"),
    ],
)
def test_relative_strength_binds_only_given_benchmarks(
    benchmarks: tuple[TradingPair, ...], requested: str, message: str
) -> None:
    request = tool("relative_strength", {"benchmark": requested}).requests
    assert bind_tool_request(request, BTC, benchmarks) == ToolRejection(
        code="INVALID_TOOL_REQUEST", message=message
    )
    if benchmarks:
        query = bind_tool_request(
            tool("relative_strength", {"benchmark": benchmarks[0].value}).requests,
            BTC,
            benchmarks,
        )
        assert query == MarketResearchQuery(
            capability=MarketResearchCapability.RELATIVE_STRENGTH,
            pair=BTC,
            benchmark=benchmarks[0],
        )


def test_service_accessors_are_read_only_and_canonically_ordered() -> None:
    svc = service(pairs=(ETH, BTC))
    assert svc.now == T
    assert tuple(s.pair for s in svc.source_bindings) == (BTC, ETH)
    for attribute in ("now", "source_bindings"):
        with pytest.raises(AttributeError):
            setattr(svc, attribute, T)
    with pytest.raises(ValidationError):
        svc.source_bindings[0].dataset_version = "changed"


@pytest.mark.parametrize("capability", MarketResearchCapability)
@pytest.mark.parametrize("unavailable", [False, True])
def test_bound_query_dispatch_preserves_capability_output(
    capability: MarketResearchCapability, unavailable: bool
) -> None:
    svc = service(hours=721, delayed=720 if unavailable else None)
    relative = capability is MarketResearchCapability.RELATIVE_STRENGTH
    request = tool(capability.value, {"benchmark": ETH.value} if relative else {})
    query = bind_tool_request(request.requests, BTC, (ETH,))
    assert isinstance(query, MarketResearchQuery)
    expected = (
        svc.relative_strength(BTC, ETH)
        if relative
        else {
            MarketResearchCapability.TREND: svc.trend,
            MarketResearchCapability.MOMENTUM: svc.momentum,
            MarketResearchCapability.VOLATILITY: svc.volatility,
            MarketResearchCapability.BOLLINGER_POSITION: svc.bollinger_position,
            MarketResearchCapability.LEVELS: svc.levels,
            MarketResearchCapability.VOLUME_CONTEXT: svc.volume_context,
            MarketResearchCapability.CANDLE_STRUCTURE: svc.candle_structure,
            MarketResearchCapability.HIGHER_TIMEFRAME: svc.higher_timeframe,
            MarketResearchCapability.LOWER_BAND_CROSSINGS: svc.lower_band_crossings,
        }[capability](BTC)
    )
    actual = dispatch_tool(svc, query)
    assert isinstance(
        actual, ResearchToolUnavailable if unavailable else ResearchToolSuccess
    )
    assert actual.model_dump_json() == expected.model_dump_json()
