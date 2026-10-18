# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

AGENTS.md (imported above) holds the mission, safety boundary, temporal-integrity rules, and documentation policy. This file adds operational and architectural context.

## Commands

Python 3.12+. Setup: `python3 -m venv .venv && source .venv/bin/activate && python -m pip install -e ".[dev]"`.

CI runs exactly these four; run all of them before considering work done:

```bash
python -m ruff format --check .   # apply with: python -m ruff format .
python -m ruff check .
python -m mypy                    # strict; config covers both src/ and tests/
python -m pytest                  # ~3,000 cases, ~3 minutes
```

Single test or subset:

```bash
python -m pytest tests/test_replay.py::test_name
python -m pytest "tests/test_research_persistence.py::test_cache_attempts_and_budget[8]"
python -m pytest tests/test_research_workflow.py -k budget
```

`tests/` is on `pythonpath`, so test modules import helpers from each other: `tests/research_support.py` (`ScriptedModel`, fixtures, `CODE`) and `tests/test_replay.py` (`bar`, `history`, `dataset`, `config`). No test touches the network: Coinbase goes through an injected `HttpTransport`; Claude uses a real `AsyncAnthropic` over `httpx2.MockTransport` (`research_support.anthropic_client`).

There is no CLI or app entrypoint. Everything is a Python API (see README for the public replay and research entrypoints).

## Architecture

Modular monolith in `src/crypteo/`. `domain/` holds frozen Pydantic contracts; top-level modules are services. `domain/` is not a pure leaf layer: it imports `experiment`, `research_model`, and `_execution_costs`, and lazily imports research modules inside validators.

### Contracts validate by recomputation

Models use `frozen=True, extra="forbid", revalidate_instances="always"` and recompute derived values in validators. Examples: `RiskAuthorization` re-derives prices and proves quantity is maximal; `TradeTerms` re-floors levels; `PortfolioSnapshot` reconciles the whole ledger; `ResearchToolSuccess` recomputes the evidence ID. Services defensively revalidate inputs with `Model.model_validate(x.model_dump())`. When adding a derived field, add its reconciliation check.

Money uses `_add`, `_subtract`, `_multiply` from `domain/_execution_arithmetic.py`. These are exact contexts that trap `Inexact`. Ratios use private 50-digit half-even contexts (`analytics.py`, `domain/_market_research_arithmetic.py`, `replay_metrics.py`). Never rely on the ambient Decimal context; binary floats are rejected at contract boundaries.

### One replay engine, three modes

`replay._replay_engine` is a generator that owns the whole hourly lifecycle and `yield`s each admitted `Opportunity`:

- `run_scanner_only` (sync) sends back `None`.
- `research_replay.run_research_replay` (async) sends back a validated `ReplayResearchRecord` from fresh durable inference or frozen artifacts.

Per boundary T:

1. Broker reconciliation.
2. Scan BTC → ETH → SOL and record a delivery disposition for every candidate.
3. Sequential candidate processing: research (if any) → terms → proposal → risk → immediate submit.
4. Observation, then advance one hour.

Do not fork this lifecycle per mode. `ReplayResult`'s validator re-runs `replay_validation.validate_lifecycle` and recomputes all metrics. A new record kind therefore needs updates in:

- `PAYLOAD_KINDS` and `ReplayRecord` time mapping (`domain/replay.py`)
- the grammar in `replay_validation.py`
- `replay_metrics.py`

### Bounded research workflow

`research_workflow._run_opportunity_research` compiles a 5-node LangGraph per run. Nodes decide their own target and append a `NodeTransition` to the trace; `route()` just reads the last transition (edges have no static path map). Budgets and limits are `Literal`-typed in `ResearchBudgetPolicy` and `ResearchWorkflowConfig`, so they are not tunable.

`run_durable_opportunity_research` injects a `ResearchJournal`. Every append re-projects and validates the full event history through `research_journal.project()`. `JournalProjection.apply` independently re-encodes the graph transition map (including the re-ask self-transitions) and the budget numbers (8 tool attempts, 9 investigation calls, 2 invalid tool requests, 2 output re-asks, 14 model calls). Changing graph shape or budgets requires changing both files.

### Stored research is validated against current code

There is no migration path. On `load_terminal` and `inspect_run`, the journal validator rebuilds each expected `ModelRequest` from the current `research_prompts`, `structured_schema` (Pydantic JSON schemas, including class docstrings), and `research_tools.tool_definitions`, then compares exactly. So the following all make previously stored runs fail to load and change `ResearchReplayManifest` policy identity:

- editing prompt text
- editing tool descriptions
- editing any field or docstring on `InvestigationCompletion`, `ResearchThesis`, `Critique`, or `TradeDecision`

Separately, `experiment.current_code_identity()` hashes all `src/crypteo/**/*.py` plus `pyproject.toml` and requires a git checkout. Any source edit changes replay `run_id`s and blocks frozen reuse of earlier artifacts. Tests pass an explicit `CODE`.

Observed provider response bodies can only be decoded and validated for provider `"anthropic"`, the Claude adapter (`research_journal.PROVIDER_CODECS`, used by `validate_prepared` and `validate_observation`). Test fakes use provider `"fake"` and must not call the `observe` callback.

### Failure semantics are part of the contract

- Operational research failure (provider error, budget exhaustion, interrupted run, storage or admission failure) aborts replay. It must never be converted into `NO_TRADE`, model `ABSTAIN`, or a skipped candidate.
- `ResearchStorageError`, `ResearchInvariantError`, clock movement, and programmer errors raise.
- Only typed provider replies become runtime-`ABSTAIN` interruptions, and only in standalone research.

### Identities and encodings

All IDs are SHA-256 hashes of canonical JSON: capture, dataset version, acquisition, opportunity, evidence, replay `run_id`, order, `research_run_id`, and policy digests. There are deliberately separate encodings, so don't mix them:

- `coinbase_dataset` canonical rows
- `experiment.canonical_bytes` / `identity` (replay)
- `domain.market_research._canonical` (evidence)
- `research_model.render_json` (journal, preserves Decimal spelling)

### Performance characteristic

Replay cost and `ReplayResult` size grow quadratically with replay length:

- The scanner recomputes full visible history every boundary.
- Every boundary re-records `DUPLICATE` deliveries for all past opportunities.
- Each observation embeds the full portfolio ledger.

Roughly 1,000 single-asset hours ≈ 15 s and ~48 MB of canonical JSON. Keep test replay windows to a few hours.

## Docs map

- `docs/v1-specification.md`: authoritative behavioral semantics (formulas, budgets, rejection precedence, replay grammar).
- `docs/architecture.md`: component boundaries.
- `docs/data-pipeline.md`: Coinbase storage layout and identity.
- `docs/decisions/`: ADRs.

Update the relevant current-state doc in the same change as behavior.
