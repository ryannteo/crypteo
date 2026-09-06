import inspect
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

import crypteo.coinbase_dataset as dataset_module
from crypteo.coinbase import CoinbaseCandleRequest, CoinbaseRawCandleResponse
from crypteo.coinbase_dataset import (
    COINBASE_DATASET_SOURCE,
    compose_coinbase_dataset,
)
from crypteo.coinbase_normalization import normalize_coinbase_candles
from crypteo.domain.market import Candle, TradingPair, Venue

START = datetime(2026, 1, 1, tzinfo=UTC)
CREATED_AT = datetime(2026, 2, 1, tzinfo=UTC)


def test_composition_api_does_not_accept_dataset_version() -> None:
    assert (
        "dataset_version" not in inspect.signature(compose_coinbase_dataset).parameters
    )


def row(
    hour: int = 0,
    *,
    close: float = 103.5,
) -> list[int | float]:
    return [
        int((START + timedelta(hours=hour)).timestamp()),
        99.125,
        105.875,
        100.25,
        close,
        12.125,
    ]


def raw_response(
    rows: object,
    *,
    pair: TradingPair = TradingPair.BTC_USD,
    request_start: datetime = START,
    request_end: datetime = START + timedelta(hours=1),
) -> CoinbaseRawCandleResponse:
    return CoinbaseRawCandleResponse(
        request=CoinbaseCandleRequest(
            pair=pair,
            start=request_start,
            end=request_end,
        ),
        body=json.dumps(rows).encode(),
    )


def compose(
    responses: list[CoinbaseRawCandleResponse] | tuple[CoinbaseRawCandleResponse, ...],
    *,
    pair: TradingPair = TradingPair.BTC_USD,
    created_at: datetime = CREATED_AT,
) -> dataset_module.CoinbaseHistoricalDataset:
    return compose_coinbase_dataset(
        pair=pair,
        raw_responses=responses,
        created_at=created_at,
    )


def test_decodes_fractional_json_numbers_without_precision_loss() -> None:
    precise = "103.5000000000000000000000001"
    body = (
        f"[[{int(START.timestamp())},99.125,105.875,100.25,{precise},12.125]]"
    ).encode()
    response = raw_response([])

    result = compose([CoinbaseRawCandleResponse(request=response.request, body=body)])

    assert result.candles[0].close == Decimal(precise)


def test_decodes_ordinary_json_integer_exactly() -> None:
    timestamp = int(START.timestamp())

    result = compose([raw_response([[timestamp, 99, 105, 100, 103, 12]])])

    assert result.candles[0].open_time == START


@pytest.mark.parametrize("body", [b"not json", b"[", b'"unterminated'])
def test_rejects_malformed_json(body: bytes) -> None:
    response = raw_response([])
    malformed = CoinbaseRawCandleResponse(request=response.request, body=body)

    with pytest.raises(ValueError, match="valid JSON"):
        compose([malformed])


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_rejects_nonstandard_non_finite_json_numbers(constant: str) -> None:
    response = raw_response([])
    body = f"[[{int(START.timestamp())},99,105,100,103,{constant}]]".encode()

    with pytest.raises(ValueError, match="invalid JSON numeric constant"):
        compose([CoinbaseRawCandleResponse(request=response.request, body=body)])


@pytest.mark.parametrize("payload", [{}, "rows", 1, None, True])
def test_rejects_non_array_top_level_payload(payload: object) -> None:
    with pytest.raises(ValueError, match="array"):
        compose([raw_response(payload)])


def test_preserves_raw_response_objects_and_body_independently() -> None:
    response = raw_response([row()])
    original_body = response.body
    source = [response]

    result = compose(source)
    source.clear()

    assert result.raw_responses == (response,)
    assert result.raw_responses[0] is response
    assert result.raw_responses[0].body == original_body


def test_reuses_existing_normalizer(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[TradingPair, list[object]]] = []

    def recording_normalizer(
        *, pair: TradingPair, rows: list[object]
    ) -> tuple[Candle, ...]:
        calls.append((pair, rows))
        return normalize_coinbase_candles(pair=pair, rows=rows)

    monkeypatch.setattr(
        "crypteo.coinbase_dataset.normalize_coinbase_candles", recording_normalizer
    )

    first_rows = [row(1), row(0)]
    second_rows = [row(2)]
    compose(
        [
            raw_response(first_rows, request_end=START + timedelta(hours=2)),
            raw_response(
                second_rows,
                request_start=START + timedelta(hours=2),
                request_end=START + timedelta(hours=3),
            ),
        ]
    )

    assert calls == [
        (TradingPair.BTC_USD, first_rows),
        (TradingPair.BTC_USD, second_rows),
    ]


