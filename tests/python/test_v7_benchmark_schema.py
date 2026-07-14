from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts.verify_q5_oracle import result_hash_hex


ROWS = [
    {"nation": "JAPAN", "revenue_1e4": 1900000},
    {"nation": "INDIA", "revenue_1e4": 900000},
]
RESULT_HASH = result_hash_hex(
    [(row["nation"], row["revenue_1e4"]) for row in ROWS]
)


def valid_setup(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "schema_version": 2,
        "experiment_id": "v7-resident-test",
        "session_id": "00000000-0000-4000-8000-000000000001",
        "lifecycle": "resident",
        "status": "ok",
        "error_class": "",
        "return_code": 0,
        "engine": "gpu-copy",
        "scale_factor": "1",
        "dataset_path": "data/tpch_sf1_arrow",
        "region": "ASIA",
        "date": "1994-01-01",
        "threads": 1,
        "cpu_ratio": 0.0,
        "gpu_ratio": 1.0,
        "gpu_chunk_rows": 0,
        "memory_scope": "process_tree",
        "mode_options_json": "{}",
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
        "process_elapsed_ms": 4.0,
        "cpu_peak_rss_bytes": 4096,
        "gpu_peak_memory_bytes": 8192,
        "stdout_log": "logs/gpu-copy.stdout.jsonl",
        "stderr_log": "logs/gpu-copy.stderr.txt",
        "started_at_utc": "2026-07-14T00:00:00Z",
        "finished_at_utc": "2026-07-14T00:00:01Z",
    }
    record.update(overrides)
    return record


def valid_request(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "schema_version": 2,
        "experiment_id": "v7-resident-test",
        "run_uuid": "00000000-0000-4000-8000-000000000002",
        "status": "ok",
        "error_class": "",
        "return_code": 0,
        "engine": "gpu-copy",
        "scenario": "resident",
        "scale_factor": "1",
        "region": "ASIA",
        "date": "1994-01-01",
        "sample_index": 0,
        "is_warmup": False,
        "threads": 1,
        "cpu_ratio": 0.0,
        "gpu_ratio": 1.0,
        "gpu_chunk_rows": 0,
        "memory_scope": "process_tree",
        "mode_options_json": "{}",
        "not_applicable_phases": "[]",
        "result_rows": 2,
        "result_hash": RESULT_HASH,
        "rows_json": json.dumps(ROWS, separators=(",", ":"), sort_keys=True),
        "oracle_status": "passed",
        "load_ms": 0.0,
        "plan_build_ms": 0.0,
        "host_prepare_ms": 0.0,
        "h2d_ms": 0.0,
        "cpu_scan_ms": 0.0,
        "gpu_kernel_ms": 2.0,
        "d2h_ms": 0.1,
        "overlap_wall_ms": 0.0,
        "query_total_ms": 2.2,
        "process_elapsed_ms": 5.0,
        "input_lineitem_rows": 6,
        "matched_lineitem_rows": 2,
        "cpu_input_rows": 0,
        "gpu_input_rows": 6,
        "h2d_bytes": 0,
        "d2h_bytes": 64,
        "mapped_remote_read_bytes": 0,
        "cpu_peak_rss_bytes": 4096,
        "gpu_peak_memory_bytes": 8192,
        "throughput_rows_per_second": 3000.0,
        "stdout_log": "logs/gpu-copy.stdout.jsonl",
        "stderr_log": "logs/gpu-copy.stderr.txt",
        "started_at_utc": "2026-07-14T00:00:00Z",
        "finished_at_utc": "2026-07-14T00:00:01Z",
        "session_id": "00000000-0000-4000-8000-000000000001",
        "lifecycle": "resident",
        "dataset_load_ms": 1.0,
        "session_setup_ms": 2.0,
        "tune_ms": 0.0,
        "resident_host_bytes": 1024,
        "resident_gpu_bytes": 2048,
        "resident_pinned_bytes": 512,
        "selected_cpu_ratio": 0.0,
        "predicted_cpu_ratio": 0.0,
        "request_index": 1,
    }
    record.update(overrides)
    return record


def test_setup_and_request_records_accept_strict_resident_values() -> None:
    from scripts.v7_benchmark_schema import validate_request, validate_setup

    setup = validate_setup(valid_setup())
    request = validate_request(valid_request())

    assert setup.session_setup_ms == 2.0
    assert request.rows == ROWS
    assert request.result_hash == RESULT_HASH


@pytest.mark.parametrize(
    ("factory", "field", "value"),
    [
        (valid_setup, "lifecycle", "cold"),
        (valid_setup, "resident_gpu_bytes", -1),
        (valid_setup, "dataset_load_ms", -0.1),
        (valid_request, "lifecycle", "cold"),
        (valid_request, "request_index", -1),
        (valid_request, "query_total_ms", -0.1),
        (valid_request, "d2h_bytes", -1),
    ],
)
def test_schema_rejects_nonresident_or_negative_values(
    factory: object, field: str, value: object
) -> None:
    from scripts.v7_benchmark_schema import validate_request, validate_setup

    validator = validate_setup if factory is valid_setup else validate_request
    with pytest.raises(ValueError, match=field):
        validator(factory(**{field: value}))  # type: ignore[operator]


@pytest.mark.parametrize("field", ["stdout_log", "stderr_log"])
@pytest.mark.parametrize("value", ["/tmp/output.log", "../output.log", "output.log"])
def test_logs_must_be_bundle_relative_under_logs(field: str, value: str) -> None:
    from scripts.v7_benchmark_schema import validate_request, validate_setup

    with pytest.raises(ValueError, match=field):
        validate_setup(valid_setup(**{field: value}))
    with pytest.raises(ValueError, match=field):
        validate_request(valid_request(**{field: value}))


def test_request_recomputes_hash_from_exact_rows() -> None:
    from scripts.v7_benchmark_schema import validate_request

    changed = [*ROWS[:-1], {"nation": "INDIA", "revenue_1e4": 899999}]
    with pytest.raises(ValueError, match="result_hash.*rows"):
        validate_request(
            valid_request(
                rows_json=json.dumps(changed, separators=(",", ":"), sort_keys=True)
            )
        )


def test_request_requires_result_rows_to_match_exact_rows() -> None:
    from scripts.v7_benchmark_schema import validate_request

    with pytest.raises(ValueError, match="result_rows"):
        validate_request(valid_request(result_rows=3))


def test_request_csv_round_trip_requires_exact_header(tmp_path: Path) -> None:
    from scripts.v7_benchmark_schema import REQUEST_FIELDS, read_requests

    path = tmp_path / "raw.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REQUEST_FIELDS)
        writer.writeheader()
        writer.writerow(valid_request())

    records = read_requests(path)

    assert len(records) == 1
    assert records[0].request_index == 1
    assert records[0].rows == ROWS
