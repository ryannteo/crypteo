"""Seeded synthetic hourly candles, ingested through the real canonical path.

The candles come from a hash-seeded random walk with occasional sharp drops,
so the lower-band scanner finds candidates. They are served by an in-process
transport in place of Coinbase's endpoint and pass through ``crypteo``'s real
retrieval, raw capture, canonical dataset and acquisition steps. Prices are
exact integers of cents; no binary float is used and no network is reached.
The series is dated in January 2000, before any of these markets existed, so
it cannot be mistaken for real Coinbase data.
"""

import calendar
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from crypteo.coinbase import CoinbaseCandleRetriever
from crypteo.coinbase_dataset import CoinbaseCanonicalDataset
from crypteo.coinbase_persistence import (
    CoinbaseLocalPersistence,
    ingest_coinbase_candles,
)
from crypteo.domain.market import TradingPair

SEED = "crypteo-demo-8"
START = datetime(2000, 1, 3, tzinfo=UTC)
HOURS = 168
END = START + timedelta(hours=HOURS)
H = timedelta(hours=1)
# Starting prices in cents.
OPENING = {
    TradingPair.BTC_USD: 4_000_000,
    TradingPair.ETH_USD: 250_000,
    TradingPair.SOL_USD: 10_000,
}
# One Coinbase row: time, low, high, open, close, volume.
type Row = tuple[int, int, int, int, int, int]


def _draw(pair: TradingPair, hour: int, name: str, below: int) -> int:
    """A deterministic integer in [0, below) for one pair, hour and purpose."""
    text = f"{SEED}/{pair.value}/{hour}/{name}".encode()
    return int.from_bytes(sha256(text).digest()[:8], "big") % below


def synthetic_rows(pair: TradingPair) -> tuple[Row, ...]:
    """Hourly rows from START to END, oldest first, prices and volume in cents.

    Each hour moves the close by up to 0.8% either way; about one hour in
    fifteen drops a further 2-4%, which is what pushes a close below its
    lower Bollinger band. Wicks extend up to 0.5% past the open and close.
    """
    rows: list[Row] = []
    close = OPENING[pair]
    for hour in range(HOURS):
        opened = close
        move = _draw(pair, hour, "move", 161) - 80
        if _draw(pair, hour, "shock", 15) == 0:
            move -= 200 + _draw(pair, hour, "depth", 201)
        close = max(1, opened * (10_000 + move) // 10_000)
        top, bottom = max(opened, close), min(opened, close)
        high = top + top * _draw(pair, hour, "high", 51) // 10_000
        low = max(1, bottom - bottom * _draw(pair, hour, "low", 51) // 10_000)
        volume = 100 + _draw(pair, hour, "volume", 100_000)
        time = calendar.timegm((START + hour * H).utctimetuple())
        rows.append((time, low, high, opened, close, volume))
    return tuple(rows)


def _cents(value: int) -> str:
    """Exact text for a positive integer of cents, whatever the Decimal context."""
    return f"{value // 100}.{value % 100:02d}"


class SyntheticTransport:
    """Answers candle requests from the synthetic series, newest row first.

    It implements ``crypteo.coinbase.HttpTransport`` in-process: the URL is
    only used to pick the pair, and nothing is sent anywhere.
    """

    def __init__(self) -> None:
        self.requests = 0

    def get(self, url: str, params: Mapping[str, str | int]) -> bytes:
        self.requests += 1
        pair = TradingPair(url.rsplit("/", 2)[-2])
        start, end = (
            calendar.timegm(datetime.fromisoformat(str(params[key])).utctimetuple())
            for key in ("start", "end")
        )
        rows = [row for row in reversed(synthetic_rows(pair)) if start <= row[0] < end]
        body = ",".join(
            f"[{row[0]},{','.join(_cents(value) for value in row[1:])}]" for row in rows
        )
        # Validate as JSON here, so a malformed body fails at its source.
        json.loads(f"[{body}]", parse_float=Decimal)
        return f"[{body}]".encode()


def ingest(root: Path) -> list[CoinbaseCanonicalDataset]:
    """Ingest BTC-USD, ETH-USD and SOL-USD under ``root`` and load them back."""
    persistence = CoinbaseLocalPersistence(root)
    retriever = CoinbaseCandleRetriever(SyntheticTransport())
    loaded = []
    for pair in OPENING:
        dataset = ingest_coinbase_candles(
            retriever=retriever,
            artifact_root=root,
            pair=pair,
            start=START,
            end=END,
            created_at=END,
            acquisition_started_at=END,
        )
        if dataset.acquisition is None:
            raise ValueError("ingestion recorded no acquisition")
        loaded.append(
            persistence.load_dataset(
                dataset.manifest.dataset_version,
                acquisition_id=dataset.acquisition.acquisition_id,
            )
        )
    return loaded
