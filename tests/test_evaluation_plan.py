"""Mechanical single-candidate window planning over scanner candidates only."""

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from test_evaluation import research_run
from test_replay import BTC, CODE, ETH, SOL, D, H, T, bar, config, dataset, payloads

import crypteo.experiment as experiment
import crypteo.replay as replay
import crypteo.research_replay as research_replay
from crypteo.coinbase_dataset import CoinbaseCanonicalDataset
from crypteo.domain.evaluation import (
    EVALUATION_PERIOD_START,
    FinancialOutcome,
    PlannedCandidate,
    WindowPlan,
    WindowPlanSettings,
)
from crypteo.domain.execution import ClosedTrade
from crypteo.domain.market import TradingPair
from crypteo.domain.replay import Delivery
from crypteo.domain.research import Opportunity
from crypteo.evaluation import (
    _financial_outcome,
    compare_replays,
    plan_windows,
    planned_window,
    validate_plan,
)
from crypteo.execution import SimulatedBroker
from crypteo.experiment import SCANNER_POLICY, ReplayConfig
from crypteo.replay import run_scanner_only
from crypteo.research_persistence import ResearchStore
from crypteo.risk import RiskService
from crypteo.scanner import OpportunityScanner
from crypteo.trade_terms import TradeTermsService

EVALUATION_HOURS = (EVALUATION_PERIOD_START - T) // H


def series(
    pair: TradingPair,
    closes: tuple[str, ...],
    *,
    offset: int = 0,
    delayed: tuple[int, ...] = (),
) -> CoinbaseCanonicalDataset:
    """Twenty closes at 100, then closes[k] at T + (offset + k)h.

    Candles closing at the ``delayed`` indexes become available one hour late.
    """
    candles = tuple(bar(offset + i) for i in range(-21, -1)) + tuple(
        bar(offset + k - 1, price)
        if k not in delayed
        else bar(offset + k - 1, price, available_at=T + (offset + k + 1) * H)
        for k, price in enumerate(closes)
    )
    return dataset(candles, pair=pair)


# Crossings, at hours after T: BTC 0 and 2; ETH 0 and 3; SOL 0 (its trigger
# candle is delayed) and 5 (the final close).
BTC_DATA = series(BTC, ("90", "100", "90", "95", "96", "97", "98"))
ETH_DATA = series(ETH, ("90", "100", "100", "90", "95", "96", "97"))
SOL_DATA = series(SOL, ("90", "95", "96", "97", "100", "90"), delayed=(0,))
DATASETS = (BTC_DATA, ETH_DATA, SOL_DATA)
EXPECTED = [
    (0, BTC, "OVERLAPPING_CANDIDATE"),  # BTC at +2h is inside (T, T+3h)
    (0, ETH, None),  # ETH at +3h is the window end, so it is never scanned
    (0, SOL, "NOT_AVAILABLE_AT_OBSERVATION"),
    (2, BTC, None),
    (3, ETH, None),  # window end is the final ETH close
    (5, SOL, "WINDOW_BEYOND_DATA"),
]


def settings(**changes: Any) -> WindowPlanSettings:
    cfg = config()
    return WindowPlanSettings.model_validate(
        dict(
            purpose="PLUMBING",
            planning_start=T,
            limit=10,
            initial_cash=cfg.initial_cash,
            trade_terms=cfg.trade_terms,
            risk=cfg.risk,
            execution=cfg.execution,
        )
        | changes
    )


def summary(plan: WindowPlan) -> list[tuple[int, TradingPair, str | None]]:
    return [
        (
            (c.opportunity.observed_at - T) // H,
            c.opportunity.pair,
            c.exclusion_reason,
        )
        for c in plan.candidates
    ]


def forged(plan: WindowPlan, **fields: Any) -> dict[str, Any]:
    """Plan data with replaced fields and a recomputed plan_id."""
    data = plan.model_dump() | fields
    data["plan_id"] = experiment.identity(
        {
            "purpose": "evaluation_window_plan_v1",
            "settings": data["settings"],
            "scanner_policy": data["scanner_policy"],
            "datasets": data["datasets"],
            "candidates": data["candidates"],
        }
    )
    return data


def with_candidate(plan: WindowPlan, index: int, **changes: Any) -> dict[str, Any]:
    candidates = list(plan.model_dump()["candidates"])
    candidates[index] = candidates[index] | changes
    return forged(plan, candidates=candidates)


def single_pair_datasets(
    pair: TradingPair, datasets: tuple[CoinbaseCanonicalDataset, ...] = DATASETS
) -> list[CoinbaseCanonicalDataset]:
    return [d for d in datasets if d.manifest.pair is pair]


