"""Export stored evaluations and their research runs to a pinned bundle.

Every input is copied to a private temporary directory and only the copies are
opened, through ``crypteo``'s public loaders. Values are taken as stored; the
list-price cost estimate is the one computation. Nothing is written unless the
whole bundle builds, and the originals must hash the same before and after.
No model client is constructed and no network request is made.
"""

import re
import tempfile
from dataclasses import dataclass
from decimal import (
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    Rounded,
)
from pathlib import Path
from typing import Any

from crypteo.domain.evaluation import EvaluationResult
from crypteo.domain.replay_research import ReplayResearchRecord
from crypteo.domain.research_workflow import ModelCallRecord, ResearchRunResult
from crypteo.evaluation import load_evaluation
from crypteo.experiment import CodeIdentity, current_code_identity
from crypteo.research_persistence import ResearchStore
from crypteo_viewer.bundle import (
    BUNDLE_FORMAT,
    INDEX,
    MANIFEST,
    ViewerRefusal,
    decode,
    digest,
    encode,
    publish,
    refuse_with_key,
    require_empty_output,
    safe_segment,
)

COST_LABEL = (
    "List-price estimate in USD from provider-reported token usage and this "
    "bundle's price table; not a bill."
)
PRICE_FIELDS = ("input", "output", "cache_write", "cache_read")
USAGE_FIELDS = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cache_write": "cache_creation_input_tokens",
    "cache_read": "cache_read_input_tokens",
}
BASELINE_KINDS = frozenset(
    {
        "TERMS",
        "TERMS_REJECTION",
        "BASELINE_SELECTION",
        "RISK_AUTHORIZATION",
        "RISK_REJECTION",
        "SUBMISSION",
        "FILL",
        "CLOSED_TRADE",
        "ENTRY_REJECTION",
        "DATA_GAP",
    }
)
RESEARCH_KINDS = frozenset({"RESEARCH", "RESEARCH_SELECTION"})
_PRICE = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
_EXACT = Context(
    prec=1000,
    traps=[InvalidOperation, Inexact, Rounded, Overflow, DivisionByZero],
)
_MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class EvaluationInput:
    label: str
    evaluation: Path
    store: Path


@dataclass(frozen=True)
class TrialInput:
    name: str
    evaluations: tuple[EvaluationInput, ...]
    attachments: tuple[Path, ...]


@dataclass(frozen=True)
class ExportSpec:
    bundle: str
    prices: dict[str, dict[str, str]]
    trials: tuple[TrialInput, ...]


def _object(value: Any, what: str, required: set[str], optional: set[str]) -> Any:
    if not isinstance(value, dict):
        raise ViewerRefusal(f"{what} must be a JSON object")
    if missing := sorted(required - set(value)):
        raise ViewerRefusal(f"{what} lacks {missing[0]!r}")
    if unknown := sorted(set(value) - required - optional):
        raise ViewerRefusal(f"{what} has unknown key {unknown[0]!r}")
    return value


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ViewerRefusal(f"{what} must be a nonblank string")
    return value


def _list(value: Any, what: str, *, nonempty: bool) -> list[Any]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ViewerRefusal(f"{what} must be a {'nonempty ' if nonempty else ''}list")
    return value


def _input(base: Path, value: Any, what: str) -> Path:
    path = base / _text(value, what)
    if not path.is_file():
        raise ViewerRefusal(f"{what} {path.name} does not exist or is not a file")
    return path.resolve()


def _prices(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict):
        raise ViewerRefusal("prices must be a JSON object")
    table: dict[str, dict[str, str]] = {}
    for model, row in value.items():
        _text(model, "price table model")
        row = _object(row, f"prices for {model!r}", set(PRICE_FIELDS), set())
        for field in PRICE_FIELDS:
            price = row[field]
            if not isinstance(price, str) or not _PRICE.fullmatch(price):
                raise ViewerRefusal(
                    f"price {field} for {model!r} must be a decimal string"
                )
        table[model] = {field: row[field] for field in PRICE_FIELDS}
    return table


