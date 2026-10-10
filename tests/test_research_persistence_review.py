"""Regressions for the four independent Slice 13 review findings."""

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError
from research_support import (
    CODE,
    assessment,
    completion,
    critique,
    openai_research,
    service,
    structured,
    thesis,
    workflow_config,
)
from test_openai_research import envelope, invoke, message
from test_research_persistence import ObservedModel, execute, header

import crypteo.research_persistence as persistence
import crypteo.research_workflow as workflow
from crypteo.domain.research_journal import ModelResponseObserved, NodeTransitioned
from crypteo.domain.research_workflow import (
    NodeTransition,
    ResearchRunResult,
)
from crypteo.openai_research import OpenAIResearchModel
from crypteo.research_journal import ResearchStorageError
from crypteo.research_journal import project as project_journal
from crypteo.research_model import (
    FailureReply,
    ModelReply,
    ModelRequest,
    ProviderObservation,
    ResponseObserver,
)
from crypteo.research_persistence import ResearchStore


@pytest.mark.parametrize(
    "table,body_column",
    [
        ("research_runs_v1", 2),
        ("research_events_v1", 3),
        ("research_artifacts_v1", 2),
    ],
)
def test_f1_replace_cannot_delete_immutable_identity(
    tmp_path: Path, table: str, body_column: int
) -> None:
    store = ResearchStore(tmp_path / "research.sqlite")
    artifact = asyncio.run(execute(store, ObservedModel(outcome="ABSTAIN")))
    with closing(sqlite3.connect(store.path, isolation_level=None)) as connection:
        connection.execute("PRAGMA recursive_triggers=OFF")
        connection.execute("PRAGMA foreign_keys=OFF")
        assert connection.execute("PRAGMA recursive_triggers").fetchone() == (0,)
        original = connection.execute(f"SELECT * FROM {table}").fetchall()
        replacement = list(original[0])
        replacement[body_column] = b'{"replaced":true}'
        placeholders = ",".join("?" for _ in replacement)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"INSERT OR REPLACE INTO {table} VALUES ({placeholders})", replacement
            )
        assert connection.execute(f"SELECT * FROM {table}").fetchall() == original
        # WITHOUT ROWID also removes the hidden replacement conflict target.
        with pytest.raises(sqlite3.OperationalError, match="rowid"):
            connection.execute(f"INSERT OR REPLACE INTO {table}(rowid) VALUES (1)")
        assert connection.execute(f"SELECT * FROM {table}").fetchall() == original
    assert store.load_terminal("run-1") == artifact


def invalid_utf8_entity() -> bytes:
    output = json.loads(completion().content)
    output["summary"] = "BYTE_MARKER"
    body = (
        json.dumps(envelope([message(json.dumps(output))]))
        .encode()
        .replace(b"BYTE_MARKER", b"\xff")
    )
    # The JSON and structured completion are otherwise valid.
    restored = json.loads(body.replace(b"\xff", b"x").decode("utf-8"))
    assert json.loads(restored["output"][0]["content"][0]["text"])["summary"] == "x"
    with pytest.raises(UnicodeDecodeError):
        body.decode("utf-8")
    return body


def test_f2_invalid_utf8_fails_in_memory_without_repaired_content() -> None:
    reply = invoke(lambda request: httpx.Response(200, content=invalid_utf8_entity()))
    assert isinstance(reply, FailureReply) and reply.code == "INVALID_PROVIDER_ENVELOPE"
    assert reply.metadata.response_json is None
    assert "\ufffd" not in reply.model_dump_json()


