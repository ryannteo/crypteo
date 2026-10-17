"""Paired baseline/research comparison over real replay and durable research."""

import asyncio
import platform
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple

import pydantic
import pytest
from test_replay import BTC, CODE, ETH, SOL, D, H, T, bar, config, dataset, payloads
from test_research_replay import WORKFLOW, ReplayModel, execute, frozen

import crypteo.experiment as experiment
import crypteo.research_replay as research_replay
from crypteo.coinbase_dataset import CoinbaseCanonicalDataset
from crypteo.domain.evaluation import (
    CandidateComparison,
    ClosedOutcomeGroup,
    ComparisonMetrics,
    FinancialOutcome,
    ResearchComparison,
    WindowComparison,
)
from crypteo.domain.market import Timeframe, TradingPair, Venue
from crypteo.domain.replay import Delivery, ReplayResult
from crypteo.domain.replay_research import FreshResearchSettings
from crypteo.domain.research import Opportunity
from crypteo.domain.research_workflow import ResearchWorkflowConfig
from crypteo.domain.trade_terms import TradeTermsRejection
from crypteo.evaluation import (
    compare_replays,
    comparison_identity,
    comparison_metrics,
    load_comparison,
)
from crypteo.experiment import CodeIdentity, ReplayConfig
from crypteo.market_research import MarketResearchService
from crypteo.replay import run_scanner_only
from crypteo.research_journal import ResearchStorageError
from crypteo.research_model import ModelConfiguration
from crypteo.research_persistence import ResearchStore
from crypteo.research_replay import run_research_replay
from crypteo.scanner import OpportunityScanner
from crypteo.temporal import PointInTimeDataGateway
from crypteo.trade_terms import TradeTermsService

OTHER_CODE = CodeIdentity(git_commit="c" * 40, source_digest="sha256:" + "d" * 64)


class Window(NamedTuple):
    cfg: ReplayConfig
    datasets: list[CoinbaseCanonicalDataset]
    baseline: ReplayResult
    research: ReplayResult


def inputs(
    pair: TradingPair, offset: int, prices: tuple[str, ...], hours: int = 3
) -> tuple[ReplayConfig, list[CoinbaseCanonicalDataset]]:
    """One lower-band cross observed at T+offset, then the given hourly closes."""
    candles = (
        tuple(bar(i + offset) for i in range(-21, -1))
        + (bar(offset - 1, "90"),)
        + tuple(bar(offset + i, price) for i, price in enumerate(prices))
    )
    cfg = config(start=T + offset * H, end=T + (offset + hours) * H, assets=(pair,))
    return cfg, [dataset(candles, pair=pair)]


def research_run(
    store: ResearchStore,
    cfg: ReplayConfig,
    datasets: list[CoinbaseCanonicalDataset],
    outcome: str = "TRADE",
    *,
    code: CodeIdentity = CODE,
    configuration: ResearchWorkflowConfig = WORKFLOW,
) -> ReplayResult:
    return asyncio.run(
        run_research_replay(
            cfg,
            datasets,
            research=FreshResearchSettings(
                configuration=configuration, trial_id="trial-1", max_research_runs=3
            ),
            store=store,
            model=ReplayModel({p.value: outcome for p in TradingPair}),
            code=code,
        )
    )


def window(
    store: ResearchStore,
    pair: TradingPair,
    offset: int,
    prices: tuple[str, ...],
    outcome: str = "TRADE",
    *,
    hours: int = 3,
    code: CodeIdentity = CODE,
    configuration: ResearchWorkflowConfig = WORKFLOW,
    **changes: Any,
) -> Window:
    cfg, datasets = inputs(pair, offset, prices, hours)
    cfg = ReplayConfig.model_validate(cfg.model_dump() | changes)
    return Window(
        cfg,
        datasets,
        run_scanner_only(cfg, datasets, code=code),
        research_run(
            store, cfg, datasets, outcome, code=code, configuration=configuration
        ),
    )


