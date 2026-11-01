# V2 evaluation trial 8: nine tool attempts and a both-sided critic on 10 new July 2026 windows

## Summary

Trial 8 researched 10 July 2026 windows that no earlier trial had inspected. It is
the first trial under
[ADR-016](../decisions/ADR-016-tool-attempts-cover-single-pair-tools.md) (nine tool
attempts, workflow `bounded_research_v3`) and
[ADR-017](../decisions/ADR-017-critic-tests-both-cases.md) (a critic told to test
the bull and bear cases with the same scrutiny, `critique_prompt_v3`). Each window
was replayed once with the scanner-only baseline and once with fresh Claude
research. Claude declined every candidate (4 `NO_TRADE`, 6 `ABSTAIN`, 0 `TRADE`).
The baseline's risk check rejected 2 of the 10 proposals. The other 8 closed for a
net loss of 345.89 USD (2 wins, 6 losses). A separate process with no API key
reproduced every research decision and every metric from the stored artifacts.

Eight runs used all nine tool attempts and requested `bollinger_position`, a tool
no trial-6 or trial-7 run had requested. Two stopped after eight attempts without
it. Counted with the no-TRADE diagnostic's D5 definition, the critiques' challenged
claims and contradictions weakened the bull case 87 times and the bear case 34
times (builder). A blind recount found 90 and 33. Research never chose `TRADE`, so
selection quality is unmeasured. This trial is descriptive under
[ADR-013](../decisions/ADR-013-v2-evaluation-scope.md), not a performance result,
and its results are not pooled with trials 5, 6 and 7.

## What was run

Code and environment:

- Commit `d3602ac172f3a5a66cdb9bb5d4e8618b5d8afc0f`, source digest
  `sha256:8288d51b96ed557d0458220017b568b61495204f26ca383324a45846df787ff9`.
- Model `claude-opus-5-5` (provider `anthropic`) at effort `high`.
- Workflow `bounded_research_v3`: 9 tool attempts, 10 investigation calls, at most
  15 model calls and 2 output re-asks per run.
- Prompts `investigate_prompt_v4`, `draft_thesis_prompt_v2`, `critique_prompt_v3`
  and `final_assessment_prompt_v2`, as recorded in every stored model request. The
  trader-led critic prompt at this commit is `trader_critique_prompt_v2`; these
  opportunity-led runs do not use it.
- Tools `bounded_market_tools_v6` with evidence policy `market_research_v4` and
  model view `compact_research_view_v2`, unchanged from trial 7. These single-pair
  windows offered 9 tools for 9 attempts.

Changes from trial 7's policy (run commit `a2b5eed`), in order:

- `64a6d88`: ADR-016 recorded.
- `a119231`: ADR-017 recorded.
- `d3602ac`: both implemented. The budget moves to 9 tool attempts, 10
  investigation calls and 15 model calls (`bounded_research_v3`), and the critic
  stage instructions change (`critique_prompt_v3`, `trader_critique_prompt_v2`).

`d3602ac` is the only commit since `a2b5eed` that changes `src/` or
`pyproject.toml`. Tools, evidence, the other prompts, schemas, the model view and
ADR-015's crossing statistics are unchanged.

