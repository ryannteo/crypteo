"""Durable lifecycle and adversarial storage tests; never paid inference."""

import asyncio
import sqlite3
from contextlib import closing
from importlib.metadata import version
from multiprocessing.synchronize import Barrier
from pathlib import Path
from time import monotonic
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI
from research_support import (
    CODE,
    ScriptedModel,
    completion,
    critique,
    opportunity,
    service,
    structured,
    thesis,
    tool,
)

import crypteo.research_workflow as workflow
from crypteo.domain.research_journal import (
    AbortDiagnostic,
    EventEnvelope,
    ModelResponseObserved,
    ResearchArtifact,
    ResearchRunHeader,
    RunAborted,
)
from crypteo.domain.research_workflow import (
    BENCHMARK_ORDER,
    OpportunityResearchContext,
    OpportunityResearchRequest,
    ResearchWorkflowConfig,
)
from crypteo.openai_research import OpenAIResearchModel
from crypteo.research_journal import (
    ResearchStorageError,
    canonical_bytes,
    framed_digest,
)
from crypteo.research_model import (
    FailureReply,
    ModelConfiguration,
    ModelReply,
    ModelRequest,
    ProviderFailureCode,
    RefusalReply,
    ResponseObserver,
)
from crypteo.research_persistence import ResearchStore
from crypteo.research_workflow import run_durable_opportunity_research


class ObservedModel(ScriptedModel):
    async def invoke_observed(
        self, request: ModelRequest, observe: ResponseObserver
    ) -> ModelReply:
        # Scripted local models have no HTTP entity to observe.
        return await self.invoke(request)


async def execute(
    store: ResearchStore, model: ObservedModel, run_id: str = "run-1"
) -> ResearchArtifact:
    return await run_durable_opportunity_research(
        OpportunityResearchRequest(research_run_id=run_id, opportunity=opportunity()),
        service(),
        model,
        ResearchWorkflowConfig(
            model=ModelConfiguration(
                provider=model.provider, model="explicit-test-model"
            )
        ),
        store=store,
        code=CODE,
    )


@pytest.mark.parametrize("outcome", ["TRADE", "NO_TRADE", "ABSTAIN"])
def test_completed_exact_reload(tmp_path: Path, outcome: str) -> None:
    store = ResearchStore(tmp_path / "research.sqlite")
    artifact = asyncio.run(
        execute(store, ObservedModel([tool(), tool()], outcome=outcome))
    )
    assert store.load_terminal("run-1") == artifact
    assert ResearchStore(store.path).load_terminal("run-1") == artifact
    inspection = store.inspect_run("run-1")
    assert inspection.state == "COMPLETED"
    assert inspection.pending_boundaries == ()
    assert artifact.result.assessment.outcome.value == outcome
    kinds = [e.event.kind for e in inspection.events]
    assert kinds[:9] == [
        "RUN_STARTED",
        "NODE_TRANSITION",
        "MODEL_REQUEST_PREPARED",
        "MODEL_CALL_STARTED",
        "MODEL_CALL_FINISHED",
        "NODE_TRANSITION",
        "TOOL_REQUESTED",
        "TOOL_RESOLVED",
        "NODE_TRANSITION",
    ]
    assert kinds[-3:] == ["STAGE_COMPLETED", "NODE_TRANSITION", "RUN_FINISHED"]
    assert [e.sequence for e in inspection.events] == list(range(len(kinds)))
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM research_artifacts_v1"
        ).fetchone() == (1,)


def header(run_id: str = "run-1") -> ResearchRunHeader:
    svc = service()
    request = OpportunityResearchRequest(
        research_run_id=run_id, opportunity=opportunity()
    )
    return ResearchRunHeader(
        context=OpportunityResearchContext(
            request=request,
            research_at=svc.now,
            benchmarks=BENCHMARK_ORDER[1:],
            source_bindings=svc.source_bindings,
        ),
        configuration=ResearchWorkflowConfig(
            model=ModelConfiguration(provider="fake", model="explicit-test-model")
        ),
        code=CODE,
        provider=ObservedModel().identity,
        langgraph_version=version("langgraph"),
    )


def test_pragmas_verified_and_immutable_schema(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "r.sqlite", lock_wait_ms=35)
    asyncio.run(execute(store, ObservedModel([tool()])))
    with closing(store._connect()) as connection:
        for name, expected in [
            ("journal_mode", "delete"),
            ("synchronous", 3),
            ("foreign_keys", 1),
            ("busy_timeout", 35),
        ]:
            assert connection.execute(f"PRAGMA {name}").fetchone() == (expected,)
        for table in (
            "research_runs_v1",
            "research_events_v1",
            "research_artifacts_v1",
        ):
            for operation in (
                f"DELETE FROM {table}",
                f"UPDATE {table} SET research_run_id=research_run_id",
            ):
                with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                    connection.execute(operation)