@pytest.fixture(scope="module")
def plan() -> WindowPlan:
    return plan_windows(DATASETS, settings())


def test_canonical_order_reasons_and_windows(plan: WindowPlan) -> None:
    assert summary(plan) == EXPECTED
    assert plan.scanner_policy == SCANNER_POLICY
    assert [d.pair for d in plan.datasets] == [BTC, ETH, SOL]
    for candidate in plan.candidates:
        opportunity = candidate.opportunity
        if candidate.exclusion_reason is None:
            assert candidate.decision == "SELECTED"
            assert candidate.replay == ReplayConfig(
                start=opportunity.observed_at,
                end=opportunity.observed_at + 3 * H,
                initial_cash=D(1000),
                assets=(opportunity.pair,),
                trade_terms=plan.settings.trade_terms,
                risk=plan.settings.risk,
                execution=plan.settings.execution,
            )
        else:
            assert candidate.decision == "EXCLUDED" and candidate.replay is None
    assert plan.plan_id == experiment.identity(
        {
            "purpose": "evaluation_window_plan_v1",
            "settings": plan.settings,
            "scanner_policy": SCANNER_POLICY,
            "datasets": list(plan.datasets),
            "candidates": list(plan.candidates),
        }
    )
    assert WindowPlan.model_validate(forged(plan)) == plan


def test_input_order_does_not_change_plan(plan: WindowPlan) -> None:
    assert plan_windows(reversed(DATASETS), settings()) == plan


@pytest.mark.parametrize(
    "limit,expected",
    [(1, EXPECTED[:2]), (2, EXPECTED[:4]), (3, EXPECTED[:5]), (4, EXPECTED)],
)
def test_limit_truncates_after_last_selection_or_data_ends(
    limit: int, expected: list[Any]
) -> None:
    plan = plan_windows(DATASETS, settings(limit=limit))
    assert summary(plan) == expected
    selected = [c for c in plan.candidates if c.decision == "SELECTED"]
    assert len(selected) == min(limit, 3)
    if limit <= 3:
        assert plan.candidates[-1].decision == "SELECTED"


def test_planning_start_and_subset_of_pairs() -> None:
    later = plan_windows(DATASETS, settings(planning_start=T + 2 * H))
    assert summary(later) == EXPECTED[3:]
    eth = plan_windows([ETH_DATA], settings())
    assert summary(eth) == [(0, ETH, None), (3, ETH, None)]
    assert [d.pair for d in eth.datasets] == [ETH]
    nothing = plan_windows([BTC_DATA], settings(planning_start=T + 10 * H))
    assert nothing.candidates == ()


def test_window_length_follows_max_holding_bars() -> None:
    # With four holding bars, ETH +3h overlaps ETH +0h and no longer fits the data.
    longer = plan_windows(
        [ETH_DATA],
        settings(risk=config().risk.model_copy(update={"max_holding_bars": 4})),
    )
    assert summary(longer) == [
        (0, ETH, "OVERLAPPING_CANDIDATE"),
        (3, ETH, "WINDOW_BEYOND_DATA"),
    ]


def test_same_pair_overlap_one_hour_after(monkeypatch: pytest.MonkeyPatch) -> None:
    # Consecutive same-pair crossings are impossible for this scanner, so plant one.
    original = OpportunityScanner.scan

    def scan(self: OpportunityScanner, *, pair: TradingPair) -> tuple[Opportunity, ...]:
        found = original(self, pair=pair)
        if pair is not ETH:
            return found
        first = found[0]
        planted = first.model_copy(
            update={
                "opportunity_id": "sha256:" + "0" * 64,
                "observed_at": first.observed_at + H,
                "available_at": first.available_at + H,
            }
        )
        return (first, planted, *found[1:])

    monkeypatch.setattr(OpportunityScanner, "scan", scan)
    plan = plan_windows([ETH_DATA], settings())
    assert summary(plan) == [
        (0, ETH, "OVERLAPPING_CANDIDATE"),
        (1, ETH, "OVERLAPPING_CANDIDATE"),
        (3, ETH, None),
    ]


def test_future_availability_is_never_planned() -> None:
    # The final SOL candle becomes visible only after the last close, so the
    # crossing it would trigger is invisible to the planner's gateway.
    late = series(SOL, ("90", "95", "96", "97", "100", "90"), delayed=(0, 5))
    plan = plan_windows([late], settings())
    assert summary(plan) == [(0, SOL, "NOT_AVAILABLE_AT_OBSERVATION")]
    first = plan.candidates[0].opportunity
    assert first.available_at == first.observed_at + H


