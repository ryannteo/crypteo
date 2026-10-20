# V2 evaluation trial 6: wider evidence on 10 July 2026 windows

## Summary

Trial 6 re-ran the first 10 windows of the [July 2026 evaluation](v2-july-2026.md)
under a new research policy: candle structure, higher-timeframe and lower-band
crossing tools, per-tool descriptions, and a compact model view of evidence. Each
window was replayed once with the scanner-only baseline and once with fresh Claude
research. Claude again declined every candidate (9 `NO_TRADE`, 1 `ABSTAIN`,
0 `TRADE`). The baseline traded 9 of them and lost 159.95 USD net, exactly as in
trial 5. A separate process with no API key reproduced every research decision
and every metric from the stored artifacts.

The new tools filled the gaps trial 5's research cited, but the model read the
added evidence mostly against the long: trigger bars were wide-range bearish
candles, and the crossing base rate was small and unconditioned. As in trial 5,
research never chose `TRADE`, so selection quality is unmeasured. This trial is a
descriptive comparison of evidence coverage on windows already inspected, not a
performance result. Results from different policies are never pooled.

## What was run

Code and environment:

- Commit `8fe2f930be844ec4adad249d5008039d3f61da46`, clean tree.
- Python 3.12.2, Pydantic 2.13.5.
- Model `claude-opus-5-5` (provider `anthropic`) at effort `high`.
- Workflow `bounded_research_v2`, unchanged from trial 5: 8 tool attempts,
  9 investigation calls, at most 14 model calls and 2 output re-asks per run.
- Prompts unchanged from trial 5: `investigate_prompt_v4`,
  `draft_thesis_prompt_v2`, `critique_prompt_v2`, `final_assessment_prompt_v2`.
- Tools `bounded_market_tools_v3` with evidence policy `market_research_v2` and
  model view `compact_research_view_v1`. Ten capabilities exist;
  `relative_strength` is offered only for bound benchmarks, so these
  single-pair windows offered 9 tools for 8 attempts.

Changes from trial 5's policy, in order: `7a01622` (bound benchmarks only, fixed
per-tool descriptions), `1458095` (compact evidence and request history in the
model view), `8fe2f93` (`candle_structure`, `higher_timeframe`,
`lower_band_crossings`). The crossing study's 720-bar lookback and 6-bar horizon,
and the higher-timeframe block sizes, were fixed before any trial-6 run and were
not tuned on results.

