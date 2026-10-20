"""Tool identity snapshots and read-only deterministic service access."""

from itertools import combinations

import pytest
from pydantic import ValidationError
from research_support import BTC, ETH, SOL, T, service, tool

from crypteo.domain.market import TradingPair
from crypteo.domain.market_research import (
    MarketResearchCapability,
    MarketResearchPolicy,
    MarketResearchQuery,
    ResearchToolSuccess,
    ResearchToolUnavailable,
)
from crypteo.domain.research_workflow import BENCHMARK_ORDER, ToolRejection
from crypteo.research_tools import (
    TOOL_DESCRIPTIONS,
    TOOL_VERSION,
    bind_tool_request,
    dispatch_tool,
    tool_definitions,
)

# Unchanged since bounded_market_tools_v3; v4 changed only lower_band_crossings.
UNCHANGED_SINCE_V3 = {
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
}
SINGLE_ASSET_DIGESTS = UNCHANGED_SINCE_V3 | {
    "lower_band_crossings": (
        "e198511f1cc7c38926c9817d8a682ab30e9450b20c75f54605bd7ea8b2c15ada"
    ),
}
V3_LOWER_BAND_CROSSINGS = (
    "0990d9006a7d9ec3c2e13886b3c81c9f9b1a44cfa009b1c1593f4d2c7faccca0"
)
RELATIVE_STRENGTH_DIGEST = (
    "46a313cda8bdbe314a3d64119ffde3c0aaadeb92169c5f3e0e95a47ab35c55ca"
)
PENDING_RULE = (
    "Crossings whose next 6 candles have not all closed by T, including any "
    "crossing on the candle ending at T, are counted only in pending_count."
)


def digests(benchmarks: tuple[TradingPair, ...]) -> dict[str, str]:
    return {
        t.name: t.identity.digest.removeprefix("sha256:")
        for t in tool_definitions(BTC, benchmarks)
    }


def test_v4_tool_identity_snapshots() -> None:
    assert digests((ETH, SOL)) == SINGLE_ASSET_DIGESTS | {
        "relative_strength": RELATIVE_STRENGTH_DIGEST
    }
    # Without a bound benchmark, relative strength is not offered at all.
    assert digests(()) == SINGLE_ASSET_DIGESTS
    # Only the lower_band_crossings description changed from v3.
    current = digests((ETH, SOL))
    assert current.pop("lower_band_crossings") != V3_LOWER_BAND_CROSSINGS
    assert current == UNCHANGED_SINCE_V3 | {
        "relative_strength": RELATIVE_STRENGTH_DIGEST
    }
    assert TOOL_VERSION == "bounded_market_tools_v4"
    assert {t.identity.version for t in tool_definitions(BTC, ())} == {TOOL_VERSION}


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


@pytest.mark.parametrize("capability", MarketResearchCapability)
def test_every_capability_has_a_fixed_window_description(
    capability: MarketResearchCapability,
) -> None:
    assert set(TOOL_DESCRIPTIONS) == set(MarketResearchCapability)
    suffix = (
        " Completed Coinbase hourly candles visible at T; venue, timeframe, window "
        "and cutoff are runtime-owned."
    )
    definition = next(
        t for t in tool_definitions(BTC, (ETH,)) if t.name == capability.value
    )
    assert definition.description == TOOL_DESCRIPTIONS[capability] + suffix
    assert definition.description.endswith(suffix)
    bars = MarketResearchPolicy().required_bars(capability)
    assert str(bars) in TOOL_DESCRIPTIONS[capability]


def test_lower_band_crossings_states_the_pending_rule() -> None:
    description = TOOL_DESCRIPTIONS[MarketResearchCapability.LOWER_BAND_CROSSINGS]
    assert description.endswith(PENDING_RULE)
    assert "Later crossings are counted as pending." not in description


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
