# V2 baseline strategy study: four pre-registered variants on development data

## Summary

[ADR-019](../decisions/ADR-019-pre-registered-baseline-study.md) pre-registered a
scanner-only study of four baseline variants: the control (V0), a 24-hour holding
limit (V1), an SMA20 ≥ SMA50 trigger filter (V2), and both (V3). The study ran
once on Coinbase hourly candles for BTC-USD, ETH-USD and SOL-USD, planning from
2025-07-01 to 2026-07-01. No model was called.

All four variants closed at least 100 trades, so all four were eligible. Every
variant had a negative mean realized net P&L per closed trade. The best was V1
at −10.49 USD; V0, V2 and V3 had −30.52, −32.88 and −17.63 USD. The recorded
selection is `STOP: no eligible variant has a positive mean net P&L per trade`.
Under ADR-019's stop rule, the study concludes that this scanner does not produce
candidates that pay for themselves at these costs, and no further research trial
runs on it until a new decision changes the scanner.

In every variant a stop exit lost close to the full 1% risk (about 100 USD) and a
target exit gained 26 to 37 USD on average. A longer holding limit raised the
share of target exits and the mean. The trend filter lowered the mean in both
pairings.

## What was run

### Pre-registration

ADR-019 fixed the data, economics, planning, variants, measures, selection rule
and stop rule. It was committed as `9b634c6` at 2026-11-01T16:22:03Z, and the
local `origin/main` reflog records its push at 2026-11-01T16:22:05Z. The three
dataset manifests and acquisition records were created at
2026-11-02T16:05:01.483640Z, after the commit.

ADR-019's variants table, verbatim:

| Variant | Holding limit (`max_holding_bars`) | Trigger filter |
| --- | --- | --- |
| V0 (control) | 6 | none |
| V1 | 24 | none |
| V2 | 6 | SMA20 ≥ SMA50 at T |
| V3 | 24 | SMA20 ≥ SMA50 at T |

ADR-019's selection rule, verbatim:

> **Selection.** A variant is eligible when it has at least 100 closed trades. The
> chosen variant is the eligible one with the highest mean realized net P&L per
> closed trade; a tie goes to the lower-numbered variant. If no eligible variant
> has a positive mean, the study concludes that this scanner does not produce
> candidates that pay for themselves at these costs. No further research trial
> then runs on it until a new decision changes the scanner.

The economics were the V1 release economics, as ADR-019 fixes them: 10000 USD
initial cash, risk fraction 0.01, entry and exit fee rates 0.004, adverse
slippage 0.0005, price increment 0.01 and quantity increment 0.00000001. Each
variant planned its own windows with `plan_windows`, purpose `PLUMBING`, a limit
larger than the number of candidates, and ADR-018's authorization rule, from
2025-07-01T00:00:00Z.

### Run

- Commit `9b634c6b3f16b1886089665d596d04a9ae9bc5f1`, source digest
  `sha256:4199ee6f81fa7381e9bbde288518f00bb3014cb37b629fc79955ca93e03d1f3a`, both
  as recorded in `results.json`. `9b634c6` adds only ADR-019 and its index entry
  to `ad97da2`, so its source digest is `ad97da2`'s.
- Script `~/crypteo-eval/baseline_study.py`, kept outside the repository. Its
  `study` command refuses to run when `results.json` exists.
- The study ran once. `ANTHROPIC_API_KEY` was unset; the script exits if it is
  set, and no step calls a model.
- This record was written from the two input files below, read only. The
  script's `report` command was run to print the recorded results; `acquire` and
  `study` were not run again.

SHA-256 of the inputs, before and after writing this record (identical):

| File | SHA-256 |
|---|---|
| `~/crypteo-eval/datasets-study.json` | `ef54523f0d1b630e1c33585b6e57ed1df0f045e468c239f80eac2439869b53b9` |
| `~/crypteo-eval/study/results.json` | `ab088bdb1eb2a8b9feee32e62b53e92b6c4ec3df1c1cca69d76b071e1ce84b1a` |

## Data

Coinbase Exchange hourly candles over [2025-06-01T00:00:00Z,
2026-07-01T00:00:00Z), 9,480 hourly slots per pair. Planning started at
2025-07-01T00:00:00Z, so every candidate had at least 30 days of earlier
history.

