#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from scripts.verify_q5_oracle import result_hash_hex
except ModuleNotFoundError:
    from verify_q5_oracle import result_hash_hex


BASE_BACKENDS = (
    "cpu-specialized",
    "arrow-acero",
    "gpu-copy",
    "gpu-managed",
    "gpu-mapped",
    "hybrid-fixed",
    "cudf",
)
GPU_BACKENDS = {
    "gpu-copy",
    "gpu-managed",
    "gpu-mapped",
    "hybrid-fixed",
    "hybrid-auto",
    "cudf",
}
HASH_RE = re.compile(r"^[0-9a-f]{16}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def compare_rows(expected: list[dict], actual: list[dict]) -> list[str]:
    """Return exact ordered-row differences for canonical Q5 result rows."""
    errors: list[str] = []
    for source, rows in (("expected", expected), ("actual", actual)):
        nations: set[str] = set()
        for index, row in enumerate(rows):
            nation = row.get("nation") if isinstance(row, dict) else None
            if not isinstance(nation, str):
                errors.append(f"{source} row {index} has invalid nation")
                continue
            if nation in nations:
                errors.append(f"duplicate nation {nation}")
            nations.add(nation)
            revenue = row.get("revenue_1e4") if isinstance(row, dict) else None
            if isinstance(revenue, bool) or not isinstance(revenue, int):
                errors.append(f"{source} row {index} has invalid revenue_1e4")

    if len(expected) != len(actual):
        errors.append(f"row count mismatch expected={len(expected)} actual={len(actual)}")
    for index, (expected_row, actual_row) in enumerate(zip(expected, actual, strict=False)):
        if not isinstance(expected_row, dict) or not isinstance(actual_row, dict):
            errors.append(f"row {index} is not an object")
            continue
        for field in ("nation", "revenue_1e4"):
            if expected_row.get(field) != actual_row.get(field):
                errors.append(
                    f"row {index} {field} mismatch "
                    f"expected={expected_row.get(field)!r} actual={actual_row.get(field)!r}"
                )
    return errors


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} {path} must be an object")
    return payload


def _required_backends(matrix: dict[str, Any]) -> list[str]:
    if matrix.get("schema_version") != 1:
        raise ValueError("correctness matrix schema_version must be 1")
    backends = matrix.get("required_backends")
    if not isinstance(backends, list) or any(not isinstance(item, str) for item in backends):
        raise ValueError("correctness matrix required_backends must be a string list")
    if len(backends) != len(set(backends)):
        raise ValueError("correctness matrix has duplicate required_backends")
    if set(backends) != set(BASE_BACKENDS):
        raise ValueError("correctness matrix must require every fixed backend exactly once")
    hybrid_auto = matrix.get("hybrid_auto")
    if not isinstance(hybrid_auto, dict) or not isinstance(hybrid_auto.get("enabled"), bool):
        raise ValueError("correctness matrix hybrid_auto.enabled must be boolean")
    return backends + (["hybrid-auto"] if hybrid_auto["enabled"] else [])


def _matrix_identity(matrix: dict[str, Any]) -> dict[str, str]:
    dataset = matrix.get("dataset")
    query = matrix.get("query")
    if not isinstance(dataset, dict) or not isinstance(query, dict):
        raise ValueError("correctness matrix requires dataset and query objects")
    identity = {
        "experiment_id": matrix.get("experiment_id"),
        "scale_factor": dataset.get("scale_factor"),
        "dataset_path": dataset.get("path"),
        "dataset_manifest_sha256": dataset.get("manifest_sha256"),
        "region": query.get("region"),
        "date": query.get("date"),
    }
    if any(not isinstance(value, str) or not value for value in identity.values()):
        raise ValueError("correctness matrix identity fields must be nonempty strings")
    if not SHA256_RE.fullmatch(identity["dataset_manifest_sha256"]):
        raise ValueError("correctness matrix dataset manifest must be lowercase SHA256")
    return identity


