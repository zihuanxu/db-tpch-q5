#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import math
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
    "session_id",
    "lifecycle",
    "dataset_load_ms",
    "session_setup_ms",
    "tune_ms",
    "resident_host_bytes",
    "resident_gpu_bytes",
    "resident_pinned_bytes",
    "selected_cpu_ratio",
    "predicted_cpu_ratio",
    "request_index",
]
REQUEST_FIELDS = [*RAW_FIELDS]
REQUEST_FIELDS.insert(REQUEST_FIELDS.index("oracle_status"), "rows_json")
REQUEST_FIELDS.extend(V7_REQUEST_FIELDS)

SETUP_FIELDS = [
    "schema_version",
    "experiment_id",
    "session_id",
    "lifecycle",
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
    "process_elapsed_ms",
    "cpu_peak_rss_bytes",
    "gpu_peak_memory_bytes",
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


@dataclass(frozen=True)
class V7SetupRecord:
    schema_version: int
    experiment_id: str
    session_id: str
    lifecycle: str
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
    predicted_cpu_ratio: float
    process_elapsed_ms: float
    cpu_peak_rss_bytes: int
    gpu_peak_memory_bytes: int
    stdout_log: str
    stderr_log: str
    started_at_utc: str
    finished_at_utc: str

    def as_dict(self) -> dict[str, object]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


@dataclass(frozen=True)
class V7BenchmarkRecord(BenchmarkRecord):
    rows_json: str
    session_id: str
    lifecycle: str
    dataset_load_ms: float
    session_setup_ms: float
    tune_ms: float
    resident_host_bytes: int
    resident_gpu_bytes: int
    resident_pinned_bytes: int
    selected_cpu_ratio: float
    predicted_cpu_ratio: float
    request_index: int

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
) -> dict[str, Any]:
    _validate_fields(raw, expected)
    values: dict[str, Any] = {}
    for name in expected:
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


def _validate_common(values: dict[str, Any]) -> None:
    if values["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    if values["lifecycle"] != "resident":
        raise ValueError("lifecycle must be resident")
    if values["status"] not in STATUSES:
        raise ValueError("status is unsupported")
    if values["threads"] <= 0:
        raise ValueError("threads must be positive")
    if values["gpu_chunk_rows"] < 0:
        raise ValueError("gpu_chunk_rows must be nonnegative")
    for name in ("cpu_ratio", "gpu_ratio", "selected_cpu_ratio", "predicted_cpu_ratio"):
        if values[name] < 0 or values[name] > 1:
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
        if values["return_code"] != 0:
            raise ValueError("ok record requires return_code=0")
        if values["error_class"]:
            raise ValueError("ok record requires empty error_class")
    elif not values["error_class"]:
        raise ValueError("non-ok record requires error_class")


def _validate_nonnegative(values: dict[str, Any], names: set[str]) -> None:
    for name in names:
        if values[name] < 0:
            raise ValueError(f"{name} must be nonnegative")


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
    values = _convert(raw, SETUP_FIELDS, SETUP_INT_FIELDS, SETUP_FLOAT_FIELDS)
    _validate_common(values)
    _validate_nonnegative(
        values,
        (SETUP_INT_FIELDS - {"return_code", "threads", "schema_version"})
        | (SETUP_FLOAT_FIELDS - {"cpu_ratio", "gpu_ratio", "selected_cpu_ratio", "predicted_cpu_ratio"}),
    )
    return V7SetupRecord(**values)


def validate_request(raw: Mapping[str, object]) -> V7BenchmarkRecord:
    values = _convert(raw, REQUEST_FIELDS, REQUEST_INT_FIELDS, REQUEST_FLOAT_FIELDS)
    _validate_common(values)
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
        if values["oracle_status"] not in {"not_run", "passed", "failed"}:
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
