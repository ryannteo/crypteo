# Crypteo V1 Architecture Specification

Status: architecture locked for V1. Changes to a hard invariant require an architecture decision record.

## Product and Research Question

Crypteo is an agentic crypto research copilot for traders and researchers with existing market knowledge. Its long-term product is a live, up-to-date research assistant that investigates market opportunities, gathers and synthesizes evidence, challenges trade theses, surfaces uncertainty and counterevidence, and supports more informed trading decisions. AI researchers and portfolio reviewers are secondary users.

V1 is the first market-analysis capability of the broader Crypteo research platform. It uses historical OHLCV-derived evidence and does not yet implement the wider research domains intended for the eventual live product. It asks:

> Given the same deterministic trade opportunities, does bounded agentic market analysis improve trade-selection quality relative to deterministic baselines under identical execution and risk assumptions?

Historical replay, the deterministic scanner, paper trading, and structured trade-selection decisions form an evaluation lab for testing whether Crypteo's research improves trade selection. They are not Crypteo's primary product identity. V1 tests selection, not autonomous strategy invention or LLM price prediction. It makes no profitability claim and can never trade real money.

Long term, Crypteo is expected to evolve into a bounded multi-agent research system that tests whether progressively richer, temporally valid research improves selection. Market, derivatives, on-chain, news/catalyst, fundamentals/protocol, tokenomics, macro, and sentiment specialists should contribute normalized evidence to one integrated thesis—not independent buy/sell votes. A future research coordinator dynamically selects only the specialists relevant to a trader's question or setup instead of invoking every research domain indiscriminately. A capability may later be a tool, service, node, subgraph, or agent only when its complexity warrants that form. V1 intentionally remains one bounded research workflow and does not implement the coordinator or specialist agents.

## V1 Scope and Market Environment

- Coinbase Exchange spot market; `BTC-USD`, `ETH-USD`, and `SOL-USD`.
- Completed 1-hour OHLCV candles on a timezone-aware UTC timeline.
- Exactly one deterministic scanner shared by Crypteo and scanner-only baseline.
- Long positions only; domain direction remains an enum to permit a later `SHORT` value.
- Market-derived evidence only: price, returns, volume, trend, momentum, volatility, relative strength, support/resistance, and deterministic regime measures.
- Historical replay is the first complete evaluation mode.
- Decisions are `TRADE`, `NO_TRADE`, or `ABSTAIN`; `RISK_REJECTED` is a separate deterministic outcome.

Excluded from V1 research are web/news, social, on-chain, derivatives, macro, protocol/fundamental, and tokenomics data.

## Hard Responsibility Boundary

The LLM chooses permitted tools, interprets evidence, identifies conflict and assumptions, constructs bull/bear cases and a falsifiable thesis, critiques it, selects a decision, and writes qualitative post-trade analysis.

Deterministic Python owns OHLCV, indicators, returns, volatility, scanning, all arithmetic, candidate price levels, reward/risk, sizing, exposure, portfolio/risk rules, validation, fills, fees, slippage, stops, targets, time exits, accounting, P&L, clocks, replay, and metrics.

The invariant is: LLM for judgment and orchestration; deterministic software for calculation, authorization, execution, and accounting. A `TRADE` research assessment does not invent calculated values, quantity, P&L, exposure, reward/risk, execution price, or undeclared references to future deterministic level or risk-template contracts. If later decisions need candidate-level references, those references must point to runtime-owned deterministic contracts introduced alongside the corresponding domain objects.

## Components and Extension Seams

Crypteo is a modular monolith composed of point-in-time data, deterministic analytics and scanner, research tools, LangGraph workflow, risk service, simulated broker/portfolio, append-only journal, replay engine, and evaluation harness.

The eventual product supports two research entry modes without making either entry contract universal:

- **Trader-led research:** the user supplies a market question, trade idea, or thesis for Crypteo to investigate. This mode does not require a scanner or manufacture an `Opportunity`; it may later use its own research-request or context contract.
- **Opportunity-led research:** a deterministic scanner or another discovery mechanism creates an `Opportunity` as research context. The scanner generates a candidate only; the `Opportunity` does not imply that a trade should be taken.

