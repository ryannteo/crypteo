# V2 bar-length study: the lower-band crossing on 1-hour, 4-hour and daily bars

## Summary

[ADR-023](../decisions/ADR-023-pre-registered-bar-length-study.md)
pre-registered a scanner-only study of the lower-band crossing
(`bollinger_lower_cross_v1`) on 1-hour, 4-hour and daily bars, with the trigger,
terms and 6-bar holding limit unchanged in bar units. The study ran once on
newly downloaded Coinbase hourly candles for BTC-USD, ETH-USD and SOL-USD,
planning from 2022-01-01 to 2026-07-01. No model was called. The calibration on
1-hour bars reproduced the trading-logic exploration's control window by window
before any result was written.

In the primary sample, causal per pair, every variant had a negative mean net
P&L per closed trade: −34.32 USD for the 1-hour control (2,698 trades), −25.36
at 4 hours (753) and −14.84 daily (107). Both selectable variants had at least
100 closed trades, and both had negative means overall and in each half of the
planning period. The recorded selection is `STOP: neither 4h nor 1d has 100
closed trades and a positive mean overall and in each half`. Under ADR-023, the
study concludes that the lower-band crossing does not produce candidates that
pay for themselves at these costs on 4-hour or daily bars either.

Longer bars lowered costs as ADR-023 expected. Costs per trade, a descriptive
figure (the mean before costs minus the mean net), fell from 31.75 USD on
1-hour bars to 19.81 at 4 hours and 8.00 daily. No bar length had a positive
mean before costs in the primary sample: −2.57, −5.55 and −6.83 USD per trade.
The candidate source stays as it is.

## What was run

Code:

- Commit `d39b70a11232794b7199342d894898c93fec406a`, crypteo 2.0.0, source
  digest
  `sha256:0adccdaa06be66b0fef82f57383c0acc5d2048cfa88650188f863501a2afcd35`,
  both as recorded in `results.json`. Neither `src/crypteo/` nor
  `pyproject.toml` changed between ranking trial 1's run commit `4336149` and
  `d39b70a`, so this is the source digest that trial ran with. The study script
  refuses to run unless the installed crypteo is 2.0.0, the tree is clean,
  `d39b70a` is an ancestor of HEAD and the source digest is this one.
- Python 3.12.2, from the checkout's virtualenv. `ANTHROPIC_API_KEY` was unset.
  Every command of the script exits if it is set, and no step calls a model.

Scripts, kept outside the repository:

| File | Role | SHA-256 |
|---|---|---|
| `~/crypteo-eval/bar_length_study.py` | The study script, 1,616 lines | `12e37d4378a29fbab125bb6b5bc8cc0f72070a51949e4d004210e099f3ae5b03` |
| `~/crypteo-eval/test_bar_length_study.py` | Its 82 tests, 1,754 lines | `42e4bf8105bf123c4774fb410b2f31ac2d328a94f31347a8a40b3c46591fa997` |
| `~/crypteo-eval/trading_logic_explore.py` | The exploration's script, imported for its trade simulation, samples and measures | `48a99ad1a9278ffaa8b04e760a16b6bdfe14630d0bf221e0002741d2b96ec289` |

`bar_length_study.py` executes `trading_logic_explore.py` only after checking
its SHA-256, and records both hashes in `results.json`. Before the run, the
study's 82 tests and the exploration's 25 passed together (107) in a fresh
worktree of `d39b70a` with a Python 3.12 virtualenv, and ruff and mypy were
clean with the repository's configuration.

How bars reach the repository's code: bar k of a pair becomes an hourly
`Candle` k hours after the pair's first bar boundary, so a missing bar is a
missing hour. `OpportunityScanner` and `TradeTermsService` run unchanged on that
compressed series, and each candidate and its terms map back to the real bar,
whose close is the decision time T. On 1-hour bars the series is the dataset's
candles unchanged. Each trade is one single-candidate window on the real hourly
candles from T, through the repository's `RiskService` and `SimulatedBroker`,
with the V1 release economics as ADR-023 fixes them: 10000 USD initial cash in
every window, risk fraction 0.01, entry and exit fee rates 0.004, adverse
slippage 0.0005, price increment 0.01 and quantity increment 0.00000001. The
script checks these values before it runs.

