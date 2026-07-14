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
        "config_id": "gpu-copy",
        "session_id": "00000000-0000-4000-8000-000000000001",
        "lifecycle": "resident",
        "ratio_mode": "fixed",
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
        "measurement_status_json": json.dumps(
            {
                name: "measured"
                for name in (
                    "dataset_load_ms",
                    "session_setup_ms",
                    "plan_build_ms",
                    "host_staging_ms",
                    "allocation_ms",
                    "initial_h2d_ms",
                    "tune_ms",
                    "resident_host_bytes",
                    "resident_gpu_bytes",
                    "resident_pinned_bytes",
                )
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        "selected_cpu_ratio": 0.0,
        "predicted_cpu_ratio": None,
        "hybrid_provenance_status": "unavailable",
        "hybrid_model_version": None,
        "calibration_rows": None,
        "cpu_calibration_requests": None,
        "gpu_calibration_requests": None,
        "cpu_calibration_ms": None,
        "gpu_calibration_ms": None,
        "gpu_kernel_calibration_ms": None,
        "gpu_fixed_ms": None,
        "cpu_rows_per_ms": None,
        "gpu_kernel_rows_per_ms": None,
        "realized_cpu_ratio": None,
        "selected_batch_boundary_rows": None,
        "process_elapsed_ms": 4.0,
        "cpu_peak_rss_bytes": 4096,
        "gpu_peak_memory_bytes": 8192,
        "cpu_peak_rss_status": "measured",
        "gpu_peak_memory_status": "measured",
        "gpu_peak_memory_source": "nvml-process-sum",
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
        "config_id": "gpu-copy",
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
        "load_ms": None,
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
        "cpu_peak_rss_status": "measured",
        "gpu_peak_memory_status": "measured",
        "gpu_peak_memory_source": "nvml-process-sum",
        "measurement_status_json": json.dumps(
            {
                name: "measured"
                for name in (
                    "plan_build_ms",
                    "host_prepare_ms",
                    "h2d_ms",
                    "cpu_scan_ms",
                    "gpu_kernel_ms",
                    "d2h_ms",
                    "overlap_wall_ms",
                    "h2d_bytes",
                    "d2h_bytes",
                    "mapped_remote_read_bytes",
                    "query_total_ms",
                    "throughput_rows_per_second",
                    "dataset_load_ms",
                    "session_setup_ms",
                    "tune_ms",
                    "resident_host_bytes",
                    "resident_gpu_bytes",
                    "resident_pinned_bytes",
                )
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        "throughput_rows_per_second": 3000.0,
        "stdout_log": "logs/gpu-copy.stdout.jsonl",
        "stderr_log": "logs/gpu-copy.stderr.txt",
        "started_at_utc": "2026-07-14T00:00:00Z",
        "finished_at_utc": "2026-07-14T00:00:01Z",
        "session_id": "00000000-0000-4000-8000-000000000001",
        "lifecycle": "resident",
        "ratio_mode": "fixed",
        "dataset_load_ms": 1.0,
        "session_setup_ms": 2.0,
        "tune_ms": 0.0,
        "resident_host_bytes": 1024,
        "resident_gpu_bytes": 2048,
        "resident_pinned_bytes": 512,
        "selected_cpu_ratio": 0.0,
        "predicted_cpu_ratio": None,
        "request_index": 1,
    }
    statuses = json.loads(record["measurement_status_json"])
    statuses["load_ms"] = "unavailable"
    record["measurement_status_json"] = json.dumps(
        statuses, separators=(",", ":"), sort_keys=True
    )
    record.update(overrides)
    return record


def test_setup_and_request_records_accept_strict_resident_values() -> None:
    from scripts.v7_benchmark_schema import validate_request, validate_setup

    setup = validate_setup(valid_setup())
    request = validate_request(valid_request())

    assert setup.session_setup_ms == 2.0
    assert setup.config_id == "gpu-copy"
    assert request.ratio_mode == "fixed"
    assert request.rows == ROWS
    assert request.result_hash == RESULT_HASH


def test_setup_schema_round_trips_complete_hybrid_auto_provenance() -> None:
    from scripts.v7_benchmark_schema import validate_setup

    setup = validate_setup(
        valid_setup(
            config_id="hybrid-auto-t08",
            engine="hybrid-arrow",
            ratio_mode="auto",
            cpu_ratio=0.5,
            gpu_ratio=0.5,
            selected_cpu_ratio=0.5,
            predicted_cpu_ratio=0.45,
            tune_ms=1.5,
            hybrid_provenance_status="measured",
            hybrid_model_version="hybrid-cost-v1-batch-v1",
            calibration_rows=6,
            cpu_calibration_requests=1,
            gpu_calibration_requests=1,
            cpu_calibration_ms=0.6,
            gpu_calibration_ms=0.8,
            gpu_kernel_calibration_ms=0.5,
            gpu_fixed_ms=0.3,
            cpu_rows_per_ms=10.0,
            gpu_kernel_rows_per_ms=12.0,
            realized_cpu_ratio=0.5,
            selected_batch_boundary_rows=3,
        )
    )

    assert setup.hybrid_provenance_status == "measured"
    assert setup.gpu_kernel_rows_per_ms == 12.0
    assert setup.selected_batch_boundary_rows == 3


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("cpu_calibration_requests", 2, "cpu_calibration_requests"),
        ("selected_batch_boundary_rows", 4, "boundary"),
        ("realized_cpu_ratio", 0.25, "realized_cpu_ratio"),
    ],
)
def test_setup_schema_rejects_invalid_hybrid_auto_provenance(
    field: str, value: object, message: str
) -> None:
    from scripts.v7_benchmark_schema import validate_setup

    auto = valid_setup(
        config_id="hybrid-auto-t08",
        engine="hybrid-arrow",
        ratio_mode="auto",
        cpu_ratio=0.5,
        gpu_ratio=0.5,
        selected_cpu_ratio=0.5,
        predicted_cpu_ratio=0.45,
        tune_ms=1.5,
        hybrid_provenance_status="measured",
        hybrid_model_version="hybrid-cost-v1-batch-v1",
        calibration_rows=6,
        cpu_calibration_requests=1,
        gpu_calibration_requests=1,
        cpu_calibration_ms=0.6,
        gpu_calibration_ms=0.8,
        gpu_kernel_calibration_ms=0.5,
        gpu_fixed_ms=0.3,
        cpu_rows_per_ms=10.0,
        gpu_kernel_rows_per_ms=12.0,
        realized_cpu_ratio=0.5,
        selected_batch_boundary_rows=3,
    )
    auto[field] = value

    with pytest.raises(ValueError, match=message):
        validate_setup(auto)


@pytest.mark.parametrize(
    ("factory", "field", "value"),
    [
        (valid_setup, "lifecycle", "cold"),
        (valid_setup, "config_id", "GPU Copy"),
        (valid_setup, "ratio_mode", "dynamic"),
        (valid_setup, "resident_gpu_bytes", -1),
        (valid_setup, "dataset_load_ms", -0.1),
        (valid_request, "lifecycle", "cold"),
        (valid_request, "config_id", "../gpu-copy"),
        (valid_request, "ratio_mode", "profiled"),
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


def test_unavailable_measurements_round_trip_as_null_with_explicit_status() -> None:
    from scripts.v7_benchmark_schema import validate_request, validate_setup

    unavailable_fields = (
        "plan_build_ms",
        "host_prepare_ms",
        "h2d_ms",
        "cpu_scan_ms",
        "gpu_kernel_ms",
        "d2h_ms",
        "overlap_wall_ms",
        "h2d_bytes",
        "d2h_bytes",
        "mapped_remote_read_bytes",
    )
    statuses = json.loads(valid_request()["measurement_status_json"])
    statuses.update({name: "unavailable" for name in unavailable_fields})
    request = validate_request(
        valid_request(
            engine="cudf",
            threads=1,
            mode_options_json='{"framework":"cudf"}',
            plan_build_ms=None,
            host_prepare_ms=None,
            h2d_ms=None,
            cpu_scan_ms=None,
            gpu_kernel_ms=None,
            d2h_ms=None,
            overlap_wall_ms=None,
            h2d_bytes=None,
            d2h_bytes=None,
            mapped_remote_read_bytes=None,
            gpu_peak_memory_bytes=None,
            gpu_peak_memory_status="unavailable",
                gpu_peak_memory_source="",
                measurement_status_json=json.dumps(
                    statuses, separators=(",", ":"), sort_keys=True
                ),
        )
    )
    setup = validate_setup(
        valid_setup(
            engine="cudf",
            threads=1,
            mode_options_json='{"framework":"cudf"}',
            gpu_peak_memory_bytes=None,
            gpu_peak_memory_status="unavailable",
            gpu_peak_memory_source="",
        )
    )

    assert request.gpu_kernel_ms is None
    assert request.h2d_bytes is None
    assert setup.gpu_peak_memory_bytes is None


def test_measurement_status_rejects_zero_claimed_for_unavailable_metric() -> None:
    from scripts.v7_benchmark_schema import validate_request

    statuses = json.loads(valid_request()["measurement_status_json"])
    statuses["gpu_kernel_ms"] = "unavailable"

    with pytest.raises(ValueError, match="gpu_kernel_ms.*unavailable"):
        validate_request(
            valid_request(
                gpu_kernel_ms=0.0,
                measurement_status_json=json.dumps(
                    statuses, separators=(",", ":"), sort_keys=True
                ),
            )
        )


def test_failed_setup_requires_null_metrics_and_unavailable_statuses() -> None:
    from scripts.v7_benchmark_schema import validate_setup

    measurement_fields = {
        "dataset_load_ms",
        "session_setup_ms",
        "plan_build_ms",
        "host_staging_ms",
        "allocation_ms",
        "initial_h2d_ms",
        "tune_ms",
        "resident_host_bytes",
        "resident_gpu_bytes",
        "resident_pinned_bytes",
    }
    unavailable = {name: "unavailable" for name in measurement_fields}
    raw = valid_setup(
        status="error",
        error_class="ERROR_PROCESS_EXIT",
        return_code=3,
        **{name: None for name in measurement_fields},
    )
    raw["measurement_status_json"] = json.dumps(
        unavailable, separators=(",", ":"), sort_keys=True
    )

    setup = validate_setup(raw)

    assert setup.dataset_load_ms is None
    assert setup.resident_host_bytes is None
    assert setup.measurement_statuses == unavailable


def test_failed_request_requires_null_timings_with_unavailable_status() -> None:
    from scripts.v7_benchmark_schema import validate_request

    request_measurements = {
        "load_ms",
        "plan_build_ms",
        "host_prepare_ms",
        "h2d_ms",
        "cpu_scan_ms",
        "gpu_kernel_ms",
        "d2h_ms",
        "overlap_wall_ms",
        "h2d_bytes",
        "d2h_bytes",
        "mapped_remote_read_bytes",
        "query_total_ms",
        "throughput_rows_per_second",
    }
    statuses = json.loads(valid_request()["measurement_status_json"])
    statuses.update({name: "unavailable" for name in request_measurements})
    failed = validate_request(
        valid_request(
            status="error",
            error_class="ERROR_REQUEST_FAILED",
            result_rows=0,
            result_hash="",
            rows_json="[]",
            oracle_status="not_run",
            **{name: None for name in request_measurements},
            measurement_status_json=json.dumps(
                statuses, separators=(",", ":"), sort_keys=True
            ),
        )
    )

    assert failed.query_total_ms is None
    assert failed.measurement_statuses["query_total_ms"] == "unavailable"


def test_failed_request_rejects_measured_request_timing() -> None:
    from scripts.v7_benchmark_schema import validate_request

    with pytest.raises(ValueError, match="failed request.*measurements"):
        validate_request(
            valid_request(
                status="error",
                error_class="ERROR_REQUEST_FAILED",
                result_rows=0,
                result_hash="",
                rows_json="[]",
                oracle_status="not_run",
            )
        )
