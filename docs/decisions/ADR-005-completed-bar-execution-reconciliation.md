# ADR-005: Completed-bar simulated execution reconciliation

## Status

Accepted; implemented in Slice 8
(`22087f12c533d90f14ac48640fc7477b0dd274b3`).

## Context

Next-open simulated execution must respect the authoritative gateway, which exposes
candles only after both completion and availability. Hourly OHLCV establishes an
opening price and a range, but cannot establish exact intrabar touch times or paths
through missing intervals.

Sources: [temporal boundary](ADR-002-point-in-time-temporal-boundary.md),
[broker](../../src/crypteo/execution.py),
[fill and lifecycle contracts](../../src/crypteo/domain/execution.py), and
[execution tests](../../tests/test_simulated_broker.py).

## Decision

An authorized decision after close T, submitted at the current hourly boundary T,
is eligible for the open of [T, T+1h). The opening fill is recorded only when that
candle becomes gateway-visible and the broker processes it. This reconciles a
market event at T using completed evidence; it does not expose the opening price
or mutate financial state at T.

Fill records distinguish market phase (`OPEN`, `INTRABAR`, `CLOSE`) and candle
interval from evidence `available_at` and processing `recorded_at`. Exact market
time exists only for opening and closing boundaries. Intrabar fills identify the
interval with no invented exact timestamp. The broker reads authoritative gateway
time and never advances it or owns a second clock.

Due candles are reconciled chronologically. A candle absent or unavailable at its
expected close counts as missing, even if processing first occurs after delayed
data becomes visible. Missing entry data causes terminal rejection: no rollover to
a later opening and no retroactive revival of that order. Missing holding data
records an unresolved gap and halts financial processing, preserving the position
and ledger without inferring an exit or resuming on later data.

Once entered, an observed open at or beyond the stop or target triggers an exit
using that opening price. This temporally known opening event precedes any later
ambiguous intrabar touch, including a target opening followed by a stop touch.
Otherwise, when both stop and target are touched intrabar, resolve stop-first for
a LONG position. Stop/target exits take priority over a time exit on the final
holding bar; the time exit uses its close only if neither trigger applies.

## Rationale

Completed-bar reconciliation preserves point-in-time integrity without granting
the broker privileged access to incomplete candles. Separate market, availability,
and recording times make fills auditable and distinguish late processing from late
evidence. The expected-close missing-data rule keeps financial outcomes independent
of processing cadence and prevents later information from rewriting an order's
eligibility. Recording times still reflect the actual processing calls.

Opening priority uses the sequence actually established by OHLCV. Stop-first is a
deterministic conservative policy for the remaining ambiguity, not a claim about
the observed intrabar path. Halting on holding gaps avoids fabricated missing-data
information. Together these rules support deterministic replay and auditable fills.

## Consequences

Consumers must distinguish a fill's market time from when its evidence and ledger
record became available. Replay runtime remains responsible for advancing the
single clock. Rejected orders and unresolved holding gaps cannot be silently
repaired by newly visible data. These semantics constrain execution reconciliation;
fee values, increments, and the Slice 8 one-trade scope limit are not permanent
architectural constraints established by this ADR.