@pytest.mark.parametrize("valid", [False, True])
def test_f2_live_and_reconstruction_share_strict_utf8(
    tmp_path: Path, valid: bool
) -> None:
    store = ResearchStore(tmp_path / "research.sqlite")
    output = json.loads(completion().content)
    output["summary"] = "市場 café 😀"
    body = (
        json.dumps(
            envelope([message(json.dumps(output, ensure_ascii=False))]),
            ensure_ascii=False,
        ).encode()
        if valid
        else invalid_utf8_entity()
    )
    bodies = [body] + [
        json.dumps(envelope([message(reply.content)])).encode()
        for reply in (
            structured(thesis()),
            structured(critique()),
            structured(assessment("ABSTAIN", ())),
        )
    ]
    calls = 0

    def transport(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        response = httpx.Response(
            200,
            content=bodies[calls],
            headers={"content-type": "application/json; charset=iso-8859-1"},
        )
        calls += 1
        return response

    artifact = openai_research(transport, header().context.request, store)
    assert ResearchStore(store.path).load_terminal("run-1") == artifact
    result = artifact.result
    if valid:
        assert calls == 4 and result.status == "COMPLETED"
        assert result.investigation and result.investigation.summary == "市場 café 😀"
    else:
        assert calls == 1 and result.status == "INTERRUPTED"
        assert result.failure and result.failure.code == "INVALID_PROVIDER_ENVELOPE"
        assert result.investigation is None
        assert "\ufffd" not in result.model_dump_json()
    observed = next(
        e.event
        for e in store.inspect_run("run-1").events
        if isinstance(e.event, ModelResponseObserved)
    )
    assert isinstance(observed, ModelResponseObserved) and observed.body == body
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute(
            "SELECT response_body FROM research_events_v1 "
            "WHERE kind='MODEL_RESPONSE_OBSERVED' ORDER BY sequence LIMIT 1"
        ).fetchone() == (body,)


@pytest.mark.parametrize(
    "after,code", [(601.0, "WORKFLOW_TIMEOUT"), (61.0, "MODEL_TIMEOUT")]
)
def test_f3_start_commit_cannot_authorize_expired_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after: float, code: str
) -> None:
    now = 0.0
    monkeypatch.setattr(workflow, "monotonic", lambda: now)
    store = ResearchStore(tmp_path / "research.sqlite")
    original = store._commit

    def commit(connection: sqlite3.Connection) -> None:
        nonlocal now
        latest = connection.execute(
            "SELECT kind FROM research_events_v1 ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        original(connection)
        if latest == ("MODEL_CALL_STARTED",):
            now = after

    monkeypatch.setattr(store, "_commit", commit)
    model = ObservedModel(outcome="ABSTAIN")
    artifact = asyncio.run(execute(store, model))
    assert not model.requests
    assert artifact.result.failure and artifact.result.failure.code.value == code
    inspection = store.inspect_run("run-1")
    assert inspection.state == "INTERRUPTED" and inspection.pending_boundaries == ()
    assert [e.event.kind for e in inspection.events] == [
        "RUN_STARTED",
        "NODE_TRANSITION",
        "MODEL_REQUEST_PREPARED",
        "MODEL_CALL_STARTED",
        "MODEL_CALL_FINISHED",
        "NODE_TRANSITION",
        "RUN_FINISHED",
    ]
    assert store.load_terminal("run-1") == artifact


@pytest.mark.parametrize(
    "after,code,expected_calls",
    [(61.0, "MODEL_TIMEOUT", 0), (601.0, "WORKFLOW_TIMEOUT", 0), (1.0, "REFUSAL", 1)],
)
def test_f3_scheduled_provider_checks_deadline_before_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    after: float,
    code: str,
    expected_calls: int,
) -> None:
    now = 0.0
    model_calls = 0
    http_calls = 0
    callbacks = 0
    monkeypatch.setattr(workflow, "monotonic", lambda: now)
    store = ResearchStore(tmp_path / "research.sqlite")
    original = store._commit

    def advance_clock() -> None:
        nonlocal now, callbacks
        # The callback runs after task creation but before its first execution.
        assert any(
            task.get_name() == "run-1/model/0001" and not task.done()
            for task in asyncio.all_tasks()
        )
        assert model_calls == http_calls == 0
        now = after
        callbacks += 1

    def commit(connection: sqlite3.Connection) -> None:
        latest = connection.execute(
            "SELECT kind FROM research_events_v1 ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        original(connection)
        if latest == ("MODEL_CALL_STARTED",):
            asyncio.get_running_loop().call_soon(advance_clock)

    monkeypatch.setattr(store, "_commit", commit)

    class CountingOpenAIModel(OpenAIResearchModel):
        async def invoke_observed(
            self, request: ModelRequest, observe: ResponseObserver
        ) -> ModelReply:
            nonlocal model_calls
            model_calls += 1
            return await super().invoke_observed(request, observe)

    def transport(request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        return httpx.Response(200, json=envelope([message("refused", refusal=True)]))

    async def scenario() -> ResearchRunResult:
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http_client,
            AsyncOpenAI(api_key="test-only", http_client=http_client) as client,
        ):
            artifact = await workflow.run_durable_opportunity_research(
                header().context.request,
                service(),
                CountingOpenAIModel(client),
                workflow_config("openai"),
                store=store,
                code=CODE,
            )
        assert ResearchStore(store.path).load_terminal("run-1") == artifact
        return artifact.result

    result = asyncio.run(scenario())
    assert callbacks == 1
    assert model_calls == http_calls == expected_calls
    assert result.failure and result.failure.code.value == code
    inspection = store.inspect_run("run-1")
    assert inspection.state == "INTERRUPTED" and inspection.pending_boundaries == ()
    kinds = [entry.event.kind for entry in inspection.events]
    expected = [
        "RUN_STARTED",
        "NODE_TRANSITION",
        "MODEL_REQUEST_PREPARED",
        "MODEL_CALL_STARTED",
        *(["MODEL_RESPONSE_OBSERVED"] if expected_calls else []),
        "MODEL_CALL_FINISHED",
        "NODE_TRANSITION",
        "RUN_FINISHED",
    ]
    assert kinds == expected


def transition() -> NodeTransitioned:
    return NodeTransitioned(
        record=NodeTransition(source="__start__", target="investigate")
    )


@pytest.mark.parametrize(
    "sentinel",
    [
        ValueError("projection bug"),
        RuntimeError("projection bug"),
        ValidationError.from_exception_data("sentinel", []),
    ],
)
def test_f4_append_preserves_project_exception_and_poisons_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sentinel: Exception
) -> None:
    store = ResearchStore(tmp_path / "research.sqlite")
    journal = store.create_run(header())

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise sentinel

    with monkeypatch.context() as patch:
        patch.setattr(persistence, "project", fail)
        with pytest.raises(type(sentinel)) as raised:
            journal.append(transition())
        assert raised.value is sentinel
    assert not journal.healthy
    with pytest.raises(ResearchStorageError):
        journal.append(transition())
    assert len(store.inspect_run("run-1").events) == 1


