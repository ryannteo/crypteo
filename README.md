# Crypteo

Crypteo is an agentic crypto research copilot that gathers and evaluates evidence
to support informed trading decisions. Trading decisions remain with the user.

This repository currently contains the Python and quality-tooling foundation,
validated market-domain schemas, and validated research, evidence, and decision
schemas. It does not yet contain live data ingestion, market analytics, agents,
trading behavior, or simulation and replay behavior.

## Local development

Crypteo requires Python 3.12 or newer. On macOS or Linux, create an isolated
environment and install the package with its development tools:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run the quality checks from the repository root:

```bash
python -m ruff format .
python -m ruff check .
python -m mypy
python -m pytest
```

To check formatting without modifying files, as CI does, run:

```bash
python -m ruff format --check .
```

Coinbase acquisition requires a caller-supplied, strict UTC
`acquisition_started_at`. The retrieval API is
`retrieve(*, pair, start, end, acquisition_started_at) -> Iterator[CoinbaseRawCandleResponse]`.
Validation runs when iteration starts, before any HTTP call; every requested end
must be at or before that boundary. All chunks carry the same boundary.
`ingest_coinbase_candles` also requires this argument and persists each yielded
capture before advancing the iterator. A later transport failure preserves earlier
captures and prevents canonical publication.

Composition independently checks request ends and retained candle closes against
capture boundaries, and requires `created_at` at or after all supplied capture
boundaries. Canonical availability remains candle close time. Each response owns
only opens in `[request.start, request.end)`: provider extras are preserved raw but
cannot supply another request's missing slots. Excluded rows require valid structure
and timestamps; only retained rows undergo OHLCV validation.

Raw artifacts live at
`raw/coinbase/<pair>/1h/sha256/<capture-digest>/{body.json,request.json}`.
The body is unchanged bytes. Despite its existing filename, `request.json` now
contains the capture envelope: capture-format version `1`, exact request identity
(pair, timeframe, granularity, UTC start/end, URL and parameters),
`acquisition_started_at`, and the exact body's SHA-256 digest.
The capture ID is `sha256:` followed by the SHA-256 of that envelope encoded as
UTF-8 JSON with sorted keys and compact separators. Provenance timestamps use UTC
`Z` and six fractional digits. Identical captures are idempotent; revised bodies
or acquisition boundaries coexist. Existing request-only raw directories are not
migrated. Dataset schema version, empty-coverage semantics, and canonical
persistence remain unchanged.
