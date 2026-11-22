# V2 ranking trial 2: research's peer comparison did not vary enough to rank 30 baseline trades

## Summary

Ranking trial 2 set out to measure what
[ADR-025](../decisions/ADR-025-peer-comparison-and-ranking-trial-2.md)
pre-registered: whether `peer_comparison`, a judgment of how each candidate
compares with this pair's earlier lower-band crossings, ranks the baseline's
trades. Stage 1 researched 30 windows from 2026-11-07 to 2026-11-19 with
`claude-opus-5-5` at effort `high`. The peer comparison was `SIMILAR` in 27 of
the 30 windows and `WORSE` in 3, so one value covered 90%, over ADR-025's 80%
futility threshold. The trial stopped after stage 1, as pre-registered: no
stage 2 ran and no primary measure was computed. The analysis ran once on stage
1's 30 windows and computed the descriptive secondary measures only.

The lean was `AGAINST` in all 30 windows. Research declined every candidate: 29
`NO_TRADE` and 1 `ABSTAIN`, with no `TRADE`. Every baseline trade closed. The
trial spent 18.56 of its 50 USD budget, rehearsal included. A separate process
with no API key reproduced the rehearsal and stage 1 from the stored artifacts.
Results are not pooled with ranking trial 1 or any earlier trial.

## What was run

Code and policy:

- Run commit `ee348e24f90dd50d28d19076f86d8be53a475e44`, source digest
  `sha256:64b152cd6a7d4a6bd0a9dc78c3c0b898c91a3676a71d16d2e9a57b7ad0a4a867`.
- Trial: model `claude-opus-5-5` (provider `anthropic`) at effort `high`.
  Rehearsal: `claude-haiku-5-5` at effort `high`.
- Workflow `bounded_research_v3`: 9 tool attempts, 10 investigation calls, at most
  15 model calls and 2 output re-asks per run.
- The policy identities that `trial2_run.py`'s `RUN_POLICY` pins. Every spending
  command checked them against the loaded code before it ran.

| Component | Identity |
|---|---|
| Tools | `bounded_market_tools_v6` |
| Evidence | `market_research_v4` |
| Workflow | `bounded_research_v3` |
| Critic | `critique_prompt_v3` |
| Adapter | `anthropic_messages_research_v2` |
| Assessment prompt | `final_assessment_prompt_v4`, `sha256:64bb06443e770f5e0ffdc4d353e4f231221957675990dd995df68fd7a38fbb51` |
| Assessment schema | `final_assessment_schema_v3`, `sha256:2499e168ec6973357a70a702ccacdfb7b542de3d16737359fcfff44dfe2f9811` |

Economics are the V1 release economics with the 6-hour holding limit, as ADR-025
requires, from `trial2_run.py`'s `settings`:

| Setting | Value |
|---|---|
| Initial cash | 10,000 USD |
| Risk fraction | 0.01 |
| Max holding bars | 6 |
| Entry and exit fee rate | 0.004 each |
| Adverse slippage | 0.0005 |
| Price increment | 0.01 |
| Quantity increment | 0.00000001 |

Plans use purpose `EVALUATION` and
[ADR-018](../decisions/ADR-018-plan-only-authorized-candidates.md)'s rule, with
scanner policy `bollinger_lower_cross_v1`.

Data. Stage 1 used a new download over [2026-10-01T00:00:00Z,
2026-11-22T15:00:00Z), taken by `download-stage1` and recorded in
`stage1-datasets.json`. Each pair has 1,263 candles with no missing hours.

| Pair | Dataset version | Acquisition ID |
|---|---|---|
| BTC-USD | `sha256:f6a1c71192bb561dad8ab519b3a04618465eadd29a5b6bb6735448b42f354d72` | `sha256:6104e4aa891ec44fd0098c8ee5d6edf3f401d4e0c78ab877bd311e6e400703ea` |
| ETH-USD | `sha256:9df1a882b5282f96724f779fa2ca96536db306308f0a1f0f960345fb81e83eae` | `sha256:03984a6a9fc98b7eef3dc203011fbc1b2d501d5cbceb27e179a87beca1852430` |
| SOL-USD | `sha256:7bc9e4b8c3b9d720e847202394d372af054224e49d13391aab7e67e03327bfa6` | `sha256:16788b247fa2c1663477550d6fbfb95ce8f5aa2f806c7a17e17cafcb51f347fc` |

