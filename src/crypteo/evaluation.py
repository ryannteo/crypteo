"""Evaluation window planning, paired comparison, and the evaluation runner.

Planning reads candles only through the point-in-time gateway and scanner, and
never runs terms, risk, execution, replay or research, so selection cannot see
outcomes. Comparison performs no inference, research-tool dispatch, or
market-data reads: windows are built from existing ``ReplayResult``s, fresh or
frozen, and research links resolve through the caller's store. The runner
executes a plan as given through the existing replay entrypoints and saves each
window as it completes; reproduction replays saved research frozen.
"""

import os
import re
import tempfile
from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from crypteo.coinbase_dataset import (
    CoinbaseCanonicalDataset,
    CoinbaseHistoricalDataset,
)
from crypteo.coinbase_persistence import _write_immutable
from crypteo.domain._execution_arithmetic import _add
from crypteo.domain.evaluation import (
    CandidateComparison,
    ClosedOutcomeGroup,
    ComparisonMetrics,
    EvaluationAttempt,
    EvaluationResult,
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
    FreshResearchSettings,
    FrozenResearchMapping,
    FrozenResearchSettings,
    ReplayResearchRecord,
    ResearchReplayManifest,
    ResearchSelection,
    research_run_id,
)
from crypteo.domain.research import DecisionOutcome, Opportunity
from crypteo.domain.research_workflow import ResearchWorkflowConfig
from crypteo.domain.trade_terms import TradeTerms
from crypteo.experiment import (
    ASSET_PRIORITY,
    SCANNER_POLICY,
    CodeIdentity,
    DatasetIdentity,
    ExperimentManifest,
    ReplayConfig,
    build_research_manifest,
    canonical_bytes,
    current_code_identity,
    identity,
)
from crypteo.replay import _inputs, run_scanner_only
from crypteo.replay_metrics import _ratio
from crypteo.research_journal import ResearchStorageError
from crypteo.research_model import ObservableResearchModel
from crypteo.research_persistence import ResearchStore
from crypteo.research_replay import (
    ResearchBudgetExceeded,
    load_research_replay,
    run_research_replay,
    validate_research_links,
)
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


type _Dataset = CoinbaseCanonicalDataset | CoinbaseHistoricalDataset

_ATTEMPT_NAME = re.compile(r"attempt-([1-9][0-9]*)\.json")


def _selected(plan: WindowPlan) -> list[PlannedCandidate]:
    return [c for c in plan.candidates if c.decision == "SELECTED"]


def attempt_trial_id(trial: str, opportunity_id: str, attempt: int) -> str:
    return f"{trial}/{opportunity_id}/{attempt}"


def _validate_attempts(
    ids: Sequence[str],
    trial: str,
    max_research_runs: int,
    attempts: Sequence[EvaluationAttempt],
) -> dict[str, EvaluationAttempt]:
    """Attempt rules; returns each opportunity's latest attempt."""
    keys = [(a.opportunity_id, a.attempt) for a in attempts]
    if len(set(keys)) != len(keys):
        raise ValueError("attempts must be unique per opportunity and number")
    if any(a.opportunity_id not in ids for a in attempts):
        raise ValueError("attempts must belong to the plan's selected candidates")
    if keys != sorted(keys, key=lambda key: (ids.index(key[0]), key[1])):
        raise ValueError("attempts must be ordered by planned window, then number")
    latest: dict[str, EvaluationAttempt] = {}
    for attempt in attempts:
        previous = latest.get(attempt.opportunity_id)
        if attempt.attempt != (previous.attempt + 1 if previous else 1):
            raise ValueError("attempts must be numbered from 1 without gaps")
        if attempt.trial_id != attempt_trial_id(
            trial, attempt.opportunity_id, attempt.attempt
        ):
            raise ValueError("attempt trial_id must be trial/opportunity_id/attempt")
        latest[attempt.opportunity_id] = attempt
    if len(attempts) > max_research_runs:
        raise ValueError("attempts must not exceed max_research_runs")
    return latest


