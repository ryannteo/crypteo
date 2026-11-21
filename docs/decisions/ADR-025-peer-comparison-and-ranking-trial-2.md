# ADR-025: The assessment compares the candidate with its peers, and ranking trial 2 measures it

## Status

Accepted

## Context

[ADR-020](ADR-020-assessment-states-a-lean.md) added `lean` to every model
assessment: the judgment of where the balance of the collected evidence sits
for a LONG at research time.
[ADR-021](ADR-021-pre-registered-ranking-measure.md) pre-registered a trial of
whether the lean ranks the baseline's trades. In
[ranking trial 1](../evaluations/v2-ranking-trial-1.md) the lean took two values
and mirrored the decision in all 47 windows: the 36 `NO_TRADE` assessments
stated `AGAINST` and the 11 `ABSTAIN` assessments stated `BALANCED`. Spearman's
rank correlation with the outcome was −0.085206 (p ≈ 0.716). The trial
therefore compared `NO_TRADE` windows with `ABSTAIN` windows, and the lean added
no grading beyond the decision.

Two features of the lean make that likely. It asks an absolute question, where
the evidence sits for a LONG, and on this candidate source that question leaves
an absolute judgment little room to vary. In the
[trading-logic exploration](../evaluations/v2-trading-logic-exploration.md)'s
all-candidate and causal samples, candidates from 2025-07-01 to 2026-07-01, no
variant's mean before costs exceeded 3.94 USD per trade, and no variant had a
positive mean net P&L. In the
[bar-length study](../evaluations/v2-bar-length-study.md)'s primary (causal per
pair) and all-candidate samples, planned from 2022-01-01 to 2026-07-01, the
mean before costs and the mean net P&L were both negative at every bar length.
[ADR-024](ADR-024-crossing-scanner-stays-candidate-source.md) concludes from
these samples that the candidates are close to break-even at best even before
costs. And ADR-020's consistency rules bind the lean for `NO_TRADE` and
`TRADE`: `NO_TRADE` requires `AGAINST` or `STRONGLY_AGAINST`.

