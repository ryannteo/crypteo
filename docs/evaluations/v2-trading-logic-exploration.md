# V2 trading-logic exploration: entry, exit and filter variants of the crossing scanner

## Summary

This exploration varied the trading logic around the current scanner's
lower-band crossing on the development data of the
[baseline strategy study](v2-baseline-strategy-study.md): two entries, four
targets, two stops, three holding limits and three filters, 144 variants in all.
It is exploratory and descriptive. It was not pre-registered, it selects and
adopts nothing, and it called no model.

The control variant reproduced the study window by window. For the 793 scanner
candidates, the current terms put the target about as far from entry as the
stop before costs (median reward ÷ risk 1.020), and costs cut that to 0.309. For
139 candidates the reward at the target did not cover the costs.

No variant had a positive mean net P&L per trade when every candidate was
traded, or when trades were taken one at a time as the replay engine takes
them. Before costs, no variant's mean exceeded 3.94 USD per trade in those
samples, while costs were 31.62 to 39.74 USD per trade with the current stop.
Positive means appeared only under the planning rule that drops a candidate when
another crossing follows it within its window, a rule the
[specification](../v1-specification.md#evaluation-window-planning) already notes
may bias the sample.

On this data, the crossing trigger shows no measurable edge before costs, and
no entry, exit or filter change tested here creates one. No variant is carried
forward to a pre-registered test.

## What was run

- Script `~/crypteo-eval/trading_logic_explore.py`, kept outside the repository,
  SHA-256 `48a99ad1a9278ffaa8b04e760a16b6bdfe14630d0bf221e0002741d2b96ec289`.
  Its tests, `~/crypteo-eval/test_trading_logic_explore.py`, SHA-256
  `9053061086aceb967b53ae8a7389af886e6669da34b68bd746692a98fa7c704d`, run
  against the repository's test helpers and synthetic data.
- Commit `79391f772f2d4b00844dd0755d337ca71201cc9e`, source digest
  `sha256:4199ee6f81fa7381e9bbde288518f00bb3014cb37b629fc79955ca93e03d1f3a`, as
  recorded in `results.json`. This is the source digest the study ran with.
- No model was called. The script exits when `ANTHROPIC_API_KEY` is set.
- The script loads only the study's three pinned dataset versions and refuses
  any candle closing after 2026-07-01T00:00:00Z.

Outputs, in `~/crypteo-eval/explore-2/`:

| File | SHA-256 |
|---|---|
| `results.json` | `e50bb77896dbefd74b838c42cdf4312ff6316d5e161b90d98de231ffc8dc2eaa` |
| `rows.json` | `8d61083beb1a5564a0cbb3ca1666342f92430965bd143c6a14e377f9dbce31dd` |
| `report.txt` | `2bf8600c1c8462c0141869edd3136da5d10337907ed77dfc6d1166738a67126e` |

`results.json` holds the summaries and `rows.json` every candidate's decision
times, filter values, levels and outcomes. An earlier version of the script,
SHA-256 `6beb59278e2d29dcb959b7491abbb8e7b44e5b20e9780037425e16d371680aa1`,
lacked the two causal samples; its non-overlapping and all-candidate results are
identical to these.

## Data

The study's three datasets, BTC-USD, ETH-USD and SOL-USD over
[2025-06-01T00:00:00Z, 2026-07-01T00:00:00Z), with the study's dataset versions.
Candidates came from one full-history scan per pair, as in `plan_windows`, from
2025-07-01T00:00:00Z: 793 candidates, all available at observation.

## Method

### Variants

| Factor | Values |
|---|---|
| Entry | Trigger: decide at the crossing close T, as the baseline does. Confirm: decide at the first close at or above its own lower band in the 6 bars after T; no confirmation, no trade. |
| Target | SMA20; upper band (SMA20 + 2σ); R1, the reference plus the stop distance; R2, the reference plus twice the stop distance |
| Stop | The reference minus 2 or 1 times the mean true range of the last 20 bars (2MTR, 1MTR) |
| Holding limit | 6, 24 or 72 bars |
| Filter | None; H1 trend, the study's SMA20 ≥ SMA50 at the decision time; D1 trend, the last completed UTC daily close above the mean of the last 20 daily closes, a daily close being the close of the hourly candle closing at 00:00 UTC |

"At or above its own lower band" is the scanner's own re-arm condition. The
control is trigger · SMA20 · 2MTR, the current terms.

Levels at each decision time use the repository's SMA20, mean-true-range and
Bollinger computations, floored to the price increment, with
`TradeTermsService`'s rejection order. At every decision time the script checked
that the SMA20 · 2MTR levels equal `TradeTermsService`'s. Each trade is one
single-candidate window through the repository's `RiskService` and
`SimulatedBroker` with the V1 release economics the study used: 10,000 USD
initial cash, risk fraction 0.01, entry and exit fee rates 0.004, adverse
slippage 0.0005. Every window starts from the same cash, so sizing does not
compound.

### Samples

Every variant is measured on four samples. All of them first drop candidates
not available at observation, without confirmation, or whose window ends after
the data.

- **Non-overlapping:** `plan_windows`' rule. A candidate is dropped when another
  candidate of the same pair falls inside its window. The rule reads later
  candidates, so it is not causal. It is kept to reproduce the study.
- **All candidates:** every candidate traded on its own.
- **Causal per pair:** in decision order, a candidate is skipped while a trade
  taken earlier on the same pair is open.
- **Causal portfolio:** one trade at a time across all pairs, ties in BTC, ETH,
  SOL order. This is the replay engine's rule: on synthetic data, the control's
  closed trades at 6 and 24 bars equal those of one `run_scanner_only` replay
  over all three pairs.

Candidates rejected by the terms or the risk check, and filtered-out
candidates, take no slot. A closed trade frees its slot at its exit close.

### Measures

Per variant and sample: closed trades, mean and median net P&L per closed trade,
the mean before costs (no fees and no slippage at the same quantity), and the
mean net P&L by half-year of the decision time (July to December 2025, January
to June 2026). Unresolved windows are counted and left out.

### Calibration

For holding limits 6 and 24, the control's non-overlapping sample equals
`plan_windows` decision by decision. It also reproduced the study's
`results.json` for V0, V1, V2 and V3: 534, 322, 218 and 125 windows, each with
the same status, exit reason and net P&L. The script stops before writing
results if any study count or total differs. The plan IDs,
`sha256:fea16e80b226b931d5ff880bb8e3f93665a3525f6e138c05ff23ec759561041b`
(6 bars) and
`sha256:09da2f142f2b690afb9cad1276467340137f1d31c613343b40b37928614ee363`
(24 bars), differ from the study's because the plan identity includes the
selection limit. This script uses 100,000; the study does not record its limit.

## Results

USD throughout. Means are per closed trade, rounded to cents.

### The current terms at the trigger

All 793 candidates had valid terms. The risk check authorized 654 and rejected
139 with `NON_POSITIVE_TARGET_REWARD`. Net reward ÷ risk was at least 0.5 for
229 candidates and at least 1 for 41.

| Measure | p10 | p25 | p50 | p75 | p90 |
|---|---|---|---|---|---|
| Target distance (% of reference) | 0.690 | 1.058 | 1.733 | 2.561 | 3.807 |
| Stop distance (% of reference) | 0.822 | 1.166 | 1.663 | 2.296 | 2.907 |
| Reward ÷ risk before costs | 0.645 | 0.791 | 1.020 | 1.307 | 1.688 |
| Reward ÷ risk after costs | −0.114 | 0.068 | 0.309 | 0.563 | 0.837 |
| Costs ÷ target move | 0.243 | 0.358 | 0.526 | 0.862 | 1.311 |

Before costs uses the reference, target and stop prices. After costs uses the
risk check's estimated execution prices and fees, per unit. Costs ÷ target move
is the share of the move from the reference to the target taken by fees and
slippage.

### Confirmation

Of the 793 candidates, the first close back at or above the lower band came 1
bar after T for 366, 2 bars for 176, 3 for 127, 4 for 66, 5 for 35 and 6 for 14.
Nine did not confirm within 6 bars.

### Variants

| Sample | Variants with a positive mean | Variants with a positive mean before costs |
|---|---|---|
| Non-overlapping | 39 of 144 | 142 of 144 |
| All candidates | 0 of 144 | 15 of 144 |
| Causal per pair | 0 of 144 | 16 of 144 |
| Causal portfolio | 0 of 144 | 34 of 144 |

Mean net P&L (closed trades) by sample, no filter:

| Variant | Non-overlapping | All candidates | Causal per pair | Causal portfolio |
|---|---|---|---|---|
| Trigger · SMA20 · 2MTR · 6h | −30.52 (534) | −37.86 (654) | −37.61 (578) | −36.30 (313) |
| Trigger · SMA20 · 2MTR · 24h | −10.49 (321) | −38.33 (650) | −37.23 (535) | −39.61 (272) |
| Trigger · SMA20 · 2MTR · 72h | 11.62 (89) | −37.68 (648) | −36.33 (517) | −39.80 (253) |
| Trigger · R2 · 2MTR · 72h | 45.27 (100) | −39.51 (774) | −37.61 (528) | −42.82 (233) |
| Trigger · upper band · 2MTR · 72h | 39.46 (98) | −38.55 (752) | −36.48 (527) | −44.44 (237) |

The first two rows' non-overlapping figures are the study's V0 and V1.

The five highest means in each causal sample, with the control:

| Variant (causal per pair) | Closed | Mean | Median | Before costs | Jul–Dec 2025 | Jan–Jun 2026 |
|---|---|---|---|---|---|---|
| Confirm · SMA20 · 2MTR · 6h | 499 | −31.88 | −26.35 | −0.14 | −29.03 | −34.64 |
| Confirm · R1 · 2MTR · 6h | 629 | −32.04 | −27.76 | 1.16 | −28.54 | −35.20 |
| Confirm · R1 · 2MTR · 6h · H1 trend | 197 | −32.45 | −31.84 | 2.01 | −31.31 | −33.57 |
| Confirm · R2 · 2MTR · 6h | 702 | −33.37 | −32.00 | 2.22 | −29.19 | −37.26 |
| Confirm · upper band · 2MTR · 6h | 656 | −33.45 | −30.49 | 1.08 | −32.12 | −34.70 |
| Trigger · SMA20 · 2MTR · 6h (control) | 578 | −37.61 | −34.28 | −3.92 | −34.67 | −40.42 |
| Trigger · SMA20 · 2MTR · 24h | 535 | −37.23 | −58.16 | −3.51 | −29.60 | −44.67 |

| Variant (causal portfolio) | Closed | Mean | Median | Before costs | Jul–Dec 2025 | Jan–Jun 2026 |
|---|---|---|---|---|---|---|
| Confirm · SMA20 · 2MTR · 6h | 265 | −34.27 | −29.60 | −0.69 | −30.83 | −37.96 |
| Confirm · R1 · 2MTR · 6h · H1 trend | 130 | −34.43 | −34.83 | 1.40 | −29.88 | −39.42 |
| Confirm · upper band · 2MTR · 6h · H1 trend | 135 | −35.39 | −37.89 | 2.17 | −34.23 | −36.75 |
| Confirm · R1 · 2MTR · 6h | 323 | −35.58 | −31.81 | −0.21 | −31.95 | −39.06 |
| Confirm · SMA20 · 2MTR · 6h · H1 trend | 105 | −35.74 | −37.86 | −1.47 | −32.17 | −40.49 |
| Trigger · SMA20 · 2MTR · 6h (control) | 313 | −36.30 | −31.17 | −0.28 | −33.05 | −39.62 |
| Trigger · SMA20 · 2MTR · 24h | 272 | −39.61 | −47.40 | −3.41 | −31.97 | −47.03 |

Before costs, with the standard error of the mean (sample standard deviation
over the square root of the count), computed from `rows.json`:

| Variant | Sample | Closed | Before costs | Costs per trade | Net |
|---|---|---|---|---|---|
| Trigger · SMA20 · 2MTR · 6h | Causal per pair | 578 | −3.92 ± 2.07 | 33.70 | −37.61 |
| Trigger · SMA20 · 2MTR · 6h | All candidates | 654 | −4.40 ± 1.96 | 33.46 | −37.86 |
| Confirm · SMA20 · 2MTR · 6h | Causal per pair | 499 | −0.14 ± 2.05 | 31.74 | −31.88 |
| Confirm · SMA20 · 2MTR · 6h | All candidates | 543 | −0.56 ± 1.97 | 31.74 | −32.30 |
| Confirm · R2 · 2MTR · 6h | Causal per pair | 702 | 2.22 ± 1.91 | 35.58 | −33.37 |
| Confirm · R2 · 2MTR · 6h | All candidates | 775 | 1.51 ± 1.80 | 35.67 | −34.16 |
| Trigger · SMA20 · 1MTR · 6h | Causal per pair | 628 | −3.23 ± 2.42 | 49.61 | −52.84 |
| Trigger · SMA20 · 1MTR · 6h | All candidates | 654 | −4.13 ± 2.36 | 49.51 | −53.64 |

Costs per trade are the mean before costs minus the mean net.

## Interpretation

These statements describe the recorded results on this data.

- **Costs set the payoff, not the level formulas.** At the median, the current
  target and stop are about equally far from entry. After fees and slippage the
  median reward is about a third of the risk, and for 139 of 793 candidates the
  reward at the target does not cover the costs. With the 2MTR stop, costs were
  31.62 to 39.74 USD per trade against a risk budget of 100 USD. With the 1MTR
  stop they were 41.62 to 55.08: a nearer stop buys a larger position for the
  same risk, and costs scale with the position.
- **No edge before costs.** In the causal and all-candidate samples, no
  variant's mean before costs exceeded 3.94 USD per trade, and the tabulated
  ones lie within about two standard errors of zero. Breaking even needs the
  costs back: 31.62 to 39.74 USD per trade with the 2MTR stop. Net means ranged
  from −62.38 to −31.88. The best variant improved on the control by 5.73 USD
  in the causal per-pair sample and by 2.03 in the causal portfolio, and none
  reached zero.
- **The planning rule flatters long holds.** Under the non-overlapping rule,
  142 of 144 variants have a positive mean before costs and 39 after costs,
  31 of them with 72-bar and 8 with 24-bar holds. In the other three samples
  none is positive after costs. The rule keeps a candidate only when no later
  crossing of the same pair falls inside its window, and a later crossing means
  the price broke down again. The longer the window, the more such candidates
  it drops: for the control, the study's V1 mean of −10.49 is −37.23 in the
  causal per-pair sample.
- **The study's conclusion stands.** The bias favoured the baseline, and the
  study's four variants have negative means in all four samples.
- **Planned trial windows share the rule.** Trials plan 6-bar windows with it.
  On this data the 6-bar control is −30.52 under the rule and −37.61 in the
  causal per-pair sample. Research and baseline are compared on the same
  windows, so that comparison is like for like, but baseline totals describe the
  planned windows, not the strategy.

This record changes no decision. ADR-019's stop clause and ADR-021 are
unchanged.

## Limitations

- Exploratory: 144 variants on one dataset. The best results are optimistic,
  and no variant was selected or tested on other data.
- One twelve-month period, three pairs, hourly bars only, and one set of fees,
  slippage and risk fraction.
- Trades are not independent. Crossings cluster in time, and in the
  all-candidate sample trades overlap, so the standard errors understate the
  uncertainty.
- The confirmation rule, the R1 and R2 targets and the D1 filter are one
  untuned choice each.
- Every window starts from 10,000 USD, so sizing does not compound as in a
  single long replay.
- The script is outside the repository; this record, `results.json`, `rows.json`
  and the study's dataset manifests are its evidence.
