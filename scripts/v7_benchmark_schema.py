#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import uuid
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

try:
    from scripts.benchmark_schema import (
        FLOAT_FIELDS as V5_FLOAT_FIELDS,
        INT_FIELDS as V5_INT_FIELDS,
        RAW_FIELDS,
        STATUSES,
        BenchmarkRecord,
    )
    from scripts.verify_q5_oracle import result_hash_hex
except ModuleNotFoundError:
    from benchmark_schema import (  # type: ignore[no-redef]
        FLOAT_FIELDS as V5_FLOAT_FIELDS,
        INT_FIELDS as V5_INT_FIELDS,
        RAW_FIELDS,
        STATUSES,
        BenchmarkRecord,
    )
    from verify_q5_oracle import result_hash_hex  # type: ignore[no-redef]


SCHEMA_VERSION = 2
V7_REQUEST_FIELDS = [
    "config_id",
    "session_id",
    "lifecycle",
    "ratio_mode",
    "dataset_load_ms",
    "session_setup_ms",
    "tune_ms",
    "resident_host_bytes",
    "resident_gpu_bytes",
    "resident_pinned_bytes",
    "selected_cpu_ratio",
    "predicted_cpu_ratio",
    "request_index",
    "cpu_peak_rss_status",
    "gpu_peak_memory_status",
    "gpu_peak_memory_source",
    "measurement_status_json",
]
REQUEST_FIELDS = [*RAW_FIELDS]
REQUEST_FIELDS.insert(REQUEST_FIELDS.index("oracle_status"), "rows_json")
REQUEST_FIELDS.extend(V7_REQUEST_FIELDS)

SETUP_FIELDS = [
    "schema_version",
    "experiment_id",
    "config_id",
    "session_id",
    "lifecycle",
    "ratio_mode",
    "status",
    "error_class",
    "return_code",
    "engine",
    "scale_factor",
    "dataset_path",
    "region",
    "date",
    "threads",
    "cpu_ratio",
    "gpu_ratio",
    "gpu_chunk_rows",
    "memory_scope",
    "mode_options_json",
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
    "selected_cpu_ratio",
    "predicted_cpu_ratio",
    "hybrid_provenance_status",
    "hybrid_model_version",
    "calibration_rows",
    "cpu_calibration_requests",
    "gpu_calibration_requests",
    "cpu_calibration_ms",
    "gpu_calibration_ms",
    "gpu_kernel_calibration_ms",
    "gpu_fixed_ms",
    "cpu_rows_per_ms",
    "gpu_kernel_rows_per_ms",
    "realized_cpu_ratio",
    "selected_batch_boundary_rows",
    "process_elapsed_ms",
    "cpu_peak_rss_bytes",
    "gpu_peak_memory_bytes",
    "cpu_peak_rss_status",
    "gpu_peak_memory_status",
    "gpu_peak_memory_source",
    "stdout_log",
    "stderr_log",
    "started_at_utc",
    "finished_at_utc",
]

SETUP_INT_FIELDS = {
    "schema_version",
    "return_code",
    "threads",
    "gpu_chunk_rows",
    "resident_host_bytes",
    "resident_gpu_bytes",
    "resident_pinned_bytes",
    "cpu_peak_rss_bytes",
    "gpu_peak_memory_bytes",
    "calibration_rows",
    "cpu_calibration_requests",
    "gpu_calibration_requests",
    "selected_batch_boundary_rows",
}
SETUP_FLOAT_FIELDS = {
    "cpu_ratio",
    "gpu_ratio",
    "dataset_load_ms",
    "session_setup_ms",
    "plan_build_ms",
    "host_staging_ms",
    "allocation_ms",
    "initial_h2d_ms",
    "tune_ms",
    "selected_cpu_ratio",
    "predicted_cpu_ratio",
    "process_elapsed_ms",
    "cpu_calibration_ms",
    "gpu_calibration_ms",
    "gpu_kernel_calibration_ms",
    "gpu_fixed_ms",
    "cpu_rows_per_ms",
    "gpu_kernel_rows_per_ms",
    "realized_cpu_ratio",
}
REQUEST_INT_FIELDS = V5_INT_FIELDS | {
    "resident_host_bytes",
    "resident_gpu_bytes",
    "resident_pinned_bytes",
    "request_index",
}
REQUEST_FLOAT_FIELDS = V5_FLOAT_FIELDS | {
    "dataset_load_ms",
    "session_setup_ms",
    "tune_ms",
    "selected_cpu_ratio",
    "predicted_cpu_ratio",
}
REQUEST_MEASUREMENT_FIELDS = {
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
}
HYBRID_PROVENANCE_FIELDS = {
    "hybrid_model_version",
    "calibration_rows",
    "cpu_calibration_requests",
    "gpu_calibration_requests",
    "cpu_calibration_ms",
    "gpu_calibration_ms",
    "gpu_kernel_calibration_ms",
    "gpu_fixed_ms",
    "cpu_rows_per_ms",
    "gpu_kernel_rows_per_ms",
    "realized_cpu_ratio",
    "selected_batch_boundary_rows",
}
REQUEST_NULLABLE_FIELDS = REQUEST_MEASUREMENT_FIELDS | {
    "gpu_peak_memory_bytes",
    "predicted_cpu_ratio",
}
SETUP_NULLABLE_FIELDS = {
    "gpu_peak_memory_bytes",
    "predicted_cpu_ratio",
    *HYBRID_PROVENANCE_FIELDS,
}


