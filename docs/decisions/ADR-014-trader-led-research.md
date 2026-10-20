# ADR-014: Trader-led research shares the bounded workflow and stays outside evaluation

## Status

Accepted

## Context

[ADR-013](ADR-013-v2-evaluation-scope.md) names trader-led research as V2's
second theme. The user supplies a market question or thesis, and the design is
recorded here before it is built. AGENTS.md requires that trader-led and
opportunity-led research converge on the same evidence, thesis, critique and
assessment workflow, without duplicating the research engine.

Today the deterministic tools need no `Opportunity`, but the workflow does:

- `OpportunityResearchRequest` and `OpportunityResearchContext`
  ([contracts](../../src/crypteo/domain/research_workflow.py)) are the only
  entry. The context requires `Opportunity.observed_at == T`.
- The workflow, journal rebuild and retained-result validation read the subject
  as `context.request.opportunity.pair`.
- The shared prompt policy says Crypteo is "investigating a LONG candidate" and
  calls the supplied content "opportunity" data.
- Replay admission compares canonical opportunity-context digests, and the
  journal header stores an `OpportunityResearchContext`.

The assessment vocabulary is long-only. `TRADE` supports considering a LONG
setup ([ADR-009](ADR-009-bounded-model-directed-research.md)), and the
simulator trades spot only. Live mode is out of V2 scope, so any run in V2
happens at a historical hourly T supplied by the runtime clock.

A question written now about a past T can carry what its author knows happened
afterwards. The point-in-time gateway controls what tools return. It cannot
control what the user writes into the question.

## Decision

**Entry.** Add `TraderResearchRequest` with exactly three fields:

- `research_run_id`, supplied by the caller;
- `pair`, one of the supported Coinbase spot pairs, given by the caller as a
  structured field and never parsed from the text;
- `question`, nonblank text of at most 2,000 characters, holding the trader's
  question or thesis.

`TraderResearchContext` sits beside `OpportunityResearchContext`. It has the
same research time, benchmark and source-binding rules, but no requirement tied
to an opportunity. Both contexts expose the subject pair and benchmarks through
the same accessors. Workflow, journal and validation code read the subject from
there rather than from `request.opportunity`.

The durable journal header accepts either context. Stored opportunity-led
headers keep their current JSON shape and must still load unchanged.

**One engine.** Trader-led runs use the same runtime, five-node graph, budgets,
tools, evidence views, stage schemas, independent critic and `TradeDecision` as
opportunity-led runs. They enter through `run_trader_research` (in memory) and
`run_durable_trader_research` (journaled), which share the existing private run
function. No stage, budget or tool is specific to one entry mode.

**Long-only.** A trader-led run assesses whether the evidence supports
considering a LONG position in the given pair at T:

- `TRADE` means the evidence supports considering it;
- `NO_TRADE` means the evidence argues against it;
- `ABSTAIN` means no reliable conclusion could be formed.

Short theses, other assets and direction-neutral questions are out of scope.
Code doesn't parse direction from the text. The model answers within the long
vocabulary.

**The question is data, not evidence.** The question reaches the model as
untrusted research data. It cannot be cited, produces no `EvidenceItem`, and
cannot select pair, time, venue, timeframe, window or tool arguments. Citations
remain successful tool evidence IDs from the same run.

**Prompts.** Opportunity-led prompt text and versions stay byte-identical.
Trader-led runs use a separate shared-policy text that names the trader's
question as untrusted data and states the long-only meaning. They get their own
prompt versions. Stage instructions are shared, so trader-led prompt identities
differ only by the shared policy.

**Time.** T is `service.now` at an hourly UTC boundary, captured once, exactly
as in opportunity-led research. All reads go through the same gateway.

**Not evaluated.** Trader-led research is standalone. Replay admission,
frozen mappings, evaluation planning, the runner and the comparison artifact
accept only opportunity-led contexts and reject a trader-led artifact. Its
results are never pooled with evaluation results and are not evidence about
selection quality.

**No conversation or memory.** One request produces one bounded run. A
follow-up is a new run with no access to earlier questions, theses, decisions
or outcomes.

**No financial path.** A trader-led run never calls trade terms, risk, broker or
portfolio code, and receives no portfolio or position state.

## Rationale

Generalizing the subject while keeping the request types separate lets both
entry modes share the engine without turning `Opportunity` into a catch-all.
The scanner contract keeps its meaning: an `Opportunity` remains a deterministic
candidate, and a trader's question is a different kind of input.

Keeping opportunity-led prompts and stored context shapes unchanged means the
evaluation policy, release-smoke goldens and stored evaluation runs are not
disturbed by adding a second entry mode.

Keeping long-only avoids a second assessment vocabulary and keeps `TRADE` with
one meaning everywhere. A direction-neutral or short assessment would need its
own decision, because nothing downstream could act on it.

Excluding trader-led runs from evaluation is the honest consequence of the
hindsight problem. Historical evaluation rests on information being limited to
time T. A question written after T breaks that guarantee in a way code cannot
check.

## Consequences

- Validation of stored trader-led runs follows the same no-migration rule as
  today. Editing the trader-led shared policy makes earlier trader-led runs
  fail to load.
- Replay and evaluation contracts gain an explicit rejection for trader-led
  contexts. That rejection must be tested, not assumed.
- `ResearchReplayManifest` policy identity covers the opportunity-led prompts
  only. Trader-led prompt identities are retained per request in the journal.
- Tests must cover:
  - a question containing instructions or false claims, which grants no
    authority, cites nothing and changes no binding;
  - planted future candles that stay invisible to trader-led tools;
  - tampering that swaps the context kind or edits the question in a stored
    journal;
  - replay and evaluation rejecting a trader-led artifact;
  - unchanged opportunity-led request bytes and goldens.
- Planned slices:
  1. Generalize the subject accessor with no behavior change (goldens
     unchanged).
  2. Add the trader-led request, context, prompts, entry points and journal
     header.
  3. Add the replay and evaluation rejections, and docs.
- Live trader-led research, short or neutral assessments, other assets,
  multi-turn conversation and memory need a later ADR.
- No model-backed run is required to build this. Any manual trader-led run
  declares its cost cap in its TASK.md, like evaluation runs.
