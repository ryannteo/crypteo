# Architectural decisions

Architecture decision records (ADRs) document decisions that materially constrain
future architecture. The [build log](../build-log.md) records chronological
development; git records implementation detail. Ordinary implementation choices
do not need ADRs.

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
Ground rationale in repository evidence or explicit decisions; distinguish
reconstructed rationale from unavailable historical discussion.

ADRs 001–004 retrospectively record established constraints through Slice 7.
Their Accepted status records those existing decisions, not completion of later
components described by the specification.
ADR-005 records the completed Slice 8 execution semantics.
ADR-006 records the completed Slice 9 decision-time risk semantics.
ADR-007 records the completed pre-Slice-10 deterministic trade-terms prerequisite.

| ADR | Decision | Status |
| --- | --- | --- |
| [001](ADR-001-deterministic-vs-llm-boundary.md) | Deterministic vs. LLM responsibility | Accepted |
| [002](ADR-002-point-in-time-temporal-boundary.md) | Point-in-time temporal boundary | Accepted |
| [003](ADR-003-canonical-data-and-acquisition-provenance.md) | Canonical data and acquisition provenance | Accepted |
| [004](ADR-004-scanner-snapshot-semantics.md) | Scanner snapshot semantics | Accepted |
| [005](ADR-005-completed-bar-execution-reconciliation.md) | Completed-bar simulated execution reconciliation | Accepted |
| [006](ADR-006-decision-time-risk-authorization.md) | Decision-time risk authorization under unknown next-open execution | Accepted |
| [007](ADR-007-deterministic-trade-terms.md) | Deterministic market-derived trade terms | Accepted |