def comparison_of(windows: tuple[WindowComparison, ...]) -> ResearchComparison:
    return ResearchComparison(
        windows=windows,
        metrics=comparison_metrics(tuple(w.candidate for w in windows)),
        comparison_id=comparison_identity(windows),
    )


def group(
    pnl: str, wins: int, losses: int, breakevens: int, rate: Decimal | None
) -> ClosedOutcomeGroup:
    return ClosedOutcomeGroup(
        net_pnl=D(pnl),
        wins=wins,
        losses=losses,
        breakevens=breakevens,
        win_rate=rate,
    )


def closed(reason: str, pnl: str) -> FinancialOutcome:
    return FinancialOutcome.model_validate(
        {"status": "CLOSED", "exit_reason": reason, "net_pnl": D(pnl)}
    )


class Scenario(NamedTuple):
    store: ResearchStore
    windows: list[Window]
    comparison: ResearchComparison


@pytest.fixture(scope="module")
def scenario(tmp_path_factory: pytest.TempPathFactory) -> Scenario:
    store = ResearchStore(tmp_path_factory.mktemp("evaluation") / "r.sqlite")
    windows = [
        window(store, BTC, 0, ("90", "95", "96")),  # taken TIME win
        window(store, ETH, 0, ("90", "85", "84")),  # taken STOP loss
        window(store, SOL, 0, ("90", "95", "120"), "NO_TRADE"),  # declined win
        window(store, BTC, 5, ("90", "88", "88"), "ABSTAIN"),  # declined loss
        window(store, ETH, 5, ("90", "90", "90")),  # taken breakeven
        window(store, SOL, 5, ("90", "95"), "NO_TRADE", hours=2),  # open at end
        window(store, BTC, 10, ("90",)),  # taken holding data gap
    ]
    shuffled = [windows[i] for i in (6, 3, 0, 5, 2, 4, 1)]
    comparison = compare_replays(
        [(w.baseline, w.research) for w in shuffled], store=store
    )
    return Scenario(store, windows, comparison)


@pytest.mark.parametrize("outcome", ["TRADE", "NO_TRADE", "ABSTAIN"])
def test_single_window_candidate_and_metrics(tmp_path: Path, outcome: str) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    item = window(store, BTC, 0, ("90", "95", "96"), outcome)
    comparison = compare_replays([(item.baseline, item.research)], store=store)
    (only,) = comparison.windows
    opportunity = payloads(item.baseline, Delivery)[0].opportunity
    baseline = closed("TIME", "24")
    traded = outcome == "TRADE"
    assert only.candidate == CandidateComparison.model_validate(
        {
            "opportunity_id": opportunity.opportunity_id,
            "pair": BTC,
            "observed_at": T,
            "outcome": outcome,
            "confidence": None if outcome == "ABSTAIN" else 5,
            "baseline": baseline,
            "research": baseline if traded else {"status": "NOT_SELECTED"},
        }
    )
    assert comparison.metrics == ComparisonMetrics(
        window_count=1,
        trade_count=int(traded),
        no_trade_count=int(outcome == "NO_TRADE"),
        abstain_count=int(outcome == "ABSTAIN"),
        baseline_closed_count=1,
        research_closed_count=int(traded),
        baseline_realized_net_pnl=D(24),
        research_realized_net_pnl=D(24 if traded else 0),
        taken=group("24", 1, 0, 0, D(1)) if traded else group("0", 0, 0, 0, None),
        declined=group("0", 0, 0, 0, None) if traded else group("24", 1, 0, 0, D(1)),
        unresolved_count=0,
    )
    assert only.baseline == item.baseline and only.research == item.research
    assert comparison.comparison_id == experiment.identity(
        {
            "purpose": "research_comparison_v1",
            "pairs": [[item.baseline.run_id, item.research.run_id]],
        }
    )