| Pair | Candles | Gaps | Dataset version | Acquisition ID |
|---|---|---|---|---|
| BTC-USD | 9,470 of 9,480 | 10 | `sha256:72e73931ec153d64eb0334ae35b470db725155cc192970638097220a053c60e3` | `sha256:6c1b00fecafa2b53ff58d2585de1470c619f5845e36ff675419350f3272d6046` |
| ETH-USD | 9,470 of 9,480 | 10 | `sha256:412d4e3f2a484c22158243bcd646d22db90e9a1930108a0d0f4038b1443bfda1` | `sha256:2de3cd5793625ecba342a8630015d9f4f45abf1d6fd2faad0805fdcd96711202` |
| SOL-USD | 9,470 of 9,480 | 10 | `sha256:7e47fdfedb028e20f72bd481e2d85845484a01a90b7f30be1b07cd5c0f003e77` | `sha256:00239cc45bd4b4f81414ea6e521239a20e4a2c29ae9ca97538e630772b14832f` |

The manifests' `missing_open_times` are the same ten hours for all three pairs,
in two runs of five:

- 2025-10-25T16:00:00Z, 17:00, 18:00, 19:00 and 20:00;
- 2026-05-08T02:00:00Z, 03:00, 04:00, 05:00 and 06:00.

ADR-019 treats a window whose position stays unresolved as counted and left out
of the measures. One window was unresolved: V1's ETH-USD window with signal hour
2026-05-07T15:00:00Z, status `DATA_GAP`. The 24 hours after its signal hour
include the 2026-05-08 gap. It is counted in V1's 322 windows and left out of
V1's 321 closed trades and their P&L. The SMA filter removed it from V3. No other
window in any variant was unresolved.

## Plans

Each holding limit gives its own plan. The filtered variants share their
unfiltered partner's plan and keep a subset of its selected windows.

| Plan | Variants | Candidates | Selected | `BASELINE_NOT_AUTHORIZED` | `OVERLAPPING_CANDIDATE` | `WINDOW_BEYOND_DATA` |
|---|---|---|---|---|---|---|
| `sha256:d724f9c39fb27643776ed12230fc545a97b23919dac3ba3d08073409ed27f7fe` | V0, V2 | 793 | 534 | 100 | 159 | 0 |
| `sha256:d9116c1e0524284072e521233b44c162494db2992b1ec0f95e2eb75bb334253e` | V1, V3 | 793 | 322 | 55 | 413 | 3 |

The trend filter, applied to each plan's selected windows:

| Variant | Selected in plan | Failed SMA20 ≥ SMA50 | No trend result | Windows kept |
|---|---|---|---|---|
| V2 | 534 | 315 | 1 | 218 |
| V3 | 322 | 197 | 0 | 125 |

So the filter removed 316 of V0's 534 windows (315 failed the condition and 1
had no trend result, which ADR-019 drops and counts) and 197 of V1's 322.
`results.json` does not identify the window without a trend result. Every window
kept by V2 or V3 has the same pair, signal hour, status, exit reason and net P&L
as in V0 or V1 respectively.

## Results

Measures per variant as recorded in `results.json`. Wins and losses count closed
trades with positive and negative net P&L; no trade had zero net P&L. P&L is in
USD.

| Variant | Hold | Filter | Windows | Closed | Wins / losses | Unresolved | Mean net P&L | Median net P&L | Total net P&L | Exits (STOP / TARGET / TIME) |
|---|---|---|---|---|---|---|---|---|---|---|
| V0 | 6 | no | 534 | 534 | 180 / 354 | 0 | −30.522483073728229962546816479400749063670411985019 | −24.9941690572494 | −16299.0059613708748 | 135 / 102 / 297 |
| V1 | 24 | no | 322 | 321 | 199 / 122 | 1 (`DATA_GAP`) | −10.494800417991066666666666666666666666666666666667 | 12.4781289034872 | −3368.8309341751324 | 98 / 168 / 55 |
| V2 | 6 | yes | 218 | 218 | 64 / 154 | 0 | −32.875663019628346788990825688073394495412844036697 | −28.0054553309240 | −7166.8945382789796 | 55 / 44 / 119 |
| V3 | 24 | yes | 125 | 125 | 76 / 49 | 0 | −17.6346106219784128 | 7.8925566504960 | −2204.3263277473016 | 41 / 64 / 20 |

