"""Coinbase raw capture identity."""

import json
from datetime import datetime
from hashlib import sha256

from crypteo.coinbase import CoinbaseRawCandleResponse


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
