# V2 no-TRADE decision diagnostic

## Summary

Across trials 5, 6 and 7, 42 opportunity-led research runs produced no `TRADE`:
34 `NO_TRADE` and 8 `ABSTAIN`. This record codes the stored stage outputs of
every run (investigation, thesis, critique, final assessment) to answer two
questions. Trade outcomes were not used. Each trial is reported separately and
never pooled.

1. **Did `NO_TRADE` rest on evidence against the long, or only on insufficient
   support?** Every one of the 34 `NO_TRADE` assessments (19, 9 and 6 by trial)
   states at least one specific bearish reading of cited evidence, so all are
   "against" under the trader-led meanings (D2). Every assessment in all 42
   runs also states insufficient or fragile support and missing evidence (D1).
   An assessment presents a single basis as deciding the outcome in 15 of 22
   trial 5 runs, 3 of 10 trial 6 runs and 3 of 10 trial 7 runs. Where it does,
   the decisive basis is evidence against the long in 8, 3 and 0 runs.
2. **Did any stated route to `TRADE` use evidence the workflow could supply at
   T?** In trials 5 and 6, none did. Every run's stated routes needed a V2
   change (a benchmark binding, class B, or a different statistic, class E) or
   were never available in V2 (classes C, D, F). In trial 7, every run named
   band width and percent_b from `bollinger_position`, a tool that was offered
   but never requested (class A). Every trial 7 run had used all 8 of its 8
   tool attempts, so that evidence was obtainable only in place of another
   request. The stored text does not show whether it would have changed any
   assessment.

