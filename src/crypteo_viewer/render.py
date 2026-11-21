"""Render a verified bundle to static HTML pages that open directly from disk.

Standard library and ``crypteo_viewer.bundle`` only; this module never imports
``crypteo`` and never reads a store, an evaluation file or a dataset. Pages
carry no script, no event handler attribute and no reference outside the site.
Every stored text is HTML-escaped and every value appears as stored.
"""

import hashlib
import html
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from crypteo_viewer.bundle import (
    INDEX,
    ViewerRefusal,
    decode,
    publish,
    read_bundle,
    refuse_with_key,
    require_empty_output,
)

STYLESHEET = "style.css"
_CSS = """\
:root { color-scheme: light dark; --fg: #1d1d1f; --bg: #ffffff; --muted: #5f6368;
  --line: #d9d9de; --panel: #f5f5f7; --link: #0b57d0; }
@media (prefers-color-scheme: dark) {
  :root { --fg: #e8e8ea; --bg: #151517; --muted: #a0a0a8; --line: #3a3a40;
    --panel: #202024; --link: #8ab4f8; }
}
* { box-sizing: border-box; }
body { margin: 0 auto; max-width: 78rem; padding: 1rem 1rem 4rem; color: var(--fg);
  background: var(--bg); font: 15px/1.5 system-ui, -apple-system, "Segoe UI",
  sans-serif; }
a { color: var(--link); }
h1 { font-size: 1.5rem; margin: 0.5rem 0 1rem; overflow-wrap: anywhere; }
h2 { font-size: 1.2rem; margin: 2rem 0 0.5rem; border-bottom: 1px solid var(--line); }
h3 { font-size: 1rem; margin: 1.25rem 0 0.4rem; }
nav { font-size: 0.9rem; margin-bottom: 0.5rem; }
.note { color: var(--muted); font-size: 0.9rem; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; margin: 0.25rem 0 0.75rem; font-size: 0.9rem; }
th, td { border: 1px solid var(--line); padding: 0.25rem 0.5rem; text-align: left;
  vertical-align: top; overflow-wrap: anywhere; }
th { background: var(--panel); font-weight: 600; }
table.kv th { width: 14rem; }
td.number { text-align: right; white-space: nowrap; }
pre { background: var(--panel); border: 1px solid var(--line); padding: 0.5rem;
  margin: 0.25rem 0; overflow-x: auto; white-space: pre-wrap;
  overflow-wrap: anywhere; font-size: 0.8rem; }
code { overflow-wrap: anywhere; }
ul { margin: 0; padding-left: 1.25rem; }
details > summary { cursor: pointer; color: var(--muted); }
section.record { border-left: 3px solid var(--line); padding-left: 0.75rem;
  margin: 0.75rem 0; }
.none { color: var(--muted); }
"""


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def evaluation_page(evaluation_id: str) -> str:
    return f"evaluations/{_hash(evaluation_id)}.html"


def run_page(research_run_id: str) -> str:
    return f"runs/{_hash(research_run_id)}.html"


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)


def _pre(value: Any) -> str:
    return f"<pre>{_e(value if isinstance(value, str) else _json(value))}</pre>"


def _folded(summary: str, value: Any) -> str:
    return f"<details><summary>{_e(summary)}</summary>{_pre(value)}</details>"


# Stands for a key a bundle predates: peer_comparison before ADR-025.
NOT_STORED = object()


def _peer_comparison(assessment: dict[str, Any]) -> Any:
    return assessment.get("peer_comparison", NOT_STORED)


def _show(value: Any) -> str:
    """A stored value as stored: strings verbatim, lists as items, objects nested."""
    if value is NOT_STORED:
        return '<span class="none">not stored</span>'
    if value is None:
        return '<span class="none">—</span>'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str | int):
        return _e(value)
    if isinstance(value, list):
        if not value:
            return '<span class="none">none</span>'
        if all(isinstance(item, str | int) for item in value):
            return "<ul>" + "".join(f"<li>{_e(item)}</li>" for item in value) + "</ul>"
        return _pre(value)
    if isinstance(value, dict):
        return _kv(value.items())
    raise ViewerRefusal("bundle holds a value of an unexpected type")


