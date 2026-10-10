"""Scripted model and deterministic fixtures. No model/network calls."""

import asyncio
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, overload

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel

from crypteo.domain.market import Candle, TradingPair
from crypteo.domain.market_research import MarketSourceBinding
from crypteo.domain.research import Critique, Opportunity, ResearchThesis, TradeDecision
from crypteo.domain.research_journal import ResearchArtifact
from crypteo.domain.research_workflow import (
    InvestigationCompletion,
    OpportunityResearchRequest,
    ResearchRunResult,
    ResearchWorkflowConfig,
)
from crypteo.experiment import CodeIdentity
from crypteo.market_research import MarketResearchService
from crypteo.openai_research import OpenAIResearchModel
from crypteo.research_model import (
    ModelConfiguration,
    ModelReply,
    ModelRequest,
    ProviderIdentity,
    StructuredReply,
    ToolReply,
    ToolRequest,
    render_json,
)
from crypteo.research_persistence import ResearchStore
from crypteo.research_workflow import (
    run_durable_opportunity_research,
    run_opportunity_research,
)
from crypteo.temporal import PointInTimeDataGateway, SimulationClock

T = datetime(2022, 1, 4, tzinfo=UTC)
BTC, ETH, SOL = TradingPair
CODE = CodeIdentity(git_commit="a" * 40, source_digest="sha256:" + "b" * 64)


def opportunity(pair: TradingPair = BTC) -> Opportunity:
    return Opportunity.model_validate(
        dict(
            opportunity_id="opportunity-1",
            venue="COINBASE",
            pair=pair,
            timeframe="1h",
            observed_at=T,
            available_at=T,
            trigger_type="test_trigger_v1",
            trigger_payload={"value": "100.0001"},
            summary="A LONG candidate.",
        )
    )


def candles(
    pair: TradingPair = BTC, *, future: bool = False, delayed: int | None = None
) -> tuple[Candle, ...]:
    return tuple(
        Candle.model_validate(
            dict(
                venue="COINBASE",
                pair=pair,
                timeframe="1h",
                open_time=T - timedelta(hours=60 - i),
                close_time=T - timedelta(hours=59 - i),
                available_at=T + timedelta(hours=1)
                if i == delayed
                else T - timedelta(hours=59 - i),
                open="100.0000000000000000000001",
                close="100.0000000000000000000001",
                high="102",
                low="98",
                volume="0",
            )
        )
        for i in range(61 if future else 60)
    )


def service(
    clock: SimulationClock | None = None,
    *,
    pairs: tuple[TradingPair, ...] = (BTC, ETH),
    future: bool = False,
    delayed: int | None = None,
) -> MarketResearchService:
    return MarketResearchService(
        PointInTimeDataGateway(
            clock or SimulationClock(T),
            tuple(c for p in pairs for c in candles(p, future=future, delayed=delayed)),
        ),
        [
            MarketSourceBinding(pair=p, dataset_version="sha256:" + "c" * 64)
            for p in pairs
        ],
    )


def tool(name: str = "trend", arguments: dict[str, Any] | None = None) -> ToolReply:
    return ToolReply(
        requests=(
            ToolRequest(
                name=name,
                arguments_json=json.dumps(arguments or {}),
                provider_call_id="provider-tool-1",
            ),
        )
    )


def structured(value: BaseModel | dict[str, Any]) -> StructuredReply:
    return StructuredReply(
        content=value.model_dump_json()
        if isinstance(value, BaseModel)
        else json.dumps(value)
    )


def completion(ids: tuple[str, ...] = ()) -> StructuredReply:
    return structured(
        InvestigationCompletion(
            summary="Investigator completed.",
            relevant_evidence_ids=ids,
            remaining_gaps=("No additional domain data.",),
        )
    )


def thesis(ids: tuple[str, ...] = ()) -> ResearchThesis:
    return ResearchThesis(
        summary="Draft thesis.",
        bull_case="Potential support.",
        bear_case="Countercase.",
        supporting_evidence_ids=ids,
        assumptions=("Market regime could persist.",),
    )


