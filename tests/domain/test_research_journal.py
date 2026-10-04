"""Closed immutable lifecycle contracts and stable byte integrity encoding."""

import pytest
from pydantic import ValidationError

from crypteo.domain.research_journal import (
    EventEnvelope,
    ModelCallStarted,
    ModelResponseObserved,
)
from crypteo.research_journal import canonical_bytes, decode_envelope, event_checksum


def test_event_checksum_version_one_golden() -> None:
    envelope = EventEnvelope(
        research_run_id="run-1",
        sequence=3,
        event=ModelCallStarted(call_id="run-1/model/0001"),
    )
    assert event_checksum(envelope) == (
        "sha256:ac4c28bef59f681369001c2d99b2f51e3215f45fdac80d728072ba2f60109f76"
    )
    assert decode_envelope(canonical_bytes(envelope), None) == envelope
    with pytest.raises(ValidationError):
        envelope.sequence = 4
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(envelope.model_copy(update={"sequence": -1}))


@pytest.mark.parametrize("body", [b"", b"\xff\x00\x80", "市場 😀".encode()])
def test_opaque_body_never_encoded_as_project_json(body: bytes) -> None:
    envelope = EventEnvelope(
        research_run_id="run-1",
        sequence=4,
        event=ModelResponseObserved(
            call_id="run-1/model/0001",
            body=body,
            http_status=200,
            request_id="request-😀",
        ),
    )
    raw = canonical_bytes(envelope)
    assert b'"body"' not in raw
    assert decode_envelope(raw, body) == envelope
    altered = envelope.model_copy(
        update={"event": envelope.event.model_copy(update={"body": body + b" "})}
    )
    assert event_checksum(envelope) != event_checksum(altered)
    with pytest.raises(ValueError, match="BLOB"):
        decode_envelope(raw, None)


@pytest.mark.parametrize(
    "event",
    [
        {"kind": "ARBITRARY", "payload": {}},
        {"kind": "MODEL_CALL_STARTED", "call_id": "id", "extra": {}},
        {"kind": "MODEL_CALL_STARTED", "call_id": "\ud800"},
    ],
)
def test_event_contract_is_closed_and_unicode_safe(event: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(
            {
                "research_run_id": "run-1",
                "sequence": 0,
                "event": event,
            }
        )
