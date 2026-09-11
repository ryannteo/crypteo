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
    canonical_dataset_artifacts,
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
        acquisition_started_at=CREATED_AT,
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
        requested_start=min((r.request.start for r in responses), default=START),
        requested_end=max((r.request.end for r in responses), default=START),
        created_at=created_at,
    )


def test_decodes_fractional_json_numbers_without_precision_loss() -> None:
    precise = "103.5000000000000000000000001"
    body = (
        f"[[{int(START.timestamp())},99.125,105.875,100.25,{precise},12.125]]"
    ).encode()
    response = raw_response([])

    result = compose(
        [
            CoinbaseRawCandleResponse(
                acquisition_started_at=CREATED_AT, request=response.request, body=body
            )
        ]
    )

    assert result.candles[0].close == Decimal(precise)


def test_decodes_ordinary_json_integer_exactly() -> None:
    timestamp = int(START.timestamp())

    result = compose([raw_response([[timestamp, 99, 105, 100, 103, 12]])])

    assert result.candles[0].open_time == START


@pytest.mark.parametrize("body", [b"not json", b"[", b'"unterminated'])
def test_rejects_malformed_json(body: bytes) -> None:
    response = raw_response([])
    malformed = CoinbaseRawCandleResponse(
        acquisition_started_at=CREATED_AT, request=response.request, body=body
    )

    with pytest.raises(ValueError, match="valid JSON"):
        compose([malformed])


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_rejects_nonstandard_non_finite_json_numbers(constant: str) -> None:
    response = raw_response([])
    body = f"[[{int(START.timestamp())},99,105,100,103,{constant}]]".encode()

    with pytest.raises(ValueError, match="invalid JSON numeric constant"):
        compose(
            [
                CoinbaseRawCandleResponse(
                    acquisition_started_at=CREATED_AT,
                    request=response.request,
                    body=body,
                )
            ]
        )


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


def test_composition_preserves_established_normalization_semantics() -> None:
    first_rows = [row(1), row(0)]
    second_rows = [row(2)]
    result = compose(
        [
            raw_response(first_rows, request_end=START + timedelta(hours=2)),
            raw_response(
                second_rows,
                request_start=START + timedelta(hours=2),
                request_end=START + timedelta(hours=3),
            ),
        ]
    )

    expected = normalize_coinbase_candles(
        pair=TradingPair.BTC_USD, rows=[row(0), row(1), row(2)]
    )
    assert result.candles == expected


def test_exact_request_start_is_in_range() -> None:
    result = compose([raw_response([row(0)])])

    assert result.candles[0].open_time == START


def test_final_candle_strictly_inside_request_range_is_valid() -> None:
    result = compose([raw_response([row(2)], request_end=START + timedelta(hours=3))])

    assert result.candles[0].open_time == START + timedelta(hours=2)


@pytest.mark.parametrize("hour", [-1, 1, 2])
def test_provider_extras_are_ignored(hour: int) -> None:
    response = raw_response([row(hour), row(0)])
    result = compose([response])
    assert [candle.open_time for candle in result.candles] == [START]
    assert result.raw_responses == (response,)


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