def _kv(pairs: Iterable[tuple[str, Any]]) -> str:
    rows = "".join(
        f"<tr><th>{_e(key)}</th><td>{_show(v)}</td></tr>" for key, v in pairs
    )
    return f'<div class="scroll"><table class="kv">{rows}</table></div>'


def _table(headers: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="scroll"><table><tr>{head}</tr>{body}</table></div>'


def _link(href: str, text: Any) -> str:
    return f'<a href="{_e(href)}">{_e(text)}</a>'


def _page(title: str, depth: int, body: Iterable[str]) -> bytes:
    up = "../" * depth
    nav = "" if depth == 0 else f"<nav>{_link(up + 'index.html', 'Index')}</nav>"
    document = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="referrer" content="no-referrer">\n'
        f"<title>{_e(title)}</title>\n"
        f'<link rel="stylesheet" href="{up}{STYLESHEET}">\n'
        f"</head>\n<body>\n{nav}\n<h1>{_e(title)}</h1>\n"
        + "\n".join(body)
        + "\n</body>\n</html>\n"
    )
    return document.encode("utf-8")


class _Site:
    """Pages for one verified bundle; a pure function of its bytes."""

    def __init__(self, manifest: dict[str, Any], files: dict[str, bytes]) -> None:
        self.manifest, self.files = manifest, files
        self.index = self._load(INDEX)
        self.runs: dict[str, Any] = {}
        for entry in self.index["runs"]:
            run = self._load(entry["file"])
            if run["research_run_id"] != entry["research_run_id"]:
                raise ViewerRefusal(f"bundle file {entry['file']} names another run")
            self.runs[entry["research_run_id"]] = run
        # Every (trial, label, evaluation, window index, window) that links a run.
        self.links: dict[str, list[tuple[str, str, Any, int, Any]]] = {}
        self.evaluations: list[tuple[Any, Any, Any]] = []
        for trial in self.index["trials"]:
            for entry in trial["evaluations"]:
                evaluation = self._load(entry["file"])
                if evaluation["evaluation_id"] != entry["evaluation_id"]:
                    raise ViewerRefusal(f"bundle file {entry['file']} is misnamed")
                self.evaluations.append((trial, entry, evaluation))
                for number, window in enumerate(evaluation["windows"], 1):
                    run_id = window["research_run_id"]
                    if run_id not in self.runs:
                        raise ViewerRefusal(f"bundle lacks linked run {run_id}")
                    self.links.setdefault(run_id, []).append(
                        (trial["name"], entry["label"], evaluation, number, window)
                    )

    def _load(self, name: str) -> Any:
        if name not in self.files:
            raise ViewerRefusal(f"bundle index names unlisted file {name}")
        return decode(self.files[name], name)

    def pages(self) -> dict[str, bytes]:
        pages = {"index.html": self.index_page(), STYLESHEET: _CSS.encode("utf-8")}
        for trial, entry, evaluation in self.evaluations:
            pages[evaluation_page(evaluation["evaluation_id"])] = self.evaluation_html(
                trial, entry, evaluation
            )
        for run_id, run in self.runs.items():
            pages[run_page(run_id)] = self.run_html(run)
        return pages

    def index_page(self) -> bytes:
        manifest, code = self.manifest, self.manifest["code"]
        body = [
            "<h2>Manifest</h2>",
            _kv(
                [
                    ("bundle format", manifest["bundle_format"]),
                    ("commit", code["git_commit"]),
                    ("source digest", code["source_digest"]),
                    ("cost estimate", manifest["cost_label"]),
                ]
            ),
            "<h3>Price table (USD per million tokens)</h3>",
            _table(
                ("model", "input", "output", "cache write", "cache read"),
                (
                    (_e(model),)
                    + tuple(_e(row[f]) for f in ("input", "output"))
                    + tuple(_e(row[f]) for f in ("cache_write", "cache_read"))
                    for model, row in sorted(manifest["prices"].items())
                ),
            ),
            "<h3>Inputs</h3>",
            _table(
                ("role", "name", "SHA-256"),
                (
                    (_e(i["role"]), _e(i["name"]), f"<code>{_e(i['sha256'])}</code>")
                    for i in manifest["inputs"]
                ),
            ),
            "<h2>Trials</h2>",
        ]
        for trial in self.index["trials"]:
            body.append(f"<h3>{_e(trial['name'])}</h3>")
            body.append(
                _table(
                    ("evaluation", "evaluation_id"),
                    (
                        (
                            _link(evaluation_page(e["evaluation_id"]), e["label"]),
                            f"<code>{_e(e['evaluation_id'])}</code>",
                        )
                        for e in trial["evaluations"]
                    ),
                )
            )
            if trial["attachments"]:
                body.append(
                    _table(
                        ("attachment", "SHA-256"),
                        (
                            (_e(a["name"]), f"<code>{_e(a['sha256'])}</code>")
                            for a in trial["attachments"]
                        ),
                    )
                )
        return _page(f"Run viewer: {self.index['bundle']}", 0, body)

    def _assessment(self, run_id: str) -> Any:
        return self.runs[run_id]["artifact"]["result"]["assessment"]

    def evaluation_html(self, trial: Any, entry: Any, evaluation: Any) -> bytes:
        plan = evaluation["plan"]
        body = [
            _kv(
                [
                    ("trial", trial["name"]),
                    ("label", entry["label"]),
                    ("evaluation_id", evaluation["evaluation_id"]),
                    ("stored trial", evaluation["trial"]),
                    ("max_research_runs", evaluation["max_research_runs"]),
                    ("plan_id", plan["plan_id"]),
                    ("comparison_id", evaluation["comparison_id"]),
                    ("evaluation file", evaluation["source"]["evaluation"]),
                    ("store file", evaluation["source"]["store"]),
                ]
            ),
            "<h2>Windows</h2>",
            self._windows_table(evaluation),
            "<h2>Metrics</h2>",
            _kv(sorted(evaluation["metrics"].items())),
            "<h2>Plan</h2>",
            "<h3>Settings</h3>",
            _kv(sorted(plan["settings"].items())),
            "<h3>Scanner policy</h3>",
            _pre(plan["scanner_policy"]),
            "<h3>Datasets</h3>",
            _table(
                ("pair", "dataset_version", "acquisition_id"),
                (
                    (
                        _e(d["pair"]),
                        _show(d["dataset_version"]),
                        _show(d["acquisition_id"]),
                    )
                    for d in plan["datasets"]
                ),
            ),
            "<h3>Candidates</h3>",
            _table(
                (
                    "observed_at",
                    "pair",
                    "opportunity_id",
                    "decision",
                    "exclusion reason",
                    "baseline rejection",
                ),
                (
                    (
                        _e(c["opportunity"]["observed_at"]),
                        _e(c["opportunity"]["pair"]),
                        f"<code>{_e(c['opportunity']['opportunity_id'])}</code>",
                        _e(c["decision"]),
                        _show(c["exclusion_reason"]),
                        _show(c["baseline_rejection"]),
                    )
                    for c in plan["candidates"]
                ),
            ),
            "<h2>Attempts</h2>",
            _table(
                ("opportunity_id", "attempt", "trial_id"),
                (
                    (
                        f"<code>{_e(a['opportunity_id'])}</code>",
                        _e(a["attempt"]),
                        _e(a["trial_id"]),
                    )
                    for a in evaluation["attempts"]
                ),
            ),
        ]
        for number, window in enumerate(evaluation["windows"], 1):
            candidate = window["candidate"]
            body.append(
                f'<h2 id="window-{number}">Window {number}: '
                f"{_e(candidate['pair'])} at {_e(candidate['observed_at'])}</h2>"
            )
            body.append(
                _kv(
                    [
                        ("opportunity_id", candidate["opportunity_id"]),
                        ("research_run_id", window["research_run_id"]),
                        ("baseline replay run_id", window["baseline_run_id"]),
                        ("research replay run_id", window["research_replay_run_id"]),
                        ("candidate comparison", candidate),
                    ]
                )
            )
            body.append(_link("../" + run_page(window["research_run_id"]), "Run page"))
            for heading, key in (
                ("Baseline lifecycle records", "baseline_records"),
                ("Research replay records", "research_records"),
            ):
                body.append(f"<h3>{heading}</h3>")
                body.append(_records(window[key]))
        if trial["attachments"]:
            body.append("<h2>Trial attachments</h2>")
            for attachment in trial["attachments"]:
                text = self.files[attachment["file"]].decode("utf-8")
                body.append(f"<h3>{_e(attachment['name'])}</h3>")
                body.append(f"<p>SHA-256 <code>{_e(attachment['sha256'])}</code></p>")
                body.append(f"<pre>{_e(text)}</pre>")
        title = f"Evaluation {entry['label']} ({trial['name']})"
        return _page(title, 1, body)

    def _windows_table(self, evaluation: Any) -> str:
        rows = []
        for number, window in enumerate(evaluation["windows"], 1):
            candidate, run_id = window["candidate"], window["research_run_id"]
            baseline, research = candidate["baseline"], candidate["research"]
            assessment = self._assessment(run_id)
            rows.append(
                (
                    _link(f"#window-{number}", candidate["observed_at"]),
                    _e(candidate["pair"]),
                    _e(candidate["outcome"]),
                    _show(candidate["confidence"]),
                    _show(assessment["lean"]),
                    _show(_peer_comparison(assessment)),
                    _e(baseline["status"]),
                    _show(baseline["exit_reason"]),
                    _show(baseline["net_pnl"]),
                    _e(research["status"])
                    + (
                        f" ({_e(research['exit_reason'])}, {_e(research['net_pnl'])})"
                        if research["status"] == "CLOSED"
                        else ""
                    ),
                    _link("../" + run_page(run_id), "run"),
                )
            )
        return _table(
            (
                "signal time",
                "pair",
                "decision",
                "confidence",
                "lean",
                "peer comparison",
                "baseline status",
                "exit",
                "net P&L",
                "research outcome",
                "run",
            ),
            rows,
        )

    def run_html(self, run: Any) -> bytes:
        artifact = run["artifact"]
        result = artifact["result"]
        model = result["configuration"]["model"]
        links = self.links.get(run["research_run_id"], [])
        prompts: list[tuple[str, Any]] = []
        for record in result["trace"]:
            if record["kind"] in ("model", "model_rejection"):
                request = record["request"]
                identity = (request["stage"], request["prompt_identity"])
                if identity not in prompts:
                    prompts.append(identity)
        policies: list[Any] = []
        for *_, window in links:
            for record in window["research_records"]:
                digest = record["payload"].get("policy_digest")
                if record["kind"] == "RESEARCH" and digest not in policies:
                    policies.append(digest)
        body = [
            _kv(
                [
                    ("research_run_id", run["research_run_id"]),
                    ("state", run["state"]),
                    ("status", result["status"]),
                    ("assessment origin", result["assessment_origin"]),
                    ("provider", model["provider"]),
                    ("model", model["model"]),
                    ("effort", model["parameters"]["effort"]),
                    ("budget policy", result["configuration"]["budget"]["policy_id"]),
                    ("research policy digest", policies),
                    (
                        "prompt identities",
                        [
                            f"{stage}: {p['version']} {p['digest']}"
                            for stage, p in prompts
                        ],
                    ),
                    ("code commit", result["code"]["git_commit"]),
                    ("code source digest", result["code"]["source_digest"]),
                    ("provider identity", result["provider"]),
                    ("langgraph version", result["langgraph_version"]),
                    ("artifact checksum", artifact["artifact_checksum"]),
                    ("ordered journal digest", artifact["ordered_journal_digest"]),
                    ("store file", run["source"]["store"]),
                ]
            ),
            "<h2>Linked windows</h2>",
            _table(
                ("trial", "evaluation", "window"),
                (
                    (
                        _e(trial),
                        _e(label),
                        _link(
                            f"../{evaluation_page(e['evaluation_id'])}#window-{n}",
                            f"window {n}",
                        ),
                    )
                    for trial, label, e, n, _ in links
                ),
            ),
            "<h2>Candidate</h2>",
            _kv(sorted(result["context"].items())),
            "<h2>Terms</h2>",
        ]
        terms = [
            (label, n, record)
            for _, label, _, n, window in links
            for record in window["baseline_records"]
            if record["kind"] in ("TERMS", "TERMS_REJECTION")
        ]
        if not terms:
            body.append('<p class="none">No terms are linked to this run.</p>')
        for label, n, record in terms:
            body.append(f"<h3>{_e(record['kind'])} ({_e(label)}, window {n})</h3>")
            body.append(_kv(sorted(record["payload"].items())))
        body.append("<h2>Trace</h2>")
        calls = {call["call_id"]: call for call in run["cost"]["calls"]}
        for number, record in enumerate(result["trace"], 1):
            body.append(_trace_record(number, record, calls))
        if result["failure"] is not None:
            body += ["<h2>Failure</h2>", _kv(sorted(result["failure"].items()))]
        for heading, key in (
            ("Investigation", "investigation"),
            ("Thesis", "thesis"),
            ("Critique", "critique"),
        ):
            body.append(f"<h2>{heading}</h2>")
            body.append(_show(result[key]))
        assessment = result["assessment"]
        first = (
            "outcome",
            "confidence",
            "lean",
            "peer_comparison",
            "rationale",
            "key_uncertainties",
        )
        shown = assessment | {"peer_comparison": _peer_comparison(assessment)}
        body += [
            "<h2>Assessment</h2>",
            _kv(
                [(k, shown[k]) for k in first]
                + sorted((k, v) for k, v in assessment.items() if k not in first)
            ),
            "<h2>Tokens and cost</h2>",
            _cost(run["cost"]),
            "<h2>Journal events</h2>",
            _table(
                ("sequence", "event type", "event"),
                (
                    (
                        _e(event["sequence"]),
                        _e(event["event_type"]),
                        _folded("stored event", event["event"]),
                    )
                    for event in run["events"]
                ),
            ),
        ]
        return _page(f"Run {run['research_run_id']}", 1, body)


