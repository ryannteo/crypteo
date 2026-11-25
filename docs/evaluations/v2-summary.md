# V2 summary: what was built, what the evaluation found, what stays open

## Summary

V2 turned V1's historical research replay into an evaluation lab and ran it on
Coinbase data from after the research model's training cutoff. In every
model-backed evaluation trial, trials 5 to 8 and both ranking trials, research
declined every candidate it assessed. No run returned `TRADE`. A constant "never
trade" rule gives the same result, so whether research selects better trades is
unmeasured. Two pre-registered studies and an exploration found no edge after
costs in the candidate source, in every sample that traded every candidate or
took trades one at a time. Two pre-registered ranking trials did not show
ranking either. Both graded judgments collapsed to one or two values. What V2 does show is that the harness works end to end:
every model-backed evaluation run reproduced from its stored artifacts with no
API key.

On this candidate source, V2 cannot tell whether research improves or worsens
trade selection.

This record adds no new result. Every figure comes from the record, ADR or
specification it links, except one test's 0.25 s wait, which comes from the
test file it links. The only arithmetic here is counting and adding figures
those sources state. Trials ran under different research policies, so tallies
across trials are descriptive and are not pooled as evidence.

## What V2 built

V2 kept V1's single replay lifecycle and bounded research workflow and added:

