#!/usr/bin/env python3
"""Recompute the formal V7 request gate against an independent Q5 oracle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from scripts.run_v7_benchmarks import (
        configurations,
        load_matrix,
        validate_oracle_payload,
    )
    from scripts.v7_benchmark_schema import read_requests, read_setups
    from scripts.v7_correctness_gate import compare_rows
    from scripts.v7_evidence_bundle import _validate_coverage
except ModuleNotFoundError:
    from run_v7_benchmarks import (  # type: ignore[no-redef]
        configurations,
        load_matrix,
        validate_oracle_payload,
    )
    from v7_benchmark_schema import read_requests, read_setups  # type: ignore[no-redef]
    from v7_correctness_gate import compare_rows  # type: ignore[no-redef]
    from v7_evidence_bundle import _validate_coverage  # type: ignore[no-redef]


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return payload


def materialize_correctness(
    bundle: Path, matrix_path: Path, oracle_path: Path, output: Path
) -> dict[str, object]:
    matrix = load_matrix(matrix_path)
    configs = configurations(matrix)
    setups = read_setups(bundle / "setups.csv")
    warmups = read_requests(bundle / "warmups.csv")
    measured = read_requests(bundle / "raw.csv")
    _validate_coverage(bundle, matrix, configs, setups, warmups, measured)

    expected_hash = matrix["dataset"]["expected_hash"]
    oracle = _load_json(oracle_path, "oracle")
    validate_oracle_payload(oracle, expected_hash=expected_hash)
    expected_rows = oracle.get("rows")
    if not isinstance(expected_rows, list):
        raise ValueError("oracle rows must be a list")

    backend_by_config = {
        config.config_id: config.correctness_backend for config in configs
    }
    errors_by_backend: dict[str, list[str]] = {
        backend: [] for backend in sorted(set(backend_by_config.values()))
    }
    observed_hashes: set[str] = set()
    for record in [*warmups, *measured]:
        backend = backend_by_config[record.config_id]
        observed_hashes.add(record.result_hash)
        differences = compare_rows(expected_rows, record.rows)
        errors_by_backend[backend].extend(
            f"{record.config_id} sample={record.sample_index}: {difference}"
            for difference in differences
        )

    backends = {
        backend: {
            "status": "passed" if not errors else "failed",
            "errors": errors,
        }
        for backend, errors in errors_by_backend.items()
    }
    report: dict[str, object] = {
        "ok": all(result["status"] == "passed" for result in backends.values()),
        "expected_hash": expected_hash,
        "observed_hashes": sorted(observed_hashes),
        "backends": backends,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(output)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Recompute formal V7 request rows against the independent oracle"
    )
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--oracle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = materialize_correctness(
            args.bundle, args.matrix, args.oracle, args.output
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
