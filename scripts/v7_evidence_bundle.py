#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts.run_v7_benchmarks import (
        Configuration,
        _payload_sha256,
        build_command,
        canonical_command,
        configurations,
        load_matrix,
        validate_oracle_payload,
    )
    from scripts.summarize_v7_records import (
        summarize_records,
        summary_fieldnames,
        write_markdown,
        write_summary,
    )
    from scripts.v7_benchmark_schema import (
        V7BenchmarkRecord,
        V7SetupRecord,
        read_requests,
        read_setups,
    )
except ModuleNotFoundError:
    from run_v7_benchmarks import (  # type: ignore[no-redef]
        Configuration,
        _payload_sha256,
        build_command,
        canonical_command,
        configurations,
        load_matrix,
        validate_oracle_payload,
    )
    from summarize_v7_records import (  # type: ignore[no-redef]
        summarize_records,
        summary_fieldnames,
        write_markdown,
        write_summary,
    )
    from v7_benchmark_schema import (  # type: ignore[no-redef]
        V7BenchmarkRecord,
        V7SetupRecord,
        read_requests,
        read_setups,
    )


MANIFEST_FILES = {"manifest.json", "manifest.sha256"}
REQUIRED_INPUT_FILES = {
    "setups.csv",
    "warmups.csv",
    "raw.csv",
    "environment.json",
    "commands.txt",
}
REQUIRED_BUNDLE_FILES = REQUIRED_INPUT_FILES | {
    "summary.csv",
    "summary.md",
    "correctness.json",
    "matrix.yml",
    "dataset-manifest.json",
    "oracle.json",
    *MANIFEST_FILES,
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain an object")
    return payload


def _require_regular(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or non-regular {label}: {path}")


def artifact_checksums(root: Path) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"bundle symlinks are forbidden: {path.relative_to(root)}")
        if path.is_file() and path.name not in MANIFEST_FILES:
            relative = path.relative_to(root).as_posix()
            checksums[relative] = sha256_file(path)
    return checksums


def _validate_sources(
    matrix: dict[str, Any], dataset_manifest: Path, oracle: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    if matrix["protocol"]["warmup"] != 3 or matrix["protocol"]["repeat"] != 10:
        raise ValueError("formal V7 evidence requires exactly 3 warmup and 10 measured requests")
    if "oracle" not in matrix:
        raise ValueError("formal V7 matrix requires an independent oracle path and sha256")
    _require_regular(dataset_manifest, "dataset manifest")
    _require_regular(oracle, "oracle")
    if sha256_file(dataset_manifest) != matrix["dataset"]["manifest_sha256"]:
        raise ValueError("dataset manifest SHA256 does not match the matrix")
    if sha256_file(oracle) != matrix["oracle"]["sha256"]:
        raise ValueError("oracle SHA256 does not match the matrix")
    dataset_payload = _load_json(dataset_manifest, "dataset manifest")
    if str(dataset_payload.get("scale_factor")) != matrix["dataset"]["scale_factor"]:
        raise ValueError("dataset manifest scale_factor does not match the matrix")
    oracle_payload = _load_json(oracle, "oracle")
    validate_oracle_payload(
        oracle_payload, expected_hash=matrix["dataset"]["expected_hash"]
    )
    if oracle_payload.get("matched") is False:
        raise ValueError("independent oracle explicitly reports a mismatch")
    return dataset_payload, oracle_payload


def _validate_correctness(
    correctness: dict[str, Any], matrix: dict[str, Any], configs: Sequence[Configuration]
) -> None:
    expected_hash = matrix["dataset"]["expected_hash"]
    if correctness.get("ok") is not True:
        raise ValueError("correctness gate did not pass")
    if correctness.get("expected_hash") != expected_hash:
        raise ValueError("correctness expected_hash does not match the matrix")
    observed = correctness.get("observed_hashes")
    if observed != [expected_hash]:
        raise ValueError("correctness observed_hashes do not contain exactly the oracle hash")
    backends = correctness.get("backends")
    if not isinstance(backends, dict):
        raise ValueError("correctness report requires backend results")
    expected_backends = {config.correctness_backend for config in configs}
    if set(backends) != expected_backends:
        raise ValueError("correctness backend coverage does not match the matrix")
    for backend in sorted(expected_backends):
        result = backends.get(backend)
        if not isinstance(result, dict) or result.get("status") != "passed":
            raise ValueError(f"correctness backend {backend} did not pass")
        if result.get("errors") not in (None, []):
            raise ValueError(f"correctness backend {backend} contains errors")


def _config_matches_setup(config: Configuration, setup: V7SetupRecord) -> bool:
    if (
        setup.config_id != config.config_id
        or setup.engine != config.engine
        or setup.threads != config.threads
        or setup.ratio_mode != config.ratio_mode
    ):
        return False
    if config.ratio_mode == "fixed" and not math.isclose(
        setup.cpu_ratio, config.cpu_ratio, abs_tol=1e-9
    ):
        return False
    return True


def _check_log_paths(
    root: Path, setups: Sequence[V7SetupRecord], requests: Sequence[V7BenchmarkRecord]
) -> None:
    for record in [*setups, *requests]:
        for value in (record.stdout_log, record.stderr_log):
            path = root / value
            _require_regular(path, f"log {value}")
            try:
                path.resolve().relative_to(root.resolve())
            except ValueError as exc:
                raise ValueError(f"log path escapes the bundle: {value}") from exc


def _validate_coverage(
    root: Path,
    matrix: dict[str, Any],
    configs: Sequence[Configuration],
    setups: Sequence[V7SetupRecord],
    warmups: Sequence[V7BenchmarkRecord],
    measured: Sequence[V7BenchmarkRecord],
) -> None:
    config_by_id = {config.config_id: config for config in configs}
    expected_ids = set(config_by_id)
    setup_by_id: dict[str, V7SetupRecord] = {}
    for setup in setups:
        if setup.config_id in setup_by_id:
            raise ValueError(f"duplicate setup for configuration {setup.config_id}")
        setup_by_id[setup.config_id] = setup
    if set(setup_by_id) != expected_ids:
        raise ValueError(
            "setup configuration coverage mismatch "
            f"missing={sorted(expected_ids - set(setup_by_id))} "
            f"extra={sorted(set(setup_by_id) - expected_ids)}"
        )
    session_ids = [setup.session_id for setup in setups]
    if len(session_ids) != len(set(session_ids)):
        raise ValueError("setup session_id values must be unique")

    warmups_by_id: dict[str, list[V7BenchmarkRecord]] = {
        config_id: [] for config_id in expected_ids
    }
    measured_by_id: dict[str, list[V7BenchmarkRecord]] = {
        config_id: [] for config_id in expected_ids
    }
    for record in warmups:
        if record.config_id not in warmups_by_id:
            raise ValueError(f"unexpected warmup configuration {record.config_id}")
        warmups_by_id[record.config_id].append(record)
    for record in measured:
        if record.config_id not in measured_by_id:
            raise ValueError(f"unexpected measured configuration {record.config_id}")
        measured_by_id[record.config_id].append(record)

    protocol = matrix["protocol"]
    expected_hash = matrix["dataset"]["expected_hash"]
    run_uuids: list[str] = []
    for config_id, config in config_by_id.items():
        setup = setup_by_id[config_id]
        if not _config_matches_setup(config, setup):
            raise ValueError(f"setup metadata does not match configuration {config_id}")
        if setup.status != "ok" or setup.return_code != 0:
            raise ValueError(f"setup for {config_id} is not successful")
        if (
            setup.experiment_id != matrix["experiment_id"]
            or setup.scale_factor != matrix["dataset"]["scale_factor"]
            or setup.dataset_path != matrix["dataset"]["path"]
            or setup.region != matrix["query"]["region"]
            or setup.date != matrix["query"]["date"]
        ):
            raise ValueError(f"setup matrix identity mismatch for {config_id}")
        config_warmups = warmups_by_id[config_id]
        config_measured = measured_by_id[config_id]
        if len(config_warmups) != protocol["warmup"]:
            raise ValueError(
                f"warmup samples for {config_id} expected={protocol['warmup']} "
                f"actual={len(config_warmups)}"
            )
        if len(config_measured) != protocol["repeat"]:
            raise ValueError(
                f"measured samples for {config_id} expected={protocol['repeat']} "
                f"actual={len(config_measured)}"
            )
        if sorted(record.sample_index for record in config_warmups) != list(
            range(protocol["warmup"])
        ):
            raise ValueError(f"warmup sample indexes are incomplete for {config_id}")
        if sorted(record.sample_index for record in config_measured) != list(
            range(protocol["repeat"])
        ):
            raise ValueError(f"measured sample indexes are incomplete for {config_id}")
        combined = sorted(config_warmups + config_measured, key=lambda row: row.request_index)
        if [record.request_index for record in combined] != list(
            range(protocol["warmup"] + protocol["repeat"])
        ):
            raise ValueError(f"request indexes are incomplete for {config_id}")
        for record in combined:
            if record.is_warmup != (record in config_warmups):
                raise ValueError(f"warmup marker mismatch for {record.run_uuid}")
            if (
                record.session_id != setup.session_id
                or record.engine != setup.engine
                or record.threads != setup.threads
                or record.ratio_mode != setup.ratio_mode
                or record.experiment_id != setup.experiment_id
                or record.scale_factor != setup.scale_factor
                or record.region != setup.region
                or record.date != setup.date
            ):
                raise ValueError(f"request metadata does not match setup for {config_id}")
            if (
                not math.isclose(record.cpu_ratio, setup.cpu_ratio, abs_tol=1e-9)
                or not math.isclose(
                    record.selected_cpu_ratio, setup.selected_cpu_ratio, abs_tol=1e-9
                )
                or record.predicted_cpu_ratio != setup.predicted_cpu_ratio
            ):
                raise ValueError(f"request ratio provenance changed for {config_id}")
            if (
                record.status != "ok"
                or record.return_code != 0
                or record.result_hash != expected_hash
                or record.oracle_status != "expected_hash_match"
            ):
                raise ValueError(f"request result gate failed for {config_id}")
            if config.ratio_mode == "auto":
                boundary = setup.selected_batch_boundary_rows
                rows = setup.calibration_rows
                if (
                    rows is None
                    or boundary is None
                    or record.input_lineitem_rows != rows
                    or record.cpu_input_rows != boundary
                    or record.gpu_input_rows != rows - boundary
                ):
                    raise ValueError(f"request batch boundary does not conserve rows for {config_id}")
            run_uuids.append(record.run_uuid)
    if len(run_uuids) != len(set(run_uuids)):
        raise ValueError("request run_uuid values must be globally unique")
    _check_log_paths(root, setups, [*warmups, *measured])


def _validate_environment(
    environment: dict[str, Any], matrix: dict[str, Any], matrix_path: Path
) -> dict[str, object]:
    git = environment.get("git")
    cli = environment.get("session_cli")
    cudf = environment.get("cudf_environment")
    gpu = environment.get("gpu")
    runner = environment.get("v7_runner")
    if not all(isinstance(value, dict) for value in (git, cli, cudf, gpu, runner)):
        raise ValueError("environment identity sections are incomplete")
    assert isinstance(git, dict)
    assert isinstance(cli, dict)
    assert isinstance(cudf, dict)
    assert isinstance(gpu, dict)
    assert isinstance(runner, dict)

    commit = git.get("commit")
    runner_commit = runner.get("git_commit")
    if not isinstance(commit, str) or not GIT_COMMIT_RE.fullmatch(commit):
        raise ValueError("environment git commit is invalid")
    if runner_commit != commit:
        raise ValueError("runner git commit does not match captured git identity")
    cli_sha = cli.get("sha256")
    if not isinstance(cli_sha, str) or not SHA256_RE.fullmatch(cli_sha):
        raise ValueError("session CLI SHA256 is invalid")
    if runner.get("session_cli_sha256") != cli_sha:
        raise ValueError("runner session CLI SHA256 does not match captured binary")
    cli_path = cli.get("path")
    if not isinstance(cli_path, str) or not cli_path or runner.get("session_cli") != cli_path:
        raise ValueError("runner session CLI path does not match captured binary")
    if runner.get("matrix_sha256") != sha256_file(matrix_path):
        raise ValueError("runner matrix SHA256 does not match matrix.yml")
    if runner.get("matrix_payload") != matrix:
        raise ValueError("runner matrix payload does not match matrix.yml")
    if runner.get("matrix_payload_sha256") != _payload_sha256(matrix):
        raise ValueError("runner matrix payload SHA256 is invalid")

    cudf_name = cudf.get("name")
    if not isinstance(cudf_name, str) or not cudf_name or runner.get("cudf_env") != cudf_name:
        raise ValueError("cuDF environment provenance is inconsistent")
    cudf_details = cudf.get("details")
    if not isinstance(cudf_details, dict) or not cudf_details:
        raise ValueError("cuDF environment details are missing")
    dataset = runner.get("dataset")
    if not isinstance(dataset, str) or not dataset:
        raise ValueError("runner dataset provenance is missing")

    gpu_index = gpu.get("requested_index")
    visible = gpu.get("cuda_visible_devices")
    if (
        isinstance(gpu_index, bool)
        or not isinstance(gpu_index, int)
        or runner.get("gpu_index") != gpu_index
        or runner.get("cuda_visible_devices") != visible
        or not isinstance(visible, str)
        or not visible
    ):
        raise ValueError("GPU index/CUDA_VISIBLE_DEVICES provenance is inconsistent")
    devices = gpu.get("devices")
    if not isinstance(devices, list):
        raise ValueError("GPU device provenance is missing")
    selected = next(
        (
            device
            for device in devices
            if isinstance(device, dict) and device.get("index") == gpu_index
        ),
        None,
    )
    if not isinstance(selected, dict) or not selected.get("uuid"):
        raise ValueError("requested GPU UUID is missing")
    if not selected.get("driver_version") or not selected.get("name"):
        raise ValueError("requested GPU driver/name provenance is missing")
    return {
        "git_commit": commit,
        "session_cli": cli_path,
        "session_cli_sha256": cli_sha,
        "dataset": dataset,
        "cudf_env": cudf_name,
        "cudf_details": cudf_details,
        "gpu_index": gpu_index,
        "gpu_uuid": selected["uuid"],
        "gpu_name": selected["name"],
        "gpu_driver": selected["driver_version"],
        "cuda_visible_devices": visible,
    }


def _validate_commands(
    root: Path,
    matrix: dict[str, Any],
    configs: Sequence[Configuration],
    environment: dict[str, Any],
) -> None:
    runner = environment["v7_runner"]
    commands = [
        canonical_command(
            build_command(
                config,
                session_cli=Path(runner["session_cli"]),
                dataset=Path(runner["dataset"]),
                region=matrix["query"]["region"],
                date=matrix["query"]["date"],
                warmup=matrix["protocol"]["warmup"],
                repeat=matrix["protocol"]["repeat"],
                cudf_env=runner["cudf_env"],
            ),
            gpu_index=runner["gpu_index"],
        )
        for config in configs
    ]
    expected = "\n".join(commands) + "\n"
    actual = (root / "commands.txt").read_text(encoding="utf-8")
    if actual != expected:
        raise ValueError("commands.txt does not match the matrix execution order")


def _render_summaries(
    measured: Sequence[V7BenchmarkRecord], setups: Sequence[V7SetupRecord]
) -> tuple[bytes, bytes, list[dict[str, object]]]:
    rows = summarize_records(measured, setups)
    with tempfile.TemporaryDirectory(prefix="v7-summary-") as directory:
        temporary = Path(directory)
        csv_path = temporary / "summary.csv"
        markdown_path = temporary / "summary.md"
        write_summary(csv_path, rows)
        write_markdown(markdown_path, rows)
        return csv_path.read_bytes(), markdown_path.read_bytes(), rows


def _validate_bundle_data(
    root: Path,
    matrix_path: Path,
    dataset_manifest_path: Path,
    oracle_path: Path,
    correctness_path: Path,
) -> dict[str, object]:
    matrix = load_matrix(matrix_path)
    configs = configurations(matrix)
    dataset_payload, oracle_payload = _validate_sources(
        matrix, dataset_manifest_path, oracle_path
    )
    correctness = _load_json(correctness_path, "correctness report")
    _validate_correctness(correctness, matrix, configs)
    setups = read_setups(root / "setups.csv")
    warmups = read_requests(root / "warmups.csv")
    measured = read_requests(root / "raw.csv")
    _validate_coverage(root, matrix, configs, setups, warmups, measured)
    environment = _load_json(root / "environment.json", "environment")
    identity = _validate_environment(environment, matrix, matrix_path)
    _validate_commands(root, matrix, configs, environment)
    summary_csv, summary_markdown, summary_rows = _render_summaries(measured, setups)
    return {
        "matrix": matrix,
        "configs": configs,
        "identity": identity,
        "correctness": correctness,
        "dataset_payload": dataset_payload,
        "oracle_payload": oracle_payload,
        "setups": setups,
        "warmups": warmups,
        "measured": measured,
        "summary_csv": summary_csv,
        "summary_markdown": summary_markdown,
        "summary_rows": summary_rows,
    }


def _build_manifest(root: Path, context: dict[str, object]) -> dict[str, Any]:
    matrix = context["matrix"]
    configs = context["configs"]
    identity = context["identity"]
    correctness = context["correctness"]
    setups = context["setups"]
    warmups = context["warmups"]
    measured = context["measured"]
    summary_rows = context["summary_rows"]
    assert isinstance(matrix, dict)
    assert isinstance(configs, list)
    assert isinstance(identity, dict)
    assert isinstance(correctness, dict)
    assert isinstance(setups, list)
    assert isinstance(warmups, list)
    assert isinstance(measured, list)
    assert isinstance(summary_rows, list)
    backends = correctness["backends"]
    assert isinstance(backends, dict)
    return {
        "manifest_version": 1,
        "status": "complete",
        "experiment_id": matrix["experiment_id"],
        "identity": identity,
        "dataset": {
            "source_file": "dataset-manifest.json",
            "path": matrix["dataset"]["path"],
            "sha256": sha256_file(root / "dataset-manifest.json"),
            "manifest_sha256": matrix["dataset"]["manifest_sha256"],
            "scale_factor": matrix["dataset"]["scale_factor"],
        },
        "oracle": {
            "source_file": "oracle.json",
            "path": matrix["oracle"]["path"],
            "sha256": sha256_file(root / "oracle.json"),
            "result_hash": matrix["dataset"]["expected_hash"],
        },
        "matrix": {
            "source_file": "matrix.yml",
            "sha256": sha256_file(root / "matrix.yml"),
            "payload": matrix,
            "payload_sha256": _payload_sha256(matrix),
            "configuration_count": len(configs),
            "configurations": [asdict(config) for config in configs],
        },
        "protocol": {
            "warmup": matrix["protocol"]["warmup"],
            "repeat": matrix["protocol"]["repeat"],
            "expected_configurations": len(configs),
        },
        "correctness": {
            "source_file": "correctness.json",
            "sha256": sha256_file(root / "correctness.json"),
            "expected_hash": correctness["expected_hash"],
            "observed_hashes": correctness["observed_hashes"],
            "counts": {
                "configurations": len(configs),
                "correctness_backends": len(backends),
                "setups": len(setups),
                "successful_setups": sum(row.status == "ok" for row in setups),
                "warmups": len(warmups),
                "successful_warmups": sum(row.status == "ok" for row in warmups),
                "measured": len(measured),
                "successful_measured": sum(row.status == "ok" for row in measured),
            },
        },
        "summary": {
            "source_files": ["summary.csv", "summary.md"],
            "fields": summary_fieldnames(),
            "row_count": len(summary_rows),
        },
        "artifacts": artifact_checksums(root),
    }


def _require_bundle_files(root: Path, *, finalized: bool) -> None:
    required = REQUIRED_BUNDLE_FILES if finalized else REQUIRED_INPUT_FILES
    missing = sorted(name for name in required if not (root / name).is_file())
    if missing:
        raise ValueError(f"missing evidence artifacts: {missing}")
    logs = root / "logs"
    if logs.is_symlink() or not logs.is_dir():
        raise ValueError("missing regular logs directory")


def finalize_bundle(
    root: Path,
    *,
    matrix: Path,
    dataset_manifest: Path,
    oracle: Path,
    correctness: Path,
) -> dict[str, Any]:
    root = root.resolve()
    _require_bundle_files(root, finalized=False)
    matrix = matrix.resolve()
    dataset_manifest = dataset_manifest.resolve()
    oracle = oracle.resolve()
    correctness = correctness.resolve()
    _require_regular(matrix, "matrix")
    _require_regular(correctness, "correctness report")

    source_matrix = load_matrix(matrix)
    source_configs = configurations(source_matrix)
    _validate_sources(source_matrix, dataset_manifest, oracle)
    source_correctness = _load_json(correctness, "correctness report")
    _validate_correctness(source_correctness, source_matrix, source_configs)

    copies = {
        matrix: root / "matrix.yml",
        dataset_manifest: root / "dataset-manifest.json",
        oracle: root / "oracle.json",
        correctness: root / "correctness.json",
    }
    for source, destination in copies.items():
        shutil.copyfile(source, destination)

    context = _validate_bundle_data(
        root,
        root / "matrix.yml",
        root / "dataset-manifest.json",
        root / "oracle.json",
        root / "correctness.json",
    )
    (root / "summary.csv").write_bytes(context["summary_csv"])  # type: ignore[arg-type]
    (root / "summary.md").write_bytes(context["summary_markdown"])  # type: ignore[arg-type]

    manifest = _build_manifest(root, context)
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (root / "manifest.sha256").write_text(
        f"{sha256_file(manifest_path)}  manifest.json\n", encoding="ascii"
    )
    return manifest


def audit_bundle(root: Path) -> dict[str, object]:
    root = root.resolve()
    errors: list[str] = []
    try:
        _require_bundle_files(root, finalized=True)
    except (OSError, ValueError) as exc:
        errors.append(str(exc))

    manifest: dict[str, Any] | None = None
    try:
        manifest = _load_json(root / "manifest.json", "manifest")
        expected_digest = f"{sha256_file(root / 'manifest.json')}  manifest.json"
        actual_digest = (root / "manifest.sha256").read_text(encoding="ascii").strip()
        if actual_digest != expected_digest:
            errors.append("manifest.sha256 does not match manifest.json")
    except (OSError, UnicodeError, ValueError) as exc:
        errors.append(str(exc))

    if manifest is not None:
        try:
            expected_artifacts = manifest.get("artifacts")
            if not isinstance(expected_artifacts, dict):
                raise ValueError("manifest artifacts must be an object")
            actual_artifacts = artifact_checksums(root)
            mismatches = sorted(
                name
                for name in set(expected_artifacts) | set(actual_artifacts)
                if expected_artifacts.get(name) != actual_artifacts.get(name)
            )
            if mismatches:
                errors.append(f"artifact checksum/file-set mismatch: {mismatches}")
        except (OSError, ValueError) as exc:
            errors.append(str(exc))

    context: dict[str, object] | None = None
    try:
        context = _validate_bundle_data(
            root,
            root / "matrix.yml",
            root / "dataset-manifest.json",
            root / "oracle.json",
            root / "correctness.json",
        )
        if (root / "summary.csv").read_bytes() != context["summary_csv"]:
            errors.append("summary.csv does not match the summary recomputed from raw.csv")
        if (root / "summary.md").read_bytes() != context["summary_markdown"]:
            errors.append("summary.md does not match the summary recomputed from raw.csv")
    except (OSError, ValueError) as exc:
        errors.append(str(exc))

    if manifest is not None and context is not None:
        try:
            expected_manifest = _build_manifest(root, context)
            if manifest != expected_manifest:
                errors.append("manifest does not match the fully recomputed bundle declaration")
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            errors.append(f"manifest semantic audit failed: {exc}")
    return {"ok": not errors, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize or audit a V7 evidence bundle")
    subparsers = parser.add_subparsers(dest="command", required=True)
    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--directory", type=Path, required=True)
    finalize.add_argument("--matrix", type=Path, required=True)
    finalize.add_argument("--dataset-manifest", type=Path, required=True)
    finalize.add_argument("--oracle", type=Path, required=True)
    finalize.add_argument("--correctness", type=Path, required=True)
    audit = subparsers.add_parser("audit")
    audit.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "finalize":
        manifest = finalize_bundle(
            args.directory,
            matrix=args.matrix,
            dataset_manifest=args.dataset_manifest,
            oracle=args.oracle,
            correctness=args.correctness,
        )
        print(json.dumps({"ok": True, "status": manifest["status"]}, sort_keys=True))
        return 0
    report = audit_bundle(args.directory)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
