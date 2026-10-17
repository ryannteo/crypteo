"""Evaluation window plans, paired comparisons and executed evaluation contracts.

A window plan derives replay windows from scanner candidates only. A comparison
reads completed replay results only. An evaluation result binds a comparison to
its plan and research attempts. None grants financial authority or feeds
anything back into research. Pure validation cannot prove that referenced
research artifacts exist or that a plan covers its datasets;
``crypteo.evaluation`` resolves both.
"""

from datetime import UTC, datetime
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from crypteo.domain.execution import ExecutionConfig
from crypteo.domain.market import ExactDecimal, NonNegativeDecimal, TradingPair
from crypteo.domain.replay import ReplayResult
from crypteo.domain.research import DecisionOutcome, NonBlankText, Opportunity
from crypteo.domain.risk import RiskConfig
from crypteo.domain.time import UtcDatetime
from crypteo.domain.trade_terms import TradeTermsConfig
from crypteo.experiment import (
    ASSET_PRIORITY,
    SCANNER_POLICY,
    DatasetIdentity,
    Digest,
    ReplayConfig,
    canonical_bytes,
)

FinancialStatus = Literal[
    "NOT_SELECTED",
    "TERMS_REJECTED",
    "RISK_REJECTED",
    "ENTRY_REJECTED",
    "CLOSED",
    "OPEN_AT_END",
    "DATA_GAP",
]


class FinancialOutcome(BaseModel):
    """One mode's terminal result for the candidate; only CLOSED carries P&L."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )
    status: FinancialStatus
    exit_reason: Literal["STOP", "TARGET", "TIME"] | None = None
    net_pnl: ExactDecimal | None = None

    @model_validator(mode="after")
    def validate_closed(self) -> Self:
        closed = self.status == "CLOSED"
        if closed != (self.exit_reason is not None) or closed != (
            self.net_pnl is not None
        ):
            raise ValueError("exactly CLOSED outcomes carry exit reason and net P&L")
        return self


class CandidateComparison(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )
    opportunity_id: NonBlankText
    pair: TradingPair
    observed_at: UtcDatetime
    outcome: DecisionOutcome
    confidence: int | None = Field(default=None, strict=True, ge=0, le=100)
    baseline: FinancialOutcome
    research: FinancialOutcome

    @model_validator(mode="after")
    def validate_selection(self) -> Self:
        if self.baseline.status == "NOT_SELECTED":
            raise ValueError("baseline selects every admitted candidate")
        if (self.research.status == "NOT_SELECTED") != (
            self.outcome != DecisionOutcome.TRADE
        ):
            raise ValueError("research is NOT_SELECTED exactly when not TRADE")
        if self.outcome == DecisionOutcome.TRADE and self.research != self.baseline:
            raise ValueError("research TRADE must reproduce the baseline outcome")
        return self


class WindowComparison(BaseModel):
    """One single-candidate window replayed in scanner-only and research mode."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )
    baseline: ReplayResult
    research: ReplayResult
    candidate: CandidateComparison

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        # Local import, as ReplayResult does with replay_metrics.
        from crypteo.evaluation import compare_window

        if compare_window(self.baseline, self.research) != self.candidate:
            raise ValueError("candidate comparison must match paired replay results")
        return self


class ClosedOutcomeGroup(BaseModel):
    """Baseline CLOSED outcomes for one research decision group."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )
    net_pnl: ExactDecimal
    wins: int = Field(strict=True, ge=0)
    losses: int = Field(strict=True, ge=0)
    breakevens: int = Field(strict=True, ge=0)
    win_rate: NonNegativeDecimal | None


class ComparisonMetrics(BaseModel):
    """Descriptive counts and exact sums; no significance or calibration claim.

    ``taken`` groups baseline CLOSED outcomes where research chose TRADE;
    ``declined`` groups those where it chose NO_TRADE or ABSTAIN. Win rate is a
    fraction rounded half-even to 50 significant digits, None when empty.
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )
    window_count: int = Field(strict=True, ge=0)
    trade_count: int = Field(strict=True, ge=0)
    no_trade_count: int = Field(strict=True, ge=0)
    abstain_count: int = Field(strict=True, ge=0)
    baseline_closed_count: int = Field(strict=True, ge=0)
    research_closed_count: int = Field(strict=True, ge=0)
    baseline_realized_net_pnl: ExactDecimal
    research_realized_net_pnl: ExactDecimal
    taken: ClosedOutcomeGroup
    declined: ClosedOutcomeGroup
    unresolved_count: int = Field(strict=True, ge=0)


