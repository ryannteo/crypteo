"""Narrow model tool authority and explicit deterministic dispatch."""

from typing import Any

from crypteo.domain.market import TradingPair
from crypteo.domain.market_research import (
    MarketResearchCapability,
    MarketResearchQuery,
    ResearchToolSuccess,
    ResearchToolUnavailable,
)
from crypteo.domain.research_workflow import BENCHMARK_ORDER, ToolRejection
from crypteo.market_research import MarketResearchService
from crypteo.research_model import (
    ContentIdentity,
    ModelOutputError,
    ToolDefinition,
    ToolRequest,
    content_digest,
    parse_json,
    render_json,
)

TOOL_VERSION = "bounded_market_tools_v1"


def tool_definitions(subject: TradingPair) -> tuple[ToolDefinition, ...]:
    tools: list[ToolDefinition] = []
    for capability in MarketResearchCapability:
        relative = capability is MarketResearchCapability.RELATIVE_STRENGTH
        parameters: dict[str, Any] = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "benchmark": {
                    "type": "string",
                    "enum": [p.value for p in BENCHMARK_ORDER if p != subject],
                }
            }
            if relative
            else {},
            "required": ["benchmark"] if relative else [],
        }
        description = (
            f"Inspect deterministic {capability.value} evidence for the bound subject. "
            "Venue, timeframe, windows and historical visibility are runtime-owned."
        )
        digest = content_digest(
            render_json(
                {
                    "name": capability.value,
                    "description": description,
                    "parameters": parameters,
                    "strict": True,
                }
            )
        )
        tools.append(
            ToolDefinition.model_validate(
                dict(
                    name=capability.value,
                    description=description,
                    parameters=parameters,
                    identity=ContentIdentity(version=TOOL_VERSION, digest=digest),
                )
            )
        )
    return tuple(tools)


def bind_tool_request(
    requests: tuple[ToolRequest, ...],
    subject: TradingPair,
) -> MarketResearchQuery | ToolRejection:
    """Validate the sole model-selectable argument before binding the subject."""
    try:
        if len(requests) != 1:
            raise ModelOutputError("exactly one tool request per response is permitted")
        request = requests[0]
        if request.argument_error is not None:
            raise ModelOutputError("invalid Unicode surrogate scalar")
        arguments = parse_json(request.arguments_json)
        if not isinstance(arguments, dict):
            raise ModelOutputError("tool arguments must be a JSON object")
        if request.name not in {c.value for c in MarketResearchCapability}:
            raise ModelOutputError("unknown tool")
        capability = MarketResearchCapability(request.name)
        benchmark: TradingPair | None = None
        if capability is MarketResearchCapability.RELATIVE_STRENGTH:
            permitted = tuple(p.value for p in BENCHMARK_ORDER if p != subject)
            if (
                set(arguments) != {"benchmark"}
                or arguments["benchmark"] not in permitted
            ):
                raise ModelOutputError(
                    "relative_strength requires only a permitted benchmark"
                )
            benchmark = TradingPair(arguments["benchmark"])
        elif arguments:
            raise ModelOutputError("single-asset tools accept no arguments")
        return MarketResearchQuery(
            capability=capability, pair=subject, benchmark=benchmark
        )
    except ModelOutputError as error:
        return ToolRejection(code="INVALID_TOOL_REQUEST", message=str(error))


def dispatch_tool(
    service: MarketResearchService,
    query: MarketResearchQuery,
) -> ResearchToolSuccess | ResearchToolUnavailable:
    return service._research(MarketResearchQuery.model_validate(query))
