import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from crypteo.coinbase import (
    CoinbaseCandleRequest,
    CoinbaseCandleRetriever,
    CoinbaseRawCandleResponse,
)
from crypteo.coinbase_dataset import (
    CoinbaseHistoricalDataset,
    compose_coinbase_dataset,
)
from crypteo.coinbase_persistence import (
    CoinbaseLocalPersistence,
    ingest_coinbase_candles,
)
from crypteo.domain.market import Candle, DatasetManifest, TradingPair

START = datetime(2026, 1, 1, tzinfo=UTC)
CREATED_AT = datetime(2026, 2, 1, tzinfo=UTC)
_CANDLES_ADAPTER = TypeAdapter(tuple[Candle, ...])


class FakeTransport:
    def __init__(self, bodies: list[bytes]) -> None:
        self._bodies = bodies
        self.calls: list[tuple[str, dict[str, str | int]]] = []

    def get(self, url: str, params: Mapping[str, str | int]) -> bytes:
        self.calls.append((url, dict(params)))
        return self._bodies[len(self.calls) - 1]


def body(hours: list[int]) -> bytes:
    rows = [
        [
            int((START + timedelta(hours=hour)).timestamp()),
            99.125,
            105.875,
            100.25,
            103.5,
            12.125,
        ]
        for hour in hours
    ]
    return json.dumps(rows, separators=(",", ":")).encode()


def response(
    response_body: bytes,
    *,
    start: datetime = START,
    end: datetime = START + timedelta(hours=1),
) -> CoinbaseRawCandleResponse:
    return CoinbaseRawCandleResponse(
        acquisition_started_at=CREATED_AT,
        request=CoinbaseCandleRequest(
            pair=TradingPair.BTC_USD,
            start=start,
            end=end,
        ),
        body=response_body,
    )


def canonical_dataset(
    *, created_at: datetime = CREATED_AT
) -> CoinbaseHistoricalDataset:
    return compose_coinbase_dataset(
        pair=TradingPair.BTC_USD,
        raw_responses=[response(body([0]))],
        requested_start=START,
        requested_end=START + timedelta(hours=1),
        created_at=created_at,
    )


def test_persists_exact_raw_body_and_request_metadata(tmp_path: Path) -> None:
    raw = response(b"[ [1767225600, 99, 105, 100, 103, 12] ]\n")
    persistence = CoinbaseLocalPersistence(tmp_path)

    persistence.persist_raw([raw])

    artifact_dir = persistence.raw_artifact_dir(raw)
    assert (artifact_dir / "body.json").read_bytes() == raw.body
    assert json.loads((artifact_dir / "request.json").read_bytes())["request"] == {
        "pair": "BTC-USD",
        "timeframe": "1h",
        "granularity": 3600,
        "start": "2026-01-01T00:00:00.000000Z",
        "end": "2026-01-01T01:00:00.000000Z",
        "url": "https://api.exchange.coinbase.com/products/BTC-USD/candles",
        "params": {
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-01T01:00:00Z",
            "granularity": 3600,
        },
    }


def test_different_request_ranges_have_different_raw_artifact_dirs(
    tmp_path: Path,
) -> None:
    persistence = CoinbaseLocalPersistence(tmp_path)
    first = response(b"[]")
    second = response(
        b"[]",
        start=START + timedelta(hours=1),
        end=START + timedelta(hours=2),
    )

    assert persistence.raw_artifact_dir(first) != persistence.raw_artifact_dir(second)


def test_raw_artifacts_survive_later_normalization_failure(tmp_path: Path) -> None:
    malformed_body = b"[[1767225600,1]]"
    transport = FakeTransport([malformed_body])
    retriever = CoinbaseCandleRetriever(transport)
    raw = response(malformed_body)
    persistence = CoinbaseLocalPersistence(tmp_path)

    with pytest.raises(ValueError, match="six"):
        ingest_coinbase_candles(
            retriever=retriever,
            artifact_root=tmp_path,
            pair=TradingPair.BTC_USD,
            start=START,
            end=START + timedelta(hours=1),
            acquisition_started_at=CREATED_AT,
            created_at=CREATED_AT,
        )

    artifact_dir = persistence.raw_artifact_dir(raw)
    assert (artifact_dir / "body.json").read_bytes() == malformed_body
    assert (artifact_dir / "request.json").is_file()
    assert not (tmp_path / "canonical").exists()
    assert not (tmp_path / "acquisitions").exists()


def test_repeated_identical_raw_persistence_is_safe(tmp_path: Path) -> None:
    raw = response(body([0]))
    persistence = CoinbaseLocalPersistence(tmp_path)

    persistence.persist_raw([raw])
    persistence.persist_raw([raw])

    assert (persistence.raw_artifact_dir(raw) / "body.json").read_bytes() == raw.body