| Variant | Bar | Bar boundaries (UTC) | Holding limit (`max_holding_bars`, hourly candles) |
|---|---|---|---|
| 1h (control) | 1 hour | every hour | 6 |
| 4h | 4 hours | 00, 04, 08, 12, 16, 20 | 24 |
| 1d | 1 day | 00:00 | 144 |

## Data

Coinbase Exchange hourly candles over [2021-11-01T00:00:00Z,
2026-07-01T00:00:00Z), 40,872 hourly slots per pair, listed in
`~/crypteo-eval/datasets-bar-study.json` and stored under
`~/crypteo-eval/data-bar-study/`. Planning started at 2022-01-01T00:00:00Z
(1,642 days to the cutoff), so every candidate had at least two months of
earlier history. The halves split at 2024-04-01T00:00:00Z.

| Pair | Candles | Missing hours | Dataset version | Acquisition ID |
|---|---|---|---|---|
| BTC-USD | 40,859 of 40,872 | 13 | `sha256:06587fdff880ff0db4d4fa4ef5a8d36c57caee7e740d63712684191796f72f3f` | `sha256:4c671a0779c0a66da44870bb83c1f873603d48aed279729ea5e5dac3e0a02485` |
| ETH-USD | 40,859 of 40,872 | 13 | `sha256:61ab872172d7205a736952e74b875d9c7a20bcd9632c9f9ece7a37fd5d1e0577` | `sha256:bd197157f638dc64da7fc42f6a4bb0e2f1b3434107c6c4eaff8baf9b0ac0b32d` |
| SOL-USD | 40,857 of 40,872 | 15 | `sha256:be95655076c76c5a1fffc08856861940304db238c34876fa538359811d167181` | `sha256:7efe42c9ff968fae61b00f43f6ca886a46f8ed3368d7ba6aa48e4e15ef4f81fc` |

Every pair's first candle opens at 2021-11-01T00:00:00Z and its last closes at
2026-07-01T00:00:00Z. The missing hours, by opening time:

- 2023-03-04: 18:00, 19:00 and 20:00 for BTC-USD and ETH-USD; 17:00 to 21:00
  for SOL-USD;
- 2025-10-25: 16:00 to 20:00, all three pairs;
- 2026-05-08: 02:00 to 06:00, all three pairs.

The last two runs are the gaps in the
[baseline strategy study](v2-baseline-strategy-study.md#data)'s data. A bar
exists only when all its hourly candles do, so each run removes the 4-hour and
daily bars that contain it, and a missing bar resets the scanner's warm-up.

All three acquisition records were created at 2026-11-18T16:45:44.596461Z, and
each pair's 137 captures record that same acquisition start. This is after the
pre-registration commit at 2026-11-15T17:26:04Z.

## Pre-registration and integrity

ADR-023 fixed the data, bars, candidates, terms, trades, variants, samples,
measures, selection rule and stop conclusion. It was committed as `d39b70a` at
2026-11-15T17:26:04Z, before the data was downloaded, and CI passed on that
commit. The script enforces the rest:

- `acquire` is one-time: it refuses to run once the dataset list exists.
- `study` loads only the listed dataset versions and acquisition IDs, and it
  refuses unless every pair's acquisition record and every capture started
  after the pre-registration commit. Each capture's `request.json` is checked
  against its capture ID before its start time is read.
- It refuses any candle closing after 2026-07-01T00:00:00Z and any candle not
  available at its close.
- `study` runs once. It refuses to start if any of its three outputs exists,
  and it creates each with exclusive create.
- It runs the calibration below first, and stops without writing results if any
  check differs.
- Every variant is reported in all three samples, chosen or not.

Of the script's commands, only `study` computes scanner output, terms or
outcomes on the study data. `acquire` downloads and stores it, and `check`
reports only the datasets' identities, coverage, gaps and acquisition times.
During the script's build, one read-only `calibrate` ran on the baseline
study's datasets and the exploration's recorded outputs; it writes nothing.

## Calibration

ADR-023 requires the bar path to reproduce, on 1-hour bars, the repository's
scanner and `TradeTermsService` and the exploration's control (trigger · SMA20
· 2MTR · 6h) window by window in all three samples. The calibration used the
baseline study's datasets, pinned by `~/crypteo-eval/datasets-study.json`
(SHA-256 `ef54523f0d1b630e1c33585b6e57ed1df0f045e468c239f80eac2439869b53b9`),
planning from 2025-07-01T00:00:00Z, and the exploration's outputs, pinned by
SHA-256: `results.json`
(`e50bb77896dbefd74b838c42cdf4312ff6316d5e161b90d98de231ffc8dc2eaa`) and
`rows.json` (`8d61083beb1a5564a0cbb3ca1666342f92430965bd143c6a14e377f9dbce31dd`).
It checked, in order:

1. **Candidates:** the same 793 candidates, opportunity IDs and decision times
   as the exploration's scanner pass and its `rows.json`.
2. **Levels:** at every T, the bar path's levels or rejection equal
   `TradeTermsService`'s on the real data.
3. **Trades:** every control trade's reference, stop, target, status, exit
   reason, net P&L and risk reason equal the exploration's.
4. **Samples:** every candidate's status in each sample equals the
   exploration's classification.
5. **Summaries:** each sample's closed trades, wins, losses, exits and total net
   P&L equal the exploration's control: 534 closed in the overlap-rule sample,
   654 for all candidates and 578 causal per pair.

`results.json` records `passed: true` with all five checks.

## Results

USD per closed trade throughout, rounded to cents. The tables are pasted from
`tables.md`, the output of `record_tables.py` (see [Outputs](#outputs)), which
re-derives every figure from `rows.json` and asserts that it equals
`results.json`.

"Mean before costs" is ADR-023's measure: the same quantity, with entry and exit
prices before slippage and no fees. "Costs per trade" is a derived, descriptive
figure: the mean before costs minus the mean net, as the exploration computed
it. It is not a pre-registered measure.

Candidates, one full-history scan per pair and variant from
2022-01-01T00:00:00Z:

| Variant | BTC-USD | ETH-USD | SOL-USD | All |
|---|---|---|---|---|
| 1h | 1,237 | 1,240 | 1,259 | 3,736 |
| 4h | 288 | 279 | 312 | 879 |
| 1d | 36 | 41 | 44 | 121 |

Every candidate was available at T and had valid terms: no exclusion for
availability and no terms rejection in any variant.

### Primary sample

Causal per pair: in decision-time order, a candidate is skipped while an
earlier trade on the same pair holds its slot.

| Variant | Closed | Mean net | Standard error | Mean before costs | Costs per trade | Before 2024-04-01 | From 2024-04-01 | Closed per 30 days |
|---|---|---|---|---|---|---|---|---|
| 1h (control) | 2,698 | −34.32 | 1.02 | −2.57 | 31.75 | −32.78 (1,332) | −35.82 (1,366) | 49.29 |
| 4h | 753 | −25.36 | 2.34 | −5.55 | 19.81 | −24.28 (352) | −26.31 (401) | 13.76 |
| 1d | 107 | −14.84 | 6.58 | −6.83 | 8.00 | −15.47 (47) | −14.34 (60) | 1.95 |

The halves give each mean with its closed trades in brackets. The standard
error is ADR-023's: the sample standard deviation (n − 1) of net P&L over the
square root of the count.

Exact values from `results.json`:

| Variant | Total net | Mean net |
|---|---|---|
| 1h | −92603.4093524930504 | −34.322983451628261823573017049666419570051890289103 |
| 4h | −19094.9650973214200 | −25.358519385553014608233731739707835325365205843293 |
| 1d | −1587.4714241132156 | −14.836181533768370093457943925233644859813084112150 |

### Every sample

The two descriptive samples are every candidate traded on its own (all
candidates), and `plan_windows`' overlap rule, which drops a candidate when
another of the same pair is observed strictly inside (T, T + holding limit).

| Variant | Sample | Candidates | Beyond data | Busy or overlapping | Risk rejections | Trades taken | Closed | Unresolved | Wins / losses | Exits (STOP / TARGET / TIME) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1h | Causal per pair (primary) | 3,736 | 0 | 386 | 651 | 2,699 | 2,698 | 1 | 864 / 1,834 | 818 / 513 / 1,367 |
| 1h | All candidates | 3,736 | 0 | – | 656 | 3,080 | 3,079 | 1 | 980 / 2,099 | 911 / 576 / 1,592 |
| 1h | Overlap rule | 3,736 | 0 | 817 | 470 | 2,449 | 2,448 | 1 | 909 / 1,539 | 583 / 523 / 1,342 |
| 4h | Causal per pair (primary) | 879 | 1 | 104 | 20 | 754 | 753 | 1 | 305 / 448 | 250 / 171 / 332 |
| 4h | All candidates | 879 | 1 | – | 20 | 858 | 857 | 1 | 345 / 512 | 280 / 193 / 384 |
| 4h | Overlap rule | 879 | 1 | 184 | 15 | 679 | 678 | 1 | 318 / 360 | 186 / 179 / 313 |
| 1d | Causal per pair (primary) | 121 | 1 | 13 | 0 | 107 | 107 | 0 | 50 / 57 | 33 / 15 / 59 |
| 1d | All candidates | 121 | 1 | – | 0 | 120 | 120 | 0 | 57 / 63 | 36 / 17 / 67 |
| 1d | Overlap rule | 121 | 1 | 24 | 0 | 96 | 96 | 0 | 49 / 47 | 25 / 15 / 56 |

| Variant | Sample | Closed | Total net | Mean net | Median net | Standard error | Mean before costs | Costs per trade | Closed per 30 days |
|---|---|---|---|---|---|---|---|---|---|
| 1h | Causal per pair (primary) | 2,698 | −92603.41 | −34.32 | −29.58 | 1.02 | −2.57 | 31.75 | 49.29 |
| 1h | All candidates | 3,079 | −103665.47 | −33.67 | −28.95 | 0.95 | −2.08 | 31.59 | 56.25 |
| 1h | Overlap rule | 2,448 | −62503.46 | −25.53 | −17.85 | 1.05 | 6.00 | 31.53 | 44.73 |
| 4h | Causal per pair (primary) | 753 | −19094.97 | −25.36 | −21.19 | 2.34 | −5.55 | 19.81 | 13.76 |
| 4h | All candidates | 857 | −21582.02 | −25.18 | −21.21 | 2.18 | −5.64 | 19.55 | 15.66 |
| 4h | Overlap rule | 678 | −10482.41 | −15.46 | −8.03 | 2.44 | 4.44 | 19.90 | 12.39 |
| 1d | Causal per pair (primary) | 107 | −1587.47 | −14.84 | −6.06 | 6.58 | −6.83 | 8.00 | 1.95 |
| 1d | All candidates | 120 | −1622.34 | −13.52 | −5.95 | 6.20 | −5.56 | 7.96 | 2.19 |
| 1d | Overlap rule | 96 | −558.76 | −5.82 | 3.35 | 7.03 | 2.24 | 8.06 | 1.75 |

No closed trade had zero net P&L, and no entry was rejected. Every risk
rejection was `NON_POSITIVE_TARGET_REWARD`. Over all candidates whose window
lies inside the data, before any sample rule, the rejected candidates are
exactly those whose estimated net target reward per unit G (the
[specification](../v1-specification.md)'s `RiskService` rule) is at most zero:
656 at 1h, 20 at 4h and none daily.

The windows that are unresolved or end beyond the data, by sample:

| Variant | Pair | T | Causal per pair | All candidates | Overlap rule |
|---|---|---|---|---|---|
| 1h | SOL-USD | 2023-03-04T16:00:00Z | DATA_GAP | DATA_GAP | DATA_GAP |
| 4h | ETH-USD | 2026-05-07T16:00:00Z | DATA_GAP | DATA_GAP | DATA_GAP |
| 4h | BTC-USD | 2026-06-30T12:00:00Z | BEYOND_DATA | BEYOND_DATA | BEYOND_DATA |
| 1d | BTC-USD | 2026-06-26T00:00:00Z | BEYOND_DATA | BEYOND_DATA | BEYOND_DATA |

The two `DATA_GAP` windows reach missing hours: the 1h window from
2023-03-04T16:00:00Z reaches the SOL-USD gap from 17:00, and the 4h window from
2026-05-07T16:00:00Z reaches the 2026-05-08 gap from 02:00. The two
`BEYOND_DATA` windows end after 2026-07-01T00:00:00Z. Unresolved windows are
counted and left out of the measures; windows beyond the data are excluded and
counted.

### Descriptive breakdowns

These are derived from `rows.json` and are not pre-registered measures.

The primary sample by exit reason:

| Variant | Exit | Trades | Total net | Mean net |
|---|---|---|---|---|
| 1h | STOP | 818 | −81664.57 | −99.83 |
| 1h | TARGET | 513 | 15543.31 | 30.30 |
| 1h | TIME | 1,367 | −26482.15 | −19.37 |
| 4h | STOP | 250 | −24978.96 | −99.92 |
| 4h | TARGET | 171 | 8741.20 | 51.12 |
| 4h | TIME | 332 | −2857.21 | −8.61 |
| 1d | STOP | 33 | −3301.13 | −100.03 |
| 1d | TARGET | 15 | 1012.42 | 67.49 |
| 1d | TIME | 59 | 701.24 | 11.89 |

The median stop distance (reference minus stop, as a percentage of the
reference) over the primary sample's closed trades was 1.971% at 1h, 3.957% at
4h and 11.569% daily.

The mean before costs, with the standard error of the before-costs values,
computed as for net P&L:

| Variant | Causal per pair (primary) | All candidates | Overlap rule |
|---|---|---|---|
| 1h | −2.57 ± 1.01 | −2.08 ± 0.94 | 6.00 ± 1.03 |
| 4h | −5.55 ± 2.32 | −5.64 ± 2.16 | 4.44 ± 2.41 |
| 1d | −6.83 ± 6.62 | −5.56 ± 6.24 | 2.24 ± 7.06 |

## Selection

ADR-023's rule, applied as written to the primary sample of the two selectable
variants. The 1h control is reported for reference and cannot be chosen.

| Variant | At least 100 closed | Mean positive | First half positive | Second half positive | Qualifies |
|---|---|---|---|---|---|
| 4h | yes (753) | no | no | no | no |
| 1d | yes (107) | no | no | no | no |

Neither variant qualifies. Outcome: **STOP**. `results.json` records the
selection as `STOP: neither 4h nor 1d has 100 closed trades and a positive mean
overall and in each half`. Both variants had enough trades; neither had a
positive mean, overall or in either half. The study concludes, in ADR-023's
words, that the lower-band crossing does not produce candidates that pay for
themselves at these costs on 4-hour or daily bars either.

No variant is chosen, so ADR-023's adoption step does not arise. The candidate
source stays as it is, and
[ADR-019](../decisions/ADR-019-pre-registered-baseline-study.md)'s stop clause
and [ADR-021](../decisions/ADR-021-pre-registered-ranking-measure.md) are
unchanged.

## Interpretation

These statements describe the recorded results.

- **Longer bars cut costs, as ADR-023 expected.** A longer bar has a wider mean
  true range, so the stop sits further away: the median stop distance was
  1.97% of the reference at 1h, 3.96% at 4h and 11.57% daily. Sizing then buys a
  smaller position for the same 100 USD risk, and costs per trade fell from
  31.75 to 19.81 and 8.00 USD in the primary sample.
- **No bar length has an edge before costs.** In the primary sample the mean
  before costs was −2.57 ± 1.01 at 1h, −5.55 ± 2.32 at 4h and −6.83 ± 6.62
  daily, and the all-candidate sample agrees in sign. Lower costs therefore
  brought the net mean closer to zero without making it positive. The daily
  figure is within about one standard error of zero, so it shows no edge
  either way.
- **Stops still outweigh targets.** A stop exit lost about the full 1% risk at
  every bar length (−99.83 to −100.03 on average). Target exits paid more on
  longer bars (30.30, 51.12 and 67.49), and daily time exits were positive on
  average (11.89). At 4 hours and daily, stop exits lost more in total than
  target and time exits together gained; at 1 hour, target and time exits
  together lost as well.
- **Both halves agree.** Every variant's mean was negative before and from
  2024-04-01, so no single half of the period decides the result.
- **The overlap rule flatters again.** Under `plan_windows`' rule the mean
  before costs is positive at every bar length (6.00, 4.44 and 2.24) and the
  daily median net is positive (3.35), while the causal and all-candidate
  samples are negative before costs. This is the bias the
  [trading-logic exploration](v2-trading-logic-exploration.md) found, and the
  reason ADR-023 selects on the causal sample.
- **The 1-hour control is consistent with earlier records.** On this longer
  period the control's primary mean was −34.32 over 2,698 trades. The
  exploration's causal per-pair control was −37.61 over 578 trades on its
  twelve months. The periods differ, so this is a comparison, not a
  reproduction; the calibration is the reproduction.
- **Development data only.** The data precedes
  [ADR-013](../decisions/ADR-013-v2-evaluation-scope.md)'s evaluation period.
  The study called no model and makes no claim about research selection.

This record changes no decision.

## Limitations

- One period, 2022-01-01 to 2026-07-01, and three pairs.
- One trigger, one terms rule, a holding limit of 6 bars, and one set of fees,
  slippage and risk fraction. No other bar length, boundary or holding limit
  was run.
- Trades are not independent. Crossings cluster in time and across pairs, and
  in the all-candidate sample trades overlap, so the standard errors understate
  the uncertainty.
- The daily sample is small: 107 closed trades in the primary sample, with a
  standard error of 6.58 USD.
- Bars are aggregated from hourly candles at fixed UTC boundaries. A missing
  hour removes the whole bar containing it.
- Every window starts from 10,000 USD, so sizing does not compound as in a
  single long replay.
- The scripts are outside the repository, and their tests do not run in CI.
  This record, the outputs and their checksums are their evidence.

## Outputs

The study's outputs, in `~/crypteo-eval/bar-study/`, and its dataset list:

| File | SHA-256 |
|---|---|
| `results.json` | `769d00be6d9a9d4af4bb347abf3df88ed7e12d9c59e2c5de49abe8f70e2968c5` |
| `rows.json` | `a1683b8dc6b628e2e3074b2b1c183641aab370d5a1a2e134ce22c4f19c8cb9ab` |
| `report.txt` | `b14631bddd028f3e4db0520570a5a96c2ece48dae25f07052150e891e5ad8031` |
| `~/crypteo-eval/datasets-bar-study.json` | `ff6fdd7b59371ca5b33ec4bd6f6dc5b3f5ae5bb1b11bb2f5d27a712afd420242` |

`results.json` holds the fixed values, dataset coverage, calibration, every
variant's measures in all three samples, the qualification and the selection.
`rows.json` holds every candidate's bar, decision time, availability, levels,
risk result, outcome and status in each sample. `report.txt` is the script's
summary of `results.json`, rounded to 4 places.

The record's tables, in `~/crypteo-eval/bar-study-record/`:

| File | Content | SHA-256 |
|---|---|---|
| `record_tables.py` | The table script | `cc6ba1aa1f45fd7a2a259c7d03f34676769dfe2f3af21854f4a123d765607db5` |
| `tables.md` | Its output | `74655f722d69c671e028f5de7adb0ec8c6a3d98cddc99fa0c2d9949b5b2665f9` |

`record_tables.py` uses the standard library only. It exits unless
`results.json` and `rows.json` have their pinned SHA-256. It then rebuilds every
candidate's status in all three samples from `rows.json`'s rows, with its own
slot and overlap code, and recomputes every count, total, mean, median,
standard error, mean before costs, half and rate. It checks every bar's
boundary and length, T against the bar's close, every stop and target against
the floored formula, and the risk check's sign of G. It asserts that each figure
equals `results.json` and that the selection follows. The record's tables are
pasted from its output.
