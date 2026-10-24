"""Release smoke check over the public API, for wheel and editable installs.

Run as ``python tests/release_smoke.py [--expect-installed]``. Only crypteo's
public API and its runtime dependencies are used; no test module or dev tool is
imported, and no network is reached. Each step asserts its own invariants, then
environment-independent outputs are compared with pinned golden digests.
"""

import argparse
import asyncio
import importlib.metadata
import json
import sys
import sysconfig
import tempfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx2
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient
from pydantic import BaseModel

import crypteo
from crypteo.anthropic_research import AnthropicResearchModel
from crypteo.coinbase import CoinbaseCandleRetriever
from crypteo.coinbase_dataset import CoinbaseHistoricalDataset, compose_coinbase_dataset
from crypteo.domain.execution import ExecutionConfig
from crypteo.domain.market import TradingPair
from crypteo.domain.market_research import MarketSourceBinding
from crypteo.domain.replay import Delivery, ReplayResult
from crypteo.domain.replay_research import (
    FreshResearchSettings,
    FrozenResearchMapping,
    FrozenResearchSettings,
    ReplayResearchRecord,
)
from crypteo.domain.research import (
    Critique,
    DecisionOutcome,
    Opportunity,
    ResearchThesis,
    TradeDecision,
)
from crypteo.domain.research_journal import ResearchArtifact
from crypteo.domain.research_workflow import (
    InvestigationCompletion,
    OpportunityResearchRequest,
    ResearchWorkflowConfig,
)
from crypteo.domain.risk import RiskConfig
from crypteo.domain.trade_terms import TradeTermsConfig
from crypteo.experiment import CodeIdentity, ReplayConfig, current_code_identity
from crypteo.market_research import MarketResearchService
from crypteo.replay import run_scanner_only
from crypteo.research_model import (
    ModelConfiguration,
    ModelReply,
    ModelRequest,
    ProviderIdentity,
    ResponseObserver,
    StructuredReply,
    ToolReply,
    ToolRequest,
    render_json,
)
from crypteo.research_persistence import ResearchStore
from crypteo.research_replay import (
    load_research_replay,
    run_research_replay,
    validate_research_links,
)
from crypteo.research_workflow import run_durable_opportunity_research
from crypteo.temporal import PointInTimeDataGateway, SimulationClock

VERSION = "2.0.0"
CODE = CodeIdentity(git_commit="5" * 40, source_digest="sha256:" + "5" * 64)
T = datetime(2023, 1, 2, tzinfo=UTC)
H = timedelta(hours=1)
BTC = TradingPair.BTC_USD
MODEL = "release-smoke-model"
CLAUDE_RUN_ID = "release-smoke-claude-1"
CHECKOUT_UNAVAILABLE = "source checkout unavailable; supply explicit CodeIdentity"

# Excluded wherever they occur, because they embed interpreter/library versions:
#   python_version, pydantic_version  replay manifests (platform, pydantic)
#   langgraph_version                 research results and run headers
#   sdk_version                       provider identity (anthropic.__version__)
# and identities that hash a value embedding one of them:
#   run_id                  replay manifest identity
#   order_id                hashes run_id
#   research_run_id         replay-derived run IDs hash the fresh replay run_id
#   replay_run_id           frozen origin: the fresh replay run_id
#   artifact_checksum       hashes the artifact (langgraph and SDK versions)
#   ordered_journal_digest  hashes journal events (same versions)
# Each step excludes only the subset that is environment-dependent for it; the
# Claude run's research_run_id is caller-chosen, so it stays in its digest.
REPLAY_ENVIRONMENT = frozenset(
    {"python_version", "pydantic_version", "run_id", "order_id"}
)
RESEARCH_REPLAY_ENVIRONMENT = REPLAY_ENVIRONMENT | {
    "research_run_id",
    "replay_run_id",
    "artifact_checksum",
    "ordered_journal_digest",
}
ARTIFACT_ENVIRONMENT = frozenset(
    {"langgraph_version", "sdk_version", "artifact_checksum", "ordered_journal_digest"}
)
GOLDEN = {
    "scanner_only": (
        "sha256:c53eb965e8472b2a126936167e7cc8a28a121356506ca9ce97c132adeae0e0bd"
    ),
    "frozen_research": (
        "sha256:09dc2523ea97d2ab66a2ff386a932bd03ddde2e218c4260cac44b9a7ee4f57cf"
    ),
    "claude_artifact": (
        "sha256:2f79e2a32b46697dc0a7ce8f2925b4f5b141aa301af3291deeb7b31e04e51c3c"
    ),
}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# Synthetic Coinbase data: 20 flat closes, then a lower-band cross closing at T.
def _row(hour: int, price: int) -> list[int]:
    return [int((T + hour * H).timestamp()), price - 1, price + 1, price, price, 1]


