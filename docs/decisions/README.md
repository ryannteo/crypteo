# Architectural decisions

Architecture decision records (ADRs) preserve rationale for decisions that
materially constrain architecture: the chosen rule, important alternatives,
consequences, and trade-offs. Ordinary implementation choices do not need ADRs.

Documentation describes the system that exists at HEAD. ADRs preserve rationale
for durable architectural decisions. Git preserves implementation history.
See the [architecture overview](../architecture.md) for current component flow
and the [behavioral specification](../v1-specification.md) for exact semantics.

Use sequential identifiers and descriptive filenames (`ADR-001-short-title.md`).
Keep each record focused on one constraint and use this minimal format:

```markdown
# ADR-NNN: Title

## Status

Proposed | Accepted | Superseded by ADR-NNN

## Context

The problem and relevant constraints.

## Decision

The chosen architectural rule.

## Rationale

Why the rule is justified by available evidence.

## Consequences

Obligations, tradeoffs, and implications for future work.
```

Accepted ADRs are not silently rewritten when superseded. Add a new ADR and mark
the older record superseded with a link to its replacement when appropriate.
Ground rationale in repository evidence or explicit decisions. Retain rationale
when updating obsolete implementation-status notes; record a new ADR when a
durable decision actually changes.

| ADR | Decision | Status |
| --- | --- | --- |
| [001](ADR-001-deterministic-vs-llm-boundary.md) | Deterministic vs. LLM responsibility | Accepted |
| [002](ADR-002-point-in-time-temporal-boundary.md) | Point-in-time temporal boundary | Accepted |
| [003](ADR-003-canonical-data-and-acquisition-provenance.md) | Canonical data and acquisition provenance | Accepted |
| [004](ADR-004-scanner-snapshot-semantics.md) | Scanner snapshot semantics | Accepted |
| [005](ADR-005-completed-bar-execution-reconciliation.md) | Completed-bar simulated execution reconciliation | Accepted |
| [006](ADR-006-decision-time-risk-authorization.md) | Decision-time risk authorization under unknown next-open execution | Accepted |
| [007](ADR-007-deterministic-trade-terms.md) | Deterministic market-derived trade terms | Accepted; selection timing narrowly superseded by ADR-011 |
| [008](ADR-008-scanner-only-replay-boundary-delivery-ordering.md) | Scanner-only replay boundary, delivery, and simultaneous-candidate ordering | Accepted |
| [009](ADR-009-bounded-model-directed-research.md) | Bounded model-directed research | Accepted |
| [010](ADR-010-durable-research-lifecycle.md) | Durable append-only research lifecycle and transactional terminal publication | Accepted |
| [011](ADR-011-model-research-historical-replay.md) | Model research admission into deterministic historical replay | Accepted |
