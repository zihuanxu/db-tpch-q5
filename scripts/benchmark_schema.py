#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


RAW_FIELDS = [
    "schema_version", "experiment_id", "run_uuid", "status", "error_class",
    "return_code", "engine", "scenario", "scale_factor", "region", "date",
    "sample_index", "is_warmup", "threads", "cpu_ratio", "gpu_ratio",
    "gpu_chunk_rows", "memory_scope", "mode_options_json",
    "not_applicable_phases", "result_rows", "result_hash", "oracle_status",
    "load_ms", "plan_build_ms", "host_prepare_ms", "h2d_ms", "cpu_scan_ms",
    "gpu_kernel_ms", "d2h_ms", "overlap_wall_ms", "query_total_ms",
    "process_elapsed_ms", "input_lineitem_rows", "matched_lineitem_rows",
    "cpu_input_rows", "gpu_input_rows", "h2d_bytes", "d2h_bytes",
    "mapped_remote_read_bytes", "cpu_peak_rss_bytes",
    "gpu_peak_memory_bytes", "throughput_rows_per_second", "stdout_log",
    "stderr_log", "started_at_utc", "finished_at_utc",
]

STATUSES = {"ok", "error", "timeout", "oom", "skipped_no_gpu"}
HASH_RE = re.compile(r"^[0-9a-f]{16}$")


@dataclass(frozen=True)
class BenchmarkRecord:
    schema_version: int
    experiment_id: str
    run_uuid: str
    status: str
    error_class: str
    return_code: int
    engine: str
    scenario: str
    scale_factor: str
    region: str
    date: str
    sample_index: int
    is_warmup: bool
    threads: int
    cpu_ratio: float
    gpu_ratio: float
    gpu_chunk_rows: int
    memory_scope: str
    mode_options_json: str
    not_applicable_phases: str
    result_rows: int
    result_hash: str
    oracle_status: str
    load_ms: float
    plan_build_ms: float
    host_prepare_ms: float
    h2d_ms: float
    cpu_scan_ms: float
    gpu_kernel_ms: float
    d2h_ms: float
    overlap_wall_ms: float
    query_total_ms: float
    process_elapsed_ms: float
    input_lineitem_rows: int
    matched_lineitem_rows: int
    cpu_input_rows: int
    gpu_input_rows: int
    h2d_bytes: int
    d2h_bytes: int
    mapped_remote_read_bytes: int
    cpu_peak_rss_bytes: int
    gpu_peak_memory_bytes: int
    throughput_rows_per_second: float
    stdout_log: str
    stderr_log: str
    started_at_utc: str
    finished_at_utc: str

    def as_dict(self) -> dict[str, object]:
        return {field.name: getattr(self, field.name) for field in fields(self)}


INT_FIELDS = {
    "schema_version", "return_code", "sample_index", "threads",
    "gpu_chunk_rows", "result_rows", "input_lineitem_rows",
    "matched_lineitem_rows", "cpu_input_rows", "gpu_input_rows", "h2d_bytes",
    "d2h_bytes", "mapped_remote_read_bytes", "cpu_peak_rss_bytes",
    "gpu_peak_memory_bytes",
}
FLOAT_FIELDS = {
    "cpu_ratio", "gpu_ratio", "load_ms", "plan_build_ms", "host_prepare_ms",
    "h2d_ms", "cpu_scan_ms", "gpu_kernel_ms", "d2h_ms", "overlap_wall_ms",
    "query_total_ms", "process_elapsed_ms", "throughput_rows_per_second",
}
NONNEGATIVE_INT_FIELDS = INT_FIELDS - {"return_code", "sample_index"}
NONNEGATIVE_FLOAT_FIELDS = FLOAT_FIELDS


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


def _validate_json(name: str, value: str, expected_type: type) -> None:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be valid JSON") from exc
    if not isinstance(parsed, expected_type):
        raise ValueError(f"{name} has the wrong JSON type")


def _validate_utc(name: str, value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")


def validate_record(raw: Mapping[str, object]) -> BenchmarkRecord:
    missing = [name for name in RAW_FIELDS if name not in raw]
    extra = [name for name in raw if name not in RAW_FIELDS]
    if missing or extra:
        raise ValueError(f"record fields mismatch missing={missing} extra={extra}")

    values: dict[str, Any] = {}
    for name in RAW_FIELDS:
        value = raw[name]
        if name in INT_FIELDS:
            values[name] = _as_int(name, value)
        elif name in FLOAT_FIELDS:
            values[name] = _as_float(name, value)
        elif name == "is_warmup":
            values[name] = _as_bool(name, value)
        else:
            values[name] = str(value)

    if values["schema_version"] != 1:
        raise ValueError("schema_version must be 1")
    if values["status"] not in STATUSES:
        raise ValueError("status is unsupported")
    if values["scenario"] not in {"cold", "resident"}:
        raise ValueError("scenario must be cold or resident")
    if values["threads"] <= 0:
        raise ValueError("threads must be positive")
    if values["sample_index"] < -1:
        raise ValueError("sample_index must be at least -1")
    for name in NONNEGATIVE_INT_FIELDS:
        if values[name] < 0:
            raise ValueError(f"{name} must be nonnegative")
    for name in NONNEGATIVE_FLOAT_FIELDS:
        if values[name] < 0:
            raise ValueError(f"{name} must be nonnegative")
    for name in ("cpu_ratio", "gpu_ratio"):
        if values[name] > 1:
            raise ValueError(f"{name} must be between 0 and 1")
    if not math.isclose(
        values["cpu_ratio"] + values["gpu_ratio"], 1.0, abs_tol=1e-9
    ):
        raise ValueError("cpu/gpu ratios must sum to one")
    if values["matched_lineitem_rows"] > values["input_lineitem_rows"]:
        raise ValueError("matched_lineitem_rows exceeds input_lineitem_rows")
    if (
        values["cpu_input_rows"] + values["gpu_input_rows"]
        != values["input_lineitem_rows"]
    ):
        raise ValueError("cpu_input_rows plus gpu_input_rows must equal input rows")

    _validate_json("mode_options_json", values["mode_options_json"], dict)
    _validate_json("not_applicable_phases", values["not_applicable_phases"], list)
    _validate_utc("started_at_utc", values["started_at_utc"])
    _validate_utc("finished_at_utc", values["finished_at_utc"])

    if values["status"] == "ok":
        if values["return_code"] != 0:
            raise ValueError("ok record requires return_code=0")
        if values["error_class"]:
            raise ValueError("ok record requires empty error_class")
        if not HASH_RE.fullmatch(values["result_hash"]):
            raise ValueError("ok record requires a 16-hex result_hash")
        if values["oracle_status"] not in {"not_run", "passed", "failed"}:
            raise ValueError("ok record has invalid oracle_status")
    else:
        if not values["error_class"]:
            raise ValueError("non-ok record requires error_class")
        if not values["stdout_log"] or not values["stderr_log"]:
            raise ValueError("non-ok record requires stdout_log and stderr_log")

    return BenchmarkRecord(**values)


def read_records(path: Path) -> list[BenchmarkRecord]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != RAW_FIELDS:
            raise ValueError("raw CSV header does not match schema_version=1")
        return [validate_record(row) for row in reader]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate MEMQ5 benchmark records")
    parser.add_argument("command", choices=["validate"])
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    records = read_records(args.path)
    print(f"ok records={len(records)} schema_version=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