@pytest.mark.parametrize(
    "reply",
    [
        RefusalReply(refusal="No"),
        FailureReply(code=ProviderFailureCode.NETWORK, message="network failure"),
        FailureReply(code=ProviderFailureCode.TIMEOUT, message="provider timeout"),
        structured({"unexpected": "invalid output"}),
        tool("unknown"),
    ],
)
def test_runtime_interruption_and_invalid_attempts(
    tmp_path: Path, reply: ModelReply
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    result = asyncio.run(execute(store, ObservedModel([reply, reply])))
    assert result.result.status == "INTERRUPTED"
    assert result.result.assessment_origin == "RUNTIME"
    assert result.result.assessment.outcome == "ABSTAIN"
    assert store.load_terminal("run-1") == result


@pytest.mark.parametrize("count", [1, 8, 9])
def test_cache_attempts_and_budget(tmp_path: Path, count: int) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    artifact = asyncio.run(execute(store, ObservedModel([tool()] * count)))
    assert artifact.result.status == ("INTERRUPTED" if count == 9 else "COMPLETED")
    assert store.load_terminal("run-1") == artifact


def test_registration_failure_and_duplicate_prevent_inference(tmp_path: Path) -> None:
    with pytest.raises(ResearchStorageError):
        ResearchStore(tmp_path / "absent" / "r.sqlite")
    store = ResearchStore(tmp_path / "r.sqlite")
    journal = store.create_run(header())
    for terminal in (False, True):
        if terminal:
            journal.abort(AbortDiagnostic(reason="INVARIANT_OR_PROGRAMMER_FAILURE"))
        model = ObservedModel()
        with pytest.raises(ResearchStorageError):
            asyncio.run(execute(store, model))
        assert model.requests == []


@pytest.mark.parametrize(
    "point",
    [
        "RUN_STARTED",
        "MODEL_REQUEST_PREPARED",
        "MODEL_CALL_STARTED",
        "MODEL_CALL_FINISHED",
        "TOOL_REQUESTED",
        "TOOL_RESOLVED",
        "STAGE_COMPLETED",
        "RUN_FINISHED",
    ],
)
@pytest.mark.parametrize("after_commit", [False, True])
def test_commit_failures_stop_work_and_never_publish_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    point: str,
    after_commit: bool,
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    original = store._commit
    failed = False

    def commit(connection: sqlite3.Connection) -> None:
        nonlocal failed
        row = connection.execute(
            "SELECT kind FROM research_events_v1 ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        if not failed and row and row[0] == point:
            failed = True
            if after_commit:
                original(connection)  # Acknowledgement lost: outcome is uncertain.
            raise sqlite3.OperationalError("injected commit fault")
        original(connection)

    monkeypatch.setattr(store, "_commit", commit)
    model = ObservedModel([tool()])
    with pytest.raises(ResearchStorageError):
        asyncio.run(execute(store, model))
    assert failed
    if point in ("RUN_STARTED", "MODEL_REQUEST_PREPARED", "MODEL_CALL_STARTED"):
        assert not model.requests
    monkeypatch.setattr(store, "_commit", original)
    if point == "RUN_STARTED" and not after_commit:
        with pytest.raises(ResearchStorageError):
            store.inspect_run("run-1")
    else:
        inspection = store.inspect_run("run-1")
        assert inspection.state == (
            "COMPLETED" if point == "RUN_FINISHED" and after_commit else "INCOMPLETE"
        )
        assert not any(isinstance(e.event, RunAborted) for e in inspection.events)


def test_invariant_abort_preserves_original_and_unresolved_call(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    error = RuntimeError("private exception must not enter diagnostic")

    def fail(request: ModelRequest) -> ModelReply:
        raise error

    with pytest.raises(RuntimeError) as raised:
        asyncio.run(execute(store, ObservedModel([fail])))
    assert raised.value is error
    inspection = store.inspect_run("run-1")
    assert inspection.state == "ABORTED" and inspection.artifact is None
    assert inspection.pending_boundaries == ("CALL_STARTED:run-1/model/0001",)
    assert b"private exception" not in store.path.read_bytes()


def test_abort_failure_never_hides_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    original = store._insert

    def insert(connection: sqlite3.Connection, event: EventEnvelope) -> None:
        if isinstance(event.event, RunAborted):
            raise sqlite3.OperationalError("abort failure")
        original(connection, event)

    monkeypatch.setattr(store, "_insert", insert)

    def fail(request: ModelRequest) -> ModelReply:
        raise LookupError("original")

    with pytest.raises(LookupError, match="original"):
        asyncio.run(execute(store, ObservedModel([fail])))
    assert store.inspect_run("run-1").state == "INCOMPLETE"


@pytest.mark.parametrize("boundary", ["prepared", "started", "tool"])
def test_prefix_inspection_does_not_resume(tmp_path: Path, boundary: str) -> None:
    source = ResearchStore(tmp_path / "source.sqlite")
    asyncio.run(execute(source, ObservedModel([tool()])))
    history = source.inspect_run("run-1")
    store = ResearchStore(tmp_path / "prefix.sqlite")
    journal = store.create_run(history.header)
    stop = {
        "prepared": "MODEL_REQUEST_PREPARED",
        "started": "MODEL_CALL_STARTED",
        "tool": "TOOL_REQUESTED",
    }[boundary]
    for item in history.events[1:]:
        journal.append(item.event)
        if item.event.kind == stop:
            break
    inspection = store.inspect_run("run-1")
    assert inspection.state == "INCOMPLETE" and inspection.pending_boundaries
    with pytest.raises(ResearchStorageError):
        store.load_terminal("run-1")
    with pytest.raises(ResearchStorageError):
        store.create_run(history.header)


def test_prepared_but_not_invoked_input_rejection(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    model = ObservedModel()
    request = OpportunityResearchRequest(
        research_run_id="large",
        opportunity=opportunity().model_copy(update={"summary": "界😀" * 40000}),
    )
    artifact = asyncio.run(
        run_durable_opportunity_research(
            request, service(), model, header().configuration, store=store, code=CODE
        )
    )
    assert not model.requests
    assert (
        artifact.result.failure
        and artifact.result.failure.code == "INPUT_BUDGET_EXHAUSTED"
    )
    kinds = [e.event.kind for e in store.inspect_run("large").events]
    assert "MODEL_REQUEST_PREPARED" in kinds and "MODEL_REQUEST_REJECTED" in kinds
    assert "MODEL_CALL_STARTED" not in kinds


def test_external_cancellation_and_late_observer_sealed(tmp_path: Path) -> None:
    from crypteo.research_model import ProviderObservation

    async def scenario() -> None:
        store = ResearchStore(tmp_path / "r.sqlite")
        started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class LateModel(ObservedModel):
            async def invoke_observed(
                self, request: ModelRequest, observe: ResponseObserver
            ) -> ModelReply:
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                observe(ProviderObservation(body=b"late", http_status=200))
                finished.set()
                return completion()

        task = asyncio.create_task(execute(store, LateModel()))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        before = store.inspect_run("run-1")
        assert before.state == "ABORTED"
        release.set()
        await finished.wait()
        await asyncio.sleep(0)
        assert store.inspect_run("run-1") == before

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "body,status",
    [
        (b"{ broken \xff", 200),
        (b'  {"id":"r","model":"m","status":"completed","output":[]}\n', 200),
        (b'{"error":{"message":"failure"}}', 503),
    ],
)
def test_exact_raw_body_commits_before_decoder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: bytes, status: int
) -> None:
    import crypteo.openai_research as adapter

    store = ResearchStore(tmp_path / "r.sqlite")
    original = adapter.decode_response_entity
    saw_raw = False

    def decode(entity: bytes, metadata: Any) -> ModelReply:
        nonlocal saw_raw
        # Read SQL directly: inspect itself invokes the pure decoder for reconciliation.
        with closing(sqlite3.connect(store.path)) as connection:
            row = connection.execute(
                "SELECT response_body FROM research_events_v1 "
                "WHERE kind='MODEL_RESPONSE_OBSERVED'"
            ).fetchone()
            assert row == (body,)
        saw_raw = True
        return original(entity, metadata)

    monkeypatch.setattr(adapter, "decode_response_entity", decode)

    async def scenario() -> ResearchArtifact:
        async with (
            httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(status, content=body)
                )
            ) as http_client,
            AsyncOpenAI(api_key="test-only", http_client=http_client) as client,
        ):
            return await run_durable_opportunity_research(
                header().context.request,
                service(),
                OpenAIResearchModel(client),
                ResearchWorkflowConfig(
                    model=ModelConfiguration(
                        provider="openai", model="explicit-test-model"
                    )
                ),
                store=store,
                code=CODE,
            )

    artifact = asyncio.run(scenario())
    monkeypatch.setattr(adapter, "decode_response_entity", original)
    assert saw_raw == (status == 200)
    inspection = store.inspect_run("run-1")
    observed = [
        e.event for e in inspection.events if isinstance(e.event, ModelResponseObserved)
    ]
    assert len(observed) == 1 and observed[0].body == body
    assert store.load_terminal("run-1") == artifact