def test_schema_v1_canonical_identity_matches_reviewed_golden_fixture() -> None:
    expected_candle_bytes = (
        b'[{"available_at":"2026-01-01T01:00:00.000000Z",'
        b'"close":"103.5","close_time":"2026-01-01T01:00:00.000000Z",'
        b'"high":"105.875","low":"99.125","open":"100.25",'
        b'"open_time":"2026-01-01T00:00:00.000000Z","pair":"BTC-USD",'
        b'"timeframe":"1h","venue":"COINBASE","volume":"12.125"}]'
    )
    expected_dataset_version = (
        "sha256:3a6d37d3f110dab316d05979920a686c7be976a13998c28bee7c1b098f10b044"
    )

    dataset = compose([raw_response([row()])])
    _, candle_bytes = canonical_dataset_artifacts(dataset.candles, dataset.manifest)

    assert candle_bytes == expected_candle_bytes
    assert dataset.manifest.dataset_version == expected_dataset_version


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
        [
            CoinbaseRawCandleResponse(
                acquisition_started_at=CREATED_AT, request=request, body=integer_body
            )
        ]
    )
    decimal_result = compose(
        [
            CoinbaseRawCandleResponse(
                acquisition_started_at=CREATED_AT, request=request, body=decimal_body
            )
        ]
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


def test_empty_requested_coverage_does_not_change_dataset_version() -> None:
    one_hour = compose([raw_response([])])
    two_hours = compose([raw_response([], request_end=START + timedelta(hours=2))])

    assert one_hour.manifest.dataset_version == two_hours.manifest.dataset_version
    assert one_hour.acquisition != two_hours.acquisition


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
    assert result.manifest.start_time is None
    assert result.manifest.end_time is None
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

    assert result.manifest.start_time is None
    assert result.manifest.end_time is None


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

    assert result.acquisition.requested_start == START
    assert result.acquisition.requested_end == START + timedelta(
        hours=expected_end_hour
    )
    assert result.manifest.start_time is None
    assert result.manifest.end_time is None


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
    assert result.manifest.start_time is None
    assert result.manifest.end_time is None
    assert result.manifest.created_at == CREATED_AT
    assert result.manifest.missing_open_times == ()


def test_extras_cannot_fill_another_requests_missing_owned_slot() -> None:
    first = raw_response([row(0), row(1)])
    second = raw_response(
        [row(2)],
        request_start=START + timedelta(hours=1),
        request_end=START + timedelta(hours=3),
    )
    result = compose([first, second])
    assert [candle.open_time for candle in result.candles] == [
        START,
        START + timedelta(hours=2),
    ]
    assert result.manifest.missing_open_times == (START + timedelta(hours=1),)


@pytest.mark.parametrize("hour", [-1, 1, 2])
def test_excluded_rows_do_not_undergo_ohlcv_validation(hour: int) -> None:
    extra: list[object] = [
        int((START + timedelta(hours=hour)).timestamp()),
        None,
        {},
        -1,
        False,
        "bad",
    ]
    assert len(compose([raw_response([extra, row()])]).candles) == 1


@pytest.mark.parametrize(
    "bad_row",
    [
        [0, 1],
        "bad",
        {},
        [None, 1, 2, 1, 1, 1],
        [True, 1, 2, 1, 1, 1],
        [10**100, 1, 2, 1, 1, 1],
        ["1767225600", 1, 2, 1, 1, 1],
    ],
)
def test_unusable_extra_structure_or_timestamp_fails(bad_row: object) -> None:
    with pytest.raises(ValueError):
        compose([raw_response([row(), bad_row])])


def test_retained_rows_still_require_valid_ohlcv() -> None:
    with pytest.raises(ValueError):
        compose([raw_response([[int(START.timestamp()), 1, 2, -1, 1, 1]])])


def test_composition_accepts_exact_completion_and_creation_boundary() -> None:
    from dataclasses import replace

    boundary = START + timedelta(hours=1)
    response = replace(raw_response([row()]), acquisition_started_at=boundary)
    result = compose([response], created_at=boundary)
    assert result.candles[0].close_time == boundary
    assert result.candles[0].available_at == boundary


def test_composition_rejects_request_after_supplied_acquisition_boundary() -> None:
    from dataclasses import replace

    response = replace(raw_response([]), acquisition_started_at=START)
    with pytest.raises(ValueError, match="request end"):
        compose([response])


def test_composition_rejects_creation_before_any_capture() -> None:
    with pytest.raises(ValueError, match="created_at"):
        compose([raw_response([row()])], created_at=CREATED_AT - timedelta(seconds=1))


def test_composition_independently_checks_retained_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candle = normalize_coinbase_candles(pair=TradingPair.BTC_USD, rows=[row()])[0]
    future = candle.model_copy(update={"close_time": CREATED_AT + timedelta(hours=1)})

    def invalid_normalizer(*, pair: TradingPair, rows: object) -> tuple[Candle, ...]:
        return (future,)

    monkeypatch.setattr(
        dataset_module, "normalize_coinbase_candles", invalid_normalizer
    )
    with pytest.raises(ValueError, match="close_time"):
        compose([raw_response([row()])])


def test_composition_revalidates_capture_utc_provenance() -> None:
    response = raw_response([])
    object.__setattr__(response, "acquisition_started_at", datetime(2026, 2, 1))
    with pytest.raises(ValueError, match="UTC"):
        compose([response])


def test_acquisition_gaps_include_leading_internal_and_trailing_slots() -> None:
    result = compose(
        [raw_response([row(1), row(3)], request_end=START + timedelta(hours=5))]
    )
    assert result.acquisition.missing_open_times == tuple(
        START + timedelta(hours=h) for h in (0, 2, 4)
    )
    assert result.manifest.missing_open_times == (START + timedelta(hours=2),)
    assert result.manifest.start_time == START + timedelta(hours=1)
    assert result.manifest.end_time == START + timedelta(hours=4)


def test_all_empty_acquisition_has_all_requested_gaps() -> None:
    result = compose([raw_response([], request_end=START + timedelta(hours=3))])
    assert result.manifest.start_time is result.manifest.end_time is None
    assert result.manifest.missing_open_times == ()
    assert result.acquisition.missing_open_times == tuple(
        START + timedelta(hours=h) for h in range(3)
    )


def test_zero_length_acquisition_preserves_requested_instant() -> None:
    result = compose_coinbase_dataset(
        pair=TradingPair.BTC_USD,
        raw_responses=[],
        created_at=CREATED_AT,
        requested_start=START,
        requested_end=START,
    )
    assert (
        result.acquisition.requested_start == result.acquisition.requested_end == START
    )
    assert result.acquisition.capture_ids == result.acquisition.missing_open_times == ()
    assert result.manifest.start_time is result.manifest.end_time is None


def test_same_canonical_data_different_requests_have_distinct_acquisitions() -> None:
    first = compose([raw_response([row(1)], request_end=START + timedelta(hours=2))])
    second = compose([raw_response([row(1)], request_end=START + timedelta(hours=3))])
    assert first.manifest == second.manifest
    assert first.acquisition.acquisition_id != second.acquisition.acquisition_id


def test_capture_ids_are_exact_unique_and_order_independent() -> None:
    from crypteo.coinbase_capture import coinbase_capture_id

    first = raw_response([row()])
    second = raw_response(
        [row(1)],
        request_start=START + timedelta(hours=1),
        request_end=START + timedelta(hours=2),
    )
    forward = compose([first, second, first])
    reverse = compose([second, first])
    assert forward.acquisition == reverse.acquisition
    assert forward.acquisition.capture_ids == tuple(
        sorted([coinbase_capture_id(first), coinbase_capture_id(second)])
    )
    assert forward.acquisition.dataset_version == forward.manifest.dataset_version


@pytest.mark.parametrize("responses", [[], [raw_response([])]])
def test_composition_rejects_unaccounted_request_ranges(
    responses: list[CoinbaseRawCandleResponse],
) -> None:
    with pytest.raises(ValueError, match="requested interval"):
        compose_coinbase_dataset(
            pair=TradingPair.BTC_USD,
            raw_responses=responses,
            created_at=CREATED_AT,
            requested_start=START,
            requested_end=START + timedelta(hours=2),
        )