Ranking needs a relative judgment instead: whether this candidate looks better
or worse than others of its kind. ADR-021 notes that ranking needs neither a
profitable source nor `TRADE` decisions. The reference class already exists as
evidence. `lower_band_crossings` reports what followed this pair's earlier
scanner-rule crossings in the 720 candles ending at T, with an unconditional
baseline and the conditioned groups that
[ADR-015](ADR-015-pre-registered-crossing-base-rates.md) pre-registered,
including which group the candle ending at T falls into
([specification](../v1-specification.md#fixed-capabilities-windows-and-formulas)).

ADR-024 keeps `bollinger_lower_cross_v1` as V2's candidate source and permits
ranking trials on its candidates, each pre-registered in its own ADR.
[ADR-019](ADR-019-pre-registered-baseline-study.md)'s stop clause still bars
measuring research's `TRADE` selections against this scanner's baseline.

Trial 1's last window has its signal at 2026-11-05 15:00 UTC (SOL-USD) and ends
at 2026-11-05T21:00:00Z. Its stage-2b plan stopped at its 30th selection, so no
later candidate was planned, researched or replayed. No window reported in
`docs/evaluations/` ends after 2026-11-05T21:00:00Z.

## Decision

### The peer comparison

**Field.** `TradeDecision` gains `peer_comparison`, one of `MUCH_WORSE`,
`WORSE`, `SIMILAR`, `BETTER` and `MUCH_BETTER`: the assessment's judgment of how
this candidate, as a LONG at research time, compares with earlier candidates of
its kind from the same discovery rule. For V2's one candidate source those are
this pair's earlier lower-band crossings, as the collected evidence reports
them. It is a judgment, not a probability, forecast or expected return. The
research workflow computes nothing from it. The docstrings of `TradeDecision`
and of the levels are part of the schema: they describe peers from the same
discovery rule and name no candidate source. Of the field's texts, only the
instruction names the lower-band crossings.

**No tie to the decision.** No rule links `peer_comparison` to the `outcome`
or to the lean. `lean`, its consistency rules and its instruction sentences
stay exactly as ADR-020 defines them.

**Who states it.**

- Every opportunity-led model assessment states a peer comparison, `ABSTAIN`
  included.
- A trader-led assessment states none. A trader's question comes from no
  discovery rule, so it has no peers.
- A runtime abstention after an operational interruption states none, because
  code must not supply a judgment.

Code enforces these rules where the entry mode is known. An opportunity-led
model reply without a peer comparison, or a trader-led reply with one, is
invalid output and is re-asked like any other invalid output. Validation of
retained and stored results applies the same rules.

**Instructions.** Stage instructions stay shared by both entry modes
([ADR-014](ADR-014-trader-led-research.md)). The final-assessment stage
instructions gain exactly these sentences, immediately after ADR-020's lean
sentences:

> When context.request holds an opportunity, give peer_comparison, your
> judgment of how this candidate, as a LONG at T, compares with this pair's
> earlier lower-band crossings as the collected evidence reports them:
> MUCH_WORSE, WORSE, SIMILAR, BETTER or MUCH_BETTER. It has no required
> relation to outcome or lean, and it is not a probability or a forecast. Use
> SIMILAR when the collected evidence does not set this candidate apart from
> those crossings. When context.request holds a question, set peer_comparison
> to null.

**Versions.** `final_assessment_prompt_v3` becomes `final_assessment_prompt_v4`,
and `trader_final_assessment_prompt_v2` becomes
`trader_final_assessment_prompt_v3`. The final-assessment schema changes, and
its version becomes `final_assessment_schema_v3`. The workflow, budgets, tools,
evidence, model view, shared policies and the other stages are unchanged.
Replay records and the comparison artifact are unchanged; consumers read the
peer comparison from the stored research artifact.

### Ranking trial 2

**What it measures.** Trial 2 measures ranking with ADR-021's outcome,
statistic and test, but with its own seed (see the primary measure) and with
the score taken from `peer_comparison` instead of `lean`. This ADR applies
ADR-021's ranking-only exception to ADR-019's stop clause to that one score,
for this trial. It permits no measure of selection: the stop clause still bars
measuring research's `TRADE` selections against this scanner's baseline.

**Policy.** The research policy current when this ADR is implemented:
`claude-opus-5-5` at effort `high`
([ADR-013](ADR-013-v2-evaluation-scope.md)), with
[ADR-016](ADR-016-tool-attempts-cover-single-pair-tools.md)'s budgets,
[ADR-017](ADR-017-critic-tests-both-cases.md)'s critic, ADR-020's lean and the
peer comparison. Economics are the V1 release economics with the 6-hour holding
limit. Plans use purpose `EVALUATION`, scanner policy
`bollinger_lower_cross_v1` and
[ADR-018](ADR-018-plan-only-authorized-candidates.md)'s rule.

**Scores and outcomes.** A peer comparison scores `MUCH_WORSE` −2, `WORSE` −1,
`SIMILAR` 0, `BETTER` +1 and `MUCH_BETTER` +2. A window's outcome is the
baseline's realized net P&L in that window. A window whose baseline position is
unresolved or whose entry is rejected is counted and left out.

**Data.** Coinbase Exchange hourly candles for BTC-USD, ETH-USD and SOL-USD,
newly downloaded for each stage, starting at 2026-10-01T00:00:00Z. Planning
starts at 2026-11-06T00:00:00Z, after the last hour any reported window used,
so every candidate has at least 720 hours of earlier history in the download,
the longest lookback any tool reads.

**Rehearsal.** Before stage 1, one `PLUMBING` window on contaminated data from
before 2026-07-01, with `claude-haiku-5-5` at effort `high`, checks the live
schema and instructions. It is not part of the results. Its output is not a
reason to change the field, its levels or its instructions; such a change needs
a new ADR before stage 1 runs.

**Stage 1.** The first 30 windows planned from 2026-11-06T00:00:00Z, or all of
them if fewer, on a download that ends at the latest complete hour when stage 1
is declared. If stage 1's plan selects no window, the trial stops before any
stage 1 research run.

**Futility.** After stage 1, if one peer comparison value covers 80% or more of
stage 1's windows, the trial stops: research does not discriminate between
these candidates. The check reads peer comparisons only. If the trial stops
here, the analysis step still runs once, on stage 1's included windows: it
computes the secondary measures and not the primary measure.

**Stage 2.** Otherwise, stage 2 runs N2 more windows: the first N2 selected
candidates that follow stage 1's last selected candidate in the planner's
canonical order, in a plan that starts at that candidate's signal hour on a new
download. N2 is the smaller of 30 and the largest whole number of windows that
keeps the projected total within the budget, at stage 1's measured cost per
window. Stage 2 is declared once, on the first download whose plan reaches N2
such windows. If none has by 2026-12-21T00:00:00Z, stage 2 uses a download
ending at 2026-12-21T00:00:00Z and takes all such windows. Stage 2's plan must
decide every candidate it shares with stage 1's plan as stage 1's plan did,
except those stage 1 excluded as `WINDOW_BEYOND_DATA`. Stage 2 has no window
if N2 is 0, or if none is selected by 2026-12-21T00:00:00Z; no stage 2
research run is made, and the trial ends with stage 1's windows.

**Budget.** At most 50 USD of model spend in total, the rehearsal included,
measured from the Console balance. Each stage declares its plan, run cap and
estimated cost in its TASK.md before it runs. Every stage's cap keeps the
projected total within 50 USD.

**Primary measure.** Spearman's rank correlation between score and outcome,
with average ranks for ties, computed by the analysis after stage 2 over every
included window of both stages. If stage 2 has no window, it is computed over
stage 1's included windows. A futility stop computes no primary measure.
Significance comes from a one-sided permutation test. One
`random.Random(20261120)` from Python's standard library shuffles a fresh copy
of the outcome list, in plan order (stage 1, then stage 2), 10,000 times, and
p = (1 + the number of permuted correlations at least as large as the observed
one) / 10,001. Research's peer comparison ranks better than chance when the
correlation is positive and p < 0.05. If every peer comparison is the same,
there is no ranking.

**Secondary measures**, descriptive only, over the included windows the
analysis covers:

- per peer comparison value, per decision and per lean value: the window count,
  the mean and median outcome, and the win rate;
- the lean's Spearman correlation with the outcome over the same windows,
  without a test;
- the number of windows for each pair of lean and peer comparison values;
- for each window, whether `lower_band_crossings` was requested.

**Integrity.**

- Until the analysis step, nobody computes the primary or secondary measures,
  or views outcomes beside peer comparisons or leans. The run script reports
  peer comparisons, leans, decisions, tools and cost, but not baseline
  outcomes, until its analysis step.
- Scores, outcomes, the statistic, the test, the seed, the stage rules, the
  futility rule, the budget and the data sources are fixed here and are not
  changed after any result is seen.
- The analysis runs once, when the trial ends: after stage 2 on every included
  window of both stages, or after a futility stop on stage 1's included
  windows. Results are not pooled with ranking trial 1 or any earlier trial.

## Rationale

Ranking needs only an order. A relative judgment can vary even when the
candidates lose on average, as this source's did in the samples Context cites.
An absolute judgment has little room to vary there: in ranking trial 1 the lean
took two values and followed the decision in every window.

A new field, rather than a redefined lean, keeps the lean exactly as trial 1
measured it, and the secondary measures report both on the same windows.
Leaving the peer comparison untied to the decision matters: a candidate
research declines can still look better than most of its peers, and a tie
would recreate the mirror trial 1 found.

The reference class is evidence code already computes from the 720 candles
ending at T, so the model interprets statistics and calculates none
([ADR-001](ADR-001-deterministic-vs-llm-boundary.md)). The conditioned groups
let it place the current candle among earlier crossings. When the collected
evidence cannot set the candidate apart, for example because the run did not
request `lower_band_crossings`, `SIMILAR` is the answer that ranks it neither
above nor below its peers, and it avoids an invented comparison. If that answer
dominates, the futility check stops the trial.

Five named levels follow ADR-020's reasoning: the fewest that distinguish
strength on each side and a middle, without implying a calibrated number. The
field's name and schema describe peers from a discovery rule, not this
scanner, so evaluation logic is not tied to a market-analysis implementation;
of the field's texts, only the versioned instruction names the crossings. A
trader's question has no such peers. A condition inside the shared instruction
keeps ADR-014's single set of stage instructions.

Fresh windows in two stages keep ADR-021's protection against the likeliest
failure, a field that barely varies: the futility check can stop the trial
before stage 2 spends anything. Stage 1 uses the data available when stage 1 is
declared. Stage 2 waits for new data, and its deadline bounds how long V2's
wrap-up waits.

Alternatives considered:

- *Drop the lean's consistency rules, or redefine the lean as relative.* That
  changes ADR-020's field and breaks comparison with trial 1.
- *A rank against the other candidates in the trial.* Research would need to
  see other candidates' runs, which breaks per-run independence and the rule
  that decisions do not retrieve prior theses or decisions.
- *A percentile or a 0 to 100 score.* That suggests a precision the judgment
  does not have.
- *An `UNKNOWN` level.* It would need either a score or exclusion. Exclusion
  shrinks an already small sample and lets a run opt out of the ranking.
- *Re-run trial 1's windows with the new field.* Their outcomes have been
  inspected.
- *Add cost context or other evidence in the same trial.* Two changes in one
  trial could not be told apart. Each is left to its own decision.

## Consequences

- The implementing slice adds the peer-comparison levels and changes
  `TradeDecision` (field and docstring), the rules for which assessments state
  a peer comparison, the final-assessment instructions and both prompt
  versions, the schema version, the specification's research contract and
  outcome sections, the viewer's assessment display, and the tests that pin
  them, including a trader-led reply that states a peer comparison.
- Both final-assessment prompt identities and the schema identity change, so
  research policy identity changes and stored runs fail to load at that commit.
  Each still loads at its recorded checkout. Trial 1's viewer exports are
  pinned bundles and are unaffected
  ([ADR-022](ADR-022-local-run-viewer.md)).
- Trader-led prompts change only by the added sentences, and trader-led
  assessments still state no peer comparison.
- The run script gains the stage plans, the stop when stage 1's plan selects
  no window, the futility check on peer comparisons, the withheld-outcome
  reports and an analysis step implementing the measures exactly, with only
  the secondary measures after a futility stop. It stays outside the
  repository and is tested in the web session. Exports of trial 2 for the
  viewer are made only after its analysis, as ADR-022 requires.
- With about 50 windows, a one-sided p below 0.05 needs a correlation of
  roughly 0.24 or more. Windows in the same signal hour have correlated
  outcomes, so the effective sample is smaller. A null result does not show
  that research carries no information.
- Stage 2 may run into late December 2026.
- The trial record reports every window's peer comparison, lean, decision and
  outcome, the measures its analysis computed, and the spend, and it says when
  stage 2 had no window. It does not report `TRADE` selections against the
  baseline as evidence of research quality.
- ADR-019, ADR-020, ADR-021 and ADR-024 are unchanged. Another ranking trial,
  or another score, needs its own ADR.