def _records(records: list[Any]) -> str:
    if not records:
        return '<p class="none">No records.</p>'
    return _table(
        ("sequence", "at", "kind", "payload"),
        (
            (
                _e(r["sequence"]),
                _e(r["at"]),
                _e(r["kind"]),
                _folded("stored payload", r["payload"]),
            )
            for r in records
        ),
    )


def _reask(request: Any) -> Any:
    """The stored re-ask feedback in a request's last message, if it carries one."""
    try:
        content = json.loads(request["messages"][-1]["content"])
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    data = content.get("research_data") if isinstance(content, dict) else None
    return data.get("output_rejection") if isinstance(data, dict) else None


def _trace_record(number: int, record: Any, calls: dict[str, Any]) -> str:
    kind = record["kind"]
    if kind == "transition":
        rows: list[tuple[str, Any]] = [
            ("source", record["source"]),
            ("target", record["target"]),
            ("termination reason", record["termination_reason"]),
        ]
        title = "node transition"
    elif kind == "model":
        request, reply = record["request"], record["reply"]
        title = f"model call: {request['stage']}"
        rows = [("call_id", request["call_id"]), ("reply", reply["kind"])]
        if (reask := _reask(request)) is not None:
            rows += [
                ("re-ask of", reask.get("call_id")),
                ("problems", reask.get("problems")),
            ]
        if reply["kind"] == "structured":
            rows.append(("content", reply["content"]))
        elif reply["kind"] == "tools":
            rows += [
                (
                    f"tool request {i}",
                    {"name": r["name"], "arguments": r["arguments_json"]},
                )
                for i, r in enumerate(reply["requests"], 1)
            ]
        elif reply["kind"] == "refusal":
            rows.append(("refusal", reply["refusal"]))
        else:
            rows += [("failure code", reply["code"]), ("message", reply["message"])]
        usage = calls.get(request["call_id"], {}).get("usage")
        rows.append(("tokens", usage if usage is not None else "not reported"))
    elif kind == "model_rejection":
        request, failure = record["request"], record["failure"]
        title = f"request rejection: {request['stage']}"
        rows = [
            ("call_id", request["call_id"]),
            ("failure code", failure["code"]),
            ("problem", failure["message"]),
        ]
    elif kind == "tool":
        title = "tool call"
        result, rejection = record["result"], record["rejection"]
        rows = [
            ("attempt_id", record["attempt_id"]),
            ("model_call_id", record["model_call_id"]),
        ]
        rows += [
            (f"tool {i}", {"name": r["name"], "arguments": r["arguments_json"]})
            for i, r in enumerate(record["requests"], 1)
        ]
        rows += [
            ("admitted", record["admitted"]),
            ("status", result["status"] if result else rejection["code"]),
        ]
        if result and result["status"] == "success":
            rows += [
                ("evidence_id", result["evidence"]["evidence_id"]),
                ("cached from", record["cached_from"]),
            ]
        elif result:
            rows.append(("unavailable reason", result["reason"]))
        if rejection:
            rows.append(("rejection", rejection["message"]))
    else:
        raise ViewerRefusal(f"bundle trace holds unknown record kind {kind}")
    details = ""
    if kind == "tool" and record["result"] and record["result"]["status"] == "success":
        details = _folded("payload", record["result"]["payload"]) + _folded(
            "evidence", record["result"]["evidence"]
        )
    elif kind == "tool" and record["result"]:
        details = _folded("result", record["result"])
    return (
        f'<section class="record"><h3>#{number} {_e(title)}</h3>'
        f"{_kv(rows)}{details}</section>"
    )