The rehearsal used trial 1's datasets over [2026-05-15T00:00:00Z,
2026-09-01T00:00:00Z) (see
[ranking trial 1](v2-ranking-trial-1.md#what-was-run)), which
`check-rehearsal-data` found complete.

Scripts:

- The run script `~/crypteo-eval/trial2_run.py`, SHA-256
  `27827763796808cacdf4180c692aafede02cbdb42b7310dd76ad56aaf0d4dddb`.
- Its tests, `~/crypteo-eval/test_trial2_run.py`, SHA-256
  `18b53a0fd93baed1463966b03a4282b36a8b4f0737aa429fe50232c7a08301b0`: 37 tests,
  run against the repository's code at the run commit, passed before the
  rehearsal.
- Both stay outside the repository; ADR-025 requires it of the run script.
  They are new files; trial 1's `trial_run.py` is unchanged.

Trials `v2-ranking-2-rehearsal` (store `rehearsal.sqlite`) and
`v2-ranking-2-stage1` (store `trial.sqlite`), both under
`~/crypteo-eval/ranking-2/`.

## Pre-registration and integrity

ADR-025 was committed in `efab3ae8782ccd43c407017469a0c11f5f013e21`
(2026-11-21T14:40:54Z) and implemented in
`98ea1f31a723032cdb9d138cf7d0cc60fe199b4e` (2026-11-21T15:53:03Z). The run commit
`ee348e2` (2026-11-21T17:15:41Z) changed one test's timing margins and no file
under `src/`. All three precede the trial's preflight at 2026-11-22T15:12:35Z.

What `trial2_run.py`'s code guarantees:

- Until `analyze`, no command prints a baseline outcome, a research financial
  outcome or a comparison metric. Its reports print each window's decision,
  peer comparison, lean, confidence, model calls, tools, tokens and list-price
  cost; `reproduce` prints only whether metrics and candidates are equal, with
  the evaluation IDs. A test runs a full mocked trial and searches every printed
  line for an outcome.
- `futility` reads peer comparisons only. A test shows that a uniform peer
  comparison triggers it while the leans vary.
- The plan commands print each candidate's signal, decision and exclusion
  reason.
- `analyze` writes `analysis.json` once and refuses to run again once that file
  exists. After a futility stop it computes the secondary measures and not the
  primary measure.
- Seed 20261120, 10,000 permutations and the scores `MUCH_WORSE` −2, `WORSE` −1,
  `SIMILAR` 0, `BETTER` +1, `MUCH_BETTER` +2, as pre-registered. `analysis.json`
  records the seed and the permutation count; no permutation ran.

Planning itself is the repository's `plan_windows`
([specification](../v1-specification.md#evaluation-window-planning)), which
`trial2_run.py` calls: it computes the baseline's terms and risk decision at
each candidate's signal hour and runs no replay, inference or research, although
its overlap rule reads later scanner candidates.

The rehearsal and stage 1 ran from a task whose rules barred reading the stores,
`evaluation.json` or any trial file except through the script's commands, and
the session that ran it reported following them. Spending commands were run by
hand with the key set; every other command ran without it, and the script
refuses otherwise.

## How the windows were chosen

`plan_windows` with purpose `EVALUATION`, planning start 2026-11-06T00:00:00Z and
limit 30, on the stage-1 download. Plan ID
`sha256:f42bcbd92d20a6feec1ea42efa4925b9a7a126dff840e8431a122c5bd3a2f8e5`. The
planner considered 49 candidates, from 2026-11-07 13:00 to 2026-11-19 22:00, and
stopped at its 30th selection. It excluded 19: 16 as `OVERLAPPING_CANDIDATE` and
3 as `BASELINE_NOT_AUTHORIZED` (each a risk-stage rejection,
`NON_POSITIVE_TARGET_REWARD`). The selected signals run from 2026-11-07 21:00 to
2026-11-19 22:00. The run cap was 33 research runs at an estimated 0.70 USD per
window, a projected total of 23.12 USD at the cap (0.02 USD spent before stage 1
plus 33 × 0.70). Stage 1 used 30 runs.

## Futility and the stop

ADR-025's futility rule: after stage 1, if one peer comparison value covers 80%
or more of stage 1's windows, the trial stops: research does not discriminate
between these candidates. `futility` printed:

```text
MUCH_WORSE    0 of 30
WORSE         3 of 30
SIMILAR      27 of 30
BETTER        0 of 30
MUCH_BETTER   0 of 30
FUTILITY: one peer comparison covers 80% or more of stage 1; the trial stops
```

| Peer comparison | Windows | Share |
|---|---|---|
| `MUCH_WORSE` | 0 | 0.0% |
| `WORSE` | 3 | 10.0% |
| `SIMILAR` | 27 | 90.0% |
| `BETTER` | 0 | 0.0% |
| `MUCH_BETTER` | 0 | 0.0% |

Largest share: 27 of 30; 80% needs 24.

The trial therefore stopped. No stage 2 was declared, no further data was
downloaded, and ADR-025's primary measure, the rank correlation between peer
comparison and outcome with its permutation test, was not computed.

## Results

The analysis ran once after the stop, on stage 1's 30 windows, and wrote
`analysis.json`
(`sha256:1f9052fb0d7df11d9e2b9982b7b87089c9145ce03d9bac58ae1ea0f791b48d87`). Its
primary block reads `"computed": false` with the reason "futility stop: ADR-025
computes no primary measure". All 30 baseline trades closed, so all 30 windows
were included and none was excluded.

Secondary measures, descriptive only. Mean and median are baseline net P&L in
USD; the win rate counts windows with positive net P&L.

| Peer comparison | Windows | Mean | Median | Win rate |
|---|---|---|---|---|
| `MUCH_WORSE` | 0 | – | – | – |
| `WORSE` | 3 | −100.2640 | −100.0733 | 0/3 |
| `SIMILAR` | 27 | −30.3816 | −21.2615 | 10/27 |
| `BETTER` | 0 | – | – | – |
| `MUCH_BETTER` | 0 | – | – | – |

| Decision | Windows | Mean | Median | Win rate |
|---|---|---|---|---|
| `TRADE` | 0 | – | – | – |
| `NO_TRADE` | 29 | −38.8008 | −24.1213 | 9/29 |
| `ABSTAIN` | 1 | 4.1296 | 4.1296 | 1/1 |

| Lean | Windows | Mean | Median | Win rate |
|---|---|---|---|---|
| `STRONGLY_AGAINST` | 0 | – | – | – |
| `AGAINST` | 30 | −37.3698 | −23.8464 | 10/30 |
| `BALANCED` | 0 | – | – | – |
| `FOR` | 0 | – | – | – |
| `STRONGLY_FOR` | 0 | – | – | – |

Lean correlation: none; every lean is `AGAINST`.

Windows per lean and peer comparison (included windows):

| Lean | `MUCH_WORSE` | `WORSE` | `SIMILAR` | `BETTER` | `MUCH_BETTER` |
|---|---|---|---|---|---|
| `AGAINST` | 0 | 3 | 27 | 0 | 0 |

`lower_band_crossings` requested: 30 of 30 windows.

Every window in plan order. The outcome is the baseline's realized net P&L,
rounded half-even to cents; Tools counts the window's tool attempts out of 9.

| Signal (UTC) | Pair | Decision | Confidence | Lean | Peer comparison | Tools | Exit | Baseline net P&L (USD) |
|---|---|---|---|---|---|---|---|---|
| 2026-11-07 21:00 | SOL-USD | `NO_TRADE` | 58 | `AGAINST` | `SIMILAR` | 9 | TIME | −47.61 |
| 2026-11-08 22:00 | BTC-USD | `ABSTAIN` | 35 | `AGAINST` | `SIMILAR` | 9 | TIME | +4.13 |
| 2026-11-08 22:00 | SOL-USD | `NO_TRADE` | 55 | `AGAINST` | `SIMILAR` | 8 | TIME | +35.54 |
| 2026-11-10 16:00 | BTC-USD | `NO_TRADE` | 45 | `AGAINST` | `SIMILAR` | 9 | TIME | −1.40 |
| 2026-11-10 16:00 | ETH-USD | `NO_TRADE` | 60 | `AGAINST` | `SIMILAR` | 9 | TIME | +23.60 |
| 2026-11-10 16:00 | SOL-USD | `NO_TRADE` | 55 | `AGAINST` | `SIMILAR` | 8 | TIME | +11.60 |
| 2026-11-12 14:00 | BTC-USD | `NO_TRADE` | 50 | `AGAINST` | `SIMILAR` | 9 | STOP | −100.00 |
| 2026-11-12 14:00 | ETH-USD | `NO_TRADE` | 55 | `AGAINST` | `SIMILAR` | 9 | TIME | −26.78 |
| 2026-11-12 14:00 | SOL-USD | `NO_TRADE` | 60 | `AGAINST` | `SIMILAR` | 8 | TIME | −23.57 |
| 2026-11-13 03:00 | SOL-USD | `NO_TRADE` | 55 | `AGAINST` | `SIMILAR` | 8 | STOP | −99.13 |
| 2026-11-13 04:00 | BTC-USD | `NO_TRADE` | 60 | `AGAINST` | `SIMILAR` | 9 | STOP | −100.00 |
| 2026-11-13 04:00 | ETH-USD | `NO_TRADE` | 58 | `AGAINST` | `SIMILAR` | 9 | TIME | −16.07 |
| 2026-11-14 02:00 | SOL-USD | `NO_TRADE` | 58 | `AGAINST` | `WORSE` | 9 | STOP | −100.76 |
| 2026-11-14 03:00 | BTC-USD | `NO_TRADE` | 52 | `AGAINST` | `SIMILAR` | 9 | STOP | −100.00 |
| 2026-11-14 04:00 | ETH-USD | `NO_TRADE` | 55 | `AGAINST` | `SIMILAR` | 8 | TIME | +19.69 |
| 2026-11-14 15:00 | BTC-USD | `NO_TRADE` | 58 | `AGAINST` | `SIMILAR` | 9 | STOP | −100.00 |
| 2026-11-14 15:00 | ETH-USD | `NO_TRADE` | 55 | `AGAINST` | `SIMILAR` | 9 | STOP | −99.79 |
| 2026-11-14 21:00 | ETH-USD | `NO_TRADE` | 55 | `AGAINST` | `WORSE` | 9 | STOP | −100.07 |
| 2026-11-14 21:00 | SOL-USD | `NO_TRADE` | 55 | `AGAINST` | `SIMILAR` | 9 | STOP | −100.35 |
| 2026-11-15 02:00 | BTC-USD | `NO_TRADE` | 62 | `AGAINST` | `SIMILAR` | 8 | TIME | +39.83 |
| 2026-11-16 10:00 | ETH-USD | `NO_TRADE` | 55 | `AGAINST` | `SIMILAR` | 9 | TARGET | +5.93 |
| 2026-11-17 11:00 | BTC-USD | `NO_TRADE` | 58 | `AGAINST` | `SIMILAR` | 8 | TARGET | +7.41 |
| 2026-11-17 16:00 | ETH-USD | `NO_TRADE` | 40 | `AGAINST` | `SIMILAR` | 9 | TIME | +6.16 |
| 2026-11-17 17:00 | BTC-USD | `NO_TRADE` | 55 | `AGAINST` | `SIMILAR` | 9 | TARGET | +24.72 |
| 2026-11-18 15:00 | BTC-USD | `NO_TRADE` | 55 | `AGAINST` | `SIMILAR` | 9 | STOP | −100.00 |
| 2026-11-18 15:00 | ETH-USD | `NO_TRADE` | 55 | `AGAINST` | `WORSE` | 9 | STOP | −99.96 |
| 2026-11-18 17:00 | SOL-USD | `NO_TRADE` | 55 | `AGAINST` | `SIMILAR` | 8 | TIME | −38.41 |
| 2026-11-19 21:00 | BTC-USD | `NO_TRADE` | 60 | `AGAINST` | `SIMILAR` | 8 | TIME | −24.12 |
| 2026-11-19 22:00 | ETH-USD | `NO_TRADE` | 52 | `AGAINST` | `SIMILAR` | 8 | TIME | −21.26 |
| 2026-11-19 22:00 | SOL-USD | `NO_TRADE` | 55 | `AGAINST` | `SIMILAR` | 9 | TIME | −0.42 |

## Interpretation

- **No ranking was measured.** The pre-registered stop fired, so this trial
  says nothing about whether the peer comparison ranks outcomes. ADR-025
  pre-registers a stop's reading: research does not discriminate between these
  candidates. The stop does not show that research carries no information; it
  shows that this judgment did not vary enough on these candidates to be
  tested.
- **The peer comparison collapsed onto its middle level.** It was `SIMILAR` in
  27 windows and `WORSE` in 3, and never `MUCH_WORSE`, `BETTER` or
  `MUCH_BETTER`. ADR-025's instruction tells the model to "Use SIMILAR when the
  collected evidence does not set this candidate apart from those crossings."
  Every run requested `lower_band_crossings`, so the comparison evidence was
  collected each time; in 27 windows the assessment judged that it did not set
  the candidate apart. That instruction probably made `SIMILAR` the easy
  answer. This is a reading of the counts and the instruction text, not a
  tested finding.
- **The lean did not vary either.** It was `AGAINST` in all 30 windows, with 29
  `NO_TRADE` and 1 `ABSTAIN`. In ranking trial 1 it was `AGAINST` in 36 windows
  and `BALANCED` in 11, mirroring the decision. Here the single `ABSTAIN` also
  stated `AGAINST`. The two trials used different policies and windows and are
  not pooled.
- **The three `WORSE` windows all ended at the stop.** Each lost about 100 USD,
  against a mean of −30.38 USD for the 27 `SIMILAR` windows, 8 of which also
  ended at the stop. These are three windows, described after the trial
  stopped, with no test pre-registered for them. They are not evidence that the
  peer comparison ranks outcomes.
- Research declined every candidate (0 `TRADE`), as in trials 5 to 8 and ranking
  trial 1. Selection quality remains unmeasured, and
  [ADR-024](../decisions/ADR-024-crossing-scanner-stays-candidate-source.md)
  still bars measuring it against this baseline.
- Results are not pooled with earlier trials.

## Baseline

These baseline results are descriptive only.

30 windows from 20 signal hours; 8 hours fired on more than one pair (6 on 2, 2 on 3), holding 18 windows.

| Signal (UTC) | Pairs | Exits | Peer comparisons |
|---|---|---|---|
| 2026-11-08 22:00 | BTC-USD, SOL-USD | TIME, TIME | `SIMILAR`, `SIMILAR` |
| 2026-11-10 16:00 | BTC-USD, ETH-USD, SOL-USD | TIME, TIME, TIME | `SIMILAR`, `SIMILAR`, `SIMILAR` |
| 2026-11-12 14:00 | BTC-USD, ETH-USD, SOL-USD | STOP, TIME, TIME | `SIMILAR`, `SIMILAR`, `SIMILAR` |
| 2026-11-13 04:00 | BTC-USD, ETH-USD | STOP, TIME | `SIMILAR`, `SIMILAR` |
| 2026-11-14 15:00 | BTC-USD, ETH-USD | STOP, STOP | `SIMILAR`, `SIMILAR` |
| 2026-11-14 21:00 | ETH-USD, SOL-USD | STOP, STOP | `WORSE`, `SIMILAR` |
| 2026-11-18 15:00 | BTC-USD, ETH-USD | STOP, STOP | `SIMILAR`, `WORSE` |
| 2026-11-19 22:00 | ETH-USD, SOL-USD | TIME, TIME | `SIMILAR`, `SIMILAR` |

Every window shared one exit reason in 6 of 8 hours.

| Windows | Wins | Mean | Median | Total (USD) | Exact total (USD) |
|---|---|---|---|---|---|
| 30 | 10/30 | −37.3698 | −23.8464 | −1,121.09 | −1121.0949283994784 |

| Exit | Windows | Wins | Mean | Median | Total (USD) |
|---|---|---|---|---|---|
| TIME | 16 | 7/16 | −3.6932 | −0.9083 | −59.09 |
| TARGET | 3 | 3/3 | 12.6855 | 7.4068 | +38.06 |
| STOP | 11 | 0/11 | −100.0055 | −100.0000 | −1,100.06 |

| Peer comparison | Windows | TIME | TARGET | STOP | Total (USD) |
|---|---|---|---|---|---|
| `WORSE` | 3 | 0 | 0 | 3 | −300.79 |
| `SIMILAR` | 27 | 16 | 3 | 8 | −820.30 |

Stop exits lost about the full 1% risk of 100 USD, as in ranking trial 1 and the
[baseline strategy study](v2-baseline-strategy-study.md#by-exit-reason).

The planning rule biases this sample, as in ranking trial 1. Its
`OVERLAPPING_CANDIDATE` check excludes a candidate when another scanner
candidate of the same pair is observed inside its window, so it reads later
candidates and is not causal
([specification](../v1-specification.md#evaluation-window-planning)). It
excluded 16 of stage 1's 49 candidates. The rule reads no research output, so it
cannot favour any peer comparison value, but it shapes which candidates the
trial researched. Baseline totals describe the planned windows, not the
strategy.

## Rehearsal

A one-window `PLUMBING` rehearsal on contaminated June data checked the live
peer-comparison schema and instructions before stage 1. It is not part of the
results. Trial `v2-ranking-2-rehearsal`, `claude-haiku-5-5` at effort `high`,
cap 2, planning start 2026-06-15T00:00:00Z. It selected BTC-USD at 2026-06-16
15:00 (plan ID
`sha256:5d40ad6fc18703d27e9efd5231adafa60f5729f8b2a5746dbeb6472a8d8f6910`), the
same contaminated window as ranking trial 1's rehearsal. The run completed with a
model assessment: `ABSTAIN` with peer comparison `SIMILAR`, lean `AGAINST` and
confidence 25, after 9 model calls and 5 of 9 tool attempts
(`lower_band_crossings`, `higher_timeframe`, `volume_context`,
`candle_structure`, `momentum`). It used 50,914 input, 8,996 cache-write, 19,330
cache-read and 16,865 output tokens; the cache reads show that prompt caching
worked. Reproduction matched (`evaluation_id`
`sha256:8c4781ac382be574ec5679ef07eb86c99cdfb253041d3674d0763535c6d1bf75`,
reproduced as
`sha256:3d994f6658c92831d5c811769eabbce36ccb2dfa7fe517361697292926509b40`).

Before it, a first rehearsal was started with an earlier version of the script
(`b215e1fc…`, rehearsal cap 1) and cancelled with Ctrl+C. It left one reserved
attempt. A re-run under that version was refused because the cap was used, and
one under the current version was refused because the stored run settings
differed; neither made a model request. The Console balance did not change. The
`ranking-2/` folder was then deleted, and the trial restarted from an empty
folder with the current script, recording `rehearsal-start` afresh. Any spend by
the cancelled start falls outside the measured total, and the unchanged balance
bounds it below one cent. A first `run-stage1` with a placeholder plan ID was
refused before any request.

## Reproduction and cost

A separate process with `ANTHROPIC_API_KEY` unset ran `trial2_run.py reproduce`
for the rehearsal and stage 1. Each printed `metrics equal: True` and
`candidates equal: True`. The reproduced `evaluation_id`s differ by design,
because frozen replays bind new run IDs.

| Run | Original `evaluation_id` | Reproduced `evaluation_id` |
|---|---|---|
| Rehearsal | `sha256:8c4781ac382be574ec5679ef07eb86c99cdfb253041d3674d0763535c6d1bf75` | `sha256:3d994f6658c92831d5c811769eabbce36ccb2dfa7fe517361697292926509b40` |
| Stage 1 | `sha256:25b1d5fb15450743b742f0ecf7adcf932fc74231878c46af059b6c1894b808be` | `sha256:1e44ec56ca804da63f72ef6ccfa2ae9aabec4e41163affc96693031de0e05993` |

`identity` exited 0 and reported all 31 stored research runs (1 rehearsal, 30 in
stage 1) as matching the run commit's code identity, its git commit and source
digest.

Every one of the 31 runs completed with a model assessment, with a peer
comparison and a lean, and no tool attempt was rejected.

| Model calls | Tool attempts | Windows |
|---|---|---|
| 13 | 9 | 20 |
| 12 | 8 | 10 |

Every window made its tool attempts plus 4 model calls, so none re-asked.

Tools left out: `bollinger_position` in 10.

| Windows | Model calls | Tool attempts | Input | Cache write | Cache read | Output | List-price estimate (USD) |
|---|---|---|---|---|---|---|---|
| 30 | 380 | 260 | 2,481,622 | 15,429 | 1,200,671 | 415,012 | 18.54 |

Spend from the Console balances: 18.56 USD.

The Console balances, in the order recorded:

| Entry | USD |
|---|---|
| rehearsal-start | 25.00 |
| stage1-start | 24.98 |
| stage1-end | 6.44 |

| Stage | Console spend (USD) | Windows | Per window (USD) |
|---|---|---|---|
| Rehearsal | 0.02 | 1 | 0.02 |
| Stage 1 | 18.54 | 30 | 0.6180 |
| Total | 18.56 | 31 | – |

Total spend 18.56 of 50.00 USD. No top-up was recorded. List prices are
`trial2_run.py`'s `PRICES`, as in ranking trial 1: 4, 20, 5 and 0.20 USD per
million input, output, cache-write and cache-read tokens for `claude-opus-5-5`.
The Console balances are the owner's readings, recorded with
`trial2_run.py balance` in `balances.json`; they are not stored artifacts.

Saved outputs, all under `~/crypteo-eval/`. The run's outputs are in
`ranking-2-stage1-handoff/` and the analysis's in `ranking-2-analysis-handoff/`;
the `ranking-2/` paths are in the trial folder, and `record_tables.py` and
`tables.md` are in `ranking-2-record/`:

| File | Content | SHA-256 |
|---|---|---|
| `checks.txt` | Preflight results (the stage-1 handoff's) | `8f9d68edda21ef2b245ee002ddafa3f0c069abef6f5035cfd3e2a96b42b9f439` |
| `pytest-test_trial2_run.txt` | `pytest test_trial2_run.py` | `e31620dafe36f402b41f2e9ffbe304f6fb128d9a98c8087ea15d3470f2e00793` |
| `check-rehearsal-data.txt` | `trial2_run.py check-rehearsal-data` | `962dd26eeb0d01a2b2c53e023bd67190b58f4852fdd1ed07c3d14af9c3af2264` |
| `rehearse.txt` | `trial2_run.py rehearse` | `d41c06e87b638a8f931c570784a8f28ab2cc96e0d8d248104d1494b1bcf93f33` |
| `report-rehearsal.txt` | `trial2_run.py report-rehearsal` | `63c40bfc4758291697cf702ecf3f9f939e128e6e4e12a33d4798d93aeb860c9a` |
| `reproduce-rehearsal.txt` | `trial2_run.py reproduce rehearsal` | `cf118623e877dc00507377e8f1b879bbf9984f43edebe0e2e2feca531edac6dd` |
| `download-stage1.txt` | `trial2_run.py download-stage1` | `9c8e3b02a5d66d3b4739659e493e175f67fe33ea048d8bb3838be6e340b644b9` |
| `plan-stage1.txt` | `trial2_run.py plan-stage1` | `29b6389139235a3e8c3f2e27e582803172546944025355346fcd7a74d4ef8265` |
| `run-stage1.txt` | `trial2_run.py run-stage1` | `83293dc29928606f96d9cfc73da5f946c80b58929fe30c0a274e65f6979a5f4f` |
| `report-stage1.txt` | `trial2_run.py report-stage1` | `e268549e56945dca42652ca4339fd7fcc9926ce90ed9f70494bfa0408f37fa22` |
| `reproduce-stage1.txt` | `trial2_run.py reproduce stage1` | `e12024f1ed2ddc6f0971353d0b4fad45eab871cd8de82d5bc56862b55199ebd5` |
| `reproduce-stage1.invocation-error.txt` | A mistyped first invocation, kept | `7a651d144960bd21919d24aa4ecbeaa50cce0c1c5f0c305ef43916ce33c2ecb9` |
| `identity.txt` | `trial2_run.py identity` | `e73b6c36df46fac9b21595134e7a204141fe7e9e3a7dc5009123f10800095d58` |
| `futility.txt` | `trial2_run.py futility` | `225a674ad05116004d56f0785c3cbfde9296ba2bf9f4485c0d6896bd29f847c3` |
| `analyze.txt` | `trial2_run.py analyze` | `6a1082c61af21fa596a2bd3537b62aa99b61400ba21342deaea48c360479603c` |
| `analysis.json` | Copy of `ranking-2/analysis.json` | `1f9052fb0d7df11d9e2b9982b7b87089c9145ce03d9bac58ae1ea0f791b48d87` |
| `ranking-2/balances.json` | The Console ledger | `96a16d0f6de13774b947695fb5ff62ffb6987a0e2002997f256730fb5a3e376a` |
| `ranking-2/stage1-datasets.json` | The stage-1 download's pins | `5fb6db5066748fddf6898bb5495a6af39a278f81072e34e8323eee4f5788c6b2` |
| `record_tables.py` | The table script | `c2cb199702a0fdff8d12c44b2e00267dc101345794cfcd1197a9791bac1af2d6` |
| `tables.md` | Its output | `71af90168741914944612829e6d3c9acafc4cc6a93ac756cde422a081251a924` |

`record_tables.py` uses the standard library only. It exits unless
`analysis.json` and `report-stage1.txt` have their pinned SHA-256. It re-derives
every outcome figure from `analysis.json`'s per-window rows and asserts each
against its secondary block where that block holds it, checks that the report
and the analysis list the same windows with the same decisions, peer
comparisons, leans and confidences, and re-derives the token totals and the
list-price estimate from the report's per-window lines. This record's futility,
results, baseline and research-run tables are pasted from its output,
`tables.md`.

## Limitations

- 30 windows from 20 signal hours. Windows in the same hour across pairs have
  correlated outcomes (6 of the 8 multi-pair hours shared one exit reason), so
  the effective sample is smaller than 30.
- The futility stop means the trial measured no ranking. It cannot say whether
  a peer comparison that varied would have ranked these candidates.
- `SIMILAR`'s instruction doubled as the answer for evidence that does not set
  the candidate apart. The trial cannot separate "research judged these
  candidates alike" from "research used the default".
- The planning rule's overlap check is not causal (see [Baseline](#baseline)).
- One candidate source, the lower-band crossing scanner, which the baseline
  study, the trading-logic exploration and the bar-length study found has no
  edge after costs.
- 2026-11-07 to 2026-11-19 is after the model's training cutoff, but parametric
  knowledge cannot be proven point-in-time pure.
- The run script and the table script are outside the repository; this record,
  the saved outputs and their checksums are their evidence.
