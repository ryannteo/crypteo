"""Evaluation window planning and paired scanner-only/research comparison.

Planning reads candles only through the point-in-time gateway and scanner, and
never runs terms, risk, execution, replay or research, so selection cannot see
outcomes. Comparison performs no inference, research-tool dispatch, or
market-data reads: windows are built from existing ``ReplayResult``s, fresh or
frozen, and research links resolve through the caller's store.
"""

from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from crypteo.coinbase_dataset import (
    CoinbaseCanonicalDataset,
    CoinbaseHistoricalDataset,
)
from crypteo.domain._execution_arithmetic import _add
from crypteo.domain.evaluation import (
    CandidateComparison,
    ClosedOutcomeGroup,
    ComparisonMetrics,
    ExclusionReason,
    FinancialOutcome,
    PlannedCandidate,
    ResearchComparison,
    WindowComparison,
    WindowPlan,
    WindowPlanSettings,
)
from crypteo.domain.execution import (
    ClosedTrade,
    DataGap,
    EntryRejection,
    PortfolioSnapshot,
)
from crypteo.domain.market import Timeframe, TradingPair
from crypteo.domain.replay import (
    PAYLOAD_KINDS,
    Delivery,
    ReplayResult,
    proposal_from_terms,
)
from crypteo.domain.replay_research import (
    ReplayResearchRecord,
    ResearchReplayManifest,
    ResearchSelection,
)
from crypteo.domain.research import DecisionOutcome, Opportunity
from crypteo.domain.trade_terms import TradeTerms
from crypteo.experiment import (
    ASSET_PRIORITY,
    SCANNER_POLICY,
    DatasetIdentity,
    ExperimentManifest,
    ReplayConfig,
    identity,
)
from crypteo.replay import _inputs
from crypteo.replay_metrics import _ratio
from crypteo.research_persistence import ResearchStore
from crypteo.research_replay import validate_research_links
from crypteo.scanner import OpportunityScanner
from crypteo.temporal import PointInTimeDataGateway, SimulationClock


def _order_free(value: Any, order_id: str | None) -> Any:
    """Drop order IDs after proving each is the run-bound candidate order ID."""
    if isinstance(value, dict):
        stripped = {}
        for key, item in value.items():
            if key == "order_id":
                if item != order_id:
                    raise ValueError(
                        "order IDs must derive from the replay run_id and candidate"
                    )
            else:
                stripped[key] = _order_free(item, order_id)
        return stripped
    if isinstance(value, (tuple, list)):
        return [_order_free(item, order_id) for item in value]
    return value


def _financial_projection(result: ReplayResult, opportunity_id: str) -> Any:
    terms = next(
        (r.payload for r in result.records if isinstance(r.payload, TradeTerms)), None
    )
    order_id = (
        proposal_from_terms(terms, result.run_id, opportunity_id).order_id
        if terms is not None
        else None
    )
    records = []
    for record in result.records:
        if record.kind in ("SCAN", "DELIVERY", "RESEARCH"):
            continue
        payload = (
            record.payload.proposal
            if isinstance(record.payload, ResearchSelection)
            else record.payload
        )
        records.append(
            (
                PAYLOAD_KINDS[type(payload)],
                record.at,
                record.opportunity_id,
                payload.model_dump(),
            )
        )
    return _order_free(
        {
            "records": records,
            "final_portfolio": result.final_portfolio.model_dump(),
            "metrics": result.metrics.model_dump(),
        },
        order_id,
    )


