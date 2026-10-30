# V2 evaluation trial 7: conditioned crossing base rates on 10 new July 2026 windows

## Summary

Trial 7 researched 10 July 2026 windows that no earlier trial had inspected. It is
the first trial under tools `bounded_market_tools_v6` and evidence policy
`market_research_v4`, and the first with
[ADR-015](../decisions/ADR-015-pre-registered-crossing-base-rates.md)'s
unconditional baseline and conditioned groups in the lower-band crossing evidence.
Each window was replayed once with the scanner-only baseline and once with fresh
Claude research. Claude declined every candidate (6 `NO_TRADE`, 4 `ABSTAIN`,
0 `TRADE`). The baseline's risk check rejected 7 of the 10 proposals; the other 3,
all at the same hour, hit their stops for a net loss of 301.24 USD. A separate
process with no API key reproduced every research decision and every metric from
the stored artifacts.

Every investigation, critique and assessment cited the new baseline and at least
one conditioned group. The groups were small, the wide-range feature separated
almost nothing, and the stored text shows two recurring misreadings of the
groups. Research never chose `TRADE`, so selection quality is unmeasured. This
trial is descriptive under
[ADR-013](../decisions/ADR-013-v2-evaluation-scope.md), not a performance result,
and its results are not pooled with trials 5 and 6.

## What was run

Code and environment:

- Commit `a2b5eed6490216c8cb2369c84ad90797c33a372b`, source digest
  `sha256:d2e97eb8cbec689d6dcb131808af8c595c4c2ab169ce760144f643e7479ec5a0`.
- Model `claude-opus-5-5` (provider `anthropic`) at effort `high`.
- Workflow `bounded_research_v2`, unchanged from trials 5 and 6: 8 tool attempts,
  9 investigation calls, at most 14 model calls and 2 output re-asks per run.
- Prompts unchanged from trial 6 and from v2.0.0: `investigate_prompt_v4`,
  `draft_thesis_prompt_v2`, `critique_prompt_v2`, `final_assessment_prompt_v2`.
- Tools `bounded_market_tools_v6` with evidence policy `market_research_v4` and
  model view `compact_research_view_v2`. As in trial 6, these single-pair windows
  offered 9 tools for 8 attempts.

Changes from trial 6's policy, in order:

- `b16693f`: thesis, critique and final-assessment requests see the tool
  definitions, and the `lower_band_crossings` description says a crossing on the
  candle ending at T is counted only in `pending_count`. Tools
  `bounded_market_tools_v4`, model view `compact_research_view_v2`.
- `2bd58c3`: payloads and tool descriptions state each evidence window's span.
  Tools `bounded_market_tools_v5`, evidence `market_research_v3`.
- `a2b5eed`: ADR-015's unconditional baseline and conditioned groups added to the
  `lower_band_crossings` payload. Tools `bounded_market_tools_v6`, evidence
  `market_research_v4`.

The baseline, features and groups are those ADR-015 fixed before the
implementation existed. This trial did not change them.

