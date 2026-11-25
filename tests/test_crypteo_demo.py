"""The one-command demo on synthetic data with a scripted model."""

import ast
import contextlib
import decimal
import io
import json
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, NamedTuple

import pytest

import crypteo_demo
from crypteo.domain.evaluation import EvaluationResult
from crypteo.domain.market import TradingPair
from crypteo_demo import LABEL
from crypteo_demo.__main__ import TRACING_VARIABLES, default_output, main
from crypteo_demo.market import END, START, SyntheticTransport
from crypteo_demo.pipeline import CODE, TRIAL

SOURCE = Path(crypteo_demo.__file__).parent
WINDOWS = [
    ("2000-01-06 03:00", "ETH-USD", "NO_TRADE", "AGAINST", "WORSE"),
    ("2000-01-06 08:00", "BTC-USD", "TRADE", "FOR", "BETTER"),
    ("2000-01-06 16:00", "SOL-USD", "ABSTAIN", "BALANCED", "SIMILAR"),
    ("2000-01-07 06:00", "BTC-USD", "TRADE", "FOR", "BETTER"),
]


class Run(NamedTuple):
    output: Path
    status: int
    printed: str


def run(output: Path) -> Run:
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        status = main([str(output)])
    return Run(output, status, printed.getvalue())


def tree(directory: Path) -> dict[str, bytes]:
    return {
        p.relative_to(directory).as_posix(): p.read_bytes()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


@pytest.fixture(autouse=True)
def no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ANTHROPIC_API_KEY", *TRACING_VARIABLES):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="module")
def first(tmp_path_factory: pytest.TempPathFactory) -> Run:
    with pytest.MonkeyPatch.context() as patched:
        for name in ("ANTHROPIC_API_KEY", *TRACING_VARIABLES):
            patched.delenv(name, raising=False)
        return run(tmp_path_factory.mktemp("demo") / "out")


def pages(output: Path) -> dict[str, str]:
    site = output / "viewer" / "site"
    return {
        p.relative_to(site).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(site.rglob("*.html"))
    }


def test_demo_runs_end_to_end(first: Run) -> None:
    output, status, printed = first
    assert status == 0
    result = EvaluationResult.model_validate_json(
        (output / "evaluation" / "evaluation.json").read_bytes()
    )
    plan = result.plan
    assert plan.settings.purpose == "PLUMBING" and plan.settings.limit == 4
    assert [
        (f"{c.opportunity.observed_at:%Y-%m-%d %H:%M}", c.opportunity.pair.value)
        for c in plan.candidates
        if c.decision == "SELECTED"
    ] == [(at, pair) for at, pair, *_ in WINDOWS]
    assert [c.exclusion_reason for c in plan.candidates].count(
        "OVERLAPPING_CANDIDATE"
    ) == 1
    windows = result.comparison.windows
    assert [w.candidate.outcome.value for w in windows] == [w[2] for w in WINDOWS]
    assert result.trial == TRIAL
    assert {w.research.manifest.code for w in windows} == {CODE}
    # The baseline trades every window; research takes the two BTC-USD trades.
    assert result.comparison.metrics.baseline_closed_count == 4
    assert result.comparison.metrics.research_closed_count == 2
    for number, (at, pair, outcome, lean, peer) in enumerate(WINDOWS, 1):
        assert (
            f"  window {number}  {at}  {pair}  {outcome}  lean {lean}"
            f"  peer comparison {peer}\n"
        ) in printed
    reproduced = EvaluationResult.model_validate_json(
        (output / "reproduction" / "evaluation.json").read_bytes()
    )
    assert reproduced.comparison.metrics == result.comparison.metrics
    assert [w.candidate for w in reproduced.comparison.windows] == [
        w.candidate for w in windows
    ]
    assert (
        "frozen reproduction with no model: metrics equal True, candidates equal True\n"
    ) in printed
    index = output / "viewer" / "site" / "index.html"
    assert printed.endswith(f"viewer index: {index.resolve()}\n")
    assert set(pages(output)) == {"index.html"} | {
        f"{folder}/{p.name}"
        for folder in ("evaluations", "runs")
        for p in (output / "viewer" / "site" / folder).iterdir()
    }
    assert len(list((output / "viewer" / "site" / "runs").iterdir())) == 4


def test_scripted_runs_call_tools_and_state_lean_and_peer_comparison(
    first: Run,
) -> None:
    for name in sorted((first.output / "viewer" / "bundle" / "runs").iterdir()):
        result = json.loads(name.read_bytes())["artifact"]["result"]
        assert result["status"] == "COMPLETED"
        assert result["configuration"]["model"]["provider"] == "fake"
        tools = [r for r in result["trace"] if r["kind"] == "tool"]
        assert [t["requests"][0]["name"] for t in tools] == [
            "bollinger_position",
            "trend",
        ]
        assert all(t["result"]["status"] == "success" for t in tools)
        for stage in ("investigation", "thesis", "critique"):
            assert result[stage]["summary"].startswith("Scripted demo text")
        assessment = result["assessment"]
        assert assessment["lean"] is not None
        assert assessment["peer_comparison"] is not None


def test_two_runs_are_byte_identical(first: Run, tmp_path: Path) -> None:
    second = run(tmp_path / "out")
    assert second.status == 0
    assert tree(second.output) == tree(first.output)
    # Only the printed index path names the output directory.
    assert second.printed.replace(
        str(second.output.resolve()), "OUT"
    ) == first.printed.replace(str(first.output.resolve()), "OUT")


def test_demo_makes_no_network_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def blocked(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network access attempted")

    for name in ("connect", "connect_ex", "sendto", "sendall", "bind"):
        monkeypatch.setattr(socket.socket, name, blocked)
    for name in ("create_connection", "getaddrinfo", "gethostbyname"):
        monkeypatch.setattr(socket, name, blocked)
    assert run(tmp_path / "out").status == 0
    assert (tmp_path / "out" / "viewer" / "site" / "index.html").is_file()


def test_refuses_with_a_key_before_writing_anything(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-not-a-secret")

    def unwritable(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("written while a key is set")

    with monkeypatch.context() as patched:
        for name in ("mkdir", "write_bytes", "write_text", "open", "touch"):
            patched.setattr(Path, name, unwritable)
        assert main([str(tmp_path / "out")]) == 1
        assert main([]) == 1
    assert capsys.readouterr().err == (
        "crypteo_demo: refused: refusing to run while ANTHROPIC_API_KEY is set\n" * 2
    )
    assert list(tmp_path.iterdir()) == []
    completed = subprocess.run(
        [sys.executable, "-m", "crypteo_demo", str(tmp_path / "out")],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env={"PATH": "", "ANTHROPIC_API_KEY": ""},
    )
    assert completed.returncode == 1 and completed.stdout == ""
    assert completed.stderr == (
        "crypteo_demo: refused: refusing to run while ANTHROPIC_API_KEY is set\n"
    )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("variable", TRACING_VARIABLES)
def test_refuses_while_langsmith_tracing_is_configured(
    variable: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(variable, "true")
    assert main([str(tmp_path / "out")]) == 1
    assert capsys.readouterr().err == (
        f"crypteo_demo: refused: refusing to run while {variable} is set\n"
    )
    assert list(tmp_path.iterdir()) == []


def test_refuses_an_output_that_is_not_new_or_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "keep").write_text("x")
    (tmp_path / "file").write_text("x")
    for target in ("out", "file"):
        assert main([str(tmp_path / target)]) == 1
        assert "must be a new or empty directory" in capsys.readouterr().err
    assert sorted(tree(tmp_path)) == ["file", "out/keep"]


def test_default_output_is_the_first_unused_folder_under_artifacts(
    tmp_path: Path,
) -> None:
    assert default_output(tmp_path) == tmp_path / "artifacts" / "demo-1"
    (tmp_path / "artifacts" / "demo-1").mkdir(parents=True)
    (tmp_path / "artifacts" / "demo-2").touch()
    assert default_output(tmp_path) == tmp_path / "artifacts" / "demo-3"


def test_printed_output_and_pages_are_labelled(first: Run) -> None:
    printed = first.printed
    assert printed.startswith(LABEL + "\n")
    for phrase in ("synthetic data", "scripted model", "not research"):
        assert phrase in LABEL
    assert "nothing it produces is an evaluation result" in LABEL
    assert "code identity: placeholder, not a commit" in printed
    site = pages(first.output)
    index = site["index.html"]
    assert "<title>Run viewer: DEMO-synthetic-data-scripted-model</title>" in index
    assert "DEMO: synthetic data, scripted model, not an evaluation" in index
    assert f"<td>{CODE.git_commit}</td>" in index
    (evaluation,) = (text for name, text in site.items() if name.startswith("eval"))
    assert LABEL in evaluation and "DEMO-NOTICE.txt" in evaluation
    runs = [text for name, text in site.items() if name.startswith("runs/")]
    assert len(runs) == 4
    for text in runs:
        assert "crypteo-demo-scripted-model" in text
        assert "Scripted demo text, not research." in text
        assert "DEMO: synthetic data, scripted model, not an evaluation" in text


def test_the_demo_identity_names_no_commit() -> None:
    assert CODE.git_commit == "0" * 40
    assert CODE.source_digest == "sha256:" + "0" * 64


def test_synthetic_data_is_dated_before_these_markets_existed(first: Run) -> None:
    for path in (first.output / "data" / "canonical").rglob("candles.json"):
        years = {row["open_time"][:4] for row in json.loads(path.read_bytes())}
        assert years == {"2000"}


def test_candle_bodies_ignore_the_ambient_decimal_context() -> None:
    def bodies() -> list[bytes]:
        params = {"start": f"{START:%Y-%m-%dT%H:%M:%SZ}"}
        params["end"] = f"{END:%Y-%m-%dT%H:%M:%SZ}"
        return [
            SyntheticTransport().get(
                f"https://example.invalid/products/{pair.value}/candles", params
            )
            for pair in TradingPair
        ]

    expected = bodies()
    with decimal.localcontext(decimal.Context(prec=3, rounding=decimal.ROUND_DOWN)):
        assert bodies() == expected


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


def test_crypteo_and_the_viewer_never_import_the_demo() -> None:
    for package in ("crypteo", "crypteo_viewer"):
        sources = sorted((SOURCE.parent / package).rglob("*.py"))
        assert sources
        for path in sources:
            assert not any(
                name.split(".")[0] == "crypteo_demo" for name in imports(path)
            ), path
            assert "crypteo_demo" not in path.read_text(), path


def test_the_demo_imports_no_test_code() -> None:
    allowed = {"crypteo", "crypteo_viewer", "crypteo_demo", "pydantic"}
    for path in sorted(SOURCE.rglob("*.py")):
        for name in imports(path):
            top = name.split(".")[0]
            assert top in allowed or top in sys.stdlib_module_names, (path, name)
