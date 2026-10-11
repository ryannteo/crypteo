"""Claude journal reconstruction: every stored request and reply re-derives exactly."""

import asyncio
import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx2
import pytest
from research_support import MODEL, anthropic_research, opportunity
from test_anthropic_research import (
    CONFIDENCE_WIRE,
    constraint_keywords,
    final_confidence,
    graph_responses,
)

from crypteo.domain.research_journal import (
    EventEnvelope,
    ModelRequestPrepared,
    ModelResponseObserved,
    ResearchArtifact,
)
from crypteo.domain.research_workflow import ModelCallRecord, OpportunityResearchRequest
from crypteo.research_journal import (
    PROVIDER_CODECS,
    ResearchStorageError,
    canonical_bytes,
    decode_envelope,
    event_checksum,
    project,
)
from crypteo.research_model import FailureReply, render_json
from crypteo.research_persistence import ResearchStore
from crypteo.research_validation import ResearchValidationError

REQUEST = OpportunityResearchRequest(research_run_id="run-1", opportunity=opportunity())


def durable(
    tmp_path: Path, replies: list[httpx2.Response]
) -> tuple[ResearchStore, ResearchArtifact]:
    store = ResearchStore(tmp_path / "research.sqlite")
    artifact = anthropic_research(lambda request: replies.pop(0), REQUEST, store)
    assert not replies
    assert artifact.result.provider.provider == "anthropic"
    return store, artifact


def completed(tmp_path: Path) -> tuple[ResearchStore, ResearchArtifact]:
    return durable(
        tmp_path,
        [
            httpx2.Response(200, text=json.dumps(body, indent=1) + "\n")
            for body in graph_responses()
        ],
    )


def events_of(store: ResearchStore, kind: type[Any]) -> list[EventEnvelope]:
    return [e for e in store.inspect_run("run-1").events if isinstance(e.event, kind)]


def rewrite(
    store: ResearchStore,
    sequence: int,
    change: Callable[[EventEnvelope], EventEnvelope],
) -> None:
    """Replace one stored event with a self-consistent checksum, as attackers can."""
    with closing(sqlite3.connect(store.path, isolation_level=None)) as connection:
        raw, body = connection.execute(
            "SELECT envelope_json, response_body FROM research_events_v1 "
            "WHERE sequence=?",
            (sequence,),
        ).fetchone()
        envelope = change(decode_envelope(raw, body))
        event = envelope.event
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='research_events_v1_update'"
        ).fetchone()[0]
        connection.execute("DROP TRIGGER research_events_v1_update")
        connection.execute(
            "UPDATE research_events_v1 SET envelope_json=?, response_body=?, "
            "checksum=? WHERE sequence=?",
            (
                canonical_bytes(envelope),
                event.body if isinstance(event, ModelResponseObserved) else None,
                event_checksum(envelope),
                sequence,
            ),
        )
        connection.execute(trigger)


def rewrite_observed(store: ResearchStore, index: int, **changes: Any) -> None:
    # Located by SQL: an earlier rewrite may already make inspection fail closed.
    with closing(sqlite3.connect(store.path)) as connection:
        sequences = connection.execute(
            "SELECT sequence FROM research_events_v1 "
            "WHERE kind='MODEL_RESPONSE_OBSERVED' ORDER BY sequence"
        ).fetchall()
    rewrite(
        store,
        sequences[index][0],
        lambda e: e.model_copy(update={"event": e.event.model_copy(update=changes)}),
    )


def assert_load_rejects(store: ResearchStore, cause: str) -> None:
    with pytest.raises(ResearchStorageError) as raised:
        store.load_terminal("run-1")
    assert isinstance(raised.value.__cause__, ResearchValidationError)
    assert str(raised.value.__cause__) == cause