Data and economics are the same as trials 5 and 6: the same three Coinbase datasets
over [2026-05-15T00:00:00Z, 2026-09-01T00:00:00Z) and the V1 release economics (see
[What was run](v2-july-2026.md#what-was-run)). A data check before planning found
2,616 candles per pair with no gaps.

## How the windows were chosen

`plan_windows` with purpose `EVALUATION`, `planning_start` 2026-07-18T00:00:00Z and
limit 10, otherwise as trial 5. The start falls after every window trials 5 and 6
researched (2026-07-05 to 2026-07-17). The planner considered 11 candidates from
2026-07-18 14:00 to 2026-07-24 12:00, selected 10 and excluded BTC-USD at
2026-07-23 08:00 as `OVERLAPPING_CANDIDATE`. Plan ID
`sha256:e1c5b4c008c409f0dc2aa669d6b7606878471e18cf6b6a6f61d30bd3f7835d78`. The
run cap was 12 research runs, and the estimated cost was about 6.70 USD for
10 windows and at most about 8.04 USD at the cap.

No earlier trial had inspected any of the 10 windows. As ADR-015 requires,
planning read scanner signals only, and the rehearsal used June data.

Three signal hours fired on more than one pair: 2026-07-20 06:00 (BTC, ETH),
2026-07-22 06:00 (BTC, SOL) and 2026-07-23 13:00 (BTC, ETH, SOL). The 10 windows
therefore come from six signal hours, with simultaneous signals counted once.

## Results

| Signal (UTC) | Pair | Research | Confidence | Baseline outcome | Baseline net P&L (USD) |
|---|---|---|---|---|---|
| 2026-07-18 14:00 | SOL-USD | NO_TRADE | 55 | Risk rejected | – |
| 2026-07-20 06:00 | BTC-USD | NO_TRADE | 58 | Risk rejected | – |
| 2026-07-20 06:00 | ETH-USD | NO_TRADE | 55 | Risk rejected | – |
| 2026-07-22 06:00 | BTC-USD | ABSTAIN | – | Risk rejected | – |
| 2026-07-22 06:00 | SOL-USD | NO_TRADE | 55 | Risk rejected | – |
| 2026-07-22 13:00 | BTC-USD | ABSTAIN | – | Risk rejected | – |
| 2026-07-23 13:00 | BTC-USD | NO_TRADE | 60 | STOP | −100.00 |
| 2026-07-23 13:00 | ETH-USD | ABSTAIN | – | STOP | −100.62 |
| 2026-07-23 13:00 | SOL-USD | ABSTAIN | – | STOP | −100.61 |
| 2026-07-24 12:00 | SOL-USD | NO_TRADE | 58 | Risk rejected | – |

Comparison metrics (`evaluation_id`
`sha256:1fe1ec3162d15bd1097955346b9aca9a1b4626c6a217f40827267af3bbbc3685`):

| Metric | Value |
|---|---|
| Windows | 10 |
| Research decisions | 0 TRADE, 6 NO_TRADE, 4 ABSTAIN |
| Baseline closed trades | 3 (3 STOP); 7 risk rejections |
| Baseline wins / losses | 0 / 3 (win rate 0) |
| Baseline realized net P&L | −301.2361499565248 USD |
| Research realized net P&L | 0 USD |
| Declined trades' net P&L | −301.2361499565248 USD |
| Unresolved windows | 0 |

## Interpretation

- Research declined all 10 candidates. `NO_TRADE` confidence ranged from 55 to 60,
  the same range as trial 6. With no `TRADE` decisions the `taken` group is empty,
  and a constant "never trade" rule produces the same numbers. Selection quality
  is unmeasured.
- The baseline's risk check rejected 7 of 10 proposals, against 1 of 10 in trial 6
  (a descriptive comparison of different windows). Every rejection's `reason` in
  the baseline replay's `RISK_REJECTION` record is `NON_POSITIVE_TARGET_REWARD`:
  the estimated net reward per unit at the target, after entry and exit fees and
  slippage, was not positive
  ([specification](../v1-specification.md#simulation-and-risk)). Those 7 windows
  had no baseline trade for research to take or decline. Research could change a financial outcome in
  only 3 windows, and all 3 are the same 2026-07-23 13:00 move.
- The 4 `ABSTAIN` decisions are model assessments of completed runs (status
  `COMPLETED`, origin `MODEL`). Each gives `CONFLICTING_EVIDENCE` and
  `INSUFFICIENT_EVIDENCE` as its reasons. An abstention carries no required
  confidence, and none of the four gives one.

## Why research declined

The same stored fields as trials 5 and 6 were read for every window: the
assessment rationale and key uncertainties, the investigation's remaining gaps,
and the critique. The counts below come from reading that text.

Every run made 8 successful tool calls, starting with `lower_band_crossings`, and
never requested `bollinger_position`. Every investigation listed the missing band
width and percent_b as a gap, and every critic listed the tool as missing
evidence. Three abstentions (BTC-USD 07-22 13:00, ETH-USD 07-23 13:00, SOL-USD
07-23 13:00) named the missing band-depth evidence among the reasons they could
not reach a conclusion.

The assessments weighed the crossing evidence against the trigger candle and
short-term structure. In the six `NO_TRADE` windows the assessment found the
crossing edge over the baseline small, fragile, or carried by the group that did
not match the candle at T, while trend, momentum and candle anatomy leaned
against a long. The four `ABSTAIN` assessments found the same weaknesses but did
not consider them grounds for an active rejection.

| Gap cited by the investigation, critic or assessment | Windows |
|---|---|
| `bollinger_position` not requested, so band width and breach depth unknown | 10 of 10 |
| No benchmark, market-wide or cross-venue context (single-pair windows bind no benchmark) | 10 of 10 |
| Derivatives, order book, funding or news evidence (outside V1 scope) | 10 of 10 |
| Small crossing samples that may cluster or overlap | 10 of 10 |
| Lookback is one ~30-day regime and may not be representative | 10 of 10 |
| Wide-range feature does not discriminate: no or almost no not-wide crossings | 9 of 10 |
| Current crossing pending; no post-trigger confirmation | 9 of 10 |
| Per-crossing detail, group medians, dispersion or significance measures | 7 of 10 |
| Conditioning on features ADR-015 does not report (volatility regime, daily trend, candle anatomy and volume, 24h-low break, repeat crossings) | 5 of 10 |
| A joint wide-range and trend group | 5 of 10 |
| Comparability of historical crossings with current volatility or band width | 5 of 10 |
| Longer or out-of-sample crossing history | 5 of 10 |
| Hour-of-day baseline for trigger volume | 3 of 10 |
| Which SMA20 "reached SMA20" refers to | 2 of 10 |
| Transaction costs against small mean returns | 1 of 10 |

Trial 6's base-rate gaps, a conditioned base rate (10 of 10) and an unconditional
baseline (3 of 10), were replaced by questions about the new groups' size,
discrimination and overlap. No stage cited undocumented evidence windows, a trial-6
gap in 8 of 10. No stage questioned whether the pending crossing was the current
trigger, a trial-6 gap in 7 of 10. The stages that discussed pending crossings
described them as the tool description states. Transaction costs fell from 8 of
10 in trial 6 to 1 of 10. These are descriptive comparisons; the windows differ
between the trials.

The "joint group" row counts windows whose investigation, critic or assessment
said a joint wide-range and trend group was missing. In 4 of those 5 windows the
trend group already was the joint group (see
[ADR-015 reporting](#adr-015-reporting)). Of the five, only ETH-USD 07-23 had
not-wide crossings among its classified samples.

## ADR-015 reporting

Per window: whether `lower_band_crossings` was requested; sampled, classified and
unclassified crossings; and the group the candle ending at T falls in for each
feature, with that group's sample count.

| Signal (UTC) | Pair | Requested | Sampled | Classified | Unclassified | Range group at T (n) | Trend group at T (n) |
|---|---|---|---|---|---|---|---|
| 2026-07-18 14:00 | SOL-USD | yes | 18 | 18 | 0 | wide range (18) | downtrend (9) |
| 2026-07-20 06:00 | BTC-USD | yes | 15 | 14 | 1 | wide range (14) | not downtrend (6) |
| 2026-07-20 06:00 | ETH-USD | yes | 16 | 15 | 1 | wide range (13) | not downtrend (8) |
| 2026-07-22 06:00 | BTC-USD | yes | 15 | 14 | 1 | wide range (14) | not downtrend (6) |
| 2026-07-22 06:00 | SOL-USD | yes | 17 | 16 | 1 | wide range (16) | not downtrend (8) |
| 2026-07-22 13:00 | BTC-USD | yes | 15 | 14 | 1 | wide range (14) | not downtrend (7) |
| 2026-07-23 13:00 | BTC-USD | yes | 16 | 14 | 2 | wide range (14) | downtrend (6) |
| 2026-07-23 13:00 | ETH-USD | yes | 15 | 13 | 2 | wide range (11) | downtrend (5) |
| 2026-07-23 13:00 | SOL-USD | yes | 17 | 15 | 2 | wide range (15) | downtrend (6) |
| 2026-07-24 12:00 | SOL-USD | yes | 17 | 16 | 1 | wide range (16) | downtrend (7) |

Citations. In all 10 windows the investigation, the critique and the assessment
each cited both the unconditional baseline and at least one conditioned group:
30 of 30 stage outputs, confirmed by reading. The run script's pattern matches
flagged most of these and missed 7 citations in 5 windows: group citations in
the SOL-USD 07-18 and BTC-USD 07-20 assessments ("downtrend split", "not-downtrend
subgroup"), and baseline citations in the BTC-USD 07-22 13:00 assessment ("a 0.54
baseline"), the ETH-USD 07-23 critique ("vs 0.55") and assessment ("a 0.55
baseline"), and the SOL-USD 07-24 assessment ("+0.05% baseline median"). That
assessment's group citations ("wide-range subgroup", "downtrend subgroup") were
also missed.

Groups:

- The candle ending at T was wide-range in 10 of 10 windows. The not-wide group had
  samples in only 2 windows, ETH-USD 07-20 and ETH-USD 07-23, with 2 samples each.
  In the other 8, every classified crossing was wide-range, so the wide-range group
  was the whole classified sample.
- The downtrend feature split 5 and 5. The candle at T was in a downtrend for
  SOL-USD 07-18, the three 07-23 13:00 windows and SOL-USD 07-24, and not in a
  downtrend for the two 07-20 windows, the two 07-22 06:00 windows and BTC-USD
  07-22 13:00.

Two reading errors recur in the stored text:

- **A later SMA crossover treated as moving the setup into another group.**
  Classification is fixed at the crossing candle, so a later SMA20/SMA50 crossover
  cannot reclassify the current crossing. Every thesis in the five not-downtrend
  windows made this reading, as an invalidation condition:
  - BTC-USD 07-20: the critique repeated it ("whether the current not-downtrend
    classification will persist over the 6-bar horizon"), and so did the assessment
    ("could flip within the 6-bar horizon"). Not corrected.
  - ETH-USD 07-20: the critique corrected it ("Classification is fixed by the
    crossing candle's state"), and the assessment accepted the correction.
  - BTC-USD 07-22 06:00: the critique repeated it ("the bull case depends on the
    not-downtrend classification holding"). The assessment kept regime persistence
    as an uncertainty without naming a group. Not corrected.
  - SOL-USD 07-22 06:00: the critique challenged the condition only because the
    downtrend group had done better, and the assessment set it aside on that
    ground. The classification point was not corrected.
  - BTC-USD 07-22 13:00: the critique repeated it ("reclassifying the setup into
    the weaker downtrend group"), and so did the assessment. Not corrected.
- **No joint wide-range and downtrend group.** When every classified crossing is
  wide-range, the downtrend group is the joint group. This statement appeared in
  three such windows:
  - BTC-USD 07-23: in the investigation's gaps and in the thesis. The critique
    corrected it, and the assessment accepted the correction.
  - SOL-USD 07-23: in the investigation's gaps and in the thesis. The critique
    corrected it, and the assessment accepted the correction.
  - SOL-USD 07-24: the critique listed statistics for the intersection of downtrend
    and wide-range crossings as missing evidence. The assessment neither repeated
    nor corrected it.

  The same reading appears in its not-downtrend form in the BTC-USD 07-22 06:00
  critique, which said the intersection of not-downtrend and wide-range crossings
  was not reported. The assessment did not repeat it. The BTC-USD 07-20 critique
  asked for history jointly conditioned on not-downtrend, wide-range, candle shape
  and volume; its not-downtrend plus wide-range part is the same misreading, and
  the extra features remain outside ADR-015. In ETH-USD 07-23 all four
  stages said no joint group exists. There, 2 classified crossings were not
  wide-range, so the payload does not give the joint group and the statement is
  accurate.

These are observations of the stored text. ADR-015 fixes the parameters.

## Rehearsal

A one-window `PLUMBING` rehearsal on contaminated June data checked the API path
and key-free reproduction before the trial. It is not part of the results.
Planning start 2026-06-15T00:00:00Z, so `lower_band_crossings` had its 720 visible
hours; trial `v2-rehearsal-7`, `claude-haiku-5-5` at effort `high`, cap 1. It
selected BTC-USD at 2026-06-16 15:00 (plan ID
`sha256:dcc418a362e62ecdfb3b41358189b7a78392db8ec7ffbfa83e560748dbc9f9b3`), the
same contaminated window as trial 6's rehearsal. The run completed with 8 model
calls and 4 of 8 tool attempts and returned `ABSTAIN` with confidence 25.
Reproduction matched (`evaluation_id`
`sha256:db8bef49d8a75957e2fb916c5aa05d7ed26cb3aac2018bdfaf526f4b5c98331a`,
reproduced as
`sha256:2448b8b69bda7304d57b73d397c18e1c562e0e605d68fe8c00e94ee05315edab`).

## Reproduction and cost

A separate process with `ANTHROPIC_API_KEY` unset ran `reproduce_evaluation`.
Every baseline replayed exactly, research replayed frozen from the stored
artifacts, and candidate decisions and metrics matched exactly. The reproduced
`evaluation_id` is
`sha256:a3c733e23b2210e6318b19d8fe96e74164b9e32228e1a7daf35c42aa6652c101`. It
differs by design, because frozen replays bind new run IDs. All 11 stored research
runs (10 trial windows and the rehearsal) carry the run commit's code identity.

Every trial run completed with a model assessment, 12 model calls, no re-asks
and 8 of 8 tool attempts.

| Item | Value |
|---|---|
| Trial | `v2-evaluation-7`, cap 12, 10 attempts used |
| Model calls | 120 (12 per window), 0 re-asks |
| Tokens | 1,124,373 input, 129,011 output |
| Input per window | about 112,400 tokens (trial 6: about 71,500) |
| Cost estimated before the run | about 6.70 USD for 10 windows |
| Cost estimated from tokens at list prices | 7.08 USD for the run; 7.09 USD with the rehearsal |
| Cost of the run | 7.08 USD (Console balance 10.30 → 3.22 USD) |

The Console balance change covers the trial run only and agrees with its 7.08 USD
at list prices. The rehearsal, about 0.01 USD, was paid from the earlier balance
of 0.31 USD, before a 10 USD top-up.

## Limitations

- 10 windows in about a week, from six signal hours. Descriptive only; no
  statistical claim.
- Research never chose `TRADE`, so selection quality is unmeasured.
- Risk rejection removed 7 of the 10 windows from any financial comparison. The
  remaining 3 are one move.
- ADR-015's groups are small and overlapping, and crossings cluster in time. The
  wide-range group was the whole classified sample in 8 windows.
- The comparison with trial 6 changes several inputs at once (windows, tool
  descriptions, evidence windows, crossing statistics, synthesis-stage tool
  definitions). It cannot attribute a difference to one change.
- July 2026 is after the model's training cutoff, but parametric knowledge cannot
  be proven point-in-time pure.
- The run script is kept outside the repository, as in V1 and trial 6.
