# ADR-013: V2 evaluates research selection on post-cutoff data

## Status

Accepted

## Context

V1 can run, journal and reproduce bounded Claude research inside deterministic
historical replay, but it cannot show whether research changes trade selection.
The V1 release compared one candidate by hand (n=1). The specification lists a
baseline/model comparison artifact, an evaluation harness and statistical
evaluation as unimplemented
([spec](../v1-specification.md#unimplemented-research-and-evaluation-boundaries)).

The specification's planned chronological split puts the untouched test period at
2024-07-01 through 2026-06-30. The only research model calibrated and used is
`claude-opus-5-5` ([ADR-012](ADR-012-claude-research-provider.md)), whose
reliable knowledge cutoff and training data cutoff are both June 2026 according
to Anthropic's models overview. Every period in the planned split is therefore
inside the model's training data. The point-in-time gateway keeps future market data out of tools,
but it cannot remove what the model already knows about BTC-USD, ETH-USD and
SOL-USD price paths. Results on those periods cannot support a claim about
research selection. The other current Claude models have the same cutoff.

Post-cutoff data exists from July 2026. The V1 release inspected part of it: it
acquired data from 2026-09-01, ran the scanner-only baseline over 85 single-asset
windows through 2026-10-16, and examined the 2026-10-15 research run.

Replay cost and result size grow quadratically with replay length, so long
continuous replays are impractical. Under the single active trade, baseline and
research portfolios also diverge in a long replay: a trade one mode takes blocks
later candidates the other mode can take, so the modes stop facing the same
choices.

Crypteo is a personal project. At the release run's usage (155,566 input and 9,807
output tokens at $4 and $20 per million), one research run costs about $0.82, and
the owner's evaluation budget is $25.

## Decision

V2 has two themes, in order. First, evaluation: comparing bounded research with
the scanner-only baseline on the same opportunities under identical deterministic
terms, risk and execution. Second, trader-led research: the user supplies a
market question or thesis, which converges on the same evidence, thesis, critique
and assessment workflow; its design is recorded in ADR-014 before it is built.
New evidence domains, live mode and multi-model comparison are out of V2 scope
unless a later ADR adds them.

**Model.** Claude remains the only provider. V2 evaluates one pinned research
policy: `claude-opus-5-5` at effort `high`, with the V1 workflow, prompts,
schemas, tools and budgets. Results from different policies are never pooled.

**Periods.** The V1 planned split is replaced:

| Role | Hourly boundaries | Permitted use |
| --- | --- | --- |
| Contaminated | Before 2026-07-01T00:00:00Z | Plumbing, determinism and integration checks only; no claims about research selection |
| Evaluation | From 2026-07-01T00:00:00Z | Model-backed evaluation runs |

Evaluation results state which part of their period the V1 release had already
inspected.

**Evaluation unit.** The unit is the admitted scanner candidate. Windows are
derived mechanically from point-in-time scanner output without reference to
outcomes, and every candidate in the evaluated range is either evaluated or
excluded with a recorded reason. Each window is replayed in scanner-only mode and
in research mode with identical replay configuration and datasets, using the
existing entrypoints and the single `_replay_engine` lifecycle.

**Comparison artifact.** A deterministic comparison artifact pairs the two
results for each window. It is validated by recomputation like other contracts:
it revalidates both `ReplayResult`s, requires that their deterministic inputs
match, resolves research links through the store, and recomputes every
comparison metric. Where a window admits a single candidate and research returns
`TRADE`, the artifact requires the research financial chain to equal the
baseline's, excluding run-bound order IDs. Comparison statistics are computed by
deterministic Python with the project's Decimal policy.

**Budget and sample.** The first model-backed evaluation runs at most 25
research runs, starting from the first evaluable candidate after 2026-07-01 in
chronological order (which precedes the data the V1 release inspected). It
sets `max_research_runs=25` and an Anthropic Console spend limit of $25 as
the billing backstop, since software limits are not a billing guarantee. Any
later run declares its run cap and estimated cost in its TASK.md.

**Claims.** At this sample size the evaluation demonstrates the harness end to
end. Its results are reported as descriptive comparisons, not as evidence that
research improves or worsens selection.

## Rationale

Data after the model's training cutoff is outside its training data, which is the
strongest temporal claim available without proving the model's parametric
knowledge, and that remains impossible. Starting in July 2026 also uses data the
V1 release did not inspect.

Short per-candidate windows keep replay cost linear in the number of candidates
and give a paired comparison. In a single-candidate window both modes start from
the same portfolio and freeze the same deterministic terms, so research `TRADE`
should reproduce the baseline trade exactly; the comparison artifact enforces this
rather than assuming it. Research then acts as a filter on baseline trades, and
its effect shows in the outcomes of the candidates it declines. Building on
existing replay results avoids forking the lifecycle or adding a replay record
kind.

A $25 budget fits a personal project, and 25 candidates is enough to exercise
every path through the harness on real data. A credible performance claim would
need a much larger, pre-registered sample; that is left to a later decision.

## Consequences

- The specification's planned split table is replaced by these periods, and the
  V2 evaluation boundaries move from "unimplemented" as slices land.
- The harness, window planner and comparison artifact are built and tested
  without inference; normal CI keeps mocking the model.
- Development-period results, including the V1 release run, are reported
  separately from the 25-run evaluation.
- The comparison artifact and window planner are new modules. They change the
  source digest but not research policy identity, replay record kinds, or the
  release-smoke golden digests, which use an explicit code identity.
- Reaching a performance claim later means a new ADR with a pre-registered,
  untouched test period and a larger budget.
