import inspect
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast

import pytest
from pydantic import ValidationError

from crypteo.coinbase import (
    COINBASE_EXCHANGE_API,
    COINBASE_HOURLY_GRANULARITY_SECONDS,
    COINBASE_MAX_CANDLES_PER_REQUEST,
    CoinbaseCandleRequest,
    CoinbaseCandleRetriever,
    build_candle_requests,
)
from crypteo.domain.market import Timeframe, TradingPair

START = datetime(2026, 1, 1, tzinfo=UTC)


class FakeTransport:
    def __init__(self, responses: list[bytes]) -> None:
        self.responses: list[bytes] = list(responses)
        self.calls: list[tuple[str, dict[str, str | int]]] = []

    def get(self, url: str, params: Mapping[str, str | int]) -> bytes:
        self.calls.append((url, dict(params)))
        return self.responses[len(self.calls) - 1]


class FailingSecondTransport:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, url: str, params: Mapping[str, str | int]) -> bytes:
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("transport failed")
        return b"[]"


@pytest.mark.parametrize("pair", list(TradingPair))
def test_builds_coinbase_request_for_each_v1_pair(pair: TradingPair) -> None:
    request = build_candle_requests(
        pair=pair,
        start=START,
        end=START + timedelta(hours=1),
    )[0]

    assert request.url == f"{COINBASE_EXCHANGE_API}/products/{pair.value}/candles"
    assert request.params == {
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-01-01T01:00:00Z",
        "granularity": COINBASE_HOURLY_GRANULARITY_SECONDS,
    }
    assert request.timeframe is Timeframe.ONE_HOUR
    assert request.expected_candle_count == 1


@pytest.mark.parametrize(
    ("hours", "expected_ranges"),
    [
        (1, [(0, 1)]),
        (299, [(0, 299)]),
        (300, [(0, 300)]),
        (301, [(0, 300), (300, 301)]),
        (600, [(0, 300), (300, 600)]),
        (601, [(0, 300), (300, 600), (600, 601)]),
    ],
)
def test_chunks_hourly_ranges_deterministically(
    hours: int, expected_ranges: list[tuple[int, int]]
) -> None:
    requests = build_candle_requests(
        pair=TradingPair.BTC_USD,
        start=START,
        end=START + timedelta(hours=hours),
    )

    assert [(item.start, item.end) for item in requests] == [
        (START + timedelta(hours=start), START + timedelta(hours=end))
        for start, end in expected_ranges
    ]
    assert all(
        item.expected_candle_count <= COINBASE_MAX_CANDLES_PER_REQUEST
        for item in requests
    )


def test_empty_range_produces_no_requests() -> None:
    assert (
        build_candle_requests(
            pair=TradingPair.ETH_USD,
            start=START,
            end=START,
        )
        == ()
    )


@pytest.mark.parametrize(
    "invalid",
    [
        datetime(2026, 1, 1),
        START.astimezone(timezone(timedelta(hours=8))),
    ],
)
@pytest.mark.parametrize("field", ["start", "end"])
def test_builder_rejects_invalid_utc_boundaries(field: str, invalid: Any) -> None:
    values = {"start": START, "end": START + timedelta(hours=1)}
    values[field] = invalid

    with pytest.raises(ValidationError):
        cast(Any, build_candle_requests)(pair=TradingPair.BTC_USD, **values)


def test_rejects_reversed_or_unaligned_ranges() -> None:
    with pytest.raises(ValueError, match="at or before"):
        build_candle_requests(
            pair=TradingPair.BTC_USD,
            start=START + timedelta(hours=1),
            end=START,
        )
    with pytest.raises(ValueError, match="align"):
        build_candle_requests(
            pair=TradingPair.BTC_USD,
            start=START + timedelta(microseconds=1),
            end=START + timedelta(hours=1),
        )
    with pytest.raises(ValueError, match="align"):
        build_candle_requests(
            pair=TradingPair.BTC_USD,
            start=START,
            end=START + timedelta(hours=1, microseconds=1),
        )


def request(**overrides: Any) -> CoinbaseCandleRequest:
    values: dict[str, Any] = {
        "pair": TradingPair.BTC_USD,
        "start": START,
        "end": START + timedelta(hours=1),
    }
    values.update(overrides)
    return cast(CoinbaseCandleRequest, cast(Any, CoinbaseCandleRequest)(**values))


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("start", datetime(2026, 1, 1)),
        ("end", datetime(2026, 1, 1, 1)),
        ("start", START.astimezone(timezone(timedelta(hours=8)))),
        (
            "end",
            (START + timedelta(hours=1)).astimezone(timezone(timedelta(hours=8))),
        ),
        ("start", "2026-01-01T00:00:00Z"),
        ("end", "2026-01-01T01:00:00Z"),
        ("start", 1767225600),
        ("end", 1767229200),
    ],
)
def test_direct_request_requires_strict_utc_boundaries(
    field: str, invalid: Any
) -> None:
    with pytest.raises(ValidationError):
        request(**{field: invalid})


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (START, START, "before end"),
        (START + timedelta(hours=1), START, "before end"),
        (START + timedelta(microseconds=1), START + timedelta(hours=1), "align"),
        (START, START + timedelta(hours=1, microseconds=1), "align"),
        (START, START + timedelta(hours=301), "300"),
    ],
)
def test_direct_request_rejects_invalid_ranges(
    start: datetime, end: datetime, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        request(start=start, end=end)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeframe", Timeframe.ONE_HOUR),
        ("granularity", COINBASE_HOURLY_GRANULARITY_SECONDS),
    ],
)
def test_direct_request_fixed_v1_metadata_cannot_be_overridden(
    field: str, value: object
) -> None:
    with pytest.raises(TypeError, match="unexpected keyword"):
        request(**{field: value})

    valid = request()
    assert valid.timeframe is Timeframe.ONE_HOUR
    assert valid.granularity == COINBASE_HOURLY_GRANULARITY_SECONDS


