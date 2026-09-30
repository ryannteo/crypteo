# ADR-002: Point-in-time temporal boundary

## Status

Accepted.

## Context

Stored datasets can contain candles that were incomplete or unavailable at a
historical decision time. Schema-valid timestamps alone do not establish visibility.

Sources: [temporal specification](../v1-specification.md#temporal-and-replay-invariants),
[implementation](../../src/crypteo/temporal.py), and
[temporal tests](../../tests/test_temporal.py).

## Decision

`SimulationClock` is authoritative historical time. Runtime advances it explicitly
with strict, timezone-aware UTC values; it cannot move backward. Historical
visibility uses no wall clock.

`PointInTimeDataGateway` is the sole visibility boundary for historical candle
reads by replay/scanner/research consumers. A candle is visible only when both
`close_time <= clock.now` and `available_at <= clock.now`. Reads accept market
identity, never a caller-controlled cutoff. Future and temporally unavailable
records stay hidden, including completed candles with delayed availability.

## Rationale

Centralizing visibility makes the historical information boundary enforceable and
testable independently of consumers or prompts. Exact completion and availability
checks prevent lookahead while allowing delayed records only when legitimately
available. Explicit time makes runs independent of machine time.

## Consequences

Scanner, replay services, and research tools read through the gateway; future
consumers must do the same. Pure analytics accepts supplied candles and does not
independently authorize visibility. Acquisition/persistence may inspect a full dataset to build
artifacts; that access does not authorize exposing it to historical consumers.

Tests must plant future and delayed data and check exact visibility boundaries.
As time advances, newly visible internal candles can close gaps and change derived
snapshot results; consumers must preserve dependency availability. Additional
research sources need credible point-in-time guarantees before replay use.