def load_spec(path: Path) -> ExportSpec:
    """Parse the owner's export spec; relative paths resolve beside the spec."""
    path = Path(path)
    if not path.is_file():
        raise ViewerRefusal(f"spec {path.name} does not exist or is not a file")
    raw = _object(
        decode(path.read_bytes(), "spec"), "spec", {"bundle", "prices", "trials"}, set()
    )
    base = path.resolve().parent
    trials: list[TrialInput] = []
    for index, item in enumerate(_list(raw["trials"], "trials", nonempty=True)):
        trial = _object(
            item, f"trial {index}", {"name", "evaluations"}, {"attachments"}
        )
        name = _text(trial["name"], f"trial {index} name")
        evaluations = []
        for position, entry in enumerate(
            _list(trial["evaluations"], f"trial {name!r} evaluations", nonempty=True)
        ):
            what = f"trial {name!r} evaluation {position}"
            entry = _object(entry, what, {"label", "evaluation", "store"}, set())
            evaluations.append(
                EvaluationInput(
                    label=_text(entry["label"], f"{what} label"),
                    evaluation=_input(base, entry["evaluation"], f"{what} evaluation"),
                    store=_input(base, entry["store"], f"{what} store"),
                )
            )
        if len({e.label for e in evaluations}) != len(evaluations):
            raise ViewerRefusal(f"trial {name!r} repeats an evaluation label")
        attachments = tuple(
            _input(base, item, f"trial {name!r} attachment")
            for item in _list(
                trial.get("attachments", []),
                f"trial {name!r} attachments",
                nonempty=False,
            )
        )
        for attachment in attachments:
            safe_segment(attachment.name, "attachment name")
        if len({a.name for a in attachments}) != len(attachments):
            raise ViewerRefusal(f"trial {name!r} repeats an attachment name")
        trials.append(TrialInput(name, tuple(evaluations), attachments))
    if len({t.name for t in trials}) != len(trials):
        raise ViewerRefusal("spec repeats a trial name")
    return ExportSpec(
        bundle=safe_segment(_text(raw["bundle"], "bundle"), "bundle name"),
        prices=_prices(raw["prices"]),
        trials=tuple(trials),
    )