def _financial_outcome(result: ReplayResult, opportunity_id: str) -> FinancialOutcome:
    kinds = {
        record.kind: record.payload
        for record in result.records
        if record.opportunity_id == opportunity_id
    }
    if isinstance(trade := kinds.get("CLOSED_TRADE"), ClosedTrade):
        reason = trade.exit_fill.reason
        assert reason != "ENTRY"
        return FinancialOutcome(
            status="CLOSED", exit_reason=reason, net_pnl=trade.net_pnl
        )
    if isinstance(kinds.get("DATA_GAP"), DataGap):
        return FinancialOutcome(status="DATA_GAP")
    if isinstance(kinds.get("ENTRY_REJECTION"), EntryRejection):
        return FinancialOutcome(status="ENTRY_REJECTED")
    if "SUBMISSION" in kinds:
        return FinancialOutcome(status="OPEN_AT_END")
    if "RISK_REJECTION" in kinds:
        return FinancialOutcome(status="RISK_REJECTED")
    if "TERMS_REJECTION" in kinds:
        return FinancialOutcome(status="TERMS_REJECTED")
    if "TERMS" in kinds:
        raise ValueError("valid terms must reach a risk result")
    return FinancialOutcome(status="NOT_SELECTED")


def compare_window(
    baseline: ReplayResult, research: ReplayResult
) -> CandidateComparison:
    """Validate one paired single-candidate window and derive its comparison.

    Pure: proves the pair shares deterministic inputs and, for research TRADE,
    the baseline financial chain. Research artifact existence is not checked.
    """
    baseline = ReplayResult.model_validate(baseline.model_dump())
    research = ReplayResult.model_validate(research.model_dump())
    if not isinstance(baseline.manifest, ExperimentManifest) or not isinstance(
        research.manifest, ResearchReplayManifest
    ):
        raise ValueError("pair requires a SCANNER_ONLY baseline and a RESEARCH replay")
    left, right = baseline.manifest, research.manifest
    if (
        left.code,
        left.python_version,
        left.pydantic_version,
        left.replay,
        left.datasets,
    ) != (
        right.code,
        right.python_version,
        right.pydantic_version,
        right.replay,
        right.datasets,
    ):
        raise ValueError("paired replays must share code, versions, config and data")

    def discovery(result: ReplayResult) -> list[Any]:
        return [
            (record.kind, record.at, record.opportunity_id, record.payload)
            for record in result.records
            if record.kind in ("SCAN", "DELIVERY")
        ]

    if discovery(baseline) != discovery(research):
        raise ValueError("paired replays must record identical scans and deliveries")
    admitted = [
        record.payload.opportunity
        for record in baseline.records
        if isinstance(record.payload, Delivery)
        and record.payload.disposition == "ADMITTED"
    ]
    if len(admitted) != 1:
        raise ValueError(
            f"comparison window must admit exactly one candidate; found {len(admitted)}"
        )
    opportunity = admitted[0]
    oid = opportunity.opportunity_id
    (assessment,) = (
        record.payload
        for record in research.records
        if isinstance(record.payload, ReplayResearchRecord)
    )
    if assessment.outcome == DecisionOutcome.TRADE:
        if _financial_projection(baseline, oid) != _financial_projection(research, oid):
            raise ValueError(
                "research TRADE must reproduce the baseline financial chain"
            )
    else:
        cash = research.manifest.replay.initial_cash
        if (
            any(
                record.opportunity_id == oid
                and record.kind not in ("DELIVERY", "RESEARCH")
                for record in research.records
            )
            or research.metrics.entry_count != 0
            or research.final_portfolio
            != PortfolioSnapshot(initial_cash=cash, cash=cash)
        ):
            raise ValueError("declined research must leave the portfolio flat")
    return CandidateComparison(
        opportunity_id=oid,
        pair=opportunity.pair,
        observed_at=opportunity.observed_at,
        outcome=assessment.outcome,
        confidence=assessment.confidence,
        baseline=_financial_outcome(baseline, oid),
        research=_financial_outcome(research, oid),
    )


def _window_key(window: WindowComparison) -> tuple[Any, ...]:
    candidate = window.candidate
    return (
        candidate.observed_at,
        ASSET_PRIORITY.index(candidate.pair),
        candidate.opportunity_id,
    )