def test_selected_windows_admit_exactly_the_planned_candidate(
    plan: WindowPlan,
) -> None:
    outcomes = []
    for candidate in plan.candidates:
        if candidate.replay is None:
            continue
        opportunity = candidate.opportunity
        result = run_scanner_only(
            candidate.replay, single_pair_datasets(opportunity.pair), code=CODE
        )
        admitted = [
            d.opportunity
            for d in payloads(result, Delivery)
            if d.disposition == "ADMITTED"
        ]
        assert admitted == [opportunity]
        outcome = _financial_outcome(result, opportunity.opportunity_id)
        assert outcome.status not in ("OPEN_AT_END", "DATA_GAP")
        outcomes.append(outcome.exit_reason)
        if outcome.exit_reason == "TIME":
            (trade,) = payloads(result, ClosedTrade)
            assert trade.exit_fill.candle_close_time == candidate.replay.end
            assert trade.exit_fill.recorded_at == candidate.replay.end
    assert outcomes == ["TARGET", "TIME", "TIME"]


@pytest.mark.parametrize("bars", [1, 2, 3, 5])
def test_holding_bound_is_exactly_max_holding_bars(bars: int) -> None:
    data = series(BTC, ("90",) + ("95",) * 6)
    risk = config().risk.model_copy(update={"max_holding_bars": bars})
    plan = plan_windows([data], settings(risk=risk))
    (candidate,) = plan.candidates
    window = candidate.replay
    assert window is not None and window.end == T + bars * H
    oid = candidate.opportunity.opportunity_id
    result = run_scanner_only(window, [data], code=CODE)
    (trade,) = payloads(result, ClosedTrade)
    assert trade.exit_fill.reason == "TIME" and trade.holding_bars == bars
    assert trade.exit_fill.recorded_at == window.end
    # One hour shorter leaves the position open; with one bar it admits nothing.
    shorter = ReplayConfig.model_validate(window.model_dump() | {"end": window.end - H})
    early = run_scanner_only(shorter, [data], code=CODE)
    assert _financial_outcome(early, oid) == FinancialOutcome(
        status="OPEN_AT_END" if bars > 1 else "NOT_SELECTED"
    )


def test_holding_gap_resolves_as_data_gap_within_window() -> None:
    candles = tuple(c for c in BTC_DATA.candles if c.open_time != T + 3 * H)
    data = dataset(candles, pair=BTC)
    plan = plan_windows([data], settings())
    selected = [c for c in plan.candidates if c.replay is not None]
    assert [c.opportunity.observed_at for c in selected] == [T + 2 * H]
    (candidate,) = selected
    assert candidate.replay is not None
    result = run_scanner_only(candidate.replay, [data], code=CODE)
    outcome = _financial_outcome(result, candidate.opportunity.opportunity_id)
    assert outcome == FinancialOutcome(status="DATA_GAP")


