# ADR-001: Deterministic vs. LLM responsibility

## Status

Accepted; retrospectively records the V1 responsibility boundary.

## Context

Crypteo will evaluate whether qualitative research improves selection among shared
deterministic opportunities. Allowing model inference to define numerical values
or execution behavior would confound that comparison.

Sources: [specification](../v1-specification.md#hard-responsibility-boundary),
[repository guidelines](../../AGENTS.md#architecture-boundaries), and
[research contracts](../../src/crypteo/domain/research.py).
Architecture foundations: `77fa2f1`–`195b0ee`; research contracts: `2e0d092`;
analytics/scanner: `00b40d7`, `758d2de`.

## Decision

LLMs may perform qualitative research judgment and bounded orchestration: choose
permitted tools, interpret evidence, construct and critique theses, surface
uncertainty, and form structured research assessments.

Deterministic Python owns calculations, scanner logic, authorization, risk and
sizing, simulated execution mechanics, accounting/PnL, replay mechanics, and
metrics. Model outputs must not supply calculated quantities or execution semantics.
A `TRADE` assessment is research content; deterministic authorization remains separate.

## Rationale

Deterministic ownership supports reproducibility and auditability, makes numerical
correctness directly testable, and preserves replay fairness by keeping inputs,
risk, and execution assumptions identical across agent and baseline runs. It also
prevents model-generated sizes, prices, PnL, or fill rules from becoming authority.

## Consequences

Future tools expose code-calculated values as evidence. Future risk, execution,
accounting, and replay components must enforce their own contracts regardless of
the research assessment. Runtime identity and timestamps remain runtime-owned.

Through Slice 7, research schemas, data boundaries, analytics, and scanner exist;
LLM orchestration, risk authorization, simulated execution, and replay are later
work. This ADR does not claim those components are implemented. Real order routing
remains outside Crypteo's scope.