@pytest.mark.parametrize(
    "case,status",
    [
        ("terms", "TERMS_REJECTED"),
        ("risk", "RISK_REJECTED"),
        ("entry", "ENTRY_REJECTED"),
    ],
)
def test_trade_window_with_rejected_baseline(
    tmp_path: Path, case: str, status: str
) -> None:
    # Cases from test_research_replay.test_trade_rejections_are_distinct.
    cfg = config(end=T + H)
    candles = tuple(bar(i) for i in range(-21, -1)) + (bar(-1, "90"),)
    if case == "terms":
        candles = tuple(
            c.model_copy(update={"high": D(200), "low": D(1)}) for c in candles
        )
    elif case == "risk":
        cfg = config(
            end=T + H,
            execution=cfg.execution.model_copy(update={"entry_fee_rate": D("0.2")}),
        )
    datasets = [dataset(candles)]
    store = ResearchStore(tmp_path / "r.sqlite")
    comparison = compare_replays(
        [
            (
                run_scanner_only(cfg, datasets, code=CODE),
                research_run(store, cfg, datasets),
            )
        ],
        store=store,
    )
    candidate = comparison.windows[0].candidate
    assert (
        candidate.baseline
        == candidate.research
        == FinancialOutcome.model_validate({"status": status})
    )
    metrics = comparison.metrics
    assert metrics.trade_count == 1
    assert metrics.baseline_closed_count == metrics.research_closed_count == 0
    assert metrics.unresolved_count == 0
    assert metrics.taken == metrics.declined == group("0", 0, 0, 0, None)


def test_multi_window_metrics_and_pnl_identities(scenario: Scenario) -> None:
    comparison = scenario.comparison
    # compare_replays canonicalizes shuffled input: observed_at, then asset priority.
    assert [(w.baseline, w.research) for w in comparison.windows] == [
        (w.baseline, w.research) for w in scenario.windows
    ]
    assert [w.candidate.pair for w in comparison.windows] == [
        BTC,
        ETH,
        SOL,
        BTC,
        ETH,
        SOL,
        BTC,
    ]
    assert [
        (w.candidate.outcome, w.candidate.baseline, w.candidate.research)
        for w in comparison.windows
    ] == [
        ("TRADE", closed("TIME", "24"), closed("TIME", "24")),
        ("TRADE", closed("STOP", "-20"), closed("STOP", "-20")),
        ("NO_TRADE", closed("TARGET", "120"), FinancialOutcome(status="NOT_SELECTED")),
        ("ABSTAIN", closed("TIME", "-8"), FinancialOutcome(status="NOT_SELECTED")),
        ("TRADE", closed("TIME", "0"), closed("TIME", "0")),
        (
            "NO_TRADE",
            FinancialOutcome(status="OPEN_AT_END"),
            FinancialOutcome(status="NOT_SELECTED"),
        ),
        (
            "TRADE",
            FinancialOutcome(status="DATA_GAP"),
            FinancialOutcome(status="DATA_GAP"),
        ),
    ]
    metrics = comparison.metrics
    third = D("0.33333333333333333333333333333333333333333333333333")
    assert metrics == ComparisonMetrics(
        window_count=7,
        trade_count=4,
        no_trade_count=2,
        abstain_count=1,
        baseline_closed_count=5,
        research_closed_count=3,
        baseline_realized_net_pnl=D(116),
        research_realized_net_pnl=D(4),
        taken=group("4", 1, 1, 1, third),
        declined=group("112", 1, 1, 0, D("0.5")),
        unresolved_count=2,
    )
    assert metrics.research_realized_net_pnl == metrics.taken.net_pnl
    assert (
        metrics.baseline_realized_net_pnl
        == metrics.taken.net_pnl + metrics.declined.net_pnl
    )
    assert metrics.baseline_realized_net_pnl == sum(
        (w.baseline.metrics.realized_net_pnl for w in comparison.windows), D(0)
    )


