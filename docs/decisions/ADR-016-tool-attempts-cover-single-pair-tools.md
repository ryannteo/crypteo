# ADR-016: Tool attempts cover every tool in a single-pair window

## Status

Accepted

## Context

The bounded research workflow allows 8 admitted tool attempts per run
([ADR-009](ADR-009-bounded-model-directed-research.md),
[specification](../v1-specification.md#investigation-policy-and-graph)).
Through trial 5 a single-pair evaluation window was offered at most seven tools,
so every tool fit. Commit `8fe2f93` added three capabilities for trial 6. Since
then a single-pair window is offered nine tools for eight attempts, and the
budget was not revisited.

Every Opus run since has used all eight attempts, and in every evaluation window
the tool left out was `bollinger_position`: 10 of 10 windows in
[trial 6](../evaluations/v2-july-2026-trial-6.md#why-research-declined) and
10 of 10 in [trial 7](../evaluations/v2-july-2026-trial-7.md#why-research-declined).
Trial 6's record kept the budget on a condition: "No assessment cited it as a
deciding gap, so the 8-attempt budget stays." In trial 7 that condition no
longer held. Every investigation listed the missing band width and percent_b as
a gap, every critic listed the tool as missing evidence, and three of the four
abstentions named the missing band-depth evidence among their reasons.

The [no-TRADE diagnostic](../evaluations/v2-no-trade-diagnostic.md) coded every
trial-7 run's stated routes to a stronger assessment. Each run named band width
and percent_b from `bollinger_position` as a route obtainable within the run,
and each had already used all eight attempts on other tools. Trial 5 runs did
request `bollinger_position` and still produced no `TRADE`. This decision is
about letting a run complete the evidence it can request, not about producing
`TRADE`.

The opportunity context does not fill the gap. The scanner's trigger payload
carries the trigger's close and lower band, but not band width, percent_b, the
upper band or SMA20, and the model must not compute values code can compute.

This evidence is about the research process: which evidence the model requested,
and which it reported missing. No trade outcome or return informed this
decision.

## Decision

**Rule.** The tool-attempt budget allows a single-pair run to request every tool
it is offered once. A change that adds a capability revisits the budget in the
same change.

**Budgets.** Under the current ten capabilities a single-pair window is offered
nine tools, so the workflow moves to `bounded_research_v3` with:

- 9 admitted tool attempts (from 8);
- 10 investigation calls (from 9), so the investigator can still complete after
  its last attempt;
- 15 total model calls (from 14): 10 investigation, 3 synthesis and 2 re-asks;
- graph recursion limit 26 (from 24). The worst case grows from 22 to 24 node
  executions, and LangGraph 1.2 needs a limit of at least one more than that;
  26 keeps the current margin of one.

Output re-asks (2 per run), invalid tool requests (2) and every other limit are
unchanged. Tools, evidence, prompts, schemas and the model view are unchanged.

**Choice stays with the model.** The investigator still chooses which tools to
request, in which order, and when to complete. Nothing requires it to use every
attempt.

**Multi-pair runs.** A run with bound benchmarks is offered relative strength as
well, with one query per benchmark: up to eleven distinct queries in a
three-pair trader-led run, for nine attempts. The rule covers single-pair
windows, the evaluation unit; choice still binds in multi-pair runs.

## Rationale

The squeeze is structural. With nine tools and eight attempts one tool is always
left out. In 20 of 20 evaluation windows it was the same tool, and in trial 7
every run then reported that tool's evidence as missing. Adding one attempt
removes the squeeze without moving evidence between tools or steering the model.

Alternatives considered:

- *Put the current band statistics in the `lower_band_crossings` payload*,
  requested first in every trial-7 run. This either duplicates
  `bollinger_position`'s evidence under a second evidence ID, or removes a
  capability that trader-led runs use. It also leaves the general problem: the
  next added tool recreates the squeeze.
- *Tell the investigator to prefer `bollinger_position`.* This steers tool
  selection, which belongs to the model, and changes every prompt identity
  without removing the cause.
- *Remove another tool.* That drops evidence the model uses in every run.
- *Derive the budget from the number of offered tools.* Budgets are fixed
  literal values that the journal re-encodes independently. A derived budget
  would make that cross-check weaker, for a benefit the fixed rule already
  provides.

[ADR-013](ADR-013-v2-evaluation-scope.md) pinned its first evaluation to the V1
workflow, prompts, schemas, tools and budgets. Later trials changed tools and
prompts, each trial recording its own policy, with results from different
policies never pooled. This ADR changes the budget on the same basis.

## Consequences

- The implementing slice changes `ResearchBudgetPolicy` and the journal's
  independent re-encoding of the budgets (`research_workflow` and
  `research_journal`), the specification's budget table and worst case, and the
  tests that pin them.
- The workflow identity changes, so research policy identity changes and stored
  runs fail to load at that commit, as with every earlier policy change. Each
  still loads at its recorded checkout.
- Each run makes at most one more tool call and one more investigation call.
  Expect roughly 10 to 15 percent more input per run; the next rehearsal
  measures it before the trial declares its cap and cost.
- The next trial runs under `bounded_research_v3` on uninspected windows. Its
  record reports which tools each run requested, including whether
  `bollinger_position` was used. Its results are not pooled with earlier
  trials.
- Adding a capability later revisits this budget in the same change.