Data and economics are the same as trials 5, 6 and 7: the same three Coinbase
datasets over [2026-05-15T00:00:00Z, 2026-09-01T00:00:00Z) and the V1 release
economics (see [What was run](v2-july-2026.md#what-was-run)). The run TASK records
that the data check passed before planning; re-run for this record, it found 2,616
candles per pair with no gaps.

## How the windows were chosen

`plan_windows` with purpose `EVALUATION`, `planning_start` 2026-07-25T00:00:00Z and
limit 10, otherwise as trial 5. Trials 5 to 7 researched windows up to 2026-07-24
18:00 (trial 7's last signal, SOL-USD at 12:00, plus its 6-hour holding horizon),
so the start falls after all of them. The planner considered 12 candidates from
2026-07-25 09:00 to 2026-07-31 08:00, selected 10 and excluded ETH-USD at
2026-07-31 03:00 and SOL-USD at 2026-07-31 05:00 as `OVERLAPPING_CANDIDATE`. Plan
ID `sha256:d5010629e281307398071a97c8b4a54e0857a1503d0264f3e651fef576ab6a5c`. The
run cap was 12 research runs, and the estimated cost was about 8.00 USD for
10 windows and at most about 9.60 USD at the cap.

No earlier trial, and not the no-TRADE diagnostic, had inspected any of the 10
windows. As ADR-015 requires, planning read scanner signals only, and the
rehearsal used June data.

Three signal hours fired on more than one pair: 2026-07-25 09:00 (BTC, ETH),
2026-07-27 16:00 (BTC, SOL) and 2026-07-27 23:00 (BTC, ETH, SOL). With 2026-07-29
15:00 (ETH), 2026-07-29 20:00 (SOL) and 2026-07-31 08:00 (BTC), the 10 windows come
from six signal hours, with simultaneous signals counted once.

## Results

| Signal (UTC) | Pair | Research | Confidence | Baseline outcome | Baseline net P&L (USD) |
|---|---|---|---|---|---|
| 2026-07-25 09:00 | BTC-USD | NO_TRADE | 45 | Risk rejected | – |
| 2026-07-25 09:00 | ETH-USD | ABSTAIN | – | Risk rejected | – |
| 2026-07-27 16:00 | BTC-USD | ABSTAIN | – | TIME | −23.27 |
| 2026-07-27 16:00 | SOL-USD | NO_TRADE | 55 | TIME | −18.07 |
| 2026-07-27 23:00 | BTC-USD | ABSTAIN | – | TIME | −77.36 |
| 2026-07-27 23:00 | ETH-USD | ABSTAIN | – | TIME | −60.53 |
| 2026-07-27 23:00 | SOL-USD | NO_TRADE | 55 | TIME | −79.63 |
| 2026-07-29 15:00 | ETH-USD | ABSTAIN | – | TARGET | +2.32 |
| 2026-07-29 20:00 | SOL-USD | NO_TRADE | 60 | TARGET | +10.65 |
| 2026-07-31 08:00 | BTC-USD | ABSTAIN | – | STOP | −100.00 |

Comparison metrics (`evaluation_id`
`sha256:24d949669aad381178a1da451d6145363d09efb972655f75d19bf22ddd9173ee`):

| Metric | Value |
|---|---|
| Windows | 10 |
| Research decisions | 0 TRADE, 4 NO_TRADE, 6 ABSTAIN |
| Baseline closed trades | 8 (5 TIME, 2 TARGET, 1 STOP); 2 risk rejections |
| Baseline wins / losses | 2 / 6 (win rate 0.25) |
| Baseline realized net P&L | −345.8888146240432 USD |
| Research realized net P&L | 0 USD |
| Declined trades' net P&L | −345.8888146240432 USD |
| Unresolved windows | 0 |

## Interpretation

- Research declined all 10 candidates. With no `TRADE` decisions the `taken` group
  is empty, and a constant "never trade" rule produces the same numbers. Selection
  quality is unmeasured.
- Decisions moved toward `ABSTAIN`: 6 of 10, against 4 of 10 in trial 7 and 1 of 10
  in trial 6. `NO_TRADE` confidence ranged from 45 to 60 (45, 55, 55, 60), against
  55 to 60 in trials 6 and 7. These are descriptive comparisons of different
  windows. The windows, the tool budget (ADR-016) and the critic instructions
  (ADR-017) all changed in this trial, and 10 windows cannot attribute either
  shift to any one of them.
- The baseline's risk check rejected 2 of 10 proposals, both at 2026-07-25 09:00.
  Each rejection's `reason` in the baseline replay's `RISK_REJECTION` record is
  `NON_POSITIVE_TARGET_REWARD`: the estimated net reward per unit at the target,
  after entry and exit fees and slippage, was not positive
  ([specification](../v1-specification.md#simulation-and-risk)). Those 2 windows had
  no baseline trade for research to take or decline. Research could change a
  financial outcome in the other 8.
- The 6 `ABSTAIN` decisions are model assessments of completed runs (status
  `COMPLETED`, origin `MODEL`). Each gives `INSUFFICIENT_EVIDENCE` and
  `CONFLICTING_EVIDENCE` as its reasons. An abstention carries no required
  confidence, and none of the six gives one.

## Why research declined

The same stored fields as trials 5 to 7 were read for every window: the assessment
rationale and key uncertainties, the investigation's remaining gaps, and the
critique. The counts below come from reading that text.

All 10 assessments set the crossing history (an edge over the unconditional
baseline, stronger in some group) against the trigger candle, momentum and
short-term trend. The four `NO_TRADE` assessments found no crossing evidence
matched to the candle at T that supported the long, or no edge distinguishable
from noise, while the clearest current-state evidence was bearish: BTC-USD
07-25 ("the LONG case lacks any profile-matching support"), SOL-USD 07-27 16:00
("the setup shows no reliable conditional edge"), SOL-USD 07-27 23:00 ("the
absence of a demonstrated reversion edge") and SOL-USD 07-29 20:00 ("lacks a
reliable edge, and the best-supported evidence is bearish"). The six `ABSTAIN`
assessments found the same weak support but did not consider the bearish readings
established, each saying in its own words that the evidence neither supports the
long nor justifies an active rejection. In all 10 runs the assessment's critic
response accepts at least one of the critique's challenges to the bear case.

| Gap cited by the investigation, critic or assessment | Windows |
|---|---|
| No benchmark, market-wide or cross-venue context (single-pair windows bind no benchmark) | 10 of 10 |
| Derivatives, order book, funding or news evidence (outside V1 scope) | 10 of 10 |
| Small crossing samples that may cluster or overlap | 10 of 10 |
| Lookback is one ~30-day regime and may not be representative | 10 of 10 |
| Current crossing pending; no post-trigger confirmation | 10 of 10 |
| Per-crossing detail, group medians, dispersion or significance measures | 9 of 10 |
| Longer, multi-regime or out-of-sample crossing history, or other horizons | 8 of 10 |
| The hourly trend classification at T is borderline or may not reflect the regime (narrow SMA20/SMA50 margin, close below both) | 8 of 10 |
| A joint wide-range and trend group | 7 of 10 |
| Wide-range feature does not discriminate: no or almost no not-wide crossings | 7 of 10 |
| Conditioning on features ADR-015 does not report (crossing depth, band width, volatility regime, candle anatomy, volume, daily trend) | 7 of 10 |
| Comparability of historical crossings with current volatility, crossing depth, band width or distance to SMA20 | 6 of 10 |
| Hour-of-day or day-of-week baseline for trigger volume | 3 of 10 |
| `bollinger_position` not requested, so band width and percent_b unknown | 2 of 10 |
| No or almost no historical crossings match the not-wide-range candle at T | 2 of 10 |
| Transaction costs against small mean returns | 2 of 10 |

The `bollinger_position` gap, cited in 10 of 10 trial-7 windows, appears only in
the two runs that did not request the tool (see [Tool use](#tool-use-adr-016)).
The joint-group row counts windows whose investigation, critic or assessment said
a joint group was missing. In 6 of those 7 windows the classified crossings
include not-wide-range crossings, so the payload does not give the joint group
(see [ADR-015 reporting](#adr-015-reporting)). These are descriptive comparisons;
the windows differ between the trials.

## Tool use (ADR-016)

Tools requested per run, in order, taken by script from each run's trace. All 9
tools were offered in every run, in the order `trend`, `momentum`, `volatility`,
`bollinger_position`, `levels`, `volume_context`, `candle_structure`,
`higher_timeframe`, `lower_band_crossings`.

| Signal (UTC) | Pair | Tools requested, in order | Attempts |
|---|---|---|---|
| 2026-07-25 09:00 | BTC-USD | `lower_band_crossings`, `higher_timeframe`, `candle_structure`, `levels`, `momentum`, `volume_context`, `volatility`, `trend`, `bollinger_position` | 9 of 9 |
| 2026-07-25 09:00 | ETH-USD | `lower_band_crossings`, `trend`, `momentum`, `higher_timeframe`, `candle_structure`, `volume_context`, `volatility`, `levels`, `bollinger_position` | 9 of 9 |
| 2026-07-27 16:00 | BTC-USD | `lower_band_crossings`, `trend`, `higher_timeframe`, `candle_structure`, `volume_context`, `levels`, `momentum`, `volatility`, `bollinger_position` | 9 of 9 |
| 2026-07-27 16:00 | SOL-USD | `lower_band_crossings`, `trend`, `higher_timeframe`, `momentum`, `candle_structure`, `volume_context`, `levels`, `volatility`, `bollinger_position` | 9 of 9 |
| 2026-07-27 23:00 | BTC-USD | `lower_band_crossings`, `trend`, `higher_timeframe`, `candle_structure`, `volume_context`, `levels`, `momentum`, `volatility`, `bollinger_position` | 9 of 9 |
| 2026-07-27 23:00 | ETH-USD | `lower_band_crossings`, `trend`, `higher_timeframe`, `volume_context`, `candle_structure`, `momentum`, `levels`, `volatility`, `bollinger_position` | 9 of 9 |
| 2026-07-27 23:00 | SOL-USD | `lower_band_crossings`, `higher_timeframe`, `candle_structure`, `volume_context`, `momentum`, `volatility`, `levels`, `trend`, `bollinger_position` | 9 of 9 |
| 2026-07-29 15:00 | ETH-USD | `lower_band_crossings`, `trend`, `higher_timeframe`, `candle_structure`, `volume_context`, `momentum`, `levels`, `volatility`, `bollinger_position` | 9 of 9 |
| 2026-07-29 20:00 | SOL-USD | `lower_band_crossings`, `higher_timeframe`, `candle_structure`, `volume_context`, `levels`, `momentum`, `volatility`, `trend` | 8 of 9 |
| 2026-07-31 08:00 | BTC-USD | `lower_band_crossings`, `trend`, `higher_timeframe`, `momentum`, `candle_structure`, `volume_context`, `levels`, `volatility` | 8 of 9 |

Runs requesting each tool: `bollinger_position` 8 of 10 (trials 6 and 7: 0 of 20);
every other tool 10 of 10, once per run. Every run requested
`lower_band_crossings` first, and every run that requested `bollinger_position`
requested it last. All 88 attempts succeeded; none was rejected or served from
cache.

Two runs completed before using all nine attempts:

- **SOL-USD 2026-07-29 20:00**: 8 attempts and 13 model calls. One call was an
  output re-ask. With 1 attempt remaining, the investigator returned a completion
  with an empty summary, gaps and evidence list. It was rejected
  (`summary: text must be nonblank`), and the re-asked completion, the run's tenth
  investigation call, was accepted.
- **BTC-USD 2026-07-31 08:00**: 8 attempts and 12 model calls, with no re-ask.

Neither run's stored text says why it stopped. Both investigations list the tool
as a gap without giving a reason: "The bollinger_position tool was not requested.
The trigger payload gives the lower band (about 72.74 against a close of 72.67),
but band width and percent_b were not checked separately." (SOL-USD) and
"bollinger_position was not requested, so band width and percent_b were not
checked directly. The trigger payload gives the lower band, and volatility gives
some range context." (BTC-USD).

Only these two assessments still list band width or percent_b as missing. SOL-USD
07-29 20:00 gives "Bollinger position (percent_b, band width) was not requested,
so the depth of the crossing relative to band width is unknown" as a key
uncertainty and says "Confidence is moderate because Bollinger position and
benchmark context are missing". BTC-USD 07-31 08:00 gives "Bollinger band width and
percent_b were not observed" and names Bollinger position first among "Key
discriminating evidence [that] is missing". None of the eight assessments in runs
that requested the tool lists band width or percent_b at T as missing. Six of
their critiques asked instead for band-width history or for crossing outcomes
conditioned on band width or crossing depth, which no tool provides.

## Critique direction (ADR-017)

ADR-017's measurement uses the no-TRADE diagnostic's D5 definition, quoted verbatim
from [its Method](v2-no-trade-diagnostic.md#method):

> **D5. Direction of the critique's challenges.** Count the critique's challenged
> claims and contradictions that weaken the bull case and those that weaken the
> bear case. Count only items that clearly do one or the other; others are
> uncounted.

**Method.** `blind.py` built `blind-extract.json` from the stored runs. Per run it
holds the label (trial, signal time, pair), the four stage outputs, the tools
offered and the tools requested with status and attempts used. It holds no
baseline, P&L, exit, trade outcome or code; the script asserts the absence of the
outcome fields. The builder coded every item from that extract, applying the
diagnostic's D5 convention and two additions: items that challenge both cases at
once, and items about how an invalidation condition was designed, are uncounted.
The builder had seen the trial's baseline outcomes before coding, which the
diagnostic's builder had not. `builder-codes.json` (counts and item lists) was
saved before the blind pass and its checksum written to `hashes-pre-blind.txt`.

One fresh reviewer agent, outside the review loop, then got the extract's path,
the D5 definition above verbatim, and an instruction to list for each run every
`challenged_claims` and `contradictions` item it counts as weakening the bull or the
bear case, with totals. It was not given the builder's convention, the builder's
codes or any record. It was told to open no other file in `~/crypteo-eval/` or the
repository, and it stated that the extract was the only file it opened (besides
its own printouts of it). Its output opens with the rules it says it applied,
quoted here verbatim:

> - An item counts as bull or bear when its main target is a claim in that case, or the support/counterevidence framing behind it. An item counts as bull when it attacks evidence listed as support or the summary's supportive framing. An item counts as bear when it attacks the bear case's own claims.
> - Items aimed at both cases are uncounted. So are items calling something symmetric, items about bookkeeping only (IDs in both lists, wrong list), and items aimed only at invalidation conditions or assumptions that belong to neither case.
> - An item that challenges a claim but ends up affirming it is uncounted (SOL 07-29 challenged_claims[8]).
> - When an item challenges one case and mentions the other in passing, I counted it once, for its main target.

The output was saved verbatim to `blind-d5-codes.txt`, and its checksum was
appended to `hashes-pre-blind.txt` before any comparison. `d5_agreement.py`
compares the two saved files. Neither file was edited, and no code was resolved
or adjusted.

Items weakening each case, per window (bull / bear):

| Signal (UTC) | Pair | Items | Builder | Blind | Direction (both coders) |
|---|---|---|---|---|---|
| 07-25 09:00 | BTC-USD | 15 | 9 / 4 | 10 / 4 | bull > bear |
| 07-25 09:00 | ETH-USD | 15 | 11 / 3 | 11 / 3 | bull > bear |
| 07-27 16:00 | BTC-USD | 13 | 9 / 3 | 9 / 3 | bull > bear |
| 07-27 16:00 | SOL-USD | 14 | 9 / 4 | 9 / 4 | bull > bear |
| 07-27 23:00 | BTC-USD | 13 | 9 / 3 | 10 / 3 | bull > bear |
| 07-27 23:00 | ETH-USD | 14 | 10 / 3 | 10 / 3 | bull > bear |
| 07-27 23:00 | SOL-USD | 13 | 8 / 3 | 8 / 3 | bull > bear |
| 07-29 15:00 | ETH-USD | 13 | 9 / 4 | 9 / 4 | bull > bear |
| 07-29 20:00 | SOL-USD | 12 | 6 / 3 | 7 / 2 | bull > bear |
| 07-31 08:00 | BTC-USD | 13 | 7 / 4 | 7 / 4 | bull > bear |
| **Total** | | **135** | **87 / 34** | **90 / 33** | 10 of 10 under each |

"Items" is the critique's challenged claims plus contradictions. The two coders'
per-window directions agree in 10 of 10 windows. They code 4 items differently, in
3 windows: `contradictions[0]` in BTC-USD 07-25 09:00, BTC-USD 07-27 23:00 and
SOL-USD 07-29 20:00 (builder uncounted, because each says an evidence ID is listed
on both sides; blind bull), and `challenged_claims[8]` in SOL-USD 07-29 20:00
(builder bear; blind uncounted, as an item that ends up affirming the claim).

Beside trial 7, a descriptive comparison of different windows:

| Trial | Runs | Builder: bull | Builder: bear | Blind: bull | Blind: bear |
|---|---|---|---|---|---|
| 7 ([diagnostic](v2-no-trade-diagnostic.md#d5-blind-recount)) | 10 | 87 | 15 | 85 | 13 |
| 8 | 10 | 87 | 34 | 90 | 33 |

In trial 8, 89 of the 95 challenged claims open with a side label: "Bull:" (46),
"Bull case:" (11), "Bear:" (26) or "Bear case:" (6). As ADR-017 requires, these counts describe the critiques; they are
not a measure of decision quality, and the trial cannot attribute the difference
from trial 7 to the new critic instructions.

Derived files, all in `~/crypteo-eval/diagnostic-8/`:

| File | SHA-256 |
|---|---|
| `blind-extract.json` | `6715bbc3344976783af8ae2b7302bef7d668529a2230be9c3ae677ec1d8a3e87` |
| `builder-codes.json` | `d2a72425134b7d7010e25bfead761242181252331552bead74970f0d14b9b86d` |
| `blind-d5-codes.txt` | `8f80cfeafafa5a2a8bb1647c0a39ad97f8f5083af63e3e9cbd2f66e4f69b0533` |
| `d5_agreement.py` | `c02031cbf8e3af1644618ed3cc87aca5806a5b16e0846b7489820c675f368a8d` |
| `blind-prompt.txt` | `0a68bb1dddb300cfab4438b835d2e7c7670467a3b210224285567d4783f7fe75` |

The other scripts there are `extract.py` (loads the runs), `blind.py` (builds the
extract), `show.py` (prints a run's stage text), `reask.py` (the re-ask request),
`groups.py` (crossing-group sizes), `policy.py` (stored prompt and model versions)
and an unchanged copy of the run script, `trial_run.py`.

## ADR-015 reporting

Per window: sampled, classified and unclassified crossings, and the group the
candle ending at T falls in for each feature, with that group's sample count.
`lower_band_crossings` was requested and succeeded in all 10 runs.

| Signal (UTC) | Pair | Sampled | Classified | Unclassified | Range group at T (n) | Trend group at T (n) |
|---|---|---|---|---|---|---|
| 07-25 09:00 | BTC-USD | 17 | 17 | 0 | not wide range (0) | downtrend (9) |
| 07-25 09:00 | ETH-USD | 15 | 15 | 0 | not wide range (2) | downtrend (7) |
| 07-27 16:00 | BTC-USD | 18 | 16 | 2 | wide range (15) | not downtrend (8) |
| 07-27 16:00 | SOL-USD | 14 | 14 | 0 | wide range (14) | not downtrend (6) |
| 07-27 23:00 | BTC-USD | 18 | 17 | 1 | wide range (16) | not downtrend (9) |
| 07-27 23:00 | ETH-USD | 16 | 14 | 2 | wide range (11) | not downtrend (8) |
| 07-27 23:00 | SOL-USD | 15 | 15 | 0 | wide range (15) | not downtrend (7) |
| 07-29 15:00 | ETH-USD | 15 | 14 | 1 | wide range (11) | not downtrend (8) |
| 07-29 20:00 | SOL-USD | 15 | 15 | 0 | wide range (15) | downtrend (8) |
| 07-31 08:00 | BTC-USD | 17 | 17 | 0 | wide range (16) | not downtrend (10) |

Citations. In all 10 windows each of the four stages (investigation, thesis,
critique and assessment) cited both the unconditional baseline and at least one
conditioned group: 40 of 40 stage outputs, confirmed by reading. The run script's
pattern matches missed 12 of these citations in 8 stage outputs across 5 windows:

- BTC-USD 07-25: the thesis's group citation ("The analog subgroup matching
  current_downtrend is weak"), and the assessment's baseline ("64.7% positive
  versus a 53% baseline") and group ("The downtrend-conditioned analogs show 9
  samples") citations.
- ETH-USD 07-25: the thesis's group citation ("The downtrend subgroup (7
  samples)").
- BTC-USD 07-27 23:00: the investigation's group citation ("Not-downtrend (n=9):
  89% positive"), and the assessment's baseline ("versus 54% and +0.08% for the
  baseline") and group ("the not-downtrend subgroup, which looks stronger (n=9)")
  citations.
- SOL-USD 07-29 20:00: the investigation's baseline ("versus 51% for all candles")
  and group ("The 8 crossings in a downtrend, which matches the current candle")
  citations.
- BTC-USD 07-31 08:00: the investigation's baseline ("all candles (694 samples)
  closed higher 54.3% of the time") and the assessment's baseline ("The baseline
  was 54.3%") and group ("the not-downtrend subgroup did so 80% of the time")
  citations.

Groups:

- The candle ending at T was not wide-range in 2 windows, BTC-USD and ETH-USD at
  07-25 09:00, the first such windows in any trial. For BTC-USD the not-wide group
  had 0 samples, so no classified crossing matched the candle's range feature. For
  ETH-USD it had 2.
- The not-wide group had samples in 6 windows: ETH-USD 07-25 (2), BTC-USD 07-27
  16:00 (1), BTC-USD 07-27 23:00 (1), ETH-USD 07-27 23:00 (3), ETH-USD 07-29 15:00
  (3) and BTC-USD 07-31 (1). In the other 4 (BTC-USD 07-25, SOL-USD 07-27 16:00,
  SOL-USD 07-27 23:00 and SOL-USD 07-29 20:00) every classified crossing was
  wide-range.
- The candle at T was in a downtrend in 3 windows (BTC-USD and ETH-USD 07-25,
  SOL-USD 07-29 20:00) and not in a downtrend in the other 7.

The two reading errors trial 7 named:

- **A later SMA crossover treated as moving the setup into another group.**
  Classification is fixed at the crossing candle, so a later SMA20/SMA50 crossover
  cannot reclassify the current crossing. Every thesis in the seven not-downtrend
  windows made this reading, as an invalidation condition:
  - BTC-USD 07-27 16:00 ("moving the setup into the historically weaker
    downtrend-crossing group"): the critique corrected it ("a later SMA20/SMA50 flip
    does not move this setup into the downtrend group"), and the assessment
    accepted the correction ("a deterioration signal, not a reclassification").
  - SOL-USD 07-27 16:00 ("moving the setup into downtrend conditions"): the
    critique kept the flip as a risk ("close to the thesis's own first
    invalidation condition") without correcting the classification, and the
    assessment repeated the reading ("a flip would change the conditioning
    group"). Not corrected.
  - BTC-USD 07-27 23:00 ("moving the setup into the historically weak
    downtrend-crossing regime"): the critique repeated it ("Whether the
    not-downtrend classification will persist after the next few bars"), and so did
    the assessment ("could flip into the historically weak downtrend regime"). Not
    corrected.
  - ETH-USD 07-27 23:00 ("moving the setup into the downtrend group"): the critique
    repeated it ("persist over the next bars or flip into the weaker downtrend
    group"), and so did the assessment ("persists or flips into the weaker
    downtrend group"). Not corrected.
  - SOL-USD 07-27 23:00 ("moving the setup into the downtrend regime"): the critique
    challenged the condition only because downtrend crossings had done no worse
    ("Even if it happens, history shows downtrend crossings did not resolve
    worse"), and the assessment set it aside on that ground ("a downtrend
    reclassification is not shown to be harmful"). The classification point was
    not corrected.
  - ETH-USD 07-29 15:00 ("moving the setup into the historically weaker
    downtrend-crossing group"): the critique repeated it ("could reclassify the
    setup into the downtrend group"), and the assessment kept it as an uncertainty
    ("Whether the not-downtrend classification persists over the 6-bar horizon").
    Not corrected.
  - BTC-USD 07-31 08:00 ("That would move the setup into the downtrend group"): the
    critique corrected it ("A later 1h SMA20/SMA50 cross does not move this sample
    into the downtrend group's statistics"), and the assessment accepted the
    correction ("Invalidation 1 misapplies the crossing-time downtrend label"). Its
    key uncertainties still call the label fragile, without naming a group.

  No stage in the three downtrend windows said a later crossover would move the
  setup into the not-downtrend group.
- **No joint wide-range and trend group.** When every classified crossing is
  wide-range and the candle at T is wide-range, the trend group is the joint group.
  That holds in SOL-USD 07-27 16:00, SOL-USD 07-27 23:00 and SOL-USD 07-29 20:00,
  and no stage in those windows said a joint group was missing. The error does not
  occur in this trial.

  In the five windows with wide-range candles and not-wide crossings, the payload
  does not give the joint group. In four of them (BTC-USD 07-27 16:00, BTC-USD
  07-27 23:00, ETH-USD 07-29 15:00 and BTC-USD 07-31) the stages said it is not
  reported, which is accurate. In ETH-USD 07-27 23:00 the investigation said no
  joint group "is available", but the critique said "the current setup's exact
  conditions have zero direct samples". With 8 not-downtrend crossings and only 3
  not-wide ones, at least 5 crossings were both wide-range and not-downtrend, so
  that statement is wrong. The assessment's "no joint subgroup exists" neither
  repeats the count nor corrects it.

  Two other readings of the joint group appear. In BTC-USD 07-25 the critique
  listed "a joint breakdown for downtrend crossed with not-wide-range" as missing;
  with no not-wide crossings that group is empty, as the investigation and assessment said ("No
  analogs exist for the current downtrend plus not-wide-range profile"). In ETH-USD
  07-25 the investigation and thesis said no joint downtrend and not-wide-range
  sample exists. The critique corrected this ("the joint count is unreported (it
  could be 0, 1 or 2)"), and the assessment accepted the correction.

These are observations of the stored text. ADR-015 fixes the parameters.

## Rehearsal

A one-window `PLUMBING` rehearsal on contaminated June data checked the API path
and key-free reproduction before the trial. It is not part of the results.
Planning start 2026-06-15T00:00:00Z, so `lower_band_crossings` had its 720 visible
hours; trial `v2-rehearsal-8`, `claude-haiku-5-5` at effort `high`, cap 1, at the
run commit. It selected BTC-USD at 2026-06-16 15:00 (plan ID
`sha256:dcc418a362e62ecdfb3b41358189b7a78392db8ec7ffbfa83e560748dbc9f9b3`), the same
contaminated window as the trial-6 and trial-7 rehearsals. The run completed with
a model assessment, 7 model calls and 3 of 9 tool attempts (`lower_band_crossings`,
`volume_context`, `trend`), and returned `ABSTAIN` with no confidence
(`INSUFFICIENT_EVIDENCE`, `CONFLICTING_EVIDENCE`, `UNRELIABLE_EVIDENCE`). It used
52,397 input and 18,186 output tokens. Reproduction matched (`evaluation_id`
`sha256:47f9d9f9be7a29a7af7480d698c39abfa99fe4374a423a52dbb1263f4e81b987`,
reproduced as
`sha256:460d144ae9ae029c2b24d19dfdf9e0f6ec79cf7cf1e63e4517f374c479f54e76`).

## Reproduction and cost

A separate process with `ANTHROPIC_API_KEY` unset ran `reproduce_evaluation`.
Every baseline replayed exactly, research replayed frozen from the stored
artifacts, and candidate decisions and metrics matched exactly. The reproduced
`evaluation_id` is
`sha256:88d0d9471103fef961f07dd8f48d4cbec88628c34e100425801b6bef83487a70`. It
differs by design, because frozen replays bind new run IDs. All 11 stored research
runs (10 trial windows and the rehearsal) carry the run commit's code identity.

This record's checks ran from the main checkout at the run commit, on copies of
`evaluation-8/`, `evaluation-8.sqlite`, `rehearsal-8/` and `rehearsal-8.sqlite`,
with `ANTHROPIC_API_KEY` unset. SHA-256 of the originals, before copying and after
all reading (identical):

| Original | SHA-256 |
|---|---|
| `evaluation-8.sqlite` | `4867e390023feec7bf7ca77f7a793cd018087fcae22f5813574c469966a2f4b5` |
| `rehearsal-8.sqlite` | `7dfb5c1aceb5f90541e5230b40f6eb72ac2031b15e11f6a65b724b1277920b7d` |
| `evaluation-8/` (digest of its sorted per-file list, 33 files) | `64bdf2d0505bd2cafc0429a5c14464d25829f58087219926ca3ddc8f5ee7f224` |
| `rehearsal-8/` (digest of its sorted per-file list, 6 files) | `f086050091f6b30b775121923073ffa3eff0a96aea83c671b015388bdbbea246` |

The per-file lists are `~/crypteo-eval/diagnostic-8/checksums-before.txt` and
`checksums-after.txt`, and the two files are byte-identical. Each directory digest
is the SHA-256 of that directory's `shasum -a 256` lines, with paths as
`evaluation-8/…` or `rehearsal-8/…`, sorted as whole lines (that is, by file hash)
and newline-terminated: `find evaluation-8 -type f -exec shasum -a 256 {} + | sort
| shasum -a 256` from `~/crypteo-eval`. No store had `-wal` or `-shm` files.

Every trial run completed with a model assessment. Eight made 13 model calls with
9 of 9 tool attempts; SOL-USD 07-29 20:00 made 13 calls, one of them an output
re-ask, with 8 attempts; BTC-USD 07-31 08:00 made 12 calls with 8 attempts.

| Item | Value |
|---|---|
| Trial | `v2-evaluation-8`, cap 12, 10 attempts used |
| Model calls | 129 (13 in 9 windows, 12 in 1), 1 output re-ask |
| Tool attempts | 88 (9 of 9 in 8 windows, 8 of 9 in 2), all successful |
| Tokens | 1,241,072 input, 135,084 output |
| Input per window | about 124,100 tokens (trial 7: about 112,400) |
| Cost estimated before the run | about 8.00 USD for 10 windows; at most about 9.60 USD at the cap |
| Cost estimated from tokens at list prices | 7.67 USD for the run; 7.68 USD with the rehearsal |
| Cost of the run | 7.67 USD (Console balance 13.21 → 5.54 USD) |

The run cost 0.33 USD less than the 10-window estimate. List prices are those in
the [v2.0.0 release record](../releases/v2.0.0.md). The Console balances are the
owner's readings, recorded in this trial's TASK; they are not in the stored
artifacts. The balance change covers the trial run only and agrees with its
7.67 USD at list prices. The rehearsal, about 0.01 USD, was paid before the
10 USD top-up.

## Limitations

- 10 windows in about a week, from six signal hours. Descriptive only; no
  statistical claim.
- Research never chose `TRADE`, so selection quality is unmeasured. Risk
  rejection removed 2 windows from any financial comparison.
- Two policy changes at once: nine tool attempts (ADR-016) and the both-sided
  critic (ADR-017), on new windows. The trial cannot attribute a difference in
  decisions, confidence or critique direction to either change.
- ADR-015's groups are small and overlapping, and crossings cluster in time. The
  wide-range group was the whole classified sample in 4 windows. Among the 8
  windows with a wide-range candle at T it was the whole sample in 3; in the 2
  windows whose candle was not wide-range, the matching not-wide group had 0 and 2
  samples.
- D5 was coded by models: the builder and one blind coder, each a model. Agreement
  between two model coders is not agreement with a human reading, and the blind
  coder had the definition only, without the builder's convention.
- Comparisons with trial 7 are descriptive comparisons of different windows.
- July 2026 is after the model's training cutoff, but parametric knowledge cannot
  be proven point-in-time pure.
- The run script is kept outside the repository, as in V1 and trials 6 and 7.