def test_bounded_lock_contention(tmp_path: Path) -> None:
    store = ResearchStore(tmp_path / "r.sqlite", lock_wait_ms=40)
    with closing(sqlite3.connect(store.path, isolation_level=None)) as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        started = monotonic()
        with pytest.raises(ResearchStorageError):
            store.create_run(header())
        assert monotonic() - started < 1
        blocker.rollback()
    store.create_run(header())


@pytest.mark.parametrize(
    "mutation", ["version", "trigger", "event", "artifact", "body", "sequence"]
)
def test_storage_corruption_rejected(tmp_path: Path, mutation: str) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    asyncio.run(execute(store, ObservedModel([tool()])))
    with closing(sqlite3.connect(store.path, isolation_level=None)) as connection:
        if mutation == "version":
            connection.execute("PRAGMA user_version=99")
        elif mutation == "trigger":
            connection.execute("DROP TRIGGER research_event_order_v1")
        else:
            table = (
                "research_artifacts_v1"
                if mutation == "artifact"
                else "research_events_v1"
            )
            trigger = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name=?", (table + "_update",)
            ).fetchone()[0]
            connection.execute(f"DROP TRIGGER {table}_update")
            if mutation == "artifact":
                connection.execute(
                    "UPDATE research_artifacts_v1 SET artifact_json=x'7b7d'"
                )
            elif mutation == "sequence":
                connection.execute(
                    "UPDATE research_events_v1 SET sequence=sequence+100"
                )
            elif mutation == "body":
                connection.execute(
                    "UPDATE research_events_v1 SET checksum=? WHERE sequence=2",
                    ("sha256:" + "0" * 64,),
                )
            else:
                connection.execute(
                    "UPDATE research_events_v1 SET envelope_json=x'7b7d' "
                    "WHERE sequence=2"
                )
            connection.execute(trigger)
    with pytest.raises(ResearchStorageError):
        store.inspect_run("run-1")


def test_loader_has_no_model_or_data_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    artifact = asyncio.run(execute(store, ObservedModel([tool()])))

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("load performed work")

    monkeypatch.setattr(ObservedModel, "invoke", forbidden)
    monkeypatch.setattr(workflow, "dispatch_tool", forbidden)
    from crypteo.temporal import PointInTimeDataGateway

    monkeypatch.setattr(PointInTimeDataGateway, "read_candles", forbidden)
    assert store.load_terminal("run-1") == artifact


