# ADR-021: Research ranking is measured by a pre-registered two-stage trial

## Status

Accepted. Narrowly supersedes the stop clause of
[ADR-019](ADR-019-pre-registered-baseline-study.md) for ranking trials only.

## Context

Research declined every candidate in trials 5 to 8, so comparing its `TRADE`
selections with the baseline measures nothing. The
[baseline strategy study](../evaluations/v2-baseline-strategy-study.md) then
found that no pre-registered variant of the current scanner pays for itself, and
ADR-019's stop clause says: "No further research trial then runs on it until a
new decision changes the scanner."

[ADR-020](ADR-020-assessment-states-a-lean.md) adds a graded lean to every
model assessment. A lean can be compared with outcomes even when research never
trades, and even when the candidate source loses money overall: the question is
whether candidates research leans against turn out worse than candidates it
leans toward. Since [ADR-018](ADR-018-plan-only-authorized-candidates.md), every
planned window has a baseline trade the risk check authorized, so nearly every
window has an outcome.

## Decision

**Supersession.** Research trials may run on the current scanner's candidates
to measure ranking as defined here. ADR-019's stop clause still holds for
selection: no trial measures research's `TRADE` selections against this
scanner's baseline until a new decision changes the scanner.

**Policy.** The research policy current when ADR-020 is implemented:
`claude-opus-5-5` at effort `high`
([ADR-013](ADR-013-v2-evaluation-scope.md)), with ADR-016's budgets, ADR-017's
critic and ADR-020's lean. Economics are the V1 release economics with the
6-hour holding limit. Plans use purpose `EVALUATION` and ADR-018's rule.

**Scores and outcomes.** A lean scores `STRONGLY_AGAINST` −2, `AGAINST` −1,
`BALANCED` 0, `FOR` +1 and `STRONGLY_FOR` +2. A window's outcome is the
baseline's realized net P&L in that window. A window whose baseline position is
unresolved or whose entry is rejected is counted and left out.

**Stage 1.** The first 30 windows planned from 2026-08-01T00:00:00Z on the
trial datasets, or all of them if fewer.

**Futility.** After stage 1, if one lean value covers 80% or more of stage 1's
windows, the trial stops: research does not discriminate between these
candidates. The check reads leans only.

**Stage 2.** Otherwise, stage 2 runs N2 more windows: the August 2026 windows
after stage 1, then windows planned from 2026-10-17T00:00:00Z on a new
download, until N2 are reached or the data ends. N2 is the smaller of 30 and
the largest whole number of windows that keeps the projected total within the
budget, at stage 1's measured cost per window.

**Budget.** At most 50 USD of model spend in total, rehearsals included,
measured from the Console balance. Each stage declares its plan, run cap and
estimated cost in its TASK.md before it runs. Every stage's cap keeps the
projected total within 50 USD.

**Primary measure.** After the trial ends, Spearman's rank correlation between
score and outcome over every included window of both stages, with average ranks
for ties. Significance comes from a one-sided permutation test. One
`random.Random(20261103)` from Python's standard library shuffles a fresh copy
of the outcome list, in plan order (stage 1, then stage 2), 10,000 times, and
p = (1 + the number of permuted correlations at least as large as the observed
one) / 10,001. Research ranks better than chance when the correlation is
positive and p < 0.05. If every lean is the same, there is no ranking.

**Secondary measures**, descriptive only: per lean value and per decision, the
window count, mean and median outcome, and win rate.

**Integrity.**

- Until the trial ends, nobody computes the primary or secondary measures, or
  views outcomes beside leans. The run script reports leans, decisions, tools
  and cost, but not baseline outcomes, until its analysis step.
- Scores, outcomes, the statistic, the test, the seed, the stage sizes, the
  futility rule, the budget and the data sources are fixed here and are not
  changed after any result is seen.
- The analysis runs once, on all included windows. Results are not pooled with
  earlier trials.

## Rationale

Ranking is the question the evaluation can still answer: whether research's
judgment carries information about outcomes. It needs neither a profitable
candidate source nor `TRADE` decisions. A rank correlation suits an ordinal
lean, and a permutation test needs no assumption about the shape of trade
outcomes.

The two stages cap the cost of the likeliest failure, a lean that barely varies.
That check reads no outcome, so it cannot bias the final test. Thirty windows
per stage fits the fresh data available: August 2026 is the last uninspected
month in the trial datasets, and the data after 2026-10-16 has not been seen.
The ceiling keeps the trial within the owner's budget.

Alternatives considered:

- *One stage of 60 windows.* That spends the whole budget even when stage 1
  would already show the lean does not vary.
- *Test leans against outcomes after stage 1.* An interim test on outcomes
  needs a stricter threshold to stay honest, for little saving.
- *A cheaper model or lower effort.* That would measure a different research
  policy from the one ADR-013 pins.

## Consequences

- ADR-019's status and index entry note this narrow supersession.
- The run script gains the stage plans, the futility check, the budget formula,
  a withheld-outcome report, and an analysis step implementing the measures
  exactly. It stays outside the repository and is tested in the web session.
- Stage 2 may need a new download covering 2026-09-15 to the latest complete
  hour, for the 720-hour crossing lookback before 2026-10-17.
- With about 60 windows, only a fairly strong ranking would show. A null result
  does not show that research carries no information.
- The trial record reports every window's lean, decision and outcome, the
  primary and secondary measures, and the spend.