def test_comparison_performs_no_research_or_market_reads(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("comparison attempted inference or market reads")

    monkeypatch.setattr(research_replay, "run_durable_opportunity_research", forbidden)
    monkeypatch.setattr(MarketResearchService, "__init__", forbidden)
    monkeypatch.setattr(PointInTimeDataGateway, "read_candles", forbidden)
    monkeypatch.setattr(ReplayModel, "invoke_observed", forbidden)
    pairs = [(w.baseline, w.research) for w in scenario.windows]
    assert compare_replays(pairs, store=scenario.store) == scenario.comparison
    encoded = scenario.comparison.canonical_bytes()
    assert load_comparison(encoded, store=scenario.store) == scenario.comparison


def test_frozen_research_comparison_matches_fresh(scenario: Scenario) -> None:
    store = scenario.store
    pairs = [
        (
            item.baseline,
            execute(
                store,
                settings=frozen(item.research),
                cfg=item.cfg,
                datasets=item.datasets,
            ),
        )
        for item in scenario.windows
    ]
    fresh, reproduced = scenario.comparison, compare_replays(pairs, store=store)
    assert reproduced.metrics == fresh.metrics
    assert [w.candidate for w in reproduced.windows] == [
        w.candidate for w in fresh.windows
    ]
    assert [w.baseline for w in reproduced.windows] == [
        w.baseline for w in fresh.windows
    ]
    assert all(
        a.research.run_id != b.research.run_id
        for a, b in zip(reproduced.windows, fresh.windows, strict=True)
    )
    assert reproduced.comparison_id != fresh.comparison_id


@pytest.mark.parametrize("pair", ["swapped", "baselines", "researches"])
def test_rejects_swapped_or_same_mode_pairs(scenario: Scenario, pair: str) -> None:
    first = scenario.comparison.windows[0]
    baseline, research = {
        "swapped": (first.research, first.baseline),
        "baselines": (first.baseline, first.baseline),
        "researches": (first.research, first.research),
    }[pair]
    with pytest.raises(ValueError, match="SCANNER_ONLY baseline and a RESEARCH"):
        compare_replays([(baseline, research)], store=scenario.store)
    with pytest.raises(ValueError, match="SCANNER_ONLY baseline and a RESEARCH"):
        WindowComparison(
            baseline=baseline, research=research, candidate=first.candidate
        )


@pytest.mark.parametrize(
    "change", ["initial_cash", "datasets", "code", "python", "pydantic"]
)
def test_rejects_differing_deterministic_inputs(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    item = scenario.windows[0]
    cfg, datasets, code = item.cfg, item.datasets, CODE
    if change == "initial_cash":
        cfg = ReplayConfig.model_validate(cfg.model_dump() | {"initial_cash": D(2000)})
    elif change == "datasets":
        datasets = inputs(BTC, 0, ("90", "95", "97"))[1]
    elif change == "code":
        code = OTHER_CODE
    elif change == "python":
        monkeypatch.setattr(platform, "python_version", lambda: "3.99.0")
    else:
        monkeypatch.setattr(pydantic, "__version__", "0.0.0")
    baseline = run_scanner_only(cfg, datasets, code=code)
    monkeypatch.undo()
    with pytest.raises(ValueError, match="share code, versions, config and data"):
        compare_replays([(baseline, item.research)], store=scenario.store)


def test_rejects_altered_scans_and_deliveries(
    scenario: Scenario, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = scenario.windows[0]
    original = OpportunityScanner.scan

    def scan(self: OpportunityScanner, *, pair: TradingPair) -> tuple[Opportunity, ...]:
        found = original(self, pair=pair)
        if self._gateway.now == T + H:
            stale = found[0].model_copy(
                update={
                    "opportunity_id": "sha256:" + "0" * 64,
                    "observed_at": T - 5 * H,
                    "available_at": T - 5 * H,
                }
            )
            return (*found, stale)
        return found

    monkeypatch.setattr(OpportunityScanner, "scan", scan)
    baseline = run_scanner_only(item.cfg, item.datasets, code=CODE)
    monkeypatch.undo()
    assert "NOT_CURRENT" in [d.disposition for d in payloads(baseline, Delivery)]
    with pytest.raises(ValueError, match="identical scans and deliveries"):
        compare_replays([(baseline, item.research)], store=scenario.store)


@pytest.mark.parametrize("admitted", [0, 2])
def test_rejects_zero_or_several_admitted_candidates(
    tmp_path: Path, admitted: int
) -> None:
    if admitted:
        cfg = config(assets=(BTC, ETH), end=T + H)
        datasets = [dataset(pair=p) for p in (BTC, ETH)]
    else:
        cfg = config(end=T + H)
        datasets = [dataset(tuple(bar(i) for i in range(-21, 1)))]
    store = ResearchStore(tmp_path / "r.sqlite")
    research = research_run(store, cfg, datasets)
    baseline = run_scanner_only(cfg, datasets, code=CODE)
    with pytest.raises(ValueError, match=f"exactly one candidate; found {admitted}"):
        compare_replays([(baseline, research)], store=store)


def test_rejects_research_trade_differing_from_baseline_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A self-consistent research result whose terms differ from the baseline's.
    cfg, datasets = inputs(BTC, 0, ("90", "95", "96"))
    baseline = run_scanner_only(cfg, datasets, code=CODE)

    def generate(self: TradeTermsService, *, pair: TradingPair) -> Any:
        return TradeTermsRejection(
            venue=Venue.COINBASE,
            pair=pair,
            timeframe=Timeframe.ONE_HOUR,
            decision_at=T,
            config=cfg.trade_terms,
            reason="HISTORY_UNAVAILABLE",
        )

    monkeypatch.setattr(TradeTermsService, "generate", generate)
    store = ResearchStore(tmp_path / "r.sqlite")
    research = research_run(store, cfg, datasets)
    monkeypatch.undo()
    with pytest.raises(ValueError, match="reproduce the baseline financial chain"):
        compare_replays([(baseline, research)], store=store)


@pytest.mark.parametrize(
    "changes",
    [
        {"confidence": 6},
        {"outcome": "NO_TRADE", "research": {"status": "NOT_SELECTED"}},
        {"observed_at": T + H},
        {"pair": ETH},
        {
            "baseline": {"status": "CLOSED", "exit_reason": "TIME", "net_pnl": "25"},
            "research": {"status": "CLOSED", "exit_reason": "TIME", "net_pnl": "25"},
        },
    ],
)
def test_rejects_tampered_candidate_comparison(
    scenario: Scenario, changes: dict[str, Any]
) -> None:
    first = scenario.comparison.windows[0]
    data = first.model_dump()
    data["candidate"] = data["candidate"] | changes
    with pytest.raises(ValueError, match="candidate comparison must match"):
        WindowComparison.model_validate(data)


def test_rejects_duplicate_opportunity_or_run_ids(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    long = window(store, BTC, 0, ("90", "95", "96"))
    short = window(store, BTC, 0, ("90", "95", "96"), hours=2)
    pairs = [(item.baseline, item.research) for item in (long, short)]
    with pytest.raises(ValueError, match="unique opportunity IDs"):
        compare_replays(pairs, store=store)
    single = compare_replays(pairs[:1], store=store).windows[0]
    with pytest.raises(ValueError, match="unique baseline run IDs"):
        comparison_of((single, single))


@pytest.mark.parametrize("change", ["code", "workflow", "initial_cash", "risk"])
def test_rejects_mixed_policy_code_or_economics(
    scenario: Scenario, tmp_path: Path, change: str
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    choices: dict[str, dict[str, Any]] = {
        "code": {"code": OTHER_CODE},
        "workflow": {
            "configuration": ResearchWorkflowConfig(
                model=ModelConfiguration(provider="fake", model="other-model")
            )
        },
        "initial_cash": {"initial_cash": D(2000)},
        "risk": {
            "risk": scenario.windows[0].cfg.risk.model_copy(
                update={"risk_fraction": D("0.01")}
            )
        },
    }
    other = window(store, BTC, 20, ("90", "95", "96"), **choices[change])
    with pytest.raises(ValueError, match="windows must share"):
        compare_replays(
            [
                (scenario.windows[0].baseline, scenario.windows[0].research),
                (other.baseline, other.research),
            ],
            store=store,
        )


def test_rejects_noncanonical_window_order(scenario: Scenario) -> None:
    windows = scenario.comparison.windows
    with pytest.raises(ValueError, match="windows must be ordered"):
        comparison_of((windows[1], windows[0]))
    assert comparison_of(windows[:2]).windows == windows[:2]


@pytest.mark.parametrize(
    "field,value",
    [
        ("window_count", 8),
        ("trade_count", 3),
        ("abstain_count", 0),
        ("research_closed_count", 5),
        ("baseline_realized_net_pnl", "117"),
        ("research_realized_net_pnl", "116"),
        ("unresolved_count", 0),
        (
            "taken",
            {
                "net_pnl": "4",
                "wins": 1,
                "losses": 1,
                "breakevens": 1,
                "win_rate": "0.3",
            },
        ),
        (
            "declined",
            {
                "net_pnl": "112",
                "wins": 2,
                "losses": 0,
                "breakevens": 0,
                "win_rate": "1",
            },
        ),
    ],
)
def test_rejects_tampered_metrics(scenario: Scenario, field: str, value: Any) -> None:
    data = scenario.comparison.model_dump()
    data["metrics"] = data["metrics"] | {field: value}
    with pytest.raises(ValueError, match="metrics must match"):
        ResearchComparison.model_validate(data)


def test_rejects_tampered_comparison_id(scenario: Scenario) -> None:
    data = scenario.comparison.model_dump()
    windows = scenario.comparison.windows
    for forged in (
        "sha256:" + "0" * 64,
        comparison_identity(windows[1:]),
        experiment.identity(
            {
                "purpose": "research_comparison_v2",
                "pairs": [[w.baseline.run_id, w.research.run_id] for w in windows],
            }
        ),
    ):
        with pytest.raises(ValueError, match="comparison_id must match"):
            ResearchComparison.model_validate(data | {"comparison_id": forged})


def test_revalidation_cannot_be_bypassed(scenario: Scenario) -> None:
    comparison = scenario.comparison
    first = comparison.windows[0]
    forged = first.candidate.model_copy(update={"confidence": 99})
    with pytest.raises(ValueError, match="candidate comparison must match"):
        comparison_of((first.model_copy(update={"candidate": forged}),))
    tampered = first.research.model_copy(
        update={
            "metrics": first.research.metrics.model_copy(
                update={"realized_net_pnl": D(0)}
            )
        }
    )
    with pytest.raises(ValueError, match="metrics must match"):
        WindowComparison(
            baseline=first.baseline, research=tampered, candidate=first.candidate
        )
    with pytest.raises(ValueError, match="metrics must match"):
        ResearchComparison.model_validate(
            comparison.model_copy(
                update={
                    "metrics": comparison.metrics.model_copy(update={"window_count": 1})
                }
            )
        )
    with pytest.raises(ValueError, match="comparison_id must match"):
        ResearchComparison.model_validate(
            comparison.model_copy(update={"comparison_id": "sha256:" + "0" * 64})
        )
    assert ResearchComparison.model_validate(comparison) == comparison


def test_canonical_round_trip_and_missing_artifact(
    scenario: Scenario, tmp_path: Path
) -> None:
    comparison = scenario.comparison
    encoded = comparison.canonical_bytes()
    loaded = load_comparison(encoded, store=scenario.store)
    assert loaded == comparison
    assert loaded.canonical_bytes() == encoded
    assert load_comparison(encoded.decode(), store=scenario.store) == comparison
    empty = ResearchStore(tmp_path / "empty.sqlite")
    with pytest.raises(ResearchStorageError):
        load_comparison(encoded, store=empty)
    first = comparison.windows[0]
    with pytest.raises(ResearchStorageError):
        compare_replays([(first.baseline, first.research)], store=empty)


def test_financial_outcome_carries_pnl_only_when_closed() -> None:
    with pytest.raises(ValueError, match="exactly CLOSED"):
        FinancialOutcome.model_validate({"status": "CLOSED", "exit_reason": "TIME"})
    with pytest.raises(ValueError, match="exactly CLOSED"):
        FinancialOutcome.model_validate({"status": "OPEN_AT_END", "net_pnl": "1"})
    with pytest.raises(ValueError):
        FinancialOutcome.model_validate(
            {"status": "CLOSED", "exit_reason": "TIME", "net_pnl": 1.5}
        )