def validate_windows(windows: Sequence[WindowComparison]) -> None:
    """Cross-window rules: unique, one policy, shared economics, canonical order."""
    for values, name in (
        ([w.baseline.run_id for w in windows], "baseline run IDs"),
        ([w.research.run_id for w in windows], "research run IDs"),
        ([w.candidate.opportunity_id for w in windows], "opportunity IDs"),
    ):
        if len(values) != len(set(values)):
            raise ValueError(f"comparison windows require unique {name}")
    manifests = [w.research.manifest for w in windows]
    first = manifests[0]
    assert isinstance(first, ResearchReplayManifest)
    for manifest in manifests:
        assert isinstance(manifest, ResearchReplayManifest)
        if (
            manifest.code != first.code
            or manifest.policy != first.policy
            or manifest.research.configuration != first.research.configuration
        ):
            raise ValueError("windows must share code, research policy and workflow")
        left, right = manifest.replay, first.replay
        if (left.initial_cash, left.trade_terms, left.risk, left.execution) != (
            right.initial_cash,
            right.trade_terms,
            right.risk,
            right.execution,
        ):
            raise ValueError("windows must share cash, terms, risk and execution")
    keys = [_window_key(w) for w in windows]
    if keys != sorted(keys):
        raise ValueError(
            "windows must be ordered by observed_at, asset priority, opportunity ID"
        )


def comparison_identity(windows: Sequence[WindowComparison]) -> str:
    return identity(
        {
            "purpose": "research_comparison_v1",
            "pairs": [[w.baseline.run_id, w.research.run_id] for w in windows],
        }
    )


def _closed_pnls(outcomes: Iterable[FinancialOutcome]) -> list[Decimal]:
    return [o.net_pnl for o in outcomes if o.net_pnl is not None]


def _closed_group(outcomes: Iterable[FinancialOutcome]) -> ClosedOutcomeGroup:
    pnls = _closed_pnls(outcomes)
    wins = sum(pnl > 0 for pnl in pnls)
    return ClosedOutcomeGroup(
        net_pnl=_sum(pnls),
        wins=wins,
        losses=sum(pnl < 0 for pnl in pnls),
        breakevens=sum(pnl == 0 for pnl in pnls),
        win_rate=_ratio(wins, len(pnls)),
    )


def _sum(values: Iterable[Decimal]) -> Decimal:
    total = Decimal(0)
    for value in values:
        total = _add(total, value)
    return total


def comparison_metrics(
    candidates: Sequence[CandidateComparison],
) -> ComparisonMetrics:
    baseline = _closed_pnls(c.baseline for c in candidates)
    research = _closed_pnls(c.research for c in candidates)
    return ComparisonMetrics(
        window_count=len(candidates),
        trade_count=sum(c.outcome == DecisionOutcome.TRADE for c in candidates),
        no_trade_count=sum(c.outcome == DecisionOutcome.NO_TRADE for c in candidates),
        abstain_count=sum(c.outcome == DecisionOutcome.ABSTAIN for c in candidates),
        baseline_closed_count=len(baseline),
        research_closed_count=len(research),
        baseline_realized_net_pnl=_sum(baseline),
        research_realized_net_pnl=_sum(research),
        taken=_closed_group(
            c.baseline for c in candidates if c.outcome == DecisionOutcome.TRADE
        ),
        declined=_closed_group(
            c.baseline for c in candidates if c.outcome != DecisionOutcome.TRADE
        ),
        unresolved_count=sum(
            c.baseline.status in ("OPEN_AT_END", "DATA_GAP") for c in candidates
        ),
    )


def _with_links(
    comparison: ResearchComparison, store: ResearchStore
) -> ResearchComparison:
    for window in comparison.windows:
        validate_research_links(window.research, store=store)
    return comparison