Data and economics are the same as trial 5: the same three Coinbase datasets over
[2026-05-15T00:00:00Z, 2026-09-01T00:00:00Z) and the V1 release economics (see
[What was run](v2-july-2026.md#what-was-run)).

## How the windows were chosen

`plan_windows` with purpose `EVALUATION`, `planning_start` 2026-07-01T00:00:00Z and
limit 10, otherwise as trial 5. The planner considered 11 candidates from
2026-07-05 to 2026-07-12, selected 10 and excluded ETH-USD at 2026-07-06 10:00 as
`OVERLAPPING_CANDIDATE`. Overlap decisions do not depend on the limit, so the 10
selected windows are trial 5's first 10. Plan ID
`sha256:bfeb3314f33c6249b62463f8114a181b240fb4c9198d2863168734fa77954500`.

All 10 windows had been evaluated in trial 5, whose research decisions and
baseline outcomes were viewed and published before this trial was declared. The
windows were chosen to compare policies on the same candidates, not as fresh
data. The cap was 12 research runs for an estimated 5 to 6 USD.

## Results

| Signal (UTC) | Pair | Trial 6 | Confidence | Trial 5 | Baseline outcome | Baseline net P&L (USD) |
|---|---|---|---|---|---|---|
| 2026-07-05 02:00 | SOL-USD | ABSTAIN | – | NO_TRADE 55 | TIME exit | −46.09 |
| 2026-07-06 13:00 | BTC-USD | NO_TRADE | 60 | NO_TRADE 55 | TARGET | +54.52 |
| 2026-07-06 13:00 | ETH-USD | NO_TRADE | 60 | NO_TRADE 55 | TARGET | +43.68 |
| 2026-07-06 13:00 | SOL-USD | NO_TRADE | 60 | ABSTAIN | TARGET | +36.23 |
| 2026-07-08 02:00 | ETH-USD | NO_TRADE | 55 | NO_TRADE 58 | TIME exit | −30.81 |
| 2026-07-08 02:00 | SOL-USD | NO_TRADE | 58 | NO_TRADE 60 | TIME exit | −66.45 |
| 2026-07-08 09:00 | BTC-USD | NO_TRADE | 58 | NO_TRADE 55 | TIME exit | −43.49 |
| 2026-07-10 18:00 | SOL-USD | NO_TRADE | 60 | ABSTAIN | TIME exit | −8.21 |
| 2026-07-12 00:00 | BTC-USD | NO_TRADE | 58 | NO_TRADE 55 | Risk rejected | – |
| 2026-07-12 00:00 | SOL-USD | NO_TRADE | 60 | NO_TRADE 55 | STOP | −99.34 |

Comparison metrics (`evaluation_id`
`sha256:a4fd7f510af597175fc3e36b56655ac5b121351c6ea8b1f20abf26a3cd656eb5`):

| Metric | Value |
|---|---|
| Windows | 10 |
| Research decisions | 0 TRADE, 9 NO_TRADE, 1 ABSTAIN |
| Baseline closed trades | 9 (3 TARGET, 1 STOP, 5 TIME); 1 risk rejection |
| Baseline wins / losses | 3 / 6 (win rate 0.333…) |
| Baseline realized net P&L | −159.9519088941384 USD |
| Research realized net P&L | 0 USD |
| Declined trades' net P&L | −159.9519088941384 USD |
| Unresolved windows | 0 |

## Interpretation

- Every baseline outcome equals trial 5's to the cent, including the risk
  rejection. The commits between the trials changed research inputs only, not the
  deterministic replay path.
- Research declined all 10 candidates. `NO_TRADE` confidence ranged from 55 to 60,
  against 55 to 60 in trial 5. Decisions changed in three windows: SOL-USD
  07-05 moved from `NO_TRADE` to `ABSTAIN`, and SOL-USD 07-06 and 07-10 moved
  from `ABSTAIN` to `NO_TRADE`.
- The SOL-USD 07-05 `ABSTAIN` is the model's assessment of a completed run
  (status `COMPLETED`, origin `MODEL`), citing conflicting and insufficient
  evidence; an abstention carries no required confidence. It is the only window
  where a daily uptrend and a 75% crossing base rate (n=16) offset bearish 1h and
  4h readings.
- With no `TRADE` decisions the `taken` group is empty. A constant "never trade"
  rule produces the same numbers.

## Why research declined

The same stored fields as trial 5 were read for every window: the assessment
rationale and uncertainties, the investigation's remaining gaps, and the critique.

Every run used the same 8 tools and left `bollinger_position` unused. No
assessment cited it as a deciding gap, so the 8-attempt budget stays.

Trial 5's gaps, with this trial's evidence:

| Trial 5 gap | Trial 6 |
|---|---|
| No historical base rate | Present in 10 of 10. Cited as weak in every window: 14 to 17 samples from one 720-bar lookback, possibly clustered, likely drawn from the preceding rally, and not conditioned on the trigger bar's character. 12.5% to 43.75% of samples reached SMA20, and mean adverse excursion was often as large as the mean close return. |
| No higher-timeframe context | Present in 10 of 10 and read as mixed: daily SMA20 rising while the 4h close sat below its SMA20 with negative 5-block returns. Critics repeatedly questioned whether daily trend bears on a 6-bar horizon. |
| No trigger-bar candle structure | Present in 10 of 10 and counterevidence in 9: full-body bearish bars closing near their lows, with ranges about 1.9 to 3.2 times mean true range and volume up to 5.6 times the prior mean. |
| Relative strength unavailable | Still 10 of 10; single-pair windows bind no benchmark. |
| No post-trigger confirmation | 9 of 10; unavailable by design at decision time. |

Gaps raised in this trial:

| Gap cited by the assessment or critic | Windows |
|---|---|
| Base rate conditioned on breakdown-type triggers or on the current regime | 10 of 10 |
| Derivatives, order book, liquidity or news evidence (outside V1 scope) | 10 of 10 |
| Transaction cost context for small mean returns | 8 of 10 |
| Undocumented windows for levels, volume and volatility; differing mean true range figures | 8 of 10 |
| Whether the pending crossing is the current trigger | 7 of 10 |
| Unconditional 6-bar baseline over the same lookback | 3 of 10 |

Two of these are presentation gaps in the current code, not missing data:

- The window definitions are stated in the tool descriptions, which only
  investigation calls with attempts remaining receive
  (`research_workflow.py`). Thesis, critique and final assessment requests
  carry no tool definitions, so those stages see payloads without their
  definitions.
- The crossing at T is always counted in `pending_count`, because its six outcome
  candles have not closed (`market_research.py`, `_lower_band_crossings`). The
  tool description does not say so.

The added evidence turned trial 5's missing evidence into evidence against the
long. The research workflow still had no route to a well-supported `TRADE` at
these triggers; this trial measured evidence coverage, not selection skill.

## Rehearsal

A one-window `PLUMBING` rehearsal on contaminated June data verified the API path
and key-free reproduction before the trial; it is not part of the results.
Planning start 2026-06-15T00:00:00Z, so `lower_band_crossings` had its 720
visible hours; trial `v2-rehearsal-6`, `claude-haiku-5-5` at effort `high`. It
selected BTC-USD at 2026-06-16 15:00 (plan ID
`sha256:dcc418a362e62ecdfb3b41358189b7a78392db8ec7ffbfa83e560748dbc9f9b3`) and
returned `ABSTAIN` with confidence 30. Reproduction matched (`evaluation_id`
`sha256:d7c5966e756f8f0f1093756fb97a806e94043beba31d87ede44a080f81576142`,
reproduced as
`sha256:aa4ab8ab43524a0c5801b50b33cae352b1e392ef5e22a7a1ae873c2d7ef6c86e`).

## Reproduction and cost

A separate process with `ANTHROPIC_API_KEY` unset ran `reproduce_evaluation`:
every baseline replayed exactly, research replayed frozen from the stored
artifacts, and candidate decisions and metrics matched exactly. The reproduced
`evaluation_id` is
`sha256:7b985926eeadf9832dccb2545ddfc0f98179f785e83176b8c19ac31fbc434bee`; it
differs by design because frozen replays bind new run IDs.

| Item | Value |
|---|---|
| Trial | `v2-evaluation-6`, cap 12, 10 attempts used |
| Model calls | 120 (12 per window), 0 re-asks |
| Tokens | 715,103 input, 110,219 output |
| Input per window | about 71,500 tokens (trial 5: about 154,000) |
| Cost estimated from tokens | 5.06 USD |
| Cost of rehearsal and run | 5.08 USD (Console balance) |

## Limitations

- 10 windows covering five or six distinct market moves in eight days, all
  previously inspected in trial 5. Descriptive only; no statistical claim.
- Research never chose `TRADE`, so selection quality is unmeasured.
- The comparison with trial 5 changes several inputs at once (tool set,
  descriptions, model view); it cannot attribute a difference to one change.
- Single-pair windows bind one dataset, so relative-strength evidence remained
  unavailable.
- The July windows from 2026-07-05 to 2026-07-17 have now been inspected twice; a
  later trial needs windows not yet viewed.
- July 2026 is after the model's training cutoff, but parametric knowledge cannot
  be proven point-in-time pure.
- The run script is kept outside the repository, as in V1.