def test_revised_raw_bodies_coexist(tmp_path: Path) -> None:
    persistence = CoinbaseLocalPersistence(tmp_path)
    original = response(b"[]")
    conflicting = response(body([0]))

    persistence.persist_raw([original])

    persistence.persist_raw([conflicting])
    assert persistence.raw_artifact_dir(original) != persistence.raw_artifact_dir(
        conflicting
    )
    assert (
        persistence.raw_artifact_dir(conflicting) / "body.json"
    ).read_bytes() == conflicting.body

    assert (persistence.raw_artifact_dir(original) / "body.json").read_bytes() == b"[]"


def test_success_persists_versioned_candles_and_manifest_that_round_trip(
    tmp_path: Path,
) -> None:
    precise_close = "103.5000000000000000000000001"
    precise_body = (
        f"[[{int(START.timestamp())},99.125,105.875,100.25,{precise_close},12.125]]"
    ).encode()
    transport = FakeTransport([precise_body])
    dataset = ingest_coinbase_candles(
        retriever=CoinbaseCandleRetriever(transport),
        artifact_root=tmp_path,
        pair=TradingPair.BTC_USD,
        start=START,
        end=START + timedelta(hours=1),
        acquisition_started_at=CREATED_AT,
        created_at=CREATED_AT,
    )
    persistence = CoinbaseLocalPersistence(tmp_path)
    artifact_dir = persistence.dataset_artifact_dir(dataset.manifest.dataset_version)

    restored_candles = _CANDLES_ADAPTER.validate_json(
        (artifact_dir / "candles.json").read_bytes()
    )
    restored_manifest = DatasetManifest.model_validate_json(
        (artifact_dir / "manifest.json").read_bytes()
    )

    digest = dataset.manifest.dataset_version.removeprefix("sha256:")
    assert artifact_dir == tmp_path / "canonical" / "sha256" / digest
    assert artifact_dir.is_relative_to(tmp_path)
    assert restored_candles == dataset.candles
    assert restored_candles[0].close == Decimal(precise_close)
    assert restored_manifest == dataset.manifest
    assert restored_manifest.dataset_version == dataset.manifest.dataset_version


def test_repeated_identical_canonical_persistence_is_safe(tmp_path: Path) -> None:
    transport = FakeTransport([body([0])])
    dataset = ingest_coinbase_candles(
        retriever=CoinbaseCandleRetriever(transport),
        artifact_root=tmp_path,
        pair=TradingPair.BTC_USD,
        start=START,
        end=START + timedelta(hours=1),
        acquisition_started_at=CREATED_AT,
        created_at=CREATED_AT,
    )
    persistence = CoinbaseLocalPersistence(tmp_path)

    persistence.persist_dataset(dataset)

    artifact_dir = persistence.dataset_artifact_dir(dataset.manifest.dataset_version)
    assert (artifact_dir / "candles.json").is_file()
    assert (artifact_dir / "manifest.json").is_file()


def test_conflicting_canonical_overwrite_is_rejected(tmp_path: Path) -> None:
    transport = FakeTransport([body([0])])
    dataset = ingest_coinbase_candles(
        retriever=CoinbaseCandleRetriever(transport),
        artifact_root=tmp_path,
        pair=TradingPair.BTC_USD,
        start=START,
        end=START + timedelta(hours=1),
        acquisition_started_at=CREATED_AT,
        created_at=CREATED_AT,
    )
    persistence = CoinbaseLocalPersistence(tmp_path)
    candles_path = (
        persistence.dataset_artifact_dir(dataset.manifest.dataset_version)
        / "candles.json"
    )
    candles_path.write_bytes(b"[]")

    with pytest.raises(ValueError, match="coverage mismatch"):
        persistence.persist_dataset(dataset)


@pytest.mark.parametrize(
    "invalid_version",
    [
        "../outside",
        "/absolute/path",
        "sha1:" + "a" * 64,
        "sha256:" + "a" * 63,
        "sha256:" + "a" * 65,
        "sha256:" + "A" * 64,
        "sha256:" + "g" * 64,
        "sha256:" + "a" * 63 + "/",
    ],
)
def test_rejects_invalid_dataset_versions_before_writing(
    tmp_path: Path, invalid_version: str
) -> None:
    persistence = CoinbaseLocalPersistence(tmp_path)
    dataset = canonical_dataset()
    invalid_manifest = dataset.manifest.model_copy(
        update={"dataset_version": invalid_version}
    )
    invalid_dataset = replace(dataset, manifest=invalid_manifest)

    with pytest.raises(ValueError, match="64 lowercase hex"):
        persistence.persist_dataset(invalid_dataset)

    assert not (tmp_path / "canonical").exists()
    assert not (tmp_path / "acquisitions").exists()


