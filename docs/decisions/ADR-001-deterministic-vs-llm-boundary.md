# ADR-001: Deterministic vs. LLM responsibility

## Status

Accepted.

## Context

Crypteo will evaluate whether qualitative research improves selection among shared
deterministic opportunities. Allowing model inference to define numerical values
or execution behavior would confound that comparison.

Sources: [specification](../v1-specification.md#hard-responsibility-boundary),
[repository guidelines](../../AGENTS.md#architecture-boundaries), and
[research contracts](../../src/crypteo/domain/research.py).

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

Research tools expose code-calculated values as evidence. Risk, execution,
accounting, and replay components must enforce their own contracts regardless of
the research assessment. Runtime identity and timestamps remain runtime-owned.

Model-backed orchestration remains unimplemented. The implemented deterministic
components enforce this boundary independently. Real order routing remains outside
Crypteo's scope.
