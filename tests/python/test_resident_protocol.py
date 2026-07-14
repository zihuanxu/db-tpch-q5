from __future__ import annotations

import json

import pytest

from scripts.verify_q5_oracle import result_hash_hex


ROWS = [
    {"nation": "JAPAN", "revenue_1e4": 1900000},
    {"nation": "INDIA", "revenue_1e4": 900000},
]
RESULT_HASH = result_hash_hex(
    [(row["nation"], row["revenue_1e4"]) for row in ROWS]
)
SESSION_ID = "00000000-0000-4000-8000-000000000001"


def setup_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "record_type": "session_setup",
        "session_id": SESSION_ID,
        "lifecycle": "resident",
        "status": "ok",
        "error_class": "",
        "engine": "gpu-copy",
        "dataset": "data/tpch_sf1_arrow",
        "region": "ASIA",
        "date": "1994-01-01",
        "threads": 1,
        "warmup": 1,
        "repeat": 2,
        "dataset_load_ms": 1.0,
        "session_setup_ms": 2.0,
        "plan_build_ms": 0.5,
        "host_staging_ms": 0.25,
        "allocation_ms": 0.75,
        "initial_h2d_ms": 0.5,
        "tune_ms": 0.0,
        "resident_host_bytes": 1024,
        "resident_gpu_bytes": 2048,
        "resident_pinned_bytes": 512,
        "selected_cpu_ratio": 0.0,
        "predicted_cpu_ratio": 0.0,
    }
    record.update(overrides)
    return record


def request_record(index: int, **overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "record_type": "request",
        "session_id": SESSION_ID,
        "lifecycle": "resident",
        "status": "ok",
        "error_class": "",
        "request_index": index,
        "is_warmup": index == 0,
        "selected_cpu_ratio": 0.0,
        "result_rows": len(ROWS),
        "result_hash": RESULT_HASH,
        "rows": ROWS,
        "build_ms": 0.0,
        "h2d_ms": 0.0,
        "kernel_ms": 2.0,
        "d2h_ms": 0.1,
        "scan_ms": 0.0,
        "query_total_ms": 2.2,
        "cpu_ms": 0.0,
        "gpu_ms": 2.0,
        "overlap_ms": 0.0,
        "input_lineitem_rows": 6,
        "matched_lineitem_rows": 2,
        "cpu_input_rows": 0,
        "gpu_input_rows": 6,
        "h2d_bytes": 0,
        "d2h_bytes": 64,
        "mapped_remote_read_bytes": 0,
    }
    record.update(overrides)
    return record


def jsonl(records: list[dict[str, object]]) -> str:
    return "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)


def valid_jsonl() -> str:
    return jsonl([setup_record(), *(request_record(index) for index in range(3))])


def test_parse_current_cpp_jsonl_splits_warmup_and_measured_requests() -> None:
    from scripts.resident_protocol import parse_resident_jsonl

    session = parse_resident_jsonl(
        valid_jsonl(), warmup=1, repeat=2, expected_hash=RESULT_HASH
    )

    assert session.session_id == SESSION_ID
    assert session.setup["engine"] == "gpu-copy"
    assert [row["request_index"] for row in session.warmups] == [0]
    assert [row["request_index"] for row in session.measured] == [1, 2]
    assert session.rows == ROWS


def test_parse_current_cudf_jsonl_allows_absent_cpp_only_phase_fields() -> None:
    from scripts.resident_protocol import parse_resident_jsonl

    setup = setup_record(engine="cudf")
    for field in (
        "threads",
        "warmup",
        "repeat",
        "plan_build_ms",
        "host_staging_ms",
        "allocation_ms",
        "initial_h2d_ms",
    ):
        setup.pop(field)
    requests = []
    for index in range(3):
        request = request_record(index)
        for field in (
            "build_ms",
            "h2d_ms",
            "kernel_ms",
            "d2h_ms",
            "scan_ms",
            "cpu_ms",
            "gpu_ms",
            "overlap_ms",
        ):
            request.pop(field)
        requests.append(request)

    session = parse_resident_jsonl(
        jsonl([setup, *requests]), warmup=1, repeat=2, expected_hash=RESULT_HASH
    )

    assert session.setup["engine"] == "cudf"
    assert len(session.measured) == 2


