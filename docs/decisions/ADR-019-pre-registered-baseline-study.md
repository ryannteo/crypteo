# ADR-019: Baseline strategy changes are chosen by a pre-registered study

## Status

Accepted. [ADR-021](ADR-021-pre-registered-ranking-measure.md) narrowly
supersedes the stop clause for ranking trials only; it still holds for
selection.

## Context

The scanner-only baseline buys at the next open after an hourly close crosses
below the lower Bollinger band, with deterministic stop and target terms, a
6-hour holding limit, 0.4% fees on each side and 0.05% adverse slippage. In
trials 6, 7 and 8 it closed 20 trades: 5 reached their target and 10 ended by
time exit. It lost money in each trial: 159.95, 301.24 and 345.89 USD
([trial 6](../evaluations/v2-july-2026-trial-6.md),
[trial 7](../evaluations/v2-july-2026-trial-7.md),
[trial 8](../evaluations/v2-july-2026-trial-8.md)). In trial 6's windows,
12.5% to 43.75% of past crossings reached their SMA20 within six hours.

Research declined every candidate in trials 5 to 8: 52 opportunity-led runs on
42 distinct candidates, since trial 6 re-ran trial 5's first 10 windows. A
constant "never trade" rule gives the same result, so the evaluation cannot yet
tell research judgment from refusal. Better candidates could change that, but
changing the baseline after looking at those trials' results would fit it to
windows already inspected. [ADR-018](ADR-018-plan-only-authorized-candidates.md)
removed candidates the baseline could not trade; this decision fixes how the
baseline strategy itself may change.

## Decision

**Study.** Before any further research trial, a scanner-only study compares four
baseline variants on development data. No model is called.

**Data.** Coinbase Exchange hourly candles for BTC-USD, ETH-USD and SOL-USD over
[2025-06-01T00:00:00Z, 2026-07-01T00:00:00Z), newly downloaded. Planning starts
at 2025-07-01T00:00:00Z, so every candidate has at least 30 days of earlier
history. The period precedes the evaluation period
([ADR-013](ADR-013-v2-evaluation-scope.md)). The model's training cutoff does
not matter here, because no model is used.

**Economics.** The V1 release economics, unchanged: 10000 USD initial cash, risk
fraction 0.01, entry and exit fee rates 0.004, adverse slippage 0.0005, price
increment 0.01 and quantity increment 0.00000001. The cost assumption is not
part of the study.

**Planning.** Each variant plans its own windows with `plan_windows`, purpose
`PLUMBING`, a limit larger than the number of candidates, and ADR-018's
authorization rule.

**Variants.**

| Variant | Holding limit (`max_holding_bars`) | Trigger filter |
| --- | --- | --- |
| V0 (control) | 6 | none |
| V1 | 24 | none |
| V2 | 6 | SMA20 ≥ SMA50 at T |
| V3 | 24 | SMA20 ≥ SMA50 at T |

The filter uses the hourly SMA20 and SMA50 of the closes ending at the signal
hour T, as the `trend` tool computes them through a gateway whose clock is T.
It is applied to each plan's selected windows; overlap and authorization are as
planned. A window without a trend result is dropped from V2 and V3 and counted.

**Replay.** Each remaining window is replayed scanner-only. A window whose
position stays unresolved is counted and left out of the measures.

**Measures**, per variant: closed trades, wins and losses, exit reasons, and the
total, mean and median realized net P&L per closed trade.

**Selection.** A variant is eligible when it has at least 100 closed trades. The
chosen variant is the eligible one with the highest mean realized net P&L per
closed trade; a tie goes to the lower-numbered variant. If no eligible variant
has a positive mean, the study concludes that this scanner does not produce
candidates that pay for themselves at these costs. No further research trial
then runs on it until a new decision changes the scanner.

**Integrity.** These parameters are fixed here, before the data is downloaded.
No variant, period, filter, cost or criterion is added or changed after results
are seen. The study runs once, and all four variants are reported, chosen or
not.

**After the study.** If V1, V2 or V3 is chosen, a separate decision adopts it
for evaluation: the holding limit through the plan settings, and the filter in
the planner. The next research trial then runs the chosen variant on
uninspected windows from 2026-08-01, which is the variant's first test on data
the study did not see. If V0 is chosen, the baseline stays as it is.

## Rationale

The records point at two weaknesses. A six-hour limit cuts off a mean-reversion
target that most crossings do not reach that fast, and many triggers came in
short-term downtrends. A longer holding limit and a trend filter address one
each, and V3 combines them. Four variants keep the chance of a lucky winner low,
and the 100-trade minimum keeps a handful of trades from deciding. Development
data from before the evaluation period keeps the choice away from inspected
windows, and the next trial tests the choice out of sample without spending
fresh data twice.

Alternatives considered:

- *Tune on the trial windows.* They are inspected, and together they hold 31
  distinct baseline trades.
- *Search many holding limits and thresholds.* Every added variant raises the
  chance that the best result is noise.
- *Change the cost assumption.* That is a separate decision, and choosing costs
  by their effect on results would defeat the study.
- *Enter only after a close back inside the band.* That needs a scanner change
  and is left for a later decision.

## Consequences

- The study needs a new download and a script kept outside the repository,
  written and tested in the web session. Its record goes in `docs/evaluations/`
  with the dataset versions, plan IDs, and every variant's measures.
- The study itself needs no repository change: `plan_windows`,
  `run_scanner_only` and the `trend` tool exist.
- Adopting a non-control variant is its own decision and slice. It changes plan
  settings or the planner, so plan identities change.
- The development period's results are not used to tune research prompts,
  tools or budgets.
- A stop outcome ends evaluation on the current scanner; changing the scanner
  needs its own decision.
