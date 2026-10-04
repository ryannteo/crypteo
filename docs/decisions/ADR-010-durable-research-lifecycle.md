# ADR-010: Durable append-only research lifecycle and transactional terminal publication

## Status

Accepted.

## Context

Bounded research can stop between preparing a request, invoking a provider,
observing a response, resolving a tool, and producing a validated stage. Saving
only a final result loses these distinctions and leaves interrupted or unresolved
work unavailable for audit. Consumers also need a trustworthy relationship between
retained observations and the terminal result, without repeating inference or
market-data queries.

[ADR-001](ADR-001-deterministic-vs-llm-boundary.md) establishes financial authority,
[ADR-002](ADR-002-point-in-time-temporal-boundary.md) establishes historical
visibility, and [ADR-009](ADR-009-bounded-model-directed-research.md) establishes
bounded research and its conclusion/interruption distinction. This decision
preserves those boundaries while adding durable retention. Sources:
[workflow](../../src/crypteo/research_workflow.py),
[journal contracts](../../src/crypteo/domain/research_journal.py),
[projection validation](../../src/crypteo/research_journal.py), and
[store](../../src/crypteo/research_persistence.py). Exact current semantics belong
in the [specification](../v1-specification.md#durable-research-lifecycle).

## Decision

Provide an optional durable entrypoint to the same bounded research engine,
injecting journal hooks into its runtime. Keep the in-memory entrypoint usable.
Retain an append-only lifecycle in a caller-owned local SQLite database, with a
single writer capability per run and permanent immutable terminal closure.

Commit lifecycle records before dependent provider/tool actions where applicable,
and commit observations, resolutions, and validated stage outputs before their
downstream use. Retain exact client-observable provider entity bytes separately
from normalized project-owned model output. SQLite transactions do not span
external provider or tool execution and cannot promise exactly-once external effects.

Publish the terminal event and immutable `ResearchArtifact` atomically for completed
or operationally interrupted research. Journal events remain authoritative; the
artifact is a validated consumer projection linked to that history. Consumers
load validated artifacts and inspect valid prefixes without inference or redispatch.

Fail closed on persistence failure: stop work, raise, and return no terminal
artifact from the failing invocation. Never convert uncertain storage into a
research conclusion or ordinary runtime `ABSTAIN`. Preserve original programmer
errors and cancellation; abort recording is best effort. Incomplete history remains
distinct from both operational interruption and aborted history, and does not by
itself prove a crash.

Do not automatically resume graphs, retry providers after a crash, redispatch tools,
or take over stale runs. Recovery is transactional database recovery plus validated
inspection/loading. Terminal closure cannot be reopened or silently replaced.

## Rationale

Append-only lifecycle retention explains what was prepared, started, observed,
validated, and left unresolved. Completed-result JSON alone is simpler but cannot
retain unfinished work or establish the ordering of dependent actions. Raw provider
observations preserve malformed responses and exact observable content without
equating that content with validated model output.

SQLite supplies local transactions, uniqueness, ordering constraints, immutable-row
guards, and atomic terminal publication without another persistence dependency.
JSONL or file-per-event storage would require bespoke coordination, synchronization,
partial-write detection, and multi-record publication rules. A generic event-sourcing
framework adds abstractions and operational scope beyond this bounded local journal.

Committing before dependent actions prevents unrecorded admission from authorizing
further work. Atomic terminal publication prevents a consumer-visible successful
terminal event without its matching artifact. It cannot remove uncertainty between
local commits and external effects, so the journal must represent unresolved work
rather than infer outcomes.

Automatic graph resumption would require rules for uncertain provider outcomes,
duplicate external effects, writer ownership, and deadlines. Validated inspection
preserves information without making those unsupported decisions. Treating
persistence failure as runtime `ABSTAIN` would disguise the inability to retain
trustworthy history as a valid research result, undermining later evaluation.

## Consequences

Durable calls incur transaction and validation overhead and may fail on bounded
lock contention or synchronization errors. Local filesystem and SQLite guarantees
remain prerequisites. An uncertain acknowledgement may leave committed history,
including a terminal artifact, even though the invocation raises and returns none;
inspection must determine the retained state without retrying the run.

Readers must validate lifecycle relationships and terminal consistency, not merely
decode schemas. Integrity hashes detect corruption and inconsistent retained data;
they do not authenticate against a party able to rewrite history and recompute them.
Version/schema changes require explicit compatibility decisions.

Incomplete and aborted histories remain inspectable without manufactured results.
Historical model-backed replay, frozen-response reproduction, and assessment-to-trade
integration remain separate work. Journal retention grants no market-data visibility,
decision memory, or financial authority beyond ADR-001/002/009.
