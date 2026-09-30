# Crypteo

Crypteo is an agentic crypto research copilot in development for traders and
researchers with market knowledge. It is designed to gather evidence, challenge
trade theses, and surface uncertainty to support informed trading decisions.
Trading decisions remain with the user. The long-term product is a live research
assistant; historical replay and paper trading provide its evaluation lab.

## Current status

The repository implements the deterministic foundations:

- Validated market and research contracts, including normalized evidence, theses,
  critiques, and research assessments.
- Authoritative simulation time and point-in-time access to completed candles.
- Coinbase historical candle retrieval, normalization, gap reporting, and immutable
  local raw, canonical, and acquisition artifacts.
- Deterministic Bollinger analytics and a lower-band crossover opportunity scanner.
- Seven deterministic market-research tools producing point-in-time evidence:
  trend, momentum, volatility, Bollinger position, recent levels, volume context,
  and relative strength. These provide infrastructure for the future AI workflow;
  see the [research tool specification](docs/v1-specification.md#deterministic-market-research).
- Simulated LONG execution and portfolio accounting with fees, slippage,
  stop/target and time exits, and conservative missing-data handling.
- Deterministic market-derived stop/target terms and decision-time LONG risk
  authorization, positive estimated net target reward checks, and position sizing.
- Deterministic scanner-only historical replay with reproducible in-memory
  results, marked equity, and core metrics for later research comparisons.

Still unfinished: the bounded LangGraph/LLM workflow (model-backed investigation,
thesis generation, critic, and final AI research assessment), agentic replay,
durable lifecycle journaling, and the integrated evaluation and reporting harness.
No agents, live mode, or UI are implemented yet.

## Architecture

Crypteo is a Python modular monolith. Canonical Coinbase data feeds an authoritative
clock and point-in-time gateway. Scanner-only replay connects the scanner,
deterministic trade terms, risk authorization, and simulated broker/portfolio,
then produces validated replay artifacts and metrics. Standalone research tools
turn visible market data into normalized evidence for future model-backed work.

LLMs are reserved for interpretation and judgment. Deterministic code owns
calculation, authorization, simulation, and accounting. Research selection and
risk approval remain separate; estimated stop risk does not cap realized gap loss.
See the [architecture overview](docs/architecture.md) for component flow.

## V1 scope

- Coinbase Exchange spot: `BTC-USD`, `ETH-USD`, and `SOL-USD`; completed 1-hour
  OHLCV candles in UTC; LONG positions only.
- Market-derived research and one shared deterministic scanner for agent/baseline
  comparisons. V1 tests trade selection under identical risk and execution rules.
- One bounded research workflow is planned. Live mode, non-market research,
  specialist agents, and a dashboard are deferred.
- No real-money trading, real order path, exchange trading credentials, private
  keys, or withdrawal capabilities.

## Scanner-only replay API

`crypteo.replay.run_scanner_only(config, datasets, *, code=None)` accepts a
`ReplayConfig` from `crypteo.experiment` and one explicit canonical dataset per
configured asset. Supply acquisition results directly or use the
[read-only dataset loader](docs/data-pipeline.md#read-only-canonical-loading).

The returned `ReplayResult` contains the experiment manifest, audit records,
portfolio observations, final portfolio, and metrics; `result.canonical_bytes()`
produces byte-stable JSON. This is an in-memory baseline for later agentic
comparisons. See the [replay specification](docs/v1-specification.md#scanner-only-replay)
for configuration, boundary behavior, and reproducibility.

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
