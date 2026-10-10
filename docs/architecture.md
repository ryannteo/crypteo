# Current architecture

Crypteo is a Python modular monolith under `src/crypteo/`. It implements a
research platform with canonical market data, controlled historical visibility,
deterministic opportunity discovery and evidence, bounded model-backed research,
and deterministic simulated trade terms, risk, execution, accounting, and shared
baseline/fresh/frozen historical replay. The [README](../README.md) describes the
product; the [V1 specification](v1-specification.md) defines exact behavioral
semantics.

## Component flow

Arrows show current ownership and admission boundaries. Services do not call one
another or share an autonomous agent loop; replay coordinates them.

```mermaid
flowchart TD
    DATA[Canonical Coinbase datasets] --> GW[PointInTimeDataGateway]
    CLOCK[SimulationClock] --> GW
    REPLAY[Shared historical replay lifecycle] -->|alone advances| CLOCK
    REPLAY --> RECON[Broker reconciliation]
    RECON -->|decision boundary| SCAN[Scanner and delivery admission]
    SCAN -->|baseline| TERMS[Deterministic TradeTerms]
    TERMS -->|baseline valid terms| SELECT[Baseline selection]
    SCAN -->|fresh| WORK[Bounded durable research workflow]
    WORK --> TOOLS[MarketResearchService and evidence]
    WORK --> JOURNAL[SQLite lifecycle journal]
    JOURNAL --> ART[Immutable terminal ResearchArtifact]
    ART --> LOAD[ResearchStore.load_terminal]
    SCAN -->|frozen explicit mapping| LOAD
    LOAD --> ADMIT[Replay context and provenance admission]
    ADMIT --> OUTCOME[Completed model outcome]
    OUTCOME -->|TRADE only| TERMS
    OUTCOME -->|NO_TRADE or ABSTAIN| NEXT[Next candidate]
    TERMS -->|research valid terms| PROPOSAL[Deterministic TradeProposal]
    SELECT --> PROPOSAL
    PROPOSAL --> RISK[RiskService]
    RISK -->|authorized| BROKER[SimulatedBroker submission]
    BROKER --> PORT[Portfolio and accounting]
    RECON --> PORT
    PORT -->|current state| RISK
    GW --> SCAN
    GW --> TOOLS
    GW --> TERMS
    GW --> RISK
    GW --> RECON
    REPLAY --> RESULT[Compact ReplayResult and metrics]
    PORT --> RESULT
    RESULT --> LINKS[External research-link validation]
    LOAD --> LINKS
```

At each boundary replay reconciles the broker first, scans and records delivery,
processes admitted candidates sequentially, observes, then advances one hour.
The final boundary only reconciles and observes. Baseline freezes terms before
selection. Research freezes terms only after validated model `TRADE`; rejection
or model uncertainty ends that candidate before financial preparation. Operational
research failure aborts evaluation.

Fresh replay assembles `MarketResearchService` with the exact replay gateway and
source bindings. Frozen replay loads mapped durable artifacts without instantiating
a research service, running the graph, or making model/tool calls. Both use the
same deterministic financial lifecycle; frozen scanning, terms, execution, and
valuation still read market data.

## Data and historical visibility

Coinbase retrieval, normalization, dataset composition, and local persistence
preserve raw observations, canonical semantic data, and acquisition provenance as
separate immutable artifacts. Publication verifies their association. Loading is
explicit by dataset version, with optional acquisition linkage; it does not fetch
or repair data. The [data pipeline](data-pipeline.md) owns storage formats, gaps,
containment, and failure/concurrency details.

`SimulationClock` owns historical time. Runtime alone advances it.
`PointInTimeDataGateway` validates input candles and exposes only completed,
available history at that time. Scanner, research, terms, risk, and broker consume
this boundary without supplying their own cutoff. Pure analytics operates on
supplied candles; it cannot grant visibility. Full-dataset acquisition and integrity
checks are distinct from historical decision-time reads.

## Discovery and evidence

`OpportunityScanner` applies the fixed Bollinger crossover policy to visible
history and returns a reproducible snapshot of candidates. An `Opportunity` is
research context; it does not grant trading authority. Replay owns freshness,
delivery, and deduplication.

`MarketResearchService` supplies seven fixed-window tools: trend, momentum,
volatility, Bollinger position, recent levels, volume context, and relative
strength. Each returns typed unavailable information or neutral measurements in
a common `EvidenceItem`, with temporal and source provenance. Evidence identity
describes contributing data and computed payload; acquisition and whole-dataset
identities remain provenance. Large candle windows stay outside the envelope.

Common opportunity, evidence, thesis, critique, and assessment schemas are validated
contracts. They revalidate existing/nested instances and detach mutable inputs.
The model-backed workflow produces validated theses, critiques, and assessments
through those common contracts.

