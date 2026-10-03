# Current architecture

Crypteo is a Python modular monolith under `src/crypteo/`. It implements a
research platform with canonical market data, controlled historical visibility,
deterministic opportunity discovery and evidence, bounded model-backed research,
and separate simulated trade terms, risk, execution, accounting, and scanner-only
replay. The [README](../README.md) describes the product; the
[V1 specification](v1-specification.md) defines exact behavioral semantics.

## Component flow

Arrows show current data/control relationships. Model-backed research is a
separate caller-invoked API; scanner-only replay invokes neither research tools
nor the model workflow.

```mermaid
flowchart TD
    CB[Coinbase public candles] --> DATA[Acquisition and canonical persistence]
    DATA --> INPUT[Explicit canonical datasets]
    INPUT --> GW[PointInTimeDataGateway]
    CLOCK[SimulationClock] --> GW
    REPLAY[Scanner-only replay] -->|advances| CLOCK
    REPLAY -->|coordinates| SCAN[OpportunityScanner]
    REPLAY -->|coordinates| TERMS[TradeTermsService / TradeTerms]
    REPLAY -->|coordinates| RISK[RiskService]
    REPLAY -->|coordinates| BROKER[SimulatedBroker]
    GW --> SCAN
    GW --> TERMS
    GW --> RISK
    GW --> BROKER
    SCAN -->|admitted opportunity| TERMS
    TERMS -->|frozen terms and proposal| RISK
    RISK -->|authorized order| BROKER
    BROKER --> PORT[Portfolio snapshots and accounting]
    PORT -->|current state| RISK
    REPLAY --> RESULT[Replay records and experiment manifest]
    PORT --> RESULT
    RESULT --> METRICS[Marked equity and metrics]
    OP[Opportunity supplied by caller] --> WORK[Bounded research workflow]
    WORK --> INV[Investigate model call]
    INV --> ADAPTER[Deterministic research-tool adapter]
    ADAPTER --> TOOLS[MarketResearchService]
    GW --> TOOLS
    TOOLS --> EVIDENCE[EvidenceItem and typed results]
    EVIDENCE --> INV
    INV -->|investigation completion| THESIS[Draft thesis]
    THESIS --> CRITIC[Critique]
    CRITIC --> FINAL[Final assessment]
    FINAL --> DECISION[TradeDecision: research judgment only]
```

The opportunity-to-terms-to-risk arrows represent replay's coordination. Services
do not call one another or share an autonomous agent loop. Research assembly uses
its supplied runtime clock and datasets to construct its own gateway and matching
source bindings; it does not attach itself to an existing replay run.

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

The runtime captures one research time from the executing `MarketResearchService`
and requires the opportunity's observation time to match it. The service binds
all reads to its authoritative gateway; models cannot choose cutoffs or windows.
Clock checks surround model/tool/cache boundaries and return. Successful evidence
is deduplicated consistently, and research references must resolve within the run.
The critic receives a fresh context containing the validated thesis, all collected
evidence, and request history; completed research cannot skip it. This is
procedural independence, not statistical independence.

A completed run contains a model assessment. An operational interruption contains
a typed failure and deterministic runtime `ABSTAIN`; it is not a completed model
conclusion. Neither result calls `TradeTermsService`, `RiskService`,
`SimulatedBroker`, or portfolio/accounting. `TRADE` supports further consideration
of a LONG setup and grants no financial authority. Mapping that judgment into a
trade proposal and historical model-backed replay remain future integration work.
[ADR-009](decisions/ADR-009-bounded-model-directed-research.md) records this boundary.

`ResearchRunResult` retains in-memory context, configuration, code/provider/graph
identity, exact observable model requests/replies, provider metadata, tool attempts
and cache/rejection links, research stages, and transitions/termination. Retained
relationships are validated so trace, evidence, and context cannot be arbitrarily
recombined. This is in-memory auditability; durable trace/lifecycle persistence and
exact fresh-inference reproducibility are not implemented or promised.

## Terms, authorization, and execution

`TradeTermsService` creates immutable market-derived stop/target terms from visible
history. It neither selects opportunities nor sizes positions. Replay freezes
terms before constructing the baseline proposal. `RiskService` checks that proposal
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

`run_scanner_only` validates explicit canonical inputs and builds one clock/gateway
shared by scanner, terms, risk, and broker. At each decision boundary it reconciles
execution first, scans, admits fresh candidates in deterministic order, freezes
terms, selects every candidate with valid terms, and authorizes/submits sequentially.
It records a portfolio observation before advancing the clock. The final boundary
reconciles and observes without new decisions or forced liquidation.

`ReplayResult` retains the experiment manifest, ordered lifecycle records,
observations, final portfolio, and metrics. Canonical serialization supports
byte-stable reproduction. Validation reconciles retained execution/risk records
and ledger prefixes, and recomputes metrics. It does not reconstruct market paths
absent from the artifact. Marked equity uses visible completed closes; a holding
gap leaves only valid-prefix valuation and known realized results available.

These are in-memory audit artifacts with canonical JSON output. Durable lifecycle
journaling, restart recovery, agentic replay, and a walk-forward evaluation runner
are unimplemented. Exact ordering, end conditions, artifact identity, and metric
semantics live in the [replay specification](v1-specification.md#scanner-only-replay).

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
Trader-led chat, live mode, UI, web/news/social/on-chain/derivatives research,
and long-term memory/RAG are unimplemented. Real order routing,
trading credentials, private keys, and withdrawal capabilities are out of scope.
