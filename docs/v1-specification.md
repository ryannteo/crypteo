# Crypteo V1 Architecture Specification

Status: architecture locked for V1. Changes to a hard invariant require an architecture decision record.

## Product and Research Question

Crypteo is an agentic crypto trade-analysis and paper-trading platform. Its primary user is a crypto trader or researcher seeking an auditable AI second opinion on candidate setups. AI researchers and portfolio reviewers are secondary users.

V1 is specifically an **agentic market-analysis and trade-selection system**, not a general research platform. It asks:

> Given the same deterministic trade opportunities, does bounded agentic market analysis improve trade-selection quality relative to deterministic baselines under identical execution and risk assumptions?

V1 tests selection, not autonomous strategy invention or LLM price prediction. It makes no profitability claim and can never trade real money.

Long term, Crypteo may test whether progressively richer, temporally valid research improves selection. Market, derivatives, on-chain, news, fundamental, tokenomics, macro, and sentiment capabilities should contribute normalized evidence to one integrated thesis—not independent buy/sell votes. A capability may later be a tool, service, node, subgraph, or agent only when its complexity warrants that form.

## V1 Scope and Market Environment

- Coinbase Exchange spot market; `BTC-USD`, `ETH-USD`, and `SOL-USD`.
- Completed 1-hour OHLCV candles on a timezone-aware UTC timeline.
- Exactly one deterministic scanner shared by Crypteo and scanner-only baseline.
- Long positions only; domain direction remains an enum to permit a later `SHORT` value.
- Market-derived evidence only: price, returns, volume, trend, momentum, volatility, relative strength, support/resistance, and deterministic regime measures.
- Historical replay is the first complete product mode.
- Decisions are `TRADE`, `NO_TRADE`, or `ABSTAIN`; `RISK_REJECTED` is a separate deterministic outcome.

Excluded from V1 research are web/news, social, on-chain, derivatives, macro, protocol/fundamental, and tokenomics data.

## Hard Responsibility Boundary

The LLM chooses permitted tools, interprets evidence, identifies conflict and assumptions, constructs bull/bear cases and a falsifiable thesis, critiques it, selects a decision, and writes qualitative post-trade analysis.

Deterministic Python owns OHLCV, indicators, returns, volatility, scanning, all arithmetic, candidate price levels, reward/risk, sizing, exposure, portfolio/risk rules, validation, fills, fees, slippage, stops, targets, time exits, accounting, P&L, clocks, replay, and metrics.

The invariant is: **LLM for judgment and orchestration; deterministic software for calculation, authorization, execution, and accounting.** A `TRADE` decision references deterministically calculated entry/stop/target candidates or a versioned risk template; it does not invent calculated values, quantity, P&L, exposure, reward/risk, or execution price.

## Components and Extension Seams

Crypteo is a modular monolith composed of point-in-time data, deterministic analytics and scanner, research tools, LangGraph workflow, risk service, simulated broker/portfolio, append-only journal, replay engine, and evaluation harness.

The minimal extension seams are:

- `OpportunityScanner.scan(context) -> opportunities`
- `ResearchTool.invoke(request, runtime_context) -> evidence`
- `Broker.submit(order_intent, market_event) -> execution_events`
- `Clock.now -> UTC datetime`
- market-data provider behind a `PointInTimeDataGateway`

No plugin framework, dependency-injection framework, or speculative capability hierarchy is required. Future research capabilities register tools that emit the same evidence representation. They may join historical replay only if classified `POINT_IN_TIME_REPLAY_SAFE`; sources without defensible historical availability are `LIVE_ONLY`.

## Evidence Contract

Every normalized `EvidenceItem` contains `evidence_id`, category/type, source, `observed_at`, `available_at`, provenance, tool/query parameters, dataset/source version, immutable structured payload or artifact reference, concise relevance, and optional quality/confidence metadata. Large dataframes and raw datasets stay outside graph state.

Tool runtime, not the model, injects asset, venue, timeframe, and authoritative cutoff. Tool outputs expose warnings and temporal provenance. The thesis and critic consume evidence contracts rather than market-provider objects.

## Initial LangGraph

```text
Opportunity -> Investigate (bounded tools) -> Draft thesis -> Critic
            -> Final schema-validated decision
```

Investigation dynamically selects typed market tools. The critic is a distinct model call receiving the evidence and draft. There is no planning agent, specialist-agent team, delegation, debate, recursive hierarchy, or repeated autonomous research loop.

Configurable limits cover tool calls, total tokens, graph iterations, retries, model calls, and decision age. Exhaustion, malformed output, unavailable/stale evidence, or unrecoverable tool/model failure fails closed to `ABSTAIN`. The graph may retry only within its fixed budget.