@pytest.mark.parametrize(
    ("records", "message"),
    [
        ([request_record(0), request_record(1), request_record(2)], "one session_setup"),
        (
            [setup_record(), setup_record(), request_record(0), request_record(1), request_record(2)],
            "one session_setup",
        ),
        ([setup_record(), request_record(0), request_record(2)], "request count"),
        (
            [setup_record(), request_record(0), request_record(0), request_record(2)],
            "request_index",
        ),
        (
            [
                setup_record(),
                request_record(0),
                request_record(1, is_warmup=True),
                request_record(2),
            ],
            "is_warmup",
        ),
        (
            [
                setup_record(),
                request_record(0),
                request_record(1, session_id="00000000-0000-4000-8000-000000000002"),
                request_record(2),
            ],
            "session_id",
        ),
    ],
)
def test_protocol_rejects_missing_duplicate_or_noncontiguous_records(
    records: list[dict[str, object]], message: str
) -> None:
    from scripts.resident_protocol import parse_resident_jsonl

    with pytest.raises(ValueError, match=message):
        parse_resident_jsonl(
            jsonl(records), warmup=1, repeat=2, expected_hash=RESULT_HASH
        )


def test_protocol_rejects_stable_claimed_hash_with_changed_rows() -> None:
    from scripts.resident_protocol import parse_resident_jsonl

    changed = [*ROWS[:-1], {"nation": "INDIA", "revenue_1e4": 899999}]
    records = [
        setup_record(),
        request_record(0),
        request_record(1, rows=changed),
        request_record(2),
    ]

    with pytest.raises(ValueError, match="result_hash.*rows"):
        parse_resident_jsonl(
            jsonl(records), warmup=1, repeat=2, expected_hash=RESULT_HASH
        )


def test_protocol_rejects_hash_and_exact_row_drift_together() -> None:
    from scripts.resident_protocol import parse_resident_jsonl

    changed = [*ROWS[:-1], {"nation": "INDIA", "revenue_1e4": 899999}]
    changed_hash = result_hash_hex(
        [(row["nation"], row["revenue_1e4"]) for row in changed]
    )
    records = [
        setup_record(),
        request_record(0),
        request_record(1, rows=changed, result_hash=changed_hash),
        request_record(2),
    ]

    with pytest.raises(ValueError, match="exact rows changed"):
        parse_resident_jsonl(jsonl(records), warmup=1, repeat=2)


@pytest.mark.parametrize(
    ("target", "field", "value"),
    [
        ("setup", "lifecycle", "cold"),
        ("setup", "session_setup_ms", -0.1),
        ("setup", "resident_gpu_bytes", -1),
        ("request", "lifecycle", "cold"),
        ("request", "query_total_ms", -0.1),
        ("request", "h2d_bytes", -1),
    ],
)
def test_protocol_rejects_cold_lifecycle_and_negative_metrics(
    target: str, field: str, value: object
) -> None:
    from scripts.resident_protocol import parse_resident_jsonl

    setup = setup_record(**({field: value} if target == "setup" else {}))
    requests = [
        request_record(index, **({field: value} if target == "request" and index == 1 else {}))
        for index in range(3)
    ]

    with pytest.raises(ValueError, match=field):
        parse_resident_jsonl(
            jsonl([setup, *requests]), warmup=1, repeat=2, expected_hash=RESULT_HASH
        )


def test_protocol_rejects_malformed_jsonl_without_ignoring_the_line() -> None:
    from scripts.resident_protocol import parse_resident_jsonl

    with pytest.raises(ValueError, match="JSONL line 2"):
        parse_resident_jsonl(
            json.dumps(setup_record()) + "\nnot-json\n",
            warmup=0,
            repeat=1,
        )