def test_durable_claude_run_through_final_assessment_reloads_identically(
    tmp_path: Path,
) -> None:
    store, artifact = completed(tmp_path)
    result = artifact.result
    assert result.status == "COMPLETED" and result.assessment.outcome == "TRADE"
    assert store.load_terminal("run-1") == artifact
    assert ResearchStore(store.path).load_terminal("run-1") == artifact
    calls = [r for r in result.trace if isinstance(r, ModelCallRecord)]
    assert [c.request.stage for c in calls] == [
        "investigate",
        "investigate",
        "draft_thesis",
        "critique",
        "final_assessment",
    ]
    assert all(c.reply.metadata.returned_model == MODEL for c in calls)
    # The stored final-assessment body states the range only in its description.
    final = json.loads(calls[-1].request.prepared_input_json or "{}")
    assert final["max_tokens"] == 16384
    assert not constraint_keywords(final)
    assert final_confidence(final) == CONFIDENCE_WIRE
    observed = events_of(store, ModelResponseObserved)
    assert len(observed) == 5
    assert all(
        isinstance(e.event, ModelResponseObserved) and e.event.body.endswith(b"\n")
        for e in observed
    )


@pytest.mark.parametrize(
    "tamper",
    [
        lambda body: body.replace(b"Countercase.", b"Countercase!"),
        lambda body: body.replace(MODEL.encode(), b"other-model"),
        lambda body: body + b" ",
    ],
)
def test_tampered_stored_claude_body_is_rejected_on_load(
    tmp_path: Path, tamper: Callable[[bytes], bytes]
) -> None:
    store, _ = completed(tmp_path)
    original = events_of(store, ModelResponseObserved)[2].event
    assert isinstance(original, ModelResponseObserved)
    assert tamper(original.body) != original.body
    rewrite_observed(store, 2, body=tamper(original.body))
    assert_load_rejects(store, "observable provider body differs from normalized reply")


def test_tampered_stored_prepared_body_is_rejected_on_load(tmp_path: Path) -> None:
    store, _ = completed(tmp_path)
    prepared = events_of(store, ModelRequestPrepared)[-1]
    assert isinstance(prepared.event, ModelRequestPrepared)
    assert prepared.event.request.stage == "final_assessment"

    def reinsert_minimum(item: EventEnvelope) -> EventEnvelope:
        event = item.event
        assert isinstance(event, ModelRequestPrepared)
        body = json.loads(event.request.prepared_input_json or "{}")
        final_confidence(body)[0]["minimum"] = 0
        request = event.request.model_copy(
            update={"prepared_input_json": render_json(body)}
        )
        return item.model_copy(
            update={"event": event.model_copy(update={"request": request})}
        )

    rewrite(store, prepared.sequence, reinsert_minimum)
    assert_load_rejects(store, "prepared body differs from normalized request")


def test_claude_http_error_reconciles_and_tampering_is_rejected(
    tmp_path: Path,
) -> None:
    body = b'{"type":"error","error":{"type":"overloaded_error","message":"busy"}}'
    store, artifact = durable(
        tmp_path, [httpx2.Response(529, content=body, headers={"request-id": "e-1"})]
    )
    result = artifact.result
    assert result.status == "INTERRUPTED"
    assert result.failure and result.failure.code == "PROVIDER_ERROR"
    call = next(r for r in result.trace if isinstance(r, ModelCallRecord))
    assert isinstance(call.reply, FailureReply)
    assert call.reply.metadata.error_type == "overloaded_error"
    assert call.reply.metadata.http_status == 529
    assert call.reply.metadata.request_id == "e-1"
    assert ResearchStore(store.path).load_terminal("run-1") == artifact
    rewrite_observed(
        store, 0, body=body.replace(b"overloaded_error", b"rate_limit_error")
    )
    assert_load_rejects(store, "HTTP error differs from normalized reply")
    rewrite_observed(store, 0, body=body, http_status=503)
    assert_load_rejects(store, "HTTP error differs from normalized reply")
    rewrite_observed(store, 0, http_status=529, request_id="e-2")
    assert_load_rejects(store, "HTTP error differs from normalized reply")
    rewrite_observed(store, 0, request_id="e-1")
    assert store.load_terminal("run-1") == artifact


def test_claude_reply_without_observation_is_rejected(tmp_path: Path) -> None:
    store, artifact = completed(tmp_path)
    events = store.inspect_run("run-1").events
    assert project(events).result == artifact.result
    observed = events_of(store, ModelResponseObserved)[0].sequence
    remaining = tuple(e for e in events if e.sequence != observed)
    resequenced = tuple(
        e.model_copy(update={"sequence": i}) for i, e in enumerate(remaining)
    )
    with pytest.raises(
        ResearchValidationError, match="^provider reply requires an observed entity$"
    ):
        project(resequenced)