Graph state contains IDs and compact structured results: run/opportunity context, evidence references, tool-call records, draft thesis, critique, final decision, budgets consumed, status, and errors. It does not contain raw candle collections.

## Temporal and Replay Invariants

`SimulationClock` is authoritative. Every historical read crosses `PointInTimeDataGateway`, which rejects `available_at > clock.now`. The scanner sees completed bars only. A decision after close T cannot fill before the next bar open. The model cannot choose or extend a cutoff. Journal reads enforce availability time and V1 exposes no prior decisions, theses, or outcomes to decision-making. All timestamps are aware UTC.

Tests deliberately seed future data and require it to remain unreachable. Prompts are not a security control.

Model parametric knowledge is a residual replay limitation: a model may remember historical events. Model-facing replay context should omit absolute calendar dates where they are not analytically required, while persisted evidence retains exact timestamps. Robustness experiments may blind or normalize price paths. This mitigates but cannot prove elimination of memorized historical knowledge, so reports must disclose it.

## Scanner Lock

V1 uses one **hourly lower-Bollinger-band breach scanner**. After each completed bar, emit one opportunity when close crosses from at/above to below `SMA(20) - 2 * population_standard_deviation(20)`; require 20 valid hourly closes and suppress repeat opportunities until price closes back at/above the lower band. The scanner supplies a candidate, not a trade signal.

This rule is transparent, intentionally untuned, volatility-scaled across assets, and active in both ranges and selloffs. It should create a manageable order of several opportunities per asset-month, but frequency is an unverified planning estimate and must be measured on development data before freezing configuration. In ranges it often identifies potentially reversible extremes; in downtrends it produces falling-knife candidates. That ambiguity gives the agent meaningful work: assess trend, regime, momentum, relative strength, volume, volatility, and nearby levels. Scanner-only measures naive dip buying; the agent is tested on whether it filters poor breaches without receiving different opportunities. If frequency is inadequate, changing parameters is a versioned design change made using development data only.

## Canonical Historical Data

Use the public Coinbase Exchange REST candles endpoint for the Coinbase spot pairs `BTC-USD`, `ETH-USD`, and `SOL-USD`, with granularity 3600 seconds. Persist immutable raw responses, then normalize them into a versioned local dataset. Coinbase is a recognizable USD spot venue, supplies the exact products and hourly aggregation, and requires no multi-venue reconciliation.

Limitations are material: requests are capped at 300 candles, candles can be incomplete, and intervals without ticks may be absent. Ingestion must request bounded nonoverlapping windows, deduplicate, sort, validate OHLC invariants, and produce a gap manifest. Never silently forward-fill OHLCV. A missing hour makes indicators depending on that window invalid; scanner/replay skip affected opportunities until the required contiguous warm-up is restored. Dataset integrity checks may inspect timestamps, schema, and gaps across all splits, but final-period prices, signals, and outcomes remain off limits during development.

## Decision and Risk Outcomes

The validated decision contains opportunity/run identity, evidence and counterevidence references, thesis summary, bull/bear cases, assumptions, falsifiable invalidations, uncertainty, confidence, horizon, `TRADE|NO_TRADE|ABSTAIN`, and—only for `TRADE`—`LONG` plus selected deterministic level/template references.

- `TRADE`: evidence supports the long candidate.
- `NO_TRADE`: evidence supports rejecting it.
- `ABSTAIN`: evidence is insufficient, conflicting, stale, unavailable, or unreliable.
- `RISK_REJECTED`: the agent chose `TRADE`, but deterministic authorization refused it.

These remain separate in storage and evaluation.

## Simulation and Risk

Decisions occur after a completed close and market entries fill at the next open with configurable adverse slippage and fees. Deterministic policy calculates size from stop distance and portfolio risk. Stops and targets activate after entry. Gaps fill at the first available price. If stop and target are both touched in one hourly bar, the stop is assumed first for a long position. A versioned maximum holding duration causes a time exit. Orders, fills, rejections, exits, and portfolio transitions are append-only. Every baseline uses exactly the same risk and execution configuration.

Only `SimulatedBroker` is implemented. No credentials, private keys, trading permissions, exchange adapter package, or real order path may exist.

## Journal and Reproducibility

Persist from day one: opportunities, evidence, tool calls and responses, theses, critiques, decisions, risk results, orders, fills, exits, outcomes, post-trade reviews, graph traces, and experiment metadata. V1 journal access is for audit, evaluation, debugging, and post-trade analysis only; there is no decision memory, embedding, or vector retrieval.

