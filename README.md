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
- Simulated LONG execution and portfolio accounting with fees, slippage,
  stop/target and time exits, and conservative missing-data handling.
- Deterministic market-derived stop/target terms and decision-time LONG risk
  authorization, positive estimated net target reward checks, and position sizing.

Still unfinished: replay orchestration,
research tools and the bounded LangGraph/LLM workflow, durable lifecycle journaling,
and the integrated evaluation and reporting harness. No agents are implemented yet.

## V1 architecture

A Python modular monolith under `src/crypteo/`, with this intended evaluation flow
(the complete pipeline is not yet wired together):

```text
Historical market data -> point-in-time gateway -> analytics / scanner
  -> research tools / evidence -> thesis -> critic -> research assessment
  -> deterministic risk authorization -> simulated execution / portfolio
  -> journal / metrics / evaluation
Replay advances the authoritative clock and coordinates the flow.
```

**LLMs handle interpretation and judgment; deterministic software handles
calculation, authorization, execution, and accounting.** A research `TRADE` is
not risk approval. Risk sizing uses decision-time information and estimates
nongapped stop loss; the simulator separately handles actual next-open
affordability and execution outcomes. Authorization does not cap realized loss.

## V1 scope

- Coinbase Exchange spot: `BTC-USD`, `ETH-USD`, and `SOL-USD`; completed 1-hour
  OHLCV candles in UTC; LONG positions only.
- Market-derived research and one shared deterministic scanner for agent/baseline
  comparisons. V1 tests trade selection under identical risk and execution rules.
- One bounded research workflow is planned. Live mode, non-market research,
  specialist agents, and a dashboard are deferred.
- No real-money trading, real order path, exchange trading credentials, private
  keys, or withdrawal capabilities.

## Documentation

- [V1 specification](docs/v1-specification.md): intended architecture, scope,
  invariants, evaluation protocol, and completion criteria.
- [Engineering build log](docs/build-log.md): development history, consequential
  decisions, review findings, and recorded verification.
- [Architecture decision records](docs/decisions/README.md): durable constraints
  and their rationale.
- [Data pipeline](docs/data-pipeline.md): implemented Coinbase acquisition,
  provenance, dataset identity, persistence, and failure semantics.

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