def compare_replays(
    pairs: Iterable[tuple[ReplayResult, ReplayResult]], *, store: ResearchStore
) -> ResearchComparison:
    """Pair ``(baseline, research)`` results into a canonically ordered comparison.

    The pure contract cannot prove that research artifacts exist, so every
    research result is also resolved through ``store``. Any failure raises; no
    partial comparison is returned.
    """
    windows = sorted(
        (
            WindowComparison(
                baseline=baseline,
                research=research,
                candidate=compare_window(baseline, research),
            )
            for baseline, research in pairs
        ),
        key=_window_key,
    )
    return _with_links(
        ResearchComparison(
            windows=tuple(windows),
            metrics=comparison_metrics(tuple(w.candidate for w in windows)),
            comparison_id=comparison_identity(windows),
        ),
        store,
    )


def load_comparison(
    encoded: bytes | str, *, store: ResearchStore
) -> ResearchComparison:
    """Import a comparison; pure parsing cannot prove research artifacts exist."""
    return _with_links(ResearchComparison.model_validate_json(encoded), store)


def _candidate_key(opportunity: Opportunity) -> tuple[Any, ...]:
    return (
        opportunity.observed_at,
        ASSET_PRIORITY.index(opportunity.pair),
        opportunity.opportunity_id,
    )


def planned_window(
    opportunity: Opportunity, settings: WindowPlanSettings
) -> ReplayConfig:
    """The single-candidate window ``[T, T + max_holding_bars hours]``.

    A position authorized at T enters on the candle opening at T, and the broker
    closes, gaps or rejects it by the reconciliation at the window end. A candidate
    observed at the end is never scanned, since that boundary only reconciles.
    """
    start = opportunity.observed_at
    return ReplayConfig(
        start=start,
        end=start + settings.risk.max_holding_bars * Timeframe.ONE_HOUR.duration,
        initial_cash=settings.initial_cash,
        assets=(opportunity.pair,),
        trade_terms=settings.trade_terms,
        risk=settings.risk,
        execution=settings.execution,
    )


def _overlaps(
    opportunity: Opportunity, others: Iterable[Opportunity], end: datetime
) -> bool:
    return any(
        other.pair == opportunity.pair
        and opportunity.observed_at < other.observed_at < end
        for other in others
    )


def validate_planned_candidates(
    settings: WindowPlanSettings,
    pairs: tuple[TradingPair, ...],
    candidates: Sequence[PlannedCandidate],
) -> None:
    """Pure plan rules; dataset ends and unlisted candidates need ``validate_plan``."""
    opportunities = [c.opportunity for c in candidates]
    if len({o.opportunity_id for o in opportunities}) != len(opportunities):
        raise ValueError("planned candidates require unique opportunity IDs")
    keys = [_candidate_key(o) for o in opportunities]
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        raise ValueError(
            "candidates must be ordered by observed_at, asset priority, opportunity ID"
        )
    selected = sum(c.decision == "SELECTED" for c in candidates)
    if selected > settings.limit:
        raise ValueError("plan selects more candidates than its limit")
    truncated = selected == settings.limit
    if truncated and candidates[-1].decision != "SELECTED":
        raise ValueError("a plan at its limit must end with its last selection")
    last = opportunities[-1].observed_at if opportunities else None
    for candidate in candidates:
        opportunity = candidate.opportunity
        if opportunity.observed_at < settings.planning_start:
            raise ValueError("planned candidates must not precede planning_start")
        if opportunity.pair not in pairs:
            raise ValueError("planned candidates must belong to a planned dataset")
        window = planned_window(opportunity, settings)
        if candidate.replay is not None and candidate.replay != window:
            raise ValueError(
                "selected replay must match the candidate's derived window"
            )
        if candidate.exclusion_reason == "NOT_AVAILABLE_AT_OBSERVATION":
            continue
        listed = _overlaps(opportunity, opportunities, window.end)
        if listed and candidate.decision == "SELECTED":
            raise ValueError("a selected window cannot contain a listed candidate")
        if (
            candidate.exclusion_reason == "OVERLAPPING_CANDIDATE"
            and not listed
            and not (truncated and last is not None and window.end > last)
        ):
            raise ValueError("OVERLAPPING_CANDIDATE requires an overlapping candidate")