## Model-backed research

`run_opportunity_research` compiles a five-node typed LangGraph workflow:
`investigate --tool request--> execute_tool --result--> investigate`;
only investigation completion advances through
`investigate --complete--> draft_thesis → critique → final_assessment`.
The investigator chooses among seven deterministic market capabilities; the tool
adapter binds the opportunity's subject and allows only a relative-strength
benchmark argument. Software enforces tool-attempt, invalid-request, model-call,
output, provider-input, and deadline budgets. The graph recursion limit is a
secondary guard. Exact limits live in the
[specification](v1-specification.md#bounded-model-backed-research).

`ResearchModel` is the project-owned async provider abstraction: it prepares the
provider-facing request and returns typed replies and observable metadata.
`OpenAIResearchModel` currently implements it with the OpenAI Responses API,
strict structured schemas, no automatic retries, and no provider conversation
state. The caller owns client lifetime and chooses the model and supported
parameters. Prompts, schemas, and tool definitions carry versions and digests.
Its observable invocation boundary exposes client-visible HTTP entity bytes
before project decoding; durable hooks persist those observations separately
from normalized project-owned replies.

The runtime captures one research time from the executing `MarketResearchService`
and requires the opportunity's observation time to match it. The service binds
all reads to its authoritative gateway; models cannot choose cutoffs or windows.
Clock checks surround model/tool/cache boundaries and return. Replay checks T again
after durable publication/loading before financial preparation; awaiting inference
does not advance simulated time. Point-in-time data access cannot prove historical
purity of model parametric knowledge, including when assessments are frozen.
Successful evidence is deduplicated consistently, and research references must
resolve within the run.
The critic receives a fresh context containing the validated thesis, all collected
evidence, and request history; completed research cannot skip it. This is
procedural independence, not statistical independence.

A completed run contains a model assessment. An operational interruption contains
a typed failure and deterministic runtime `ABSTAIN`; it is not a completed model
conclusion. Neither result calls `TradeTermsService`, `RiskService`,
`SimulatedBroker`, or portfolio/accounting. `TRADE` supports further consideration
of a LONG setup and grants no financial authority. Mapping that judgment into a
trade proposal is replay-owned and occurs only after validated model `TRADE`.
[ADR-009](decisions/ADR-009-bounded-model-directed-research.md) records this boundary.

`ResearchRunResult` retains in-memory context, configuration, code/provider/graph
identity, exact observable model requests/replies, provider metadata, tool attempts
and cache/rejection links, research stages, and transitions/termination. Retained
relationships are validated so trace, evidence, and context cannot be arbitrarily
recombined. The in-memory entrypoint remains usable without a store. Exact
fresh-inference reproducibility is not promised.

## Durable research lifecycle

`run_durable_opportunity_research(..., store=...)` registers a caller-supplied run
ID and injects a `ResearchJournal` into the same workflow runtime. Runtime hooks
emit lifecycle records; SQLite persistence is not embedded in graph nodes.
`ResearchStore` owns local transactions using standard-library SQLite, separately
from Coinbase persistence and replay financial records. Fresh replay requires this
durable entrypoint; standalone research still permits the in-memory API.

The three layers have distinct responsibilities:

- Active in-memory graph state holds the current context, evidence, trace, and
  stage outputs used to advance bounded research.
- The durable journal retains the authoritative append-only committed lifecycle:
  prepared requests, call starts, exact provider response BLOBs, normalized replies,
  tool requests/resolutions and cache links, validated stages, and transitions.
  A valid prefix without a terminal event is `INCOMPLETE`; inspection can expose
  unresolved boundaries without assuming a crash or manufacturing a conclusion.
- `ResearchArtifact` is an immutable terminal consumer projection containing a
  validated `ResearchRunResult` and journal integrity linkage. It is published
  atomically with `RUN_FINISHED` for `COMPLETED` or `INTERRUPTED` research. A
  best-effort `RUN_ABORTED` closes a run without an ordinary result/artifact.

Lifecycle commits precede dependent provider/tool actions and downstream use of
their results. No transaction spans SQLite and an external provider or tool
execution. Storage failure stops further work and raises without returning an
artifact; it never becomes `NO_TRADE` or ordinary runtime `ABSTAIN`. An uncertain
commit can leave retained data for later inspection. Terminal closure is permanent.

Consumers call `ResearchStore.load_terminal` to obtain validated `ResearchArtifact`
objects rather than consume SQL rows. `inspect_run` validates committed prefixes,
terminal consistency, and unresolved operations. Both validate retained
relationships without inference, market-data reads, or tool redispatch. Recovery
means SQLite transactional recovery and inspection/loading; automatic graph resume,
provider retry after crash, and stale-run takeover are unimplemented.

The model and journal layer grants no financial authority. Replay consumes validated
terminal loading and adds candidate/time/policy/origin admission checks without
duplicating the journal validator. The durable store owns full research histories;
replay owns compact references and deterministic financial progression. See the
[lifecycle specification](v1-specification.md#durable-research-lifecycle) and
[ADR-010](decisions/ADR-010-durable-research-lifecycle.md).

## Terms, authorization, and execution

`TradeTermsService` creates immutable market-derived stop/target terms from visible
history. It neither selects opportunities nor sizes positions. Replay freezes
terms before constructing either mode's deterministic proposal. `RiskService` checks it
against the current portfolio and decision-time market reference, validates costs
and target economics, and sizes the largest permitted aligned quantity.
Authorization neither reserves cash nor changes portfolio state.

`SimulatedBroker` accepts already-authorized orders and reconciles next-open fills
only when the corresponding completed candle is visible. It owns one pending or
open trade at a time. Fees, adverse slippage, gaps, stop/target precedence, and time
exits are deterministic. Unknown next-open prices can cause affordability rejection
or losses beyond the decision-time estimate.

Portfolio transitions maintain exact cash, fee, cost-basis, and realized-P&L
accounting in immutable snapshots with append-only histories. Broker processing
commits a validated snapshot atomically. Missing entry data causes terminal
rejection; missing holding data preserves an unresolved position and halts finances.

## Replay and artifacts

`run_scanner_only` is the synchronous baseline entrypoint; `run_research_replay`
is async and supplies fresh or frozen research to the same lifecycle. Candidate
order is BTC-USD → ETH-USD → SOL-USD, then opportunity ID. Each candidate completes
research and any financial preparation/submission before the next is processed.
A submission occupies the one active trade slot; later risk checks see updated
pending/open/halted state. The model receives no portfolio state. Research remains
required for every admitted research-mode candidate even while busy or halted.

Fresh settings require explicit trial identity, positive `max_research_runs`,
workflow configuration, observable model, and caller-owned store. One budget unit
is reserved before registration/inference. Exhaustion aborts rather than truncates
a successful evaluation; per-run model-call budgets remain independent.

Baseline uses the schema-1 `SCANNER_ONLY` manifest. Research uses a schema-2
manifest binding deterministic replay inputs, sources, research settings, and
workflow/prompt/schema/tool/policy identities. Fresh identity excludes generated
model outputs. Frozen settings contain explicit candidate-to-artifact mappings and
fresh origins: originating replay ID, code, trial, and cap. Reconstructing the fresh
manifest from current deterministic inputs and retained origin enforces strict V1
compatibility; the artifact's producer code must also match. This prevents reuse
under changed replay economics, interval, datasets, code, or policy.

`ReplayResult` retains ordered lifecycle records, observations, final portfolio,
financial metrics, and research metrics in research mode. Compact research records
link canonical opportunity-context digests, run/artifact integrity, outcomes, and
origin/policy provenance; full thesis, critique, evidence, provider observations,
model-call history, and journal events remain in the durable artifact/journal.
Progression is derived from actual records. Canonical serialization supports
byte-stable baseline and compatible frozen reproduction; fresh inference is auditable
but may differ under identical inputs.

Mode-aware validation checks admitted-candidate stage chains, timing, financial
records/ledger prefixes, and recomputed metrics. It does not reconstruct unretained
market paths or prove external research exists. `validate_research_links` resolves
each referenced run through `load_terminal`, applies replay admission, and compares
the full compact projection. `load_research_replay` combines JSON parsing with that
external validation for imported results used in evaluation.

Marked equity uses visible completed closes; a holding gap leaves valid-prefix
valuation and known realized results available. Replay results remain in-memory
with canonical JSON output; durable research persistence does not persist the
financial replay lifecycle. Unimplemented replay and evaluation capabilities are
listed in the
[specification](v1-specification.md#unimplemented-research-and-evaluation-boundaries).
Exact contracts live in the
[baseline specification](v1-specification.md#scanner-only-replay) and
[research replay specification](v1-specification.md#historical-research-replay).

## Research extension boundary

Opportunity-led model research is implemented. Future trader-led interactive
research should converge on the same evidence, thesis, critique, and assessment
engine; deterministic tools already operate without requiring an `Opportunity`.
Research assessment stays separate from deterministic authorization.

Future research domains should emit normalized evidence. Sources require credible
historical availability to be replay safe; live-only sources cannot enter historical
evaluation. A bounded coordinator and relevant specialist capabilities are a
long-term direction, not current components or V1 orchestration. Recursive
delegation, unrestricted loops, and independently trading agents remain excluded.
Unimplemented and out-of-scope capabilities are listed in the
[specification](v1-specification.md#unimplemented-research-and-evaluation-boundaries).