def test_exact_request_start_is_in_range() -> None:
    result = compose([raw_response([row(0)])])

    assert result.candles[0].open_time == START


def test_final_candle_strictly_inside_request_range_is_valid() -> None:
    result = compose([raw_response([row(2)], request_end=START + timedelta(hours=3))])

    assert result.candles[0].open_time == START + timedelta(hours=2)


def test_rejects_candle_before_its_request_start() -> None:
    with pytest.raises(ValueError, match="outside its request range"):
        compose([raw_response([row(-1)])])


def test_rejects_candle_at_exact_request_end() -> None:
    with pytest.raises(ValueError, match="outside its request range"):
        compose([raw_response([row(1)])])


def test_cross_chunk_exact_duplicates_collapse() -> None:
    result = compose([raw_response([row()]), raw_response([row()])])

    assert len(result.candles) == 1
    assert result.manifest.row_count == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_cross_chunk_conflicting_duplicates_fail_in_any_order(reverse: bool) -> None:
    responses = [raw_response([row()]), raw_response([row(close=104.0)])]
    if reverse:
        responses.reverse()

    with pytest.raises(ValueError, match="conflicting"):
        compose(responses)


def test_chunk_order_does_not_change_candles_or_manifest() -> None:
    early = raw_response([row(0), row(1)], request_end=START + timedelta(hours=2))
    late = raw_response(
        [row(2)],
        request_start=START + timedelta(hours=2),
        request_end=START + timedelta(hours=3),
    )

    forward = compose([early, late])
    reverse = compose([late, early])

    assert forward.candles == reverse.candles
    assert forward.manifest == reverse.manifest
    assert [candle.open_time for candle in forward.candles] == [
        START,
        START + timedelta(hours=1),
        START + timedelta(hours=2),
    ]


def test_dataset_version_has_sha256_format() -> None:
    version = compose([raw_response([row()])]).manifest.dataset_version

    assert re.fullmatch(r"sha256:[0-9a-f]{64}", version)


def test_equivalent_reversed_and_rechunked_responses_have_same_version() -> None:
    combined = raw_response([row(0), row(1)], request_end=START + timedelta(hours=2))
    first = raw_response([row(0)])
    second = raw_response(
        [row(1)],
        request_start=START + timedelta(hours=1),
        request_end=START + timedelta(hours=2),
    )

    versions = {
        compose([combined]).manifest.dataset_version,
        compose([first, second]).manifest.dataset_version,
        compose([second, first]).manifest.dataset_version,
    }

    assert len(versions) == 1


def test_created_at_does_not_affect_version_for_same_canonical_dataset() -> None:
    response = raw_response([row()])

    first = compose([response], created_at=CREATED_AT)
    later = compose([response], created_at=CREATED_AT + timedelta(days=1))

    assert first.manifest.created_at != later.manifest.created_at
    assert first.manifest.dataset_version == later.manifest.dataset_version


def test_relevant_candle_content_changes_version() -> None:
    original = compose([raw_response([row(close=103.5)])])
    changed = compose([raw_response([row(close=104.0)])])

    assert original.manifest.dataset_version != changed.manifest.dataset_version


def test_numerically_equal_decimal_spellings_have_same_version() -> None:
    request = raw_response([]).request
    timestamp = int(START.timestamp())
    integer_body = f"[[{timestamp},99,105,100,100,12]]".encode()
    decimal_body = f"[[{timestamp},99.0,105.00,100.0,100.00,12.000]]".encode()

    integer_result = compose(
        [CoinbaseRawCandleResponse(request=request, body=integer_body)]
    )
    decimal_result = compose(
        [CoinbaseRawCandleResponse(request=request, body=decimal_body)]
    )

    assert integer_result.candles == decimal_result.candles
    assert (
        integer_result.manifest.dataset_version
        == decimal_result.manifest.dataset_version
    )


def test_pair_changes_version() -> None:
    bitcoin = compose([raw_response([row()])], pair=TradingPair.BTC_USD)
    ether = compose(
        [raw_response([row()], pair=TradingPair.ETH_USD)],
        pair=TradingPair.ETH_USD,
    )

    assert bitcoin.manifest.dataset_version != ether.manifest.dataset_version


def test_empty_coverage_changes_version() -> None:
    one_hour = compose([raw_response([])])
    two_hours = compose([raw_response([], request_end=START + timedelta(hours=2))])

    assert one_hour.manifest.dataset_version != two_hours.manifest.dataset_version