def plan_identity(
    settings: WindowPlanSettings,
    scanner_policy: str,
    datasets: Sequence[DatasetIdentity],
    candidates: Sequence[PlannedCandidate],
) -> str:
    return identity(
        {
            "purpose": "evaluation_window_plan_v1",
            "settings": settings,
            "scanner_policy": scanner_policy,
            "datasets": list(datasets),
            "candidates": list(candidates),
        }
    )


def plan_windows(
    datasets: Iterable[CoinbaseCanonicalDataset | CoinbaseHistoricalDataset],
    settings: WindowPlanSettings,
) -> WindowPlan:
    """Plan single-candidate windows from one full-history scan per pair.

    Each pair's gateway clock sits at its last completed candle close. A
    crossing depends only on the 21 candles up to it, so the scan finds the same
    opportunity a replay starting at its ``observed_at`` admits, provided it was
    available then. Selection uses only scanner candidates and candle close
    times, never prices or outcomes. Failures raise.
    """
    settings = WindowPlanSettings.model_validate(settings.model_dump())
    datasets = tuple(datasets)
    supplied = {dataset.manifest.pair for dataset in datasets}
    pairs = tuple(pair for pair in ASSET_PRIORITY if pair in supplied)
    candles, dataset_ids = _inputs(pairs, datasets)
    found: list[Opportunity] = []
    last_close: dict[TradingPair, datetime] = {}
    for pair in pairs:
        history = tuple(candle for candle in candles if candle.pair is pair)
        if not history:
            continue
        last_close[pair] = max(candle.close_time for candle in history)
        gateway = PointInTimeDataGateway(SimulationClock(last_close[pair]), history)
        scanned = tuple(
            Opportunity.model_validate(item.model_dump())
            for item in OpportunityScanner(gateway).scan(pair=pair)
        )
        if any(item.pair != pair for item in scanned):
            raise ValueError("scanner returned a different pair")
        found.extend(o for o in scanned if o.observed_at >= settings.planning_start)
    found.sort(key=_candidate_key)
    planned: list[PlannedCandidate] = []
    selected = 0
    for opportunity in found:
        if selected == settings.limit:
            break
        window = planned_window(opportunity, settings)
        reason: ExclusionReason | None = None
        if opportunity.available_at > opportunity.observed_at:
            reason = "NOT_AVAILABLE_AT_OBSERVATION"
        elif window.end > last_close[opportunity.pair]:
            reason = "WINDOW_BEYOND_DATA"
        elif _overlaps(opportunity, found, window.end):
            reason = "OVERLAPPING_CANDIDATE"
        if reason is None:
            selected += 1
            planned.append(
                PlannedCandidate(
                    opportunity=opportunity, decision="SELECTED", replay=window
                )
            )
        else:
            planned.append(
                PlannedCandidate(
                    opportunity=opportunity,
                    decision="EXCLUDED",
                    exclusion_reason=reason,
                )
            )
    return WindowPlan(
        settings=settings,
        scanner_policy=SCANNER_POLICY,
        datasets=dataset_ids,
        candidates=tuple(planned),
        plan_id=plan_identity(settings, SCANNER_POLICY, dataset_ids, planned),
    )


def validate_plan(
    plan: WindowPlan,
    datasets: Iterable[CoinbaseCanonicalDataset | CoinbaseHistoricalDataset],
) -> WindowPlan:
    """Re-plan from ``datasets`` and require exact equality.

    Catches what pure validation cannot see: unlisted overlaps, dataset ends,
    omitted candidates, and different datasets.
    """
    plan = WindowPlan.model_validate(plan.model_dump())
    if plan_windows(datasets, plan.settings) != plan:
        raise ValueError("plan must equal a re-plan of the supplied datasets")
    return plan
