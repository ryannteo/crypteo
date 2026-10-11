# ADR-012: Claude as the durable research provider

## Status

Accepted

## Why Claude

The project no longer maintains an OpenAI account, so V1 needs a provider it
can actually run for real research and for the release verification run.
Claude's Messages API provides everything the bounded workflow relies on:
strict tool definitions, JSON-schema structured outputs, explicit effort
control, no server-side conversation state, and request IDs on every response.
A real-API calibration with claude-opus-5-5 confirmed each research stage
end to end and that the response echoes the exact requested model ID. This
decision concerns provider availability and capability fit. It makes no claim
that Claude produces better research than other providers; comparing research
quality across models is future evaluation work.

## Context

Durable research and research replay accept a model reply only when the journal
can re-derive it from retained bytes
([ADR-010](ADR-010-durable-research-lifecycle.md)). Until this decision only the
OpenAI adapter had such a codec. A Claude Messages adapter existed but could not
store a run, because the journal compared every other provider's prepared body
against the normalized project request and refused all other observed entities.

Claude differs from the OpenAI Responses API in ways that affect provenance and
limits:

- The SDK exposes server-side model fallback through beta headers and client
  middleware, so the model that answers is not necessarily the one requested.
- Client default headers and `ANTHROPIC_CUSTOM_HEADERS` can change the wire API
  version.
- Structured outputs reject several JSON Schema constraint keywords that the
  project schemas use.
- Thinking is always on and its tokens count against `max_tokens`.

Real-API calibration (`claude-opus-5-5`, effort `high`, one call per stage)
returned `claude-opus-5-5` on every call. Output tokens were 48 (investigate),
657 (draft thesis), 811 (critique), and 436 (final assessment); the slowest call
took 13.6 seconds. The specification's
[model boundary](../v1-specification.md#model-boundary-and-operational-limits)
defines the current contracts; sources include the
[Claude adapter](../../src/crypteo/anthropic_research.py) and the
[journal codecs](../../src/crypteo/research_journal.py).

## Decision

Claude is the only research provider: the Claude Messages adapter is the only
provider adapter.

- The journal maps each provider name to a codec: the adapter's pure request
  renderer, response decoder, and HTTP error decoder, imported lazily.
  `anthropic` is the only registered provider. Its prepared body
  and every observed entity must re-derive the stored normalized request and
  reply. Unregistered providers keep normalized-request comparison and cannot
  carry observations.
- Fallbacks are off. The adapter omits client- and environment-supplied
  `anthropic-beta` headers and drops injected client middleware. The SDK's own
  authentication marker for OAuth or workload-identity credentials is the only
  remaining beta header.
- The API version is pinned per request to `2023-06-01`.
- The returned `model` must equal the requested `model` in the retained request
  body for every stop reason. Otherwise the reply is an invalid-envelope
  failure. Because the journal re-runs the decoder, stored runs get the same
  check.
- Wire schemas strip Claude-unsupported constraint keywords at schema positions
  and state them in descriptions. Project validation still enforces them, and
  project schema identities are unchanged.
- Limits rise to 8,192 investigation and 16,384 synthesis output tokens, a
  180-second per-call deadline (equal to the client timeout), and a 900-second
  workflow deadline.

## Rationale

A reply only counts as research if the journal can prove which model produced
it from which request. Server-side fallback, rewriting middleware, or an
unpinned version would each allow a stored artifact to claim one model while
containing another's output, and re-derivation alone cannot detect that. The
equality check closes the remaining gap: whatever the transport did, another
model's reply never becomes research output.

A codec registry keeps the reconstruction rule provider-neutral. Adding a
provider means registering pure functions, not branching the journal.

Stripping constraints only from the wire keeps the project contract
authoritative. The model sees each range in its description, and out-of-range
output still fails project validation as invalid model output. The rewrite is
correct only where removing a keyword cannot change which instances a schema
accepts. Removing a constraint inside `not`, `if`, or `contains` would change
the schema's meaning. Pydantic never emits constraints there for the project
schemas, so this is a known limit, not a current defect.

The calibrated output (at most 811 tokens) and latency (13.6 seconds) fit the
old limits, but only narrowly once thinking grows with evidence-bearing context.
The new limits leave at least 10x headroom over every calibrated output count
and over the slowest call. The workflow deadline still bounds a run.

## Consequences

- Claude is the only provider adapter and the only registered codec, so real
  research requires Anthropic API access. Adding a provider means adding an
  adapter and registering its codec.
- Any change to a registered adapter's renderer or decoders changes how stored
  runs re-derive. A change that alters output makes earlier runs fail to load,
  in the same way as a prompt or schema change.
- Raising the limits changes `ResearchWorkflowConfig` and therefore policy
  identity. Runs stored with the old limits no longer load; no release
  artifacts existed.
- If a requested identifier is answered under a different returned identifier,
  such as an alias reported as a dated snapshot, the reply fails the equality
  check. Callers must request the identifier the API returns.
- If a future provider needs fallback, it must record the answering model as
  provenance without weakening the equality rule for existing providers.