def test_planned_window_passes_research_comparison(
    plan: WindowPlan, tmp_path: Path
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    pairs = []
    for candidate, outcome in zip(
        (c for c in plan.candidates if c.replay is not None),
        ("TRADE", "NO_TRADE", "TRADE"),
        strict=True,
    ):
        assert candidate.replay is not None
        datasets = single_pair_datasets(candidate.opportunity.pair)
        pairs.append(
            (
                run_scanner_only(candidate.replay, datasets, code=CODE),
                research_run(store, candidate.replay, datasets, outcome),
            )
        )
    comparison = compare_replays(pairs, store=store)
    assert [w.candidate.opportunity_id for w in comparison.windows] == [
        c.opportunity.opportunity_id for c in plan.candidates if c.replay is not None
    ]
    assert comparison.metrics.unresolved_count == 0


def test_evaluation_purpose_enforces_period_start() -> None:
    with pytest.raises(ValueError, match="start in the evaluation period"):
        settings(purpose="EVALUATION", planning_start=EVALUATION_PERIOD_START - H)
    assert settings(planning_start=EVALUATION_PERIOD_START - H).purpose == "PLUMBING"
    # Crossings one hour either side of the period start.
    data = series(
        BTC, ("90", "100", "90", "95", "96", "97"), offset=EVALUATION_HOURS - 1
    )
    evaluation = plan_windows(
        [data], settings(purpose="EVALUATION", planning_start=EVALUATION_PERIOD_START)
    )
    assert [c.opportunity.observed_at for c in evaluation.candidates] == [
        EVALUATION_PERIOD_START + H
    ]
    plumbing = plan_windows(
        [data], settings(planning_start=EVALUATION_PERIOD_START - H)
    )
    assert [c.opportunity.observed_at for c in plumbing.candidates] == [
        EVALUATION_PERIOD_START - H,
        EVALUATION_PERIOD_START + H,
    ]


def test_planner_never_reaches_outcomes(
    plan: WindowPlan, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "planner reached terms, risk, execution, replay or research"
        )

    for owner, name in (
        (TradeTermsService, "__init__"),
        (TradeTermsService, "generate"),
        (RiskService, "__init__"),
        (RiskService, "authorize"),
        (SimulatedBroker, "__init__"),
        (SimulatedBroker, "submit"),
        (SimulatedBroker, "process"),
        (replay, "_replay_engine"),
        (replay, "run_scanner_only"),
        (research_replay, "run_research_replay"),
        (research_replay, "run_durable_opportunity_research"),
    ):
        monkeypatch.setattr(owner, name, forbidden)
    assert plan_windows(DATASETS, settings()) == plan
    assert validate_plan(plan, DATASETS) == plan


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"planning_start": T + timedelta(minutes=30)}, "align to an hour"),
        ({"limit": 0}, "greater than 0"),
        ({"limit": D(1)}, "valid integer"),
        (
            {
                "execution": config().execution.model_copy(
                    update={"price_increment": D(1)}
                )
            },
            "price increments must match",
        ),
        ({"purpose": "LIVE"}, "EVALUATION"),
    ],
)
def test_settings_rules(changes: dict[str, Any], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        settings(**changes)


def test_planned_candidate_decision_consistency(plan: WindowPlan) -> None:
    selected, excluded = plan.candidates[1], plan.candidates[0]
    unavailable = plan.candidates[2]
    for data in (
        selected.model_dump() | {"decision": "EXCLUDED"},
        selected.model_dump() | {"exclusion_reason": "OVERLAPPING_CANDIDATE"},
        excluded.model_dump() | {"decision": "SELECTED"},
        excluded.model_dump() | {"replay": selected.replay},
    ):
        with pytest.raises(ValueError, match="exactly SELECTED"):
            PlannedCandidate.model_validate(data)
    for data in (
        unavailable.model_dump() | {"exclusion_reason": "WINDOW_BEYOND_DATA"},
        excluded.model_dump() | {"exclusion_reason": "NOT_AVAILABLE_AT_OBSERVATION"},
    ):
        with pytest.raises(ValueError, match="opportunity's own times"):
            PlannedCandidate.model_validate(data)


def _window(plan: WindowPlan, index: int, **changes: Any) -> dict[str, Any]:
    replay_config = plan.candidates[index].replay
    assert replay_config is not None
    return with_candidate(plan, index, replay=replay_config.model_dump() | changes)


@pytest.mark.parametrize(
    "tamper,match",
    [
        (
            lambda p: forged(p, scanner_policy=SCANNER_POLICY + " "),
            "scanner policy must match",
        ),
        (
            lambda p: forged(p, datasets=list(reversed(p.model_dump()["datasets"]))),
            "canonical pair order",
        ),
        (
            lambda p: forged(p, datasets=[p.model_dump()["datasets"][0]] * 2),
            "canonical pair order",
        ),
        (
            lambda p: forged(p, datasets=p.model_dump()["datasets"][:2]),
            "belong to a planned dataset",
        ),
        (
            lambda p: forged(
                p, candidates=list(reversed(p.model_dump()["candidates"]))
            ),
            "must be ordered",
        ),
        (
            lambda p: forged(p, candidates=[p.model_dump()["candidates"][1]] * 2),
            "unique opportunity IDs",
        ),
        (
            lambda p: forged(
                p, settings=p.settings.model_copy(update={"planning_start": T + H})
            ),
            "precede planning_start",
        ),
        (lambda p: _window(p, 1, end=T + 4 * H), "derived window"),
        (lambda p: _window(p, 1, assets=(BTC,)), "derived window"),
        (lambda p: _window(p, 1, initial_cash=D(2000)), "derived window"),
        (
            lambda p: forged(
                p, settings=p.settings.model_copy(update={"initial_cash": D(2000)})
            ),
            "derived window",
        ),
        (
            lambda p: with_candidate(
                p,
                0,
                decision="SELECTED",
                exclusion_reason=None,
                replay=planned_window(p.candidates[0].opportunity, p.settings),
            ),
            "listed candidate",
        ),
        (
            lambda p: with_candidate(
                p,
                1,
                decision="EXCLUDED",
                exclusion_reason="OVERLAPPING_CANDIDATE",
                replay=None,
            ),
            "requires an overlapping candidate",
        ),
        (
            lambda p: forged(p, settings=p.settings.model_copy(update={"limit": 2})),
            "more candidates than its limit",
        ),
        (
            lambda p: forged(p, settings=p.settings.model_copy(update={"limit": 3})),
            "end with its last selection",
        ),
        (lambda p: p.model_dump() | {"plan_id": "sha256:" + "0" * 64}, "plan_id"),
        (
            lambda p: (
                p.model_dump()
                | {
                    "plan_id": experiment.identity(
                        {
                            "purpose": "evaluation_window_plan_v2",
                            **{
                                k: v
                                for k, v in p.model_dump().items()
                                if k != "plan_id"
                            },
                        }
                    )
                }
            ),
            "plan_id",
        ),
    ],
)
def test_pure_contract_rejections(plan: WindowPlan, tamper: Any, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        WindowPlan.model_validate(tamper(plan))


def test_truncated_overlap_may_lie_beyond_the_last_selection() -> None:
    # Limit 1 ends at ETH +0h; BTC +0h overlaps BTC +2h, which is not listed.
    plan = plan_windows(DATASETS, settings(limit=1))
    assert summary(plan) == EXPECTED[:2]
    assert WindowPlan.model_validate(forged(plan)) == plan
    raised = forged(plan, settings=plan.settings.model_copy(update={"limit": 2}))
    with pytest.raises(ValueError, match="requires an overlapping candidate"):
        WindowPlan.model_validate(raised)


def test_validate_plan_requires_exact_replan(plan: WindowPlan) -> None:
    assert validate_plan(plan, DATASETS) == plan
    candidates = plan.model_dump()["candidates"]
    omitted = forged(plan, candidates=[c for i, c in enumerate(candidates) if i != 2])
    # Selecting BTC +0h and hiding the BTC +2h crossing that overlaps it.
    hidden = forged(
        plan,
        candidates=[
            candidates[0]
            | {
                "decision": "SELECTED",
                "exclusion_reason": None,
                "replay": planned_window(plan.candidates[0].opportunity, plan.settings),
            },
            *(c for i, c in enumerate(candidates[1:], 1) if i != 3),
        ],
    )
    # A plan for limit 2, relabeled as limit 3, stays purely consistent.
    short = plan_windows(DATASETS, settings(limit=2))
    relabeled = forged(short, settings=short.settings.model_copy(update={"limit": 3}))
    for data in (omitted, hidden, relabeled):
        forged_plan = WindowPlan.model_validate(data)
        with pytest.raises(ValueError, match="re-plan of the supplied datasets"):
            validate_plan(forged_plan, DATASETS)
    changed = (
        BTC_DATA,
        series(ETH, ("90", "100", "100", "90", "95", "96", "98")),
        SOL_DATA,
    )
    with pytest.raises(ValueError, match="re-plan of the supplied datasets"):
        validate_plan(plan, changed)
    with pytest.raises(ValueError, match="re-plan of the supplied datasets"):
        validate_plan(plan, DATASETS[:2])


def test_dataset_inputs_are_validated() -> None:
    with pytest.raises(ValueError, match="exactly one canonical dataset per pair"):
        plan_windows([BTC_DATA, BTC_DATA], settings())
    with pytest.raises(ValueError, match="at least 1"):
        plan_windows([], settings())
    tampered = CoinbaseCanonicalDataset(
        candles=BTC_DATA.candles[:-1], manifest=BTC_DATA.manifest
    )
    with pytest.raises(ValueError, match="coverage mismatch"):
        plan_windows([tampered], settings())


def test_canonical_round_trip_and_revalidation(plan: WindowPlan) -> None:
    encoded = plan.canonical_bytes()
    loaded = WindowPlan.model_validate_json(encoded)
    assert loaded == plan and loaded.canonical_bytes() == encoded
    with pytest.raises(ValueError, match="plan_id must match"):
        WindowPlan.model_validate(
            plan.model_copy(update={"plan_id": "sha256:" + "0" * 64})
        )
    forged_candidate = plan.candidates[1].model_copy(update={"decision": "EXCLUDED"})
    with pytest.raises(ValueError, match="exactly SELECTED"):
        WindowPlan.model_validate(
            plan.model_copy(
                update={"candidates": (plan.candidates[0], forged_candidate)}
            )
        )
    with pytest.raises(ValueError, match="EVALUATION planning must start"):
        WindowPlan.model_validate(
            plan.model_copy(
                update={
                    "settings": plan.settings.model_copy(
                        update={"purpose": "EVALUATION"}
                    )
                }
            )
        )
    assert WindowPlan.model_validate(plan) == plan
