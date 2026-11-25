"""The demo pipeline: ingest, plan, evaluate, reproduce, export and render.

Every step is ``crypteo``'s public API or the viewer's own export and render
step; the demo adds only its synthetic market, its scripted model and the
labels. Everything is written beneath one output directory:

``data/``                     synthetic candles: raw, canonical, acquisition
``research.sqlite``           the durable research store
``evaluation/``               the evaluation runner's plan, windows and result
``reproduction/``             the frozen reproduction's evaluation
``viewer/``                   the export spec, the notice, the bundle and site
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from crypteo.coinbase_dataset import CoinbaseCanonicalDataset
from crypteo.domain.evaluation import EvaluationResult, WindowPlan, WindowPlanSettings
from crypteo.domain.execution import ExecutionConfig
from crypteo.domain.replay_research import ReplayResearchRecord
from crypteo.domain.research_workflow import ResearchWorkflowConfig
from crypteo.domain.risk import RiskConfig
from crypteo.domain.trade_terms import TradeTermsConfig
from crypteo.evaluation import plan_windows, reproduce_evaluation, run_evaluation
from crypteo.experiment import CodeIdentity
from crypteo.research_persistence import ResearchStore
from crypteo_demo import LABEL, market
from crypteo_demo.scripted import CONFIGURATION, ScriptedModel
from crypteo_viewer.export import export_bundle
from crypteo_viewer.render import render_site

# A wheel install has no git checkout. All zeros is git's null object ID, which
# names no commit, and an all-zero digest names no source tree.
CODE = CodeIdentity(git_commit="0" * 40, source_digest="sha256:" + "0" * 64)
TRIAL = "DEMO-synthetic-data-scripted-model"
BUNDLE = "DEMO-synthetic-data-scripted-model"
NOTICE = "DEMO-NOTICE.txt"
NOTICE_TEXT = f"""{LABEL}

The candles are a seeded synthetic random walk for BTC-USD, ETH-USD and SOL-USD,
dated January 2000, before these markets existed. They are not Coinbase data,
although they pass through the Coinbase ingestion path and carry its venue name.

Every model reply is fixed text from crypteo_demo's script under provider
"fake": it calls two tools, then assigns TRADE to every BTC-USD candidate,
NO_TRADE to every ETH-USD candidate and ABSTAIN to every SOL-USD candidate.

The code identity is a placeholder, not a commit: an all-zero git commit and an
all-zero source digest. The cost table is empty, because nothing was billed.
"""
WINDOWS = 4


def settings() -> WindowPlanSettings:
    """PLUMBING windows of six hours, from three days into the series."""
    return WindowPlanSettings(
        purpose="PLUMBING",
        planning_start=market.START + timedelta(hours=72),
        limit=WINDOWS,
        initial_cash=Decimal(10000),
        trade_terms=TradeTermsConfig(price_increment=Decimal("0.01")),
        risk=RiskConfig(risk_fraction=Decimal("0.01"), max_holding_bars=6),
        execution=ExecutionConfig(
            entry_fee_rate=Decimal("0.004"),
            exit_fee_rate=Decimal("0.004"),
            adverse_slippage=Decimal("0.0005"),
            price_increment=Decimal("0.01"),
            quantity_increment=Decimal("0.00000001"),
        ),
    )


@dataclass(frozen=True)
class Decision:
    """One window's scripted assessment, as the research store holds it."""

    pair: str
    observed_at: str
    outcome: str
    lean: str | None
    peer_comparison: str | None


@dataclass(frozen=True)
class DemoRun:
    plan: WindowPlan
    result: EvaluationResult
    decisions: tuple[Decision, ...]
    metrics_equal: bool
    candidates_equal: bool
    index: Path


def _decisions(result: EvaluationResult, store: ResearchStore) -> tuple[Decision, ...]:
    decisions = []
    for window in result.comparison.windows:
        candidate = window.candidate
        (record,) = (
            r.payload
            for r in window.research.records
            if isinstance(r.payload, ReplayResearchRecord)
        )
        assessment = store.load_terminal(record.research_run_id).result.assessment
        decisions.append(
            Decision(
                pair=candidate.pair.value,
                observed_at=f"{candidate.observed_at:%Y-%m-%d %H:%M}",
                outcome=candidate.outcome.value,
                lean=None if assessment.lean is None else assessment.lean.value,
                peer_comparison=None
                if assessment.peer_comparison is None
                else assessment.peer_comparison.value,
            )
        )
    return tuple(decisions)


async def _evaluate(
    output: Path,
    datasets: list[CoinbaseCanonicalDataset],
    plan: WindowPlan,
    store: ResearchStore,
) -> tuple[EvaluationResult, EvaluationResult]:
    selected = sum(c.decision == "SELECTED" for c in plan.candidates)
    result = await run_evaluation(
        plan,
        datasets,
        directory=output / "evaluation",
        store=store,
        model=ScriptedModel(),
        configuration=ResearchWorkflowConfig(model=CONFIGURATION),
        trial=TRIAL,
        max_research_runs=selected,
        code=CODE,
    )
    # No model: every window's research replays frozen from the store.
    reproduced = await reproduce_evaluation(result, datasets, store=store, code=CODE)
    return result, reproduced


def _export(output: Path) -> Path:
    viewer = output / "viewer"
    viewer.mkdir()
    (viewer / NOTICE).write_text(NOTICE_TEXT, encoding="utf-8")
    spec = {
        "bundle": BUNDLE,
        "prices": {},
        "trials": [
            {
                "name": "DEMO: synthetic data, scripted model, not an evaluation",
                "evaluations": [
                    {
                        "label": "DEMO evaluation (synthetic data, scripted model)",
                        "evaluation": "../evaluation/evaluation.json",
                        "store": "../research.sqlite",
                    }
                ],
                "attachments": [NOTICE],
            }
        ],
    }
    (viewer / "spec.json").write_text(
        json.dumps(spec, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    export_bundle(viewer / "spec.json", viewer / "bundle", code=CODE)
    render_site(viewer / "bundle", viewer / "site")
    return viewer / "site" / "index.html"


def run_demo(output: Path) -> DemoRun:
    """Run every step into ``output``, which must be new or empty."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    datasets = market.ingest(output / "data")
    plan = plan_windows(datasets, settings())
    store = ResearchStore(output / "research.sqlite")
    result, reproduced = asyncio.run(_evaluate(output, datasets, plan, store))
    (output / "reproduction").mkdir()
    (output / "reproduction" / "evaluation.json").write_bytes(
        reproduced.canonical_bytes()
    )
    decisions = _decisions(result, store)
    index = _export(output)
    return DemoRun(
        plan=plan,
        result=result,
        decisions=decisions,
        metrics_equal=reproduced.comparison.metrics == result.comparison.metrics,
        candidates_equal=[w.candidate for w in reproduced.comparison.windows]
        == [w.candidate for w in result.comparison.windows],
        index=index,
    )
