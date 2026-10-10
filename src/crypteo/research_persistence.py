"""Caller-owned SQLite research store. No reopen-for-append or execution recovery."""

from __future__ import annotations

import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError

from crypteo.domain.research_journal import (
    AbortDiagnostic,
    EventEnvelope,
    EventReceipt,
    LifecycleEvent,
    ModelResponseObserved,
    ResearchArtifact,
    ResearchRunHeader,
    ResearchRunInspection,
    RunAborted,
    RunFinished,
    RunStarted,
)
from crypteo.domain.research_workflow import ResearchRunResult
from crypteo.research_journal import (
    ResearchStorageError,
    artifact_checksum,
    canonical_bytes,
    decode_envelope,
    event_checksum,
    ordered_digest,
    project,
)
from crypteo.research_model import ModelOutputError, parse_json
from crypteo.research_validation import ResearchValidationError

_REGISTRATION_CAPABILITY = object()

_SCHEMA = (
    """CREATE TABLE research_runs_v1 (
        research_run_id TEXT PRIMARY KEY NOT NULL,
        schema_version TEXT NOT NULL CHECK(schema_version = '1'),
        header_json BLOB NOT NULL CHECK(typeof(header_json) = 'blob')
    ) WITHOUT ROWID""",
    """CREATE TABLE research_events_v1 (
        research_run_id TEXT NOT NULL REFERENCES research_runs_v1(research_run_id),
        sequence INTEGER NOT NULL CHECK(sequence >= 0),
        kind TEXT NOT NULL CHECK(kind IN (
            'RUN_STARTED','MODEL_REQUEST_PREPARED','MODEL_REQUEST_REJECTED',
            'MODEL_CALL_STARTED','MODEL_RESPONSE_OBSERVED','MODEL_CALL_FINISHED',
            'TOOL_REQUESTED','TOOL_RESOLVED','STAGE_COMPLETED','NODE_TRANSITION',
            'RUN_FINISHED','RUN_ABORTED')),
        envelope_json BLOB NOT NULL CHECK(typeof(envelope_json) = 'blob'),
        response_body BLOB,
        checksum TEXT NOT NULL CHECK(length(checksum) = 71),
        PRIMARY KEY(research_run_id, sequence),
        CHECK((kind = 'MODEL_RESPONSE_OBSERVED' AND typeof(response_body) = 'blob')
            OR (kind != 'MODEL_RESPONSE_OBSERVED' AND response_body IS NULL))
    ) WITHOUT ROWID""",
    """CREATE TABLE research_artifacts_v1 (
        research_run_id TEXT PRIMARY KEY NOT NULL
            REFERENCES research_runs_v1(research_run_id),
        terminal_sequence INTEGER NOT NULL,
        artifact_json BLOB NOT NULL CHECK(typeof(artifact_json) = 'blob'),
        FOREIGN KEY(research_run_id, terminal_sequence)
            REFERENCES research_events_v1(research_run_id, sequence)
    ) WITHOUT ROWID""",
    """CREATE TRIGGER research_event_order_v1 BEFORE INSERT ON research_events_v1
    BEGIN
        SELECT CASE WHEN NEW.sequence != (
            SELECT count(*) FROM research_events_v1
            WHERE research_run_id = NEW.research_run_id
        ) THEN RAISE(ABORT, 'noncontiguous sequence') END;
        SELECT CASE WHEN EXISTS (
            SELECT 1 FROM research_events_v1 WHERE research_run_id = NEW.research_run_id
            AND kind IN ('RUN_FINISHED','RUN_ABORTED')
        ) THEN RAISE(ABORT, 'sealed run') END;
        SELECT CASE WHEN (NEW.sequence = 0) != (NEW.kind = 'RUN_STARTED')
            THEN RAISE(ABORT, 'invalid start') END;
    END""",
    """CREATE TRIGGER research_artifact_terminal_v1
    BEFORE INSERT ON research_artifacts_v1
    BEGIN
        SELECT CASE WHEN NOT EXISTS (
            SELECT 1 FROM research_events_v1 WHERE research_run_id = NEW.research_run_id
            AND sequence = NEW.terminal_sequence AND kind = 'RUN_FINISHED'
        ) THEN RAISE(ABORT, 'artifact without terminal') END;
    END""",
) + tuple(
    f"CREATE TRIGGER {table}_{operation.lower()} BEFORE {operation} ON {table} "
    "BEGIN SELECT RAISE(ABORT, 'immutable research history'); END"
    for table in ("research_runs_v1", "research_events_v1", "research_artifacts_v1")
    for operation in ("UPDATE", "DELETE")
)