@dataclass(frozen=True)
class V7SetupRecord:
    schema_version: int
    experiment_id: str
    config_id: str
    session_id: str
    lifecycle: str
    ratio_mode: str
    status: str
    error_class: str
    return_code: int
    engine: str
    scale_factor: str
    dataset_path: str
    region: str
    date: str
    threads: int
    cpu_ratio: float
    gpu_ratio: float
    gpu_chunk_rows: int
    memory_scope: str
    mode_options_json: str
    dataset_load_ms: float
    session_setup_ms: float
    plan_build_ms: float
    host_staging_ms: float
    allocation_ms: float
    initial_h2d_ms: float
    tune_ms: float
    resident_host_bytes: int
    resident_gpu_bytes: int
    resident_pinned_bytes: int
    selected_cpu_ratio: float
    predicted_cpu_ratio: float | None
    hybrid_provenance_status: str
    hybrid_model_version: str | None
    calibration_rows: int | None
    cpu_calibration_requests: int | None
    gpu_calibration_requests: int | None
    cpu_calibration_ms: float | None
    gpu_calibration_ms: float | None
    gpu_kernel_calibration_ms: float | None
    gpu_fixed_ms: float | None
    cpu_rows_per_ms: float | None
    gpu_kernel_rows_per_ms: float | None
    realized_cpu_ratio: float | None
    selected_batch_boundary_rows: int | None
    process_elapsed_ms: float
    cpu_peak_rss_bytes: int
    gpu_peak_memory_bytes: int | None
    cpu_peak_rss_status: str
    gpu_peak_memory_status: str
    gpu_peak_memory_source: str
    stdout_log: str
    stderr_log: str
    started_at_utc: str
    finished_at_utc: str

    def as_dict(self) -> dict[str, object]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


@dataclass(frozen=True)
class V7BenchmarkRecord(BenchmarkRecord):
    rows_json: str
    config_id: str
    session_id: str
    lifecycle: str
    ratio_mode: str
    dataset_load_ms: float
    session_setup_ms: float
    tune_ms: float
    resident_host_bytes: int
    resident_gpu_bytes: int
    resident_pinned_bytes: int
    selected_cpu_ratio: float
    predicted_cpu_ratio: float | None
    request_index: int
    cpu_peak_rss_status: str
    gpu_peak_memory_status: str
    gpu_peak_memory_source: str
    measurement_status_json: str

    @property
    def rows(self) -> list[dict[str, object]]:
        return json.loads(self.rows_json)

    def as_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in REQUEST_FIELDS}


def _as_int(name: str, value: object) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        converted = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if str(value).strip() not in {str(converted), f"+{converted}"}:
        raise ValueError(f"{name} must be an integer")
    return converted


def _as_float(name: str, value: object) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(converted):
        raise ValueError(f"{name} must be finite")
    return converted


def _as_bool(name: str, value: object) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _validate_fields(raw: Mapping[str, object], expected: list[str]) -> None:
    missing = [name for name in expected if name not in raw]
    extra = [name for name in raw if name not in expected]
    if missing or extra:
        raise ValueError(f"record fields mismatch missing={missing} extra={extra}")


def _convert(
    raw: Mapping[str, object],
    expected: list[str],
    int_fields: set[str],
    float_fields: set[str],
    nullable_fields: set[str] = frozenset(),
) -> dict[str, Any]:
    _validate_fields(raw, expected)
    values: dict[str, Any] = {}
    for name in expected:
        if name in nullable_fields and (raw[name] is None or str(raw[name]).strip() == ""):
            values[name] = None
            continue
        if name in int_fields:
            values[name] = _as_int(name, raw[name])
        elif name in float_fields:
            values[name] = _as_float(name, raw[name])
        elif name == "is_warmup":
            values[name] = _as_bool(name, raw[name])
        else:
            values[name] = str(raw[name])
    return values


