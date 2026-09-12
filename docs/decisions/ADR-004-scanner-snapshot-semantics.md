# ADR-004: Scanner snapshot semantics

## Status

Accepted; implemented in Slice 7 (`758d2de`).

## Context

The locked scanner needs reproducible candidates before runtime/replay delivery
exists. Its visible history can change when delayed internal candles arrive.
Sources: [scanner](../../src/crypteo/scanner.py),
[tests](../../tests/test_scanner.py), and the
[scanner lock](../v1-specification.md#scanner-lock). The rationale below reconstructs
the implications of the accepted contract; historical alternative-review notes
are not recorded.

## Decision

`OpportunityScanner` reads only gateway-visible Coinbase hourly history.
`scan(pair=...)` returns all qualifying opportunities derivable from the current
visible snapshot, in chronological order. It retains no event-delivery state.

Within each scan:

- The first valid band initializes comparison without emitting, even if below-band.
- A crossover requires the previous close at/above its own lower band and the
  current close below its own lower band. With contiguous history, the earliest
  opportunity is at candle 21.
- `None`/gap resets comparison; the next valid band initializes again.
- Repeated below-band observations do not emit again; equality or above-band
  observation re-arms comparison.
- Opportunity observation time is the trigger close; availability includes the
  dependencies of both compared windows.

Opportunity IDs deterministically hash trigger version/type, venue, pair,
timeframe, and observed time. They are stable across repeated scans and fresh
scanner instances. Payload and availability are excluded from identity.

Delivery cursors and deduplication across runs belong to later runtime/replay
layers. Within-snapshot crossover suppression does not promise once-only delivery
across calls.

## Rationale

Snapshot semantics makes the result depend on visible inputs rather than the
sequence of previous calls. It permits direct comparison of fresh and reused
scanners and handles newly visible internal history without defining incremental
delivery, cursor persistence, or restart behavior prematurely. Stable IDs provide
references for those later consumers.

## Consequences

Repeated scans return existing opportunities again. Later runtime/replay consumers
must define delivery and deduplication explicitly and retain dataset/run context;
an opportunity ID is not a content hash of all evidence. Newly visible candles
may change historical snapshot results, so this API is not an append-only event
stream. Full visible history is recalculated on each scan; incremental optimization
must preserve the snapshot contract if introduced later.