Both converge on `research context -> normalized evidence -> integrated thesis -> critic -> final research assessment`. V1 historical evaluation implements opportunity-led research first because identical scanner-generated opportunities provide a controlled basis for agent-versus-baseline comparison. The scanner and `Opportunity` contract are evaluation-path inputs, not prerequisites for all future live research.

The minimal extension seams are:

- `OpportunityScanner.scan(context) -> opportunities`
- `ResearchTool.invoke(request, runtime_context) -> evidence`
- `Broker.submit(order_intent, market_event) -> execution_events`
- `Clock.now -> UTC datetime`
- market-data provider behind a `PointInTimeDataGateway`

No plugin framework, dependency-injection framework, or speculative capability hierarchy is required. Future research capabilities register tools that emit the same evidence representation. They may join historical replay only if classified `POINT_IN_TIME_REPLAY_SAFE`; sources without defensible historical availability are `LIVE_ONLY`.

## Opportunity Contract

An `Opportunity` is the small deterministic research-candidate contract for opportunity-led research. It contains only `opportunity_id`, `venue`, `pair`, `timeframe`, `observed_at`, `available_at`, `trigger_type`, `trigger_payload`, and `summary`.

`opportunity_id` is assigned by deterministic runtime or scanner logic, is never generated by the LLM, has no random schema default, and is stable enough for research, replay, journaling, and evaluation to reference. V1 uses the canonical Coinbase Exchange spot venue, supported `BTC-USD`, `ETH-USD`, and `SOL-USD` pairs, and one-hour timeframe. No UUID framework or concrete ID-generation algorithm is prescribed here.

`observed_at` is when the deterministic trigger became complete. For the V1 hourly scanner this is the trigger candle's completion, not its open; for a window-derived trigger it is the latest point required for the trigger to be valid. `available_at` is the earliest time the opportunity could legitimately have been known, and the schema enforces only `available_at >= observed_at`.

`trigger_type` is an open-ended, stable, nonblank deterministic trigger identifier rather than an enumeration of scanner strategies. `trigger_payload` contains compact JSON-compatible machine-readable trigger facts with string mapping keys and no arbitrary Python objects. Complete histories, large indicator windows, raw datasets, and provider responses remain external. `summary` is a concise deterministic explanation of why the candidate exists.

An `Opportunity` is not an LLM decision, directional vote, order, execution or risk authorization, or portfolio authorization. It contains no run or decision identity, model-generated IDs or timestamps, scanner implementation/configuration objects, position or trade-level arithmetic, journal/graph metadata, LLM reasoning, thesis, or critique content.

## Evidence Contract

Every normalized `EvidenceItem` contains `evidence_id`, category/type, source, `observed_at`, `available_at`, provenance, tool/query parameters, dataset/source version, immutable structured payload or artifact reference, concise relevance, and optional quality metadata. Large dataframes and raw datasets stay outside graph state. Research capabilities emit this evidence rather than independent `BUY`, `SELL`, `BULLISH`, or `BEARISH` votes; the integrated research process owns interpretation.

Tool runtime, not the model, injects asset, venue, timeframe, and authoritative cutoff. Tool outputs expose warnings and temporal provenance. The thesis and critic consume evidence contracts rather than market-provider objects.

`Opportunity` and `EvidenceItem` schemas enforce internal timestamp consistency, including `available_at >= observed_at`. They do not enforce `available_at <= SimulationClock.now`; Slice 4's `SimulationClock` and `PointInTimeDataGateway` own authoritative visibility at replay time.

## Initial LangGraph

```text
Research context -> Investigate (bounded tools) -> Draft thesis -> Critic
                 -> Final schema-validated research assessment
```

For the V1 opportunity-led path, the research context is an `Opportunity`; a future trader-led path may use its own request/context contract. Investigation dynamically selects relevant typed market tools. The critic is a distinct model call receiving the evidence and draft. V1 has no planning agent, specialist-agent team, delegation, debate, recursive hierarchy, or repeated autonomous research loop. This bounded single workflow validates the common research, evidence, critique, and evaluation architecture before later specialist orchestration is introduced.