ROWS = [_row(hour, 100) for hour in range(-21, -1)] + [
    _row(-1, 90),
    _row(0, 90),
    _row(1, 95),
    _row(2, 96),
]


class SyntheticCoinbaseTransport:
    def get(self, url: str, params: object) -> bytes:
        return json.dumps(ROWS).encode()


def synthetic_dataset() -> CoinbaseHistoricalDataset:
    start, end = T - 21 * H, T + 3 * H
    return compose_coinbase_dataset(
        pair=BTC,
        raw_responses=CoinbaseCandleRetriever(SyntheticCoinbaseTransport()).retrieve(
            pair=BTC, start=start, end=end, acquisition_started_at=end
        ),
        created_at=T + 100 * H,
        requested_start=start,
        requested_end=end,
    )


def replay_config() -> ReplayConfig:
    return ReplayConfig(
        start=T,
        end=T + 3 * H,
        initial_cash=Decimal(1000),
        assets=(BTC,),
        trade_terms=TradeTermsConfig(price_increment=Decimal("0.1")),
        risk=RiskConfig(risk_fraction=Decimal("0.02"), max_holding_bars=3),
        execution=ExecutionConfig(
            entry_fee_rate=Decimal(0),
            exit_fee_rate=Decimal(0),
            adverse_slippage=Decimal(0),
            price_increment=Decimal("0.1"),
            quantity_increment=Decimal("0.5"),
        ),
    )


def workflow(provider: str) -> ResearchWorkflowConfig:
    return ResearchWorkflowConfig(
        model=ModelConfiguration(provider=provider, model=MODEL)
    )


def evidence_ids(user_content: str) -> list[str]:
    data = json.loads(user_content)["research_data"]
    return [item["evidence_id"] for item in data["evidence"]]


def stage_output(stage: str, ids: list[str]) -> str:
    """Deterministic structured output, rendered with every field present."""
    evidence = tuple(ids)
    check(len(evidence) == 1, f"expected one evidence record, got {len(evidence)}")
    output: BaseModel
    match stage:
        case "investigate":
            output = InvestigationCompletion(
                summary="Investigation completed.",
                relevant_evidence_ids=evidence,
                remaining_gaps=("No non-market research domains.",),
            )
        case "draft_thesis":
            output = ResearchThesis(
                summary="Draft thesis.",
                bull_case="Potential mean reversion.",
                bear_case="Breakdown could continue.",
                supporting_evidence_ids=evidence,
                assumptions=("The market regime persists.",),
            )
        case "critique":
            output = Critique(
                summary="Independent challenge.",
                missing_evidence=("Catalysts are unavailable.",),
                unsupported_assumptions=("Regime persistence is uncertain.",),
            )
        case "final_assessment":
            output = TradeDecision(
                outcome=DecisionOutcome.TRADE,
                rationale="Research supports further consideration.",
                confidence=5,
                supporting_evidence_ids=evidence,
                key_uncertainties=("Limited research domains.",),
                critic_response="The critique's uncertainty is acknowledged.",
            )
        case _:
            raise AssertionError(f"unexpected stage {stage}")
    return output.model_dump_json()


class ScriptedFakeModel:
    """Observable deterministic fake; never calls ``observe`` (provider "fake")."""

    def __init__(self) -> None:
        self.calls = 0

    @property
    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(
            provider="fake", adapter_version="release_smoke_v1", sdk_version="none"
        )

    def prepare(self, request: ModelRequest) -> ModelRequest:
        return ModelRequest.model_validate(
            request.model_copy(update={"prepared_input_json": render_json(request)})
        )

    async def invoke(self, request: ModelRequest) -> ModelReply:
        self.calls += 1
        content = request.messages[-1].content
        if (
            request.stage == "investigate"
            and not json.loads(content)["research_data"]["request_history"]
        ):
            return ToolReply(
                requests=(
                    ToolRequest(
                        name="bollinger_position",
                        arguments_json="{}",
                        provider_call_id="fake-tool-1",
                    ),
                )
            )
        return StructuredReply(
            content=stage_output(request.stage, evidence_ids(content))
        )

    async def invoke_observed(
        self, request: ModelRequest, observe: ResponseObserver
    ) -> ModelReply:
        return await self.invoke(request)


