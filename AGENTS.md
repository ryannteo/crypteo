# Repository Guidelines

## Mission and Safety Boundary

Crypteo is an agentic crypto research copilot for traders and researchers with existing market knowledge. Its purpose is to investigate market opportunities, gather and synthesize evidence, challenge trade theses, surface uncertainty and counterevidence, and support more informed trading decisions.

Crypteo's long-term product is a live, up-to-date research assistant. Historical replay and paper trading are evaluation infrastructure used to test whether Crypteo's research and reasoning improve trade selection under controlled conditions; they are not the product's primary identity.

Crypteo must never route real orders, store exchange trading credentials, or expose withdrawal/private-key capabilities. Treat any proposed real-broker adapter as out of scope. Trading decisions remain with the user; Crypteo provides research, analysis, and simulated evaluation.

## Architecture Boundaries

Keep the repository a modular monolith under `src/crypteo/`, with tests mirroring modules under `tests/`. Put architectural decisions in `docs/`, versioned experiment settings in `configs/`, and local datasets/artifacts in ignored directories.

Crypteo should be designed as an extensible research platform. V1 implements market-analysis research only, but future capabilities may include derivatives, on-chain, news/catalyst, protocol/fundamental, tokenomics, macro, and sentiment research. Do not implement these prematurely, but avoid coupling thesis, critique, evidence, or evaluation logic directly to market-analysis-specific implementations.

Research capabilities should primarily produce normalized evidence rather than independent buy/sell votes. The integrated thesis and critique workflow should reason across evidence from available research domains.

Use the LLM for research orchestration and qualitative judgment: deciding what evidence is needed, tool selection, evidence interpretation, bull/bear cases, assumptions, uncertainty, thesis construction, critique, and structured trade assessment such as `TRADE`/`NO_TRADE`/`ABSTAIN` in historical evaluation.

Deterministic Python owns market calculations, scanning, arithmetic, risk authorization, sizing, simulation, accounting, replay, and metrics. Never ask a model to calculate a value that code can calculate reliably.

V1 LangGraph nodes should be few, explicit, bounded, and typed; do not add specialist agents or multi-agent orchestration to V1. Long term, the bounded research coordinator and specialist-agent architecture described in `docs/v1-specification.md` is permitted, but planning-agent hierarchies, recursive delegation, unrestricted autonomous loops, and independently trading agents remain prohibited. Do not add services, infrastructure, or abstraction layers without a demonstrated need. Avoid generic `utils` modules and deep class hierarchies.

The architecture should support two eventual research entry modes without duplicating the research engine:

1. Trader-led research: the user supplies a market question, trade idea, or thesis for Crypteo to investigate.
2. Opportunity-led research: a deterministic scanner or other discovery mechanism supplies a candidate setup.

Both should eventually converge on the same evidence, thesis, critique, and assessment workflow.

## Temporal Integrity and Evidence

Historical time is a security boundary. Every time-dependent read must pass through the authoritative simulation clock and point-in-time gateway. Tools must not accept a model-controlled cutoff or return records whose `available_at` exceeds simulated time. All timestamps are timezone-aware UTC. Tests must plant future records and prove they remain inaccessible.

Represent all research through a normalized evidence model containing provenance, evidence category, observation and availability times, query parameters, source/dataset version, and an immutable payload or artifact reference. Do not place large raw datasets in graph state.

The common evidence representation must be usable by both V1 market-analysis tools and future research capabilities.

Future research sources must be classified as either:

- point-in-time replay safe, meaning historical availability can be established and the source may participate in historical evaluation; or
- live only, meaning it may be used in future live research but must not be introduced into historical replay without credible temporal guarantees.

V1 decisions must not retrieve prior theses, decisions, or outcomes. Historical journal data is initially for auditing, evaluation, debugging, and post-trade analysis only.

## Development and Quality

Target Python 3.12+, Pydantic, LangGraph, and pytest. Use four-space indentation, type annotations, small focused modules, `snake_case` functions/files, and `PascalCase` types. Once configured, run the repository formatter, linter, type checker, and `pytest` before every pull request. Mock LLM calls in normal CI; keep model-backed evaluations separate and versioned.

Every behavior change needs tests. Prioritize unit tests for deterministic finance logic, contract tests for research tools and schemas, graph-routing tests, replay determinism, baseline fairness, temporal leakage, and end-to-end simulation tests.

Financial state transitions, evidence records, research traces, and journal records must be append-only and auditable where appropriate.

## Reproducibility, Secrets, and Reviews

Record code, data, scanner, risk, simulation, prompt, model, and tool versions for every experiment, including tool requests/responses and graph traces.

Historical evaluation must distinguish between:

- frozen-response reproduction, where stored model/tool artifacts are reused; and
- fresh-inference reproduction, where the same historical information boundary is preserved but model inference is performed again.

Never commit API keys, populated environment files, private keys, exchange credentials, or raw licensed datasets. Provide sanitized `.env.example` files when needed.

Use short imperative commits, such as `Enforce point-in-time candle access`. Pull requests should describe the invariant affected, tests run, configuration/schema changes, and reproducibility impact. Include evaluation artifacts only when they are deterministic, appropriately sized, and safe to redistribute.