Configurable limits cover tool calls, total tokens, graph iterations, retries, model calls, and decision age. Exhaustion, malformed output, unavailable/stale evidence, or unrecoverable tool/model failure fails closed to `ABSTAIN`. The graph may retry only within its fixed budget.

Graph state contains runtime-owned identity/context and compact structured results: evidence references, tool-call records, draft thesis, critique, final decision, budgets consumed, status, and errors. It does not contain raw candle collections. Runtime identity is associated with model-facing records by orchestration and journaling rather than generated inside those records.

## Long-Term Research Architecture and Presentation

The intended long-term research flow is:

```text
Research request / opportunity
        -> Research coordinator
        -> Select only relevant specialist research agents
        -> Normalized evidence
        -> Integrated synthesis / thesis
        -> Critic
        -> Final structured research assessment
        -> Interactive research dashboard
```

Potential specialists include market, derivatives, on-chain, news/catalysts, protocol/fundamentals, tokenomics, macro, and sentiment research. The coordinator selects only those relevant to the request; not every specialist runs every time. Specialists produce normalized, provenance-aware evidence and do not independently emit `BUY` or `SELL` decisions. Integrated synthesis owns the combined interpretation, the critic remains distinct from synthesis, and the final result remains a structured research assessment rather than execution authorization.

Multi-agent orchestration must remain bounded and auditable. It must not introduce recursive delegation, unrestricted autonomous loops, or independently trading agent teams. These are long-term product directions, not V1 implementation requirements.

The interactive research dashboard is intended to be the primary structured presentation layer. It may eventually visualize price and market data, evidence and provenance, bull and bear cases, uncertainty, critic output, research activity, and domain-specific research results. Chat remains available for follow-up questions, refinement, and re-running research. Domain models remain frontend-independent, and no frontend technology choice is prescribed here.

## Temporal and Replay Invariants

`SimulationClock` is authoritative. Every historical read crosses `PointInTimeDataGateway`, which rejects `available_at > clock.now`. The scanner sees completed bars only. A decision after close T cannot fill before the next bar open. The model cannot choose or extend a cutoff. Journal reads enforce availability time and V1 exposes no prior decisions, theses, or outcomes to decision-making. All timestamps are aware UTC.

Tests deliberately seed future data and require it to remain unreachable. Prompts are not a security control.

Model parametric knowledge is a residual replay limitation: a model may remember historical events. Model-facing replay context should omit absolute calendar dates where they are not analytically required, while persisted evidence retains exact timestamps. Robustness experiments may blind or normalize price paths. This mitigates but cannot prove elimination of memorized historical knowledge, so reports must disclose it.

## Scanner Lock

V1 uses one hourly lower-Bollinger-band breach scanner. After each completed bar, emit one opportunity when close crosses from at/above to below `SMA(20) - 2 * population_standard_deviation(20)`; require 20 valid hourly closes and suppress repeat opportunities until price closes back at/above the lower band. The scanner supplies a candidate, not a trade signal.

This rule is transparent, intentionally untuned, volatility-scaled across assets, and active in both ranges and selloffs. It should create a manageable order of several opportunities per asset-month, but frequency is an unverified planning estimate and must be measured on development data before freezing configuration. In ranges it often identifies potentially reversible extremes; in downtrends it produces falling-knife candidates. That ambiguity gives the agent meaningful work: assess trend, regime, momentum, relative strength, volume, volatility, and nearby levels. Scanner-only measures naive dip buying; the agent is tested on whether it filters poor breaches without receiving different opportunities. If frequency is inadequate, changing parameters is a versioned design change made using development data only.

## Canonical Historical Data

Use the public Coinbase Exchange REST candles endpoint for the Coinbase spot pairs `BTC-USD`, `ETH-USD`, and `SOL-USD`, with granularity 3600 seconds. Persist immutable raw responses, then normalize them into a versioned local dataset. Coinbase is a recognizable USD spot venue, supplies the exact products and hourly aggregation, and requires no multi-venue reconciliation.

