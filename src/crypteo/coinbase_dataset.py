"""Compose raw Coinbase responses into a canonical historical dataset."""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any

from crypteo.coinbase import CoinbaseRawCandleResponse
from crypteo.coinbase_normalization import normalize_coinbase_candles
from crypteo.domain.market import (
    Candle,
    DatasetManifest,
    Timeframe,
    TradingPair,
    Venue,
)

COINBASE_DATASET_SOURCE = "coinbase_exchange_candles"


@dataclass(frozen=True, slots=True)
class CoinbaseHistoricalDataset:
    """Canonical candles and manifest alongside their untouched raw inputs."""

    candles: tuple[Candle, ...]
    manifest: DatasetManifest
    raw_responses: tuple[CoinbaseRawCandleResponse, ...]


def compose_coinbase_dataset(
    *,
    pair: TradingPair,
    raw_responses: Iterable[CoinbaseRawCandleResponse],
    created_at: datetime,
) -> CoinbaseHistoricalDataset:
    """Decode and normalize raw Coinbase chunks into one canonical dataset."""
    preserved_responses = tuple(raw_responses)
    acquisition_range = _validate_acquisition_range(preserved_responses, pair)
    normalized_chunks: list[tuple[Candle, ...]] = []
    for response in preserved_responses:
        candles = normalize_coinbase_candles(
            pair=pair,
            rows=_decode_candle_rows(response.body),
        )
        if any(
            not response.request.start <= candle.open_time < response.request.end
            for candle in candles
        ):
            raise ValueError("Coinbase candle falls outside its request range")
        normalized_chunks.append(candles)

    candles = _merge_candles(normalized_chunks)
    if candles:
        start_time = candles[0].open_time
        end_time = candles[-1].close_time
        missing_open_times = _find_missing_open_times(candles)
    elif acquisition_range is not None:
        start_time, end_time = acquisition_range
        missing_open_times = ()
    else:
        start_time = created_at
        end_time = created_at
        missing_open_times = ()

    dataset_version = _build_dataset_version(
        pair=pair,
        start_time=start_time,
        end_time=end_time,
        missing_open_times=missing_open_times,
        candles=candles,
    )
    manifest = DatasetManifest(
        schema_version="1",
        dataset_version=dataset_version,
        source=COINBASE_DATASET_SOURCE,
        venue=Venue.COINBASE,
        pair=pair,
        timeframe=Timeframe.ONE_HOUR,
        start_time=start_time,
        end_time=end_time,
        created_at=created_at,
        row_count=len(candles),
        missing_open_times=missing_open_times,
    )
    return CoinbaseHistoricalDataset(
        candles=candles,
        manifest=manifest,
        raw_responses=preserved_responses,
    )


def _build_dataset_version(
    *,
    pair: TradingPair,
    start_time: datetime,
    end_time: datetime,
    missing_open_times: tuple[datetime, ...],
    candles: tuple[Candle, ...],
) -> str:
    hash_input = {
        "schema_version": "1",
        "source": COINBASE_DATASET_SOURCE,
        "venue": Venue.COINBASE.value,
        "pair": pair.value,
        "timeframe": Timeframe.ONE_HOUR.value,
        "start_time": _canonical_timestamp(start_time),
        "end_time": _canonical_timestamp(end_time),
        "row_count": len(candles),
        "missing_open_times": [
            _canonical_timestamp(value) for value in missing_open_times
        ],
        "candles": [
            {
                "venue": candle.venue.value,
                "pair": candle.pair.value,
                "timeframe": candle.timeframe.value,
                "open_time": _canonical_timestamp(candle.open_time),
                "close_time": _canonical_timestamp(candle.close_time),
                "available_at": _canonical_timestamp(candle.available_at),
                "open": _canonical_decimal(candle.open),
                "high": _canonical_decimal(candle.high),
                "low": _canonical_decimal(candle.low),
                "close": _canonical_decimal(candle.close),
                "volume": _canonical_decimal(candle.volume),
            }
            for candle in candles
        ],
    }
    canonical_json = json.dumps(
        hash_input,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"sha256:{sha256(canonical_json).hexdigest()}"


def _canonical_timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    fixed_point = format(value, "f")
    if "." in fixed_point:
        return fixed_point.rstrip("0").rstrip(".")
    return fixed_point


def _validate_acquisition_range(
    responses: tuple[CoinbaseRawCandleResponse, ...], pair: TradingPair
) -> tuple[datetime, datetime] | None:
    if not responses:
        return None
    if any(response.request.pair is not pair for response in responses):
        raise ValueError("raw Coinbase response pair does not match dataset pair")

    ranges = sorted(
        ((response.request.start, response.request.end) for response in responses),
        key=lambda item: (item[0], item[1]),
    )
    acquisition_start, acquisition_end = ranges[0]
    for range_start, range_end in ranges[1:]:
        if range_start > acquisition_end:
            raise ValueError(
                "raw Coinbase request ranges must form one contiguous interval"
            )
        acquisition_end = max(acquisition_end, range_end)
    return acquisition_start, acquisition_end


def _merge_candles(chunks: Iterable[tuple[Candle, ...]]) -> tuple[Candle, ...]:
    candles_by_identity: dict[
        tuple[Venue, TradingPair, Timeframe, datetime], Candle
    ] = {}
    for chunk in chunks:
        for candle in chunk:
            identity = (
                candle.venue,
                candle.pair,
                candle.timeframe,
                candle.open_time,
            )
            existing = candles_by_identity.get(identity)
            if existing is not None and existing != candle:
                raise ValueError("conflicting Coinbase candles share an identity")
            candles_by_identity[identity] = candle
    return tuple(
        sorted(candles_by_identity.values(), key=lambda candle: candle.open_time)
    )


def _decode_candle_rows(body: bytes) -> list[object]:
    try:
        payload: Any = json.loads(
            body,
            parse_float=Decimal,
            parse_constant=_reject_non_json_constant,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("Coinbase response body must contain valid JSON") from error
    if not isinstance(payload, list):
        raise ValueError("Coinbase response payload must be an array of candle rows")
    return payload


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON numeric constant: {value}")


def _find_missing_open_times(candles: tuple[Candle, ...]) -> tuple[datetime, ...]:
    actual_open_times = {candle.open_time for candle in candles}
    missing: list[datetime] = []
    candidate = candles[0].open_time + Timeframe.ONE_HOUR.duration
    final_open_time = candles[-1].open_time
    while candidate < final_open_time:
        if candidate not in actual_open_times:
            missing.append(candidate)
        candidate += Timeframe.ONE_HOUR.duration
    return tuple(missing)
