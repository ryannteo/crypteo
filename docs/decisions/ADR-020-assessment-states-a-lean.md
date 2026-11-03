# ADR-020: The assessment states a graded lean on the evidence

## Status

Accepted

## Context

The final assessment gives one of `TRADE`, `NO_TRADE` or `ABSTAIN`, and a
confidence that rates the quality of the research, not the direction of the
evidence
([specification](../v1-specification.md#research-contract-validation)). Across
trials 5 to 8, research declined all 42 distinct candidates it assessed, in 52
runs. With only declines, the outcome cannot show whether research tells better
candidates from worse ones: a constant "never trade" rule gives the same result.

The declines are not all alike. The
[no-TRADE diagnostic](../evaluations/v2-no-trade-diagnostic.md) found that every
`NO_TRADE` cites specific evidence against the long, and the abstentions cite
missing or conflicting evidence. Neither the outcome nor the confidence records
how strongly the evidence leans.

## Decision

**Field.** `TradeDecision` gains `lean`, one of `STRONGLY_AGAINST`, `AGAINST`,
`BALANCED`, `FOR` and `STRONGLY_FOR`: the assessment's judgment of where the
balance of the collected evidence sits for a LONG at research time. It is a
judgment, not a probability, forecast or expected return. The research workflow
computes nothing from it.

**Consistency.** `TRADE` requires `FOR` or `STRONGLY_FOR`, and `NO_TRADE`
requires `AGAINST` or `STRONGLY_AGAINST`. `ABSTAIN` may use any value. Every
model assessment states a lean. A runtime abstention after an operational
interruption states none, because code must not supply a judgment.

**Instructions.** The final-assessment stage instructions gain exactly these
sentences:

> Give lean, your judgment of where the balance of the collected evidence sits
> for a LONG at T: STRONGLY_AGAINST, AGAINST, BALANCED, FOR or STRONGLY_FOR.
> TRADE requires FOR or STRONGLY_FOR, NO_TRADE requires AGAINST or
> STRONGLY_AGAINST, and ABSTAIN may use any value. Lean is not a probability or
> a forecast.

**Versions.** `final_assessment_prompt_v2` becomes `final_assessment_prompt_v3`,
and `trader_final_assessment_prompt_v1` becomes
`trader_final_assessment_prompt_v2`. The final-assessment schema changes, and
its version becomes `final_assessment_schema_v2`. The workflow, budgets, tools,
evidence, model view and the other stages are unchanged. Replay records and the
comparison artifact are unchanged; consumers read the lean from the stored
research artifact.

## Rationale

A graded judgment keeps information that a three-way outcome discards. Five
levels are the fewest that distinguish strength on each side and a balanced
middle. Named levels rather than numbers keep the lean a judgment and avoid
implying a calibrated probability; code, not the model, turns levels into
scores where an evaluation needs them. The consistency rules stop the lean and
the outcome from contradicting each other.

Alternatives considered:

- *Reuse confidence.* Confidence rates research quality, not direction.
  Changing its meaning would break comparison with every earlier run.
- *Ask for a probability that the target is reached.* That asks the model for a
  calibrated forecast, which the assessment is defined not to give.
- *A 0 to 100 score.* That suggests a precision the judgment does not have.
- *A separate stage that rates the lean.* That adds a model call to every run.

## Consequences

- The implementing slice changes `TradeDecision` (field, consistency rules and
  docstring), the rule that model assessments state a lean and runtime
  abstentions do not, the final-assessment instructions and versions, the
  schema version, the specification, and the tests that pin them.
- Prompt and schema identities change, so research policy identity changes and
  stored runs fail to load at that commit. Each still loads at its recorded
  checkout.
- Trader-led assessments state a lean too, because stage instructions are
  shared ([ADR-014](ADR-014-trader-led-research.md)).
- [ADR-021](ADR-021-pre-registered-ranking-measure.md) defines how evaluation
  uses the lean.
