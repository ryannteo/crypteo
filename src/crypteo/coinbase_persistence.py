"""Minimal local persistence for the Coinbase ingestion path."""

import json
import re
from collections.abc import Iterable
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
    CoinbaseHistoricalDataset,
    compose_coinbase_dataset,
)
from crypteo.domain.market import Candle, TradingPair

_CANDLES_ADAPTER = TypeAdapter(tuple[Candle, ...])
_DATASET_VERSION_PATTERN = re.compile(r"sha256:([0-9a-f]{64})")


class CoinbaseLocalPersistence:
    """Persist Coinbase raw and canonical artifacts beneath a caller-owned root."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def persist_raw(self, responses: Iterable[CoinbaseRawCandleResponse]) -> None:
        for response in responses:
            artifact_dir = self.raw_artifact_dir(response)
            metadata = _capture_envelope(response)
            _write_immutable(artifact_dir / "body.json", response.body)
            _write_immutable(
                artifact_dir / "request.json",
                json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode(),
            )

    def persist_dataset(self, dataset: CoinbaseHistoricalDataset) -> None:
        artifact_dir = self.dataset_artifact_dir(dataset.manifest.dataset_version)
        _write_immutable(
            artifact_dir / "candles.json",
            _CANDLES_ADAPTER.dump_json(dataset.candles),
        )
        _write_immutable(
            artifact_dir / "manifest.json",
            dataset.manifest.model_dump_json().encode(),
        )

    def persist_acquisition(self, dataset: CoinbaseHistoricalDataset) -> None:
        """Publish acquisition provenance after verifying all referenced artifacts."""
        record = dataset.acquisition
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
        for response in dataset.raw_responses:
            raw_dir = self.raw_artifact_dir(response)
            for name in ("body.json", "request.json"):
                if not (raw_dir / name).is_file():
                    raise FileNotFoundError(
                        f"raw capture artifact does not exist: {raw_dir / name}"
                    )

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
    persistence.persist_dataset(dataset)
    persistence.persist_acquisition(dataset)
    return dataset


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
