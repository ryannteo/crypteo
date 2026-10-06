# ADR-009: Bounded model-directed research

## Status

Accepted.

## Context

An opportunity is a candidate for research, not financial authority. Qualitative
investigation benefits from choosing the next evidence request based on what has
already been learned, but a model's tool choice must not expand historical
visibility, calculate financial values, or create an unrestricted agent loop.
Operational failures also need to remain distinguishable from substantive
research conclusions for audit and future evaluation.

[ADR-001](ADR-001-deterministic-vs-llm-boundary.md) establishes deterministic versus
model responsibility; [ADR-002](ADR-002-point-in-time-temporal-boundary.md)
establishes the authoritative historical visibility boundary. This decision
applies those constraints to the
[research workflow](../../src/crypteo/research_workflow.py), its
[tool adapter](../../src/crypteo/research_tools.py), and
[retained contracts](../../src/crypteo/domain/research_workflow.py).
Exact current limits and semantics belong in the
[specification](../v1-specification.md#bounded-model-backed-research).

## Decision

Allow model-directed investigation only through a narrow, runtime-enforced,
bounded deterministic research-tool interface. Runtime binds the opportunity's
subject, authoritative research time, source relationships, venue, and fixed
windows. The model can choose a permitted capability and supported relative-
strength benchmark; it cannot choose market-data cutoffs/lookbacks or own market
and financial arithmetic.

Enforce investigation budgets, invalid-request handling, input/output limits,
and deadlines in software. Prompt instructions communicate policy but do not
provide authority or replace enforcement. Cache reuse remains a budgeted attempt,
and only successful runtime-produced evidence can ground structured references.

Keep draft thesis, critique, and final assessment as separate validated stages.
The critic receives a fresh context containing the validated thesis and collected
evidence; completed research must pass through it. This supplies procedural
independence, not a claim of statistical independence or guaranteed correctness.

Distinguish completed model research from operational interruption. A completed
run contains investigation completion, thesis, critique, and a model assessment.
An interrupted run contains a typed failure and deterministic runtime `ABSTAIN`.
Runtime-generated `ABSTAIN` is not equivalent to a completed model `ABSTAIN`;
provider failure is never substantive `NO_TRADE`. Integrity/programmer failures
raise rather than being disguised as ordinary conclusions.

Keep research assessment separate from deterministic authorization and execution.
`TRADE` supports further consideration, not trade terms, sizing, risk approval,
order submission, portfolio mutation, or accounting. Those components retain the
independent authority established by ADR-001.

## Rationale

Adaptive investigation can focus limited requests on evidence relevant to the
candidate while retaining deterministic calculations and an enforceable
information boundary. Precollecting every capability deterministically is simpler
and gives uniform coverage, but removes model-directed evidence selection and
requests evidence regardless of the emerging research question. It remains a
possible future comparison policy rather than the chosen workflow.

An unconstrained generic ReAct/agent executor makes stopping, allowed arguments,
visibility, and failure handling depend too heavily on model obedience or broad
executor behavior. A small typed graph and explicit local admission policy make
those limits inspectable and enforceable independently of the provider.

Combining critique and final assessment into one call would reduce latency, but
would remove the distinct challenge artifact and the explicit requirement that
the final assessment respond to it. A fresh critic context gives the challenge
stage its own instructions and exact validated inputs without inheriting the
investigator's conversation.

Treating infrastructure failures as research decisions would confound inability
to obtain a conclusion with evidence against the candidate. Separate status,
origin, and typed failure preserve that distinction even when the shared outcome
vocabulary uses `ABSTAIN` for both completed uncertainty and interruption.

## Consequences

Research adapters must preserve project-owned typed requests/replies and observable
provider metadata; providers cannot bypass local budgets or temporal checks.
Additional capabilities must fit normalized evidence and explicit tool authority,
with credible availability guarantees before historical evaluation. Prompt changes
alone cannot expand tool or financial permissions.

Separate synthesis calls add latency and cost, and bounded investigation may end
without sufficient evidence. Critique can share model biases despite its fresh
context. Point-in-time tool access does not guarantee historical purity of model
parametric knowledge or deterministic fresh inference.

In-memory traces retain requests, results, versions, stage outputs, and termination
for inspection with validated relationships. Optional durable retention and
transactional terminal publication are governed by
[ADR-010](ADR-010-durable-research-lifecycle.md). Historical model-backed replay
and assessment admission into deterministic trade preparation are governed by
[ADR-011](ADR-011-model-research-historical-replay.md), preserving the difference
between completed research and operational interruption and granting no financial
authority to model output.