def _validate_utc(name: str, value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")


def _validate_session_id(value: str) -> None:
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise ValueError("session_id must be a UUID") from exc
    if str(parsed) != value:
        raise ValueError("session_id must be a canonical lowercase UUID")


def _validate_log_path(name: str, value: str) -> None:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value != path.as_posix()
        or not path.parts
        or path.parts[0] != "logs"
        or len(path.parts) < 2
        or ".." in path.parts
    ):
        raise ValueError(f"{name} must be bundle-relative under logs/")


def _validate_common(
    values: dict[str, Any], *, allow_ok_process_failure: bool = False
) -> None:
    if values["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    if values["lifecycle"] != "resident":
        raise ValueError("lifecycle must be resident")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", values["config_id"]):
        raise ValueError("config_id must be a lowercase slug")
    if values["ratio_mode"] not in {"fixed", "auto"}:
        raise ValueError("ratio_mode must be fixed or auto")
    if values["status"] not in STATUSES:
        raise ValueError("status is unsupported")
    if values["threads"] <= 0:
        raise ValueError("threads must be positive")
    if values["gpu_chunk_rows"] < 0:
        raise ValueError("gpu_chunk_rows must be nonnegative")
    for name in ("cpu_ratio", "gpu_ratio", "selected_cpu_ratio", "predicted_cpu_ratio"):
        if values[name] is not None and (values[name] < 0 or values[name] > 1):
            raise ValueError(f"{name} must be between 0 and 1")
    if not math.isclose(values["cpu_ratio"] + values["gpu_ratio"], 1.0, abs_tol=1e-9):
        raise ValueError("cpu_ratio and gpu_ratio must sum to one")
    try:
        options = json.loads(values["mode_options_json"])
    except json.JSONDecodeError as exc:
        raise ValueError("mode_options_json must be valid JSON") from exc
    if not isinstance(options, dict):
        raise ValueError("mode_options_json must contain an object")
    _validate_session_id(values["session_id"])
    _validate_log_path("stdout_log", values["stdout_log"])
    _validate_log_path("stderr_log", values["stderr_log"])
    _validate_utc("started_at_utc", values["started_at_utc"])
    _validate_utc("finished_at_utc", values["finished_at_utc"])
    if values["status"] == "ok":
        if values["return_code"] != 0 and not allow_ok_process_failure:
            raise ValueError("ok record requires return_code=0")
        if values["error_class"]:
            raise ValueError("ok record requires empty error_class")
    elif not values["error_class"]:
        raise ValueError("non-ok record requires error_class")


def _validate_nonnegative(values: dict[str, Any], names: set[str]) -> None:
    for name in names:
        if values[name] is not None and values[name] < 0:
            raise ValueError(f"{name} must be nonnegative")


def _validate_peak_measurements(values: dict[str, Any]) -> None:
    if values["cpu_peak_rss_status"] != "measured":
        raise ValueError("cpu_peak_rss_status must be measured")
    gpu_status = values["gpu_peak_memory_status"]
    gpu_bytes = values["gpu_peak_memory_bytes"]
    gpu_source = values["gpu_peak_memory_source"]
    if gpu_status == "measured":
        if gpu_bytes is None or not gpu_source:
            raise ValueError("measured gpu_peak_memory requires bytes and source")
    elif gpu_status == "unavailable":
        if gpu_bytes is not None or gpu_source:
            raise ValueError("unavailable gpu_peak_memory requires null bytes and empty source")
    else:
        raise ValueError("gpu_peak_memory_status must be measured or unavailable")


def _validate_request_measurements(values: dict[str, Any]) -> None:
    try:
        statuses = json.loads(values["measurement_status_json"])
    except json.JSONDecodeError as exc:
        raise ValueError("measurement_status_json must be valid JSON") from exc
    if not isinstance(statuses, dict) or set(statuses) != REQUEST_MEASUREMENT_FIELDS:
        raise ValueError("measurement_status_json fields do not match the measurement contract")
    for name in sorted(REQUEST_MEASUREMENT_FIELDS):
        status = statuses[name]
        value = values[name]
        if status == "measured" and value is None:
            raise ValueError(f"{name} is measured but has a null value")
        if status == "unavailable" and value is not None:
            raise ValueError(f"{name} must be null when unavailable")
        if status not in {"measured", "unavailable"}:
            raise ValueError(f"{name} measurement status is invalid")
    values["measurement_status_json"] = json.dumps(
        statuses, separators=(",", ":"), sort_keys=True
    )


def _validate_hybrid_provenance(values: dict[str, Any]) -> None:
    status = values["hybrid_provenance_status"]
    provenance = {name: values[name] for name in HYBRID_PROVENANCE_FIELDS}
    if status == "unavailable":
        if any(value is not None for value in provenance.values()):
            raise ValueError("unavailable hybrid provenance requires null fields")
        if values["predicted_cpu_ratio"] is not None:
            raise ValueError("unavailable hybrid provenance requires null predicted_cpu_ratio")
        if values["ratio_mode"] == "auto" and values["status"] == "ok":
            raise ValueError("successful hybrid auto setup requires measured provenance")
        return
    if status != "measured":
        raise ValueError("hybrid_provenance_status must be measured or unavailable")
    if values["ratio_mode"] != "auto" or values["engine"] != "hybrid-arrow":
        raise ValueError("measured hybrid provenance requires hybrid-arrow ratio_mode=auto")
    missing = sorted(name for name, value in provenance.items() if value is None)
    if values["predicted_cpu_ratio"] is None:
        missing.append("predicted_cpu_ratio")
    if missing:
        raise ValueError(f"measured hybrid provenance has null fields: {missing}")
    if not values["hybrid_model_version"]:
        raise ValueError("hybrid_model_version must be nonempty")
    if values["calibration_rows"] <= 0:
        raise ValueError("calibration_rows must be positive")
    for name in ("cpu_calibration_requests", "gpu_calibration_requests"):
        if values[name] != 1:
            raise ValueError(f"{name} must be exactly 1")
    for name in ("cpu_calibration_ms", "gpu_kernel_calibration_ms"):
        if values[name] <= 0:
            raise ValueError(f"{name} must be positive")
    for name in ("cpu_rows_per_ms", "gpu_kernel_rows_per_ms"):
        if values[name] <= 0:
            raise ValueError(f"{name} must be positive")
    boundary = values["selected_batch_boundary_rows"]
    rows = values["calibration_rows"]
    if boundary < 0 or boundary > rows:
        raise ValueError("selected batch boundary must be within calibration rows")
    realized = values["realized_cpu_ratio"]
    if realized < 0 or realized > 1:
        raise ValueError("realized_cpu_ratio must be between 0 and 1")
    if not math.isclose(realized, values["selected_cpu_ratio"], abs_tol=1e-9):
        raise ValueError("realized_cpu_ratio must equal selected_cpu_ratio")
    if not math.isclose(realized * rows, boundary, abs_tol=1e-9):
        raise ValueError("hybrid batch boundary does not conserve calibration rows")
    if not math.isclose(
        values["cpu_rows_per_ms"],
        rows / values["cpu_calibration_ms"],
        rel_tol=1e-9,
        abs_tol=1e-9,
    ):
        raise ValueError("cpu_rows_per_ms contradicts calibration")
    if not math.isclose(
        values["gpu_kernel_rows_per_ms"],
        rows / values["gpu_kernel_calibration_ms"],
        rel_tol=1e-9,
        abs_tol=1e-9,
    ):
        raise ValueError("gpu_kernel_rows_per_ms contradicts calibration")
    expected_fixed = max(
        0.0, values["gpu_calibration_ms"] - values["gpu_kernel_calibration_ms"]
    )
    if not math.isclose(values["gpu_fixed_ms"], expected_fixed, abs_tol=1e-9):
        raise ValueError("gpu_fixed_ms contradicts GPU calibration timings")


def _canonical_rows(rows_json: str) -> tuple[list[dict[str, object]], str]:
    try:
        rows = json.loads(rows_json)
    except json.JSONDecodeError as exc:
        raise ValueError("rows_json must be valid JSON") from exc
    if not isinstance(rows, list):
        raise ValueError("rows_json must contain a list")
    seen: set[str] = set()
    exact: list[tuple[str, int]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"nation", "revenue_1e4"}:
            raise ValueError(f"rows_json row {index} must contain nation and revenue_1e4")
        nation = row["nation"]
        revenue = row["revenue_1e4"]
        if not isinstance(nation, str) or not nation:
            raise ValueError(f"rows_json row {index} nation must be nonempty")
        if nation in seen:
            raise ValueError(f"rows_json contains duplicate nation {nation}")
        if isinstance(revenue, bool) or not isinstance(revenue, int):
            raise ValueError(f"rows_json row {index} revenue_1e4 must be an integer")
        seen.add(nation)
        exact.append((nation, revenue))
    canonical = json.dumps(rows, separators=(",", ":"), sort_keys=True)
    return rows, result_hash_hex(exact)


def validate_setup(raw: Mapping[str, object]) -> V7SetupRecord:
    values = _convert(
        raw,
        SETUP_FIELDS,
        SETUP_INT_FIELDS,
        SETUP_FLOAT_FIELDS,
        SETUP_NULLABLE_FIELDS,
    )
    _validate_common(values)
    _validate_peak_measurements(values)
    if values["tune_ms"] > values["session_setup_ms"]:
        raise ValueError("tune_ms must be a subinterval of session_setup_ms")
    _validate_hybrid_provenance(values)
    _validate_nonnegative(
        values,
        (SETUP_INT_FIELDS - {"return_code", "threads", "schema_version"})
        | (SETUP_FLOAT_FIELDS - {"cpu_ratio", "gpu_ratio", "selected_cpu_ratio", "predicted_cpu_ratio"}),
    )
    return V7SetupRecord(**values)


def validate_request(raw: Mapping[str, object]) -> V7BenchmarkRecord:
    values = _convert(
        raw,
        REQUEST_FIELDS,
        REQUEST_INT_FIELDS,
        REQUEST_FLOAT_FIELDS,
        REQUEST_NULLABLE_FIELDS,
    )
    _validate_common(values, allow_ok_process_failure=True)
    _validate_peak_measurements(values)
    _validate_request_measurements(values)
    if values["ratio_mode"] == "fixed" and values["predicted_cpu_ratio"] is not None:
        raise ValueError("fixed ratio request requires unavailable predicted_cpu_ratio")
    if (
        values["ratio_mode"] == "auto"
        and values["status"] == "ok"
        and values["predicted_cpu_ratio"] is None
    ):
        raise ValueError("successful auto request requires predicted_cpu_ratio")
    if values["scenario"] != "resident":
        raise ValueError("scenario must be resident")
    if values["request_index"] < 0:
        raise ValueError("request_index must be nonnegative")
    if values["sample_index"] < -1:
        raise ValueError("sample_index must be at least -1")
    _validate_nonnegative(
        values,
        (REQUEST_INT_FIELDS - {"return_code", "sample_index", "schema_version"})
        | (REQUEST_FLOAT_FIELDS - {"cpu_ratio", "gpu_ratio", "selected_cpu_ratio", "predicted_cpu_ratio"}),
    )
    if values["matched_lineitem_rows"] > values["input_lineitem_rows"]:
        raise ValueError("matched_lineitem_rows exceeds input_lineitem_rows")
    if values["cpu_input_rows"] + values["gpu_input_rows"] != values["input_lineitem_rows"]:
        raise ValueError("cpu_input_rows plus gpu_input_rows must equal input rows")
    try:
        not_applicable = json.loads(values["not_applicable_phases"])
    except json.JSONDecodeError as exc:
        raise ValueError("not_applicable_phases must be valid JSON") from exc
    if not isinstance(not_applicable, list):
        raise ValueError("not_applicable_phases must contain a list")

    rows, derived_hash = _canonical_rows(values["rows_json"])
    values["rows_json"] = json.dumps(rows, separators=(",", ":"), sort_keys=True)
    if values["result_rows"] != len(rows):
        raise ValueError("result_rows must equal the number of exact rows")
    if values["status"] == "ok":
        if values["result_hash"] != derived_hash:
            raise ValueError("result_hash does not match exact rows")
        if values["oracle_status"] not in {
            "not_run",
            "expected_hash_match",
            "passed",
            "failed",
        }:
            raise ValueError("ok record has invalid oracle_status")
    elif values["result_rows"] != 0 or values["result_hash"] or rows:
        raise ValueError("non-ok request must not claim result rows or hash")
    return V7BenchmarkRecord(**values)


def _read_records(path: Path, fields_: list[str], validator: object) -> list[Any]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != fields_:
            raise ValueError(f"CSV header does not match schema_version={SCHEMA_VERSION}")
        return [validator(row) for row in reader]  # type: ignore[operator]


def read_setups(path: Path) -> list[V7SetupRecord]:
    return _read_records(path, SETUP_FIELDS, validate_setup)


def read_requests(path: Path) -> list[V7BenchmarkRecord]:
    return _read_records(path, REQUEST_FIELDS, validate_request)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate MEMQ5 V7 resident records")
    parser.add_argument("kind", choices=["setup", "request"])
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    records = read_setups(args.path) if args.kind == "setup" else read_requests(args.path)
    print(f"ok kind={args.kind} records={len(records)} schema_version={SCHEMA_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