def _plain(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _usage(raw: Any) -> dict[str, int] | None:
    """Provider-reported token counts, or None when input/output are unreported.

    Absent or null cache counts are reported as zero, as Claude omits them when
    no cache is used; any other non-integer count means usage is unreported.
    """
    if not isinstance(raw, dict):
        return None
    usage: dict[str, int] = {}
    for field, key in USAGE_FIELDS.items():
        count = raw.get(key)
        if count is None and field.startswith("cache_"):
            count = 0
        if type(count) is not int or count < 0:
            return None
        usage[field] = count
    return usage


def estimate_cost(
    result: ResearchRunResult, prices: dict[str, dict[str, str]]
) -> dict[str, Any]:
    """Per model call and per run: sum(tokens x USD per million) / 1,000,000.

    A model missing from ``prices`` gets no estimate, never a default. A call
    without reported usage contributes nothing and marks the run. The run total
    exists only when some call is estimated and no reported call is unpriced.
    """
    calls: list[dict[str, Any]] = []
    total = Decimal(0)
    for record in result.trace:
        if not isinstance(record, ModelCallRecord):
            continue
        model = record.request.configuration.model
        metadata = record.reply.metadata.model_dump(mode="json")
        usage = _usage(metadata["usage"])
        call: dict[str, Any] = {
            "call_id": record.request.call_id,
            "stage": record.request.stage,
            "model": model,
            "usage": usage,
            "estimate_usd": None,
        }
        if usage is None:
            call["status"] = "USAGE_UNREPORTED"
        elif model not in prices:
            call["status"] = "MODEL_NOT_PRICED"
        else:
            amount = Decimal(0)
            for field in PRICE_FIELDS:
                amount = _EXACT.add(
                    amount,
                    _EXACT.multiply(
                        Decimal(usage[field]), Decimal(prices[model][field])
                    ),
                )
            amount = _EXACT.divide(amount, _MILLION)
            total = _EXACT.add(total, amount)
            call["status"] = "ESTIMATED"
            call["estimate_usd"] = _plain(amount)
        calls.append(call)
    statuses = {call["status"] for call in calls}
    return {
        "label": COST_LABEL,
        "calls": calls,
        "usage_unreported": "USAGE_UNREPORTED" in statuses,
        "unpriced_models": sorted(
            {c["model"] for c in calls if c["status"] == "MODEL_NOT_PRICED"}
        ),
        "estimate_usd": _plain(total)
        if "ESTIMATED" in statuses and "MODEL_NOT_PRICED" not in statuses
        else None,
    }


def run_document(
    store: ResearchStore,
    research_run_id: str,
    prices: dict[str, dict[str, str]],
    source: dict[str, str],
) -> dict[str, Any]:
    """One run's bundle document: the stored artifact, events and cost estimate."""
    inspection = store.inspect_run(research_run_id)
    artifact = store.load_terminal(research_run_id)
    if inspection.artifact != artifact:
        raise ViewerRefusal(f"run {research_run_id} loads inconsistently")
    return {
        "research_run_id": research_run_id,
        "state": inspection.state,
        "artifact": artifact.model_dump(mode="json"),
        "events": [
            {
                "sequence": envelope.sequence,
                "event_type": envelope.event.kind,
                "event": envelope.model_dump(mode="json")["event"],
            }
            for envelope in inspection.events
        ],
        "cost": estimate_cost(artifact.result, prices),
        "source": {"store": source},
    }


def _lifecycle(replay: Any, opportunity_id: str, kinds: frozenset[str]) -> list[Any]:
    return [
        record
        for record in replay["records"]
        if record["opportunity_id"] == opportunity_id and record["kind"] in kinds
    ]


def _evaluation_document(
    result: EvaluationResult, raw: Any, sources: dict[str, dict[str, str]]
) -> tuple[dict[str, Any], list[str]]:
    """The stored evaluation's own JSON values, located by the validated model."""
    plan, comparison = raw["plan"], raw["comparison"]
    if raw["evaluation_id"] != result.evaluation_id or len(
        comparison["windows"]
    ) != len(result.comparison.windows):
        raise ViewerRefusal("stored evaluation differs from its loaded contract")
    windows, run_ids = [], []
    for stored, window in zip(
        comparison["windows"], result.comparison.windows, strict=True
    ):
        opportunity_id = window.candidate.opportunity_id
        linked = [
            r.payload.research_run_id
            for r in window.research.records
            if isinstance(r.payload, ReplayResearchRecord)
            and r.opportunity_id == opportunity_id
        ]
        research = _lifecycle(stored["research"], opportunity_id, RESEARCH_KINDS)
        if (
            len(linked) != 1
            or [
                r["payload"]["research_run_id"]
                for r in research
                if r["kind"] == "RESEARCH"
            ]
            != linked
        ):
            raise ViewerRefusal(f"window {opportunity_id} lacks one linked run")
        run_ids.append(linked[0])
        windows.append(
            {
                "candidate": stored["candidate"],
                "research_run_id": linked[0],
                "baseline_run_id": stored["baseline"]["run_id"],
                "research_replay_run_id": stored["research"]["run_id"],
                "baseline_records": _lifecycle(
                    stored["baseline"], opportunity_id, BASELINE_KINDS
                ),
                "research_records": research,
            }
        )
    document = {
        "evaluation_id": raw["evaluation_id"],
        "trial": raw["trial"],
        "max_research_runs": raw["max_research_runs"],
        "attempts": raw["attempts"],
        "plan": {
            key: plan[key]
            for key in (
                "plan_id",
                "settings",
                "scanner_policy",
                "datasets",
                "candidates",
            )
        },
        "comparison_id": comparison["comparison_id"],
        "metrics": comparison["metrics"],
        "windows": windows,
        "source": sources,
    }
    return document, run_ids


def _hashes(paths: list[Path]) -> dict[Path, str]:
    return {path: digest(path.read_bytes()) for path in paths}


def export_bundle(
    spec_path: Path, output: Path, *, code: CodeIdentity | None = None
) -> None:
    """Export every evaluation the spec names, with its linked runs, or nothing.

    ``code`` defaults to the checkout's identity, as other entry points allow.
    """
    refuse_with_key()
    output = Path(output)
    require_empty_output(output)
    spec = load_spec(spec_path)
    identity = CodeIdentity.model_validate(
        (code or current_code_identity()).model_dump()
    )
    roles: dict[Path, str] = {}
    for trial in spec.trials:
        named = [(e.evaluation, "evaluation") for e in trial.evaluations]
        named += [(e.store, "store") for e in trial.evaluations]
        named += [(a, "attachment") for a in trial.attachments]
        for path, role in named:
            if roles.setdefault(path, role) != role:
                raise ViewerRefusal(f"input {path.name} is named in two roles")
    originals = list(roles)

    files: dict[str, bytes] = {}
    runs: dict[str, dict[str, Any]] = {}
    index_trials: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="crypteo-viewer-") as scratch:
        copies: dict[Path, Path] = {}
        before: dict[Path, str] = {}
        for number, original in enumerate(originals):
            data = original.read_bytes()
            before[original] = digest(data)
            copy = Path(scratch) / f"{number:04d}" / original.name
            copy.parent.mkdir()
            copy.write_bytes(data)
            copies[original] = copy
        source = {
            path: {"name": path.name, "sha256": before[path]} for path in originals
        }
        stores: dict[Path, ResearchStore] = {}
        for trial_number, trial in enumerate(spec.trials):
            evaluations = []
            for entry in trial.evaluations:
                what = f"evaluation {entry.label!r} in trial {trial.name!r}"
                try:
                    if entry.store not in stores:
                        stores[entry.store] = ResearchStore(copies[entry.store])
                    store = stores[entry.store]
                    encoded = copies[entry.evaluation].read_bytes()
                    result = load_evaluation(encoded, store=store)
                    document, run_ids = _evaluation_document(
                        result,
                        decode(encoded, what),
                        {
                            "evaluation": source[entry.evaluation],
                            "store": source[entry.store],
                        },
                    )
                    loaded = [
                        run_document(store, run_id, spec.prices, source[entry.store])
                        for run_id in run_ids
                    ]
                except ViewerRefusal:
                    raise
                except Exception as error:
                    reason = (str(error).splitlines() or [""])[0]
                    raise ViewerRefusal(
                        f"{what} failed to load: {type(error).__name__}: {reason}"
                    ) from error
                name = (
                    "evaluations/" + document["evaluation_id"].split(":")[1] + ".json"
                )
                if name in files:
                    raise ViewerRefusal(f"{what} repeats an exported evaluation")
                files[name] = encode(document)
                for run in loaded:
                    previous = runs.setdefault(run["research_run_id"], run)
                    if {**previous, "source": None} != {**run, "source": None}:
                        raise ViewerRefusal(
                            f"run {run['research_run_id']} differs between stores"
                        )
                evaluations.append(
                    {
                        "label": entry.label,
                        "evaluation_id": document["evaluation_id"],
                        "file": name,
                    }
                )
            attachments = []
            for attachment in trial.attachments:
                data = copies[attachment].read_bytes()
                try:
                    data.decode("utf-8")
                except UnicodeDecodeError as error:
                    raise ViewerRefusal(
                        f"attachment {attachment.name} is not UTF-8 text"
                    ) from error
                name = f"attachments/{trial_number:03d}/{attachment.name}"
                files[name] = data
                attachments.append(
                    {"name": attachment.name, "file": name, "sha256": digest(data)}
                )
            index_trials.append(
                {
                    "name": trial.name,
                    "evaluations": evaluations,
                    "attachments": attachments,
                }
            )
    after = _hashes(originals)
    if changed := [path.name for path in originals if after[path] != before[path]]:
        raise ViewerRefusal(f"original input {changed[0]} changed during export")

    run_files = []
    for run_id in sorted(runs):
        name = "runs/" + digest(run_id.encode("utf-8")).split(":")[1] + ".json"
        files[name] = encode(runs[run_id])
        run_files.append({"research_run_id": run_id, "file": name})
    files[INDEX] = encode(
        {"bundle": spec.bundle, "trials": index_trials, "runs": run_files}
    )
    files[MANIFEST] = encode(
        {
            "bundle_format": BUNDLE_FORMAT,
            "code": identity.model_dump(mode="json"),
            "prices": spec.prices,
            "cost_label": COST_LABEL,
            "inputs": [{"role": roles[path], **source[path]} for path in originals],
            "files": {
                name: digest(data) for name, data in files.items() if name != MANIFEST
            },
        }
    )
    publish(files, output)