# REPLACE must not delete immutable identities, regardless of recursive_triggers.
_SCHEMA += tuple(
    f"CREATE TRIGGER {table}_insert_identity BEFORE INSERT ON {table} "
    f"WHEN EXISTS (SELECT 1 FROM {table} WHERE {identity}) "
    "BEGIN SELECT RAISE(ABORT, 'immutable research identity'); END"
    for table, identity in (
        ("research_runs_v1", "research_run_id = NEW.research_run_id"),
        (
            "research_events_v1",
            "research_run_id = NEW.research_run_id AND sequence = NEW.sequence",
        ),
        ("research_artifacts_v1", "research_run_id = NEW.research_run_id"),
    )
)


def _normalized(sql: str) -> str:
    return " ".join(sql.split()).lower()


@contextmanager
def _stored_contract() -> Iterator[None]:
    """Translate known untrusted serialization failures, never arbitrary errors."""
    try:
        yield
    except (
        ValidationError,
        ModelOutputError,
        UnicodeDecodeError,
        ResearchValidationError,
    ) as error:
        raise ResearchStorageError("stored research contract is invalid") from error


def _load_contract[Contract: BaseModel](
    raw: bytes, contract: type[Contract]
) -> Contract:
    with _stored_contract():
        parse_json(raw.decode("utf-8"))
        return contract.model_validate_json(raw)
    raise AssertionError("unreachable contract decoding")


def _event_row(envelope: EventEnvelope) -> tuple[int, str, bytes, bytes | None, str]:
    event = envelope.event
    return (
        envelope.sequence,
        event.kind,
        canonical_bytes(envelope),
        event.body if isinstance(event, ModelResponseObserved) else None,
        event_checksum(envelope),
    )


