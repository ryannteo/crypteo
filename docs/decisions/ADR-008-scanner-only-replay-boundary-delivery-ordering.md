# ADR-008: Scanner-only replay boundary, delivery, and simultaneous-candidate ordering

## Status

Accepted.

## Context

The scanner returns opportunities from a visible historical snapshot and can
return the same identity on successive calls. Replay must define when those
candidates can enter the baseline and how simultaneous candidates compete while
the portfolio supports only one active trade. The scanner-only baseline must be
reproducible and suitable for later comparison with agentic trade selection.

Sources: [replay orchestration](../../src/crypteo/replay.py),
[delivery contracts](../../src/crypteo/domain/replay.py),
[lifecycle validation](../../src/crypteo/replay_validation.py), and
[replay tests](../../tests/test_replay.py). This records the implemented decisions
and their rationale; the [specification](../v1-specification.md#scanner-only-replay)
defines the API, artifacts, and metrics.

## Decision

- One authoritative `SimulationClock` and point-in-time gateway serve the scanner,
  terms service, risk service, and simulated broker. Replay alone advances time.
- At each hourly boundary in `[start, end)`, reconcile the broker before scanning
  or authorizing new proposals. At `end`, reconcile and observe once without new
  scans or submissions. `start == end` is valid and has no decision boundaries.
- Admit only opportunities with `observed_at == gateway.now` and
  `available_at <= gateway.now`. Every first delivery disposition is terminal
  within the run, including skipped candidates; repeated identities never retry.
- Reuse of an opportunity ID with a different canonical ADR-004
  `Opportunity.identity_key` is an invariant failure, not a duplicate. Payload
  and availability are excluded from that identity.
- Order simultaneous candidates BTC-USD → ETH-USD → SOL-USD, then by
  `opportunity_id`. Freeze and validate terms for each admitted candidate;
  every candidate with valid terms is baseline-selected, even while the portfolio
  is busy or halted. Selection remains separate from risk authorization.
- Authorize sequentially against the current portfolio and immediately submit
  each authorized order before considering the next candidate. This preserves
  the single-active-trade limitation.
- Do not force liquidation or cancellation at replay end. Ordinary final
  reconciliation may close a trade; remaining open positions stay open and are
  marked when valid data exists. Holding gaps preserve unresolved state.

## Rationale

A shared temporal boundary and reconciliation before new decisions prevent
lookahead and ensure authorization uses the portfolio after known outcomes.
Current-boundary admission prevents delayed snapshot discoveries from becoming
backdated trades. Terminal delivery makes candidate handling reproducible without
changing the scanner's snapshot contract.

Explicit asset and opportunity ordering removes dependence on mapping, set, or
input iteration order. Sequential submission makes the allocation of the one
available trade slot explicit. Selecting every valid-terms candidate establishes
a clean scanner-only baseline for measuring later research selection under the
same risk and execution rules. Preserving open state at the end avoids adding an
unrequested liquidation policy and hypothetical exit costs.

## Consequences

BTC-first ordering creates an explicit temporary allocation bias. Future
multi-position support may supersede that part of this ADR, with a versioned
allocation policy for comparable experiments.

Later agentic replay must preserve these temporal and delivery semantics unless
explicitly superseded. The scanner remains snapshot-based under
[ADR-004](ADR-004-scanner-snapshot-semantics.md); replay owns delivery state and
does not promise cross-run deduplication or restart recovery. Its in-memory audit
artifact does not provide durable lifecycle journaling.