def critique() -> Critique:
    return Critique(
        summary="Independent challenge.",
        missing_evidence=("Catalysts unavailable.",),
        unsupported_assumptions=("Regime persistence is uncertain.",),
    )


def assessment(outcome: str, ids: tuple[str, ...]) -> TradeDecision:
    return TradeDecision.model_validate(
        dict(
            outcome=outcome,
            rationale="Research conclusion.",
            confidence=5 if outcome != "ABSTAIN" else None,
            supporting_evidence_ids=ids if outcome == "TRADE" else (),
            counterevidence_ids=ids if outcome == "NO_TRADE" else (),
            key_uncertainties=("Limited research domains.",),
            critic_response="Addressed independent challenge.",
            abstention_reasons=("INSUFFICIENT_EVIDENCE",)
            if outcome == "ABSTAIN"
            else (),
        )
    )


type Step = ModelReply | Callable[[ModelRequest], ModelReply]


class ScriptedModel:
    def __init__(
        self,
        steps: Sequence[Step] | None = None,
        *,
        outcome: str = "TRADE",
        provider: str = "fake",
    ) -> None:
        self.steps = list(steps or ())
        self.outcome, self.provider = outcome, provider
        self.requests: list[ModelRequest] = []

    @property
    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(
            provider=self.provider, adapter_version="scripted_v1", sdk_version="none"
        )

    def prepare(self, request: ModelRequest) -> ModelRequest:
        return ModelRequest.model_validate(
            request.model_copy(
                update={
                    "prepared_input_json": render_json(request),
                }
            )
        )

    async def invoke(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        if self.steps:
            step = self.steps.pop(0)
            return step(request) if callable(step) else step
        data = json.loads(request.messages[-1].content)["research_data"]
        ids = tuple(e["evidence_id"] for e in data["evidence"])
        match request.stage:
            case "investigate":
                return completion(ids)
            case "draft_thesis":
                return structured(thesis(ids))
            case "critique":
                return structured(critique())
            case "final_assessment":
                return structured(assessment(self.outcome, ids))


def run(
    model: ScriptedModel,
    svc: MarketResearchService | None = None,
    request: OpportunityResearchRequest | None = None,
    config: ResearchWorkflowConfig | None = None,
) -> ResearchRunResult:
    return asyncio.run(
        run_opportunity_research(
            request
            or OpportunityResearchRequest(
                research_run_id="run-1", opportunity=opportunity()
            ),
            svc or service(),
            model,
            config or workflow_config(model.provider),
            code=CODE,
        )
    )


def workflow_config(provider: str = "fake") -> ResearchWorkflowConfig:
    return ResearchWorkflowConfig(
        model=ModelConfiguration(provider=provider, model="explicit-test-model")
    )


type Handler = Callable[[httpx.Request], httpx.Response]


@overload
def openai_research(
    handler: Handler, request: OpportunityResearchRequest
) -> ResearchRunResult: ...


@overload
def openai_research(
    handler: Handler, request: OpportunityResearchRequest, store: ResearchStore
) -> ResearchArtifact: ...


def openai_research(
    handler: Handler,
    request: OpportunityResearchRequest,
    store: ResearchStore | None = None,
) -> ResearchRunResult | ResearchArtifact:
    """Run the workflow through the real OpenAI adapter over a mocked transport."""

    async def execute() -> ResearchRunResult | ResearchArtifact:
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client,
            AsyncOpenAI(api_key="test-only", http_client=http_client) as client,
        ):
            model, config = OpenAIResearchModel(client), workflow_config("openai")
            if store is None:
                return await run_opportunity_research(
                    request, service(), model, config, code=CODE
                )
            return await run_durable_opportunity_research(
                request, service(), model, config, store=store, code=CODE
            )

    return asyncio.run(execute())