class ResearchStore:
    """A caller-owned database path; each transaction owns a connection."""

    def __init__(self, path: Path, *, lock_wait_ms: int = 1000) -> None:
        if type(lock_wait_ms) is not int or not 0 <= lock_wait_ms <= 60_000:
            raise ValueError("lock_wait_ms must be an integer between 0 and 60000")
        self.path, self.lock_wait_ms = Path(path).absolute(), lock_wait_ms
        with self._transaction(write=True, initialize=True) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            objects = connection.execute(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if version == 0 and not objects:
                for statement in _SCHEMA:
                    connection.execute(statement)
                connection.execute("PRAGMA user_version=1")
            self._validate_schema(connection)

    def _connect(self, *, create: bool = False) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path.as_uri() + ("?mode=rwc" if create else "?mode=rw"),
            uri=True,
            timeout=self.lock_wait_ms / 1000,
            isolation_level=None,
        )
        try:
            settings: tuple[tuple[str, str, str | int], ...] = (
                ("busy_timeout", str(self.lock_wait_ms), self.lock_wait_ms),
                ("journal_mode", "DELETE", "delete"),
                ("synchronous", "EXTRA", 3),
                ("foreign_keys", "ON", 1),
            )
            if sys.platform == "darwin":
                settings += (("fullfsync", "ON", 1),)
            for name, value, expected in settings:
                connection.execute(f"PRAGMA {name}={value}")
                row = connection.execute(f"PRAGMA {name}").fetchone()
                if row is None or row[0] != expected:
                    raise ResearchStorageError(f"SQLite did not configure {name}")
            return connection
        except BaseException:
            with suppress(sqlite3.Error):
                connection.close()
            raise

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ResearchStorageError("unsupported research store schema")
        actual = {
            _normalized(row[0])
            for row in connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
            )
        }
        if actual != {_normalized(sql) for sql in _SCHEMA}:
            raise ResearchStorageError("research store schema/constraints differ")

    @contextmanager
    def _transaction(
        self,
        *,
        write: bool,
        initialize: bool = False,
    ) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect(create=initialize)
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            if not initialize:
                self._validate_schema(connection)
            yield connection
            self._commit(connection)
        except BaseException as error:
            if connection is not None:
                with suppress(sqlite3.Error):
                    connection.rollback()
            if isinstance(error, (sqlite3.Error, OSError)):
                raise ResearchStorageError(
                    "research storage operation failed"
                ) from error
            raise
        finally:
            if connection is not None:
                active_error = sys.exc_info()[0] is not None
                try:
                    connection.close()
                except sqlite3.Error as error:
                    if not active_error:
                        raise ResearchStorageError(
                            "research connection close failed"
                        ) from error

    @staticmethod
    def _commit(connection: sqlite3.Connection) -> None:
        connection.commit()

    def create_run(self, header: ResearchRunHeader) -> ResearchJournal:
        header = ResearchRunHeader.model_validate(header)
        envelope = EventEnvelope(
            research_run_id=header.research_run_id,
            sequence=0,
            event=RunStarted(header=header),
        )
        project((envelope,))
        with self._transaction(write=True) as connection:
            connection.execute(
                "INSERT INTO research_runs_v1 VALUES (?, ?, ?)",
                (header.research_run_id, "1", canonical_bytes(header)),
            )
            self._insert(connection, envelope)
        return ResearchJournal(
            self, header, (envelope,), _registration=_REGISTRATION_CAPABILITY
        )

    @staticmethod
    def _insert(connection: sqlite3.Connection, envelope: EventEnvelope) -> None:
        connection.execute(
            "INSERT INTO research_events_v1 VALUES (?, ?, ?, ?, ?, ?)",
            (envelope.research_run_id, *_event_row(envelope)),
        )

    def inspect_run(self, research_run_id: str) -> ResearchRunInspection:
        with self._transaction(write=False) as connection:
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ResearchStorageError("SQLite integrity check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ResearchStorageError("SQLite foreign key validation failed")
            row = connection.execute(
                "SELECT schema_version, header_json FROM research_runs_v1 "
                "WHERE research_run_id=?",
                (research_run_id,),
            ).fetchone()
            if row is None or row[0] != "1":
                raise ResearchStorageError("run missing or unsupported version")
            header_raw = row[1]
            header = _load_contract(header_raw, ResearchRunHeader)
            if (
                header.research_run_id != research_run_id
                or canonical_bytes(header) != header_raw
            ):
                raise ResearchStorageError("invalid run registration")
            events: list[EventEnvelope] = []
            receipts: list[EventReceipt] = []
            for sequence, kind, raw, body, checksum in connection.execute(
                "SELECT sequence, kind, envelope_json, response_body, checksum "
                "FROM research_events_v1 WHERE research_run_id=? ORDER BY sequence",
                (research_run_id,),
            ):
                with _stored_contract():
                    envelope = decode_envelope(raw, body)
                if (
                    envelope.sequence != sequence
                    or envelope.event.kind != kind
                    or event_checksum(envelope) != checksum
                ):
                    raise ResearchStorageError("event checksum/identity mismatch")
                events.append(envelope)
                receipts.append(
                    EventReceipt(
                        research_run_id=research_run_id,
                        sequence=sequence,
                        checksum=checksum,
                    )
                )
            try:
                projection = project(tuple(events))
            except (ResearchValidationError, ModelOutputError) as error:
                raise ResearchStorageError(
                    "stored research relationships are invalid"
                ) from error
            if projection.header != header:
                raise ResearchStorageError("start differs from registration")
            stored = connection.execute(
                "SELECT terminal_sequence, artifact_json FROM research_artifacts_v1 "
                "WHERE research_run_id=?",
                (research_run_id,),
            ).fetchone()
            state: Literal["INCOMPLETE", "COMPLETED", "INTERRUPTED", "ABORTED"]
            artifact: ResearchArtifact | None = None
            if isinstance(projection.terminal, RunFinished):
                if stored is None:
                    raise ResearchStorageError("terminal artifact missing")
                artifact = _load_contract(stored[1], ResearchArtifact)
                if (
                    canonical_bytes(artifact) != stored[1]
                    or artifact.artifact_checksum != artifact_checksum(artifact)
                    or artifact.research_run_id != research_run_id
                    or artifact.terminal_sequence != len(events) - 1
                    or stored[0] != artifact.terminal_sequence
                    or artifact.ordered_journal_digest
                    != ordered_digest(tuple(r.checksum for r in receipts))
                    or artifact.result != projection.result
                ):
                    raise ResearchStorageError(
                        "artifact differs from journal projection"
                    )
                state = (
                    "COMPLETED"
                    if projection.terminal.status == "COMPLETED"
                    else "INTERRUPTED"
                )
            else:
                if stored is not None:
                    raise ResearchStorageError("nonfinished run has artifact")
                state = "ABORTED" if projection.terminal else "INCOMPLETE"
            return ResearchRunInspection(
                header=header,
                state=state,
                events=tuple(events),
                receipts=tuple(receipts),
                pending_boundaries=projection.pending,
                artifact=artifact,
            )

    def load_terminal(self, research_run_id: str) -> ResearchArtifact:
        inspection = self.inspect_run(research_run_id)
        if inspection.artifact is None:
            raise ResearchStorageError("run has no terminal research artifact")
        return inspection.artifact


class ResearchJournal:
    """The sole writer capability, issued only by successful registration."""

    def __init__(
        self,
        store: ResearchStore,
        header: ResearchRunHeader,
        events: tuple[EventEnvelope, ...],
        *,
        _registration: object,
    ) -> None:
        if _registration is not _REGISTRATION_CAPABILITY:
            raise ResearchStorageError("writer requires successful new registration")
        self._store, self.header, self._events = store, header, events
        self._healthy = True
        self._sealed = False

    @property
    def healthy(self) -> bool:
        return self._healthy and not self._sealed

    def _write(
        self,
        event: LifecycleEvent,
        result: ResearchRunResult | None = None,
    ) -> tuple[EventReceipt, ResearchArtifact | None]:
        if not self.healthy:
            raise ResearchStorageError(
                "journal is sealed or storage health is uncertain"
            )
        try:
            envelope = EventEnvelope(
                research_run_id=self.header.research_run_id,
                sequence=len(self._events),
                event=event,
            )
            events = self._events + (envelope,)
            projection = project(events)
            artifact = None
            if result is not None:
                if projection.result != ResearchRunResult.model_validate(result):
                    raise ResearchStorageError(
                        "final result differs from journal projection"
                    )
                artifact = ResearchArtifact(
                    research_run_id=self.header.research_run_id,
                    terminal_sequence=envelope.sequence,
                    ordered_journal_digest=ordered_digest(
                        tuple(event_checksum(e) for e in events)
                    ),
                    result=result,
                    artifact_checksum="sha256:" + "0" * 64,
                )
                artifact = artifact.model_copy(
                    update={"artifact_checksum": artifact_checksum(artifact)}
                )
            expected = [_event_row(e) for e in self._events]
            with self._store._transaction(write=True) as connection:
                registration = connection.execute(
                    "SELECT schema_version, header_json FROM research_runs_v1 "
                    "WHERE research_run_id=?",
                    (self.header.research_run_id,),
                ).fetchone()
                if registration != ("1", canonical_bytes(self.header)):
                    raise ResearchStorageError("committed registration changed")
                # Check SQL identities as well as bytes/checksums against the prefix.
                rows = connection.execute(
                    "SELECT sequence, kind, envelope_json, response_body, checksum "
                    "FROM research_events_v1 WHERE research_run_id=? ORDER BY sequence",
                    (self.header.research_run_id,),
                ).fetchall()
                if rows != expected:
                    raise ResearchStorageError("committed journal prefix changed")
                self._store._insert(connection, envelope)
                if artifact is not None:
                    connection.execute(
                        "INSERT INTO research_artifacts_v1 VALUES (?, ?, ?)",
                        (
                            artifact.research_run_id,
                            artifact.terminal_sequence,
                            canonical_bytes(artifact),
                        ),
                    )
            self._events = events
            self._sealed = isinstance(event, (RunFinished, RunAborted))
            return EventReceipt(
                research_run_id=envelope.research_run_id,
                sequence=envelope.sequence,
                checksum=event_checksum(envelope),
            ), artifact
        except BaseException:
            self._healthy = False
            raise

    def append(self, event: LifecycleEvent) -> EventReceipt:
        if isinstance(event, (RunStarted, RunFinished, RunAborted)):
            raise ResearchStorageError(
                "use registration/finalize/abort for run boundaries"
            )
        return self._write(event)[0]

    def finalize(self, result: ResearchRunResult) -> ResearchArtifact:
        try:
            result = ResearchRunResult.model_validate(result)
            event = RunFinished(
                status=result.status,
                assessment_origin=result.assessment_origin,
                assessment=result.assessment,
                failure=result.failure,
            )
            artifact = self._write(event, result)[1]
            if artifact is None:
                raise ResearchStorageError("publication did not produce an artifact")
            return artifact
        except BaseException:
            self._healthy = False
            raise

    def abort(self, diagnostic: AbortDiagnostic) -> EventReceipt:
        return self._write(RunAborted(diagnostic=diagnostic))[0]