def validate_evaluation(
    plan: WindowPlan,
    comparison: ResearchComparison,
    trial: str,
    max_research_runs: int,
    attempts: Sequence[EvaluationAttempt],
) -> None:
    """Pure binding of comparison windows and attempts to the executed plan."""
    selected = _selected(plan)
    windows = comparison.windows
    ids = [c.opportunity.opportunity_id for c in selected]
    if [w.candidate.opportunity_id for w in windows] != ids:
        raise ValueError(
            "comparison windows must match the plan's selected candidates in order"
        )
    datasets = {item.pair: item for item in plan.datasets}
    settings = plan.settings
    for window, candidate in zip(windows, selected, strict=True):
        replay = candidate.replay
        assert replay is not None
        if (
            window.baseline.manifest.replay != replay
            or window.research.manifest.replay != replay
        ):
            raise ValueError("window replay configs must equal the planned windows")
        if window.baseline.manifest.datasets != (datasets[candidate.opportunity.pair],):
            raise ValueError("windows must replay only the plan's dataset for the pair")
        # Implied by the planned windows; checked so the binding is explicit.
        if (replay.initial_cash, replay.trade_terms, replay.risk, replay.execution) != (
            settings.initial_cash,
            settings.trade_terms,
            settings.risk,
            settings.execution,
        ):
            raise ValueError("window economics must equal the plan settings")
    latest = _validate_attempts(ids, trial, max_research_runs, attempts)
    for window in windows:
        (record,) = (
            r.payload
            for r in window.research.records
            if isinstance(r.payload, ReplayResearchRecord)
        )
        attempt = latest.get(window.candidate.opportunity_id)
        if attempt is None or record.origin.trial_id != attempt.trial_id:
            raise ValueError("window research must originate from its latest attempt")


def evaluation_identity(
    plan: WindowPlan,
    comparison: ResearchComparison,
    trial: str,
    max_research_runs: int,
    attempts: Sequence[EvaluationAttempt],
) -> str:
    return identity(
        {
            "purpose": "evaluation_result_v1",
            "plan_id": plan.plan_id,
            "comparison_id": comparison.comparison_id,
            "trial": trial,
            "max_research_runs": max_research_runs,
            "attempts": list(attempts),
        }
    )


def _evaluation(
    plan: WindowPlan,
    comparison: ResearchComparison,
    trial: str,
    max_research_runs: int,
    attempts: tuple[EvaluationAttempt, ...],
) -> EvaluationResult:
    return EvaluationResult(
        plan=plan,
        comparison=comparison,
        trial=trial,
        max_research_runs=max_research_runs,
        attempts=attempts,
        evaluation_id=evaluation_identity(
            plan, comparison, trial, max_research_runs, attempts
        ),
    )


def load_evaluation(encoded: bytes | str, *, store: ResearchStore) -> EvaluationResult:
    """Import an evaluation; pure parsing cannot prove research artifacts exist."""
    result = EvaluationResult.model_validate_json(encoded)
    _with_links(result.comparison, store)
    return result


def _pair_dataset(datasets: Sequence[_Dataset], pair: TradingPair) -> _Dataset:
    return next(d for d in datasets if d.manifest.pair is pair)


def _window_dir(directory: Path, index: int) -> Path:
    return directory / "windows" / f"{index:03d}"


def _write_atomic(path: Path, content: bytes) -> None:
    """Publish whole or not at all: a temporary file in the directory, renamed."""
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(handle.name, 0o644)  # Temporary files are private; peers are not.
    os.replace(handle.name, path)


def _attempts(
    directory: Path, ids: Sequence[str], trial: str, max_research_runs: int
) -> tuple[EvaluationAttempt, ...]:
    """Every reservation under ``windows/``, each canonical and in its own window."""
    found = []
    for path in (directory / "windows").rglob("attempt-*.json"):
        encoded = path.read_bytes()
        attempt = EvaluationAttempt.model_validate_json(encoded)
        match = _ATTEMPT_NAME.fullmatch(path.name)
        if (
            match is None
            or int(match.group(1)) != attempt.attempt
            or attempt.opportunity_id not in ids
            or path.parent
            != _window_dir(directory, ids.index(attempt.opportunity_id) + 1)
            or canonical_bytes(attempt) != encoded
        ):
            raise ValueError(
                f"attempt file differs from its window or encoding: {path}"
            )
        found.append(attempt)
    found.sort(key=lambda a: (ids.index(a.opportunity_id), a.attempt))
    _validate_attempts(ids, trial, max_research_runs, found)
    return tuple(found)


def _attempt_research_run_id(
    candidate: PlannedCandidate,
    dataset: _Dataset,
    settings: FreshResearchSettings,
    code: CodeIdentity,
    trial_id: str,
) -> str:
    """The ``research_run_id`` a fresh window replay under ``trial_id`` registers."""
    replay = candidate.replay
    assert replay is not None
    manifest = build_research_manifest(
        replay,
        _inputs(replay.assets, [dataset])[1],
        code,
        FreshResearchSettings(
            configuration=settings.configuration,
            trial_id=trial_id,
            max_research_runs=1,
        ),
    )
    return research_run_id(manifest.run_id, candidate.opportunity)


