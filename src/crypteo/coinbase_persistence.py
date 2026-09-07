"""Minimal local persistence for the Coinbase ingestion path."""

import json
import re
from collections.abc import Iterable
from datetime import datetime
from hashlib import sha256
from pathlib import Path

from pydantic import TypeAdapter

from crypteo.coinbase import CoinbaseCandleRetriever, CoinbaseRawCandleResponse
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
    )
    persistence.persist_dataset(dataset)
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


def _utc_text(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _capture_envelope(response: CoinbaseRawCandleResponse) -> dict[str, object]:
    request = response.request
    return {
        "capture_format_version": "1",
        "request": {
            "pair": request.pair.value,
            "timeframe": request.timeframe.value,
            "granularity": request.granularity,
            "start": _utc_text(request.start),
            "end": _utc_text(request.end),
            "url": request.url,
            "params": dict(request.params),
        },
        "acquisition_started_at": _utc_text(response.acquisition_started_at),
        "body_sha256": sha256(response.body).hexdigest(),
    }


def coinbase_capture_id(response: CoinbaseRawCandleResponse) -> str:
    """Hash canonical capture provenance and the digest of the unchanged body."""
    envelope = json.dumps(
        _capture_envelope(response), sort_keys=True, separators=(",", ":")
    ).encode()
    return f"sha256:{sha256(envelope).hexdigest()}"