@pytest.mark.parametrize("failure", ["network", "timeout"])
def test_unobserved_claude_transport_failure_loads(
    tmp_path: Path, failure: str
) -> None:
    store = ResearchStore(tmp_path / "research.sqlite")

    def handler(request: httpx2.Request) -> httpx2.Response:
        if failure == "timeout":
            raise httpx2.ReadTimeout("test timeout", request=request)
        raise httpx2.ConnectError("test failure", request=request)

    artifact = anthropic_research(handler, REQUEST, store)
    assert artifact.result.failure
    assert artifact.result.failure.code == (
        "MODEL_TIMEOUT" if failure == "timeout" else "PROVIDER_NETWORK"
    )
    assert not events_of(store, ModelResponseObserved)
    assert ResearchStore(store.path).load_terminal("run-1") == artifact


@pytest.mark.parametrize("encoding", ["latin-1", "utf-16", "UTF-8"])
def test_non_utf8_claude_text_encoding_is_rejected(
    tmp_path: Path, encoding: str
) -> None:
    store, _ = completed(tmp_path)
    rewrite_observed(store, 0, text_encoding=encoding)
    assert_load_rejects(store, "provider response encoding must be UTF-8")


def test_returned_model_mismatch_is_reproduced_on_reload(tmp_path: Path) -> None:
    responses = [httpx2.Response(200, json=body) for body in graph_responses()[:4]] + [
        httpx2.Response(
            200, json=graph_responses()[-1] | {"model": "claude-substitute-model"}
        )
    ]
    store, artifact = durable(tmp_path, responses)
    result = artifact.result
    assert result.status == "INTERRUPTED" and result.assessment_origin == "RUNTIME"
    assert result.failure and result.failure.code == "INVALID_PROVIDER_ENVELOPE"
    assert result.failure.message == "Claude returned a different model"
    assert result.assessment.outcome == "ABSTAIN"
    call = [r for r in result.trace if isinstance(r, ModelCallRecord)][-1]
    assert call.request.stage == "final_assessment"
    assert isinstance(call.reply, FailureReply)
    assert call.reply.metadata.returned_model == "claude-substitute-model"
    assert ResearchStore(store.path).load_terminal("run-1") == artifact

    # Restoring the requested model in the stored body cannot launder the reply.
    observed = events_of(store, ModelResponseObserved)[-1].event
    assert isinstance(observed, ModelResponseObserved)
    rewrite_observed(
        store,
        -1,
        body=observed.body.replace(b"claude-substitute-model", MODEL.encode()),
    )
    assert_load_rejects(store, "observable provider body differs from normalized reply")


@pytest.mark.parametrize("provider", ["fake", "openai"])
def test_unregistered_provider_keeps_normalized_prepared_body_and_cannot_observe(
    tmp_path: Path, provider: str
) -> None:
    from test_research_persistence import ObservedModel, execute

    from crypteo.research_model import (
        ModelReply,
        ModelRequest,
        ProviderObservation,
        ResponseObserver,
    )

    store = ResearchStore(tmp_path / "fake.sqlite")
    artifact = asyncio.run(
        execute(store, ObservedModel(outcome="ABSTAIN", provider=provider))
    )
    assert artifact.result.provider.provider == provider
    assert set(PROVIDER_CODECS) == {"anthropic"}
    assert store.load_terminal("run-1") == artifact

    class Observing(ObservedModel):
        async def invoke_observed(
            self, request: ModelRequest, observe: ResponseObserver
        ) -> ModelReply:
            observe(ProviderObservation(body=b"opaque", http_status=200))
            return await self.invoke(request)

    store = ResearchStore(tmp_path / "observing.sqlite")
    with pytest.raises(
        ResearchValidationError, match="^unsupported observable provider encoding"
    ):
        asyncio.run(execute(store, Observing(provider=provider)))
    assert store.inspect_run("run-1").state == "INCOMPLETE"
