# ADR-024: V2 keeps the crossing scanner and ends the candidate-source search

## Status

Accepted

## Context

V2's opportunity-led candidates come from one scanner: an hourly close crossing
below the lower Bollinger band (`bollinger_lower_cross_v1`), with deterministic
terms and a 6-hour holding limit. Two pre-registered studies and one
exploration have now tested whether a change to it pays for itself at the V1
release costs:

- The [baseline strategy study](../evaluations/v2-baseline-strategy-study.md)
  ([ADR-019](ADR-019-pre-registered-baseline-study.md)) ran four holding-limit
  and trend-filter variants. All four had a negative mean net P&L per trade;
  the best was −10.49 USD. Its selection was STOP.
- The [trading-logic exploration](../evaluations/v2-trading-logic-exploration.md)
  ran 144 entry, exit and filter variants on hourly bars. None had a positive
  mean when every candidate was traded or when trades were taken one at a time.
  In those samples no mean before costs exceeded 3.94 USD per trade, while
  costs took 31.62 to 39.74 USD per trade with the current stop.
- The [bar-length study](../evaluations/v2-bar-length-study.md)
  ([ADR-023](ADR-023-pre-registered-bar-length-study.md)) ran the crossing on
  1-hour, 4-hour and daily bars. Longer bars cut costs per trade from 31.75 to
  19.81 and 8.00 USD, but the mean before costs in the primary sample was
  negative at every bar length: −2.57, −5.55 and −6.83 USD. Its selection was
  STOP.

The mean before costs uses no fees and no slippage. When every candidate is
traded, or trades are taken one at a time, it was at most 3.94 USD per trade in
the exploration and negative at every bar length in the bar-length study, so
even without costs these candidates are close to break-even at best. Under the
planning rule's overlap check most variants are positive before costs, but the
exploration showed that the rule flatters the sample.

On this source, a comparison of research's `TRADE` selections with the baseline
cannot tell judgment from refusal. Research declined every candidate in trials
5 to 8 and in [ranking trial 1](../evaluations/v2-ranking-trial-1.md), and a
constant "never trade" rule gives the same result. ADR-019's stop clause
already bars such trials on this scanner.
[ADR-021](ADR-021-pre-registered-ranking-measure.md) allows ranking trials,
because ranking needs neither a profitable source nor `TRADE` decisions. In
ranking trial 1 the lean took two values and mirrored the decision in every
window, and its rank correlation with outcomes was −0.085 (p ≈ 0.716).

## Decision

**Candidate source.** For the rest of V2, opportunity-led candidates come from
`bollinger_lower_cross_v1` on hourly bars, with the current trade terms, the
6-hour holding limit and the V1 release economics. Evaluation windows are still
planned as [ADR-018](ADR-018-plan-only-authorized-candidates.md) and
[ADR-013](ADR-013-v2-evaluation-scope.md) define them.

**The search ends.** No further study of triggers, filters, entries, exits,
holding limits or bar lengths runs in V2 to find a candidate source that pays
for itself. Reopening the search needs a new ADR that says why. Any change to
the candidate source also needs its own decision and, as in ADR-019 and
ADR-023, a pre-registered study before the change.

**Use of these candidates.** They serve plumbing, determinism and integration
checks, and research trials that measure ranking, as ADR-021 permits. Each such
trial is pre-registered in its own ADR, as ADR-021 was. ADR-019's stop clause
still holds for everything else: no trial measures research's `TRADE`
selections against this scanner's baseline, and a trial measuring anything
other than ranking would need its own ADR to narrow the clause, as ADR-021
did.

**Next work.** V2's next evaluation work concerns research's output and
evidence, not the candidate source. This decision chooses no such change; each
needs its own decision.

**Scope.** This decision covers V2's evaluation. It does not restrict the
deterministic scanner or other discovery mechanisms a later version may add for
opportunity-led research.

## Rationale

No tested change to the crossing, across holding limits, filters, entries,
exits, targets, stops and bar lengths, showed a measurable edge before costs in
a causal or all-candidate sample. Another trigger family is a new search
with a weak prior, and each search adds to the chance that a winner is noise.
Evaluation exists to test whether research improves trade selection, and
ranking can test that on the current candidates. Ending the search here keeps
V2's remaining effort on research.

Alternatives considered:

- *One more pre-registered study of a different trigger, with a stop that ends
  the search if it fails.* It is the only option that could change the source's
  economics. But it costs a study, it adds multiple testing on a weak prior,
  and a winner would arrive slowly on fresh data: in the bar-length study's
  primary sample, 4-hour bars closed 13.76 trades per 30 days across the three
  pairs and daily bars 1.95.
- *A lower cost assumption.* It is a separate decision, and choosing costs by
  their effect on results would defeat the studies. A lower assumption could
  turn some overlap-rule results positive, but in the causal and all-candidate
  samples these candidates are close to break-even at best even before costs.
- *Cost context for research, or a different data source.* Neither changes the
  candidate source. Each is left to its own decision.
- *End opportunity-led evaluation.* That would remove the candidates that
  plumbing checks and ranking trials use.

## Consequences

- No code, configuration or identity changes. The scanner policy, trade terms
  and plan settings are unchanged, so plan IDs and replay `run_id`s are not
  affected.
- ADR-019, ADR-021 and ADR-023 are unchanged. ADR-019's stop clause still holds
  for selection, and ADR-023's outcome stands.
- The study scripts outside the repository run no further study. Their records
  stay as they are.
- A research trial on these candidates needs its own ADR before it runs, and
  measures ranking unless that ADR narrows ADR-019's stop clause further. Its
  record does not report `TRADE` selections against the baseline as evidence of
  research quality.
- Reopening the candidate-source search, or changing the economics, needs a new
  ADR that supersedes this one in that part.
