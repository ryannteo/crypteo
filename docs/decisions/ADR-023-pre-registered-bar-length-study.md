# ADR-023: Longer-bar candidate sources are chosen by a pre-registered study

## Status

Accepted

The study is complete and stopped; see its
[record](../evaluations/v2-bar-length-study.md).

## Context

Every candidate so far comes from one scanner: an hourly close crossing below
the lower Bollinger band (`bollinger_lower_cross_v1`), with deterministic terms
and a 6-hour holding limit. The
[baseline strategy study](../evaluations/v2-baseline-strategy-study.md) found
that no pre-registered variant of it pays for itself at the V1 release costs,
and [ADR-019](ADR-019-pre-registered-baseline-study.md)'s stop clause still
holds for selection. The
[trading-logic exploration](../evaluations/v2-trading-logic-exploration.md)
found no measurable edge before costs in 144 entry, exit and filter variants on
hourly bars, while costs took 31.62 to 39.74 USD per trade from a 100 USD risk
budget with the current stop. In
[ranking trial 1](../evaluations/v2-ranking-trial-1.md), research's lean did
not rank these candidates. It leaned `AGAINST` or `BALANCED` on every one, and
the baseline lost 30.35 USD per trade on average over its 47 windows.

Costs take that share because the stop is near. The stop sits two mean true
ranges below the reference, and sizing buys the quantity that loses the risk
budget at the stop. Fees and slippage scale with the position, so the nearer
the stop, the larger they are against the risk. With a stop of one mean true
range they rose to 41.62 to 55.08 USD. A longer bar has a wider true range, so
the same rule on 4-hour or daily bars sets a wider stop, buys a smaller position
and pays lower costs per unit of risk. Whether the crossing has an edge before
costs on longer bars has not been measured: the exploration varied holding
limits on hourly bars, not bar length.

The scanner fires per bar, so daily bars give far fewer candidates than hourly
bars over the same months. The baseline study's development period, about 13
months, would hold too few daily trades to judge.

## Decision

**Study.** Before any change to the candidate source, a scanner-only study
compares the crossing on 1-hour, 4-hour and daily bars on development data. No
model is called.

**Data.** Coinbase Exchange hourly candles for BTC-USD, ETH-USD and SOL-USD over
[2021-11-01T00:00:00Z, 2026-07-01T00:00:00Z), newly downloaded. Planning starts
at 2022-01-01T00:00:00Z. The period precedes the evaluation period
([ADR-013](ADR-013-v2-evaluation-scope.md)). The model's training cutoff does
not matter here, because no model is used.

**Bars.** A 4-hour bar aggregates the hourly candles opening in [t, t + 4h),
with t at 00:00, 04:00, 08:00, 12:00, 16:00 or 20:00 UTC. A daily bar
aggregates those opening in [t, t + 24h), with t at 00:00 UTC. A bar's open is
its first candle's open, its high the highest high, its low the lowest low, its
close the last candle's close, and its volume the sum of the volumes. A bar
exists only when every one of its hourly candles is in the dataset. It closes at
t plus its length and is available at the latest availability among its
candles. A 1-hour bar is the hourly candle itself.

**Candidates.** The scanner rule of `bollinger_lower_cross_v1`, applied to bars
instead of hourly candles: the same band, crossing, re-arm and availability
rules, with a missing bar resetting warm-up as a missing hour does. The decision
time T is the crossing bar's close. A candidate not available at T is excluded
and counted.

**Terms.** `TradeTermsService`'s rule, applied to bars: 21 contiguous bars
ending at T, the mean true range of the last 20, a stop at the reference close
minus 2 mean true ranges, and a target at the SMA20 of the last 20 closes, both
floored to the price increment, with the same rejection order.

**Trades.** Each candidate with valid terms is one single-candidate window. The
repository's `RiskService` authorizes the proposal at T against the hourly
candle closing at T, which closes with the bar. The repository's
`SimulatedBroker` executes it on hourly candles, entering on the candle opening
at T. The holding limit is 6 bars of the variant's length, counted in hourly
candles. Economics are the V1 release economics, unchanged: 10000 USD initial
cash in every window, risk fraction 0.01, entry and exit fee rates 0.004,
adverse slippage 0.0005, price increment 0.01 and quantity increment
0.00000001. A window that ends after the data is excluded and counted. A window
whose entry is rejected or whose position stays unresolved is counted and left
out of the measures.

**Variants.**

| Variant | Bar | Holding limit (`max_holding_bars`, hourly candles) |
| --- | --- | --- |
| 1h (control) | 1 hour | 6 |
| 4h | 4 hours | 24 |
| 1d | 1 day | 144 |