def _oracle_rows_and_hash(oracle: dict[str, Any]) -> tuple[list[dict], str]:
    rows = oracle.get("rows")
    result_hash = oracle.get("result_hash")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("oracle rows must be a list of objects")
    if not isinstance(result_hash, str) or not HASH_RE.fullmatch(result_hash):
        raise ValueError("oracle result_hash must be 16 lowercase hex characters")
    row_errors = compare_rows(rows, rows)
    if row_errors:
        raise ValueError(f"oracle rows are invalid: {row_errors}")
    derived_hash = result_hash_hex(
        [(row["nation"], row["revenue_1e4"]) for row in rows]
    )
    if result_hash != derived_hash:
        raise ValueError("oracle result_hash does not match rows")
    return rows, result_hash


def _result(status: str, errors: list[str]) -> dict[str, object]:
    return {"status": status, "errors": errors}


def _check_backend(
    directory: Path,
    backend: str,
    expected_rows: list[dict],
    expected_hash: str,
    expected_identity: dict[str, str],
) -> tuple[dict[str, object], str | None]:
    path = directory / f"{backend}.json"
    if not path.is_file():
        return _result("failed", ["missing run record"]), None
    try:
        record = _load_json(path, "run record")
    except ValueError as exc:
        return _result("failed", [str(exc)]), None
    if record.get("backend") != backend:
        return _result("failed", [f"backend mismatch expected={backend!r}"]), None
    if record.get("schema_version") != 1:
        return _result("failed", ["run record schema_version must be 1"]), None
    if record.get("identity") != expected_identity:
        return _result("failed", ["run identity does not match matrix"]), None

    process = record.get("process")
    if not isinstance(process, dict):
        return _result("failed", ["process must be an object"]), None
    process_status = process.get("status")
    return_code = process.get("return_code")
    if process_status == "skipped_gpu" and return_code == 77 and backend in GPU_BACKENDS:
        return _result("unavailable", ["GPU backend unavailable"]), None
    if process_status != "ok" or return_code != 0:
        return _result(
            "failed", [f"process status={process_status} return_code={return_code}"]
        ), None

    output = record.get("output")
    if not isinstance(output, dict):
        return _result("failed", ["output must be an object"]), None
    actual_rows = output.get("rows")
    actual_hash = output.get("result_hash")
    if not isinstance(actual_rows, list) or any(not isinstance(row, dict) for row in actual_rows):
        return _result("failed", ["output rows must be a list of objects"]), None
    errors = compare_rows(expected_rows, actual_rows)
    row_errors = compare_rows(actual_rows, actual_rows)
    if not row_errors:
        derived_hash = result_hash_hex(
            [(row["nation"], row["revenue_1e4"]) for row in actual_rows]
        )
        if actual_hash != derived_hash:
            errors.append("output result_hash does not match rows")
    if actual_hash != expected_hash:
        errors.append(f"result_hash mismatch expected={expected_hash} actual={actual_hash}")
    if errors:
        return _result("failed", errors), actual_hash if isinstance(actual_hash, str) else None
    return _result("passed", []), actual_hash


def verify_correctness(directory: Path, matrix: Path, oracle: Path) -> dict:
    matrix_payload = _load_json(matrix, "correctness matrix")
    required_backends = _required_backends(matrix_payload)
    expected_identity = _matrix_identity(matrix_payload)
    expected_rows, expected_hash = _oracle_rows_and_hash(_load_json(oracle, "oracle"))
    expected_files = {f"{backend}.json" for backend in required_backends}
    actual_files = {path.name for path in directory.glob("*.json") if path.is_file()}
    unexpected_files = sorted(actual_files - expected_files)
    if unexpected_files:
        raise ValueError(f"unexpected run record(s): {unexpected_files}")
    results: dict[str, dict[str, object]] = {}
    observed_hashes: set[str] = set()
    for backend in required_backends:
        result, observed_hash = _check_backend(
            directory, backend, expected_rows, expected_hash, expected_identity
        )
        results[backend] = result
        if observed_hash is not None:
            observed_hashes.add(observed_hash)
    return {
        "ok": all(result["status"] == "passed" for result in results.values()),
        "expected_hash": expected_hash,
        "observed_hashes": sorted(observed_hashes),
        "backends": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Gate Formal SF10 backend results against DuckDB")
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--oracle", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = verify_correctness(args.runs, args.matrix, args.oracle)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
