# Crypteo

[![CI](https://github.com/ryannteo/crypteo/actions/workflows/ci.yml/badge.svg)](https://github.com/ryannteo/crypteo/actions/workflows/ci.yml)

Crypteo is an agentic crypto research copilot in development for traders and
researchers with market knowledge. It is designed to gather evidence, challenge
trade theses, and surface uncertainty to support informed trading decisions.
Trading decisions remain with the user. The long-term product is a live research
assistant; historical replay and paper trading provide its evaluation lab.

## Current status

The repository implements deterministic foundations and bounded AI research:

- Validated market and research contracts, including normalized evidence, theses,
  critiques, and research assessments.
- Authoritative simulation time and point-in-time access to completed candles.
- Coinbase historical candle retrieval, normalization, gap reporting, and immutable
  local raw, canonical, and acquisition artifacts.
- Deterministic Bollinger analytics and a lower-band crossover opportunity scanner.
- Seven deterministic market-research tools producing point-in-time evidence:
  trend, momentum, volatility, Bollinger position, recent levels, volume context,
  and relative strength.
- Bounded opportunity-led model research: the model chooses deterministic tools,
  then produces a structured thesis, a separate critique, and a final research
  assessment. A Claude adapter implements the provider boundary; see the
  [workflow specification](docs/v1-specification.md#bounded-model-backed-research).
- Durable research lifecycle persistence in SQLite, retaining provider observations,
  tool resolutions, and stage outputs with immutable terminal research artifacts.
- Simulated LONG execution and portfolio accounting with fees, slippage,
  stop/target and time exits, and conservative missing-data handling.
- Deterministic market-derived stop/target terms and decision-time LONG risk
  authorization, positive estimated net target reward checks, and position sizing.
- One historical replay lifecycle with scanner-only baseline, fresh model research,
  and frozen validated-artifact modes, marked equity, and financial/research metrics.
- Mandatory durable research journaling and an explicit positive research-run cap
  for fresh replay; strict provenance-compatible frozen reproduction.

## V1 release run

V1 was verified end to end at the `v1.0.0` tag on real Coinbase Exchange hourly
data: a fresh historical research replay with Claude, then an exact frozen
reproduction from the stored artifacts with no inference, and a fresh clone of the
tag reproducing the run's code identity. The single research run assessed one BTC-USD
candidate and returned NO_TRADE with confidence 58. That is one decision (n=1), not
a performance result. See the [release-run record](docs/releases/v1.0.0.md).

## Architecture

Crypteo is a Python modular monolith. Canonical Coinbase data feeds an authoritative
clock and point-in-time gateway shared by scanning, research tools, deterministic
trade terms, risk, and simulated execution. Baseline replay selects valid terms.
Research replay admits durable, validated `COMPLETED / MODEL` assessments; only
`TRADE` proceeds to deterministic terms, proposal, risk, and authorized submission.
Model `NO_TRADE` and `ABSTAIN` stop before financial preparation. Operational
research failures abort evaluation rather than become trading conclusions.

LLMs choose research tools and interpret evidence. Deterministic Python owns
stop/target calculation, quantity, risk authorization, execution prices, fees,
portfolio mutation, and P&L. Estimated stop risk does not cap realized gap loss.
See the [architecture overview](docs/architecture.md) for component flow.

Historical tools exclude future market data, and model latency does not advance
simulated time. Crypteo cannot prove that an LLM's parametric knowledge is
historically pure: fresh replay is auditable and point-in-time data-controlled,
but lacks a purely deterministic strategy's historical-purity guarantee. Frozen
artifacts improve reproducibility without resolving that limitation.

## V1 scope

- Coinbase Exchange spot: `BTC-USD`, `ETH-USD`, and `SOL-USD`; completed 1-hour
  OHLCV candles in UTC; LONG positions only.
- Market-derived research and one shared deterministic scanner and financial
  lifecycle across replay modes.
- One bounded opportunity-led research workflow is implemented. Trader-led
  interactive research/chat, live mode, UI, non-market research, specialist agents,
  and memory/RAG are deferred.
- No integrated evaluation/reporting harness, statistical model-performance
  evaluation, or automatic replay crash resume. A paired baseline/research
  comparison artifact, a mechanical single-candidate evaluation window planner,
  and a resumable evaluation runner with frozen reproduction are implemented.
- No real-money trading, real order path, exchange trading credentials, private
  keys, or withdrawal capabilities.

The specification keeps the full list of
[unimplemented boundaries](docs/v1-specification.md#unimplemented-research-and-evaluation-boundaries).

## Scanner-only replay API

`crypteo.replay.run_scanner_only(config, datasets, *, code=None)` accepts a
`ReplayConfig` from `crypteo.experiment` and one explicit canonical dataset per
configured asset. Supply acquisition results directly or use the
[read-only dataset loader](docs/data-pipeline.md#read-only-canonical-loading).

The returned `ReplayResult` contains the experiment manifest, audit records,
portfolio observations, final portfolio, and metrics; `result.canonical_bytes()`
produces byte-stable JSON. This is the in-memory deterministic baseline. See the
[replay specification](docs/v1-specification.md#scanner-only-replay)
for configuration, boundary behavior, and reproducibility.

With `code=None`, the replay and research entry points derive code identity from
the executing git source checkout. An installed package, such as a wheel outside
a checkout, has none and raises `ValueError`; pass an explicit
`code=crypteo.experiment.CodeIdentity(git_commit=..., source_digest=...)` that
identifies the installed build.

## Historical research replay API

`await crypteo.research_replay.run_research_replay(config, datasets, *, research, store, model=None, code=None)`
returns a `ReplayResult` with compact research links and factual research-policy
metrics alongside financial records and metrics.

Supply `FreshResearchSettings(configuration=..., trial_id=..., max_research_runs=...)`,
a caller-owned `ResearchStore`, and an observable model adapter.
The explicit positive cap reserves one run for every admitted candidate before
registration/inference; exceeding it aborts without a partial successful result.
Per-run workflow limits also apply. These software limits are not a billing guarantee.
Fresh repeats may produce different conclusions.

Supply `FrozenResearchSettings(configuration=..., mapping=...)` and
the durable store, with no model. Explicit mappings identify retained artifacts;
frozen replay performs no new inference, workflow execution, or research-tool
calls. Scanning, terms after `TRADE`, execution, and valuation still read market
data. Identical frozen inputs can reproduce byte-stable results, subject to strict
originating code/configuration compatibility. Changed capital, risk, execution,
interval, or other manifest-bound inputs cannot freely reuse the assessments.

For imported research results, use `load_research_replay(encoded, *, store)` or
`validate_research_links(result, *, store)` to resolve and validate external journal
links. Parsing a compact replay result alone does not prove the artifacts exist.

`crypteo.evaluation.compare_replays(pairs, *, store)` pairs `(baseline, research)`
results for windows that each admit one candidate and returns a validated
`ResearchComparison` with descriptive metrics; `load_comparison(encoded, *, store)`
imports one from canonical JSON. Both resolve research links through the store.
`crypteo.evaluation.plan_windows(datasets, settings)` derives those windows from
scanner candidates without reference to outcomes, recording a reason for every
excluded candidate; `validate_plan(plan, datasets)` re-plans and requires
equality. See the
[window planning specification](docs/v1-specification.md#evaluation-window-planning).
`await crypteo.evaluation.run_evaluation(plan, datasets, *, directory, store, model, configuration, trial, max_research_runs, code=None)`
runs each planned window in scanner-only and fresh research mode, saving files as
they complete, and returns an `EvaluationResult`; calling it again resumes without
repeating completed research, and attempts never exceed the cap across calls.
`await reproduce_evaluation(result, datasets, *, store, code=None)` rebuilds the
result from frozen research with no model or API key, and
`load_evaluation(encoded, *, store)` imports one. Results bind code identity, so
real runs should start from a clean, committed tree. See the
[evaluation runner specification](docs/v1-specification.md#evaluation-runner).
See the [research replay specification](docs/v1-specification.md#historical-research-replay)
and [ADR-011](docs/decisions/ADR-011-model-research-historical-replay.md).

## Model-backed research API

`crypteo.research_workflow.run_opportunity_research(request, service, model, configuration)`
is async and requires an explicit research-run ID, a current `Opportunity`, and a
`MarketResearchService` whose authoritative clock matches the opportunity's
observation time. It returns a validated in-memory `ResearchRunResult`.

`run_durable_opportunity_research(request, service, model, configuration, *, store=...)`
uses the same bounded workflow with lifecycle events committed to SQLite. Supply
`crypteo.research_persistence.ResearchStore(path)` with a caller-owned database path
in an existing writable directory and an adapter supporting provider observation,
such as `AnthropicResearchModel`. Completed or operationally interrupted runs publish
a validated immutable `ResearchArtifact`. `store.inspect_run(research_run_id)`
inspects committed history, including incomplete/aborted runs;
`store.load_terminal(research_run_id)` loads validated terminal artifacts. Neither
re-runs models, tools, or market-data queries. Storage failures stop work and raise
without returning an artifact or becoming research conclusions. Automatic crash
resume is unimplemented.

After installing the package, supply `ANTHROPIC_API_KEY` securely in the local
process environment for `anthropic.AsyncAnthropic()` and wrap that caller-owned
client in `crypteo.anthropic_research.AnthropicResearchModel`. Configure
`ResearchWorkflowConfig` with an explicit model and effort:

```python
from anthropic import AsyncAnthropic

from crypteo.anthropic_research import AnthropicResearchModel
from crypteo.domain.research_workflow import ResearchWorkflowConfig
from crypteo.research_model import ModelConfiguration, ModelParameters

config = ResearchWorkflowConfig(
    model=ModelConfiguration(
        provider="anthropic",
        model="claude-opus-5-5",
        parameters=ModelParameters(effort="high"),
    )
)


async def research() -> None:
    async with AsyncAnthropic() as client:  # reads ANTHROPIC_API_KEY
        model = AnthropicResearchModel(client)
        ...  # run_durable_opportunity_research(request, service, model, config, ...)
```

No model is selected by default and no `.env` file is automatically loaded. Never
put a key in a file: keep keys out of Git, configuration, and documentation. This
is a Python API; no research CLI or chat UI exists.

See the [architecture](docs/architecture.md#model-backed-research),
[behavioral specification](docs/v1-specification.md#bounded-model-backed-research),
and [ADR-009](docs/decisions/ADR-009-bounded-model-directed-research.md).
Durable semantics are defined in the
[lifecycle specification](docs/v1-specification.md#durable-research-lifecycle)
and [ADR-010](docs/decisions/ADR-010-durable-research-lifecycle.md).

## Documentation

Documentation describes the system that exists at HEAD.
ADRs preserve rationale for durable architectural decisions.
Git preserves implementation history.

- [Architecture](docs/architecture.md): current components, boundaries, and flow.
- [V1 specification](docs/v1-specification.md): authoritative behavioral contracts,
  formulas, temporal rules, risk, execution, and replay semantics.
- [Data pipeline](docs/data-pipeline.md): Coinbase acquisition, identity,
  provenance, persistence, and failure behavior.
- [ADRs](docs/decisions/README.md): durable decisions, rationale, and trade-offs.
- [V1 release run](docs/releases/v1.0.0.md): the `v1.0.0` end-to-end run on real
  data, its checks, identities, and limitations.

## Local development

Requires Python 3.12 or newer. From the repository root on macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run the same verification commands as CI (mypy uses strict mode from
`pyproject.toml`):

```bash
python -m ruff format --check .
python -m ruff check .
python -m mypy
python -m pytest
```

To apply formatting during development, run `python -m ruff format .`.

## Design principles

- Deterministic calculations and reproducible inputs.
- Point-in-time correctness enforced by the clock and data gateway.
- Explicit provenance and separate canonical-data and acquisition identities.
- Validated, typed contracts at domain boundaries.
- Conservative missing-data handling; never silently forward-fill OHLCV.
- Immutable records and append-only financial histories for auditability.
