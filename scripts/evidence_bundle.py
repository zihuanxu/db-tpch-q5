#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.benchmark_schema import read_records
    from scripts.formal_matrix import expected_configuration_keys, load_matrix
    from scripts.summarize_run_records import summary_fieldnames, summarize_records
except ModuleNotFoundError:
    from benchmark_schema import read_records
    from formal_matrix import expected_configuration_keys, load_matrix
    from summarize_run_records import summary_fieldnames, summarize_records


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_checksums(root: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name not in {"manifest.json", "manifest.sha256"}:
            checksums[path.relative_to(root).as_posix()] = sha256_file(path)
    return checksums


def audit_checksums(root: Path, expected: dict[str, str]) -> list[str]:
    actual = artifact_checksums(root)
    return sorted(
        path
        for path in set(expected) | set(actual)
        if expected.get(path) != actual.get(path)
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _git(project_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=project_root, text=True, capture_output=True, check=True
    )
    return completed.stdout.strip()


def _configuration(record) -> dict[str, object]:
    return {
        "engine": record.engine,
        "scenario": record.scenario,
        "threads": record.threads,
        "cpu_ratio": record.cpu_ratio,
        "gpu_ratio": record.gpu_ratio,
    }


def _configuration_key(record) -> tuple[object, ...]:
    return (
        record.engine,
        record.scenario,
        record.threads,
        record.cpu_ratio,
        record.gpu_ratio,
    )


def check_coverage(
    measured,
    warmups,
    *,
    warmup: int,
    repeat: int,
    expected_configurations: int,
    expected_keys: set[tuple[object, ...]] | None = None,
) -> list[str]:
    errors: list[str] = []
    measured_groups: dict[tuple[object, ...], list] = {}
    warmup_groups: dict[tuple[object, ...], list] = {}
    for record in measured:
        measured_groups.setdefault(_configuration_key(record), []).append(record)
        if record.is_warmup:
            errors.append(f"measured record {record.run_uuid} is marked warmup")
    for record in warmups:
        warmup_groups.setdefault(_configuration_key(record), []).append(record)
        if not record.is_warmup or record.sample_index != -1:
            errors.append(f"warmup record {record.run_uuid} has invalid markers")
    if len(measured_groups) != expected_configurations:
        errors.append(
            f"configuration count expected={expected_configurations} actual={len(measured_groups)}"
        )
    if expected_keys is not None and set(measured_groups) != expected_keys:
        missing = sorted(expected_keys - set(measured_groups))
        extra = sorted(set(measured_groups) - expected_keys)
        errors.append(f"configuration keys mismatch missing={missing} extra={extra}")
    if set(measured_groups) != set(warmup_groups) and warmup > 0:
        errors.append("measured and warmup configuration sets differ")
    for key, records in measured_groups.items():
        indices = sorted(record.sample_index for record in records)
        if len(records) != repeat or indices != list(range(repeat)):
            errors.append(
                f"measured samples for {key} expected={repeat} indices={indices}"
            )
    for key in measured_groups:
        records = warmup_groups.get(key, [])
        if len(records) != warmup:
            errors.append(
                f"warmup samples for {key} expected={warmup} actual={len(records)}"
            )
    run_ids = [record.run_uuid for record in measured + warmups]
    if len(run_ids) != len(set(run_ids)):
        errors.append("duplicate run_uuid values")
    return errors


def _check_log_paths(root: Path, records) -> list[str]:
    errors: list[str] = []
    for record in records:
        for value in (record.stdout_log, record.stderr_log):
            path = Path(value)
            if not path.is_absolute():
                path = root / path
            path = path.resolve()
            try:
                path.relative_to(root)
            except ValueError:
                errors.append(f"log outside evidence directory: {value}")
                continue
            if not path.is_file():
                errors.append(f"missing log: {value}")
    return errors


def _check_summary(root: Path, measured) -> list[str]:
    with (root / "summary.csv").open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        actual = list(reader)
        if reader.fieldnames != summary_fieldnames():
            return ["summary header does not match the statistics contract"]
    expected = [
        {name: str(row.get(name, "")) for name in summary_fieldnames()}
        for row in summarize_records(measured)
    ]
    return [] if actual == expected else ["summary.csv does not match statistics recomputed from raw.csv"]


def finalize(args: argparse.Namespace) -> int:
    root = args.directory.resolve()
    required = ["raw.csv", "warmups.csv", "summary.csv", "environment.json", "commands.txt"]
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise ValueError(f"missing evidence artifacts: {missing}")

    project_root = args.project_root.resolve()
    matrix = load_matrix(args.matrix.resolve())
    matrix_copy = root / "matrix.yml"
    shutil.copyfile(args.matrix.resolve(), matrix_copy)
    expected_keys = set(expected_configuration_keys(matrix))
    protocol = matrix["protocol"]
    expected_hash = str(matrix["dataset"]["expected_hash"]).lower()
    expected_dataset = (project_root / str(matrix["dataset"]["path"])).resolve()
    if args.dataset.resolve() != expected_dataset:
        raise ValueError(f"dataset does not match formal matrix path {expected_dataset}")

    measured = read_records(root / "raw.csv")
    warmups = read_records(root / "warmups.csv")
    if not measured:
        raise ValueError("raw.csv has no measured records")
    experiment_ids = {record.experiment_id for record in measured + warmups}
    if len(experiment_ids) != 1:
        raise ValueError("evidence contains multiple experiment ids")
    if experiment_ids != {str(matrix["experiment_id"])}:
        raise ValueError("record experiment id does not match formal matrix")
    if any(
        record.scale_factor != str(matrix["dataset"]["scale_factor"])
        or record.region != str(matrix["query"]["region"])
        or record.date != str(matrix["query"]["date"])
        for record in measured + warmups
    ):
        raise ValueError("record dataset/query metadata does not match formal matrix")
    successful = [record for record in measured if record.status == "ok"]
    failures = [record for record in measured if record.status != "ok"]
    hashes = sorted({record.result_hash for record in successful})
    coverage_errors = check_coverage(
        measured,
        warmups,
        warmup=int(protocol["warmup"]),
        repeat=int(protocol["repeat"]),
        expected_configurations=len(expected_keys),
        expected_keys=expected_keys,
    )
    coverage_errors.extend(_check_log_paths(root, measured + warmups))
    summary_errors = _check_summary(root, measured)
    correctness_ok = (
        not failures
        and hashes == [expected_hash]
        and all(record.oracle_status == "passed" for record in successful)
        and not coverage_errors
        and not summary_errors
    )
    correctness = {
        "schema_version": 1,
        "ok": correctness_ok,
        "expected_hash": expected_hash,
        "observed_hashes": hashes,
        "measured_records": len(measured),
        "successful_records": len(successful),
        "failed_records": len(failures),
        "warmup_records": len(warmups),
        "warmup_failures": sum(record.status != "ok" for record in warmups),
        "coverage_errors": coverage_errors,
        "summary_errors": summary_errors,
    }
    (root / "correctness.json").write_text(
        json.dumps(correctness, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    dirty_paths = [line for line in _git(project_root, "status", "--short").splitlines() if line]
    dataset_manifest = args.dataset.resolve() / "manifest.json"
    configurations = {
        json.dumps(_configuration(record), sort_keys=True)
        for record in measured
    }
    manifest = {
        "manifest_version": 1,
        "experiment_id": next(iter(experiment_ids)),
        "status": "complete" if correctness_ok else "correctness_failed",
        "created_at_utc": _utc_now(),
        "git": {
            "commit": _git(project_root, "rev-parse", "HEAD"),
            "branch": _git(project_root, "branch", "--show-current"),
            "dirty": bool(dirty_paths),
            "dirty_paths": dirty_paths,
        },
        "dataset": {
            "path": str(args.dataset.resolve()),
            "manifest_sha256": sha256_file(dataset_manifest),
            "scale_factor": measured[0].scale_factor,
        },
        "matrix": {
            "source_file": "matrix.yml",
            "sha256": sha256_file(matrix_copy),
            "configurations": [json.loads(value) for value in sorted(configurations)],
            "configuration_count": len(expected_keys),
        },
        "protocol": {
            "warmup": int(protocol["warmup"]),
            "repeat": int(protocol["repeat"]),
            "timeout_seconds": float(protocol["timeout_seconds"]),
            "resident_supported": False,
            "expected_configurations": len(expected_keys),
        },
        "correctness": correctness,
        "artifacts": artifact_checksums(root),
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (root / "manifest.sha256").write_text(
        sha256_file(root / "manifest.json") + "  manifest.json\n", encoding="ascii"
    )
    print(
        f"finalized status={manifest['status']} records={len(measured)} "
        f"artifacts={len(manifest['artifacts'])}"
    )
    return 0 if correctness_ok else 1


def audit(args: argparse.Namespace) -> int:
    root = args.directory.resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    mismatches = audit_checksums(root, manifest["artifacts"])
    digest_line = (root / "manifest.sha256").read_text(encoding="ascii").strip()
    manifest_digest_ok = digest_line == f"{sha256_file(root / 'manifest.json')}  manifest.json"
    measured = read_records(root / "raw.csv")
    warmups = read_records(root / "warmups.csv")
    correctness = json.loads((root / "correctness.json").read_text(encoding="utf-8"))
    matrix = load_matrix(root / manifest["matrix"]["source_file"])
    matrix_digest_ok = sha256_file(root / manifest["matrix"]["source_file"]) == manifest["matrix"]["sha256"]
    expected_keys = set(expected_configuration_keys(matrix))
    protocol = manifest["protocol"]
    coverage_errors = check_coverage(
        measured,
        warmups,
        warmup=int(protocol["warmup"]),
        repeat=int(protocol["repeat"]),
        expected_configurations=int(protocol["expected_configurations"]),
        expected_keys=expected_keys,
    )
    coverage_errors.extend(_check_log_paths(root, measured + warmups))
    summary_errors = _check_summary(root, measured)
    ok = (
        not mismatches
        and manifest_digest_ok
        and matrix_digest_ok
        and not coverage_errors
        and not summary_errors
        and manifest.get("status") == "complete"
        and correctness.get("ok") is True
        and len(measured) == correctness.get("measured_records")
        and len(warmups) == correctness.get("warmup_records")
    )
    print(
        f"audit ok={str(ok).lower()} measured={len(measured)} warmups={len(warmups)} "
        f"checksum_mismatches={mismatches} manifest_digest_ok={manifest_digest_ok} "
        f"matrix_digest_ok={matrix_digest_ok} coverage_errors={coverage_errors} "
        f"summary_errors={summary_errors}"
    )
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize or audit a MEMQ5 evidence bundle")
    subparsers = parser.add_subparsers(dest="command", required=True)
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--directory", type=Path, required=True)
    finalize_parser.add_argument("--project-root", type=Path, default=Path.cwd())
    finalize_parser.add_argument("--dataset", type=Path, required=True)
    finalize_parser.add_argument("--matrix", type=Path, required=True)
    audit_parser = subparsers.add_parser("audit")
    audit_parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    return finalize(args) if args.command == "finalize" else audit(args)


if __name__ == "__main__":
    raise SystemExit(main())