class ResearchComparison(BaseModel):
    """Canonically ordered windows under one code and research policy identity."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )
    windows: tuple[WindowComparison, ...] = Field(min_length=1)
    metrics: ComparisonMetrics
    comparison_id: Digest

    @model_validator(mode="after")
    def validate_comparison(self) -> Self:
        from crypteo.evaluation import (
            comparison_identity,
            comparison_metrics,
            validate_windows,
        )

        validate_windows(self.windows)
        if self.metrics != comparison_metrics(
            tuple(window.candidate for window in self.windows)
        ):
            raise ValueError("metrics must match window candidate comparisons")
        if self.comparison_id != comparison_identity(self.windows):
            raise ValueError("comparison_id must match ordered window run IDs")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self)


# ADR-013: earlier hourly boundaries are inside the research model's training data.
EVALUATION_PERIOD_START: Final = datetime(2026, 7, 1, tzinfo=UTC)

ExclusionReason = Literal[
    "NOT_AVAILABLE_AT_OBSERVATION", "WINDOW_BEYOND_DATA", "OVERLAPPING_CANDIDATE"
]


class WindowPlanSettings(BaseModel):
    """Planning range, selection cap and the economics shared by every window.

    ``PLUMBING`` permits contaminated pre-evaluation data for tests and
    integration checks; ``EVALUATION`` requires the ADR-013 evaluation period.
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )
    purpose: Literal["EVALUATION", "PLUMBING"]
    planning_start: UtcDatetime
    limit: int = Field(strict=True, gt=0)
    initial_cash: NonNegativeDecimal
    trade_terms: TradeTermsConfig
    risk: RiskConfig
    execution: ExecutionConfig

    @model_validator(mode="after")
    def validate_settings(self) -> Self:
        start = self.planning_start
        if start.minute or start.second or start.microsecond:
            raise ValueError("planning_start must align to an hour")
        if self.purpose == "EVALUATION" and start < EVALUATION_PERIOD_START:
            raise ValueError("EVALUATION planning must start in the evaluation period")
        if self.trade_terms.price_increment != self.execution.price_increment:
            raise ValueError("trade terms and execution price increments must match")
        return self


class PlannedCandidate(BaseModel):
    """One scanner candidate: SELECTED with its window, or EXCLUDED with a reason."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )
    opportunity: Opportunity
    decision: Literal["SELECTED", "EXCLUDED"]
    exclusion_reason: ExclusionReason | None = None
    replay: ReplayConfig | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        selected = self.decision == "SELECTED"
        if selected != (self.exclusion_reason is None) or selected != (
            self.replay is not None
        ):
            raise ValueError("exactly SELECTED candidates carry a replay and no reason")
        opportunity = self.opportunity
        if (opportunity.available_at > opportunity.observed_at) != (
            self.exclusion_reason == "NOT_AVAILABLE_AT_OBSERVATION"
        ):
            raise ValueError(
                "NOT_AVAILABLE_AT_OBSERVATION must match the opportunity's own times"
            )
        return self


class WindowPlan(BaseModel):
    """Canonically ordered candidates from ``planning_start`` to the last selection.

    Pure validation recomputes each window and every listed fact; only
    ``crypteo.evaluation.validate_plan`` can prove coverage of the datasets.
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )
    settings: WindowPlanSettings
    scanner_policy: str
    datasets: tuple[DatasetIdentity, ...] = Field(min_length=1)
    candidates: tuple[PlannedCandidate, ...]
    plan_id: Digest

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        from crypteo.evaluation import plan_identity, validate_planned_candidates

        if self.scanner_policy != SCANNER_POLICY:
            raise ValueError("scanner policy must match the implemented policy")
        pairs = tuple(item.pair for item in self.datasets)
        if pairs != tuple(pair for pair in ASSET_PRIORITY if pair in pairs):
            raise ValueError("plan datasets must be unique in canonical pair order")
        validate_planned_candidates(self.settings, pairs, self.candidates)
        if self.plan_id != plan_identity(
            self.settings, self.scanner_policy, self.datasets, self.candidates
        ):
            raise ValueError("plan_id must match the plan contents")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self)


class EvaluationAttempt(BaseModel):
    """One reserved fresh research run for a planned window, made before inference."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )
    opportunity_id: NonBlankText
    attempt: int = Field(strict=True, ge=1)
    trial_id: NonBlankText


class EvaluationResult(BaseModel):
    """A comparison bound to the plan it executed and every reserved attempt.

    Pure validation cannot prove that research artifacts exist or that the plan
    covers its datasets; ``crypteo.evaluation`` resolves both.
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )
    plan: WindowPlan
    comparison: ResearchComparison
    trial: NonBlankText
    max_research_runs: int = Field(strict=True, gt=0)
    attempts: tuple[EvaluationAttempt, ...]
    evaluation_id: Digest

    @model_validator(mode="after")
    def validate_evaluation(self) -> Self:
        from crypteo.evaluation import evaluation_identity, validate_evaluation

        validate_evaluation(
            self.plan,
            self.comparison,
            self.trial,
            self.max_research_runs,
            self.attempts,
        )
        if self.evaluation_id != evaluation_identity(
            self.plan,
            self.comparison,
            self.trial,
            self.max_research_runs,
            self.attempts,
        ):
            raise ValueError("evaluation_id must match the evaluation contents")
        return self

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self)
