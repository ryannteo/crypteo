# ADR-015: Lower-band crossing base rates use pre-registered conditioning

## Status

Accepted

## Context

`lower_band_crossings` reports what followed this pair's earlier scanner-rule
crossings in the 720 candles ending at T. For each crossing whose six outcome
candles closed by T, it reports whether price reached the trigger's SMA20, the
6-hour close return, and the adverse and favorable excursions
([specification](../v1-specification.md#fixed-capabilities-windows-and-formulas)).

In [trial 6](../evaluations/v2-july-2026-trial-6.md#why-research-declined) every
window's assessment or critic read this evidence as weak, and two gaps recurred:

- a base rate conditioned on breakdown-type triggers or on the current regime,
  cited in 10 of 10 windows;
- an unconditional 6-bar baseline over the same lookback, cited in 3 of 10.

Without a baseline, the model cannot tell whether the crossings' mean return
differs from what any hour in the lookback produced. Without conditioning, it
cannot tell whether crossings like the current one behaved differently from
crossings in general.

Deterministic computation over the candles the tool already reads can close
both gaps. The risk is in choosing conditions and thresholds: chosen after
seeing results, they would fit the evidence to known outcomes. All 22 July 2026
windows from 2026-07-05 to 2026-07-17 have been researched at least once, and
the first 10 of them twice. Trial 6 describes those ten windows' trigger bars as
wide-range bearish candles at 1.9 to 3.2 times mean true range. Every parameter
is therefore fixed here, before any code exists and before any of these
statistics is computed on market data.

Samples are few. Trial-6 windows had 14 to 17 sampled crossings, so a two-way
split leaves groups of a few samples each.

## Decision

**Placement.** The new statistics extend the `lower_band_crossings` payload. No
capability is added: there are still ten tools, nine offered in a single-pair
window, under the same budgets. The implementing slice moves the tool version to
`bounded_market_tools_v6` and the evidence policy to `market_research_v4`.

**Fixed parameters.** The lookback stays 720 candles and the horizon 6 candles.
Window candles are indexed `0..719` in time order, and candle 719 ends at T. The
per-sample outcome measures are the existing ones: reached SMA20, close return,
adverse excursion and favorable excursion.

**Unconditional baseline.** The baseline samples every start index `i` with
`20 <= i <= 713`, crossing or not: 694 overlapping samples, at exactly the
indices where a crossing can be sampled. It reports the sample count, the count
and fraction with a positive close return, the mean and median close return,
and the mean adverse and mean favorable excursion. Reached SMA20 is excluded:
from a close at or above its SMA20 the measure is often met trivially, so its
unconditional rate does not compare with the crossing rate.

**Features.** Two binary features of a window candle `j`, computed from
in-window candles only:

- *Wide range*: `H(j) - L(j)` is at least the mean true range of candles
  `j-20..j-1`, seeded by `C(j-21)`, as in the candle-structure baseline. The
  comparison is direct, so it involves no division and has no undefined case.
- *Downtrend*: `SMA20(j) < SMA50(j)`, where `SMA50(j)` is the mean of
  `C(j-49..j)`. This is the sign of the trend tool's `sma20_vs_sma50`.

Both features are also reported for the candle ending at T (`j = 719`), so the
model can see which group the current candle belongs to.

**Conditioned groups.** A sampled crossing is classified when `i >= 49`, the
first index at which both features are defined; the payload reports how many
sampled crossings are unclassified. For each feature separately, and for each
of its two values, the payload reports over the classified sampled crossings
with that value: the sample count, the reached-SMA20 count and fraction, the
positive-close count and fraction, the mean close return, and the mean adverse
and mean favorable excursion. Aggregates are null exactly when a group is
empty, as for the existing statistics. Features are not crossed with each
other, no group is suppressed for its size, and counts are always shown.

**Nothing else is a parameter.** There are no other features, thresholds,
lookbacks, horizons, minimum sample sizes, de-clustering, overlap corrections
or significance measures. The thresholds are reference points the tools already
use (a range equal to the candle-structure baseline mean true range, and the
sign of SMA20 minus SMA50), not values chosen to separate known windows.
Changing any parameter needs a new ADR, and a result on an evaluation-period
window is never a reason for the change.

**Integrity before the next trial.**

- The implementation is tested on synthetic candles only.
- Until the next trial's windows are planned and its run cap declared, nobody
  computes these statistics on Coinbase data from 2026-07-01 onward. A
  `PLUMBING` rehearsal on contaminated data before 2026-07-01 is permitted.
- The next trial plans only windows not yet inspected, from 2026-07-18, under
  the new versions.

**Reporting.** The next trial record reports, for each window, whether
`lower_band_crossings` was requested; the classified and unclassified counts;
which group the candle ending at T falls into for each feature, with that
group's sample count; and whether the investigation, critique or assessment
cited the baseline or a conditioned group. These are descriptive observations.
Results under this policy are not pooled with trials 5 and 6.

## Rationale

The crossing evidence already defines the lookback, the horizon and the
outcome measures. A baseline and conditioned groups over the same window answer
the critics' questions without a new data dependency or a second definition of
an outcome. Extending the existing tool keeps related evidence in one item and
adds no tool to compete for the eight attempts.

Keeping the 720-candle lookback leaves the existing statistics unchanged and
needs no older data. A longer lookback would give larger groups, but it changes
the existing tool's meaning and the history every window needs, so it needs a
decision of its own.

Two features, each split in two, are the fewest that answer both gaps: one
describes the trigger candle and one the trend regime. Crossing them would leave
four groups of one or two samples. Reusing reference points that existing tools
already report avoids inventing a cut-off. Reporting both groups of each
feature, rather than only the group matching the current candle, keeps the
payload's shape the same whatever the candle ending at T looks like.

The groups are small, the samples overlap, and crossings cluster in time. The
tool reports counts so the model and the critic can weigh this. The statistics
are observational history, not a predictive base rate.

## Consequences

- The implementing slice changes the `lower_band_crossings` payload, its
  contracts and its tool description. Its tests use synthetic candles only and
  cover each formula, the classification boundary at `i = 49`, empty groups,
  planted future candles and tampering.
- The tool and evidence versions change, so stored runs fail to load at that
  commit, as with every earlier version change. The release-smoke research
  goldens move with the tool and evidence identities; the scanner-only golden
  does not.
- Budgets, prompts, stage schemas, the model view and replay are unchanged.
- The next model-backed trial runs under the new versions on uninspected
  windows and declares its run cap and estimated cost in its TASK.md, as
  ADR-013 requires.
- Nothing here supports a claim that conditioned rates predict outcomes or that
  research improves selection.
- A larger pre-registered sample, a longer lookback or more features needs a
  new ADR.