def test_valid_generated_version_path_is_contained_by_root(tmp_path: Path) -> None:
    persistence = CoinbaseLocalPersistence(tmp_path)
    version = canonical_dataset().manifest.dataset_version

    artifact_dir = persistence.dataset_artifact_dir(version)

    assert artifact_dir == (
        tmp_path / "canonical" / "sha256" / version.removeprefix("sha256:")
    )
    assert artifact_dir.is_relative_to(tmp_path)


def test_same_version_with_different_created_at_preserves_first_manifest(
    tmp_path: Path,
) -> None:
    persistence = CoinbaseLocalPersistence(tmp_path)
    first = canonical_dataset(created_at=CREATED_AT)
    second = canonical_dataset(created_at=CREATED_AT + timedelta(days=1))
    assert first.manifest.dataset_version == second.manifest.dataset_version

    persistence.persist_dataset(first)
    manifest_path = (
        persistence.dataset_artifact_dir(first.manifest.dataset_version)
        / "manifest.json"
    )
    first_bytes = manifest_path.read_bytes()

    assert persistence.persist_dataset(second) == first.manifest

    assert manifest_path.read_bytes() == first_bytes
    assert DatasetManifest.model_validate_json(first_bytes) == first.manifest


def test_fixture_end_to_end_persists_gap_dataset(tmp_path: Path) -> None:
    raw_body = body([2, 0])
    transport = FakeTransport([raw_body])

    dataset = ingest_coinbase_candles(
        retriever=CoinbaseCandleRetriever(transport),
        artifact_root=tmp_path,
        pair=TradingPair.BTC_USD,
        start=START,
        end=START + timedelta(hours=3),
        acquisition_started_at=CREATED_AT,
        created_at=CREATED_AT,
    )
    persistence = CoinbaseLocalPersistence(tmp_path)
    raw = response(raw_body, end=START + timedelta(hours=3))
    canonical_dir = persistence.dataset_artifact_dir(dataset.manifest.dataset_version)

    assert len(transport.calls) == 1
    assert (persistence.raw_artifact_dir(raw) / "body.json").read_bytes() == raw_body
    assert [candle.open_time for candle in dataset.candles] == [
        START,
        START + timedelta(hours=2),
    ]
    assert dataset.manifest.missing_open_times == (START + timedelta(hours=1),)
    assert dataset.manifest.dataset_version.startswith("sha256:")
    assert (canonical_dir / "candles.json").is_file()
    assert (canonical_dir / "manifest.json").is_file()


def test_successful_capture_is_persisted_before_later_transport_failure(
    tmp_path: Path,
) -> None:
    first = response(body([0]), end=START + timedelta(hours=300))
    persistence = CoinbaseLocalPersistence(tmp_path)

    class FailingTransport:
        calls = 0

        def get(self, url: str, params: Mapping[str, str | int]) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return first.body
            artifact_dir = persistence.raw_artifact_dir(first)
            assert (artifact_dir / "body.json").read_bytes() == first.body
            assert (artifact_dir / "request.json").is_file()
            raise RuntimeError("later transport failure")

    transport = FailingTransport()
    with pytest.raises(RuntimeError, match="later transport"):
        ingest_coinbase_candles(
            retriever=CoinbaseCandleRetriever(transport),
            artifact_root=tmp_path,
            pair=TradingPair.BTC_USD,
            start=START,
            end=START + timedelta(hours=301),
            acquisition_started_at=CREATED_AT,
            created_at=CREATED_AT,
        )
    assert transport.calls == 2
    assert (
        persistence.raw_artifact_dir(first) / "body.json"
    ).read_bytes() == first.body
    assert not (tmp_path / "canonical").exists()
    assert not (tmp_path / "acquisitions").exists()


def test_capture_id_matches_persisted_canonical_envelope(tmp_path: Path) -> None:
    from hashlib import sha256

    from crypteo.coinbase_persistence import coinbase_capture_id

    raw = response(body([0]))
    persistence = CoinbaseLocalPersistence(tmp_path)
    persistence.persist_raw([raw])
    artifact_dir = persistence.raw_artifact_dir(raw)
    envelope_bytes = (artifact_dir / "request.json").read_bytes()
    envelope = json.loads(envelope_bytes)
    assert envelope["capture_format_version"] == "1"
    assert envelope["acquisition_started_at"] == "2026-02-01T00:00:00.000000Z"
    assert envelope["body_sha256"] == sha256(raw.body).hexdigest()
    digest = sha256(envelope_bytes).hexdigest()
    assert coinbase_capture_id(raw) == f"sha256:{digest}"
    assert (
        artifact_dir
        == tmp_path / "raw" / "coinbase" / "BTC-USD" / "1h" / "sha256" / digest
    )
    before = {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in artifact_dir.iterdir()
    }
    persistence.persist_raw([raw])
    assert before == {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in artifact_dir.iterdir()
    }


