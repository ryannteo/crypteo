# ADR-006: Decision-time risk authorization under unknown next-open execution

## Status

Accepted.

## Context

Sizing must precede next-open execution, whose price is unknown at decision time.
Using that future price would violate the point-in-time boundary. A stop-distance
estimate alone also omits execution costs that consume cash and increase loss.

Sources: [risk service](../../src/crypteo/risk.py),
[risk contracts](../../src/crypteo/domain/risk.py),
[shared execution pricing](../../src/crypteo/_execution_costs.py), and
[risk tests](../../tests/test_risk.py).

## Decision

Authorization uses only information available at authoritative gateway time, which
must equal `decision_at`. The sizing reference is the close of the exact visible
completed candle ending at `decision_at`; there is no older-candle fallback. The
actual next-open price must never be used for sizing.

Estimated entry and stop execution use the same adverse slippage and tick rounding
as the broker: BUY rounds up, SELL rounds down. Entry fees and stop exit fees are
included in estimated cash use and stop loss. Quantity is the largest whole number
of configured quantity increments satisfying both the risk budget and available
cash. The current risk service uses flat cash as its capital basis.

Authorization bounds the modeled nongapped stop loss under those pricing and cost
assumptions. It does **not** guarantee realized loss is at or below the risk budget:
entry or exit gaps can change execution prices. The simulated broker remains
responsible for actual next-open affordability and execution outcomes, without
resizing the order.
Authorization does not mutate or reserve portfolio state, consume an order ID, or
advance the clock; submission and execution remain separate operations.

## Rationale

A decision-time reference preserves historical integrity. Shared pricing avoids
disagreement between estimated costs and broker mechanics, while maximal aligned
sizing makes both financial constraints exact and testable. Separating estimates
from realized outcomes prevents a modeled risk bound from becoming a false loss
guarantee. Tests demonstrate nongapped reconciliation, gap losses above budget,
and next-open affordability rejection.

## Consequences

Consumers must retain the reference and configuration alongside authorization
estimates and distinguish them from eventual fills and realized PnL. Public
authorization contracts validate execution estimates, alignment, and maximality;
future portfolio history is an input error before ordinary risk rejection.

Specific risk fractions, holding durations, stop/target formulas, and the temporary
one-position limitation are not permanent architectural constraints of this ADR.
Risk validates supplied stop/target levels independently of their generator.
[ADR-007](ADR-007-deterministic-trade-terms.md) defines the separate deterministic
generation boundary.
