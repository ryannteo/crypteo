"""The local run viewer (ADR-022): export to a pinned bundle, render to pages."""

import ast
import asyncio
import json
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
from contextlib import closing
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, NamedTuple

import httpx2
import pytest
from research_support import MODEL
from test_anthropic_research import graph_responses
from test_evaluation_run import WindowModel, evaluate
from test_replay import CODE
from test_research_journal_claude import durable
from test_research_persistence import ObservedModel, execute
from test_research_reasks import PROBLEMS, reask_steps

import crypteo_viewer
from crypteo.evaluation import load_evaluation
from crypteo.research_persistence import ResearchStore
from crypteo_viewer.__main__ import main
from crypteo_viewer.bundle import ViewerRefusal, digest, encode
from crypteo_viewer.export import (
    COST_LABEL,
    estimate_cost,
    export_bundle,
    run_document,
)
from crypteo_viewer.render import evaluation_page, render_site, run_page

HOSTILE = "<script>alert(\"x\")</script> & 'q'"
ESCAPED = "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; &#x27;q&#x27;"
PRICES = {
    MODEL: {"input": "3", "output": "15", "cache_write": "3.75", "cache_read": "0.3"}
}
SOURCE = Path(crypteo_viewer.__file__).parent


class Source(NamedTuple):
    root: Path
    spec: Path
    evaluation: Path
    store: Path


@pytest.fixture(autouse=True)
def no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def write_spec(
    root: Path, evaluation: str, store: str, name: str = "spec.json"
) -> Path:
    spec = root / name
    spec.write_text(
        json.dumps(
            {
                "bundle": "viewer-test",
                "prices": PRICES,
                "trials": [
                    {
                        "name": f"trial {HOSTILE}",
                        "evaluations": [
                            {
                                "label": f"label {HOSTILE}",
                                "evaluation": evaluation,
                                "store": store,
                            }
                        ],
                        "attachments": ["analysis.json"],
                    }
                ],
            }
        )
    )
    return spec


@pytest.fixture(scope="module")
def source(tmp_path_factory: pytest.TempPathFactory) -> Source:
    """A completed TRADE / NO_TRADE / ABSTAIN evaluation and its store."""
    root = tmp_path_factory.mktemp("viewer-source")
    store = ResearchStore(root / "research.sqlite")
    evaluate(root / "run", store, WindowModel())
    (root / "analysis.json").write_text(json.dumps({"note": HOSTILE}) + "\n")
    spec = write_spec(root, "run/evaluation.json", "research.sqlite")
    return Source(root, spec, root / "run" / "evaluation.json", store.path)


class Built(NamedTuple):
    bundle: Path
    site: Path


@pytest.fixture(scope="module")
def built(source: Source, tmp_path_factory: pytest.TempPathFactory) -> Built:
    out = tmp_path_factory.mktemp("viewer-out")
    export_bundle(source.spec, out / "bundle", code=CODE)
    render_site(out / "bundle", out / "site")
    return Built(out / "bundle", out / "site")