The control is the current scanner, terms and holding limit. It is reported for
reference and cannot be chosen.

**Samples.** The primary sample is causal per pair. In decision-time order, a
candidate is skipped while a trade taken earlier on the same pair holds its
slot. A trade holds the slot until its exit close, until its entry candle's
close if its entry is rejected, or until its window end if it stays unresolved,
and a candidate decided at or after that time may be taken. A candidate
rejected by the terms or the risk check takes no slot. Two more
samples are reported for description only: every candidate traded on its own,
and `plan_windows`' overlap rule, which drops a candidate when another candidate
of the same pair is observed strictly inside (T, T + holding limit).

**Measures**, per variant and sample: candidates; exclusions, and terms and
risk rejections, by reason; trades taken; closed trades; entry rejections;
unresolved windows; wins and losses; exit reasons; the total, mean and median
net P&L per closed trade; the standard error of the mean, the sample standard
deviation over the square root of the count; the mean before costs, at the same
quantity with entry and exit prices before slippage and no fees; and closed
trades per 30 days of the planning period. For the primary sample, also the
mean net P&L per closed trade in each half of the planning period: decisions
before 2024-04-01T00:00:00Z, and decisions from then on.

**Selection.** Only 4h and 1d can be chosen. A variant qualifies when its
primary sample has at least 100 closed trades and a positive mean net P&L per
closed trade overall and in each half. The chosen variant is the qualifying one
with the higher overall mean; a tie goes to 4h. If neither qualifies, the study
concludes that the lower-band crossing does not produce candidates that pay for
themselves at these costs on 4-hour or daily bars either.

**Integrity.**

- These parameters are fixed here, before the data is downloaded. No bar length,
  period, rule, cost, sample or criterion is added or changed after results are
  seen. The study runs once, and every variant is reported, chosen or not.
- Before it writes results, the script checks its bar path on 1-hour bars,
  using the baseline study's datasets and planning start. Its candidates and
  levels must equal the repository's scanner and `TradeTermsService`. Its
  control trades must equal the exploration's control (trigger · SMA20 · 2MTR ·
  6h) window by window in all three samples. If any differs, it stops without
  writing results.
- The script stays outside the repository, refuses to run while
  `ANTHROPIC_API_KEY` is set, and reads no candle closing after
  2026-07-01T00:00:00Z.

**After the study.** If 4h or 1d is chosen, a separate decision adopts it for
evaluation: a scanner policy and trade terms on that bar length, its holding
limit, and the data for the next research trial. That trial is the variant's
first test on data the study did not see. If neither is chosen, the candidate
source stays as it is, and ADR-019's stop clause and
[ADR-021](ADR-021-pre-registered-ranking-measure.md) are unchanged.

## Rationale

Bar length is the lever the records point to that has not been tested, and it
acts on costs, which set the payoff. Keeping the trigger, terms and holding
limit the same in bars changes one thing at a time. Executing on hourly candles
keeps fills and exits as precise as the current replay and reuses its risk and
broker code unchanged. An implementation could then keep hourly datasets and
the hourly replay clock. The causal per-pair sample avoids the overlap rule's
bias that the exploration found. The condition on each half keeps one market
regime from deciding. Two selectable variants keep the chance of a lucky winner
low. A period longer than ADR-019's gives the daily variant enough trades to
judge and spans more market conditions, and it ends where the evaluation period
begins.

Alternatives considered:

- *Keep ADR-019's period.* About 13 months holds too few daily trades to judge.
- *More bar lengths or holding limits.* Every added variant raises the chance
  that the best result is noise.
- *Execute on the longer bars.* That coarsens stop and target fills, and the
  replay has no broker for it.
- *A different trigger.* That is a new scanner, not a change of scale, and is
  left for a later decision.
- *Change the cost assumption.* That is a separate decision, and choosing costs
  by their effect on results would defeat the study.
- *Select on the overlap-rule sample.* The exploration showed that the rule
  flatters long windows.

## Consequences

- The study needs a new download of about 41,000 hourly candles per pair and a
  script kept outside the repository, written and tested in the web session.
  Its record goes in `docs/evaluations/` with the dataset versions, the
  calibration, and every variant's measures in all three samples.
- The study itself needs no repository change: Coinbase acquisition,
  `RiskService` and `SimulatedBroker` exist.
- Adopting a longer bar is its own decision and slices. It adds a scanner policy
  and trade terms on that bar length and changes plan settings. Research tools
  whose windows count hourly bars probably change too, so research policy
  identity changes and stored runs stop loading at that commit.
- Daily bars give far fewer candidates per month than hourly bars. The rate
  measure shows how long a trial on fresh data would take.
- The development period's results are not used to tune research prompts,
  tools or budgets.