def test_acquisition_provenance_changes_capture_identity(tmp_path: Path) -> None:
    persistence = CoinbaseLocalPersistence(tmp_path)
    first = response(b"[]")
    later = replace(first, acquisition_started_at=CREATED_AT + timedelta(seconds=1))
    persistence.persist_raw([first, later])
    assert persistence.raw_artifact_dir(first) != persistence.raw_artifact_dir(later)


@pytest.mark.parametrize("hours", [0, 1, 3])
def test_persisted_acquisition_resolves_exact_captures_and_dataset(
    tmp_path: Path,
    hours: int,
) -> None:
    from crypteo.coinbase_acquisition import CoinbaseAcquisitionRecord
    from crypteo.coinbase_capture import coinbase_capture_id

    dataset = ingest_coinbase_candles(
        retriever=CoinbaseCandleRetriever(FakeTransport([b"[]"])),
        artifact_root=tmp_path,
        pair=TradingPair.BTC_USD,
        start=START,
        end=START + timedelta(hours=hours),
        acquisition_started_at=CREATED_AT,
        created_at=CREATED_AT,
    )
    persistence = CoinbaseLocalPersistence(tmp_path)
    record = dataset.acquisition
    path = persistence.acquisition_artifact_dir(record) / "acquisition.json"
    assert (
        path
        == tmp_path
        / "acquisitions"
        / "coinbase"
        / "sha256"
        / record.acquisition_id.removeprefix("sha256:")
        / "acquisition.json"
    )
    assert CoinbaseAcquisitionRecord.model_validate_json(path.read_bytes()) == record
    assert record.missing_open_times == tuple(
        START + timedelta(hours=h) for h in range(hours)
    )
    canonical = persistence.dataset_artifact_dir(record.dataset_version)
    assert (
        DatasetManifest.model_validate_json((canonical / "manifest.json").read_bytes())
        == dataset.manifest
    )
    assert (
        _CANDLES_ADAPTER.validate_json((canonical / "candles.json").read_bytes())
        == dataset.candles
    )
    assert record.capture_ids == tuple(
        sorted(coinbase_capture_id(raw) for raw in dataset.raw_responses)
    )
    for raw in dataset.raw_responses:
        assert (
            persistence.raw_artifact_dir(raw) / "body.json"
        ).read_bytes() == raw.body
    before = path.stat().st_mtime_ns
    persistence.persist_acquisition(dataset)
    assert path.stat().st_mtime_ns == before


def test_same_canonical_dataset_can_link_multiple_requested_intervals(
    tmp_path: Path,
) -> None:
    persistence = CoinbaseLocalPersistence(tmp_path)
    results = [
        ingest_coinbase_candles(
            retriever=CoinbaseCandleRetriever(FakeTransport([body([0])])),
            artifact_root=tmp_path,
            pair=TradingPair.BTC_USD,
            start=START,
            end=START + timedelta(hours=hours),
            acquisition_started_at=CREATED_AT,
            created_at=CREATED_AT,
        )
        for hours in (1, 2)
    ]
    assert results[0].manifest == results[1].manifest
    assert (
        results[0].acquisition.acquisition_id != results[1].acquisition.acquisition_id
    )
    assert all(
        (
            persistence.acquisition_artifact_dir(d.acquisition) / "acquisition.json"
        ).is_file()
        for d in results
    )


def test_canonical_corruption_prevents_new_acquisition_publication(
    tmp_path: Path,
) -> None:
    persistence = CoinbaseLocalPersistence(tmp_path)
    first = ingest_coinbase_candles(
        retriever=CoinbaseCandleRetriever(FakeTransport([body([0])])),
        artifact_root=tmp_path,
        pair=TradingPair.BTC_USD,
        start=START,
        end=START + timedelta(hours=1),
        acquisition_started_at=CREATED_AT,
        created_at=CREATED_AT,
    )
    before = sorted((tmp_path / "acquisitions").rglob("acquisition.json"))
    candles_path = (
        persistence.dataset_artifact_dir(first.manifest.dataset_version)
        / "candles.json"
    )
    candles_path.write_bytes(b"[]")
    with pytest.raises(ValueError, match="coverage mismatch"):
        ingest_coinbase_candles(
            retriever=CoinbaseCandleRetriever(FakeTransport([body([0])])),
            artifact_root=tmp_path,
            pair=TradingPair.BTC_USD,
            start=START,
            end=START + timedelta(hours=1),
            acquisition_started_at=CREATED_AT,
            created_at=CREATED_AT + timedelta(days=1),
        )
    assert sorted((tmp_path / "acquisitions").rglob("acquisition.json")) == before
    assert before == [
        persistence.acquisition_artifact_dir(first.acquisition) / "acquisition.json"
    ]