def _cost(cost: Any) -> str:
    def tokens(call: Any, field: str) -> str:
        usage = call["usage"]
        return '<span class="none">—</span>' if usage is None else _e(usage[field])

    rows = [
        (
            f"<code>{_e(c['call_id'])}</code>",
            _e(c["stage"]),
            _e(c["model"]),
            tokens(c, "input"),
            tokens(c, "output"),
            tokens(c, "cache_write"),
            tokens(c, "cache_read"),
            _e(c["estimate_usd"])
            if c["estimate_usd"] is not None
            else f"not available ({_e(c['status'])})",
        )
        for c in cost["calls"]
    ]
    total = cost["estimate_usd"]
    notes = [f'<p class="note">{_e(cost["label"])}</p>']
    shown = "not available" if total is None else _e(total)
    notes.append(f"<p>Run estimate (USD): {shown}</p>")
    if cost["usage_unreported"]:
        notes.append(
            "<p>Usage unreported: some calls have no provider-reported usage and "
            "contribute nothing.</p>"
        )
    if cost["unpriced_models"]:
        notes.append(
            "<p>Not in the price table: "
            + ", ".join(_e(m) for m in cost["unpriced_models"])
            + "</p>"
        )
    table = _table(
        (
            "call_id",
            "stage",
            "model",
            "input",
            "output",
            "cache write",
            "cache read",
            "estimate (USD)",
        ),
        rows,
    )
    return "".join(notes) + table


def render_site(bundle: Path, output: Path) -> None:
    """Verify the bundle, then write every page, or refuse and write nothing."""
    refuse_with_key()
    bundle, output = Path(bundle), Path(output)
    require_empty_output(output)
    if output.resolve().is_relative_to(bundle.resolve()):
        raise ViewerRefusal("the site cannot be written inside its bundle")
    manifest, files = read_bundle(bundle)
    try:
        pages = _Site(manifest, files).pages()
    except ViewerRefusal:
        raise
    except (KeyError, TypeError, AttributeError, ValueError, IndexError) as error:
        raise ViewerRefusal(
            f"bundle content is malformed: {type(error).__name__}"
        ) from error
    publish(pages, output)


def bundle_name(bundle: Path) -> str:
    """The verified bundle's name, for the command line's default site path."""
    _, files = read_bundle(bundle)
    if INDEX not in files:
        raise ViewerRefusal("bundle has no index.json")
    name = decode(files[INDEX], INDEX).get("bundle")
    if not isinstance(name, str):
        raise ViewerRefusal("bundle index has no name")
    return name
