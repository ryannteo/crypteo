"""Minimal Coinbase Exchange historical candle retrieval boundary."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, cast
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import TypeAdapter

from crypteo.domain.market import Timeframe, TradingPair
from crypteo.domain.time import UtcDatetime

COINBASE_EXCHANGE_API = "https://api.exchange.coinbase.com"
COINBASE_MAX_CANDLES_PER_REQUEST = 300
COINBASE_HOURLY_GRANULARITY_SECONDS = 3600
COINBASE_USER_AGENT = "crypteo/2.0.0"

_UTC_DATETIME_ADAPTER = TypeAdapter(UtcDatetime)


class HttpTransport(Protocol):
    """Replaceable byte-oriented HTTP GET boundary."""

    def get(self, url: str, params: Mapping[str, str | int]) -> bytes: ...


class CoinbasePublicHttpTransport:
    """Standard-library transport for Coinbase's public endpoint.

    Sends an identifying User-Agent because Coinbase rejects the urllib default.
    """

    def get(self, url: str, params: Mapping[str, str | int]) -> bytes:
        request = Request(
            f"{url}?{urlencode(params)}",
            headers={"User-Agent": COINBASE_USER_AGENT},
            method="GET",
        )
        with urlopen(request) as response:  # noqa: B310
            return cast(bytes, response.read())


@dataclass(frozen=True)
class CoinbaseCandleRequest:
    """Auditable metadata for one bounded Coinbase candle request."""

    pair: TradingPair
    start: datetime
    end: datetime
    timeframe: Timeframe = field(default=Timeframe.ONE_HOUR, init=False)
    granularity: int = field(default=COINBASE_HOURLY_GRANULARITY_SECONDS, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "timeframe", Timeframe.ONE_HOUR)
        object.__setattr__(self, "granularity", COINBASE_HOURLY_GRANULARITY_SECONDS)
        if not isinstance(self.pair, TradingPair):
            raise TypeError("pair must be a supported TradingPair")
        validated_start = _UTC_DATETIME_ADAPTER.validate_python(self.start)
        validated_end = _UTC_DATETIME_ADAPTER.validate_python(self.end)
        object.__setattr__(self, "start", validated_start)
        object.__setattr__(self, "end", validated_end)
        if validated_start >= validated_end:
            raise ValueError("request start must be before end")
        if not _is_hour_aligned(validated_start) or not _is_hour_aligned(validated_end):
            raise ValueError(
                "request boundaries must align to the beginning of an hour"
            )
        if self.expected_candle_count > COINBASE_MAX_CANDLES_PER_REQUEST:
            raise ValueError("request must not exceed 300 hourly candles")

    @property
    def url(self) -> str:
        return f"{COINBASE_EXCHANGE_API}/products/{self.pair.value}/candles"

    @property
    def params(self) -> Mapping[str, str | int]:
        return {
            "start": _format_utc(self.start),
            "end": _format_utc(self.end),
            "granularity": self.granularity,
        }

    @property
    def expected_candle_count(self) -> int:
        return int((self.end - self.start) / self.timeframe.duration)


@dataclass(frozen=True, slots=True)
class CoinbaseRawCandleResponse:
    """Exact raw response body paired with its request metadata."""

    request: CoinbaseCandleRequest
    body: bytes
    acquisition_started_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "acquisition_started_at",
            _UTC_DATETIME_ADAPTER.validate_python(self.acquisition_started_at),
        )


class CoinbaseCandleRetriever:
    """Retrieve bounded raw hourly candles through an injected transport."""

    def __init__(self, transport: HttpTransport) -> None:
        self._transport = transport

    def retrieve(
        self,
        *,
        pair: TradingPair,
        start: datetime,
        end: datetime,
        acquisition_started_at: datetime,
    ) -> Iterator[CoinbaseRawCandleResponse]:
        requests = build_candle_requests(
            pair=pair,
            start=start,
            end=end,
        )
        boundary = _UTC_DATETIME_ADAPTER.validate_python(acquisition_started_at)
        if any(request.end > boundary for request in requests):
            raise ValueError("request end must be at or before acquisition_started_at")
        for request in requests:
            yield CoinbaseRawCandleResponse(
                request=request,
                body=self._transport.get(request.url, request.params),
                acquisition_started_at=boundary,
            )


def build_candle_requests(
    *,
    pair: TradingPair,
    start: datetime,
    end: datetime,
) -> tuple[CoinbaseCandleRequest, ...]:
    """Build deterministic nonoverlapping Coinbase request chunks."""
    validated_start = _UTC_DATETIME_ADAPTER.validate_python(start)
    validated_end = _UTC_DATETIME_ADAPTER.validate_python(end)
    if validated_start > validated_end:
        raise ValueError("start must be at or before end")
    if not _is_hour_aligned(validated_start) or not _is_hour_aligned(validated_end):
        raise ValueError("request boundaries must align to the beginning of an hour")

    chunk_duration = Timeframe.ONE_HOUR.duration * COINBASE_MAX_CANDLES_PER_REQUEST
    requests: list[CoinbaseCandleRequest] = []
    chunk_start = validated_start
    while chunk_start < validated_end:
        chunk_end = min(chunk_start + chunk_duration, validated_end)
        requests.append(
            CoinbaseCandleRequest(
                pair=pair,
                start=chunk_start,
                end=chunk_end,
            )
        )
        chunk_start = chunk_end
    return tuple(requests)


def _format_utc(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _is_hour_aligned(value: datetime) -> bool:
    return value.minute == 0 and value.second == 0 and value.microsecond == 0