def _reserve(
    directory: Path,
    ids: Sequence[str],
    index: int,
    candidate: PlannedCandidate,
    dataset: _Dataset,
    *,
    store: ResearchStore,
    settings: FreshResearchSettings,
    code: CodeIdentity,
) -> EvaluationAttempt:
    """Durably claim the next attempt for window ``index`` before any inference.

    A retry is refused when the previous attempt completed: its result must be
    restored, not replaced by fresh inference chosen after seeing it.
    """
    trial, cap = settings.trial_id, settings.max_research_runs
    attempts = _attempts(directory, ids, trial, cap)
    if len(attempts) >= cap:
        raise ResearchBudgetExceeded(
            f"max_research_runs={cap} is exhausted by {len(attempts)} reserved "
            f"attempts; window {index:03d} was not researched"
        )
    oid = ids[index - 1]
    number = 1 + sum(a.opportunity_id == oid for a in attempts)
    if number > 1:
        previous = _attempt_research_run_id(
            candidate,
            dataset,
            settings,
            code,
            attempt_trial_id(trial, oid, number - 1),
        )
        try:
            state = store.inspect_run(previous).state
        except ResearchStorageError as error:
            # Only an unregistered run may proceed; every other failure raises.
            if str(error) != "run missing or unsupported version":
                raise
            state = None
        if state == "COMPLETED":
            raise ValueError(
                f"window {index:03d} research.json is missing although attempt "
                f"{number - 1} completed (research_run_id={previous}); restore "
                "it rather than re-research the window"
            )
    attempt = EvaluationAttempt(
        opportunity_id=oid,
        attempt=number,
        trial_id=attempt_trial_id(trial, oid, number),
    )
    path = _window_dir(directory, index) / f"attempt-{number}.json"
    with path.open("xb") as handle:
        handle.write(canonical_bytes(attempt))
        handle.flush()
        os.fsync(handle.fileno())
    return attempt


async def _run_window(
    directory: Path,
    ids: Sequence[str],
    index: int,
    candidate: PlannedCandidate,
    dataset: _Dataset,
    *,
    store: ResearchStore,
    model: ObservableResearchModel,
    settings: FreshResearchSettings,
    code: CodeIdentity,
) -> tuple[ReplayResult, ReplayResult]:
    replay = candidate.replay
    assert replay is not None
    window = _window_dir(directory, index)
    baseline = run_scanner_only(replay, [dataset], code=code)
    _write_immutable(window / "baseline.json", baseline.canonical_bytes())
    path = window / "research.json"
    if path.exists():
        encoded = path.read_bytes()
        research = load_research_replay(encoded, store=store)
        latest = [
            a
            for a in _attempts(
                directory, ids, settings.trial_id, settings.max_research_runs
            )
            if a.opportunity_id == ids[index - 1]
        ]
        manifest = research.manifest
        if (
            research.canonical_bytes() != encoded
            or not latest
            or not isinstance(manifest, ResearchReplayManifest)
            or manifest.code != code
            or manifest.research
            != FreshResearchSettings(
                configuration=settings.configuration,
                trial_id=latest[-1].trial_id,
                max_research_runs=1,
            )
        ):
            raise ValueError(
                f"window {index:03d} research.json is not this run's canonical "
                "result for its latest attempt"
            )
    else:
        attempt = _reserve(
            directory,
            ids,
            index,
            candidate,
            dataset,
            store=store,
            settings=settings,
            code=code,
        )
        try:
            research = await run_research_replay(
                replay,
                [dataset],
                research=FreshResearchSettings(
                    configuration=settings.configuration,
                    trial_id=attempt.trial_id,
                    max_research_runs=1,
                ),
                store=store,
                model=model,
                code=code,
            )
        except BaseException as error:
            error.add_note(
                f"evaluation window {index:03d}; attempt {attempt.attempt}; "
                f"trial_id={attempt.trial_id}"
            )
            raise
        _write_atomic(path, research.canonical_bytes())
    try:
        compare_window(baseline, research)
    except ValueError as error:
        error.add_note(f"evaluation window {index:03d}")
        raise
    return baseline, research


