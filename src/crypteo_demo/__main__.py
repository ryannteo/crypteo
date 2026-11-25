"""Command line: ``python -m crypteo_demo [OUTPUT]``.

``OUTPUT`` must be new or empty; it defaults to the first unused
``artifacts/demo-N`` under the current directory. The demo refuses, before
writing anything or importing ``crypteo``, while ANTHROPIC_API_KEY is set (as
the viewer does) or while LangSmith tracing is configured, since tracing would
send the workflow's runs over the network.
"""

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from crypteo_demo import LABEL

KEY_VARIABLE = "ANTHROPIC_API_KEY"
TRACING_VARIABLES = (
    "LANGSMITH_TRACING",
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
)


class DemoRefusal(Exception):
    """A one-line reason the demo will not run; nothing has been written."""


def refuse_environment() -> None:
    if KEY_VARIABLE in os.environ:
        raise DemoRefusal(f"refusing to run while {KEY_VARIABLE} is set")
    if found := [name for name in TRACING_VARIABLES if name in os.environ]:
        raise DemoRefusal(f"refusing to run while {found[0]} is set")


def default_output(base: Path) -> Path:
    number = 1
    while (base / "artifacts" / f"demo-{number}").exists():
        number += 1
    return base / "artifacts" / f"demo-{number}"


def _output(argument: Path | None) -> Path:
    output = default_output(Path.cwd()) if argument is None else argument
    if output.is_symlink():
        raise DemoRefusal(f"output {output} is a symbolic link")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise DemoRefusal(f"output {output} must be a new or empty directory")
    return output.resolve()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m crypteo_demo",
        description=f"Run crypteo's pipeline end to end. {LABEL}",
    )
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        help="a new or empty directory (default: artifacts/demo-N)",
    )
    return parser


def _report(output: Path) -> list[str]:
    from crypteo_demo import market
    from crypteo_demo.pipeline import CODE, run_demo
    from crypteo_demo.scripted import MODEL

    run = run_demo(output)
    plan = run.plan
    selected = [c for c in plan.candidates if c.decision == "SELECTED"]
    lines = [
        LABEL,
        f"code identity: placeholder, not a commit (git {CODE.git_commit},"
        f" source {CODE.source_digest})",
        "synthetic candles: BTC-USD, ETH-USD, SOL-USD, hourly,"
        f" {market.START:%Y-%m-%d %H:%M} to {market.END:%Y-%m-%d %H:%M} UTC,"
        f" seed {market.SEED}",
        f"plan {plan.plan_id} ({plan.settings.purpose}):"
        f" {len(selected)} windows selected of {len(plan.candidates)} candidates",
    ]
    for candidate in plan.candidates:
        opportunity = candidate.opportunity
        lines.append(
            f"  {opportunity.observed_at:%Y-%m-%d %H:%M}  {opportunity.pair.value}"
            f"  {candidate.decision}"
            + (f"  {candidate.exclusion_reason}" if candidate.exclusion_reason else "")
        )
    lines.append(f"scripted decisions by {MODEL} (fixed text, not research):")
    for number, decision in enumerate(run.decisions, 1):
        lines.append(
            f"  window {number}  {decision.observed_at}  {decision.pair}"
            f"  {decision.outcome}  lean {decision.lean}"
            f"  peer comparison {decision.peer_comparison}"
        )
    lines += [
        "frozen reproduction with no model:"
        f" metrics equal {run.metrics_equal},"
        f" candidates equal {run.candidates_equal}",
        f"evaluation_id {run.result.evaluation_id}",
        f"viewer index: {run.index}",
    ]
    if not (run.metrics_equal and run.candidates_equal):
        raise RuntimeError("the frozen reproduction differs from the evaluation")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0 after the whole demo ran, or 1 with a one-line refusal."""
    try:
        refuse_environment()
        output = _output(_parser().parse_args(argv).output)
    except DemoRefusal as refusal:
        print(f"crypteo_demo: refused: {refusal}", file=sys.stderr)
        return 1
    for line in _report(output):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
