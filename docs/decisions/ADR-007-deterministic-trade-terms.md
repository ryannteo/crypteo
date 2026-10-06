# ADR-007: Deterministic market-derived trade terms

## Status

Accepted. [ADR-011](ADR-011-model-research-historical-replay.md) supersedes only
the requirement to generate terms before selection for model-backed replay:
research precedes terms there. Baseline retains the original ordering;
deterministic ownership and freezing before proposal/risk/execution remain unchanged.

## Context

Controlled trade-selection evaluation needs market-derived stop/target terms
before selection, risk authorization, and simulated execution. Neither research
judgment nor knowledge of eventual fills may determine those terms. A target above
the reference price alone does not establish positive reward after execution costs.

Sources: [trade-terms service](../../src/crypteo/trade_terms.py),
[immutable terms contracts](../../src/crypteo/domain/trade_terms.py),
[risk service](../../src/crypteo/risk.py),
[risk contracts](../../src/crypteo/domain/risk.py),
[shared execution-cost arithmetic](../../src/crypteo/_execution_costs.py),
[terms tests](../../tests/test_trade_terms.py), and
[risk tests](../../tests/test_risk.py).

## Decision

Deterministic code generates market-derived trade terms from authoritative
point-in-time data. The generator reads gateway time and visible completed history;
it cannot accept a model-controlled cutoff or advance the clock. Missing required
history or unusable levels fail closed. Successful terms are immutable and retain
their market identity, decision time, configuration, reference, and statistics.

The evaluation runtime must freeze terms before selection, authorization, and
execution. Term generation is separate from scanner delivery, research judgment,
portfolio sizing, and the broker. It neither selects candidates nor constructs
orders, reserves capital, or grants execution authority. Generating terms does not
automatically expose them to the LLM or extend the research-assessment schema.

`RiskService` owns the strict positive estimated net target reward condition for
every proposal, including direct proposals that bypass the generator. Estimated
target SELL proceeds after exit fees must exceed estimated entry BUY cost including
entry fees. Zero or negative reward yields `NON_POSITIVE_TARGET_REWARD`; no epsilon
or minimum reward/risk threshold is implied.

Risk estimates share adverse execution pricing and increment arithmetic with the
broker and use common fee-inclusive execution-cost arithmetic. Public contracts
validate by recomputation: terms reconcile raw and aligned levels from their
statistics/configuration, and authorization reconciles execution estimates, net
target reward, and sizing. Schema validation alone does not prove that supplied
statistics came from visible market data; authoritative generation establishes
that boundary.

Replay runtime owns opportunity freshness and stale-term admission, as well as
deduplication, orchestration, proposal construction, metrics, and experiment
manifests. A current pair-level generation result does not establish that an older
scanner opportunity is still eligible. Those runtime policies are governed by
[ADR-008](ADR-008-scanner-only-replay-boundary-delivery-ordering.md).

## Rationale

Freezing deterministic terms keeps selection from changing the candidate's
economics and preserves a common basis for agent/baseline comparison. Separating
market-level generation from portfolio authorization permits both to enforce their
own invariants without coupling the scanner or research contracts to execution.
Shared arithmetic and recomputation prevent plausible but fabricated levels or
cost diagnostics from passing validation. Risk ownership prevents direct proposals
from bypassing the target-economics condition.

## Consequences

Decision-time estimated reward and nongapped stop loss remain distinct from actual
next-open affordability and gap outcomes. Positive estimated reward does not
guarantee positive realized target-exit P&L or cap realized loss. The broker retains
the execution responsibilities in
[ADR-005](ADR-005-completed-bar-execution-reconciliation.md); this decision extends
the decision-time authorization boundary in
[ADR-006](ADR-006-decision-time-risk-authorization.md).

The current multiplier, SMA20 target, and 20-range window are V1 policy recorded in
the [specification](../v1-specification.md#deterministic-trade-terms), not universal
architectural constraints. Future policy changes must preserve authoritative
generation, frozen terms, independent risk validation, and reproducible settings.