def claude_handler(
    bodies: list[dict[str, Any]],
) -> Callable[[httpx2.Request], httpx2.Response]:
    """Mocked Messages API: one tool call, then each structured stage output."""

    def handle(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        bodies.append(body)
        message = {
            "id": f"msg-smoke-{len(bodies)}",
            "type": "message",
            "role": "assistant",
            "model": body["model"],
            "stop_sequence": None,
            "usage": {"input_tokens": 8, "output_tokens": 12},
        }
        if len(bodies) == 1:
            message["content"] = [
                {
                    "type": "tool_use",
                    "id": "toolu-smoke-1",
                    "name": "bollinger_position",
                    "input": {},
                }
            ]
            message["stop_reason"] = "tool_use"
        else:
            stage = (
                "investigate"
                if "tools" in body
                else ("draft_thesis", "critique", "final_assessment")[len(bodies) - 3]
            )
            ids = evidence_ids(body["messages"][-1]["content"])
            message["content"] = [{"type": "text", "text": stage_output(stage, ids)}]
            message["stop_reason"] = "end_turn"
        return httpx2.Response(
            200, json=message, headers={"request-id": f"req-smoke-{len(bodies)}"}
        )

    return handle


def environment_independent(value: Any, excluded: frozenset[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: environment_independent(item, excluded)
            for key, item in value.items()
            if key not in excluded
        }
    if isinstance(value, list):
        return [environment_independent(item, excluded) for item in value]
    return value


def digest(encoded: bytes | str, excluded: frozenset[str]) -> str:
    projected = environment_independent(json.loads(encoded), excluded)
    canonical = json.dumps(projected, sort_keys=True, separators=(",", ":"))
    return "sha256:" + sha256(canonical.encode()).hexdigest()


def financial(result: ReplayResult) -> object:
    """Financial records, portfolio and metrics, minus manifest-bound order IDs."""
    research_kinds = {"SCAN", "DELIVERY", "RESEARCH"}
    encoded = json.loads(result.canonical_bytes())
    return environment_independent(
        {
            "records": [
                r for r in encoded["records"] if r["kind"] not in research_kinds
            ],
            "final_portfolio": encoded["final_portfolio"],
            "metrics": encoded["metrics"],
        },
        frozenset({"order_id"}),
    )


def research_records(result: ReplayResult) -> list[ReplayResearchRecord]:
    return [
        r.payload for r in result.records if isinstance(r.payload, ReplayResearchRecord)
    ]


def check_installation(expect_installed: bool) -> None:
    check(
        importlib.metadata.version("crypteo") == VERSION,
        f"installed crypteo distribution is not {VERSION}",
    )
    if not expect_installed:
        return
    module = Path(crypteo.__file__).resolve()
    site_packages = Path(sysconfig.get_paths()["purelib"]).resolve()
    checkout = Path(__file__).resolve().parents[1]
    check(module.is_relative_to(site_packages), f"crypteo not installed: {module}")
    check(
        not module.is_relative_to(checkout), f"crypteo loaded from checkout: {module}"
    )
    try:
        current_code_identity()
    except ValueError as error:
        check(str(error) == CHECKOUT_UNAVAILABLE, f"unexpected error: {error}")
    else:
        raise AssertionError("installed package derived a checkout code identity")
    print(f"installed: {module}")


def scanner_only() -> tuple[ReplayResult, Opportunity]:
    first = run_scanner_only(replay_config(), [synthetic_dataset()], code=CODE)
    second = run_scanner_only(replay_config(), [synthetic_dataset()], code=CODE)
    check(
        first.canonical_bytes() == second.canonical_bytes(),
        "scanner-only replay is not byte-stable",
    )
    check(first.manifest.code == CODE, "scanner-only replay ignored explicit code")
    admitted = [
        r.payload.opportunity
        for r in first.records
        if isinstance(r.payload, Delivery) and r.payload.disposition == "ADMITTED"
    ]
    check(len(admitted) == 1, "expected exactly one admitted opportunity")
    check(len(first.final_portfolio.closed_trades) == 1, "expected one closed trade")
    return first, admitted[0]


async def fresh_research(store: ResearchStore) -> ReplayResult:
    model = ScriptedFakeModel()
    result = await run_research_replay(
        replay_config(),
        [synthetic_dataset()],
        research=FreshResearchSettings(
            configuration=workflow("fake"),
            trial_id="release-smoke",
            max_research_runs=1,
        ),
        store=store,
        model=model,
        code=CODE,
    )
    records = research_records(result)
    check(len(records) == 1, "expected one fresh research record")
    check(records[0].outcome == "TRADE", "fresh research did not conclude TRADE")
    check(model.calls == 5, f"expected five fake model calls, got {model.calls}")
    artifact = store.load_terminal(records[0].research_run_id)
    check(
        artifact.artifact_checksum == records[0].artifact_checksum,
        "fresh record does not link its stored artifact",
    )
    check(
        load_research_replay(result.canonical_bytes(), store=store) == result,
        "fresh result does not reload through load_research_replay",
    )
    check(
        validate_research_links(result, store=store) == result,
        "fresh result fails validate_research_links",
    )
    return result


async def frozen_research(store: ResearchStore, fresh: ReplayResult) -> ReplayResult:
    settings = FrozenResearchSettings(
        configuration=workflow("fake"),
        mapping=tuple(
            FrozenResearchMapping(
                opportunity_id=r.opportunity_id,
                research_run_id=r.research_run_id,
                artifact_checksum=r.artifact_checksum,
                context_digest=r.context_digest,
                origin=r.origin,
            )
            for r in research_records(fresh)
        ),
    )
    runs = [
        await run_research_replay(
            replay_config(),
            [synthetic_dataset()],
            research=settings,
            store=store,
            code=CODE,
        )
        for _ in range(2)
    ]
    first, second = runs
    check(
        first.canonical_bytes() == second.canonical_bytes(),
        "frozen research replay is not byte-stable",
    )
    check(financial(first) == financial(fresh), "frozen financials differ from fresh")
    check(
        first.research_metrics == fresh.research_metrics,
        "frozen research metrics differ from fresh",
    )
    check(
        research_records(first) == research_records(fresh),
        "frozen research outcomes differ from fresh",
    )
    check(
        load_research_replay(first.canonical_bytes(), store=store) == first,
        "frozen result does not reload through load_research_replay",
    )
    return first


async def claude_research(
    store: ResearchStore, opportunity: Opportunity
) -> ResearchArtifact:
    dataset = synthetic_dataset()
    service = MarketResearchService(
        PointInTimeDataGateway(SimulationClock(T), dataset.candles),
        [
            MarketSourceBinding(
                pair=BTC,
                dataset_version=dataset.manifest.dataset_version,
                acquisition_id=dataset.acquisition.acquisition_id,
            )
        ],
    )
    bodies: list[dict[str, Any]] = []
    transport = httpx2.MockTransport(claude_handler(bodies))
    async with AsyncAnthropic(
        api_key="release-smoke-not-a-secret",
        http_client=DefaultAsyncHttpxClient(transport=transport),
    ) as client:
        artifact = await run_durable_opportunity_research(
            OpportunityResearchRequest(
                research_run_id=CLAUDE_RUN_ID, opportunity=opportunity
            ),
            service,
            AnthropicResearchModel(client),
            workflow("anthropic"),
            store=store,
            code=CODE,
        )
    result = artifact.result
    check(len(bodies) == 5, f"expected five Claude requests, got {len(bodies)}")
    check(result.status == "COMPLETED", f"Claude research ended {result.status}")
    check(result.assessment.outcome == "TRADE", "Claude research did not TRADE")
    check(result.provider.provider == "anthropic", "artifact provider is not Claude")
    check(store.load_terminal(CLAUDE_RUN_ID) == artifact, "Claude artifact reload")
    check(
        ResearchStore(store.path).load_terminal(CLAUDE_RUN_ID) == artifact,
        "Claude artifact reload through a reopened store",
    )
    return artifact


async def research_steps(
    directory: Path, opportunity: Opportunity
) -> tuple[ReplayResult, ResearchArtifact]:
    replay_store = ResearchStore(directory / "replay.sqlite")
    fresh = await fresh_research(replay_store)
    print("fresh research replay: ok")
    frozen = await frozen_research(replay_store, fresh)
    print("frozen research replay: ok")
    artifact = await claude_research(
        ResearchStore(directory / "claude.sqlite"), opportunity
    )
    print("durable Claude research: ok")
    return frozen, artifact


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expect-installed", action="store_true")
    arguments = parser.parse_args(argv)
    check_installation(arguments.expect_installed)
    print(f"crypteo {VERSION} on Python {sys.version.split()[0]}")
    baseline, opportunity = scanner_only()
    print("scanner-only replay: ok")
    with tempfile.TemporaryDirectory() as directory:
        frozen, artifact = asyncio.run(research_steps(Path(directory), opportunity))
    digests = {
        "scanner_only": digest(baseline.canonical_bytes(), REPLAY_ENVIRONMENT),
        "frozen_research": digest(
            frozen.canonical_bytes(), RESEARCH_REPLAY_ENVIRONMENT
        ),
        "claude_artifact": digest(render_json(artifact), ARTIFACT_ENVIRONMENT),
    }
    for name, value in digests.items():
        print(f"{name}: {value}")
    mismatched = [name for name, value in digests.items() if GOLDEN[name] != value]
    check(not mismatched, f"golden digest mismatch: {', '.join(mismatched)}")
    print("release smoke: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
