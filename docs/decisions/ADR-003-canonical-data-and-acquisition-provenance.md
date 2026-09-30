# ADR-003: Canonical data and acquisition provenance

## Status

Accepted.

## Context

Equivalent market data may arrive with different request chunking, numeric
spellings, raw formatting, or acquisition times. Missing requested slots may also
lie outside the actual canonical coverage. One identity cannot express both
semantic dataset equivalence and exact acquisition history.

Sources: [capture](../../src/crypteo/coinbase_capture.py),
[dataset composition](../../src/crypteo/coinbase_dataset.py),
[acquisition record](../../src/crypteo/coinbase_acquisition.py), and
[publication](../../src/crypteo/coinbase_persistence.py).

## Decision

Maintain three distinct immutable identities:

| Record | Meaning and identity |
| --- | --- |
| Raw capture | Exact Coinbase observation: unchanged response body plus request identity and acquisition boundary. Capture identity hashes the versioned envelope, including the body digest. |
| Canonical dataset | Validated semantic candles, market/source/schema identity, coverage, and internal gaps. A deterministic hash excludes raw formatting, requests, and creation/acquisition provenance. |
| Acquisition record | Requested interval, exact capture linkage, requested completeness, resulting dataset version, and acquisition record creation time. Its own hash identifies that association. |

Each request owns only opens in `[start, end)`; provider extras remain raw. A
canonical manifest describes actual first-open through final-close coverage and
internal gaps. An acquisition records all requested missing slots, including
leading/trailing gaps. Empty canonical data has `None` bounds and no gaps even
when an acquisition requested a nonempty interval.

Equivalent numeric/raw formatting can reuse canonical data. Stored artifacts are
validated semantically without rewriting them; the first persisted canonical
manifest is retained, while each acquisition keeps its distinct provenance.

Acquisition publication validates the full raw-to-canonical association: revalidate
the record and canonical artifacts, compare exact stored raw bodies/envelopes,
recompose the captures, and require matching canonical content and acquisition
completeness/linkage before writing the acquisition record.

## Rationale

Semantic identity permits reproducible comparisons and reuse without treating
formatting as new market data. Exact capture identity preserves what was observed.
Acquisition identity preserves what was requested and missing without contaminating
dataset equivalence. Recomposition prevents unrelated or corrupted artifacts from
being published as valid provenance merely because references exist.

## Consequences

Multiple acquisitions and captures may resolve to one canonical dataset. Consumers
must retain the acquisition reference when exact provenance/completeness matters;
a dataset version alone cannot answer those questions. Canonical candle
availability remains close time under the current normalization policy; acquisition
time is a separate provenance boundary.

Conflicts and corruption fail closed, without automatic migration or repair.
Publication follows raw and canonical persistence; multi-file writes are not
crash-atomic, so failures may leave artifacts without an acquisition record.
Concurrent writers are not coordinated. Regression evidence is in the
[dataset](../../tests/test_coinbase_dataset.py),
[acquisition](../../tests/test_coinbase_acquisition.py), and
[persistence tests](../../tests/test_coinbase_persistence.py).