Limitations are material: requests are capped at 300 candles, candles can be incomplete, and intervals without ticks may be absent. Ingestion must request bounded nonoverlapping windows, deduplicate, sort, validate OHLC invariants, and produce a gap manifest. Never silently forward-fill OHLCV. A missing hour makes indicators depending on that window invalid; scanner/replay skip affected opportunities until the required contiguous warm-up is restored. Dataset integrity checks may inspect timestamps, schema, and gaps across all splits, but final-period prices, signals, and outcomes remain off limits during development.

## Decision and Risk Outcomes

`TradeDecision` is the final LLM-facing research assessment. It contains qualitative rationale, evidence and counterevidence references, confidence, uncertainties, critic response, abstention reasons, and exactly one of `TRADE|NO_TRADE|ABSTAIN`. It does not own runtime/journal identity, authoritative timestamps, financial arithmetic, execution fields, or references to deterministic trade-level/template contracts that do not yet exist.

- `TRADE`: research supports proceeding with the candidate long trade. It is not an order, execution authorization, risk approval, sizing approval, or portfolio authorization.
- `NO_TRADE`: Crypteo has enough evidence to actively reject the candidate. It is a substantive research conclusion, not an abstention.
- `ABSTAIN`: Crypteo cannot form a sufficiently reliable research conclusion because evidence is insufficient, conflicting, stale, unavailable, or unreliable, or because of research/tool failure, budget exhaustion, or validation failure.
- `RISK_REJECTED`: after `TradeDecision.TRADE`, deterministic authorization refused the candidate. It is a downstream risk outcome that the LLM cannot emit.

`TRADE`, `NO_TRADE`, and `ABSTAIN` remain distinct in research storage and evaluation. A `TRADE` flows to the deterministic risk gate, which either allows further simulated processing or produces `RISK_REJECTED`.

## Simulation and Risk

Slice 9 implements deterministic LONG risk authorization and sizing through `RiskService.authorize(TradeProposal, PortfolioSnapshot)`, independent of `TradeDecision`. Authorization uses current flat cash as capital and the exact gateway-visible completed candle close ending at `decision_at`, which must equal gateway time. Supplied stop/target levels must align to the price increment and satisfy `0 < stop < reference < target`; Slice 9 validates these levels but does not generate them. Deterministic stop/target generation remains unspecified and unimplemented and must be resolved before full replay integration; replay orchestration remains later work.

Let `C` be current cash, `r` the configured risk fraction, `d` the quantity increment, and `E`/`S` the estimated entry/stop execution prices from shared Slice 8 adverse slippage and tick rounding. With entry/exit fee rates `f_entry`/`f_exit`, cash per unit is `A = E * (1 + f_entry)` and estimated loss per unit is `L = A - S * (1 - f_exit)`. Quantity is `q = d * min(floor(C * r / (L * d)), floor(C / (A * d)))`, computed exactly; fewer than one increment is rejected. This is the largest aligned quantity satisfying both risk and cash constraints. Authorization is nonmutating and bounds estimated nongapped stop loss, not guaranteed realized loss. Actual next-open prices never enter sizing; Slice 8 owns gap affordability and realized execution outcomes. See [ADR-006](decisions/ADR-006-decision-time-risk-authorization.md) for the durable decision-time boundary.

Slice 8 consumes already-authorized LONG terms without an `Opportunity` or `TradeDecision` dependency. A decision after completed close T is eligible for the open of [T, T+1h), with the fill recorded only once that candle is gateway-visible. Market phase/time, candle availability, and recording time remain distinct; intrabar fills have no invented exact timestamp, and the broker never advances or owns a second clock. See [ADR-005](decisions/ADR-005-completed-bar-execution-reconciliation.md) for the reconciliation rationale and contract.

Entries use configurable adverse slippage and fees, validated quantity/price increments, and exact Decimal accounting. Stops and targets activate after entry. Observed opening gaps fill using the opening price and take precedence over later intrabar touches; otherwise simultaneous stop/target touches resolve stop-first for a long position. A versioned maximum holding duration counts the entry candle as holding bar one; stop/target takes priority over a time exit at the final holding bar's close. An entry candle missing or unavailable at its expected close causes terminal rejection, never rollover or revival on delayed data. Missing holding data halts financial processing with the position unresolved. Slice 8 preserves immutable snapshots and append-only order, fill, and outcome histories within execution state; durable append-only journaling of the complete lifecycle remains a later V1 responsibility. Every baseline uses exactly the same risk and execution configuration.

