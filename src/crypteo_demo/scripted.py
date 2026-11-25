"""A scripted research model under provider ``"fake"``: fixed text, not research.

It satisfies ``crypteo``'s observable model boundary without any client. The
first two investigation calls request ``bollinger_position`` then ``trend``;
every later call returns a fixed, valid stage output citing the evidence the
tools collected. The final assessment depends only on the candidate's pair, by
``SCRIPT``. Provider ``"fake"`` has no response codec, so ``observe`` is never
called and nothing is sent anywhere.
"""

import json
from typing import Any

from pydantic import BaseModel

from crypteo.domain.research import (
    AbstentionReason,
    Critique,
    DecisionOutcome,
    EvidenceLean,
    PeerComparison,
    ResearchThesis,
    TradeDecision,
)
from crypteo.domain.research_workflow import InvestigationCompletion
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

MODEL = "crypteo-demo-scripted-model"
ADAPTER = "crypteo_demo_scripted_v1"
CONFIGURATION = ModelConfiguration(provider="fake", model=MODEL)
TOOLS = ("bollinger_position", "trend")
PREFIX = "Scripted demo text, not research."
# The fixed assessment for each pair: outcome, lean, peer comparison.
SCRIPT: dict[str, tuple[DecisionOutcome, EvidenceLean, PeerComparison]] = {
    "BTC-USD": (DecisionOutcome.TRADE, EvidenceLean.FOR, PeerComparison.BETTER),
    "ETH-USD": (DecisionOutcome.NO_TRADE, EvidenceLean.AGAINST, PeerComparison.WORSE),
    "SOL-USD": (
        DecisionOutcome.ABSTAIN,
        EvidenceLean.BALANCED,
        PeerComparison.SIMILAR,
    ),
}


def _text(sentence: str) -> str:
    return f"{PREFIX} {sentence}"


def _assessment(pair: str, ids: tuple[str, ...]) -> TradeDecision:
    outcome, lean, peer = SCRIPT[pair]
    trade = outcome is DecisionOutcome.TRADE
    declined = outcome is DecisionOutcome.NO_TRADE
    return TradeDecision(
        outcome=outcome,
        rationale=_text(f"The demo script assigns {outcome.value} to every {pair}."),
        supporting_evidence_ids=ids if trade else (),
        counterevidence_ids=ids if declined else (),
        confidence=None if outcome is DecisionOutcome.ABSTAIN else 50,
        key_uncertainties=(_text("The candles are synthetic."),),
        critic_response=_text("The script does not weigh the critique."),
        abstention_reasons=(AbstentionReason.CONFLICTING_EVIDENCE,)
        if outcome is DecisionOutcome.ABSTAIN
        else (),
        lean=lean,
        peer_comparison=peer,
    )


def stage_output(stage: str, pair: str, ids: tuple[str, ...]) -> BaseModel:
    """The fixed output for one stage, citing every collected evidence ID."""
    match stage:
        case "investigate":
            return InvestigationCompletion(
                summary=_text("Two deterministic tools were called."),
                relevant_evidence_ids=ids,
                remaining_gaps=(_text("No other research domain is available."),),
            )
        case "draft_thesis":
            return ResearchThesis(
                summary=_text(f"A fixed thesis for a {pair} LONG."),
                bull_case=_text("A close below the lower band may revert."),
                bear_case=_text("The drop may continue."),
                supporting_evidence_ids=ids,
                assumptions=(_text("The synthetic walk has no regime."),),
            )
        case "critique":
            return Critique(
                summary=_text("A fixed challenge to both cases."),
                missing_evidence=(_text("No catalyst data exists for a demo."),),
                unsupported_assumptions=(_text("Reversion is assumed, not shown."),),
            )
        case "final_assessment":
            return _assessment(pair, ids)
    raise ValueError(f"the script has no output for stage {stage}")


class ScriptedModel:
    """An observable model that returns ``SCRIPT``'s fixed replies in order."""

    def __init__(self) -> None:
        self.calls = 0
        self.decisions: list[tuple[str, str]] = []

    @property
    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(
            provider="fake", adapter_version=ADAPTER, sdk_version="none"
        )

    def prepare(self, request: ModelRequest) -> ModelRequest:
        return ModelRequest.model_validate(
            request.model_copy(update={"prepared_input_json": render_json(request)})
        )

    async def invoke(self, request: ModelRequest) -> ModelReply:
        self.calls += 1
        data: dict[str, Any] = json.loads(request.messages[-1].content)["research_data"]
        pair = data["context"]["request"]["opportunity"]["pair"]
        asked = len(data["request_history"])
        if request.stage == "investigate" and asked < len(TOOLS):
            return ToolReply(
                requests=(
                    ToolRequest(
                        name=TOOLS[asked],
                        arguments_json="{}",
                        provider_call_id=f"demo-tool-{asked + 1}",
                    ),
                )
            )
        ids = tuple(item["evidence_id"] for item in data["evidence"])
        output = stage_output(request.stage, pair, ids)
        if isinstance(output, TradeDecision):
            self.decisions.append((pair, output.outcome.value))
        return StructuredReply(content=output.model_dump_json())

    async def invoke_observed(
        self, request: ModelRequest, observe: ResponseObserver
    ) -> ModelReply:
        return await self.invoke(request)
