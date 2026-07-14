#!/usr/bin/env python3

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

try:
    from scripts.verify_q5_oracle import result_hash_hex
except ModuleNotFoundError:
    from verify_q5_oracle import result_hash_hex


HASH_RE = re.compile(r"^[0-9a-f]{16}$")
SETUP_REQUIRED = {
    "record_type",
    "session_id",
    "lifecycle",
    "status",
    "error_class",
    "engine",
    "dataset",
    "region",
    "date",
    "dataset_load_ms",
    "session_setup_ms",
    "tune_ms",
    "resident_host_bytes",
    "resident_gpu_bytes",
    "resident_pinned_bytes",
    "selected_cpu_ratio",
    "predicted_cpu_ratio",
}
REQUEST_REQUIRED = {
    "record_type",
    "session_id",
    "lifecycle",
    "status",
    "error_class",
    "request_index",
    "is_warmup",
    "selected_cpu_ratio",
    "result_rows",
    "result_hash",
    "rows",
    "query_total_ms",
    "input_lineitem_rows",
    "matched_lineitem_rows",
    "cpu_input_rows",
    "gpu_input_rows",
    "h2d_bytes",
    "d2h_bytes",
    "mapped_remote_read_bytes",
}
SETUP_TIMINGS = {
    "dataset_load_ms",
    "session_setup_ms",
    "plan_build_ms",
    "host_staging_ms",
    "allocation_ms",
    "initial_h2d_ms",
    "tune_ms",
}
SETUP_BYTES = {
    "resident_host_bytes",
    "resident_gpu_bytes",
    "resident_pinned_bytes",
}
REQUEST_TIMINGS = {
    "build_ms",
    "h2d_ms",
    "kernel_ms",
    "d2h_ms",
    "scan_ms",
    "query_total_ms",
    "cpu_ms",
    "gpu_ms",
    "overlap_ms",
}
REQUEST_BYTES = {
    "h2d_bytes",
    "d2h_bytes",
    "mapped_remote_read_bytes",
}
REQUEST_COUNTERS = {
    "input_lineitem_rows",
    "matched_lineitem_rows",
    "cpu_input_rows",
    "gpu_input_rows",
}


@dataclass(frozen=True)
class ResidentSession:
    session_id: str
    setup: dict[str, Any]
    warmups: tuple[dict[str, Any], ...]
    measured: tuple[dict[str, Any], ...]
    rows: list[dict[str, object]]
    result_hash: str


def _require_fields(record: dict[str, Any], required: set[str], label: str) -> None:
    missing = sorted(required - set(record))
    if missing:
        raise ValueError(f"{label} missing required fields: {missing}")


def _require_nonnegative_number(record: dict[str, Any], name: str, label: str) -> None:
    value = record[name]
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise ValueError(f"{label} {name} must be a nonnegative finite number")


def _require_nonnegative_integer(record: dict[str, Any], name: str, label: str) -> None:
    value = record[name]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} {name} must be a nonnegative integer")


def _require_ratio(record: dict[str, Any], name: str, label: str) -> None:
    value = record[name]
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
        or value > 1
    ):
        raise ValueError(f"{label} {name} must be between 0 and 1")


def _validate_lifecycle_and_status(record: dict[str, Any], label: str) -> None:
    if record["lifecycle"] != "resident":
        raise ValueError(f"{label} lifecycle must be resident")
    if record["status"] != "ok":
        raise ValueError(f"{label} status must be ok")
    if record["error_class"] != "":
        raise ValueError(f"{label} error_class must be empty when status is ok")


def _validated_rows(record: dict[str, Any], label: str) -> tuple[list[dict[str, object]], str]:
    rows = record["rows"]
    if not isinstance(rows, list):
        raise ValueError(f"{label} rows must be a list")
    exact: list[tuple[str, int]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"nation", "revenue_1e4"}:
            raise ValueError(
                f"{label} rows[{index}] must contain nation and revenue_1e4"
            )
        nation = row["nation"]
        revenue = row["revenue_1e4"]
        if not isinstance(nation, str) or not nation:
            raise ValueError(f"{label} rows[{index}].nation must be nonempty")
        if nation in seen:
            raise ValueError(f"{label} rows contain duplicate nation {nation}")
        if isinstance(revenue, bool) or not isinstance(revenue, int):
            raise ValueError(f"{label} rows[{index}].revenue_1e4 must be an integer")
        seen.add(nation)
        exact.append((nation, revenue))
    if record["result_rows"] != len(rows):
        raise ValueError(f"{label} result_rows does not match exact rows")
    derived_hash = result_hash_hex(exact)
    result_hash = record["result_hash"]
    if not isinstance(result_hash, str) or not HASH_RE.fullmatch(result_hash):
        raise ValueError(f"{label} result_hash must be 16 lowercase hex characters")
    if result_hash != derived_hash:
        raise ValueError(f"{label} result_hash does not match exact rows")
    return rows, derived_hash