Only `SimulatedBroker` is implemented. No credentials, private keys, trading permissions, exchange adapter package, or real order path may exist.

## Journal and Reproducibility

Persist from day one: opportunities, evidence, tool calls and responses, theses, critiques, decisions, risk results, orders, fills, exits, outcomes, post-trade reviews, graph traces, and experiment metadata. V1 journal access is for audit, evaluation, debugging, and post-trade analysis only; there is no decision memory, embedding, or vector retrieval.

`ResearchThesis`, `Critique`, and `TradeDecision` contain model-facing research content, not `run_id`, `decision_id`, authoritative cutoffs, or runtime, graph, journal, decision, and experiment timestamps. The runtime/journal layer associates them with run and opportunity/request identity, authoritative timestamps, graph traces, experiment metadata, and applicable model/prompt/tool configuration. `EvidenceItem` retains `evidence_id` because theses and decisions reference evidence; `Opportunity` retains its deterministic `opportunity_id` because downstream processing must reference its input. The LLM never invents or maintains runtime linkage.

The first concrete experiment manifest is introduced with Slice 10's scanner-only replay and records only configuration that exists then: code and dataset versions, scanner configuration/version, applicable risk configuration/version, simulation/execution configuration, and experiment mode. Later agentic replay slices extend it with prompts, models, research tools, and agent configuration as those components exist; Slice 3 does not define a speculative `RunManifest`.

Each experiment ultimately records the applicable versioned configuration, asset universe and period, requests/responses, decisions, and traces. `FROZEN_RESPONSE` reproduction reuses stored model/tool artifacts to replay deterministic components. `FRESH_INFERENCE` preserves information boundaries and configuration but invokes the model again. Reports label them separately.

## Evaluation Protocol

Process quality and trading performance are independent. Process metrics include schema validity, grounding, unsupported claims, tool success, temporal validity, abstention appropriateness, critic effectiveness, contradiction detection, consistency, efficiency, latency, tokens, cost, and recovery. Trading metrics derive from the equity curve and include return, drawdown, volatility, downside deviation, cautious Sharpe/Sortino, profit factor, win rate, expectancy, average win/loss, exposure, turnover, costs, loss streaks, frequency, exit reason, and breakdowns by asset, regime, and confidence.

Scanner-only is the mandatory first baseline. Later baselines may add deterministic filtering, frequency-matched random selection, LLM without tools, LLM without critic, always-reject, and contextual buy-and-hold. Comparable runs share opportunities, cutoff, risk, fees, slippage, and simulation assumptions.

Use chronological walk-forward splits:

- Development: `2021-07-01T00:00:00Z` through `2023-06-30T23:00:00Z`.
- Validation: `2023-07-01T00:00:00Z` through `2024-06-30T23:00:00Z`.
- Untouched test: `2024-07-01T00:00:00Z` through `2026-06-30T23:00:00Z`.

The common start follows SOL-USD's June 2021 Coinbase launch and leaves a short availability buffer. Development spans late bull, 2022 contraction, and early recovery; validation spans consolidation and renewed expansion; test is long enough to contain more than one condition. These labels are qualitative prior knowledge, not statistical regime labels; deterministic regime calculations must define actual subsets. The split has only one broad market cycle and three correlated assets, so uncertainty intervals and claims must remain conservative. Prompts, scanner, risk, and design freeze before final-test outcome inspection.

## Testing Policy

Normal CI mocks model responses. Unit, property, golden, contract, graph-routing, integration, and replay tests cover indicators, scanner, sizing, accounting, costs, stops/targets, limits, timing, schemas, abstention, critic paths, deterministic replay, leakage, baseline fairness, and opportunity-to-close behavior. Model-backed evaluations are separate and versioned.

## Implementation Slices

Each slice ends with passing automated tests and a usable repository state.

