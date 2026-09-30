# Coinbase historical data pipeline

This document describes the implemented Coinbase acquisition and local persistence
path. Coinbase supplies the supported USD spot pairs and hourly aggregation
without multi-venue reconciliation. The
[V1 specification](v1-specification.md#canonical-historical-data) defines the
historical-data scope; [ADR-003](decisions/ADR-003-canonical-data-and-acquisition-provenance.md)
records why raw captures, canonical datasets, and acquisition records have separate
identities. Scanner-only replay consumes explicitly supplied canonical datasets;
see the [replay specification](v1-specification.md#scanner-only-replay).

## Read-only canonical loading

`CoinbaseLocalPersistence(root).load_dataset(dataset_version, *, acquisition_id=None)`
reads and validates existing candles and manifest, recomputes canonical identity,
and verifies that the stored version matches the requested path. It returns a
`CoinbaseCanonicalDataset` without raw response bodies. An explicitly supplied
acquisition ID loads and validates that record and its dataset linkage; omitting
it returns `acquisition=None` without silently choosing among acquisitions that
share one canonical version.

Before reading, the loader resolves the canonical directory and both leaf paths
(`manifest.json` and `candles.json`) and requires containment beneath the resolved
artifact root. A leaf symlink escaping that root is rejected; a symlink remaining
inside it is allowed. An explicitly requested acquisition path is also checked
for containment. Persistence applies the same resolved-root containment rule to
raw, canonical, and acquisition write destinations, including directories and
leaves; contained symlinks remain allowed.

This narrow reader does not fetch data or repeat raw recomposition, which belongs
to acquisition publication validation. Missing or corrupt artifacts raise without
rewriting, trimming, or repairing anything. See the
[loader](../src/crypteo/coinbase_persistence.py) and its
[contract tests](../tests/test_coinbase_persistence.py).

## Acquisition boundary

Coinbase acquisition requires a caller-supplied, strict UTC
`acquisition_started_at`. The retrieval API is:

```python
retrieve(*, pair, start, end, acquisition_started_at) -> Iterator[CoinbaseRawCandleResponse]
```

Validation runs when iteration starts, before any HTTP call. Every requested end
must be at or before that boundary, and all chunks from one retrieval carry the
same boundary. `ingest_coinbase_candles` also requires this argument.

Composition independently validates each capture's UTC boundary, checks request
ends and retained candle closes against it, and requires `created_at` at or after
all supplied capture boundaries. Equality is allowed. Canonical `available_at`
remains candle close time under the current normalization policy; acquisition time
is separate provenance, not a replacement historical availability timestamp.
Historical consumers must still use the authoritative clock and gateway described
in [ADR-002](decisions/ADR-002-point-in-time-temporal-boundary.md).

## Retrieval and chunk ownership

The [retriever](../src/crypteo/coinbase.py) uses the public Coinbase Exchange candle
endpoint through an injectable byte-oriented HTTP transport. It supports the V1
pairs (`BTC-USD`, `ETH-USD`, `SOL-USD`) at granularity 3600 seconds. Requested bounds
must be strict UTC, hourly aligned, and ordered. The builder divides `[start, end)`
into deterministic, nonoverlapping requests of at most 300 hourly slots; a
zero-length interval produces no requests.

Each response owns only candle opens in `[request.start, request.end)`. Provider
extras are preserved in the raw body but cannot supply another request's missing
slots. Excluded rows still require a six-value row structure and a valid integer
Unix timestamp; only retained rows undergo OHLCV validation.

Ingestion persists each yielded capture before advancing the iterator. A later
transport failure preserves earlier captures and prevents canonical and acquisition
publication for that ingestion.

## Raw captures and provenance

All paths below are relative to the caller-supplied artifact root:

```text
raw/coinbase/<pair>/1h/sha256/<capture-digest>/
  body.json
  request.json
canonical/sha256/<dataset-digest>/
  candles.json
  manifest.json
acquisitions/coinbase/sha256/<acquisition-digest>/
  acquisition.json
```

`body.json` contains unchanged response bytes, including provider extras and raw
formatting. Despite its filename, `request.json` contains the complete capture
envelope:

- `capture_format_version`: `"1"`.
- Exact request identity: pair, timeframe, granularity, UTC start/end, URL, and
  request parameters.
- `acquisition_started_at`.
- `body_sha256`: SHA-256 of the exact body bytes.

The capture ID is `sha256:` followed by the SHA-256 digest of that envelope encoded
as UTF-8 JSON with sorted keys and compact separators. Envelope provenance
timestamps use UTC `Z` and six fractional digits; the nested request parameters
retain the retriever's request spelling (hourly start/end use `Z` without fractional
digits). See the [capture encoding](../src/crypteo/coinbase_capture.py).

Identical captures are idempotent; revised bodies or acquisition boundaries yield
different capture identities and coexist. Capture envelopes are required; there
is no automatic format migration.
Dataset schema version is `"1"`.

## Normalization and composition

[Composition](../src/crypteo/coinbase_dataset.py) requires explicit
`requested_start` and `requested_end`. Supplied captures must match the dataset
pair, and their request ranges must form one contiguous union equal to that
interval. Composition accepts overlapping or repeated ranges even though the
retriever generates nonoverlapping chunks. An acquisition with no captures is
valid only for a zero-length requested interval.

Response bodies must decode to JSON arrays; fractional JSON numbers are decoded
as exact Decimals and nonstandard non-finite constants are rejected. Retained rows
are [normalized](../src/crypteo/coinbase_normalization.py) from Coinbase's
`[time, low, high, open, close, volume]` order into validated hourly candles.
Equivalent duplicate candles collapse; conflicting values for one candle identity
fail. Output is sorted chronologically, with no synthesized or forward-filled
candles. Raw responses remain attached to `CoinbaseHistoricalDataset` unchanged.

Canonical manifests describe actual coverage: first open through final close,
with internal missing hourly opens only. Empty canonical datasets have `None`
start/end coverage, zero rows, and no canonical gaps, independent of requested
bounds or creation time. Requested completeness belongs to the acquisition record.

## Canonical dataset identity

The dataset version is `sha256:` plus the SHA-256 of a deterministic UTF-8 JSON
envelope with sorted keys and compact separators. It includes:

- Schema version `"1"`, source `coinbase_exchange_candles`, venue, pair, and timeframe.
- Actual start/end coverage, row count, and internal missing opens.
- Canonical candle rows, including market identity, open/close/availability times,
  and OHLCV values.

Canonical candle rows are chronological. Timestamps use UTC `Z` with six fractional
digits. Decimal values use exact fixed-point strings with insignificant trailing
zeros removed, and zero is encoded as `"0"`, without Decimal-context rounding.
Persisted `candles.json` artifacts use this same candle representation.

Creation time and acquisition provenance are excluded from canonical identity.
Equivalent numeric spellings, raw formatting, response order, and request chunking
therefore reuse a dataset version when they yield the same canonical content.
Requested bounds do not distinguish otherwise identical empty datasets.

## Acquisition records and missing slots

`CoinbaseHistoricalDataset.acquisition` contains an immutable
[`CoinbaseAcquisitionRecord`](../src/crypteo/coinbase_acquisition.py) with:

- Schema version `"1"`, canonical dataset version, and creation time.
- Explicit requested start/end.
- Sorted unique capture IDs.
- Sorted unique requested missing opens (`missing_open_times`).

All timestamps require strict UTC; requested boundaries and missing slots are
hourly aligned. Missing opens lie within `[requested_start, requested_end)` and
include leading, internal, and trailing slots. An all-empty acquisition reports
every requested slot; a zero-length acquisition reports none and has no captures.

The acquisition ID is `sha256:` plus SHA-256 of the complete record's UTF-8 JSON
with sorted keys, compact separators, and UTC timestamps using `Z` and six
fractional digits. It includes `created_at`. The record links to exact capture IDs
and the canonical dataset version without mutable backlinks. Multiple acquisitions
can share canonical data while preserving different requests, capture histories,
and creation times.

## Persistence and publication

[`ingest_coinbase_candles`](../src/crypteo/coinbase_persistence.py) performs these
steps in order:

1. Retrieve and persist each raw capture before requesting the next chunk.
2. Compose all captures into canonical candles, a manifest, and an acquisition record.
3. Persist canonical candles and manifest, or validate and reuse existing artifacts.
4. Replace the returned dataset's manifest with the effective stored manifest.
5. Validate the full acquisition association and publish `acquisition.json`.

### Canonical reuse

`persist_dataset(dataset) -> DatasetManifest` validates incoming candle and
manifest models, market/source identity, coverage, uniqueness, and recomputed
dataset version. It returns the effective stored manifest.

Existing canonical artifacts are loaded and schema-validated, their coverage and
market identity checked against their candles, and their dataset version
recomputed before semantic comparison with incoming content. Equivalent Decimal
or timestamp spellings, row order, JSON formatting, and later creation times reuse
existing artifacts without rewriting them.

Ingestion returns the first persisted canonical manifest while preserving the
current acquisition's own creation time and identity. The canonical manifest's
`created_at` can therefore precede the current acquisition's creation time.

### Acquisition publication checks

`persist_acquisition(dataset)` requires both canonical files and both files for
every referenced raw capture to exist before publication. Existence alone is not
sufficient. It also:

- Revalidates the acquisition record and checks its dataset version and exact
  sorted unique raw-response capture IDs against the supplied dataset.
- Validates incoming and stored canonical artifacts and compares canonical content.
- Compares each stored raw body and capture envelope byte-for-byte with the
  supplied response and its encoded envelope.
- Recomposes those captures using the acquisition's requested bounds and creation
  time, requiring matching canonical content and exact acquisition completeness
  and linkage before writing the record.

Recomposition uses the acquisition's creation time, preserving the distinction
from the first canonical manifest's time. These checks reject unrelated captures
and false provenance even when referenced files individually exist and are valid.

## Failure and concurrency limitations

Raw and acquisition writes use exclusive file creation and accept existing files
only when bytes match; conflicting bytes are not overwritten. Canonical reuse
instead uses the validated semantic comparison described above.

Normalization failure leaves already-persisted raw captures available for audit.
Corrupt, mismatched, or incomplete canonical artifacts fail closed and prevent
acquisition publication. Tampered raw bodies or envelopes and inconsistent
acquisition associations also prevent publication. Corrupt or mismatched existing
artifacts and incomplete canonical directories are not repaired automatically.
An idempotent raw-persistence retry can complete an otherwise valid partial raw
capture directory when `body.json` already contains the exact expected bytes and
`request.json` is missing.

Writes are not a crash-atomic transaction. A failure can leave partial raw or
canonical artifacts, or complete artifacts without a completed acquisition record.
Concurrent writers are not coordinated; exclusive file creation does not provide
a multi-file transaction or a concurrent-ingestion guarantee.

Regression coverage lives in the [retrieval](../tests/test_coinbase.py),
[normalization](../tests/test_coinbase_normalization.py),
[dataset](../tests/test_coinbase_dataset.py),
[acquisition](../tests/test_coinbase_acquisition.py), and
[persistence tests](../tests/test_coinbase_persistence.py).
