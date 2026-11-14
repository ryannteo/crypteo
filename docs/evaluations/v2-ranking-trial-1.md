# V2 ranking trial 1: research's lean did not rank 47 baseline trades

## Summary

Ranking trial 1 measured what
[ADR-021](../decisions/ADR-021-pre-registered-ranking-measure.md) pre-registered:
whether the graded lean that
[ADR-020](../decisions/ADR-020-assessment-states-a-lean.md) adds to every model
assessment ranks the baseline's trades, that is, whether candidates research leans
against turn out worse than candidates it leans toward. It researched 47 windows in
two stages, 17 in August 2026 and 30 from 2026-10-17 to 2026-11-05, with
`claude-opus-5-5` at effort `high`. Every window's baseline trade closed, so all 47
were included. Spearman's rank correlation between lean score and baseline net P&L
was −0.085206. In the pre-registered one-sided permutation test, 7,160 of 10,000
permutations were at least as large, so p = 7161/10001 ≈ 0.716. There is no
evidence that research ranks better than chance.

The lean took only two values and mirrored the decision in every window: 36
`NO_TRADE` windows stated `AGAINST` and 11 `ABSTAIN` windows stated `BALANCED`.
Research declined every candidate (0 `TRADE`). The trial spent 29.10 of its 50 USD
budget, rehearsal included. A separate process with no API key reproduced the
rehearsal and both stages from the stored artifacts. Results are not pooled with
earlier trials.

## What was run

Code and policy:

- Run commit `43361495c094f452e73069629affbfca5d9cad5a`, source digest
  `sha256:0adccdaa06be66b0fef82f57383c0acc5d2048cfa88650188f863501a2afcd35`.
- Trial: model `claude-opus-5-5` (provider `anthropic`) at effort `high`.
  Rehearsal: `claude-haiku-5-5` at effort `high`.
- Workflow `bounded_research_v3`: 9 tool attempts, 10 investigation calls, at most
  15 model calls and 2 output re-asks per run.
- The policy identities that `trial_run.py`'s `RUN_POLICY` pins. Every spending
  command checked them against the loaded code before it ran.

| Component | Identity |
|---|---|
| Tools | `bounded_market_tools_v6` |
| Evidence | `market_research_v4` |
| Workflow | `bounded_research_v3` |
| Critic | `critique_prompt_v3` |
| Adapter | `anthropic_messages_research_v2` |
| Assessment prompt | `final_assessment_prompt_v3`, `sha256:424d7f664954ad70a8a6993a5be51addb25efde40be59ddcdf5935174514c37c` |
| Assessment schema | `final_assessment_schema_v2`, `sha256:d0f2ec6f9ce888f6b014a23f995de35b488494ea691cee9c94c884161c84653b` |

Economics are the V1 release economics with the 6-hour holding limit, as ADR-021
requires, from `trial_run.py`'s `settings`:

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

