"""Paired baseline/research comparison contracts; descriptive, never authority.

A comparison reads completed replay results only. It grants no financial
authority and feeds nothing back into research. Pure validation cannot prove
that referenced research artifacts exist; ``crypteo.evaluation`` resolves them.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from crypteo.domain.market import ExactDecimal, NonNegativeDecimal, TradingPair
from crypteo.domain.replay import ReplayResult
from crypteo.domain.research import DecisionOutcome, NonBlankText
from crypteo.domain.time import UtcDatetime
from crypteo.experiment import Digest, canonical_bytes

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