def test_canonical_unicode_and_stable_checksum(tmp_path: Path) -> None:
    one, two = (
        ResearchStore(tmp_path / "one.sqlite"),
        ResearchStore(tmp_path / "two.sqlite"),
    )
    output = thesis().model_copy(update={"summary": "市場 café 😀"})
    steps = [completion(), structured(output), structured(critique())]
    a = asyncio.run(execute(one, ObservedModel(steps, outcome="ABSTAIN")))
    b = asyncio.run(execute(two, ObservedModel(steps, outcome="ABSTAIN")))
    assert a == b
    assert b"\xf0\x9f\x98\x80" in canonical_bytes(a)
    assert one.load_terminal("run-1") == a
    assert framed_digest("x", b"a", b"bc") != framed_digest("x", b"ab", b"c")
    assert framed_digest("x", b"") != framed_digest("x")


def test_timeout_late_response_never_appends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from crypteo.research_model import ProviderObservation

    actual_wait = asyncio.wait

    async def short_wait(tasks: Any, *, timeout: float | None = None) -> Any:
        return await actual_wait(tasks, timeout=0.01)

    monkeypatch.setattr(asyncio, "wait", short_wait)

    async def scenario() -> None:
        store = ResearchStore(tmp_path / "r.sqlite")
        release, finished = asyncio.Event(), asyncio.Event()

        class LateModel(ObservedModel):
            async def invoke_observed(
                self, request: ModelRequest, observe: ResponseObserver
            ) -> ModelReply:
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                observe(ProviderObservation(body=b"late", http_status=200))
                finished.set()
                return completion()

        artifact = await execute(store, LateModel())
        assert (
            artifact.result.failure and artifact.result.failure.code == "MODEL_TIMEOUT"
        )
        before = store.inspect_run("run-1")
        release.set()
        await finished.wait()
        await asyncio.sleep(0)
        assert store.inspect_run("run-1") == before

    asyncio.run(scenario())


def test_observation_storage_error_stops_decoder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import crypteo.openai_research as adapter
    from crypteo.research_model import ProviderMetadata

    store = ResearchStore(tmp_path / "r.sqlite")
    original = store._insert

    def insert(connection: sqlite3.Connection, item: EventEnvelope) -> None:
        if isinstance(item.event, ModelResponseObserved):
            raise sqlite3.OperationalError("observation failed")
        original(connection, item)

    def forbidden(metadata: ProviderMetadata) -> ModelReply:
        raise AssertionError("decoded after storage failure")

    monkeypatch.setattr(store, "_insert", insert)
    monkeypatch.setattr(adapter, "_decode_response", forbidden)

    async def scenario() -> None:
        async with (
            httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, content=b"{}")
                )
            ) as http_client,
            AsyncOpenAI(api_key="test-only", http_client=http_client) as client,
        ):
            with pytest.raises(ResearchStorageError):
                await run_durable_opportunity_research(
                    header().context.request,
                    service(),
                    OpenAIResearchModel(client),
                    ResearchWorkflowConfig(
                        model=ModelConfiguration(
                            provider="openai", model="explicit-test-model"
                        )
                    ),
                    store=store,
                    code=CODE,
                )

    asyncio.run(scenario())
    inspection = store.inspect_run("run-1")
    assert inspection.state == "INCOMPLETE"
    assert any(p.startswith("CALL_STARTED:") for p in inspection.pending_boundaries)


_CREATION_BARRIER: Barrier | None = None


def _initialize_creation_race(barrier: Barrier) -> None:
    global _CREATION_BARRIER
    _CREATION_BARRIER = barrier


def _process_create(path: str, run_id: str) -> bool:
    try:
        store = ResearchStore(Path(path), lock_wait_ms=3000)
        registration = header(run_id)
        if _CREATION_BARRIER is not None:
            _CREATION_BARRIER.wait(timeout=10)
        store.create_run(registration)
    except ResearchStorageError:
        return False
    return True


@pytest.mark.parametrize("same_id", [True, False])
def test_local_process_creation_races(tmp_path: Path, same_id: bool) -> None:
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    path = tmp_path / "r.sqlite"
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(
        max_workers=2,
        mp_context=context,
        initializer=_initialize_creation_race,
        initargs=(context.Barrier(2),),
    ) as pool:
        futures = [
            pool.submit(_process_create, str(path), "same" if same_id else str(i))
            for i in range(2)
        ]
        results = [f.result(timeout=20) for f in futures]
    assert sum(results) == (1 if same_id else 2)
    store = ResearchStore(path)
    for name in ("same",) if same_id else ("0", "1"):
        assert store.inspect_run(name).state == "INCOMPLETE"