- **A mechanical window planner.** It derives single-candidate evaluation
  windows from point-in-time scanner output without reference to outcomes,
  and records a reason for every excluded candidate
  ([ADR-013](../decisions/ADR-013-v2-evaluation-scope.md)). It plans only
  candidates the baseline would trade at the signal hour
  ([ADR-018](../decisions/ADR-018-plan-only-authorized-candidates.md),
  [specification](../v1-specification.md#evaluation-window-planning)).
- **A paired comparison artifact and a resumable runner.** The comparison pairs
  each window's scanner-only and research replays and recomputes its metrics
  ([specification](../v1-specification.md#baselineresearch-comparison)). The
  runner saves each window as it completes, resumes without repeating completed
  research, and rebuilds a result from frozen research with no model or API key
  ([specification](../v1-specification.md#evaluation-runner)).
- **Trader-led research.** A trader's question about a LONG in one pair runs on
  the same bounded workflow and stays outside replay and evaluation
  ([ADR-014](../decisions/ADR-014-trader-led-research.md)).
- **Evidence and workflow changes, each recorded or pre-registered.** Trial 6
  added candle-structure, higher-timeframe and lower-band crossing tools; the
  crossing lookback and horizon and the higher-timeframe block sizes were fixed
  before any trial-6 run
  ([trial 6](v2-july-2026-trial-6.md#what-was-run)). Then came pre-registered
  conditioned crossing base rates
  ([ADR-015](../decisions/ADR-015-pre-registered-crossing-base-rates.md)), a
  tool budget that covers every tool offered in a single-pair window
  ([ADR-016](../decisions/ADR-016-tool-attempts-cover-single-pair-tools.md)),
  and a critic that tests the bull and bear cases with the same scrutiny
  ([ADR-017](../decisions/ADR-017-critic-tests-both-cases.md)).
- **Two graded judgments in the assessment.** The lean says where the evidence
  sits for a LONG ([ADR-020](../decisions/ADR-020-assessment-states-a-lean.md)).
  The peer comparison says how the candidate compares with earlier candidates
  from the same discovery rule
  ([ADR-025](../decisions/ADR-025-peer-comparison-and-ranking-trial-2.md)).
- **Pre-registered studies of the candidate source.** A baseline strategy study
  ([ADR-019](../decisions/ADR-019-pre-registered-baseline-study.md)) and a
  bar-length study
  ([ADR-023](../decisions/ADR-023-pre-registered-bar-length-study.md)), both
  scanner-only with no model.
- **A local read-only run viewer.** `crypteo_viewer` exports stored evaluations
  and runs to a hashed bundle and renders static pages, outside `crypteo`'s
  code identity ([ADR-022](../decisions/ADR-022-local-run-viewer.md)).

## What the evaluation found

### Research declined every candidate it assessed

Every evaluation run below completed with a model assessment. The table counts
evaluation runs only. Each trial's one-window `PLUMBING` rehearsal on
contaminated data, and trial 5's four aborted attempts, are not part of its
results and are not counted.

| Record | Main policy change | Runs | Candidates | `TRADE` | `NO_TRADE` | `ABSTAIN` |
|---|---|---|---|---|---|---|
| [Trial 5](v2-july-2026.md#results) | First evaluation under [ADR-013](../decisions/ADR-013-v2-evaluation-scope.md) | 22 | 22 | 0 | 19 | 3 |
| [Trial 6](v2-july-2026-trial-6.md#results) | Three added tools, per-tool descriptions and a compact evidence view; re-ran trial 5's first 10 windows | 10 | 10 | 0 | 9 | 1 |
| [Trial 7](v2-july-2026-trial-7.md#results) | Conditioned crossing base rates (ADR-015), with tool-description and evidence-window changes | 10 | 10 | 0 | 6 | 4 |
| [Trial 8](v2-july-2026-trial-8.md#results) | Nine tool attempts and the both-sided critic (ADR-016, ADR-017) | 10 | 10 | 0 | 4 | 6 |
| [Ranking trial 1](v2-ranking-trial-1.md#results) | The lean (ADR-020) | 47 | 47 | 0 | 36 | 11 |
| [Ranking trial 2](v2-ranking-trial-2.md#results) | The peer comparison (ADR-025) | 30 | 30 | 0 | 29 | 1 |

Totals, as a descriptive tally across six different research policies:

- Trials 5 to 8: 52 runs on 42 distinct candidates, because trial 6 re-ran 10
  of trial 5's windows. [ADR-019](../decisions/ADR-019-pre-registered-baseline-study.md#context)
  and [ADR-020](../decisions/ADR-020-assessment-states-a-lean.md#context) state
  the same totals.
- Both ranking trials: 77 runs on 77 candidates.
- All six records: 129 runs on 119 distinct candidates, with 0 `TRADE`, 103
  `NO_TRADE` and 26 `ABSTAIN`. The candidates are distinct across records
  apart from trial 6's re-run, because their signal ranges do not overlap:
  July 2026 for trials 5 to 8, August and 2026-10-17 to 2026-11-05 for
  [ranking trial 1](v2-ranking-trial-1.md#how-the-windows-were-chosen), and
  2026-11-07 to 2026-11-19 for
  [ranking trial 2](v2-ranking-trial-2.md#how-the-windows-were-chosen).

A constant "never trade" rule gives the same result, so selection quality is
unmeasured. Each record says so
([trial 5](v2-july-2026.md#interpretation),
[ranking trial 2](v2-ranking-trial-2.md#interpretation)), and the earlier
records say why research declined. In trial 5 the evidence set gave research no
route to a well-supported `TRADE`
([trial 5](v2-july-2026.md#why-research-declined)). Trial 6's new tools filled
the gaps it cited, and the model read the added evidence mostly against the
long ([trial 6](v2-july-2026-trial-6.md#summary)).

### The candidate source has no edge after costs

V2's opportunity-led candidates come from one scanner: an hourly close crossing
below the lower Bollinger band, `bollinger_lower_cross_v1`, with deterministic
terms and a 6-hour holding limit. No model was called in any of these three
studies. [ADR-024](../decisions/ADR-024-crossing-scanner-stays-candidate-source.md#context)
summarises them:

- The [baseline strategy study](v2-baseline-strategy-study.md#summary), planned
  from 2025-07-01 to 2026-07-01, ran four pre-registered holding-limit and
  trend-filter variants. All four had a negative mean net P&L per closed
  trade, and the best was −10.49 USD. Its selection was STOP.
- The [trading-logic exploration](v2-trading-logic-exploration.md#summary), on
  the same data, ran 144 entry, exit and filter variants on hourly bars. It was
  exploratory and not pre-registered. No variant had a positive mean net P&L
  per trade when every candidate was traded, or when trades were taken one at
  a time. In those samples no mean before costs exceeded 3.94 USD per trade,
  while costs took 31.62 to 39.74 USD per trade with the current stop.
  Positive means appeared only under the planning rule's overlap check, which
  the exploration found flatters the sample.
- The [bar-length study](v2-bar-length-study.md#summary), planned from
  2022-01-01 to 2026-07-01, ran the crossing on 1-hour, 4-hour and daily bars.
  In its primary sample, causal per pair, costs per trade fell from 31.75 to
  19.81 and 8.00 USD, but the mean before costs was negative at every bar
  length: −2.57, −5.55 and −6.83 USD. Its selection was STOP.

So the no-edge finding holds in the samples that trade every candidate or take
trades one at a time. There, ADR-024 concludes, these candidates are close to
break-even at best even before costs. It does not hold under the planning
rule's overlap check, which flatters the sample. ADR-024 keeps the scanner as
V2's candidate source and ends the search for a better one in V2.

### Ranking was not shown

Ranking asks whether research's graded judgment orders the baseline's trades by
outcome. It needs neither a profitable source nor `TRADE` decisions
([ADR-021](../decisions/ADR-021-pre-registered-ranking-measure.md#rationale)).

- **Ranking trial 1 (the lean).** Over 47 windows, the lean took two values and
  mirrored the decision in every window: 36 `NO_TRADE` assessments stated
  `AGAINST` and 11 `ABSTAIN` assessments stated `BALANCED`. Spearman's rank
  correlation between lean score and baseline net P&L was ρ = −0.085206, with
  p = 7161/10001 ≈ 0.716 in the pre-registered one-sided permutation test: no
  evidence that research ranks better than chance
  ([record](v2-ranking-trial-1.md#results)).
- **Ranking trial 2 (the peer comparison).** In stage 1's 30 windows the peer
  comparison was `SIMILAR` in 27 and `WORSE` in 3. One value covered 90%, over
  the pre-registered 80% futility threshold, so the trial stopped after stage 1
  and no ranking was measured. The lean was `AGAINST` in all 30
  ([record](v2-ranking-trial-2.md#results)).

Both graded judgments collapsed to one or two values: the lean to two values in
trial 1 and one in trial 2, the peer comparison to two. Neither result shows
that research carries no information. A null result in trial 1 does not show it
([ADR-021](../decisions/ADR-021-pre-registered-ranking-measure.md#consequences)),
and trial 2's stop shows only that this judgment did not vary enough on these
candidates to be tested ([record](v2-ranking-trial-2.md#interpretation)).

### What V2 does show

The harness works end to end on post-cutoff data. Every evaluation window falls
in ADR-013's evaluation period, which starts at 2026-07-01, after the model's
June 2026 training cutoff
([ADR-013](../decisions/ADR-013-v2-evaluation-scope.md#context),
[periods](../decisions/ADR-013-v2-evaluation-scope.md#decision)). In every
record a separate process with `ANTHROPIC_API_KEY` unset reproduced each
evaluation from its stored artifacts. Candidate decisions and metrics matched
exactly:

- [Trial 5](v2-july-2026.md#reproduction-and-cost),
  [trial 6](v2-july-2026-trial-6.md#reproduction-and-cost),
  [trial 7](v2-july-2026-trial-7.md#reproduction-and-cost) and
  [trial 8](v2-july-2026-trial-8.md#reproduction-and-cost).
- [Ranking trial 1](v2-ranking-trial-1.md#reproduction-and-cost) and
  [ranking trial 2](v2-ranking-trial-2.md#reproduction-and-cost), each with its
  rehearsal.

Identities were checked. Frozen reproduction itself requires each artifact's
producer code to match ([architecture](../architecture.md#replay-and-artifacts)).
Trials 7 and 8 also record that every stored run carries the run commit's code
identity. Both ranking trials record an `identity` check that exited 0 for
every stored run, 48 and 31 runs including rehearsals. The
[no-TRADE diagnostic](v2-no-trade-diagnostic.md#stores-and-commits) loaded every
trial-5, trial-6 and trial-7 run at its commit.

### The bottom line

On this candidate source, V2 cannot tell whether research improves or worsens
trade selection. Research never traded, so selection is unmeasured. The two
graded judgments meant to measure ranking instead collapsed to one or two
values. The source itself has no edge after costs in the samples above.

## Spend

Model spend each record states, with its scope:

| Record | Spend (USD) | Scope | Not included |
|---|---|---|---|
| [Trial 5](v2-july-2026.md#reproduction-and-cost) | 18.05 | Console, completed run | Rehearsal and four aborted attempts, which the record states separately as "about 5.46 USD" |
| [Trial 6](v2-july-2026-trial-6.md#reproduction-and-cost) | 5.08 | Console, rehearsal and run | — |
| [Trial 7](v2-july-2026-trial-7.md#reproduction-and-cost) | 7.08 | Console, run only | Rehearsal, "about 0.01 USD", paid from an earlier balance |
| [Trial 8](v2-july-2026-trial-8.md#reproduction-and-cost) | 7.67 | Console, run only | Rehearsal, "about 0.01 USD", paid before a top-up |
| [Ranking trial 1](v2-ranking-trial-1.md#reproduction-and-cost) | 29.10 | Console, rehearsal and both stages | — |
| [Ranking trial 2](v2-ranking-trial-2.md#reproduction-and-cost) | 18.56 | Console, rehearsal and stage 1 | A cancelled first rehearsal start, outside the measured total; the record bounds it below one cent |

The Console figures add up to 85.54 USD. Adding the approximate figures the
records state for what those figures leave out, trial 5's 5.46 USD and two
rehearsals at 0.01 USD each, gives about 91.02 USD. The cancelled start in
ranking trial 2 has no stated figure and is not estimated here.

Outside this total:

- The [v2.0.0 release run](../releases/v2.0.0.md#cost), whose Console balance
  fell by 1.09 USD. It is a release check on a candidate the V1 release had
  already inspected, not part of V2's evaluation.
- The baseline strategy study, the trading-logic exploration and the bar-length
  study called no model. The no-TRADE diagnostic states no spend.

## What stays open

- **Cost context for research.** Trial 6's assessments and critiques cited
  transaction cost context for small mean returns in 8 of 10 windows
  ([trial 6](v2-july-2026-trial-6.md#why-research-declined)). ADR-024 leaves
  cost context to its own decision
  ([ADR-024](../decisions/ADR-024-crossing-scanner-stays-candidate-source.md#rationale)).
- **Further evidence gaps the critics raised.** The trial records count gaps
  cited by the stored critiques and the other stages.
  - *Relative-strength benchmark.* Single-pair windows bind no benchmark, so it
    was unavailable in every window of trials 5 to 8
    ([trial 5](v2-july-2026.md#why-research-declined),
    [trial 6](v2-july-2026-trial-6.md#why-research-declined),
    [trial 7](v2-july-2026-trial-7.md#why-research-declined),
    [trial 8](v2-july-2026-trial-8.md#why-research-declined)).
  - *Higher-timeframe view and trigger-bar structure.* Trial 5 cited both as
    missing ([trial 5](v2-july-2026.md#why-research-declined)), and trial 6
    added tools for them. Critics then questioned whether the daily trend bears
    on a 6-bar horizon ([trial 6](v2-july-2026-trial-6.md#why-research-declined)).
  - *Conditioned base rates.* Later trials asked for crossing base rates
    conditioned on more than ADR-015's two features, including the daily trend
    and the trigger candle's anatomy: 5 of 10 windows in
    [trial 7](v2-july-2026-trial-7.md#why-research-declined) and 7 of 10 in
    [trial 8](v2-july-2026-trial-8.md#why-research-declined).
- **A candidate source with an edge, and live research.** Both are outside V2.
  ADR-024 ends the candidate-source search in V2 and leaves later versions free
  to add other discovery mechanisms
  ([ADR-024](../decisions/ADR-024-crossing-scanner-stays-candidate-source.md#decision)).
  Live mode is out of V2 scope
  ([ADR-013](../decisions/ADR-013-v2-evaluation-scope.md#decision)).
- **Run scripts outside the repository.** The trial and study scripts are kept
  outside the repository, so CI runs neither them nor their tests. Their
  records, saved outputs and checksums are their evidence
  ([ranking trial 1](v2-ranking-trial-1.md#limitations),
  [ADR-021](../decisions/ADR-021-pre-registered-ranking-measure.md#consequences)).
- **The data source's terms.** Coinbase's Market Data Terms of Use restrict
  display to third parties. Viewer bundles and pages built from real runs stay
  local and are never published or hosted, and the viewer's tests use synthetic
  runs, so any public screenshot has to use synthetic data
  ([ADR-022](../decisions/ADR-022-local-run-viewer.md#context),
  [boundaries](../decisions/ADR-022-local-run-viewer.md#decision)).
- **Small engineering items.**
  - The deferred reorganisation of the test suite.
  - The `attempts_remaining` validation gap.
  - Haiku 5.5's cache-read price. Ranking trial 1's script assumes 0.1× the
    input rate, which affects only the rehearsal's estimate
    ([ranking trial 1](v2-ranking-trial-1.md#reproduction-and-cost)).
  - The 0.25 s waits in
    [`test_external_cancellation_propagates_even_when_provider_suppresses_it`](../../tests/test_research_workflow_repairs.py),
    which may be too short on slow runners.