async def run_evaluation(
    plan: WindowPlan,
    datasets: Iterable[_Dataset],
    *,
    directory: Path,
    store: ResearchStore,
    model: ObservableResearchModel,
    configuration: ResearchWorkflowConfig,
    trial: str,
    max_research_runs: int,
    code: CodeIdentity | None = None,
) -> EvaluationResult:
    """Execute a validated plan window by window, saving each as it completes.

    Each window runs a scanner-only replay and, unless ``research.json`` is
    already saved, reserves an attempt file and runs one fresh research replay
    with a cap of one. A failure raises and leaves its reservation; calling
    again with the same arguments resumes without repeating completed research.
    Reservations across all calls never exceed ``max_research_runs``. No
    automatic retries. Single process: concurrent runners on one directory are
    unsupported.
    """
    datasets = tuple(datasets)
    plan = validate_plan(plan, datasets)
    selected = _selected(plan)
    if not selected:
        raise ValueError("evaluation requires a plan with selected candidates")
    settings = FreshResearchSettings(
        configuration=configuration,
        trial_id=trial,
        max_research_runs=max_research_runs,
    )
    if settings.max_research_runs < len(selected):
        raise ValueError(
            f"max_research_runs={settings.max_research_runs} is less than the "
            f"plan's {len(selected)} selected windows"
        )
    # Checked before any reservation, as run_research_replay checks it after one.
    if not isinstance(model, ObservableResearchModel):
        raise ValueError("evaluation requires an observable model")
    if model.identity.provider != settings.configuration.model.provider:
        raise ValueError("configured provider differs from model adapter")
    code = CodeIdentity.model_validate((code or current_code_identity()).model_dump())
    directory = Path(directory)
    # Existing files must be byte-identical: a resume cannot change any input.
    _write_immutable(directory / "plan.json", plan.canonical_bytes())
    _write_immutable(
        directory / "run.json",
        canonical_bytes(
            {
                "trial": settings.trial_id,
                "max_research_runs": settings.max_research_runs,
                "configuration": settings.configuration,
                "code": code,
            }
        ),
    )
    ids = [c.opportunity.opportunity_id for c in selected]
    pairs = []
    for index, candidate in enumerate(selected, 1):
        pairs.append(
            await _run_window(
                directory,
                ids,
                index,
                candidate,
                _pair_dataset(datasets, candidate.opportunity.pair),
                store=store,
                model=model,
                settings=settings,
                code=code,
            )
        )
    result = _evaluation(
        plan,
        compare_replays(pairs, store=store),
        settings.trial_id,
        settings.max_research_runs,
        _attempts(directory, ids, settings.trial_id, settings.max_research_runs),
    )
    _write_immutable(directory / "evaluation.json", result.canonical_bytes())
    return result


def _frozen_settings(research: ReplayResult) -> FrozenResearchSettings:
    manifest = research.manifest
    assert isinstance(manifest, ResearchReplayManifest)
    return FrozenResearchSettings(
        configuration=manifest.research.configuration,
        mapping=tuple(
            FrozenResearchMapping(
                opportunity_id=r.payload.opportunity_id,
                research_run_id=r.payload.research_run_id,
                artifact_checksum=r.payload.artifact_checksum,
                context_digest=r.payload.context_digest,
                origin=r.payload.origin,
            )
            for r in research.records
            if isinstance(r.payload, ReplayResearchRecord)
        ),
    )


async def reproduce_evaluation(
    result: EvaluationResult,
    datasets: Iterable[_Dataset],
    *,
    store: ResearchStore,
    code: CodeIdentity | None = None,
) -> EvaluationResult:
    """Rebuild an evaluation from stored research with no model or inference.

    Each window's baseline must replay exactly, and its research replays frozen
    from the saved record. Candidates, metrics and baselines must equal the
    original's; the returned result carries the frozen research replays.
    """
    result = EvaluationResult.model_validate(result.model_dump())
    # Frozen mappings omit compact fields, so the original's links prove them.
    _with_links(result.comparison, store)
    datasets = tuple(datasets)
    plan = validate_plan(result.plan, datasets)
    code = code or current_code_identity()
    pairs = []
    for index, (candidate, window) in enumerate(
        zip(_selected(plan), result.comparison.windows, strict=True), 1
    ):
        replay = candidate.replay
        assert replay is not None
        inputs = [_pair_dataset(datasets, candidate.opportunity.pair)]
        baseline = run_scanner_only(replay, inputs, code=code)
        if baseline.canonical_bytes() != window.baseline.canonical_bytes():
            raise ValueError(f"window {index:03d} baseline differs from the original")
        research = await run_research_replay(
            replay,
            inputs,
            research=_frozen_settings(window.research),
            store=store,
            code=code,
        )
        pairs.append((baseline, research))
    comparison = compare_replays(pairs, store=store)
    original = result.comparison
    if (
        comparison.metrics != original.metrics
        or [w.candidate for w in comparison.windows]
        != [w.candidate for w in original.windows]
        or [w.baseline for w in comparison.windows]
        != [w.baseline for w in original.windows]
    ):
        raise ValueError("frozen reproduction differs from the original comparison")
    return _evaluation(
        plan,
        comparison,
        result.trial,
        result.max_research_runs,
        result.attempts,
    )