def test_different_workflows_concurrently(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = ResearchStore(tmp_path / "r.sqlite")
        a, b = await asyncio.gather(
            execute(store, ObservedModel([tool()]), "a"),
            execute(store, ObservedModel([tool()]), "b"),
        )
        assert store.load_terminal("a") == a
        assert store.load_terminal("b") == b

    asyncio.run(scenario())


def test_process_kill_recovers_committed_prefix(tmp_path: Path) -> None:
    import select
    import subprocess
    import sys

    path = tmp_path / "r.sqlite"
    script = """
import sqlite3, sys, time
from pathlib import Path
from crypteo.domain.research_journal import (
    ResearchRunHeader, EventEnvelope, NodeTransitioned,
)
from crypteo.domain.research_workflow import NodeTransition
from crypteo.research_persistence import ResearchStore
store = ResearchStore(Path(sys.argv[1]))
header = ResearchRunHeader.model_validate_json(sys.argv[2])
store.create_run(header)
connection = store._connect()
connection.execute('BEGIN IMMEDIATE')
store._insert(connection, EventEnvelope(
    research_run_id=header.research_run_id, sequence=1,
    event=NodeTransitioned(record=NodeTransition(
        source='__start__', target='investigate'))))
print('uncommitted', flush=True)
time.sleep(30)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(path), header().model_dump_json()],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        assert select.select([process.stdout], [], [], 10)[0]
        assert process.stdout.readline() == b"uncommitted\n"
        process.kill()
        process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    inspection = ResearchStore(path).inspect_run("run-1")
    assert inspection.state == "INCOMPLETE"
    assert len(inspection.events) == 1  # Uncommitted insertion rolled back by SQLite.


@pytest.mark.parametrize(
    "mutation",
    [
        "gap",
        "reorder",
        "run",
        "call",
        "attempt",
        "request",
        "query",
        "cache",
        "stage",
        "prepared",
        "budget",
        "terminal",
    ],
)
def test_semantic_journal_forgery_rejected(tmp_path: Path, mutation: str) -> None:
    from crypteo.domain.research_journal import (
        ModelRequestPrepared,
        RunFinished,
        StageCompleted,
        ToolResolved,
    )
    from crypteo.research_journal import project

    store = ResearchStore(tmp_path / "r.sqlite")
    asyncio.run(execute(store, ObservedModel([tool(), tool()])))
    events = list(store.inspect_run("run-1").events)
    if mutation == "gap":
        events[2] = events[2].model_copy(update={"sequence": 3})
    elif mutation == "reorder":
        events[2], events[3] = events[3], events[2]
    elif mutation == "run":
        events[2] = events[2].model_copy(update={"research_run_id": "another"})
    else:
        for i, item in enumerate(events):
            event = item.event
            replacement: Any = None
            if mutation in ("call", "request", "prepared", "budget") and isinstance(
                event, ModelRequestPrepared
            ):
                request = event.request
                changes: dict[str, Any] = {"call_id": "run-1/model/0002"}
                if mutation == "request":
                    changes = {
                        "messages": request.messages[:-1]
                        + (request.messages[-1].model_copy(update={"content": "{}"}),)
                    }
                elif mutation == "prepared":
                    changes = {"prepared_input_json": "{}"}
                elif mutation == "budget":
                    changes = {"max_output_tokens": 999}
                replacement = event.model_copy(
                    update={"request": request.model_copy(update=changes)}
                )
            elif mutation in ("attempt", "query", "cache") and isinstance(
                event, ToolResolved
            ):
                record = event.record
                changes = {"attempt_id": "run-1/tool/0008"}
                if mutation == "query":
                    assert record.query is not None
                    changes = {
                        "query": record.query.model_copy(
                            update={"capability": "momentum"}
                        )
                    }
                elif mutation == "cache":
                    changes = {"cached_from": "run-1/tool/0008"}
                replacement = event.model_copy(
                    update={"record": record.model_copy(update=changes)}
                )
            elif mutation == "stage" and isinstance(event, StageCompleted):
                replacement = event.model_copy(update={"call_id": "run-1/model/0001"})
            elif mutation == "terminal" and isinstance(event, RunFinished):
                replacement = event.model_copy(
                    update={
                        "assessment": event.assessment.model_copy(
                            update={"rationale": "Changed"}
                        )
                    }
                )
            if replacement is not None:
                events[i] = item.model_copy(update={"event": replacement})
                break
    with pytest.raises(ValueError):
        project(tuple(events))


@pytest.mark.parametrize(
    "raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":"\\ud800"}', b"\xff"]
)
def test_corrupt_project_json_fails_closed(tmp_path: Path, raw: bytes) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    store.create_run(header())
    with closing(sqlite3.connect(store.path, isolation_level=None)) as connection:
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='research_events_v1_update'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER research_events_v1_update")
        connection.execute("UPDATE research_events_v1 SET envelope_json=?", (raw,))
        connection.execute(trigger)
    with pytest.raises(ResearchStorageError):
        store.inspect_run("run-1")


def test_openai_completed_observed_body_reconciliation(tmp_path: Path) -> None:
    from research_support import assessment
    from test_openai_research import envelope, function, message

    store = ResearchStore(tmp_path / "r.sqlite")
    svc = service()
    evidence = svc.trend(opportunity().pair)
    evidence_id = evidence.model_dump()["evidence"]["evidence_id"]
    bodies = [
        envelope([function()]),
        envelope([message(completion((evidence_id,)).content)]),
        envelope([message(structured(thesis((evidence_id,))).content)]),
        envelope([message(structured(critique()).content)]),
        envelope([message(structured(assessment("TRADE", (evidence_id,))).content)]),
    ]
    import json

    raw = [
        json.dumps(body, ensure_ascii=False, indent=2).encode() + b"\n"
        for body in bodies
    ]
    remaining = list(raw)

    async def scenario() -> ResearchArtifact:
        async with (
            httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, content=remaining.pop(0))
                )
            ) as http_client,
            AsyncOpenAI(api_key="test-only", http_client=http_client) as client,
        ):
            return await run_durable_opportunity_research(
                header().context.request,
                svc,
                OpenAIResearchModel(client),
                ResearchWorkflowConfig(
                    model=ModelConfiguration(
                        provider="openai", model="explicit-test-model"
                    )
                ),
                store=store,
                code=CODE,
            )

    artifact = asyncio.run(scenario())
    history = store.inspect_run("run-1")
    assert [
        e.event.body
        for e in history.events
        if isinstance(e.event, ModelResponseObserved)
    ] == raw
    assert store.load_terminal("run-1") == artifact
    from crypteo.domain.research_journal import ModelCallFinished
    from crypteo.research_journal import project
    from crypteo.research_model import StructuredReply

    events = list(history.events)
    for i, item in enumerate(events):
        event = item.event
        if isinstance(event, ModelCallFinished) and isinstance(
            event.record.reply, StructuredReply
        ):
            changed_reply = event.record.reply.model_copy(
                update={"content": event.record.reply.content + " "}
            )
            changed_event = event.model_copy(
                update={
                    "record": event.record.model_copy(update={"reply": changed_reply})
                }
            )
            events[i] = item.model_copy(update={"event": changed_event})
            break
    with pytest.raises(ValueError, match="observable provider body"):
        project(tuple(events))

    # Replace a raw entity without touching its envelope/checksum.
    with closing(sqlite3.connect(store.path, isolation_level=None)) as connection:
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='research_events_v1_update'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER research_events_v1_update")
        connection.execute(
            "UPDATE research_events_v1 SET response_body=? "
            "WHERE kind='MODEL_RESPONSE_OBSERVED'",
            (b"{}",),
        )
        connection.execute(trigger)
    with pytest.raises(ResearchStorageError):
        store.load_terminal("run-1")


@pytest.mark.parametrize(
    "kind",
    ["event_version", "artifact_version", "artifact_result", "artifact_checksum"],
)
def test_unsupported_versions_and_forged_artifact(tmp_path: Path, kind: str) -> None:
    import json

    store = ResearchStore(tmp_path / "r.sqlite")
    asyncio.run(execute(store, ObservedModel([tool()])))
    table = "research_events_v1" if kind == "event_version" else "research_artifacts_v1"
    column = "envelope_json" if kind == "event_version" else "artifact_json"
    with closing(sqlite3.connect(store.path, isolation_level=None)) as connection:
        raw = connection.execute(f"SELECT {column} FROM {table} LIMIT 1").fetchone()[0]
        value = json.loads(raw)
        if kind == "event_version":
            value["schema_version"] = "999"
        elif kind == "artifact_version":
            value["artifact_schema_version"] = "999"
        elif kind == "artifact_result":
            value["result"]["assessment"]["rationale"] = "Unrelated result"
        else:
            value["artifact_checksum"] = "sha256:" + "0" * 64
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name=?", (table + "_update",)
        ).fetchone()[0]
        connection.execute(f"DROP TRIGGER {table}_update")
        connection.execute(f"UPDATE {table} SET {column}=?", (canonical_bytes(value),))
        connection.execute(trigger)
    with pytest.raises(ResearchStorageError):
        store.load_terminal("run-1")


def test_journal_sealed_and_failed_writer_cannot_append(tmp_path: Path) -> None:
    from crypteo.domain.research_journal import NodeTransitioned
    from crypteo.domain.research_workflow import NodeTransition

    event = NodeTransitioned(
        record=NodeTransition(source="__start__", target="investigate")
    )
    store = ResearchStore(tmp_path / "r.sqlite")
    aborted = store.create_run(header())
    aborted.abort(AbortDiagnostic(reason="INVARIANT_OR_PROGRAMMER_FAILURE"))
    with pytest.raises(ResearchStorageError):
        aborted.append(event)
    failed = store.create_run(header("bad"))
    with pytest.raises(ValueError, match="transition source"):
        failed.append(
            event.model_copy(
                update={"record": NodeTransition(source="wrong", target="investigate")}
            )
        )
    with pytest.raises(ResearchStorageError):
        failed.append(event)
    assert store.inspect_run("bad").state == "INCOMPLETE"


def test_preparation_timeout_never_invokes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = 0.0
    original = ObservedModel.prepare

    class SlowPreparation(ObservedModel):
        def prepare(self, request: ModelRequest) -> ModelRequest:
            nonlocal now
            result = original(self, request)
            now = 601.0
            return result

    monkeypatch.setattr(workflow, "monotonic", lambda: now)
    store = ResearchStore(tmp_path / "r.sqlite")
    model = SlowPreparation()
    artifact = asyncio.run(execute(store, model))
    assert not model.requests
    assert (
        artifact.result.failure and artifact.result.failure.code == "WORKFLOW_TIMEOUT"
    )
    assert store.load_terminal("run-1") == artifact


def test_tool_invariant_preserves_requested_without_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: Any) -> Any:
        raise ArithmeticError("broken deterministic calculation")

    monkeypatch.setattr(workflow, "dispatch_tool", fail)
    store = ResearchStore(tmp_path / "r.sqlite")
    with pytest.raises(ArithmeticError):
        asyncio.run(execute(store, ObservedModel([tool()])))
    inspection = store.inspect_run("run-1")
    assert inspection.state == "ABORTED"
    assert inspection.pending_boundaries == ("TOOL_REQUESTED:run-1/tool/0001",)


def test_sqlite_configuration_rejection_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = sqlite3.connect

    class WrongPragma(sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
            if sql == "PRAGMA synchronous":
                return super().execute("SELECT 1")
            return super().execute(sql, parameters)

    def connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        return original(*args, **kwargs, factory=WrongPragma)

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(ResearchStorageError, match="synchronous"):
        ResearchStore(tmp_path / "r.sqlite")


@pytest.mark.parametrize("scalar", ["\ud800", "\udfff"])
def test_durable_invalid_reply_surrogates_interrupt(
    tmp_path: Path, scalar: str
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    invalid = completion().model_copy(update={"content": scalar})
    artifact = asyncio.run(execute(store, ObservedModel([invalid])))
    assert (
        artifact.result.failure
        and artifact.result.failure.code == "INVALID_MODEL_OUTPUT"
    )
    assert store.load_terminal("run-1") == artifact
    from crypteo.domain.research_workflow import ModelCallRecord

    call = next(r for r in artifact.result.trace if isinstance(r, ModelCallRecord))
    assert call.reply.metadata.response_json is None
    assert call.reply.metadata.rejected_reply_json is not None


@pytest.mark.parametrize(
    "mutation",
    [
        "call",
        "stage",
        "query",
        "cache",
        "context",
        "terminal",
        "duplicate_start",
        "duplicate_terminal",
        "after_terminal",
    ],
)
def test_loader_rejects_relational_tampering_with_valid_checksums(
    tmp_path: Path, mutation: str
) -> None:
    import json

    from crypteo.research_journal import framed_digest

    store = ResearchStore(tmp_path / "r.sqlite")
    asyncio.run(execute(store, ObservedModel([tool(), tool()])))
    with closing(sqlite3.connect(store.path, isolation_level=None)) as connection:
        rows = connection.execute(
            "SELECT sequence, envelope_json, response_body FROM research_events_v1 "
            "ORDER BY sequence"
        ).fetchall()
        events = [json.loads(raw) for _, raw, _ in rows]
        if mutation in ("duplicate_start", "duplicate_terminal", "after_terminal"):
            index = len(events)
            event = (
                events[0]
                if mutation == "duplicate_start"
                else events[-1]
                if mutation == "duplicate_terminal"
                else events[1]
            )
            event = dict(event, sequence=index)
            order_trigger = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name='research_event_order_v1'"
            ).fetchone()[0]
            connection.execute("DROP TRIGGER research_event_order_v1")
            raw = canonical_bytes(event)
            checksum = framed_digest("crypteo.research.event.v1", raw, b"absent", b"")
            connection.execute(
                "INSERT INTO research_events_v1 VALUES (?, ?, ?, ?, NULL, ?)",
                ("run-1", index, event["event"]["kind"], raw, checksum),
            )
            connection.execute(order_trigger)
        else:
            kinds = {
                "call": "MODEL_CALL_FINISHED",
                "stage": "STAGE_COMPLETED",
                "query": "TOOL_RESOLVED",
                "cache": "TOOL_RESOLVED",
                "context": "MODEL_REQUEST_PREPARED",
                "terminal": "RUN_FINISHED",
            }
            value = next(e for e in events if e["event"]["kind"] == kinds[mutation])
            event = value["event"]
            if mutation == "call":
                event["record"]["request"]["call_id"] = "run-1/model/0002"
            elif mutation == "stage":
                event["value"]["output"]["summary"] = "Forged stage summary"
            elif mutation == "query":
                event["record"]["requests"][0]["name"] = "momentum"
            elif mutation == "cache":
                event["record"]["cached_from"] = "run-1/tool/0009"
            elif mutation == "context":
                message = json.loads(event["request"]["messages"][-1]["content"])
                message["research_data"]["attempts_remaining"] = 0
                event["request"]["messages"][-1]["content"] = json.dumps(message)
            else:
                event["assessment"]["rationale"] = "Forged terminal result"
            raw = canonical_bytes(value)
            checksum = framed_digest("crypteo.research.event.v1", raw, b"absent", b"")
            trigger = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name='research_events_v1_update'"
            ).fetchone()[0]
            connection.execute("DROP TRIGGER research_events_v1_update")
            connection.execute(
                "UPDATE research_events_v1 SET envelope_json=?, checksum=? "
                "WHERE sequence=?",
                (raw, checksum, value["sequence"]),
            )
            connection.execute(trigger)
    with pytest.raises(ResearchStorageError) as raised:
        store.inspect_run("run-1")
    # The relationship fails before artifact reconciliation; valid hashes do not help.
    assert isinstance(raised.value.__cause__, ValueError)


def _process_workflow(path: str, run_id: str) -> str:
    store = ResearchStore(Path(path), lock_wait_ms=3000)
    artifact = asyncio.run(execute(store, ObservedModel(outcome="ABSTAIN"), run_id))
    assert store.load_terminal(run_id) == artifact
    return artifact.research_run_id


def test_independent_process_workflows_share_database(tmp_path: Path) -> None:
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    path = tmp_path / "r.sqlite"
    with ProcessPoolExecutor(
        max_workers=2, mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        futures = [
            pool.submit(_process_workflow, str(path), run_id)
            for run_id in ("one", "two")
        ]
        assert [f.result(timeout=20) for f in futures] == ["one", "two"]
    store = ResearchStore(path)
    assert (
        store.inspect_run("one").state == store.inspect_run("two").state == "COMPLETED"
    )


def test_lost_observation_acknowledgement_prevents_decoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from crypteo.research_model import ProviderObservation

    store = ResearchStore(tmp_path / "r.sqlite")
    original = store._commit

    def commit(connection: sqlite3.Connection) -> None:
        last = connection.execute(
            "SELECT kind FROM research_events_v1 ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        original(connection)
        if last == ("MODEL_RESPONSE_OBSERVED",):
            raise sqlite3.OperationalError("lost observation acknowledgement")

    monkeypatch.setattr(store, "_commit", commit)

    class Observing(ObservedModel):
        async def invoke_observed(
            self, request: ModelRequest, observe: ResponseObserver
        ) -> ModelReply:
            observe(ProviderObservation(body=b" {opaque}\n", http_status=200))
            raise AssertionError("work after storage failure")

    with pytest.raises(ResearchStorageError):
        asyncio.run(execute(store, Observing()))
    monkeypatch.setattr(store, "_commit", original)
    inspection = store.inspect_run("run-1")
    assert inspection.state == "INCOMPLETE"
    assert "RESPONSE_OBSERVED:run-1/model/0001" in inspection.pending_boundaries
    observed = inspection.events[-1].event
    assert (
        isinstance(observed, ModelResponseObserved) and observed.body == b" {opaque}\n"
    )


def test_connection_close_failure_is_storage_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite")
    original = sqlite3.connect

    class BadClose(sqlite3.Connection):
        def close(self) -> None:
            super().close()
            raise sqlite3.OperationalError("close failed")

    def connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        return original(*args, **kwargs, factory=BadClose)

    monkeypatch.setattr(sqlite3, "connect", connect)
    model = ObservedModel()
    with pytest.raises(ResearchStorageError):
        asyncio.run(execute(store, model))
    assert not model.requests


def test_no_transaction_spans_provider_or_tool_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ResearchStore(tmp_path / "r.sqlite", lock_wait_ms=0)
    from crypteo.research_tools import dispatch_tool

    original = dispatch_tool

    def can_write() -> None:
        with closing(
            sqlite3.connect(store.path, timeout=0, isolation_level=None)
        ) as connection:
            connection.execute("BEGIN EXCLUSIVE")
            connection.rollback()

    def dispatch(*args: Any) -> Any:
        can_write()
        return original(*args)

    class Checking(ObservedModel):
        async def invoke_observed(
            self, request: ModelRequest, observe: ResponseObserver
        ) -> ModelReply:
            can_write()
            return await self.invoke(request)

    monkeypatch.setattr(workflow, "dispatch_tool", dispatch)
    artifact = asyncio.run(execute(store, Checking([tool()])))
    assert store.load_terminal("run-1") == artifact


@pytest.mark.parametrize("column", ["sequence", "kind", "header_json"])
def test_append_rechecks_committed_sql_identity(tmp_path: Path, column: str) -> None:
    from crypteo.domain.research_journal import NodeTransitioned
    from crypteo.domain.research_workflow import NodeTransition

    store = ResearchStore(tmp_path / "r.sqlite")
    journal = store.create_run(header())
    table = "research_runs_v1" if column == "header_json" else "research_events_v1"
    with closing(sqlite3.connect(store.path, isolation_level=None)) as connection:
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name=?", (table + "_update",)
        ).fetchone()[0]
        connection.execute(f"DROP TRIGGER {table}_update")
        if column == "header_json":
            connection.execute("UPDATE research_runs_v1 SET header_json=x'7b7d'")
        elif column == "kind":
            connection.execute("UPDATE research_events_v1 SET kind='NODE_TRANSITION'")
        else:
            connection.execute("UPDATE research_events_v1 SET sequence=100")
        connection.execute(trigger)
    with pytest.raises(ResearchStorageError, match="changed"):
        journal.append(
            NodeTransitioned(
                record=NodeTransition(source="__start__", target="investigate")
            )
        )
    assert not journal.healthy


def test_final_publication_revalidates_invalid_instance(tmp_path: Path) -> None:
    source = ResearchStore(tmp_path / "source.sqlite")
    artifact = asyncio.run(execute(source, ObservedModel([tool()])))
    inspection = source.inspect_run("run-1")
    store = ResearchStore(tmp_path / "r.sqlite")
    journal = store.create_run(inspection.header)
    for envelope in inspection.events[1:-1]:
        journal.append(envelope.event)
    forged = artifact.result.model_copy(
        update={
            "assessment": artifact.result.assessment.model_copy(
                update={"rationale": ""}
            )
        }
    )
    with pytest.raises(ValueError):
        journal.finalize(forged)
    assert not journal.healthy
    assert store.inspect_run("run-1").state == "INCOMPLETE"


def test_prepared_json_boolean_cannot_be_replaced_with_integer(tmp_path: Path) -> None:
    import json

    from crypteo.domain.research_journal import ModelRequestPrepared
    from crypteo.research_journal import validate_prepared

    store = ResearchStore(tmp_path / "r.sqlite")
    asyncio.run(execute(store, ObservedModel(outcome="ABSTAIN")))
    prepared = next(
        e.event
        for e in store.inspect_run("run-1").events
        if isinstance(e.event, ModelRequestPrepared)
    )
    assert isinstance(prepared, ModelRequestPrepared)
    body = json.loads(prepared.request.prepared_input_json or "{}")
    body["parallel_tool_calls"] = 0
    request = prepared.request.model_copy(
        update={"prepared_input_json": json.dumps(body)}
    )
    with pytest.raises(ValueError, match="prepared body"):
        validate_prepared(request, header())


def test_committed_response_can_precede_software_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from crypteo.research_model import ProviderObservation

    now = 0.0

    class ObservedThenLate(ObservedModel):
        async def invoke_observed(
            self, request: ModelRequest, observe: ResponseObserver
        ) -> ModelReply:
            nonlocal now
            observe(
                ProviderObservation(body=b"committed before deadline", http_status=200)
            )
            now = 61.0
            return completion()

    monkeypatch.setattr(workflow, "monotonic", lambda: now)
    store = ResearchStore(tmp_path / "r.sqlite")
    artifact = asyncio.run(execute(store, ObservedThenLate()))
    assert artifact.result.failure and artifact.result.failure.code == "MODEL_TIMEOUT"
    inspection = store.inspect_run("run-1")
    kinds = [e.event.kind for e in inspection.events]
    assert kinds.index("MODEL_RESPONSE_OBSERVED") < kinds.index("MODEL_CALL_FINISHED")
    assert inspection.artifact == artifact and inspection.pending_boundaries == ()