@pytest.mark.parametrize(
    "boundary",
    ["create_projection", "create_insert", "inspect_projection", "schema_validation"],
)
@pytest.mark.parametrize(
    "sentinel", [ValueError("project sentinel"), RuntimeError("project sentinel")]
)
def test_f4_other_boundaries_preserve_programmer_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str, sentinel: Exception
) -> None:
    store = ResearchStore(tmp_path / "research.sqlite")
    if boundary == "inspect_projection":
        store.create_run(header())

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise sentinel

    with monkeypatch.context() as patch:
        if boundary in ("create_projection", "inspect_projection"):
            patch.setattr(persistence, "project", fail)
        elif boundary == "create_insert":
            patch.setattr(store, "_insert", fail)
        else:
            patch.setattr(store, "_validate_schema", fail)
        with pytest.raises(type(sentinel)) as raised:
            if boundary == "inspect_projection":
                store.inspect_run("run-1")
            else:
                store.create_run(header())
        assert raised.value is sentinel
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM research_runs_v1"
        ).fetchone() == (1 if boundary == "inspect_projection" else 0,)


def test_f4_final_validation_preserves_exception_and_poisons_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = ResearchStore(tmp_path / "source.sqlite")
    artifact = asyncio.run(execute(source, ObservedModel(outcome="ABSTAIN")))
    history = source.inspect_run("run-1")
    store = ResearchStore(tmp_path / "research.sqlite")
    journal = store.create_run(history.header)
    for item in history.events[1:-1]:
        journal.append(item.event)
    sentinel = RuntimeError("result validator bug")

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise sentinel

    with monkeypatch.context() as patch:
        patch.setattr(ResearchRunResult, "model_validate", fail)
        with pytest.raises(RuntimeError) as raised:
            journal.finalize(artifact.result)
        assert raised.value is sentinel
    assert not journal.healthy
    with pytest.raises(ResearchStorageError):
        journal.finalize(artifact.result)
    assert store.inspect_run("run-1").state == "INCOMPLETE"


def test_f4_observation_does_not_mask_project_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ResearchStore(tmp_path / "research.sqlite")
    sentinel = RuntimeError("observation projection bug")
    original = project_journal

    def project(events: Any) -> Any:
        if isinstance(events[-1].event, ModelResponseObserved):
            raise sentinel
        return original(events)

    class Observing(ObservedModel):
        async def invoke_observed(
            self, request: ModelRequest, observe: ResponseObserver
        ) -> ModelReply:
            observe(ProviderObservation(body=b"opaque", http_status=200))
            raise AssertionError("continued after failed observation")

    monkeypatch.setattr(persistence, "project", project)
    with pytest.raises(RuntimeError) as raised:
        asyncio.run(execute(store, Observing()))
    assert raised.value is sentinel
    inspection = store.inspect_run("run-1")
    assert inspection.state == "INCOMPLETE"
    assert "CALL_STARTED:run-1/model/0001" in inspection.pending_boundaries
