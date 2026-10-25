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

TOOL_VERSION = "bounded_market_tools_v6"

_DESCRIPTION_SUFFIX = (
    "Completed Coinbase hourly candles visible at T; venue, timeframe, window and "
    "cutoff are runtime-owned. T-kh is the candle closing k hours before T; each "
    "*_candles payload field gives the first and last close time and the count of "
    "the candles it covers."
)

TOOL_DESCRIPTIONS: Mapping[MarketResearchCapability, str] = MappingProxyType(
    {
        MarketResearchCapability.TREND: (
            "Close at T, SMA20 of the closes of candles T-19h..T (sma20_candles) "
            "and SMA50 of candles T-49h..T (sma50_candles), and close/SMA20-1, "
            "close/SMA50-1 and SMA20/SMA50-1."
        ),
        MarketResearchCapability.MOMENTUM: (
            "Close-to-close returns: return_1h (close at T vs T-1h), return_6h (T "
            "vs T-6h), return_24h (T vs T-24h), previous_return_6h (T-6h vs "
            "T-12h), and return_6h minus previous_return_6h."
        ),
        MarketResearchCapability.VOLATILITY: (
            "Mean true range (intrabar high/low and previous close) of candles "
            "T-19h..T (current_candles) and of candles T-39h..T-20h "
            "(previous_candles), each divided by the close of its last candle (T "
            "and T-20h), and current/previous. Arithmetic means, not Wilder ATR."
        ),
        MarketResearchCapability.BOLLINGER_POSITION: (
            "The scanner's Bollinger bands over the closes of candles T-19h..T "
            "(band_candles; population standard deviation, +/-2 sigma): SMA20, "
            "bands, unclipped percent_b of the close at T, and band width as a "
            "fraction of SMA20."
        ),
        MarketResearchCapability.LEVELS: (
            "Highest intrabar high and lowest intrabar low of candles T-24h..T-1h "
            "(level_candles; the candle ending at T is excluded), the close time of "
            "the candle holding each (the most recent on ties), and close at "
            "T/level-1 for each. Observational levels, not support/resistance or "
            "trade terms."
        ),
        MarketResearchCapability.VOLUME_CONTEXT: (
            "Base-asset volume of the candle ending at T, mean volume of candles "
            "T-24h..T-1h (baseline_candles), and their ratio."
        ),
        MarketResearchCapability.CANDLE_STRUCTURE: (
            "Anatomy of the candle ending at T: OHLC, range, signed body, upper and "
            "lower wicks, their fractions of the range, and close_location, the "
            "close's position in the range as a fraction (0 at the low, 1 at the "
            "high), not a price distance; plus the range relative to "
            "baseline_mean_true_range, the mean true range of candles T-20h..T-1h "
            "(baseline_candles). Volatility's current mean true range differs: it "
            "covers candles T-19h..T."
        ),
        MarketResearchCapability.HIGHER_TIMEFRAME: (
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
        MarketResearchCapability.LOWER_BAND_CROSSINGS: (
            "History of this pair's hourly closes crossing below the lower "
            "Bollinger band (scanner rule) in candles T-719h..T (lookback_candles). "
            "Crossings at T-6h or earlier, whose next 6 candles all closed by T, are "
            "sampled: counts and fractions reaching the trigger's SMA20 intrabar and "
            "closing higher after 6 hours, close-return mean, median and range, and "
            "mean adverse and favorable excursions. Crossings at T-5h..T are pending "
            "and not sampled: pending_count counts them, pending_close_times lists "
            "their close times, and current_candle_crossed is true exactly when the "
            "candle ending at T is one of them. unconditional_baseline samples "
            "every candle T-699h..T-6h (start_candles), crossing or not, with the "
            "same 6-candle outcomes: positive-close count and fraction, close-return "
            "mean and median, and mean adverse and favorable excursions; its samples "
            "overlap. Sampled crossings at T-670h or later are classified by two "
            "features of the crossing candle: wide range (high minus low at least "
            "the mean true range of the 20 candles before it) and downtrend (SMA20 "
            "below SMA50 of the closes ending at it). wide_range_crossings, "
            "not_wide_range_crossings, downtrend_crossings and "
            "not_downtrend_crossings give, per group, counts and fractions reaching "
            "the trigger's SMA20 intrabar and closing higher, mean close return, "
            "and mean adverse and favorable excursions; unclassified_count counts "
            "sampled crossings before T-670h. current_wide_range and "
            "current_downtrend give both features for the candle ending at T. "
            "Groups can hold few samples."
        ),
        MarketResearchCapability.RELATIVE_STRENGTH: (
            "Subject minus benchmark close-to-close returns over 6h (close at T vs "
            "T-6h) and 24h (T vs T-24h), from aligned candles of both pairs. Offered "
            "only for benchmarks with a bound source."
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