Hypotheses, by trial (details in [Hypotheses](#hypotheses)):

- H1 (most `NO_TRADE` are "support only"): not supported in any trial.
- H2 (most routes need a V2 change or are never in V2): supported in trials 5
  and 6, not supported in trial 7.
- H3 (critiques challenge the bull case more than the bear case): supported in
  all three.
- H4 (`NO_TRADE` confidence varies little within a trial): supported in all
  three, taking "little" as a range of at most 10 points on the 0–100 scale
  (ranges of 10, 5 and 5 points).

The trial 5 and trial 6 records say the workflow gave research "no route to a
well-supported TRADE" ([trial 5](v2-july-2026.md), "Why research declined";
[trial 6](v2-july-2026-trial-6.md), "Why research declined"). In the stored
text of those two trials, no run states a route obtainable within the run. All
the routes those runs state are class B, C, D, E or F. The text cannot show
whether a B or E route would have led to `TRADE`.

Independent blind coding of D1, D2 and D4 agreed on every D1 basis set, D2
code and D4 code in all three trials. The decisive basis agreed in 21 of 22, 9
of 10 and 9 of 10 runs; the three disagreements are resolved below.

## What was read

### Stores and commits

Copies of each store and evaluation directory were read; the originals were
not opened. Each trial was loaded through `ResearchStore.load_terminal` from a
fresh clone at the commit below. The main venv's interpreter ran with the
clone's `src` first on `sys.path`, and `crypteo.__file__` resolved into that
clone in every case (`…/diagnostic/clones/t5|t6|t7/src/crypteo/__init__.py`).
`ANTHROPIC_API_KEY` was unset throughout. Every run loaded at its commit.

| Trial | Store | Evaluation directory | Loaded at | Run commit in trial record |
|---|---|---|---|---|
| 5 | `~/crypteo-eval/evaluation-5.sqlite` | `~/crypteo-eval/evaluation-5` (69 files) | `f76072b` | `f76072b` |
| 6 | `~/crypteo-eval/evaluation-6.sqlite` | `~/crypteo-eval/evaluation-6` (33 files) | `0588313` | `8fe2f93` |
| 7 | `~/crypteo-eval/evaluation-7.sqlite` | `~/crypteo-eval/evaluation-7` (33 files) | `a2b5eed` | `a2b5eed` |

Trial 6 ran at `8fe2f93` ([trial 6 record](v2-july-2026-trial-6.md), Run
configuration) and was loaded at `0588313`, the premise's commit, whose `src/`
is identical. `0588313` is the next commit and only adds the trial 6 record. `a2b5eed` and `HEAD` have identical `src/` and `pyproject.toml`.

SHA-256 of the originals, before copying and after all reading (identical):

| Original | SHA-256 |
|---|---|
| `evaluation-5.sqlite` | `791d0ebb40273d19f6131015bd1a5a6cd16c70a958a6605a1a79403e74c43c0d` |
| `evaluation-6.sqlite` | `fe6935a21c52e7dc58f7c24b0260169c736473af2e401f2aab4aa24bb55bcf43` |
| `evaluation-7.sqlite` | `7873c25e1f05bebfe5f89231bf6ad6997d73a74cd412ae05fa6ff83be0ad4837` |
| `evaluation-5/` (digest of its sorted per-file list) | `b4f644fb68287888f7687e356a666e4df173fd6325c5271345e19842cd150984` |
| `evaluation-6/` (digest of its sorted per-file list) | `41b4bc0d67b0cc26ed2d92c13927687f03b02730722718ab98df6af8c6641ac3` |
| `evaluation-7/` (digest of its sorted per-file list) | `a6ac97fd238ca791fa68488737b6d8a177edc10086a08a49ca2f85325ed5058a` |

The per-file lists are `~/crypteo-eval/diagnostic/checksums-before.txt` and
`checksums-after.txt`, and the two files are byte-identical. No store had
`-wal` or `-shm` files.

From each window's `research.json`, only the `RESEARCH` record's
`research_run_id` was read.

### Runs and stage outputs

| Trial | Opportunity-led runs | `NO_TRADE` | `ABSTAIN` | `TRADE` |
|---|---|---|---|---|
| 5 | 22 | 19 | 3 | 0 |
| 6 | 10 | 9 | 1 | 0 |
| 7 | 10 | 6 | 4 | 0 |

Every run is `COMPLETED` with a `MODEL` assessment. Every run stores all four
stage outputs, with the fields the task lists, at each commit:

- `InvestigationCompletion`: `summary`, `relevant_evidence_ids`,
  `remaining_gaps`.
- `ResearchThesis`: `bull_case`, `bear_case`, `supporting_evidence_ids`,
  `counterevidence_ids`, `assumptions`, `invalidation_conditions`,
  `uncertainties`.
- `Critique`: `challenged_claims`, `missing_evidence`, `contradictions`,
  `unsupported_assumptions`, `evidence_quality_concerns`,
  `unresolved_uncertainties`.
- `TradeDecision`: `outcome`, `rationale`, `supporting_evidence_ids`,
  `counterevidence_ids`, `confidence`, `key_uncertainties`, `critic_response`,
  `abstention_reasons`.

At `f76072b` and `0588313`, `InvestigationCompletion` sits at
`domain/research_workflow.py:119-122` rather than `:197-200`; its fields are
the same.

### Policy text

The opportunity-led shared policy's sentence at `f76072b`, at `0588313` and at
`a2b5eed`:

> TRADE supports further consideration; NO_TRADE rejects the candidate; ABSTAIN
> means a reliable research conclusion could not be formed.

It equals the current text (`src/crypteo/research_prompts.py:24-25`). The whole
`SHARED_POLICY` string and the four stage instructions are identical at all
three commits and at `HEAD`. The trader-led policy ("TRADE means the evidence
supports considering it; NO_TRADE means the evidence argues against it") does
not exist at `f76072b` or `0588313`. It exists at `a2b5eed`, but opportunity-led
runs do not use it. The three trials differ in tools, tool descriptions and
model views (see each trial record), not in this policy text.

### Tools per trial

Taken by script from each run's trace:

| Trial | Tools offered (offer order) | Requested in every run | Never requested | Attempts |
|---|---|---|---|---|
| 5 | `trend`, `momentum`, `volatility`, `bollinger_position`, `levels`, `volume_context`, `relative_strength` | All 7. `relative_strength` twice, both `unavailable (SOURCE_UNAVAILABLE)`; the rest `success` | none | 8 of 8 in all 22 |
| 6 | `trend`, `momentum`, `volatility`, `bollinger_position`, `levels`, `volume_context`, `candle_structure`, `higher_timeframe`, `lower_band_crossings` | The 8 tools other than `bollinger_position`, once each, all `success` | `bollinger_position` | 8 of 8 in all 10 |
| 7 | Same 9 as trial 6 | Same 8, once each, all `success` | `bollinger_position` | 8 of 8 in all 10 |

No request was rejected or served from cache. `relative_strength` was not
offered in trials 6 and 7, because those single-pair windows bind no benchmark.

### Derived files

All derived files are outside the repository, in `~/crypteo-eval/diagnostic/`:

| File | Content | SHA-256 |
|---|---|---|
| `blind-extract.json` | Per run: label (trial, signal time, pair), the four stage outputs, tools offered, tools requested with status, attempts used of budget | `7f030191d18abfcb9f51a3078b088263d56098d97a42ba3efa63025b669b97ac` |
| `builder-codes.json` | Builder's D1–D5 codes, saved before the blind pass | `dc3219b9783e96f8cc62e7fe6057316ac3d1941f792f29812dde69404a492663` |
| `reviewer-codes.txt` | Blind coder's D1, D2 and D4 output, verbatim | `9866912d06cd8c5202af3201ee5e208f635e75ea3c5eeec9501a38fd2a4ce67a` |
| `resolved-codes.json` | `builder-codes.json` with the three resolutions applied | `e3819f83d2cf4ce7aecef60e4c4cddc09276c5678057a50ae22d15c2a89e2c0c` |

Scripts in the same directory: `extract.py` (loads runs), `blind.py` (builds
the extract), `codes_to_json.py` (builder codes, with D4 derived from D3),
`agreement.py` (agreement and disagreements), `resolve.py` and `tables.py`.

The D5 recount's files (blind-d5-codes.txt, d5_agreement.py and the appended hashes-pre-blind.txt) are listed with their checksums in D5 blind recount.

## Method

**Reading (R1).** The originals were checksummed, then copied, and only the
copies were opened. Each trial was loaded through its own clone at its commit.
`extract.py` refuses to run with `ANTHROPIC_API_KEY` set. It asserts that
`crypteo.__file__` lies in the clone. It reads only the `research_run_id` of
the `RESEARCH` record in each window's `research.json`. The main checkout was
not checked out, branched or committed, and no worktree was created.

**Outcome blindness (R2).** Coding used only the four stage outputs, plus the
tool facts above for D3 class A. No baseline file, P&L, exit or replay
financial record was opened. Before coding, a search of the trial records for
commit hashes printed their summary lines, which include aggregate
baseline P&L. No per-run outcome was seen. This record reports no trade
outcome and makes no comparison with one.

**Mechanical fields (R3)** come from `data/t5|t6|t7.json`, written by
`extract.py`. They are trial, signal time (the opportunity's `observed_at`),
pair, outcome, confidence, abstention reasons, and the counts of supporting and
counterevidence IDs in the thesis and in the assessment.

**Coded judgments (R4)**, with the category definitions exactly as specified:

- **D1. Basis of the decision.** Mark every basis the assessment `rationale`
  and `key_uncertainties` state:
  - (a) evidence against the long: a specific bearish reading of cited
    evidence;
  - (b) insufficient or fragile support: a weak, small-sample or unconfirmed
    edge;
  - (c) missing evidence;
  - (d) conflicting evidence.

  Then mark the decisive basis only if the assessment itself presents one
  reason as deciding the outcome; otherwise "none stated".
- **D2. Fit with the trader-led meanings** (`NO_TRADE` runs only): "against" if
  the assessment states at least one specific piece of cited evidence that
  argues against the long; otherwise "support only".
- **D3. Stated routes to TRADE.** List every piece of evidence or condition
  that the assessment, critique or investigation says is missing, or would
  strengthen the long or change the assessment. Classify each:
  - A. obtainable in the same run from an offered tool that was not requested;
  - B. obtainable in V2 only with a different binding or argument (for example
    relative strength without a bound benchmark);
  - C. candles after T (post-trigger confirmation), never available at decision
    time;
  - D. outside V2's evidence scope (derivatives, order book, funding, news,
    on-chain, macro, sentiment);
  - E. a different statistic of data already available (larger or longer
    samples, other conditioning, significance, dispersion), needing a tool or
    policy change;
  - F. other.
- **D4. Achievability of a route to TRADE**, per run, from D3:
  - "within the run" if any route is A;
  - else "with a V2 change" if any is B or E;
  - else "never in V2" (only C, D or F).
- **D5. Direction of the critique's challenges.** Count the critique's
  challenged claims and contradictions that weaken the bull case and those
  that weaken the bear case. Count only items that clearly do one or the other;
  others are uncounted.

No category was added. No item was coded "other" outside D3's own class F.

The builder applied these conventions. The blind coder was not given them; it
had only the definitions above.

- D1 decisive. A basis is decisive when the assessment's explicit
  outcome-justifying sentences ("because", "so", "rests on", "which is why")
  name that one basis. These sentences were read in `rationale` and
  `critic_response`. When they join two bases, the code is "none stated".
  For `ABSTAIN`, the reason given for not issuing `NO_TRADE` was read as the
  deciding reason.
- D1 (d) is marked when the rationale or a key uncertainty calls the evidence
  mixed or conflicting. One example is a key uncertainty over whether the
  daily or the hourly trend dominates.
- D3 lists each distinct route once per run, drawn from the investigation's
  gaps, the critique's fields and the assessment.
- Class A was taken as the user specified. An offered tool that was not
  requested counts as obtainable in the same run even when the budget was
  spent. Each such route is marked budget-only (†) when the run used all 8
  attempts. All A routes are budget-only.
- Requests for the definition or documentation of evidence already supplied
  are class F, not A. Examples are window definitions, close-based versus
  intrabar levels, and the Bollinger period and multiplier. Unconfirmed data
  quality and single-source data are also F.
- In trial 5, higher-timeframe views, candle structure and base rates come
  from candles the run could see but no tool computed, so they are class E.
  Taker-side or signed order flow is class D. Up- versus down-bar volume and
  time-of-day volume baselines are class E. Spread, slippage and transaction
  cost context are class D (liquidity).
- D5. An item counts as weakening the bull case when it undercuts a bull-case
  claim or a supporting classification, or when it shows the thesis framing a
  LONG against its own evidence. An item about an evidence ID listed on both
  sides counts on neither.

**Independent coding (R5).** `blind.py` built `blind-extract.json`. Before the
blind pass, `builder-codes.json` was saved and its checksum written to
`hashes-pre-blind.txt`; the blind output's checksum was appended later. A fresh
reviewer agent got the extract, the definitions above verbatim, and an
instruction to code D1, D2 and D4 for all 42 runs. It was told not to open any
other file in `~/crypteo-eval/diagnostic/` or any trial record. It stated that
it opened no other file there. Its output was saved verbatim to
`reviewer-codes.txt`, and its checksum was recorded before any comparison.
`agreement.py` computed agreement from the two saved files. Each disagreement
was resolved by re-reading the stage text. Neither saved file was edited; the
resolutions are in `resolved-codes.json`. D3 and D5 were coded by the builder
only; D5 was later recounted blind (see D5 blind recount).

## Results per trial

Tables use the resolved codes. D1 bases: a = against, b = insufficient or
fragile support, c = missing, d = conflicting. † marks a D4 "within the run"
that rests only on budget-only class A routes. Confidence is the model's
self-assessment (0–100); `ABSTAIN` carries none.

### Trial 5

| Signal time (UTC) | Pair | Outcome | Confidence | Abstention reasons | Thesis support / counter IDs | Assessment support / counter IDs | D1 bases | D1 decisive | D2 | D4 |
|---|---|---|---|---|---|---|---|---|---|---|
| 07-05 02:00 | SOL-USD | `NO_TRADE` | 55 | — | 4 / 4 | 4 / 2 | a,b,c | none stated | against | with a V2 change |
| 07-06 13:00 | BTC-USD | `NO_TRADE` | 55 | — | 3 / 5 | 5 / 2 | a,b,c | a | against | with a V2 change |
| 07-06 13:00 | ETH-USD | `NO_TRADE` | 55 | — | 2 / 4 | 4 / 2 | a,b,c | a | against | with a V2 change |
| 07-06 13:00 | SOL-USD | `ABSTAIN` | — | `INSUFFICIENT_EVIDENCE`, `UNAVAILABLE_EVIDENCE` | 4 / 5 | 1 / 5 | a,b,c | c | — | with a V2 change |
| 07-08 02:00 | ETH-USD | `NO_TRADE` | 58 | — | 3 / 3 | 4 / 2 | a,b,c | none stated | against | with a V2 change |
| 07-08 02:00 | SOL-USD | `NO_TRADE` | 60 | — | 3 / 3 | 3 / 3 | a,b,c | a | against | with a V2 change |
| 07-08 09:00 | BTC-USD | `NO_TRADE` | 55 | — | 2 / 4 | 3 / 1 | a,b,c | b | against | with a V2 change |
| 07-10 18:00 | SOL-USD | `ABSTAIN` | — | `INSUFFICIENT_EVIDENCE`, `UNAVAILABLE_EVIDENCE` | 4 / 3 | 3 / 3 | a,b,c | c | — | with a V2 change |
| 07-12 00:00 | BTC-USD | `NO_TRADE` | 55 | — | 2 / 4 | 4 / 1 | a,b,c | a | against | with a V2 change |
| 07-12 00:00 | SOL-USD | `NO_TRADE` | 55 | — | 2 / 4 | 3 / 3 | a,b,c | none stated | against | with a V2 change |
| 07-12 07:00 | BTC-USD | `ABSTAIN` | — | `INSUFFICIENT_EVIDENCE`, `UNAVAILABLE_EVIDENCE` | 4 / 2 | 3 / 3 | a,b,c | c | — | with a V2 change |
| 07-13 02:00 | BTC-USD | `NO_TRADE` | 55 | — | 2 / 4 | 4 / 2 | a,b,c | b | against | with a V2 change |
| 07-13 04:00 | ETH-USD | `NO_TRADE` | 60 | — | 2 / 4 | 4 / 2 | a,b,c | a | against | with a V2 change |
| 07-13 04:00 | SOL-USD | `NO_TRADE` | 52 | — | 2 / 4 | 2 / 3 | a,b,c | none stated | against | with a V2 change |
| 07-13 18:00 | ETH-USD | `NO_TRADE` | 58 | — | 1 / 5 | 4 / 1 | a,b,c | none stated | against | with a V2 change |
| 07-13 18:00 | SOL-USD | `NO_TRADE` | 62 | — | 2 / 4 | 4 / 2 | a,b,c | a | against | with a V2 change |
| 07-16 08:00 | BTC-USD | `NO_TRADE` | 60 | — | 2 / 4 | 5 / 1 | a,b,c | b | against | with a V2 change |
| 07-16 08:00 | SOL-USD | `NO_TRADE` | 55 | — | 2 / 4 | 4 / 1 | a,b,c | a | against | with a V2 change |
| 07-16 13:00 | ETH-USD | `NO_TRADE` | 58 | — | 2 / 5 | 3 / 1 | a,b,c | b | against | with a V2 change |
| 07-17 06:00 | BTC-USD | `NO_TRADE` | 52 | — | 2 / 5 | 3 / 1 | a,b,c | none stated | against | with a V2 change |
| 07-17 06:00 | ETH-USD | `NO_TRADE` | 52 | — | 3 / 5 | 3 / 1 | a,b,c | none stated | against | with a V2 change |
| 07-17 06:00 | SOL-USD | `NO_TRADE` | 55 | — | 2 / 5 | 1 / 4 | a,b,c | a | against | with a V2 change |

D3 routes by class (distinct routes; runs naming at least one):

| Class | Routes | Runs | Routes stated |
|---|---|---|---|
| A | 0 | 0 of 22 | none; every offered tool was requested |
| B | 22 | 22 of 22 | relative strength vs the other two pairs, `SOURCE_UNAVAILABLE` |
| C | 22 | 22 of 22 | post-trigger bars or confirmation |
| D | 31 | 22 of 22 | derivatives, funding, order book or flow, news (22); cross-venue data (9) |
| E | 94 | 22 of 22 | trigger base rate (22); higher-timeframe context (22); trigger-bar candle structure (17); bandwidth or percent_b history (15); support beyond the 25-bar window (8); SMA slope (5); volume distribution or seasonality (4); return distribution (1) |
| F | 36 | 22 of 22 | null quality fields or single source (22); level or window definitions (14) |

D5: 139 critique items weaken the bull case and 52 the bear case. The bull
count is higher in 18 runs, the bear count in 3, and 1 is tied.

### Trial 6

| Signal time (UTC) | Pair | Outcome | Confidence | Abstention reasons | Thesis support / counter IDs | Assessment support / counter IDs | D1 bases | D1 decisive | D2 | D4 |
|---|---|---|---|---|---|---|---|---|---|---|
| 07-05 02:00 | SOL-USD | `ABSTAIN` | — | `CONFLICTING_EVIDENCE`, `INSUFFICIENT_EVIDENCE` | 3 / 5 | 2 / 5 | a,b,c,d | none stated | — | with a V2 change |
| 07-06 13:00 | BTC-USD | `NO_TRADE` | 60 | — | 3 / 8 | 6 / 2 | a,b,c,d | none stated | against | with a V2 change |
| 07-06 13:00 | ETH-USD | `NO_TRADE` | 60 | — | 2 / 7 | 8 / 2 | a,b,c | none stated | against | with a V2 change |
| 07-06 13:00 | SOL-USD | `NO_TRADE` | 60 | — | 4 / 6 | 6 / 2 | a,b,c,d | none stated | against | with a V2 change |
| 07-08 02:00 | ETH-USD | `NO_TRADE` | 55 | — | 3 / 6 | 5 / 3 | a,b,c | a | against | with a V2 change |
| 07-08 02:00 | SOL-USD | `NO_TRADE` | 58 | — | 3 / 6 | 6 / 2 | a,b,c | none stated | against | with a V2 change |
| 07-08 09:00 | BTC-USD | `NO_TRADE` | 58 | — | 3 / 6 | 7 / 1 | a,b,c,d | a | against | with a V2 change |
| 07-10 18:00 | SOL-USD | `NO_TRADE` | 60 | — | 5 / 7 | 5 / 3 | a,b,c,d | none stated | against | with a V2 change |
| 07-12 00:00 | BTC-USD | `NO_TRADE` | 58 | — | 3 / 6 | 5 / 2 | a,b,c,d | none stated | against | with a V2 change |
| 07-12 00:00 | SOL-USD | `NO_TRADE` | 60 | — | 2 / 6 | 6 / 2 | a,b,c | a | against | with a V2 change |

| Class | Routes | Runs | Routes stated |
|---|---|---|---|
| A | 0 | 0 of 10 | none; requests for Bollinger parameters are coded F (definitions) and the request for historical band width E |
| B | 10 | 10 of 10 | relative strength vs a benchmark; none bound, tool not offered |
| C | 9 | 9 of 10 | follow-through or confirmation bars |
| D | 21 | 10 of 10 | derivatives, funding, order book, liquidity, on-chain, news (10); transaction cost or slippage (7); cross-venue data (4) |
| E | 17 | 10 of 10 | crossing study conditioned on breakdown or regime, unconditional baseline, dispersion, path order, pending-crossing identity (10); level reaction history (3); volume baseline or seasonality (2); bandwidth history (1); 1h SMA slope (1) |
| F | 8 | 8 of 10 | window, level or Bollinger-parameter definitions |

D5: 83 items weaken the bull case and 9 the bear case. The bull count is
higher in all 10 runs.

### Trial 7

| Signal time (UTC) | Pair | Outcome | Confidence | Abstention reasons | Thesis support / counter IDs | Assessment support / counter IDs | D1 bases | D1 decisive | D2 | D4 |
|---|---|---|---|---|---|---|---|---|---|---|
| 07-18 14:00 | SOL-USD | `NO_TRADE` | 55 | — | 4 / 3 | 6 / 2 | a,b,c,d | none stated | against | within the run † |
| 07-20 06:00 | BTC-USD | `NO_TRADE` | 58 | — | 3 / 7 | 6 / 2 | a,b,c,d | none stated | against | within the run † |
| 07-20 06:00 | ETH-USD | `NO_TRADE` | 55 | — | 4 / 8 | 8 / 2 | a,b,c | none stated | against | within the run † |
| 07-22 06:00 | BTC-USD | `ABSTAIN` | — | `CONFLICTING_EVIDENCE`, `INSUFFICIENT_EVIDENCE` | 4 / 4 | 3 / 4 | a,b,c,d | none stated | — | within the run † |
| 07-22 06:00 | SOL-USD | `NO_TRADE` | 55 | — | 4 / 6 | 6 / 2 | a,b,c | b | against | within the run † |
| 07-22 13:00 | BTC-USD | `ABSTAIN` | — | `INSUFFICIENT_EVIDENCE`, `CONFLICTING_EVIDENCE` | 5 / 7 | 2 / 5 | a,b,c,d | none stated | — | within the run † |
| 07-23 13:00 | BTC-USD | `NO_TRADE` | 60 | — | 3 / 4 | 6 / 2 | a,b,c | none stated | against | within the run † |
| 07-23 13:00 | ETH-USD | `ABSTAIN` | — | `CONFLICTING_EVIDENCE`, `INSUFFICIENT_EVIDENCE` | 5 / 6 | 3 / 4 | a,b,c,d | none stated | — | within the run † |
| 07-23 13:00 | SOL-USD | `ABSTAIN` | — | `INSUFFICIENT_EVIDENCE`, `CONFLICTING_EVIDENCE` | 3 / 5 | 3 / 4 | a,b,c,d | d | — | within the run † |
| 07-24 12:00 | SOL-USD | `NO_TRADE` | 58 | — | 3 / 5 | 6 / 2 | a,b,c | b | against | within the run † |

| Class | Routes | Runs | Routes stated |
|---|---|---|---|
| A | 10 | 10 of 10 | band width and percent_b (depth of the cross) from `bollinger_position`, offered and never requested; all budget-only (8 of 8 attempts used) |
| B | 10 | 10 of 10 | relative strength vs a benchmark; none bound, tool not offered |
| C | 9 | 9 of 10 | outcome of the pending crossing or follow-through bars |
| D | 16 | 10 of 10 | derivatives, funding, order book, news (10); cross-venue data (6) |
| E | 14 | 10 of 10 | crossing subgroups jointly conditioned, medians, dispersion or significance, per-crossing timestamps or clustering, longer or out-of-regime lookback (10); SMA slope or regime-flip margin (2); hour-of-day volume baseline (2) |
| F | 0 | 0 of 10 | — |

D5: 87 items weaken the bull case and 15 the bear case. The bull count is
higher in all 10 runs.

### Category F across trials

Requests for window definitions or Bollinger parameters ask for tool-description
information, not tool evidence, so they are not category A. The F (other) row
was coded inconsistently across trials (for example "single source / null
quality" is coded in trial 5 only), so F tallies are not comparable between
trials; D4 and the hypotheses do not depend on them.

## Hypotheses

**H1: most `NO_TRADE` decisions are "support only" (D2). Not supported in any
trial.**

| Trial | `NO_TRADE` | "against" | "support only" |
|---|---|---|---|
| 5 | 19 | 19 | 0 |
| 6 | 9 | 9 | 0 |
| 7 | 6 | 6 | 0 |

Every `NO_TRADE` assessment cites specific bearish readings, such as closes
below both SMAs, negative 1h, 6h and 24h returns, a close below the recent
low, or a full-body bearish trigger candle. Each also states insufficient or
fragile support (b). Where one basis is decisive, the trials differ. Trial 5
has 8 runs decided by (a), 4 by (b) and 3 `ABSTAIN` runs by (c). Trial 6 has 3
decided by (a). Trial 7 has 2 decided by (b) and 1 `ABSTAIN` by (d). The other
runs are "none stated": 7 of 22, 7 of 10 and 7 of 10.

**H2: most runs' routes to TRADE are "never in V2" or "with a V2 change", not
"within the run" (D4). Supported in trials 5 and 6; not supported in trial 7.**

| Trial | Runs | Within the run | With a V2 change | Never in V2 |
|---|---|---|---|---|
| 5 | 22 | 0 | 22 | 0 |
| 6 | 10 | 0 | 10 | 0 |
| 7 | 10 | 10 (all budget-only) | 0 | 0 |

In trial 7 every run reaches "within the run" only through `bollinger_position`
under the class A rule. Each would have had to spend one of its 8 attempts on
it instead of another tool. Setting class A aside, every trial 7 run also
states B and E routes.

**H3: critiques challenge the bull case more often than the bear case (D5).
Supported in all three trials.**

| Trial | Items weakening bull | Items weakening bear | Runs bull > bear | Runs bear > bull | Ties |
|---|---|---|---|---|---|
| 5 | 139 | 52 | 18 | 3 | 1 |
| 6 | 83 | 9 | 10 | 0 | 0 |
| 7 | 87 | 15 | 10 | 0 | 0 |

**H4: confidence on `NO_TRADE` varies little within each trial. Supported in
all three trials.** TASK.md does not define "little". This record takes it to
mean that every `NO_TRADE` confidence in a trial lies within a range of at most
10 points on the 0–100 scale. That threshold was chosen after the confidence
ranges were seen, and trial 5 (52–62) sits exactly on it. The modal value holds 9 of 19 runs in trial 5
(55), 5 of 9 in trial 6 (60) and 3 of 6 in trial 7 (55).

| Trial | `NO_TRADE` | Range | Counts |
|---|---|---|---|
| 5 | 19 | 52–62 | 52 ×3, 55 ×9, 58 ×3, 60 ×3, 62 ×1 |
| 6 | 9 | 55–60 | 55 ×1, 58 ×3, 60 ×5 |
| 7 | 6 | 55–60 | 55 ×3, 58 ×2, 60 ×1 |

## Coding agreement

Builder codes against blind codes, before resolution:

| Judgment | Trial 5 | Trial 6 | Trial 7 |
|---|---|---|---|
| D1 basis set (exact) | 22/22 | 10/10 | 10/10 |
| D1 (a) marked | 22/22 | 10/10 | 10/10 |
| D1 (b) marked | 22/22 | 10/10 | 10/10 |
| D1 (c) marked | 22/22 | 10/10 | 10/10 |
| D1 (d) marked | 22/22 | 10/10 | 10/10 |
| D1 decisive | 21/22 | 9/10 | 9/10 |
| D2 (`NO_TRADE` only) | 19/19 | 9/9 | 6/6 |
| D4 | 22/22 | 10/10 | 10/10 |

Disagreements and resolutions:

| Run | Judgment | Builder | Blind | Resolution |
|---|---|---|---|---|
| T5 2026-07-08 02:00 SOL-USD | D1 decisive | none stated | a | **a.** The critic response says it chose `NO_TRADE` "because the successfully collected evidence is coherent enough to reject" and that a missing benchmark "would not restore the failed range premise". The premise failed because the levels and momentum evidence "directly contradicts" it, so the stated reason is (a). |
| T6 2026-07-08 02:00 ETH-USD | D1 decisive | none stated | a | **a.** The stated reason is that "the collected evidence is coherent rather than conflicting at the decision-relevant short horizon". The rationale calls that evidence "consistent and bearish". The next sentence ("bullish elements are too weak to offset it") weighs against that same evidence rather than adding a second reason. |
| T7 2026-07-23 13:00 SOL-USD | D1 decisive | none stated | d | **d.** The assessment abstains rather than reject "because the aggregate crossing tendency … and the trigger candle's lower wick are not refuted". That is one reason, and it sets support against the bearish context. This matches the convention used for the other `ABSTAIN` runs. |

All three resolutions adopt the blind code. The blind coder also flagged one
borderline D3 item without disagreeing on D4. Trial 6, 2026-07-06 13:00
ETH-USD asks for "Bollinger band parameters … and how band width compares
historically". Both coders classed it as not A: definitions (F) and history
(E).

## D5 blind recount

**Method.** A fresh reviewer agent, separate from any review loop, was given
three things:

- `blind-extract.json`;
- the D5 definition from Method, verbatim;
- an instruction to list, for each of the 42 runs, every `challenged_claims`
  and `contradictions` item it counts as weakening the bull case or the bear
  case, and to give the two totals.

It was not given the builder's D5 convention, the builder's codes or this
record. It was told to open no other file in `~/crypteo-eval/diagnostic/`, no
repository file and no trial record, and it stated that the extract was the
only file it opened. Its output opens with the two counting rules it says it
applied, quoted here verbatim:

> - An item that says evidence was listed as supporting but actually has bearish content counts as weakening the bull case. An item that only says counterevidence was also used by the bull case is uncounted.
> - Items that are unreconciled two-sided readings, formatting or window-definition notes, or comments on how invalidation conditions were designed are uncounted.

The output was saved verbatim to `blind-d5-codes.txt`, and its checksum was
appended to `hashes-pre-blind.txt` before any comparison. `d5_agreement.py`
compares the per-run D5 counts in `builder-codes.json` with those in
`blind-d5-codes.txt`. Neither file was edited, and no count was resolved or
adjusted.

| File | SHA-256 |
|---|---|
| `blind-extract.json` | `7f030191d18abfcb9f51a3078b088263d56098d97a42ba3efa63025b669b97ac` |
| `builder-codes.json` | `dc3219b9783e96f8cc62e7fe6057316ac3d1941f792f29812dde69404a492663` |
| `blind-d5-codes.txt` | `3371e55c753d4e9311c141723a6c73d622021e8f67f091ecdc283b4f48e67879` |
| `d5_agreement.py` | `f24db413ba4c945c391bb87c13dfd5c2952c1ff6f4e0211ab0283239cf82c192` |

**Totals.** Items weakening each case, summed over the trial's runs:

| Trial | Runs | Builder: bull | Builder: bear | Blind: bull | Blind: bear |
|---|---|---|---|---|---|
| 5 | 22 | 139 | 52 | 136 | 51 |
| 6 | 10 | 83 | 9 | 78 | 9 |
| 7 | 10 | 87 | 15 | 85 | 13 |

**Per-run direction.** Number of runs in each direction under each coder:

| Trial | Builder: bull > bear | Builder: bear > bull | Builder: tie | Blind: bull > bear | Blind: bear > bull | Blind: tie | Directions agree |
|---|---|---|---|---|---|---|---|
| 5 | 18 | 3 | 1 | 18 | 3 | 1 | 20/22 |
| 6 | 10 | 0 | 0 | 10 | 0 | 0 | 10/10 |
| 7 | 10 | 0 | 0 | 10 | 0 | 0 | 10/10 |

Runs whose direction differs (counts are bull/bear):

| Run | Builder | Builder direction | Blind | Blind direction |
|---|---|---|---|---|
| T5 2026-07-13 04:00 SOL-USD | 3/3 | tie | 3/2 | bull > bear |
| T5 2026-07-17 06:00 SOL-USD | 5/4 | bull > bear | 4/4 | tie |

No run differs in direction in trials 6 and 7.

**Verdict.** The rule was fixed before the blind pass. H3 holds under blind
coding in a trial when the blind totals show more items weakening the bull
case than the bear case, and the blind per-run directions show bull > bear in
more than half of that trial's runs.

| Trial | Blind bull vs bear | Blind runs bull > bear | H3 under blind coding |
|---|---|---|---|
| 5 | 136 > 51 | 18 of 22 | holds |
| 6 | 78 > 9 | 10 of 10 | holds |
| 7 | 85 > 13 | 10 of 10 | holds |

H3 holds under blind coding in all three trials.

**Limitation.** Both coders are models. The blind coder had the D5 definition
only, without the builder's convention.

## Limitations

- The coder is a model, and so are both blind coders. Agreement between two model
  coders is not agreement with a human reading. The builder's conventions were
  fixed before the blind pass but were not given to the blind coder.
- The categories need judgment, especially the D1 decisive basis, the D1 (d)
  boundary, D3 class A against F for definitional requests, and D5's
  "clearly". D3 was coded by the builder alone; D5 was recounted blind (see D5 blind recount).
- The 42 runs come from three trials with different tools, tool descriptions
  and model views. Results are reported per trial and never pooled into one
  rate. Trial 6 and trial 7 have 10 runs each.
- The results describe stored text, not market truth. A route being stated
  does not show that obtaining it would have produced `TRADE`, and a bearish
  reading being stated does not show the reading was correct.
- Class A follows a fixed rule. In every run the A route needed one of the 8
  attempts already spent on other tools.