1. Project quality shell:mpackage metadata, formatter/linter/type-checker/pytest configuration, CI, and architecture-invariant tests; acceptance: documented local checks pass on an empty package.
2. Market domain schemas: UTC timestamps, pair/venue/timeframe enums, candle and dataset-manifest models; acceptance: serialization and invalid-value tests pass.
3. Research-domain contracts: `Opportunity`, evidence, thesis, critique, abstention, and final research-decision contracts; acceptance: internal timestamp consistency, evidence grounding, outcome-dependent decision rules, and rejection of forbidden calculated or runtime-owned LLM fields pass.
4. Clock and temporal gateway: simulation clock plus in-memory provider; acceptance: completed-bar and planted-future-data tests pass before real ingestion exists.
5. Coinbase ingestion: paginated raw capture, normalization, validation, version hashing, and gap report; acceptance: fixture-based ingestion is deterministic and never forward-fills.
6. Deterministic analytics: only calculations required by scanner and initial tools; acceptance: independent golden examples and gap/warm-up behavior pass.
7. Locked scanner: Bollinger breach through `OpportunityScanner`; acceptance: crossover, reset, duplicate suppression, and cutoff tests pass.
8. Execution kernel: orders, fills, portfolio ledger, fees/slippage, gaps, intrabar priority, and time exits; acceptance: accounting invariants and scenario tables pass.
9. Risk gate: versioned sizing and portfolio constraints; acceptance: known boundary cases approve/reject deterministically with reason codes.
10. Scanner-only replay: event loop, equity curve, core metrics, and experiment manifest; acceptance: a fixture period reproduces byte-stable deterministic results.
11. Market tool contracts: typed bounded tools returning normalized evidence; acceptance: provenance, cutoff, artifact, stale-data, and failure contracts pass.
12. Minimal LangGraph: investigate, draft, critic, finalize with mocked model adapter and budgets; acceptance: all routes, retries, fail-closed abstention, and schema repair limits pass.
13. Append-only journal: durable local persistence for the complete lifecycle; acceptance: linkage/audit tests pass and decision-time reads cannot access memory.
14. Integrated agent replay: same opportunities flow through agent, risk, broker, and journal; acceptance: frozen-response replay is deterministic and baseline inputs are identical.
15. Agent evaluation: labeled process cases, graders, cost/latency capture, critic and no-tool/no-critic ablations as selected; acceptance: versioned report separates process and trading metrics.
16. Walk-forward runner: enforce split roles and final-test access guard; acceptance: development/validation reports run while outcome-level test access remains disabled.
17. Final experiment and presentation: unlock test once, generate reproducible report and architecture narrative; acceptance: clean-environment instructions reproduce frozen results and disclose limitations.

## V1 Definition of Done

V1 is complete only when it:

- loads and validates versioned Coinbase BTC/ETH/SOL hourly data in UTC;
- enforces the simulation clock, completed-bar rule, and future-data isolation;
- generates candidates with the single locked scanner;
- runs scanner-only through the paper simulator;
- exposes bounded typed market tools and the locked minimal LangGraph workflow;
- produces grounded, critiqued, schema-valid decisions and distinct risk outcomes;
- applies deterministic sizing/risk and next-open fills with fees, slippage, stops, targets, gaps, and time exits;
- journals the complete append-only lifecycle and reproducibility metadata;
- runs frozen-response and fresh-inference historical experiments;
- reports process metrics separately from equity-curve trading metrics;
- compares Crypteo fairly with scanner-only and any agreed additional baseline;
- passes automated timing, leakage, accounting, replay, tool, graph, and integration tests; and
- produces a reproducible evaluation report on locked walk-forward periods.

## Explicitly Postponed

Post-V1: multiple scanners, shorts, live ingestion/paper mode, alerts, all non-market research, bounded specialist-agent orchestration, interactive dashboard/UI/authentication, embeddings/vector or thesis memory, PostgreSQL unless demonstrated necessary, multiple venues, order books/ticks, limit/partial fills, Docker/cloud unless reproducibility requires it, and all real-exchange connectivity. Recursive delegation and unrestricted autonomous planning remain excluded.

Future live mode reuses the core domain services, research engine, evidence model, agent workflow, journal, and applicable evaluation and risk infrastructure. Opportunity-led live research may reuse the scanner; trader-led research does not require one. Live operation fundamentally changes the clock and data providers, not the core research contracts.
