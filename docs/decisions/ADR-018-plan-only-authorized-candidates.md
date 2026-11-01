# ADR-018: Evaluation plans only candidates the baseline would trade

## Status

Accepted

## Context

[ADR-013](ADR-013-v2-evaluation-scope.md) plans evaluation windows mechanically
from point-in-time scanner output, without reference to outcomes. Each selected
window replays one candidate twice: scanner-only, and with research. Research
runs before trade terms are frozen
([ADR-011](ADR-011-model-research-historical-replay.md)), so every selected
candidate is researched, including candidates the baseline's own risk check
would reject.

In such a window research cannot change the financial outcome. A research
`TRADE` reproduces the baseline's chain, which ends in the same rejection, and a
decline matches it. In
[trial 7](../evaluations/v2-july-2026-trial-7.md#interpretation) the risk check
rejected 7 of 10 proposals with `NON_POSITIVE_TARGET_REWARD`, so research was
paid for in 7 windows where no decision could matter. In trial 8 it was 2 of 10.

The baseline's decision at the signal hour T needs no outcome. Trade terms come
from history visible at T. In a fresh single-candidate window the portfolio is
the window's initial cash, and the risk check compares the proposal with that
portfolio, the decision-time reference price and the window's cost assumptions.

## Decision

**Rule.** A candidate is selected only if the baseline would authorize its trade
at T. The planner computes that decision with the same deterministic services
the replay uses: `TradeTermsService` terms from candles visible at T, the
baseline's deterministic proposal, and `RiskService` authorization against the
window's initial portfolio, risk and execution settings. Every read goes through
a point-in-time gateway whose clock is T, so no candle after T can contribute.

**Exclusion.** A candidate the baseline would not authorize is excluded with the
new reason `BASELINE_NOT_AUTHORIZED`. The plan records the terms outcome or the
risk rejection reason that caused it. The check runs after the existing checks,
so it does not change how overlap is decided: overlap still considers every
scanner crossing, selected or not.

**Scope.** The rule applies to every plan purpose. Replay, research, the
scanner, trade terms, risk and the comparison artifact are unchanged.
`validate_plan` re-plans and requires equality, as now.

## Rationale

The comparison asks whether research improves selection among the same
candidates. Where the baseline cannot trade, both modes end without a trade
whatever research says, so those windows cost research money and carry no
information about selection. Excluding them uses only what the replay itself
would decide at T, with the same code, so it adds no new judgment and no
information from after T.

Alternatives considered:

- *Keep planning every crossing.* That pays for research that cannot change an
  outcome, as in 7 of trial 7's 10 windows.
- *Filter in the scanner.* That would change what an opportunity is for every
  consumer, including replay and trader-led research, and the scanner policy
  identity. The planner is the evaluation-specific place for this rule.
- *Freeze terms before research in research mode.* That reorders the replay
  lifecycle ADR-011 fixed, for a benefit the planner provides without touching
  replay.

## Consequences

- The implementing slice adds the authorization check and the
  `BASELINE_NOT_AUTHORIZED` reason to `plan_windows` and `validate_plan`, the
  plan contracts and their recomputing validators, and the specification's
  window-planning section. Its tests show that an excluded candidate's replay
  ends in the recorded rejection, that a selected candidate's replay is
  authorized, and that planted candles after T change no planning decision.
- Plan identity changes, so earlier plans fail `validate_plan` at that commit.
  They still validate at their recorded checkouts.
- Reaching the plan limit can take a longer stretch of data, because
  non-authorized candidates no longer count toward it.
- Baseline results describe authorized trades only, so their totals are not
  comparable with trials 5 to 8, which also counted rejected windows. Records
  say so.
- This changes which candidates are planned, not the baseline strategy. Changes
  to holding period, trigger filters or cost assumptions need their own
  pre-registered decision.