def _validate_setup(record: dict[str, Any]) -> None:
    _require_fields(record, SETUP_REQUIRED, "session_setup")
    if record["record_type"] != "session_setup":
        raise ValueError("setup record_type must be session_setup")
    _validate_lifecycle_and_status(record, "session_setup")
    for name in SETUP_TIMINGS & set(record):
        _require_nonnegative_number(record, name, "session_setup")
    for name in SETUP_BYTES:
        _require_nonnegative_integer(record, name, "session_setup")
    for name in ("selected_cpu_ratio", "predicted_cpu_ratio"):
        _require_ratio(record, name, "session_setup")


def _validate_request(
    record: dict[str, Any],
    index: int,
    warmup: int,
    session_id: str,
    selected_cpu_ratio: float,
) -> tuple[list[dict[str, object]], str]:
    label = f"request {index}"
    _require_fields(record, REQUEST_REQUIRED, label)
    if record["record_type"] != "request":
        raise ValueError(f"{label} record_type must be request")
    _validate_lifecycle_and_status(record, label)
    if record["session_id"] != session_id:
        raise ValueError(f"{label} session_id does not match setup")
    if isinstance(record["request_index"], bool) or record["request_index"] != index:
        raise ValueError(f"{label} request_index must be contiguous from zero")
    expected_warmup = index < warmup
    if not isinstance(record["is_warmup"], bool) or record["is_warmup"] != expected_warmup:
        raise ValueError(f"{label} is_warmup does not match the protocol boundary")
    _require_ratio(record, "selected_cpu_ratio", label)
    if not math.isclose(
        float(record["selected_cpu_ratio"]), selected_cpu_ratio, abs_tol=1e-9
    ):
        raise ValueError(f"{label} selected_cpu_ratio changed within the session")
    for name in REQUEST_TIMINGS & set(record):
        _require_nonnegative_number(record, name, label)
    for name in REQUEST_BYTES:
        _require_nonnegative_integer(record, name, label)
    for name in REQUEST_COUNTERS:
        _require_nonnegative_integer(record, name, label)
    if record["matched_lineitem_rows"] > record["input_lineitem_rows"]:
        raise ValueError(f"{label} matched_lineitem_rows exceeds input_lineitem_rows")
    if record["cpu_input_rows"] + record["gpu_input_rows"] != record["input_lineitem_rows"]:
        raise ValueError(f"{label} CPU/GPU input rows do not conserve input rows")
    return _validated_rows(record, label)


def _decode_jsonl(stdout: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(stdout.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL line {line_number}: {exc.msg}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"JSONL line {line_number} must be an object")
        records.append(record)
    return records


def parse_resident_jsonl(
    stdout: str,
    *,
    warmup: int,
    repeat: int,
    expected_hash: str | None = None,
) -> ResidentSession:
    if isinstance(warmup, bool) or not isinstance(warmup, int) or warmup < 0:
        raise ValueError("warmup must be a nonnegative integer")
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 1:
        raise ValueError("repeat must be a positive integer")
    if expected_hash is not None and not HASH_RE.fullmatch(expected_hash):
        raise ValueError("expected_hash must be 16 lowercase hex characters")

    records = _decode_jsonl(stdout)
    setups = [record for record in records if record.get("record_type") == "session_setup"]
    if len(setups) != 1 or not records or records[0] is not setups[0]:
        raise ValueError("resident JSONL must contain exactly one session_setup as its first record")
    setup = setups[0]
    _validate_setup(setup)
    session_id = setup["session_id"]
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_setup session_id must be nonempty")

    requests = records[1:]
    if any(record.get("record_type") != "request" for record in requests):
        raise ValueError("resident JSONL contains an unsupported record_type")
    expected_count = warmup + repeat
    if len(requests) != expected_count:
        raise ValueError(
            f"resident request count must be {expected_count}, got {len(requests)}"
        )
    selected_cpu_ratio = float(setup["selected_cpu_ratio"])
    stable_rows: list[dict[str, object]] | None = None
    stable_hash: str | None = None
    for index, request in enumerate(requests):
        rows, result_hash = _validate_request(
            request, index, warmup, session_id, selected_cpu_ratio
        )
        if stable_rows is None:
            stable_rows = rows
            stable_hash = result_hash
        elif rows != stable_rows:
            raise ValueError(f"request {index} exact rows changed within the session")
        elif result_hash != stable_hash:
            raise ValueError(f"request {index} result_hash changed within the session")
    if expected_hash is not None and stable_hash != expected_hash:
        raise ValueError(
            f"resident result_hash mismatch expected={expected_hash} actual={stable_hash}"
        )

    return ResidentSession(
        session_id=session_id,
        setup=setup,
        warmups=tuple(requests[:warmup]),
        measured=tuple(requests[warmup:]),
        rows=stable_rows or [],
        result_hash=stable_hash or result_hash_hex([]),
    )
