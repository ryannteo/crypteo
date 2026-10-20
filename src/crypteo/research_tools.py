"""Narrow model tool authority and explicit deterministic dispatch."""

from collections.abc import Mapping
from types import MappingProxyType
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

TOOL_VERSION = "bounded_market_tools_v4"

_DESCRIPTION_SUFFIX = (
    "Completed Coinbase hourly candles visible at T; venue, timeframe, window and "
    "cutoff are runtime-owned."
)

TOOL_DESCRIPTIONS: Mapping[MarketResearchCapability, str] = MappingProxyType(
    {
        MarketResearchCapability.TREND: (
            "Close, SMA20 and SMA50 of hourly closes in the 50-candle window ending "
            "at T (current candle included), and close/SMA20-1, close/SMA50-1 and "
            "SMA20/SMA50-1."
        ),
        MarketResearchCapability.MOMENTUM: (
            "Close-to-close returns in the 25-candle window ending at T: current 1h, "
            "6h and 24h returns, the previous 6h return (close 6h ago vs 12h ago), "
            "and current minus previous 6h return."
        ),
        MarketResearchCapability.VOLATILITY: (
            "Mean true range (intrabar high/low and previous close) of the 20 "
            "candles ending at T and of the 20 before them (41-candle window), each "
            "divided by its window's ending close, and current/previous. Arithmetic "
            "means, not Wilder ATR."
        ),
        MarketResearchCapability.BOLLINGER_POSITION: (
            "The scanner's Bollinger bands over the 20-candle window of hourly "
            "closes ending at T (current candle included; population standard "
            "deviation, +/-2 sigma): SMA20, bands, unclipped percent_b and band "
            "width as a fraction of SMA20."
        ),
        MarketResearchCapability.LEVELS: (
            "In the 25-candle window ending at T: highest intrabar high and lowest "
            "intrabar low of the 24 candles before the current candle (the candle "
            "ending at T is excluded from both), their close times, and current "
            "close/level-1 for each. Observational levels, not support/resistance "
            "or trade terms."
        ),
        MarketResearchCapability.VOLUME_CONTEXT: (
            "In the 25-candle window ending at T: base-asset volume of the current "
            "candle, mean volume of the 24 candles before it, and their ratio."
        ),
        MarketResearchCapability.CANDLE_STRUCTURE: (
            "Anatomy of the current candle ending at T: OHLC, range, signed body, "
            "upper and lower wicks, their fractions of the range, and close "
            "location in the range; plus the range relative to the mean true range "
            "of the 20 candles before it (22-candle window)."
        ),
        MarketResearchCapability.HIGHER_TIMEFRAME: (
            "4-hour and daily views built from consecutive blocks of hourly candles "
            "ending at T (not calendar-aligned; 600-candle window): per view, SMA20 "
            "of block closes, close vs SMA20, SMA20 change vs five blocks earlier, "
            "5-block return, and the high/low of the 20 blocks before the current "
            "one with distances."
        ),
        MarketResearchCapability.LOWER_BAND_CROSSINGS: (
            "History of this pair's hourly closes crossing below the lower "
            "Bollinger band (scanner rule) in the 720-candle window ending at T. "
            "Only crossings whose next 6 candles all closed by T are sampled: "
            "counts and fractions reaching the trigger's SMA20 intrabar and closing "
            "higher after 6 hours, close-return mean, median and range, and mean "
            "adverse and favorable excursions. Crossings whose next 6 candles have "
            "not all closed by T, including any crossing on the candle ending at T, "
            "are counted only in pending_count."
        ),
        MarketResearchCapability.RELATIVE_STRENGTH: (
            "Subject minus benchmark close-to-close returns over 6h and 24h from "
            "aligned 25-candle windows ending at T. Offered only for benchmarks "
            "with a bound source."
        ),
    }
)


def _validate_benchmarks(
    subject: TradingPair, benchmarks: tuple[TradingPair, ...]
) -> None:
    if subject in benchmarks:
        raise ValueError("benchmarks cannot contain the subject")
    if len(set(benchmarks)) != len(benchmarks):
        raise ValueError("benchmarks cannot repeat a pair")
    if benchmarks != tuple(p for p in BENCHMARK_ORDER if p in benchmarks):
        raise ValueError("benchmarks must follow BENCHMARK_ORDER")


def tool_definitions(
    subject: TradingPair, benchmarks: tuple[TradingPair, ...]
) -> tuple[ToolDefinition, ...]:
    """Offer relative strength only against benchmarks with a bound source."""
    _validate_benchmarks(subject, benchmarks)
    tools: list[ToolDefinition] = []
    for capability in MarketResearchCapability:
        relative = capability is MarketResearchCapability.RELATIVE_STRENGTH
        if relative and not benchmarks:
            continue
        parameters: dict[str, Any] = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "benchmark": {
                    "type": "string",
                    "enum": [p.value for p in benchmarks],
                }
            }
            if relative
            else {},
            "required": ["benchmark"] if relative else [],
        }
        description = f"{TOOL_DESCRIPTIONS[capability]} {_DESCRIPTION_SUFFIX}"
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
    benchmarks: tuple[TradingPair, ...],
) -> MarketResearchQuery | ToolRejection:
    """Validate the sole model-selectable argument before binding the subject."""
    _validate_benchmarks(subject, benchmarks)
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
            if not benchmarks:
                raise ModelOutputError(
                    "relative_strength is not offered without a bound benchmark"
                )
            permitted = tuple(p.value for p in benchmarks)
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