Data. Stage 1 and the rehearsal used the trial-5 to trial-8 datasets over
[2026-05-15T00:00:00Z, 2026-09-01T00:00:00Z) (see
[What was run](v2-july-2026.md#what-was-run)). `check-data` found 2,616 candles per
pair with no gaps.

| Pair | Dataset version | Acquisition ID |
|---|---|---|
| BTC-USD | `sha256:8ed022be4bca52899ad5263f9a16271182b8672e64c7769f2910a522610d1227` | `sha256:f6d57e74041d6d9393f7042578fae0831bbb47431b798d855cb603f9d322c8ab` |
| ETH-USD | `sha256:6f94d98e6a9cdfa16162b5ea2589422f2717be1223464b136e05c0d096e12c7a` | `sha256:aa27b44029ee7fa19d2245971bb6ce9b9902694950110dae4071b8e5875e5cc0` |
| SOL-USD | `sha256:2fbca75732a798231200dde006105748612ac4c4e8d7d249cc272ed467e7a678` | `sha256:42213fd47223abc5447b3e0e20f914b0992eac1fb0ac5a22e6d6bc3ca2453324` |

Stage 2b used a new download over [2026-09-15T00:00:00Z, 2026-11-14T13:00:00Z),
recorded in `stage2-datasets.json` and pinned in the stage-2 declaration
`stage2.json`. Each pair has 1,453 candles with no missing hours.

| Pair | Dataset version | Acquisition ID |
|---|---|---|
| BTC-USD | `sha256:dad4ebcbb2834324f08527cad76cfa929ede3e317d41aa3f25c091763b8a8306` | `sha256:8babb6c65460ddf50f1932d91464ad0c707302ab5c1c6a77d99075f41e9f4574` |
| ETH-USD | `sha256:5e444c82f61fe53f529011cb0cbbce9fb90263fe6b70806ab9a7ed94dc720994` | `sha256:fc19100f09276fb8167784e5d6ad5825958cc823efea00e42f9991b014e06a1e` |
| SOL-USD | `sha256:a55be500767ae0b774aa479040abe17bd41ceaaf9cdde378948914211af7aeaf` | `sha256:37d224a7e0b4d2562d42027945afb51d47555427d48d8b0121c21a783fdc6e83` |

Scripts:

- The run script `~/crypteo-eval/trial_run.py`, SHA-256
  `b0e721f5f052fd266cc8cddf419fa40c4edc35dc0a82e974684dc0db0fa873c0`.
- Its tests, `~/crypteo-eval/test_trial_run.py`, SHA-256
  `38dd20c2682e2414aae7bfff65b2655a841f51eb78c4ab0ea0141a34ff574820`: 28 tests,
  run against the repository's test helpers at the run commit, passed on Python
  3.12.2.
- The run script stays outside the repository, as ADR-021 requires.

Trials `v2-ranking-1-rehearsal` (store `rehearsal.sqlite`), `v2-ranking-1-stage1`
and `v2-ranking-1-stage2b` (both in store `trial.sqlite`), all under
`~/crypteo-eval/ranking-1/`.

## Pre-registration and integrity

ADR-021 and ADR-020 were committed in `79391f772f2d4b00844dd0755d337ca71201cc9e`
(2026-11-03T10:08:48Z), and ADR-020 was implemented in
`82603fa3036c417a67582523c5b7d0c513334168` (2026-11-06T12:11:43Z), both before any
trial run.

- Until `analyze`, `trial_run.py` printed no baseline outcome or comparison
  measure. Its reports print each window's decision, lean, confidence, model
  calls, tools, tokens and list-price cost, and `reproduce` prints only whether
  metrics and candidates are equal, with the evaluation IDs.
- `futility` read leans only.
- Planning read scanner signals and decision-time terms only. The plan commands
  print each candidate's signal, decision and exclusion reason.
- `analyze` ran once and wrote `analysis.json`. It refuses to run again once that
  file exists.
- Seed 20261103 and 10,000 permutations, as pre-registered. `analysis.json`
  records both.
- Scores as pre-registered: `STRONGLY_AGAINST` −2, `AGAINST` −1, `BALANCED` 0,
  `FOR` +1, `STRONGLY_FOR` +2.

## How the windows were chosen

**Stage 1.** `plan_windows` with purpose `EVALUATION`, planning start
2026-08-01T00:00:00Z and limit 30, on the trial datasets. Plan ID
`sha256:07f056af394faf982187e5c5211acdec391edf5b509e50fc0109b5dc24adda34`. The
planner considered 42 candidates, from 2026-08-01 17:00 to 2026-08-31 00:00. It
selected 17 and excluded 25: 22 as `BASELINE_NOT_AUTHORIZED` (each a risk-stage
rejection, `NON_POSITIVE_TARGET_REWARD`) and 3 as `OVERLAPPING_CANDIDATE`. The
selected signals run from 2026-08-01 19:00 to 2026-08-31 00:00. August had only 17
authorized windows, so stage 1 was all of them, ADR-021's "or all of them if
fewer". The run cap was 33 research runs at an estimated 0.80 USD per window, a
projected total of 26.41 USD at the cap (0.01 USD spent before stage 1 plus
33 × 0.80). Stage 1 used 17 runs.

**Futility.** Stage 1's leans were `AGAINST` 12 and `BALANCED` 5, with 0 for the
other three values. The largest share, 12 of 17 (70.6%), is below 80%, which would
need 14 of 17. The futility rule was not met, so stage 2 was declared.

**Stage 2.** `declare-stage2` fixed N2, the plans and the caps once, in
`stage2.json`. The spend to `stage1-end` was 10.72 USD, leaving 39.28 USD. At
stage 1's measured cost of 0.63 USD per window (10.71 USD over 17 windows) that
affords 62 windows, so N2 = 30, the smaller of 30 and 62, and the retry allowance
is 3. No August window remained after stage 1, so part a had no windows and all 30
came from part b, planned from 2026-10-17T00:00:00Z on the new download, with a cap
of 33 runs. The projected total at the caps was 31.51 USD (10.72 + 33 × 0.63).

| Item | Value |
|---|---|
| Stage 1 cap and estimate | 33 runs at 0.80 USD per window |
| Spent before stage 1 | 0.01 USD |
| Stage 1 projected total at the cap | 26.41 USD |
| Spent to stage1-end | 10.72 USD |
| Remaining | 39.28 USD |
| Stage 1 measured cost per window | 10.71 / 17 = 0.63 USD |
| Windows affordable | 62 |
| N2 | 30 |
| Retry allowance | 3 |
| August windows after stage 1 (part a) | 0 |
| Part b windows and cap | 30, cap 33 |
| Stage 2 projected total at the caps | 31.51 USD |

Part b's plan ID is
`sha256:42efdc9da3a95256523a37e0620091da04002c1413eaaa2a35bcf57be474030c`. The
planner considered 55 candidates, from 2026-10-17 16:00 to 2026-11-05 15:00, and
stopped at its 30th selection. It excluded 25: 10 as `BASELINE_NOT_AUTHORIZED`
(each a risk-stage rejection, `NON_POSITIVE_TARGET_REWARD`) and 15 as
`OVERLAPPING_CANDIDATE`. The selected signals run from 2026-10-17 22:00 to
2026-11-05 15:00. Stage 2b used 30 runs.

**Signal hours.** 47 windows from 32 signal hours (stage 1: 14, stage 2b: 18). 13 hours fired on more than one pair (11 on 2, 2 on 3), holding 28 windows. Every window had the same exit reason in 12 of those 13 hours; the pairs' leans differed in 7.

| Stage | Signal (UTC) | Pairs | Exits | Leans |
|---|---|---|---|---|
| 1 | 2026-08-26 15:00 | BTC-USD, SOL-USD | TIME, TARGET | `AGAINST`, `AGAINST` |
| 1 | 2026-08-28 16:00 | BTC-USD, ETH-USD | STOP, STOP | `AGAINST`, `BALANCED` |
| 1 | 2026-08-31 00:00 | ETH-USD, SOL-USD | TIME, TIME | `AGAINST`, `BALANCED` |
| 2b | 2026-10-17 22:00 | ETH-USD, SOL-USD | TARGET, TARGET | `AGAINST`, `AGAINST` |
| 2b | 2026-10-18 20:00 | ETH-USD, SOL-USD | TARGET, TARGET | `AGAINST`, `AGAINST` |
| 2b | 2026-10-22 09:00 | BTC-USD, ETH-USD | TIME, TIME | `AGAINST`, `AGAINST` |
| 2b | 2026-10-24 01:00 | BTC-USD, ETH-USD | TIME, TIME | `BALANCED`, `AGAINST` |
| 2b | 2026-10-24 15:00 | ETH-USD, SOL-USD | STOP, STOP | `AGAINST`, `AGAINST` |
| 2b | 2026-10-29 02:00 | BTC-USD, ETH-USD, SOL-USD | TIME, TIME, TIME | `AGAINST`, `AGAINST`, `AGAINST` |
| 2b | 2026-10-30 00:00 | BTC-USD, ETH-USD | STOP, STOP | `BALANCED`, `AGAINST` |
| 2b | 2026-11-02 18:00 | ETH-USD, SOL-USD | TIME, TIME | `BALANCED`, `AGAINST` |
| 2b | 2026-11-04 03:00 | BTC-USD, ETH-USD, SOL-USD | TARGET, TARGET, TARGET | `AGAINST`, `BALANCED`, `AGAINST` |
| 2b | 2026-11-05 13:00 | BTC-USD, ETH-USD | STOP, STOP | `AGAINST`, `BALANCED` |

## Results

Primary measure, Spearman's rank correlation between score and outcome over every
included window of both stages, with ADR-021's one-sided permutation test:

| Item | Value |
|---|---|
| Windows | 47 |
| Included | 47 |
| Excluded | 0 |
| Baseline status | CLOSED 47 |
| Spearman rho | −0.085206 |
| Permutations at least as large | 7,160 of 10,000 |
| p | 7161/10001 ≈ 0.716028 |
| Result | no evidence that research ranks better than chance |

Secondary measures, descriptive only, per lean value and per decision. Mean and
median are baseline net P&L in USD; the win rate counts windows with positive net
P&L.

| Group | Value | Windows | Mean | Median | Win rate |
|---|---|---|---|---|---|
| Lean | `STRONGLY_AGAINST` | 0 | – | – | – |
| Lean | `AGAINST` | 36 | −28.4990 | −29.7544 | 11/36 |
| Lean | `BALANCED` | 11 | −36.4019 | −36.0955 | 2/11 |
| Lean | `FOR` | 0 | – | – | – |
| Lean | `STRONGLY_FOR` | 0 | – | – | – |
| Decision | `TRADE` | 0 | – | – | – |
| Decision | `NO_TRADE` | 36 | −28.4990 | −29.7544 | 11/36 |
| Decision | `ABSTAIN` | 11 | −36.4019 | −36.0955 | 2/11 |

Leans and decisions per stage:

| Stage | Windows | `STRONGLY_AGAINST` | `AGAINST` | `BALANCED` | `FOR` | `STRONGLY_FOR` | `TRADE` | `NO_TRADE` | `ABSTAIN` |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 17 | 0 | 12 | 5 | 0 | 0 | 0 | 12 | 5 |
| 2b | 30 | 0 | 24 | 6 | 0 | 0 | 0 | 24 | 6 |
| All | 47 | 0 | 36 | 11 | 0 | 0 | 0 | 36 | 11 |

Every `NO_TRADE` stated `AGAINST` and every `ABSTAIN` stated `BALANCED`.
Confidence, which rates the quality of the research rather than the direction of
the evidence:

| Decision | Windows | Range | Values |
|---|---|---|---|
| `NO_TRADE` | 36 | 40 to 66 | 55 in 18, 58 in 5, 52 in 4, 45 in 2, 50 in 2, 60 in 2, 40 in 1, 62 in 1, 66 in 1 |
| `ABSTAIN` | 11 | 30 to 35 | 35 in 7, 30 in 1, none in 3 |

Every window in plan order, stage 1 then stage 2b. The outcome is the baseline's
realized net P&L, rounded half-even to cents:

| Stage | Signal (UTC) | Pair | Decision | Confidence | Lean | Score | Exit | Baseline net P&L (USD) |
|---|---|---|---|---|---|---|---|---|
| 1 | 2026-08-01 19:00 | ETH-USD | `ABSTAIN` | – | `BALANCED` | 0 | TIME | −22.92 |
| 1 | 2026-08-06 10:00 | SOL-USD | `NO_TRADE` | 55 | `AGAINST` | −1 | TIME | −45.07 |
| 1 | 2026-08-06 19:00 | SOL-USD | `NO_TRADE` | 50 | `AGAINST` | −1 | TIME | −56.30 |
| 1 | 2026-08-10 16:00 | SOL-USD | `NO_TRADE` | 52 | `AGAINST` | −1 | TIME | −21.05 |
| 1 | 2026-08-11 15:00 | SOL-USD | `NO_TRADE` | 55 | `AGAINST` | −1 | TARGET | +6.81 |
| 1 | 2026-08-11 19:00 | BTC-USD | `NO_TRADE` | 55 | `AGAINST` | −1 | TIME | −19.43 |
| 1 | 2026-08-12 15:00 | SOL-USD | `NO_TRADE` | 55 | `AGAINST` | −1 | TIME | −14.90 |
| 1 | 2026-08-23 05:00 | ETH-USD | `ABSTAIN` | 30 | `BALANCED` | 0 | TARGET | +32.46 |
| 1 | 2026-08-25 21:00 | ETH-USD | `NO_TRADE` | 45 | `AGAINST` | −1 | TIME | +9.58 |
| 1 | 2026-08-26 15:00 | BTC-USD | `NO_TRADE` | 50 | `AGAINST` | −1 | TIME | −2.37 |
| 1 | 2026-08-26 15:00 | SOL-USD | `NO_TRADE` | 55 | `AGAINST` | −1 | TARGET | +18.23 |
| 1 | 2026-08-28 10:00 | BTC-USD | `NO_TRADE` | 55 | `AGAINST` | −1 | STOP | −100.00 |
| 1 | 2026-08-28 16:00 | BTC-USD | `NO_TRADE` | 55 | `AGAINST` | −1 | STOP | −100.00 |
| 1 | 2026-08-28 16:00 | ETH-USD | `ABSTAIN` | 35 | `BALANCED` | 0 | STOP | −99.93 |
| 1 | 2026-08-28 19:00 | SOL-USD | `ABSTAIN` | 35 | `BALANCED` | 0 | TIME | −3.10 |
| 1 | 2026-08-31 00:00 | ETH-USD | `NO_TRADE` | 55 | `AGAINST` | −1 | TIME | −4.67 |
| 1 | 2026-08-31 00:00 | SOL-USD | `ABSTAIN` | – | `BALANCED` | 0 | TIME | −1.56 |
| 2b | 2026-10-17 22:00 | ETH-USD | `NO_TRADE` | 52 | `AGAINST` | −1 | TARGET | +44.04 |
| 2b | 2026-10-17 22:00 | SOL-USD | `NO_TRADE` | 55 | `AGAINST` | −1 | TARGET | +45.90 |
| 2b | 2026-10-18 20:00 | ETH-USD | `NO_TRADE` | 58 | `AGAINST` | −1 | TARGET | +78.83 |
| 2b | 2026-10-18 20:00 | SOL-USD | `NO_TRADE` | 66 | `AGAINST` | −1 | TARGET | +56.43 |
| 2b | 2026-10-22 03:00 | SOL-USD | `NO_TRADE` | 52 | `AGAINST` | −1 | STOP | −99.65 |
| 2b | 2026-10-22 09:00 | BTC-USD | `NO_TRADE` | 55 | `AGAINST` | −1 | TIME | −41.10 |
| 2b | 2026-10-22 09:00 | ETH-USD | `NO_TRADE` | 55 | `AGAINST` | −1 | TIME | −28.58 |
| 2b | 2026-10-23 23:00 | SOL-USD | `NO_TRADE` | 55 | `AGAINST` | −1 | TIME | −25.78 |
| 2b | 2026-10-24 01:00 | BTC-USD | `ABSTAIN` | – | `BALANCED` | 0 | TIME | −36.10 |
| 2b | 2026-10-24 01:00 | ETH-USD | `NO_TRADE` | 55 | `AGAINST` | −1 | TIME | −30.93 |
| 2b | 2026-10-24 15:00 | ETH-USD | `NO_TRADE` | 58 | `AGAINST` | −1 | STOP | −99.76 |
| 2b | 2026-10-24 15:00 | SOL-USD | `NO_TRADE` | 58 | `AGAINST` | −1 | STOP | −99.57 |
| 2b | 2026-10-26 02:00 | ETH-USD | `NO_TRADE` | 58 | `AGAINST` | −1 | TIME | −33.10 |
| 2b | 2026-10-26 04:00 | SOL-USD | `NO_TRADE` | 60 | `AGAINST` | −1 | TARGET | +5.27 |
| 2b | 2026-10-29 02:00 | BTC-USD | `NO_TRADE` | 40 | `AGAINST` | −1 | TIME | −55.55 |
| 2b | 2026-10-29 02:00 | ETH-USD | `NO_TRADE` | 55 | `AGAINST` | −1 | TIME | −43.92 |
| 2b | 2026-10-29 02:00 | SOL-USD | `NO_TRADE` | 55 | `AGAINST` | −1 | TIME | −37.21 |
| 2b | 2026-10-29 23:00 | SOL-USD | `NO_TRADE` | 58 | `AGAINST` | −1 | STOP | −100.00 |
| 2b | 2026-10-30 00:00 | BTC-USD | `ABSTAIN` | 35 | `BALANCED` | 0 | STOP | −100.12 |
| 2b | 2026-10-30 00:00 | ETH-USD | `NO_TRADE` | 55 | `AGAINST` | −1 | STOP | −100.36 |
| 2b | 2026-11-01 23:00 | SOL-USD | `NO_TRADE` | 60 | `AGAINST` | −1 | TARGET | +20.49 |
| 2b | 2026-11-02 18:00 | ETH-USD | `ABSTAIN` | 35 | `BALANCED` | 0 | TIME | −48.52 |
| 2b | 2026-11-02 18:00 | SOL-USD | `NO_TRADE` | 62 | `AGAINST` | −1 | TIME | −36.94 |
| 2b | 2026-11-02 21:00 | BTC-USD | `ABSTAIN` | 35 | `BALANCED` | 0 | TIME | −49.01 |
| 2b | 2026-11-04 03:00 | BTC-USD | `NO_TRADE` | 55 | `AGAINST` | −1 | TARGET | +32.02 |
| 2b | 2026-11-04 03:00 | ETH-USD | `ABSTAIN` | 35 | `BALANCED` | 0 | TARGET | +28.55 |
| 2b | 2026-11-04 03:00 | SOL-USD | `NO_TRADE` | 55 | `AGAINST` | −1 | TARGET | +50.48 |
| 2b | 2026-11-05 13:00 | BTC-USD | `NO_TRADE` | 52 | `AGAINST` | −1 | STOP | −100.00 |
| 2b | 2026-11-05 13:00 | ETH-USD | `ABSTAIN` | 35 | `BALANCED` | 0 | STOP | −100.17 |
| 2b | 2026-11-05 15:00 | SOL-USD | `NO_TRADE` | 45 | `AGAINST` | −1 | STOP | −97.81 |

Evaluation IDs: stage 1
`sha256:7a17a07360c4841a6a80edc124c51d23bf5004b21cc0e0f7db9b8e1299cb7080`, stage 2b
`sha256:9e945206b60329a6f6660e8dfaebfce855ebf7bb02e34340106cca1e86f5a0ef`.

## Interpretation

- p ≈ 0.716 gives no evidence that research ranks better than chance. The observed
  correlation is slightly negative: `AGAINST` windows averaged −28.50 USD and
  `BALANCED` windows −36.40 USD. The test is one-sided, as pre-registered. This
  record runs no other test and claims no ranking in either direction.
- The lean took two values and mirrored the decision in every window. ADR-020
  allows `ABSTAIN` with any lean and `NO_TRADE` with `STRONGLY_AGAINST`; neither
  occurred, and no `FOR` or `STRONGLY_FOR` appeared. The trial therefore compared
  `NO_TRADE` windows with `ABSTAIN` windows, and the lean added no grading beyond
  the decision.
- Research declined every candidate (0 `TRADE`), as in trials 5 to 8, where it
  declined all 42 distinct candidates it assessed, in 52 runs (ADR-020). Selection
  quality remains unmeasured.
- A null result does not show that research carries no information (ADR-021).
  With 47 windows, only a fairly strong ranking would show.
- Results are not pooled with earlier trials.

## Baseline

These baseline results are descriptive only. Every one of the 47 baseline
positions closed.

| Windows | Wins | Mean | Median | Total (USD) | Exact total (USD) |
|---|---|---|---|---|---|
| 47 | 13/47 | −30.3486 | −30.9270 | −1,426.38 | −1,426.3847061854164 |

By exit reason:

| Exit | Windows | Wins | Mean | Median | Total (USD) |
|---|---|---|---|---|---|
| TIME | 23 | 1 | −28.1971 | −30.9270 | −648.53 |
| TARGET | 12 | 12 | 34.9599 | 32.2410 | 419.52 |
| STOP | 12 | 0 | −99.7808 | −99.9997 | −1,197.37 |

By stage:

| Stage | Windows | Signals (UTC) | Leans | Wins | Mean | Median | Exits | Total (USD) |
|---|---|---|---|---|---|---|---|---|
| 1 | 17 | 2026-08-01 19:00 to 2026-08-31 00:00 | `AGAINST` 12, `BALANCED` 5 | 4/17 | −24.9547 | −14.8992 | TIME 11, TARGET 3, STOP 3 | −424.23 |
| 2b | 30 | 2026-10-17 22:00 to 2026-11-05 15:00 | `AGAINST` 24, `BALANCED` 6 | 9/30 | −33.4052 | −37.0732 | TIME 12, TARGET 9, STOP 9 | −1,002.15 |

By lean and exit reason:

| Lean | Windows | TIME | TARGET | STOP | Total (USD) |
|---|---|---|---|---|---|
| `AGAINST` | 36 | 17 | 10 | 9 | −1,025.96 |
| `BALANCED` | 11 | 6 | 2 | 3 | −400.42 |

Means and medians rounded to cents, as the text quotes them:

| Group | Mean (USD) | Median (USD) |
|---|---|---|
| `AGAINST` | −28.50 | −29.75 |
| `BALANCED` | −36.40 | −36.10 |
| `NO_TRADE` | −28.50 | −29.75 |
| `ABSTAIN` | −36.40 | −36.10 |
| TIME exits | −28.20 | −30.93 |
| TARGET exits | 34.96 | 32.24 |
| STOP exits | −99.78 | −100.00 |
| Stage 1 | −24.95 | −14.90 |
| Stage 2b | −33.41 | −37.07 |
| All | −30.35 | −30.93 |

Stop exits lost about the full 1% risk of 100 USD (mean −99.78), and target exits
gained 34.96 on average. The
[baseline strategy study](v2-baseline-strategy-study.md#by-exit-reason) found the
same pattern: stop exits averaged −99.87 to −99.99 USD and target exits +26.20 to
+37.46 in its four variants.

The planning rule biases this sample. Its `OVERLAPPING_CANDIDATE` check excludes a
candidate when another scanner candidate of the same pair is observed inside its
window, so it reads later candidates and is not causal
([specification](../v1-specification.md#evaluation-window-planning)). On the
study's development data it flattered the 6-bar control: −30.52 USD per trade
under the rule against −37.61 in the causal per-pair sample
([trading-logic exploration](v2-trading-logic-exploration.md#interpretation)).
Baseline totals here describe the planned windows, not the strategy. The rule
reads no research output, so it cannot favour either lean, but it shapes which
candidates the ranking was measured on. It excluded 3 candidates in stage 1 and 15
in stage 2b.

## Rehearsal

A one-window `PLUMBING` rehearsal on contaminated June data checked the live lean
schema and prompt-cache reads before the trial. It is not part of the results.
Trial `v2-ranking-1-rehearsal`, `claude-haiku-5-5` at effort `high`, cap 1,
planning start 2026-06-15T00:00:00Z. It selected BTC-USD at 2026-06-16 15:00 (plan
ID `sha256:5d40ad6fc18703d27e9efd5231adafa60f5729f8b2a5746dbeb6472a8d8f6910`), the
same contaminated window as the
[trial-8 rehearsal](v2-july-2026-trial-8.md#rehearsal). The run completed with a
model assessment, `ABSTAIN` with lean `AGAINST` and confidence 30, after 8 model
calls and 4 of 9 tool attempts (`lower_band_crossings`, `trend`,
`candle_structure`, `volume_context`). It used 39,601 input, 8,391 cache-write,
15,464 cache-read and 18,309 output tokens; the cache reads show that prompt caching
worked. Reproduction matched (`evaluation_id`
`sha256:357ef297e40c7a3ecd0ddb70dfc2e36d04771a9bfe7266f231924a2ec70d4192`,
reproduced as
`sha256:f8407f597662fe18c3251fbc850c6f21ddecd0d88d23c594eef20f805276499a`). It is
the only one of the 48 runs in which an `ABSTAIN` did not state `BALANCED`.

## Reproduction and cost

A separate process with `ANTHROPIC_API_KEY` unset ran `trial_run.py reproduce` for
the rehearsal, stage 1 and stage 2b. Each printed `metrics equal: True` and
`candidates equal: True`. The reproduced `evaluation_id`s differ by design, because
frozen replays bind new run IDs.

| Run | Original `evaluation_id` | Reproduced `evaluation_id` |
|---|---|---|
| Rehearsal | `sha256:357ef297e40c7a3ecd0ddb70dfc2e36d04771a9bfe7266f231924a2ec70d4192` | `sha256:f8407f597662fe18c3251fbc850c6f21ddecd0d88d23c594eef20f805276499a` |
| Stage 1 | `sha256:7a17a07360c4841a6a80edc124c51d23bf5004b21cc0e0f7db9b8e1299cb7080` | `sha256:39c119450cc160ba435bd0d58f5f484538bacf1bb334ca2ebc27a867df2ace53` |
| Stage 2b | `sha256:9e945206b60329a6f6660e8dfaebfce855ebf7bb02e34340106cca1e86f5a0ef` | `sha256:faf5c28793a1568fd493579d056c91f7efee439b73a525c153df810775069e26` |

`identity` exited 0 and reported all 48 stored research runs (1 rehearsal, 17 in
stage 1, 30 in stage 2b) as matching the run commit's code identity, its git
commit and source digest.

This record's checks ran from the main checkout at the run commit, with
`ANTHROPIC_API_KEY` unset, on copies. `~/crypteo-eval/ranking-1-record/root/`
held copies of `datasets.json`, `data/`, `data-ranking-1/` and `ranking-1/`, and
`CRYPTEO_EVAL_ROOT` pointed there for every `trial_run.py` command. SHA-256 of the
originals in `~/crypteo-eval/ranking-1/`, before copying and after all checks
(identical):

| Original | SHA-256 |
|---|---|
| `trial.sqlite` | `a3efb11a42fcadc6c95654997ebf5f1a86a691840799ca1b535feb9f5ade06d3` |
| `rehearsal.sqlite` | `691695c5b21489c863ac574f554a585c67dfd9ad1b43390edc5ca286ee94fdeb` |
| `analysis.json` | `99c32c212de8d9ae406c2c8f4c0f1549e177d23094a3bc6bd9ddc6a9990bf3be` |
| `balances.json` | `fb96241d39f77c4a791432e775b2cc7421caaa32d63827fe0dc676ccb0d48bb1` |
| `stage2.json` | `00f900906f11bfa55d0f82067bf034fe3788b21b8c5a08e58c88b5b291e98269` |
| `stage2-datasets.json` | `cdf50fd4863f252e0ffc1f3e55f92a9dc8f3eacdd9747cf834d60bcc680d9f6d` |
| `rehearsal/` (digest of its sorted per-file list, 6 files) | `ce5b1118b3f6ea620b238944aaa6e02c5bc49fece0e42ab77fb3a93b893d53fb` |
| `stage1/` (digest of its sorted per-file list, 54 files) | `e3ef67c10fdded929da585e56dc45aac51dd84dbed46eb4476a8d0d5135fde19` |
| `stage2b/` (digest of its sorted per-file list, 94 files) | `75bf0de0cd1346feb5780562110beeb3734059d4bdab4e8da68478611355dd4f` |

The per-file lists of all 161 files under `ranking-1/` are
`~/crypteo-eval/ranking-1-record/checksums-before.txt` and `checksums-after.txt`,
and the two files are byte-identical. Each directory digest follows trial 8's
convention: from `~/crypteo-eval`, `find ranking-1/stage1 -type f -exec shasum -a
256 {} + | sort | shasum -a 256`, with paths as `ranking-1/stage1/…` and lines
sorted whole. Two of the 161 files are Finder metadata, not trial artifacts:
`ranking-1/.DS_Store` and `ranking-1/stage2b/.DS_Store`; the `stage2b/` digest
includes the latter. No store had `-wal` or `-shm` files.

Saved outputs, all in `~/crypteo-eval/ranking-1-record/`:

| File | Content | SHA-256 |
|---|---|---|
| `check-data.txt` | `trial_run.py check-data` | `acf8e8fdee0a97d25074bb3eeba9d7e060e3599481b530874fd3d339b85e230b` |
| `report-rehearsal.txt` | `trial_run.py report-rehearsal` | `b9ac61acf42376da9e45044f4d4d540610f14ac20d96330e4a65404b7748d7a2` |
| `report-stage1.txt` | `trial_run.py report-stage1` | `d57d22012ed1e061aaaafc5414079d8240315d08dc33aa75d52754cdb9850edb` |
| `report-stage2.txt` | `trial_run.py report-stage2` | `d92915bca916ceba764546db29964074a5e2352ace3b46f42bcecaf124b0ff0c` |
| `futility.txt` | `trial_run.py futility` | `5c0551444b9d35d895aeb651783eb592aa75b4233ad42b136610121eb63a68a6` |
| `reproduce-rehearsal.txt` | `trial_run.py reproduce rehearsal` | `10b83248505981d41c371257b8e1d00e8176a67bd0bb04f164970895f443b711` |
| `reproduce-stage1.txt` | `trial_run.py reproduce stage1` | `ed00e471071c314c8d6528ea255d784cceea6d5577fab4b64fb911790e00d4c2` |
| `reproduce-stage2b.txt` | `trial_run.py reproduce stage2b` | `cf2b9fe9aba620cb3be5717f9eb993a3396c0b99c088d45a6474ba46c622fdba` |
| `identity.txt` | `trial_run.py identity` | `7721ec0054fc4a0d1fac5fe680393e59b58a990ffe866e859f4ce5d8e0d7c31f` |
| `plan-stage1.txt` | `trial_run.py plan-stage1` | `8c01e6346cce163eaffa5b6daf09bc08fdee24146cbc1aa2d8f834912afaf17a` |
| `test-trial-run.txt` | `pytest test_trial_run.py` | `d971a7114521f00ae56ef41d2214a5cc5dbd55b1368aad740afa383960049c94` |
| `extract_runs.py` | Plan and research-run extraction script | `d950217fe5267175a18afc6ebe84018f095be7eb67fd2588f5cdb52fa44483a6` |
| `extract-runs.txt` | Its output | `81d6f669ec1e8f703a131ea2f07dafce5ed1b2cfe029362b3b91625d9f2e983b` |
| `coverage_stage2.py` | Stage-2b download coverage script | `ee5cb57c43a726167fd82599edd761987bf418aa803cdf12cd10044e5173e62a` |
| `stage2-coverage.txt` | Its output | `33f292882dbdc3e34886c5f0888ce201ed607d52c7ac2c0e5189b1c14cb4832b` |
| `checksums-before.txt` | Per-file SHA-256 of the originals, before copying | `07045d6af973d9370bc1570ae18da69ea74104d75b0aa7f1f5880b0a783c6cb6` |
| `checksums-after.txt` | The same, after all checks | `07045d6af973d9370bc1570ae18da69ea74104d75b0aa7f1f5880b0a783c6cb6` |
| `directory-digests-before.txt` | Directory digests, before copying | `428931973ce1756a1c05de2e3c382c88fd5c52da2006f4b84b73b7e597cac760` |
| `directory-digests-after.txt` | The same, after all checks | `428931973ce1756a1c05de2e3c382c88fd5c52da2006f4b84b73b7e597cac760` |
| `record_tables.py` | The table script | `643066f511fb7f98baf3591cf1f1bb64ed1ca58f634fb0cda3926d78e04c18f9` |
| `tables.md` | Its output | `bbd9b284d9b1fbdc6b268e00ca1660b5db97c4d69b081308817b7710f6f9a9e0` |

`extract_runs.py` reads plan fields and research-run fields from the copies and no
outcome. `record_tables.py` uses the standard library only. It exits unless
`analysis.json`, `balances.json`, `stage2.json` and `extract-runs.txt` have their
pinned SHA-256, re-derives every outcome figure from `analysis.json`'s per-window
rows, including the correlation and the permutation count, and asserts that each
matches the `primary` and `secondary` blocks where they hold it. This record's
outcome tables and the ledger and cost tables are pasted from its output,
`tables.md`.

Every one of the 48 runs completed with a model assessment (status `COMPLETED`,
origin `MODEL`), and no tool attempt was rejected or served from cache. In stage 1,
15 runs made 13 model calls with 9 of 9 tool attempts and 2 made 12 calls with 8.
In stage 2b, 18 runs made 13 calls with 9 attempts, 11 made 12 calls with 8, and
ETH-USD at 2026-11-02 18:00 made 14 calls with 9 attempts. That run had the trial's
one output re-ask: its first thesis reply was rejected (`summary`, `bull_case` and
`bear_case`: text must be nonblank), and the re-asked reply was accepted. Research
attempts equalled windows in every stage (1, 17 and 30): no failures and no retries.

| Stage | Runs | Model calls | Re-asks | Tool attempts | Input | Cache write | Cache read | Output | List-price estimate (USD) | Console spend (USD) |
|---|---|---|---|---|---|---|---|---|---|---|
| Rehearsal | 1 | 8 | 0 | 4 | 39,601 | 8,391 | 15,464 | 18,309 | 0.01 | 0.01 |
| Stage 1 | 17 | 219 | 0 | 151 | 1,436,997 | 11,148 | 671,060 | 238,625 | 10.71 | 10.71 |
| Stage 2b | 30 | 380 | 1 | 259 | 2,467,771 | 14,824 | 1,183,274 | 409,729 | 18.38 | 18.38 |
| Total | 48 | 607 | 1 | 414 | 3,944,369 | 34,363 | 1,869,798 | 666,663 | 29.10 | 29.10 |

The Console balances and top-ups, in the order recorded:

| Entry | USD |
|---|---|
| rehearsal-start | 5.54 |
| topup | 15.00 |
| stage1-start | 20.53 |
| stage1-end | 9.82 |
| topup | 12.00 |
| stage2-end | 3.44 |

| Stage | Console spend (USD) | Windows | Per window (USD) |
|---|---|---|---|
| Rehearsal | 0.01 | 1 | 0.01 |
| Stage 1 | 10.71 | 17 | 0.6300 |
| Stage 2b | 18.38 | 30 | 0.6127 |
| Total | 29.10 | 48 | – |

Total spend 29.10 of 50.00 USD.

List prices are `trial_run.py`'s `PRICES`, in USD per million input, output,
cache-write and cache-read tokens: 4, 20, 5 and 0.20 for `claude-opus-5-5`, and
0.10, 0.50, 0.125 and 0.01 for `claude-haiku-5-5`. Its Haiku cache-read rate
(0.1× input) is an assumption that affects only the rehearsal's estimate. The
Console balances are the owner's readings, recorded with `trial_run.py balance` and
`topup` in `balances.json`; they are not stored artifacts. Spend is each balance
drop plus the top-ups made in between.

## Limitations

- 47 windows from 32 signal hours. Windows in the same hour across pairs have
  correlated outcomes (12 of the 13 multi-pair hours shared one exit reason), so
  the effective sample is smaller than 47, and the permutation test, which treats
  windows as exchangeable, overstates the information.
- The lean took two values and mirrored the decisions, so the trial measured
  `NO_TRADE` against `ABSTAIN`, not a graded lean.
- The planning rule's overlap check is not causal (see [Baseline](#baseline)).
- One candidate source, the lower-band crossing scanner, which the baseline study
  and the trading-logic exploration found has no edge after costs.
- August and October–November 2026 are after the model's training cutoff, but
  parametric knowledge cannot be proven point-in-time pure.
- The run script and the table script are outside the repository; this record, the
  saved outputs and their checksums are their evidence.