def test_acquisition_cannot_publish_with_missing_raw_references(
    tmp_path: Path,
) -> None:
    dataset = canonical_dataset()
    persistence = CoinbaseLocalPersistence(tmp_path)
    persistence.persist_dataset(dataset)

    with pytest.raises(FileNotFoundError, match="raw capture artifact"):
        persistence.persist_acquisition(dataset)

    assert not (tmp_path / "acquisitions").exists()


def test_acquisition_cannot_publish_without_canonical_artifacts(
    tmp_path: Path,
) -> None:
    dataset = canonical_dataset()
    persistence = CoinbaseLocalPersistence(tmp_path)
    persistence.persist_raw(dataset.raw_responses)

    with pytest.raises(FileNotFoundError, match="canonical dataset artifact"):
        persistence.persist_acquisition(dataset)

    assert not (tmp_path / "acquisitions").exists()


def test_persisted_raw_and_canonical_artifacts_allow_acquisition_publication(
    tmp_path: Path,
) -> None:
    dataset = canonical_dataset()
    persistence = CoinbaseLocalPersistence(tmp_path)
    persistence.persist_raw(dataset.raw_responses)
    persistence.persist_dataset(dataset)

    persistence.persist_acquisition(dataset)

    acquisition_path = (
        persistence.acquisition_artifact_dir(dataset.acquisition) / "acquisition.json"
    )
    assert acquisition_path.read_bytes() == dataset.acquisition.canonical_bytes()


def test_acquisition_rejects_dataset_and_capture_reference_mismatches(
    tmp_path: Path,
) -> None:
    dataset = canonical_dataset()
    persistence = CoinbaseLocalPersistence(tmp_path)
    persistence.persist_raw(dataset.raw_responses)
    persistence.persist_dataset(dataset)

    wrong_dataset_version = dataset.acquisition.model_copy(
        update={"dataset_version": "sha256:" + "0" * 64}
    )
    with pytest.raises(ValueError, match="dataset_version"):
        persistence.persist_acquisition(
            replace(dataset, acquisition=wrong_dataset_version)
        )

    wrong_capture_ids = dataset.acquisition.model_copy(
        update={"capture_ids": ("sha256:" + "0" * 64,)}
    )
    with pytest.raises(ValueError, match="capture_ids"):
        persistence.persist_acquisition(replace(dataset, acquisition=wrong_capture_ids))

    assert not (tmp_path / "acquisitions").exists()


@pytest.mark.parametrize("spelling", ["100", "100.0", "100.00", "1E+2"])
def test_equivalent_decimal_reingestion_reuses_canonical_artifacts(
    tmp_path: Path, spelling: str
) -> None:
    persistence = CoinbaseLocalPersistence(tmp_path)
    results = []
    for close in ("100", spelling):
        raw_body = f"[[{int(START.timestamp())},99,105,100,{close},12]]".encode()
        results.append(
            ingest_coinbase_candles(
                retriever=CoinbaseCandleRetriever(FakeTransport([raw_body])),
                artifact_root=tmp_path,
                pair=TradingPair.BTC_USD,
                start=START,
                end=START + timedelta(hours=1),
                acquisition_started_at=CREATED_AT,
                created_at=CREATED_AT,
            )
        )
    assert results[0].manifest == results[1].manifest
    artifact_dir = persistence.dataset_artifact_dir(results[0].manifest.dataset_version)
    persisted = json.loads((artifact_dir / "candles.json").read_bytes())
    assert persisted[0]["close"] == "100"
    assert persisted[0]["open_time"] == "2026-01-01T00:00:00.000000Z"
    for result in results:
        assert (
            persistence.acquisition_artifact_dir(result.acquisition)
            / "acquisition.json"
        ).is_file()