@pytest.mark.parametrize(
    ("field", "value"),
    [("timeframe", "changed"), ("granularity", 60)],
)
def test_request_fixed_v1_metadata_is_instance_immutable(
    field: str, value: object
) -> None:
    valid = request()

    with pytest.raises(FrozenInstanceError):
        setattr(valid, field, value)


def test_existing_request_metadata_is_isolated_from_class_rebinding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid = request()
    before_params = valid.params

    monkeypatch.setattr(CoinbaseCandleRequest, "timeframe", "changed")
    monkeypatch.setattr(CoinbaseCandleRequest, "granularity", 60)

    assert valid.timeframe is Timeframe.ONE_HOUR
    assert valid.granularity == COINBASE_HOURLY_GRANULARITY_SECONDS
    assert valid.params == before_params


def test_public_retrieval_api_has_no_configurable_timeframe() -> None:
    assert "timeframe" not in inspect.signature(build_candle_requests).parameters
    assert (
        "timeframe"
        not in inspect.signature(CoinbaseCandleRetriever.retrieve).parameters
    )


def test_retriever_preserves_raw_responses_and_request_metadata() -> None:
    raw_responses = [b"[[1767225600,99,105,100,103,10]]", b"[]"]
    transport = FakeTransport(raw_responses)
    retriever = CoinbaseCandleRetriever(transport)

    results = tuple(
        retriever.retrieve(
            pair=TradingPair.SOL_USD,
            start=START,
            end=START + timedelta(hours=301),
            acquisition_started_at=START + timedelta(hours=301),
        )
    )

    assert [item.body for item in results] == raw_responses
    assert [(item.request.start, item.request.end) for item in results] == [
        (START, START + timedelta(hours=300)),
        (START + timedelta(hours=300), START + timedelta(hours=301)),
    ]
    assert transport.calls == [
        (item.request.url, dict(item.request.params)) for item in results
    ]


def test_replaceable_transport_makes_no_live_network_request() -> None:
    transport = FakeTransport([b"[]"])

    result = tuple(
        CoinbaseCandleRetriever(transport).retrieve(
            pair=TradingPair.BTC_USD,
            start=START,
            end=START + timedelta(hours=1),
            acquisition_started_at=START + timedelta(hours=1),
        )
    )

    assert result[0].body == b"[]"
    assert len(transport.calls) == 1


def test_later_chunk_transport_failure_preserves_incremental_result() -> None:
    transport = FailingSecondTransport()
    results = CoinbaseCandleRetriever(transport).retrieve(
        pair=TradingPair.BTC_USD,
        start=START,
        end=START + timedelta(hours=301),
        acquisition_started_at=START + timedelta(hours=301),
    )
    assert transport.calls == 0
    assert next(results).body == b"[]"
    assert transport.calls == 1
    with pytest.raises(RuntimeError, match="transport failed"):
        next(results)
    assert transport.calls == 2


@pytest.mark.parametrize(
    "boundary",
    [
        START + timedelta(hours=300),
        datetime(2026, 2, 1),
        datetime(2026, 2, 1, tzinfo=timezone(timedelta(hours=8))),
        "2026-02-01T00:00:00Z",
        1769904000,
    ],
)
def test_invalid_acquisition_boundary_fails_before_any_transport(
    boundary: Any,
) -> None:
    transport = FakeTransport([b"[]", b"[]"])
    with pytest.raises(ValueError):
        tuple(
            CoinbaseCandleRetriever(transport).retrieve(
                pair=TradingPair.BTC_USD,
                start=START,
                end=START + timedelta(hours=301),
                acquisition_started_at=boundary,
            )
        )
    assert transport.calls == []


def test_all_chunks_share_exact_acquisition_boundary() -> None:
    boundary = START + timedelta(hours=301)
    results = tuple(
        CoinbaseCandleRetriever(FakeTransport([b"[]", b"[]"])).retrieve(
            pair=TradingPair.BTC_USD,
            start=START,
            end=boundary,
            acquisition_started_at=boundary,
        )
    )
    assert len(results) == 2
    assert all(item.acquisition_started_at == boundary for item in results)


@pytest.mark.parametrize(
    "boundary",
    [
        datetime(2026, 2, 1),
        datetime(2026, 2, 1, tzinfo=timezone(timedelta(hours=8))),
        "2026-02-01T00:00:00Z",
        1769904000,
    ],
)
def test_raw_capture_requires_strict_utc_acquisition_provenance(boundary: Any) -> None:
    from crypteo.coinbase import CoinbaseRawCandleResponse

    with pytest.raises(ValidationError):
        CoinbaseRawCandleResponse(
            request=request(),
            body=b"[]",
            acquisition_started_at=boundary,
        )