def tree(directory: Path) -> dict[str, bytes]:
    return {
        p.relative_to(directory).as_posix(): p.read_bytes()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def load(bundle: Path, name: str) -> Any:
    return json.loads((bundle / name).read_bytes())


def stored(source: Source) -> Any:
    return json.loads(source.evaluation.read_bytes())


def cells(row: str) -> list[str]:
    return [
        unescape(re.sub(r"<[^>]+>", "", cell))
        for cell in re.findall(r"<td>(.*?)</td>", row)
    ]


def windows_rows(page: str) -> list[list[str]]:
    table = page.split("<h2>Windows</h2>", 1)[1].split("</table>", 1)[0]
    return [cells(row) for row in re.findall(r"<tr>(.*?)</tr>", table)[1:]]


def test_round_trip_shows_every_window_and_run_with_stored_values(
    source: Source, built: Built
) -> None:
    raw = stored(source)
    index = load(built.bundle, "index.json")
    (trial,) = index["trials"]
    (entry,) = trial["evaluations"]
    assert entry["evaluation_id"] == raw["evaluation_id"]
    evaluation = load(built.bundle, entry["file"])
    page = (built.site / evaluation_page(raw["evaluation_id"])).read_text()
    rows = windows_rows(page)
    windows = raw["comparison"]["windows"]
    assert len(rows) == len(evaluation["windows"]) == len(windows) == 3
    assert [w["candidate"]["outcome"] for w in windows] == [
        "TRADE",
        "NO_TRADE",
        "ABSTAIN",
    ]
    for number, (row, window, exported) in enumerate(
        zip(rows, windows, evaluation["windows"], strict=True), 1
    ):
        candidate = window["candidate"]
        # Values exactly as stored, including the decimal spelling of P&L.
        assert exported["candidate"] == candidate
        assert f'id="window-{number}"' in page
        run_id = exported["research_run_id"]
        with closing(
            sqlite3.connect(source.store.as_uri() + "?mode=ro", uri=True)
        ) as db:
            (artifact_json,) = db.execute(
                "SELECT artifact_json FROM research_artifacts_v1 "
                "WHERE research_run_id=?",
                (run_id,),
            ).fetchone()
        run = load(built.bundle, "runs/" + digest(run_id.encode())[7:] + ".json")
        assert run["artifact"] == json.loads(artifact_json)
        result = run["artifact"]["result"]
        assessment = result["assessment"]
        assert row[2] == candidate["outcome"] == assessment["outcome"]
        confidence = candidate["confidence"]
        assert row[3] == ("—" if confidence is None else str(confidence))
        assert row[4] == (assessment["lean"] or "—")
        # The peer comparison sits beside the lean (ADR-025).
        assert row[5] == assessment["peer_comparison"] == "SIMILAR"
        assert row[6] == candidate["baseline"]["status"] == "CLOSED"
        assert row[7] == candidate["baseline"]["exit_reason"]
        assert row[8] == candidate["baseline"]["net_pnl"]
        run_html = (built.site / run_page(run_id)).read_text()
        assert f"../{run_page(run_id)}" in page
        evidence = next(
            r["result"]["evidence"]["evidence_id"]
            for r in result["trace"]
            if r["kind"] == "tool"
        )
        for text in (
            evidence,
            assessment["outcome"],
            assessment["rationale"],
            result["thesis"]["summary"],
            result["thesis"]["bull_case"],
            result["critique"]["summary"],
            *result["critique"]["missing_evidence"],
        ):
            assert text in run_html
        if assessment["lean"] is not None:
            assert f"<td>{assessment['lean']}</td>" in run_html
        assert (
            f"<th>lean</th><td>{assessment['lean']}</td></tr>"
            f"<tr><th>peer_comparison</th><td>{assessment['peer_comparison']}</td>"
        ) in run_html
        if assessment["confidence"] is not None:
            assert f"<td>{assessment['confidence']}</td>" in run_html
        assert [e["sequence"] for e in run["events"]] == list(range(len(run["events"])))
        assert "TERMS" in run_html and "bollinger_position" in run_html
    assert sorted(p.name for p in (built.site / "runs").iterdir()) == sorted(
        Path(run_page(w["research_run_id"])).name for w in evaluation["windows"]
    )
    assert (built.site / "index.html").is_file()
    assert (built.site / "style.css").is_file()
    # Scan, delivery and observation records are left out.
    kinds = {
        r["kind"]
        for w in evaluation["windows"]
        for r in w["baseline_records"] + w["research_records"]
    }
    assert kinds.isdisjoint({"SCAN", "DELIVERY", "OBSERVATION"})
    assert {"TERMS", "CLOSED_TRADE", "RESEARCH"} <= kinds
    assert evaluation["metrics"] == raw["comparison"]["metrics"]
    assert evaluation["plan"]["candidates"] == raw["plan"]["candidates"]
    manifest = load(built.bundle, "manifest.json")
    assert manifest["bundle_format"] == "run_viewer_bundle_v1"
    assert manifest["code"] == CODE.model_dump(mode="json")
    assert manifest["inputs"] == [
        {"role": role, "name": path.name, "sha256": digest(path.read_bytes())}
        for role, path in (
            ("evaluation", source.evaluation),
            ("store", source.store),
            ("attachment", source.root / "analysis.json"),
        )
    ]
    assert str(source.root) not in (built.bundle / "manifest.json").read_text()
    assert set(manifest["files"]) == set(tree(built.bundle)) - {"manifest.json"}
    for data in tree(built.bundle).values():
        if not data.startswith(b'{"note"'):
            assert data == encode(json.loads(data))


def priced_store(tmp_path: Path) -> tuple[ResearchStore, Any]:
    """A stored Claude-adapter run with varied usage; its last call reports none."""
    bodies = graph_responses()
    usages: list[dict[str, Any] | None] = [
        {
            "input_tokens": 1200,
            "output_tokens": 300,
            "cache_creation_input_tokens": 1000,
            "cache_read_input_tokens": 0,
        },
        {
            "input_tokens": 50,
            "output_tokens": 40,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 1000,
        },
        {"input_tokens": 7, "output_tokens": 11},
        {
            "input_tokens": 3,
            "output_tokens": 5,
            "cache_creation_input_tokens": None,
            "cache_read_input_tokens": 2,
        },
        None,
    ]
    for body, usage in zip(bodies, usages, strict=True):
        if usage is None:
            del body["usage"]
        else:
            body["usage"] = usage
    return durable(tmp_path, [httpx2.Response(200, json=b) for b in bodies])


def test_cost_estimate_from_reported_usage_and_price_table(tmp_path: Path) -> None:
    _, artifact = priced_store(tmp_path)
    cost = estimate_cost(artifact.result, PRICES)
    # (in*3 + out*15 + write*3.75 + read*0.3) / 1e6, by hand:
    # 11850, 1050, 186 and 84.6 micro-dollars; the last call reported nothing.
    assert [c["estimate_usd"] for c in cost["calls"]] == [
        "0.01185",
        "0.00105",
        "0.000186",
        "0.0000846",
        None,
    ]
    assert [c["status"] for c in cost["calls"]] == ["ESTIMATED"] * 4 + [
        "USAGE_UNREPORTED"
    ]
    assert cost["estimate_usd"] == "0.0131706"
    assert cost["usage_unreported"] is True
    assert cost["unpriced_models"] == []
    assert "estimate" in cost["label"] and "List-price" in cost["label"]
    unpriced = estimate_cost(artifact.result, {"another-model": PRICES[MODEL]})
    assert unpriced["estimate_usd"] is None
    assert unpriced["unpriced_models"] == [MODEL]
    assert [c["estimate_usd"] for c in unpriced["calls"]] == [None] * 5
    assert [c["status"] for c in unpriced["calls"]] == ["MODEL_NOT_PRICED"] * 4 + [
        "USAGE_UNREPORTED"
    ]


def test_runs_without_reported_usage_are_marked(built: Built) -> None:
    for run_file in (built.bundle / "runs").iterdir():
        cost = json.loads(run_file.read_bytes())["cost"]
        assert cost["usage_unreported"] is True and cost["estimate_usd"] is None
        page = (built.site / "runs" / (run_file.stem + ".html")).read_text()
        assert "Run estimate (USD): not available" in page
        assert "not available (USAGE_UNREPORTED)" in page
        assert "Usage unreported" in page


def test_exports_and_renders_are_byte_identical(
    source: Source, built: Built, tmp_path: Path
) -> None:
    export_bundle(source.spec, tmp_path / "bundle", code=CODE)
    assert tree(tmp_path / "bundle") == tree(built.bundle)
    render_site(built.bundle, tmp_path / "site-1")
    render_site(tmp_path / "bundle", tmp_path / "site-2")
    assert tree(tmp_path / "site-1") == tree(tmp_path / "site-2") == tree(built.site)


def test_originals_are_unchanged_and_nothing_appears_beside_them(
    source: Source, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = tree(source.root)
    opened: list[str] = []
    connect = sqlite3.connect

    def recording(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        opened.append(str(database))
        connection: sqlite3.Connection = connect(database, *args, **kwargs)
        return connection

    monkeypatch.setattr(sqlite3, "connect", recording)
    # Read-only originals: opening one for writing would fail the export.
    paths = sorted(source.root.rglob("*"), reverse=True) + [source.root]
    modes = {path: path.stat().st_mode for path in paths}
    for path in paths:
        path.chmod(0o555 if path.is_dir() else 0o444)
    try:
        export_bundle(source.spec, tmp_path / "bundle", code=CODE)
    finally:
        for path in reversed(paths):
            path.chmod(modes[path])
    assert tree(source.root) == before
    assert sorted(p.name for p in source.root.iterdir()) == [
        "analysis.json",
        "research.sqlite",
        "run",
        "spec.json",
    ]
    assert opened and not any(source.root.as_uri() in uri for uri in opened)
    assert not any(str(source.root) in uri for uri in opened)


def test_an_original_changed_during_export_refuses(
    source: Source, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import crypteo_viewer.export as export_module

    root = Path(shutil.copytree(source.root, tmp_path / "source"))
    loader = load_evaluation

    def changing(encoded: bytes, *, store: ResearchStore) -> Any:
        with (root / "analysis.json").open("a") as handle:
            handle.write("\n")
        return loader(encoded, store=store)

    monkeypatch.setattr(export_module, "load_evaluation", changing)
    output = tmp_path / "out" / "bundle"
    output.parent.mkdir()
    with pytest.raises(ViewerRefusal, match="analysis.json changed during export"):
        export_bundle(root / "spec.json", output, code=CODE)
    assert_no_output(output)


def assert_no_output(output: Path) -> None:
    assert not output.exists()
    assert list(output.parent.iterdir()) == []


def test_missing_input_refuses_without_output(source: Source, tmp_path: Path) -> None:
    spec = write_spec(source.root, "run/missing.json", "research.sqlite", "m.json")
    output = tmp_path / "out" / "bundle"
    output.parent.mkdir()
    try:
        with pytest.raises(ViewerRefusal, match="missing.json does not exist"):
            export_bundle(spec, output, code=CODE)
    finally:
        spec.unlink()
    assert_no_output(output)


def test_run_that_fails_to_load_refuses_without_output(
    source: Source, tmp_path: Path
) -> None:
    data = source.store.read_bytes()
    marker = b"Independent challenge."
    assert marker in data
    shutil.copytree(source.root / "run", tmp_path / "run")
    shutil.copy(source.root / "analysis.json", tmp_path)
    (tmp_path / "research.sqlite").write_bytes(
        data.replace(marker, b"Independent challengE.")
    )
    spec = write_spec(tmp_path, "run/evaluation.json", "research.sqlite")
    output = tmp_path / "out" / "bundle"
    output.parent.mkdir()
    with pytest.raises(ViewerRefusal, match="failed to load: ResearchStorageError"):
        export_bundle(spec, output, code=CODE)
    assert_no_output(output)


def test_non_empty_output_refuses_and_is_left_alone(
    source: Source, built: Built, tmp_path: Path
) -> None:
    for step in ("export", "render"):
        output = tmp_path / step
        output.mkdir()
        (output / "keep.txt").write_text("owner file")
        with pytest.raises(ViewerRefusal, match="is not empty"):
            if step == "export":
                export_bundle(source.spec, output, code=CODE)
            else:
                render_site(built.bundle, output)
        assert tree(output) == {"keep.txt": b"owner file"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["export", "render"]


def test_empty_output_directory_is_filled(source: Source, tmp_path: Path) -> None:
    (tmp_path / "bundle").mkdir()
    export_bundle(source.spec, tmp_path / "bundle", code=CODE)
    assert (tmp_path / "bundle" / "manifest.json").is_file()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bundle"]


def run_file(bundle: Path) -> Path:
    return sorted((bundle / "runs").iterdir())[0]


def tamper_content(bundle: Path) -> None:
    path = run_file(bundle)
    path.write_bytes(path.read_bytes().replace(b"Draft thesis.", b"Draft thesiS."))


def tamper_missing(bundle: Path) -> None:
    run_file(bundle).unlink()


def tamper_unlisted(bundle: Path) -> None:
    (bundle / "runs" / "extra.json").write_text("{}\n")


def tamper_format(bundle: Path) -> None:
    path = bundle / "manifest.json"
    manifest = json.loads(path.read_bytes())
    path.write_bytes(encode(manifest | {"bundle_format": "run_viewer_bundle_v0"}))


@pytest.mark.parametrize(
    ("tamper", "match"),
    [
        (tamper_content, "does not match its SHA-256"),
        (tamper_missing, "is missing"),
        (tamper_unlisted, "runs/extra.json is not listed"),
        (tamper_format, "not bundle format run_viewer_bundle_v1"),
    ],
)
def test_render_refuses_a_tampered_bundle_before_writing(
    built: Built, tmp_path: Path, tamper: Any, match: str
) -> None:
    bundle = Path(shutil.copytree(built.bundle, tmp_path / "bundle"))
    tamper(bundle)
    output = tmp_path / "out" / "site"
    output.parent.mkdir()
    with pytest.raises(ViewerRefusal, match=match):
        render_site(bundle, output)
    assert_no_output(output)


def imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"{path.name} uses a relative import"
            assert node.module is not None
            found.add(node.module)
    return found


def test_crypteo_never_imports_the_viewer() -> None:
    package = SOURCE.parent / "crypteo"
    sources = sorted(package.rglob("*.py"))
    assert len(sources) > 30
    for path in sources:
        assert not any(
            name.split(".")[0] == "crypteo_viewer" for name in imports(path)
        ), path
        assert "crypteo_viewer" not in path.read_text(), path


def test_render_imports_only_the_standard_library_and_its_package() -> None:
    seen: set[str] = set()
    pending = ["crypteo_viewer", "crypteo_viewer.render"]
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        path = SOURCE.parent.joinpath(*module.split("."))
        path = path / "__init__.py" if path.is_dir() else path.with_suffix(".py")
        for name in imports(path):
            top = name.split(".")[0]
            if top == "crypteo_viewer":
                pending.append(name)
            else:
                assert top in sys.stdlib_module_names, (module, name)
    assert seen == {"crypteo_viewer", "crypteo_viewer.render", "crypteo_viewer.bundle"}
    # The command line imports export (and so crypteo) only inside ``export``.
    main_module = ast.parse((SOURCE / "__main__.py").read_text())
    top_level = {
        node.module if isinstance(node, ast.ImportFrom) else alias.name
        for node in main_module.body
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert top_level == {
        "argparse",
        "sys",
        "collections.abc",
        "pathlib",
        "crypteo_viewer.bundle",
    }


def test_export_and_render_make_no_network_connection(
    source: Source, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def blocked(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network access attempted")

    for name in ("connect", "connect_ex", "sendto", "bind"):
        monkeypatch.setattr(socket.socket, name, blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    export_bundle(source.spec, tmp_path / "bundle", code=CODE)
    render_site(tmp_path / "bundle", tmp_path / "site")
    assert (tmp_path / "site" / "index.html").is_file()


def test_both_commands_refuse_with_a_key_before_reading_input(
    source: Source,
    built: Built,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-a-secret")

    def unreadable(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("input read while a key is set")

    with monkeypatch.context() as patched:
        for name in ("read_bytes", "read_text", "is_file", "is_dir", "open"):
            patched.setattr(Path, name, unreadable)
        for argv in (["export", str(source.spec)], ["render", str(built.bundle)]):
            assert main(argv, artifacts=tmp_path) == 1
            err = capsys.readouterr().err
            assert err.count("\n") == 1 and "ANTHROPIC_API_KEY is set" in err
    with pytest.raises(ViewerRefusal, match="ANTHROPIC_API_KEY"):
        export_bundle(source.spec, tmp_path / "b", code=CODE)
    with pytest.raises(ViewerRefusal, match="ANTHROPIC_API_KEY"):
        render_site(built.bundle, tmp_path / "s")
    assert list(tmp_path.iterdir()) == []
    for command in ("export", "render"):
        completed = subprocess.run(
            [sys.executable, "-m", "crypteo_viewer", command, str(tmp_path / "x")],
            capture_output=True,
            text=True,
            check=False,
            env={"PATH": "", "ANTHROPIC_API_KEY": "test-only-not-a-secret"},
        )
        assert completed.returncode == 1
        assert completed.stderr == (
            "crypteo_viewer: refused: refusing to run while ANTHROPIC_API_KEY is set\n"
        )


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, list[tuple[str, str | None]]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, attrs))


ALLOWED_TAGS = {
    "html", "head", "meta", "title", "link", "body", "nav", "h1", "h2", "h3", "p",
    "a", "div", "table", "tr", "th", "td", "pre", "code", "ul", "li", "details",
    "summary", "section", "span",
}  # fmt: skip


def assert_isolated(site: Path) -> None:
    pages = sorted(site.rglob("*.html"))
    assert pages
    for page in pages:
        text = page.read_text()
        assert "<script" not in text.lower()
        parser = _Tags()
        parser.feed(text)
        for tag, attributes in parser.tags:
            assert tag in ALLOWED_TAGS, (page, tag)
            for name, value in attributes:
                assert not name.startswith("on"), (page, name)
                if name in ("href", "src"):
                    assert value is not None and name == "href"
                    target = value.split("#", 1)[0]
                    assert ":" not in target and not target.startswith("/")
                    if target:
                        resolved = (page.parent / target).resolve()
                        assert resolved.is_relative_to(site.resolve())
                        assert resolved.is_file(), (page, value)


def reseal(bundle: Path) -> None:
    path = bundle / "manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["files"] = {
        name: digest(data)
        for name, data in tree(bundle).items()
        if name != "manifest.json"
    }
    path.write_bytes(encode(manifest))


def test_stored_text_is_escaped_and_pages_are_isolated(
    built: Built, tmp_path: Path
) -> None:
    index = (built.site / "index.html").read_text()
    assert f"trial {ESCAPED}" in index and f"label {ESCAPED}" in index
    (evaluation,) = (built.site / "evaluations").iterdir()
    page = evaluation.read_text()
    assert ESCAPED.replace("&quot;", "\\&quot;") in page  # the attachment, verbatim
    assert digest((built.bundle / "attachments/000/analysis.json").read_bytes()) in page
    assert_isolated(built.site)
    # Model-authored text as stored in a run renders escaped too.
    bundle = Path(shutil.copytree(built.bundle, tmp_path / "bundle"))
    path = run_file(bundle)
    run = json.loads(path.read_bytes())
    run["artifact"]["result"]["thesis"]["summary"] = HOSTILE
    run["artifact"]["result"]["assessment"]["rationale"] = HOSTILE
    path.write_bytes(encode(run))
    reseal(bundle)
    render_site(bundle, tmp_path / "site")
    html = (tmp_path / "site" / "runs" / (path.stem + ".html")).read_text()
    assert html.count(ESCAPED) >= 2
    assert_isolated(tmp_path / "site")


def test_command_line_writes_only_under_artifacts(
    source: Source, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifacts = tmp_path / "artifacts"
    outside = tmp_path / "elsewhere"
    for argv in (
        ["export", str(source.spec), "--out", str(outside)],
        ["export", str(source.spec), "--out", str(artifacts)],
        ["export", str(source.spec), "--out", str(artifacts / ".." / "escape")],
    ):
        assert main(argv, artifacts=artifacts) == 1
        err = capsys.readouterr().err
        assert err.count("\n") == 1 and "output must be inside" in err
    # The real checkout's artifacts/ directory is the default root.
    assert main(["export", str(source.spec), "--out", str(outside)]) == 1
    assert "output must be inside" in capsys.readouterr().err
    assert sorted(p.name for p in tmp_path.iterdir()) == []


def test_command_line_defaults_under_artifacts_viewer(
    source: Source, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import crypteo_viewer.export as export_module

    exported = export_module.export_bundle

    def pinned(spec: Path, output: Path) -> None:
        exported(spec, output, code=CODE)

    monkeypatch.setattr(export_module, "export_bundle", pinned)
    artifacts = tmp_path / "artifacts"
    assert main(["export", str(source.spec)], artifacts=artifacts) == 0
    bundle = artifacts / "viewer" / "viewer-test" / "bundle"
    assert main(["render", str(bundle)], artifacts=artifacts) == 0
    assert (artifacts / "viewer" / "viewer-test" / "site" / "index.html").is_file()
    assert main(["render", str(bundle)], artifacts=artifacts) == 1


def with_runs(built: Built, tmp_path: Path, runs: list[dict[str, Any]]) -> Path:
    """The round-trip bundle plus further stored runs, resealed, then rendered."""
    bundle = Path(shutil.copytree(built.bundle, tmp_path / "bundle"))
    index = load(bundle, "index.json")
    for run in runs:
        name = "runs/" + digest(run["research_run_id"].encode())[7:] + ".json"
        (bundle / name).write_bytes(encode(run))
        index["runs"].append({"research_run_id": run["research_run_id"], "file": name})
    (bundle / "index.json").write_bytes(encode(index))
    reseal(bundle)
    render_site(bundle, tmp_path / "site")
    return tmp_path / "site"


def windows_header(page: str) -> list[str]:
    table = page.split("<h2>Windows</h2>", 1)[1].split("</table>", 1)[0]
    return re.findall(r"<th>(.*?)</th>", table)


def test_windows_table_shows_peer_comparison_beside_the_lean(built: Built) -> None:
    (evaluation,) = (built.site / "evaluations").iterdir()
    header = windows_header(evaluation.read_text())
    assert header[header.index("lean") + 1] == "peer comparison"


NOT_STORED = '<span class="none">not stored</span>'


def test_bundle_exported_before_adr_025_renders_without_peer_comparison(
    built: Built, tmp_path: Path
) -> None:
    """Bundles such as trial 1's carry no peer_comparison key and still render."""
    bundle = Path(shutil.copytree(built.bundle, tmp_path / "bundle"))
    leans = {}
    for path in sorted((bundle / "runs").iterdir()):
        run = json.loads(path.read_bytes())
        assessment = run["artifact"]["result"]["assessment"]
        del assessment["peer_comparison"]
        leans[run["research_run_id"]] = assessment["lean"]
        path.write_bytes(encode(run))
    reseal(bundle)
    render_site(bundle, tmp_path / "site")
    site = tmp_path / "site"
    (evaluation,) = (site / "evaluations").iterdir()
    page = evaluation.read_text()
    rows = windows_rows(page)
    (entry,) = load(bundle, "index.json")["trials"][0]["evaluations"]
    order = [w["research_run_id"] for w in load(bundle, entry["file"])["windows"]]
    assert len(rows) == len(leans) == len(order) == 3
    assert [row[4] for row in rows] == [leans[run_id] or "—" for run_id in order]
    assert [row[5] for row in rows] == ["not stored"] * 3
    assert page.count(f"<td>{NOT_STORED}</td>") == 3
    for run_id, lean in leans.items():
        run_html = (site / run_page(run_id)).read_text()
        assert f"<th>lean</th><td>{lean}</td>" in run_html
        assert f"<th>peer_comparison</th><td>{NOT_STORED}</td>" in run_html
    assert_isolated(site)


def test_null_peer_comparison_renders_as_stored(built: Built, tmp_path: Path) -> None:
    """A stored null (trader-led or runtime abstention) is not "not stored"."""
    bundle = Path(shutil.copytree(built.bundle, tmp_path / "bundle"))
    path = run_file(bundle)
    run = json.loads(path.read_bytes())
    run["artifact"]["result"]["assessment"]["peer_comparison"] = None
    path.write_bytes(encode(run))
    reseal(bundle)
    render_site(bundle, tmp_path / "site")
    html = (tmp_path / "site" / "runs" / (path.stem + ".html")).read_text()
    assert '<th>peer_comparison</th><td><span class="none">—</span></td>' in html
    assert NOT_STORED not in html


def test_run_page_shows_reasks_with_their_problems(
    built: Built, tmp_path: Path
) -> None:
    store = ResearchStore(tmp_path / "reask.sqlite")
    asyncio.run(execute(store, ObservedModel(reask_steps("final_assessment"))))
    run = run_document(store, "run-1", PRICES, {"name": "reask.sqlite", "sha256": ""})
    site = with_runs(built, tmp_path, [run])
    page = (site / run_page("run-1")).read_text()
    calls = [r for r in run["artifact"]["result"]["trace"] if r["kind"] == "model"]
    rejected, reask = calls[-2], calls[-1]
    assert rejected["request"]["stage"] == reask["request"]["stage"]
    assert page.count("<th>re-ask of</th>") == 1
    assert f"<th>re-ask of</th><td>{rejected['request']['call_id']}</td>" in page
    for problem in PROBLEMS["final_assessment"]:
        assert f"<li>{escape(problem)}</li>" in page
    assert_isolated(site)


def test_run_page_shows_the_cost_estimate_of_a_priced_run(
    built: Built, tmp_path: Path
) -> None:
    store, artifact = priced_store(tmp_path)
    run = run_document(store, "run-1", PRICES, {"name": "r.sqlite", "sha256": ""})
    assert run["cost"] == estimate_cost(artifact.result, PRICES)
    assert run["artifact"] == artifact.model_dump(mode="json")
    page = (with_runs(built, tmp_path, [run]) / run_page("run-1")).read_text()
    for estimate in ("0.01185", "0.00105", "0.000186", "0.0000846"):
        assert f"<td>{estimate}</td>" in page
    assert "Run estimate (USD): 0.0131706" in page
    assert page.count("not available (USAGE_UNREPORTED)") == 1
    assert "Usage unreported" in page
    assert escape(COST_LABEL) in page


def test_evaluations_sharing_a_store_export_and_render_together(
    source: Source, tmp_path: Path
) -> None:
    store = ResearchStore(tmp_path / "shared.sqlite")
    for trial in ("trial-a", "trial-b"):
        evaluate(tmp_path / trial, store, WindowModel(), trial=trial)
    shutil.copy(source.root / "analysis.json", tmp_path)
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "bundle": "shared",
                "prices": PRICES,
                "trials": [
                    {
                        "name": "shared store",
                        "evaluations": [
                            {
                                "label": trial,
                                "evaluation": f"{trial}/evaluation.json",
                                "store": "shared.sqlite",
                            }
                            for trial in ("trial-a", "trial-b")
                        ],
                    }
                ],
            }
        )
    )
    export_bundle(spec, tmp_path / "out" / "bundle", code=CODE)
    bundle = tmp_path / "out" / "bundle"
    render_site(bundle, tmp_path / "out" / "site")
    manifest, index = load(bundle, "manifest.json"), load(bundle, "index.json")
    assert [(i["role"], i["name"]) for i in manifest["inputs"]] == [
        ("evaluation", "evaluation.json"),
        ("evaluation", "evaluation.json"),
        ("store", "shared.sqlite"),
    ]
    (trial,) = index["trials"]
    assert [e["label"] for e in trial["evaluations"]] == ["trial-a", "trial-b"]
    evaluations = [load(bundle, e["file"]) for e in trial["evaluations"]]
    assert [e["trial"] for e in evaluations] == ["trial-a", "trial-b"]
    run_ids = [w["research_run_id"] for e in evaluations for w in e["windows"]]
    assert len(run_ids) == len(set(run_ids)) == len(index["runs"]) == 6
    site = tmp_path / "out" / "site"
    for evaluation in evaluations:
        assert (site / evaluation_page(evaluation["evaluation_id"])).is_file()
        store_input = {k: manifest["inputs"][2][k] for k in ("name", "sha256")}
        assert evaluation["source"]["store"] == store_input
    for run_id in run_ids:
        assert (site / run_page(run_id)).is_file()
    assert_isolated(site)
