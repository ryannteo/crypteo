# ADR-011: Model research admission into deterministic historical replay

## Status

Accepted. Narrowly supersedes [ADR-007](ADR-007-deterministic-trade-terms.md)'s
terms-before-selection requirement for model-backed replay only. Baseline ordering
and deterministic terms ownership/freezing remain in force.

## Context

Historical evaluation must admit research conclusions without creating a second
source of truth for replay economics or transferring financial authority to the
model. It must distinguish substantive rejection and uncertainty from inability to
obtain or retain trustworthy research. New inference requires auditable evidence;
reproduction requires an explicit relationship to retained research and its
originating evaluation assumptions.

[ADR-001](ADR-001-deterministic-vs-llm-boundary.md) defines financial authority,
[ADR-002](ADR-002-point-in-time-temporal-boundary.md) historical visibility,
[ADR-008](ADR-008-scanner-only-replay-boundary-delivery-ordering.md) replay ordering,
and [ADR-009](ADR-009-bounded-model-directed-research.md)/
[ADR-010](ADR-010-durable-research-lifecycle.md) bounded research and durable loading.
The [research replay specification](../v1-specification.md#historical-research-replay)
defines current contracts; sources include the
[shared lifecycle](../../src/crypteo/replay.py),
[research replay](../../src/crypteo/research_replay.py), and
[admission contracts](../../src/crypteo/domain/replay_research.py).

## Decision

Use one shared historical replay lifecycle for baseline and model-backed evaluation.
Preserve scanning, delivery, authoritative time, next-open execution, risk,
accounting, valuation, and final-boundary behavior across selection policies.

Treat model research as selection/admission only. Only validated terminal
`COMPLETED / MODEL / TRADE` research permits deterministic terms, proposal,
risk authorization, and authorized simulated submission. Model rejection and
completed uncertainty stop before financial preparation. The model never owns
stop/target arithmetic, sizing, execution, fees, or portfolio mutation.

Perform research before generating terms in model-backed replay. Keep baseline's
terms-before-selection behavior. This narrowly supersedes ADR-007's selection
timing, preserving deterministic terms ownership and freezing before proposal,
risk, and execution; model judgment cannot change the financial calculation.

Support fresh inference with mandatory durable journaling and an explicit bounded
research-run budget, and frozen reproduction from explicit validated artifact
mappings. Durable loading owns journal validation; replay adds context, historical
boundary, policy, and provenance admission. Bind frozen reuse to a compatible
originating fresh replay and artifact producer rather than searching history for
similar candidates or reusing assessments under arbitrary changed configurations.

Abort evaluation on operational research failure, including interruption,
storage/admission failure, and budget exhaustion. Do not translate infrastructure
failure into substantive rejection, model uncertainty, or silent candidate skipping.

Preserve deterministic candidate ordering and sequential processing: finish a
candidate's research and any submission before processing the next. Subsequent risk
authorization sees updated financial state; model research receives no portfolio
state. Keep replay results compact, referring to durable artifacts instead of
copying their complete research and journal histories. Validate external links
when importing research replay results for evaluation.

## Rationale

One lifecycle preserves one source of truth for financial and temporal mechanics.
A separate agent replay engine would duplicate accounting and make economic
behavior drift across policies. Independent deterministic authorization prevents
research admission from becoming financial permission.

Research-first processing measures the model's conclusion about an admitted
opportunity before deterministic financial feasibility can reject it. Generating
terms first for all modes would suppress that research whenever terms are invalid.
Baseline retains its established semantics; both paths freeze the same
point-in-time deterministic terms before downstream financial use.

Treating interruption as `NO_TRADE` or model `ABSTAIN` would confuse infrastructure
availability with research judgment and permit apparently complete evaluations
with missing conclusions. Failing closed makes this distinction explicit, at the
cost of aborting runs whose earlier research may already be durable.

Fresh-only replay would preserve auditability but require new inference for every
reproduction. Frozen artifacts make retained conclusions reproducible without
model/tool redispatch. Explicit mapping and strict originating compatibility prevent
accidental reuse under changed replay economics, data, code, or policy; heuristic
lookup and unrestricted reuse would weaken the association boundary.

Copying full artifacts into replay results would duplicate the durable journal's
consumer projection and validation responsibility. Compact links retain the
association while leaving research history with its authoritative owner; external
validation is necessary to establish that those links resolve.

Concurrent candidate research would complicate sequencing and state interactions.
Sequential research and immediate authorized submission preserve explicit
allocation of the one active trade slot and consistent downstream risk state.

## Consequences

Fresh evaluation requires a durable store, explicit trial identity and software
budgets, and incurs model latency and journal overhead. Inference remains
nondeterministic; the run cap is not a billing guarantee. Operational failures
produce no successful partial replay, although committed research remains auditable.

Frozen reproduction requires retained validated artifacts and compatible origin
inputs. It cannot serve as a general assessment cache across changed experiments.
Compact replay parsing alone cannot establish external artifact existence.

Point-in-time tools exclude future market data, but cannot prove historical purity
of model parametric knowledge. Freezing conclusions improves reproducibility without
removing that limitation. The shared lifecycle supports later comparison work;
a first-class baseline/model comparison framework is not implied by this decision.