Each experiment records code and dataset versions; scanner/risk/simulation/execution, prompt/model/tool configurations; asset universe and period; requests/responses; decisions; and traces. `FROZEN_RESPONSE` reproduction reuses stored model/tool artifacts to replay deterministic components. `FRESH_INFERENCE` preserves information boundaries and configuration but invokes the model again. Reports label them separately.

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

1. **Project quality shell:** package metadata, formatter/linter/type-checker/pytest configuration, CI, and architecture-invariant tests; acceptance: documented local checks pass on an empty package.
2. **Market domain schemas:** UTC timestamps, pair/venue/timeframe enums, candle and dataset-manifest models; acceptance: serialization and invalid-value tests pass.
3. **Decision/evidence schemas:** opportunity, evidence, thesis, critique, outcome, and run-manifest contracts; acceptance: forbidden calculated LLM fields and invalid states are rejected.
4. **Clock and temporal gateway:** simulation clock plus in-memory provider; acceptance: completed-bar and planted-future-data tests pass before real ingestion exists.
5. **Coinbase ingestion:** paginated raw capture, normalization, validation, version hashing, and gap report; acceptance: fixture-based ingestion is deterministic and never forward-fills.
6. **Deterministic analytics:** only calculations required by scanner and initial tools; acceptance: independent golden examples and gap/warm-up behavior pass.
7. **Locked scanner:** Bollinger breach through `OpportunityScanner`; acceptance: crossover, reset, duplicate suppression, and cutoff tests pass.
8. **Execution kernel:** orders, fills, portfolio ledger, fees/slippage, gaps, intrabar priority, and time exits; acceptance: accounting invariants and scenario tables pass.
9. **Risk gate:** versioned sizing and portfolio constraints; acceptance: known boundary cases approve/reject deterministically with reason codes.
10. **Scanner-only replay:** event loop, equity curve, core metrics, and experiment manifest; acceptance: a fixture period reproduces byte-stable deterministic results.
11. **Market tool contracts:** typed bounded tools returning normalized evidence; acceptance: provenance, cutoff, artifact, stale-data, and failure contracts pass.
12. **Minimal LangGraph:** investigate, draft, critic, finalize with mocked model adapter and budgets; acceptance: all routes, retries, fail-closed abstention, and schema repair limits pass.
13. **Append-only journal:** durable local persistence for the complete lifecycle; acceptance: linkage/audit tests pass and decision-time reads cannot access memory.
14. **Integrated agent replay:** same opportunities flow through agent, risk, broker, and journal; acceptance: frozen-response replay is deterministic and baseline inputs are identical.
15. **Agent evaluation:** labeled process cases, graders, cost/latency capture, critic and no-tool/no-critic ablations as selected; acceptance: versioned report separates process and trading metrics.
16. **Walk-forward runner:** enforce split roles and final-test access guard; acceptance: development/validation reports run while outcome-level test access remains disabled.
17. **Final experiment and presentation:** unlock test once, generate reproducible report and architecture narrative; acceptance: clean-environment instructions reproduce frozen results and disclose limitations.

## V1 Definition of Done

V1 is complete only when it:

- loads and validates versioned Coinbase BTC/ETH/SOL hourly data in UTC;
- enforces the simulation clock, completed-bar rule, and future-data isolation;
- generates candidates with the single locked scanner;
- runs scanner-only through the paper simulator;
- exposes bounded typed market tools and the five-node LangGraph;
- produces grounded, critiqued, schema-valid decisions and distinct risk outcomes;
- applies deterministic sizing/risk and next-open fills with fees, slippage, stops, targets, gaps, and time exits;
- journals the complete append-only lifecycle and reproducibility metadata;
- runs frozen-response and fresh-inference historical experiments;
- reports process metrics separately from equity-curve trading metrics;
- compares Crypteo fairly with scanner-only and any agreed additional baseline;
- passes automated timing, leakage, accounting, replay, tool, graph, and integration tests; and
- produces a reproducible evaluation report on locked walk-forward periods.

## Explicitly Postponed

Post-V1: multiple scanners, shorts, live ingestion/paper mode, alerts, all non-market research, multi-agent teams, delegation/recursive planning, embeddings/vector or thesis memory, PostgreSQL unless demonstrated necessary, FastAPI/UI/authentication, multiple venues, order books/ticks, limit/partial fills, Docker/cloud unless reproducibility requires it, and all real-exchange connectivity.

Future live mode must reuse domain services, scanner, agent, evidence, risk, broker contract, journal, portfolio, and evaluation logic; only clock and providers fundamentally change.