def test_gap_structure_changes_version() -> None:
    with_gap = compose(
        [raw_response([row(0), row(2)], request_end=START + timedelta(hours=3))]
    )
    without_gap = compose(
        [
            raw_response(
                [row(0), row(1), row(2)],
                request_end=START + timedelta(hours=3),
            )
        ]
    )

    assert with_gap.manifest.missing_open_times != ()
    assert with_gap.manifest.dataset_version != without_gap.manifest.dataset_version


@pytest.mark.parametrize(
    ("hours", "expected_missing"),
    [
        ([0, 2], [1]),
        ([0, 2, 5], [1, 3, 4]),
    ],
)
def test_detects_missing_hourly_opens_without_synthesizing_candles(
    hours: list[int], expected_missing: list[int]
) -> None:
    result = compose(
        [
            raw_response(
                [row(hour) for hour in reversed(hours)],
                request_end=START + timedelta(hours=hours[-1] + 1),
            )
        ]
    )

    assert [candle.open_time for candle in result.candles] == [
        START + timedelta(hours=hour) for hour in hours
    ]
    assert result.manifest.missing_open_times == tuple(
        START + timedelta(hours=hour) for hour in expected_missing
    )
    assert result.manifest.row_count == len(hours)
    assert result.manifest.start_time == START
    assert result.manifest.end_time == START + timedelta(hours=hours[-1] + 1)
    assert result.manifest.row_count + len(result.manifest.missing_open_times) == (
        hours[-1] + 1
    )


@pytest.mark.parametrize("pair", list(TradingPair))
def test_composes_each_v1_pair(pair: TradingPair) -> None:
    result = compose([raw_response([row()], pair=pair)], pair=pair)

    assert result.candles[0].pair is pair
    assert result.manifest.pair is pair
    assert result.manifest.venue is Venue.COINBASE
    assert result.manifest.source == COINBASE_DATASET_SOURCE


def test_rejects_raw_response_for_another_pair() -> None:
    with pytest.raises(ValueError, match="pair"):
        compose(
            [raw_response([row()], pair=TradingPair.ETH_USD)],
            pair=TradingPair.BTC_USD,
        )


def test_propagates_malformed_row_and_normalization_failures() -> None:
    with pytest.raises(ValueError, match="six"):
        compose([raw_response([[1, 2]])])


def test_empty_raw_response_builds_valid_zero_row_manifest() -> None:
    request_end = START + timedelta(hours=3)

    result = compose([raw_response([], request_start=START, request_end=request_end)])

    assert result.candles == ()
    assert result.manifest.row_count == 0
    assert result.manifest.start_time == START
    assert result.manifest.end_time == request_end
    assert result.manifest.missing_open_times == ()


@pytest.mark.parametrize("reverse", [False, True])
def test_contiguous_acquisition_ranges_are_valid_in_any_order(reverse: bool) -> None:
    responses = [
        raw_response([], request_start=START, request_end=START + timedelta(hours=1)),
        raw_response(
            [],
            request_start=START + timedelta(hours=1),
            request_end=START + timedelta(hours=3),
        ),
    ]
    if reverse:
        responses.reverse()

    result = compose(responses)

    assert result.manifest.start_time == START
    assert result.manifest.end_time == START + timedelta(hours=3)


@pytest.mark.parametrize(
    ("ranges", "expected_end_hour"),
    [
        ([(0, 2), (1, 3)], 3),
        ([(0, 2), (0, 2)], 2),
    ],
)
def test_overlapping_and_repeated_acquisition_ranges_are_valid(
    ranges: list[tuple[int, int]],
    expected_end_hour: int,
) -> None:
    responses = [
        raw_response(
            [],
            request_start=START + timedelta(hours=start),
            request_end=START + timedelta(hours=end),
        )
        for start, end in ranges
    ]

    result = compose(responses)

    assert result.manifest.start_time == START
    assert result.manifest.end_time == START + timedelta(hours=expected_end_hour)


def test_disjoint_acquisition_ranges_are_rejected() -> None:
    responses = [
        raw_response([], request_start=START, request_end=START + timedelta(hours=1)),
        raw_response(
            [],
            request_start=START + timedelta(hours=2),
            request_end=START + timedelta(hours=3),
        ),
    ]

    with pytest.raises(ValueError, match="contiguous"):
        compose(responses)


def test_no_raw_responses_builds_empty_manifest_at_supplied_created_time() -> None:
    result = compose([])

    assert result.candles == ()
    assert result.raw_responses == ()
    assert result.manifest.row_count == 0
    assert result.manifest.start_time == CREATED_AT
    assert result.manifest.end_time == CREATED_AT
    assert result.manifest.missing_open_times == ()