def test_reingestion_returns_first_manifest_and_current_acquisition(
    tmp_path: Path,
) -> None:
    persistence = CoinbaseLocalPersistence(tmp_path)
    results = []
    original_bytes: dict[str, bytes] = {}
    for offset in (0, 1, 1):
        current_time = CREATED_AT + timedelta(days=offset)
        raw_body = body([0]) if offset == 0 else b" \n" + body([0]) + b"\n"
        result = ingest_coinbase_candles(
            retriever=CoinbaseCandleRetriever(FakeTransport([raw_body])),
            artifact_root=tmp_path,
            pair=TradingPair.BTC_USD,
            start=START,
            end=START + timedelta(hours=1),
            acquisition_started_at=current_time,
            created_at=current_time,
        )
        results.append(result)
        artifact_dir = persistence.dataset_artifact_dir(result.manifest.dataset_version)
        actual_bytes = {
            name: (artifact_dir / name).read_bytes()
            for name in ("candles.json", "manifest.json")
        }
        if offset == 0:
            original_bytes = actual_bytes
        assert actual_bytes == original_bytes
        assert result.manifest == DatasetManifest.model_validate_json(
            actual_bytes["manifest.json"]
        )
        assert result.manifest.created_at == CREATED_AT
        assert result.acquisition.created_at == current_time
        assert result.acquisition.dataset_version == result.manifest.dataset_version
    assert (
        results[0].acquisition.acquisition_id != results[1].acquisition.acquisition_id
    )
    assert results[1] == results[2]
    assert len(list((tmp_path / "acquisitions").rglob("acquisition.json"))) == 2


