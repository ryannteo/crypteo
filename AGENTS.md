# Repository Guidelines

## Mission and Safety Boundary

Crypteo is a Python research platform for agentic crypto trade analysis, historical replay, and paper trading. It must never route real orders, store exchange trading credentials, or expose withdrawal/private-key capabilities. Treat any proposed real-broker adapter as out of scope.

## Architecture Boundaries

Keep the repository a modular monolith under `src/crypteo/`, with tests mirroring modules under `tests/`. Put architectural decisions in `docs/`, versioned experiment settings in `configs/`, and local datasets/artifacts in ignored directories.

Use the LLM only for research orchestration and qualitative judgment: tool selection, evidence interpretation, bull/bear cases, thesis construction, critique, and `TRADE`/`NO_TRADE`/`ABSTAIN`. Deterministic Python owns market calculations, scanning, arithmetic, risk authorization, sizing, simulation, accounting, replay, and metrics. Never ask a model to calculate a value that code can calculate reliably.

LangGraph nodes should be few, explicit, bounded, and typed. Do not add agents, services, infrastructure, or abstraction layers without a demonstrated V1 need. Avoid generic `utils` modules and deep class hierarchies.

## Temporal Integrity and Evidence

Historical time is a security boundary. Every time-dependent read must pass through the authoritative simulation clock and point-in-time gateway. Tools must not accept a model-controlled cutoff or return records whose `available_at` exceeds simulated time. All timestamps are timezone-aware UTC. Tests must plant future records and prove they remain inaccessible.

Represent research through normalized evidence containing provenance, observation and availability times, query parameters, source/dataset version, and an immutable payload or artifact reference. Do not place large raw datasets in graph state. V1 decisions must not retrieve prior theses, decisions, or outcomes.

## Development and Quality

Target Python 3.12+, Pydantic, LangGraph, and pytest. Use four-space indentation, type annotations, small focused modules, `snake_case` functions/files, and `PascalCase` types. Once configured, run the repository formatter, linter, type checker, and `pytest` before every pull request. Mock LLM calls in normal CI; keep model-backed evaluations separate and versioned.

Every behavior change needs tests. Prioritize unit tests for deterministic finance logic, contract tests for tools and schemas, graph-routing tests, replay determinism, baseline fairness, and end-to-end simulation tests. Financial state transitions and journal records must be append-only and auditable.

## Reproducibility, Secrets, and Reviews

Record code, data, scanner, risk, simulation, prompt, model, and tool versions for every experiment, including tool requests/responses and graph traces. Never commit API keys, populated environment files, private keys, or raw licensed datasets; provide sanitized `.env.example` files when needed.

Use short imperative commits, such as `Enforce point-in-time candle access`. Pull requests should describe the invariant affected, tests run, configuration/schema changes, and reproducibility impact. Include evaluation artifacts only when they are deterministic, appropriately sized, and safe to redistribute.
