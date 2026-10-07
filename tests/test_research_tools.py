"""Tool identity snapshots and read-only deterministic service access."""

import pytest
from pydantic import ValidationError
from research_support import BTC, ETH, T, service, tool

from crypteo.domain.market_research import (
    MarketResearchCapability,
    MarketResearchQuery,
    ResearchToolSuccess,
    ResearchToolUnavailable,
)
from crypteo.research_tools import bind_tool_request, dispatch_tool, tool_definitions


def test_v1_tool_identity_snapshots() -> None:
    expected = {
        "trend": "9d73e13f74517c14d51d5b20df71da3fb304f8bfb6df44c2b34edc71746691b5",
        "momentum": "cd98d18e279f4fd77aed0cafb5e286d6f5341e700bb4090f6b9842c4cf8b41ce",
        "volatility": (
            "6709e3b1d5375680cac046e8aee5aa402ca5e269788025066059b7948e37b39f"
        ),
        "bollinger_position": (
            "dea163c1b3f4cefd0cbb3af42777e73bf74c256d87fb1fb83cf3283ffa7da229"
        ),
        "levels": "7f1b416a37b833674a40adae5b001460d68fc5a06187ffcecb8bc609a6e03408",
        "volume_context": (
            "8506f7b56a739fc0be616a9f0d8fcba40440732e11b35e5b88ac13a5290e7165"
        ),
        "relative_strength": (
            "abe019c1c8f28b7a1e5c702778514294be60320d6667e2206b54fc94af8d3707"
        ),
    }
    assert {
        t.name: t.identity.digest.removeprefix("sha256:") for t in tool_definitions(BTC)
    } == expected


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
    svc = service(delayed=59 if unavailable else None)
    relative = capability is MarketResearchCapability.RELATIVE_STRENGTH
    request = tool(capability.value, {"benchmark": ETH.value} if relative else {})
    query = bind_tool_request(request.requests, BTC)
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
        }[capability](BTC)
    )
    actual = dispatch_tool(svc, query)
    assert isinstance(
        actual, ResearchToolUnavailable if unavailable else ResearchToolSuccess
    )
    assert actual.model_dump_json() == expected.model_dump_json()