These were recomputed from the per-window list in `results.json` (sum, mean at
50 significant digits, and median of each variant's closed net P&L), and every
value matches the recorded measures. Win rates are 33.7% (V0), 62.0% (V1),
29.4% (V2) and 60.8% (V3).

### By exit reason

Recomputed from the per-window list. Mean is the total divided by the count, at
50 significant digits.

| Variant | Exit | Trades | Total net P&L | Mean net P&L |
|---|---|---|---|---|
| V0 | STOP | 135 | −13484.2552126081152 | −99.883371945245297777777777777777777777777777777778 |
| V0 | TARGET | 102 | 2723.7172941240188 | 26.703110726706066666666666666666666666666666666667 |
| V0 | TIME | 297 | −5538.4680428867784 | −18.648040548440331313131313131313131313131313131313 |
| V1 | STOP | 98 | −9787.3773306386344 | −99.871197251414636734693877551020408163265306122449 |
| V1 | TARGET | 168 | 6294.1121345470768 | 37.464953181827838095238095238095238095238095238095 |
| V1 | TIME | 55 | 124.4342619164252 | 2.2624411257531854545454545454545454545454545454545 |
| V2 | STOP | 55 | −5499.3682152550688 | −99.988513004637614545454545454545454545454545454545 |
| V2 | TARGET | 44 | 1152.6466167147120 | 26.196514016243454545454545454545454545454545454545 |
| V2 | TIME | 119 | −2820.1729397386228 | −23.698932266711115966386554621848739495798319327731 |
| V3 | STOP | 41 | −4098.9191587884648 | −99.973638019230848780487804878048780487804878048780 |
| V3 | TARGET | 64 | 1931.6428776283040 | 30.18191996294225 |
| V3 | TIME | 20 | −37.0500465871408 | −1.85250232935704 |

Rounded: a stop exit averaged −99.87 to −99.99 USD in every variant, and single
stop exits ranged from −101.05 to −90.82 USD. A target exit averaged +26.70 (V0),
+37.46 (V1), +26.20 (V2) and +30.18 (V3). A time exit averaged −18.65, +2.26,
−23.70 and −1.85.

## Selection

ADR-019's rule, applied as written:

1. Eligibility (at least 100 closed trades): V0 534, V1 321, V2 218, V3 125. All
   four are eligible.
2. Highest mean among the eligible: V1 at
   −10.494800417991066666666666666666666666666666666667, ahead of V3, V0 and V2.
   There is no tie.
3. V1's mean is not positive, so no eligible variant has a positive mean.

Outcome: **STOP**. `results.json` records the selection as
`STOP: no eligible variant has a positive mean net P&L per trade`. The study
concludes that this scanner does not produce candidates that pay for themselves
at these costs. ADR-019's stop clause applies: "No further research trial then
runs on it until a new decision changes the scanner." No variant is chosen, so
ADR-019's adoption step for V1, V2 or V3 does not arise.

## Interpretation

These statements describe the recorded results.

- **Stops outweigh targets.** A stop exit lost about the full 1% risk
  (around 100 USD), while a target exit paid 26 to 37 USD on average. In V1,
  62% of closed trades were wins, but 168 targets (+6294.11) and 55 time exits
  (+124.43) did not offset 98 stops (−9787.38), so its mean was −10.49.
- **The 24-hour limit raised target hits and lowered losses, but no variant had a
  positive mean.** Target exits rose from 102 of 534 trades (19.1%) in V0 to 168
  of 321 (52.3%) in V1, and from 44 of 218 (20.2%) in V2 to 64 of 125 (51.2%) in
  V3. Losses fell from 354 (66.3%) to 122 (38.0%), and from 154 (70.6%) to 49
  (39.2%). The mean rose from −30.52 to −10.49, and from −32.88 to −17.63. The
  longer limit also excluded more overlapping candidates (413 against 159), so
  the 24-hour variants have fewer trades.
- **The trend filter lowered the mean in both pairings.** V2's mean (−32.88) is
  below V0's (−30.52), and V3's (−17.63) is below V1's (−10.49). The filter
  lowered the win rate in both pairings as well (29.4% against 33.7%, 60.8%
  against 62.0%).
- **Costs are inside the measures.** The study records realized net P&L only,
  after the fixed fees and slippage, so it cannot show the results before costs.
  ADR-019 kept the cost assumption out of the study; any future change to it
  needs its own justification, not this study's results.
- **Development data only.** The data precedes
  [ADR-013](../decisions/ADR-013-v2-evaluation-scope.md)'s evaluation period
  (from 2026-07-01T00:00:00Z) and lies in its contaminated period. It was used
  only to develop the deterministic baseline. The study called no model and
  makes no claim about research selection.

## Limitations

- One twelve-month planning period, 2025-07-01 to 2026-07-01.
- Three pairs: BTC-USD, ETH-USD and SOL-USD.
- Fixed economics: one set of fees, slippage, risk fraction and trade terms.
- Only the four pre-registered variants were run. No other holding limit,
  filter threshold or entry rule was tested.
- Each plan excludes overlapping candidates, so the trades are not every
  lower-band crossing; the excluded set differs between the 6-hour and 24-hour
  plans.
- The results describe this scanner and these trade terms, not trading in
  general.
- The study script is outside the repository; this record, `results.json` and
  the dataset manifests are its evidence.
