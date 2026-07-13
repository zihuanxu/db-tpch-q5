from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts.benchmark_schema import RAW_FIELDS, read_records, validate_record


def valid_record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "schema_version": 1,
        "experiment_id": "tiny-smoke",
        "run_uuid": "00000000-0000-4000-8000-000000000001",
        "status": "ok",
        "error_class": "",
        "return_code": 0,
        "engine": "cpu-specialized",
        "scenario": "cold",
        "scale_factor": "tiny",
        "region": "ASIA",
        "date": "1994-01-01",
        "sample_index": 0,
        "is_warmup": False,
        "threads": 1,
        "cpu_ratio": 1.0,
        "gpu_ratio": 0.0,
        "gpu_chunk_rows": 0,
        "memory_scope": "process",
        "mode_options_json": "{}",
        "not_applicable_phases": '["gpu"]',
        "result_rows": 2,
        "result_hash": "248d10b6ee352953",
        "oracle_status": "not_run",
        "load_ms": 0.0,
        "plan_build_ms": 1.0,
        "host_prepare_ms": 0.0,
        "h2d_ms": 0.0,
        "cpu_scan_ms": 2.0,
        "gpu_kernel_ms": 0.0,
        "d2h_ms": 0.0,
        "overlap_wall_ms": 0.0,
        "query_total_ms": 3.0,
        "process_elapsed_ms": 4.0,
        "input_lineitem_rows": 6,
        "matched_lineitem_rows": 2,
        "cpu_input_rows": 6,
        "gpu_input_rows": 0,
        "h2d_bytes": 0,
        "d2h_bytes": 0,
        "mapped_remote_read_bytes": 0,
        "cpu_peak_rss_bytes": 1024,
        "gpu_peak_memory_bytes": 0,
        "throughput_rows_per_second": 2000.0,
        "stdout_log": "logs/run.stdout.txt",
        "stderr_log": "logs/run.stderr.txt",
        "started_at_utc": "2026-07-14T00:00:00Z",
        "finished_at_utc": "2026-07-14T00:00:01Z",
    }
    record.update(overrides)
    return record


def test_ok_record_requires_hash_and_metrics() -> None:
    record = validate_record(valid_record())
    assert record.result_hash == "248d10b6ee352953"
    assert record.query_total_ms == 3.0


@pytest.mark.parametrize(
    ("field", "value"),
    [("return_code", 1), ("error_class", "ERROR_IMPOSSIBLE")],
)
def test_ok_record_rejects_failure_metadata(field: str, value: object) -> None:
    with pytest.raises(ValueError, match=field):
        validate_record(valid_record(**{field: value}))


def test_failed_record_keeps_error_and_logs() -> None:
    record = valid_record(
        status="error",
        error_class="ERROR_CUDA_OOM",
        return_code=1,
        result_rows=0,
        result_hash="",
        oracle_status="not_run",
    )
    assert validate_record(record).error_class == "ERROR_CUDA_OOM"


@pytest.mark.parametrize(
    ("field", "value"),
    [("h2d_bytes", -1), ("query_total_ms", -0.1), ("cpu_ratio", 1.1)],
)
def test_nonnegative_units_and_ratios(field: str, value: object) -> None:
    with pytest.raises(ValueError, match=field):
        validate_record(valid_record(**{field: value}))


def test_ratios_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match="ratios"):
        validate_record(valid_record(cpu_ratio=0.5, gpu_ratio=0.4))


def test_csv_round_trip_and_exact_field_order(tmp_path: Path) -> None:
    path = tmp_path / "raw.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RAW_FIELDS)
        writer.writeheader()
        writer.writerow(valid_record())
    records = read_records(path)
    assert len(records) == 1
    assert records[0].input_lineitem_rows == 6
