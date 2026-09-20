"""Minimal local persistence for the Coinbase ingestion path."""

import json
import re
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from pydantic import TypeAdapter

from crypteo.coinbase import CoinbaseCandleRetriever, CoinbaseRawCandleResponse
from crypteo.coinbase_acquisition import CoinbaseAcquisitionRecord
from crypteo.coinbase_capture import (
    _capture_envelope,
)
from crypteo.coinbase_capture import (
    coinbase_capture_id as coinbase_capture_id,
)
from crypteo.coinbase_dataset import (
    CoinbaseCanonicalDataset,
    CoinbaseHistoricalDataset,
    canonical_dataset_artifacts,
    compose_coinbase_dataset,
)
from crypteo.domain.market import Candle, DatasetManifest, TradingPair

_CANDLES_ADAPTER = TypeAdapter(tuple[Candle, ...])
_DATASET_VERSION_PATTERN = re.compile(r"sha256:([0-9a-f]{64})")


class CoinbaseLocalPersistence:
    """Persist Coinbase raw and canonical artifacts beneath a caller-owned root."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def persist_raw(self, responses: Iterable[CoinbaseRawCandleResponse]) -> None:
        for response in responses:
            artifact_dir = self.raw_artifact_dir(response)
            _write_immutable(artifact_dir / "body.json", response.body)
            _write_immutable(
                artifact_dir / "request.json",
                _capture_metadata_bytes(response),
            )

    def load_dataset(
        self, dataset_version: str, *, acquisition_id: str | None = None
    ) -> CoinbaseCanonicalDataset:
        """Validate/read an explicit canonical version; never fetch or repair.

        Provenance is read only when explicitly identified, since choosing among
        multiple acquisitions of the same content would be ambiguous.
        """
        artifact_dir = self.dataset_artifact_dir(dataset_version)
        for name in ("manifest.json", "candles.json"):
            if not (artifact_dir / name).resolve().is_relative_to(self._root.resolve()):
                raise ValueError("canonical file must remain beneath artifact root")
        manifest, candle_bytes = _read_canonical_artifacts(artifact_dir)
        if manifest.dataset_version != dataset_version:
            raise ValueError("stored canonical version differs from requested version")
        acquisition = None
        if acquisition_id is not None:
            match = _DATASET_VERSION_PATTERN.fullmatch(acquisition_id)
            if match is None:
                raise ValueError("invalid acquisition_id")
            path = (
                self._root
                / "acquisitions"
                / "coinbase"
                / "sha256"
                / match.group(1)
                / "acquisition.json"
            )
            if not path.resolve().is_relative_to(self._root.resolve()):
                raise ValueError("acquisition path must remain beneath artifact root")
            acquisition = CoinbaseAcquisitionRecord.model_validate_json(
                path.read_bytes()
            )
            if (
                acquisition.acquisition_id != acquisition_id
                or acquisition.dataset_version != dataset_version
            ):
                raise ValueError("stored acquisition identity/linkage mismatch")
        return CoinbaseCanonicalDataset(
            candles=_CANDLES_ADAPTER.validate_json(candle_bytes),
            manifest=manifest,
            acquisition=acquisition,
        )

    def persist_dataset(self, dataset: CoinbaseHistoricalDataset) -> DatasetManifest:
        """Validate and persist canonical content, returning the first manifest."""
        artifact_dir = self.dataset_artifact_dir(dataset.manifest.dataset_version)
        manifest, candle_bytes = canonical_dataset_artifacts(
            dataset.candles, dataset.manifest
        )
        if artifact_dir.exists():
            effective_manifest, stored_bytes = _read_canonical_artifacts(artifact_dir)
            if (
                effective_manifest.dataset_version != manifest.dataset_version
                or stored_bytes != candle_bytes
            ):
                raise ValueError(
                    "stored canonical content differs from incoming dataset"
                )
            return effective_manifest
        _write_immutable(
            artifact_dir / "candles.json",
            candle_bytes,
        )
        _write_immutable(
            artifact_dir / "manifest.json",
            manifest.model_dump_json().encode(),
        )
        return manifest

    def persist_acquisition(self, dataset: CoinbaseHistoricalDataset) -> None:
        """Publish acquisition provenance after verifying all referenced artifacts."""
        record = CoinbaseAcquisitionRecord.model_validate(
            dataset.acquisition.model_dump()
        )
        if record.dataset_version != dataset.manifest.dataset_version:
            raise ValueError(
                "acquisition dataset_version must match dataset manifest version"
            )
        expected_capture_ids = tuple(
            sorted(
                {coinbase_capture_id(response) for response in dataset.raw_responses}
            )
        )
        if record.capture_ids != expected_capture_ids:
            raise ValueError("acquisition capture_ids must match dataset raw responses")

        canonical_dir = self.dataset_artifact_dir(record.dataset_version)
        for name in ("candles.json", "manifest.json"):
            if not (canonical_dir / name).is_file():
                raise FileNotFoundError(
                    f"canonical dataset artifact does not exist: {canonical_dir / name}"
                )
        manifest, candle_bytes = canonical_dataset_artifacts(
            dataset.candles, dataset.manifest
        )
        stored_manifest, stored_bytes = _read_canonical_artifacts(canonical_dir)
        if (
            stored_manifest.dataset_version != manifest.dataset_version
            or stored_bytes != candle_bytes
        ):
            raise ValueError("stored canonical content differs from incoming dataset")

        for response in dataset.raw_responses:
            raw_dir = self.raw_artifact_dir(response)
            for name in ("body.json", "request.json"):
                if not (raw_dir / name).is_file():
                    raise FileNotFoundError(
                        f"raw capture artifact does not exist: {raw_dir / name}"
                    )
            if (raw_dir / "body.json").read_bytes() != response.body:
                raise ValueError("stored raw body differs from response bytes")
            if (raw_dir / "request.json").read_bytes() != _capture_metadata_bytes(
                response
            ):
                raise ValueError("stored raw request metadata differs from capture")

        recomposed = compose_coinbase_dataset(
            pair=manifest.pair,
            raw_responses=dataset.raw_responses,
            requested_start=record.requested_start,
            requested_end=record.requested_end,
            created_at=record.created_at,
        )
        recomposed_manifest, recomposed_bytes = canonical_dataset_artifacts(
            recomposed.candles, recomposed.manifest
        )
        # Canonical created_at belongs to the first ingestion, not this acquisition.
        if (
            recomposed_manifest.dataset_version != manifest.dataset_version
            or recomposed_bytes != candle_bytes
        ):
            raise ValueError("raw captures do not compose to the supplied dataset")
        if recomposed.acquisition.canonical_bytes() != record.canonical_bytes():
            raise ValueError("acquisition does not match recomposed capture provenance")

        _write_immutable(
            self.acquisition_artifact_dir(record) / "acquisition.json",
            record.canonical_bytes(),
        )

    def acquisition_artifact_dir(self, record: CoinbaseAcquisitionRecord) -> Path:
        return (
            self._root
            / "acquisitions"
            / "coinbase"
            / "sha256"
            / record.acquisition_id.removeprefix("sha256:")
        )

    def raw_artifact_dir(self, response: CoinbaseRawCandleResponse) -> Path:
        request = response.request

        return (
            self._root
            / "raw"
            / "coinbase"
            / request.pair.value
            / request.timeframe.value
            / "sha256"
            / coinbase_capture_id(response).removeprefix("sha256:")
        )

    def dataset_artifact_dir(self, dataset_version: str) -> Path:
        match = _DATASET_VERSION_PATTERN.fullmatch(dataset_version)
        if match is None:
            raise ValueError(
                "dataset_version must be sha256 followed by 64 lowercase hex characters"
            )
        artifact_dir = self._root / "canonical" / "sha256" / match.group(1)
        if not artifact_dir.resolve().is_relative_to(self._root.resolve()):
            raise ValueError(
                "canonical artifact path must remain beneath artifact root"
            )
        return artifact_dir


def ingest_coinbase_candles(
    *,
    retriever: CoinbaseCandleRetriever,
    artifact_root: Path,
    pair: TradingPair,
    start: datetime,
    end: datetime,
    created_at: datetime,
    acquisition_started_at: datetime,
) -> CoinbaseHistoricalDataset:
    """Retrieve, persist, compose, and persist one Coinbase candle dataset."""
    persistence = CoinbaseLocalPersistence(artifact_root)
    raw_responses = []
    for response in retriever.retrieve(
        pair=pair,
        start=start,
        end=end,
        acquisition_started_at=acquisition_started_at,
    ):
        persistence.persist_raw((response,))
        raw_responses.append(response)
    dataset = compose_coinbase_dataset(
        pair=pair,
        raw_responses=raw_responses,
        created_at=created_at,
        requested_start=start,
        requested_end=end,
    )
    dataset = replace(dataset, manifest=persistence.persist_dataset(dataset))
    persistence.persist_acquisition(dataset)
    return dataset


def _read_canonical_artifacts(artifact_dir: Path) -> tuple[DatasetManifest, bytes]:
    """Read and validate existing canonical artifacts without creating or repairing."""
    manifest = DatasetManifest.model_validate_json(
        (artifact_dir / "manifest.json").read_bytes()
    )
    candles = _CANDLES_ADAPTER.validate_json(
        (artifact_dir / "candles.json").read_bytes()
    )
    return canonical_dataset_artifacts(candles, manifest)


def _capture_metadata_bytes(response: CoinbaseRawCandleResponse) -> bytes:
    return json.dumps(
        _capture_envelope(response), sort_keys=True, separators=(",", ":")
    ).encode()


def _write_immutable(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as artifact:
            artifact.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise FileExistsError(
                f"refusing to overwrite artifact with different content: {path}"
            ) from None
