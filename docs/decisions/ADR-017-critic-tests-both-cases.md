# ADR-017: The critic tests the bull and bear cases with the same scrutiny

## Status

Accepted

## Context

Every research run has an independent critic
([ADR-009](ADR-009-bounded-model-directed-research.md)). It sees the validated
thesis, which has a bull case and a bear case, in a fresh conversation. Its
instructions say to "Challenge the exact supplied thesis" and to identify
unsupported claims, contradictions, assumptions, missing evidence and unresolved
uncertainties. They do not say whether the bear case gets the same scrutiny as
the bull case.

The [no-TRADE diagnostic](../evaluations/v2-no-trade-diagnostic.md#hypotheses)
counted the critiques' challenged claims and contradictions in all 42
opportunity-led runs of trials 5, 6 and 7. In every trial the critiques weakened
the bull case more often than the bear case: 139 to 52, 83 to 9 and 87 to 15
items. The bull count was higher in 18 of 22, 10 of 10 and 10 of 10 runs. A
[blind recount](../evaluations/v2-no-trade-diagnostic.md#d5-blind-recount) by a
second coder found the same lean in all three trials.

A lean in the counts is expected when one case is weaker, so it is not by itself
a defect. The concern is that nothing in the instructions ensures the bear case
is tested at all. The same diagnostic found that every `NO_TRADE` assessment
cites specific evidence against the long. The lean does not show that any
decision was wrong, and no trade outcome or return informed this decision.

## Decision

**Critic instructions.** The critic stage instructions become exactly:

> You are the independent research critic in a fresh conversation. Challenge
> the exact supplied thesis against ALL collected evidence, request history and
> typed unavailable results. Test its bull case and its bear case with the same
> scrutiny: in each, identify unsupported claims, contradictions, assumptions,
> missing evidence and unresolved uncertainties, and say where a reading
> overstates what the evidence shows. Do not defer to the investigator. Critique
> remains textual; do not invent citation fields. Return the structured
> critique. You have no tools.

**Versions.** Stage instructions are shared by both entry modes
([ADR-014](ADR-014-trader-led-research.md)), so both critic prompts change:
`critique_prompt_v2` becomes `critique_prompt_v3`, and
`trader_critique_prompt_v1` becomes `trader_critique_prompt_v2`. The shared
policies, the other stage instructions, the `Critique` schema and its
docstrings, tools, evidence and budgets are unchanged.

**Measurement.** The next trial's record counts the direction of every
critique's challenged claims and contradictions with the diagnostic's D5
definition, coded by the builder and recounted blind, and reports it per window
and in total. It reports this as a description of the critiques, not as a
measure of decision quality.

## Rationale

The critic exists to test the thesis, and the thesis argues both ways. A critic
that mainly tests the bull case leaves the bear case's readings unchallenged
before the assessment weighs them. Asking for the same scrutiny of both cases
keeps the critic independent and adversarial, without telling it which way to
lean or how many items to raise on each side.

Alternatives considered:

- *Leave the critic as it is.* The lean holds under two independent codings in
  three trials, so it is not noise in one coder's counts, and the current
  instructions leave the bear case's testing to chance.
- *Split the `Critique` schema into bull-side and bear-side fields.* This would
  force a two-column structure on every critique, change the schema identity,
  and invite filler entries on the weaker side. Instructions can ask for the
  same scrutiny without prescribing the output's shape.
- *Add a second critic that argues for the long.* That adds a node and model
  calls to a workflow kept deliberately small, and builds in an advocate rather
  than an even-handed test.
- *Tell the assessment to discount the critique.* That weakens the independent
  check instead of balancing it.

## Consequences

- The implementing slice changes the critic stage instructions and the two
  critic prompt versions in `research_prompts`, the specification's description
  of what the critic challenges ("Evidence and independent critique"), and the
  tests that pin prompt identities.
- Both critic prompt identities change, so research policy identity changes and
  stored runs fail to load at that commit. Each still loads at its recorded
  checkout.
- The change may move decisions in either direction, including toward more
  `ABSTAIN`. With 10 windows a trial cannot attribute a change in decisions to
  this ADR alone, and its record says so.
- Trader-led runs get the same critic, as ADR-014 requires.
- Results under this policy are not pooled with earlier trials.