def test_canonical_representation_is_shared_and_decimal_context_independent(
    tmp_path: Path,
) -> None:
    precise = "103.5000000000000000000000001"
    raw = response(f"[[{int(START.timestamp())},99,105,100,{precise},0.00]]".encode())
    versions = []
    encodings = []
    for precision in (2, 28, 50):
        with localcontext() as context:
            context.prec = precision
            dataset = compose_coinbase_dataset(
                pair=TradingPair.BTC_USD,
                raw_responses=[raw],
                created_at=CREATED_AT,
                requested_start=START,
                requested_end=START + timedelta(hours=1),
            )
            persistence = CoinbaseLocalPersistence(tmp_path / str(precision))
            persistence.persist_dataset(dataset)
            encoded = (
                persistence.dataset_artifact_dir(dataset.manifest.dataset_version)
                / "candles.json"
            ).read_bytes()
            encodings.append(encoded)
            versions.append(dataset.manifest.dataset_version)
    assert len(set(versions)) == len(set(encodings)) == 1
    assert json.loads(encodings[0])[0]["close"] == precise
    assert json.loads(encodings[0])[0]["volume"] == "0"
    # The historical identity envelope contains exactly the persisted candle rows.
    envelope = dataset.manifest.model_dump(
        mode="json", exclude={"created_at", "dataset_version"}
    )
    envelope["start_time"] = "2026-01-01T00:00:00.000000Z"
    envelope["end_time"] = "2026-01-01T01:00:00.000000Z"
    envelope["candles"] = json.loads(encodings[0])
    assert (
        versions[0]
        == "sha256:"
        + sha256(
            json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def test_existing_semantically_equivalent_encoding_is_validated_without_rewrite(
    tmp_path: Path,
) -> None:
    dataset = compose_coinbase_dataset(
        pair=TradingPair.BTC_USD,
        raw_responses=[response(body([0, 1]), end=START + timedelta(hours=2))],
        created_at=CREATED_AT,
        requested_start=START,
        requested_end=START + timedelta(hours=2),
    )
    persistence = CoinbaseLocalPersistence(tmp_path)
    persistence.persist_dataset(dataset)
    path = (
        persistence.dataset_artifact_dir(dataset.manifest.dataset_version)
        / "candles.json"
    )
    rows = json.loads(path.read_bytes())
    rows[0]["open"] = "100.2500"
    rows[0]["open_time"] = "2026-01-01T00:00:00+00:00"
    alternate = json.dumps(list(reversed(rows)), indent=2).encode()
    path.write_bytes(alternate)
    assert (
        persistence.persist_dataset(
            replace(dataset, candles=tuple(reversed(dataset.candles)))
        )
        == dataset.manifest
    )
    persistence.persist_raw(dataset.raw_responses)
    persistence.persist_acquisition(
        replace(dataset, candles=tuple(reversed(dataset.candles)))
    )
    assert path.read_bytes() == alternate


@pytest.mark.parametrize(
    "artifact,change",
    [
        ("candles.json", "invalid_json"),
        ("manifest.json", "invalid_json"),
        ("candles.json", "invalid_price"),
        ("candles.json", "changed_price"),
        ("candles.json", "different_pair"),
        ("candles.json", "duplicate"),
        ("manifest.json", "different_pair"),
        ("manifest.json", "forged_version"),
        ("manifest.json", "invalid_row_count"),
        ("manifest.json", "different_source"),
        ("manifest.json", "shifted_coverage"),
    ],
)
def test_corrupted_canonical_artifacts_fail_closed_without_overwrite(
    tmp_path: Path,
    artifact: str,
    change: str,
) -> None:
    dataset = canonical_dataset()
    persistence = CoinbaseLocalPersistence(tmp_path)
    persistence.persist_dataset(dataset)
    persistence.persist_raw(dataset.raw_responses)
    artifact_dir = persistence.dataset_artifact_dir(dataset.manifest.dataset_version)
    path = artifact_dir / artifact
    payload = json.loads(path.read_bytes())
    if change == "invalid_json":
        corrupt = b"invalid JSON"
    else:
        target = payload[0] if artifact == "candles.json" else payload
        if change == "invalid_price":
            target["close"] = "-1"
        elif change == "changed_price":
            target["close"] = "104"
        elif change == "different_pair":
            target["pair"] = "ETH-USD"
        elif change == "duplicate":
            payload.append(payload[0])
        elif change == "forged_version":
            target["dataset_version"] = "sha256:" + "0" * 64
        elif change == "invalid_row_count":
            target["row_count"] = -1
        elif change == "different_source":
            target["source"] = "other_source"
        elif change == "shifted_coverage":
            target["start_time"] = "2026-01-01T01:00:00Z"
            target["end_time"] = "2026-01-01T02:00:00Z"
        corrupt = json.dumps(payload).encode()
    path.write_bytes(corrupt)
    before = {
        name: (artifact_dir / name).read_bytes()
        for name in ("candles.json", "manifest.json")
    }
    with pytest.raises(ValueError):
        persistence.persist_dataset(dataset)
    with pytest.raises(ValueError):
        persistence.persist_acquisition(dataset)
    assert before == {name: (artifact_dir / name).read_bytes() for name in before}
    assert not (tmp_path / "acquisitions").exists()


def test_forged_incoming_version_is_rejected_before_writing(tmp_path: Path) -> None:
    dataset = canonical_dataset()
    forged = replace(
        dataset,
        manifest=dataset.manifest.model_copy(
            update={"dataset_version": "sha256:" + "0" * 64}
        ),
    )
    with pytest.raises(ValueError, match="dataset_version"):
        CoinbaseLocalPersistence(tmp_path).persist_dataset(forged)
    assert not (tmp_path / "canonical").exists()


@pytest.mark.parametrize("name", ["candles.json", "manifest.json"])
def test_incomplete_existing_canonical_artifacts_are_not_repaired(
    tmp_path: Path, name: str
) -> None:
    dataset = canonical_dataset()
    persistence = CoinbaseLocalPersistence(tmp_path)
    persistence.persist_dataset(dataset)
    persistence.persist_raw(dataset.raw_responses)
    path = persistence.dataset_artifact_dir(dataset.manifest.dataset_version) / name
    path.rename(path.with_suffix(".missing"))
    with pytest.raises(FileNotFoundError):
        persistence.persist_dataset(dataset)
    with pytest.raises(FileNotFoundError):
        persistence.persist_acquisition(dataset)
    assert not path.exists()


def test_changed_canonical_content_gets_distinct_version_and_storage(
    tmp_path: Path,
) -> None:
    first = canonical_dataset()
    changed = compose_coinbase_dataset(
        pair=TradingPair.BTC_USD,
        raw_responses=[response(body([0]).replace(b"103.5", b"104.0"))],
        requested_start=START,
        requested_end=START + timedelta(hours=1),
        created_at=CREATED_AT,
    )
    persistence = CoinbaseLocalPersistence(tmp_path)
    assert first.manifest.dataset_version != changed.manifest.dataset_version
    persistence.persist_dataset(first)
    persistence.persist_dataset(changed)
    first_dir = persistence.dataset_artifact_dir(first.manifest.dataset_version)
    changed_dir = persistence.dataset_artifact_dir(changed.manifest.dataset_version)
    # Valid files for another version still cannot live at this version's path.
    for name in ("candles.json", "manifest.json"):
        (first_dir / name).write_bytes((changed_dir / name).read_bytes())
    with pytest.raises(ValueError, match="differs from incoming"):
        persistence.persist_dataset(first)
    with pytest.raises(ValueError):
        persistence.persist_acquisition(first)
    assert (first_dir / "candles.json").read_bytes() == (
        changed_dir / "candles.json"
    ).read_bytes()


def test_empty_reingestion_reuses_canonical_coverage_and_preserves_acquisitions(
    tmp_path: Path,
) -> None:
    results = []
    for hours in (0, 2):
        result = ingest_coinbase_candles(
            retriever=CoinbaseCandleRetriever(FakeTransport([b"[]"])),
            artifact_root=tmp_path,
            pair=TradingPair.BTC_USD,
            start=START,
            end=START + timedelta(hours=hours),
            acquisition_started_at=CREATED_AT,
            created_at=CREATED_AT + timedelta(days=hours),
        )
        results.append(result)
    assert results[0].manifest == results[1].manifest
    assert results[1].manifest.created_at == CREATED_AT
    assert results[1].manifest.start_time is results[1].manifest.end_time is None
    assert results[0].acquisition.missing_open_times == ()
    assert results[1].acquisition.missing_open_times == (
        START,
        START + timedelta(hours=1),
    )
    assert results[1].acquisition.created_at == CREATED_AT + timedelta(days=2)
    assert (
        results[0].acquisition.acquisition_id != results[1].acquisition.acquisition_id
    )


@pytest.mark.parametrize("field", ["candles", "manifest"])
def test_invalid_incoming_models_are_revalidated_before_persistence(
    tmp_path: Path, field: str
) -> None:
    dataset = canonical_dataset()
    if field == "candles":
        dataset = replace(
            dataset,
            candles=(dataset.candles[0].model_copy(update={"close": Decimal("-1")}),),
        )
    else:
        dataset = replace(
            dataset, manifest=dataset.manifest.model_copy(update={"row_count": -1})
        )
    with pytest.raises(ValueError):
        CoinbaseLocalPersistence(tmp_path).persist_dataset(dataset)
    assert not (tmp_path / "canonical").exists()


@pytest.mark.parametrize(
    "change", ["body", "body_whitespace", "invalid_request", "boundary", "request"]
)
def test_acquisition_rejects_corrupted_raw_artifacts(
    tmp_path: Path, change: str
) -> None:
    dataset = canonical_dataset()
    persistence = CoinbaseLocalPersistence(tmp_path)
    persistence.persist_dataset(dataset)
    persistence.persist_raw(dataset.raw_responses)
    raw_dir = persistence.raw_artifact_dir(dataset.raw_responses[0])
    if change in ("body", "body_whitespace"):
        path = raw_dir / "body.json"
        path.write_bytes(b"[]" if change == "body" else path.read_bytes() + b" ")
    else:
        path = raw_dir / "request.json"
        metadata = json.loads(path.read_bytes())
        if change == "boundary":
            metadata["acquisition_started_at"] = "2026-02-02T00:00:00.000000Z"
        elif change == "request":
            metadata["request"]["pair"] = "ETH-USD"
        path.write_bytes(
            b"invalid JSON"
            if change == "invalid_request"
            else json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()
        )
    before = path.read_bytes()
    with pytest.raises(ValueError, match="stored raw"):
        persistence.persist_acquisition(dataset)
    assert path.read_bytes() == before
    assert not (tmp_path / "acquisitions").exists()


@pytest.mark.parametrize("change", ["gaps", "bounds", "created_at", "unrelated"])
def test_acquisition_revalidates_capture_association(
    tmp_path: Path, change: str
) -> None:
    dataset = canonical_dataset()
    record = dataset.acquisition
    if change == "gaps":
        record = record.model_copy(update={"missing_open_times": (START,)})
        expected_error = "recomposed capture provenance"
    elif change == "bounds":
        record = record.model_copy(update={"requested_end": START + timedelta(hours=2)})
        expected_error = "request ranges must match"
    elif change == "created_at":
        record = record.model_copy(
            update={"created_at": CREATED_AT - timedelta(seconds=1)}
        )
        expected_error = "created_at precedes"
    else:
        unrelated = response(body([0]).replace(b"103.5", b"104.0"))
        from crypteo.coinbase_capture import coinbase_capture_id

        record = record.model_copy(
            update={"capture_ids": (coinbase_capture_id(unrelated),)}
        )
        dataset = replace(dataset, raw_responses=(unrelated,))
        expected_error = "do not compose"
    dataset = replace(dataset, acquisition=record)
    persistence = CoinbaseLocalPersistence(tmp_path)
    persistence.persist_dataset(dataset)
    persistence.persist_raw(dataset.raw_responses)
    with pytest.raises(ValueError, match=expected_error):
        persistence.persist_acquisition(dataset)
    assert not (tmp_path / "acquisitions").exists()


def test_direct_publication_reuses_first_manifest_read_only(tmp_path: Path) -> None:
    first = canonical_dataset()
    later = canonical_dataset(created_at=CREATED_AT + timedelta(days=1))
    persistence = CoinbaseLocalPersistence(tmp_path)
    persistence.persist_dataset(first)
    persistence.persist_raw(later.raw_responses)
    later = replace(later, manifest=first.manifest)
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in tmp_path.rglob("*.json")
    }
    persistence.persist_acquisition(later)
    assert all(
        (path.read_bytes(), path.stat().st_mtime_ns) == content
        for path, content in before.items()
    )
    assert (
        persistence.acquisition_artifact_dir(later.acquisition) / "acquisition.json"
    ).read_bytes() == later.acquisition.canonical_bytes()
    assert later.manifest.created_at < later.acquisition.created_at
