# Crypteo

Crypteo is an agentic crypto research copilot that gathers and evaluates evidence
to support informed trading decisions. Trading decisions remain with the user.

Through Slice 7, this repository contains validated market and research schemas,
authoritative simulation time and point-in-time candle access, Coinbase historical
ingestion and persistence, deterministic Bollinger analytics, and an opportunity
scanner. Agent orchestration, risk, simulated execution, and replay remain later work.

## Documentation

- [V1 specification](docs/v1-specification.md)
- [Engineering build log](docs/build-log.md)
- [Architectural decisions](docs/decisions/README.md)

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
migrated. Dataset schema version remains "1".

Composition now requires explicit `requested_start` and `requested_end`.
Capture request ranges must account for that interval; no captures represent only
a zero-length acquisition. Empty canonical datasets have `None` start/end coverage
and no canonical gaps, independent of requested bounds or creation time.
Non-empty manifests continue to describe first open through final close, with
internal gaps only.

`CoinbaseHistoricalDataset.acquisition` contains an immutable
`CoinbaseAcquisitionRecord`: schema version "1", dataset version, creation time,
requested start/end, sorted unique capture IDs, and sorted unique requested
missing opens. Requested missing opens include leading, internal, and trailing
slots; an all-empty acquisition reports every requested slot, and a zero-length
acquisition reports none. All timestamps require strict UTC and requested slots
are hourly aligned.

Ingestion publishes
`acquisitions/coinbase/sha256/<acquisition-digest>/acquisition.json` only after
canonical persistence succeeds. The digest is SHA-256 of the complete record's
UTF-8 JSON with sorted keys, compact separators, and UTC timestamps using `Z`
and six fractional digits. It includes `created_at`. The record links to exact
capture IDs and the canonical dataset version without mutable backlinks.
Raw and canonical paths remain as documented above and
`canonical/sha256/<dataset-digest>/{candles.json,manifest.json}`, respectively.
`persist_acquisition(dataset)` verifies that the record matches the dataset's
manifest and exact raw-response capture IDs, and that both canonical files and
both files for every referenced raw capture already exist before publishing.
`persist_dataset(dataset) -> DatasetManifest` returns the effective stored
manifest. New candle artifacts use the same representation as dataset hashing:
chronological rows, UTC timestamps with six fractional digits, and exact Decimal
strings with insignificant trailing zeros removed without Decimal-context rounding.
Creation time and acquisition provenance remain outside canonical dataset identity.

Existing canonical artifacts are loaded and schema-validated, their coverage and
market identity are checked against their candles, and their dataset version is
recomputed before semantic comparison with incoming content. Equivalent spellings,
formatting, and later creation times reuse existing artifacts without rewriting
them. Ingestion returns the first persisted canonical manifest while preserving
the current acquisition's own creation time and identity. Corrupt, mismatched, or
incomplete canonical artifacts fail closed and prevent acquisition publication.
No artifacts are migrated or repaired automatically. Writes are not a crash-atomic
transaction, so a failure may leave raw or canonical artifacts without a completed
acquisition record; concurrent writers are not coordinated.
