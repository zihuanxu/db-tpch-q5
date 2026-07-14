#!/usr/bin/env python3
"""Export a compact publication copy of an audited V7 profiler bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path

try:
    from scripts.parse_ncu_csv import parse_ncu_csv
    from scripts.parse_nsys_stats import parse_nsys_csv
except ModuleNotFoundError:
    from parse_ncu_csv import parse_ncu_csv
    from parse_nsys_stats import parse_nsys_csv


SCHEMA = "memq5.v7.compact-profiler-evidence"
SCHEMA_VERSION = 1
SCALES = ("1", "10")
ENGINES = ("copy", "managed", "mapped", "hybrid-fixed", "hybrid-auto")
IDENTITY_FIELDS = (
    "scale_factor",
    "engine",
    "cpu_ratio",
    "gpu_ratio",
    "session_commit",
    "dataset_manifest",
    "evidence_manifest",
    "oracle_hash",
    "result_hash",
    "gpu_uuid",
    "execution",
)
NSYS_REQUIRED_FILES = (
    "profile.nsys-rep",
    "stats_cuda_api_sum.csv",
    "stats_cuda_gpu_kern_sum.csv",
    "stats_cuda_gpu_mem_time_sum.csv",
    "stats_nvtx_sum.csv",
    "profile.stdout.log",
    "profile.stderr.log",
    "stats.stdout.log",
    "stats.stderr.log",
)
NCU_REQUIRED_FILES = (
    "report.csv",
    "selected_metrics.json",
    "profile.stdout.log",
    "profile.stderr.log",
)
NSYS_OPTIONAL_FILES = (
    "profile.tool.stdout.log",
    "orchestrator.stdout.log",
    "orchestrator.stderr.log",
)
NCU_OPTIONAL_FILES = (
    "orchestrator.stdout.log",
    "orchestrator.stderr.log",
)
SHA256_RE = re.compile(r"[0-9a-f]{64}")
PROFILE_ID_RE = re.compile(
    r"sf(1|10)-(copy|managed|mapped|hybrid-fixed|hybrid-auto)"
)
READ_BLOCK_BYTES = 1024 * 1024


def _open_regular(path: Path, label: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot open {label}: {path}: {exc}") from exc
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ValueError(f"{label} is not a regular file: {path}")
    return descriptor


def _read_bytes(path: Path, label: str, *, limit: int | None = None) -> bytearray:
    data = bytearray()
    descriptor = _open_regular(path, label)
    with os.fdopen(descriptor, "rb") as handle:
        for block in iter(lambda: handle.read(READ_BLOCK_BYTES), b""):
            data.extend(block)
            if limit is not None and len(data) > limit:
                raise ValueError(f"{label} exceeds {limit} bytes: {path}")
    return data


def _read_hashed_bytes(path: Path, label: str) -> tuple[bytearray, str]:
    data = bytearray()
    digest = hashlib.sha256()
    descriptor = _open_regular(path, label)
    with os.fdopen(descriptor, "rb") as handle:
        for block in iter(lambda: handle.read(READ_BLOCK_BYTES), b""):
            digest.update(block)
            data.extend(block)
    return data, digest.hexdigest()


def _parse_json(data: bytearray, path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor = _open_regular(path, "artifact")
    with os.fdopen(descriptor, "rb") as handle:
        for block in iter(lambda: handle.read(READ_BLOCK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path, label: str) -> dict[str, object]:
    return _parse_json(_read_bytes(path, label), path, label)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _command(value: object, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise ValueError(f"{label} must be a non-empty string array")
    return list(value)


def _source_path(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe {label}: {value}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"missing or unsafe {label}: {value}")
    return path


def _artifact_expected(
    root: Path, artifacts: dict[str, object], path: Path, label: str
) -> str:
    relative = path.relative_to(root).as_posix()
    expected = artifacts.get(relative)
    if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
        raise ValueError(f"source manifest does not cover {label}: {relative}")
    return expected


def _read_verified_json(
    root: Path, artifacts: dict[str, object], path: Path, label: str
) -> tuple[dict[str, object], str]:
    expected = _artifact_expected(root, artifacts, path, label)
    data, actual = _read_hashed_bytes(path, label)
    if actual != expected:
        relative = path.relative_to(root).as_posix()
        raise ValueError(f"source manifest artifact checksum mismatch: {relative}")
    return _parse_json(data, path, label), actual


def _validate_manifest_checksum(root: Path) -> tuple[dict[str, object], str]:
    manifest_path = root / "manifest.json"
    digest_path = root / "manifest.sha256"
    try:
        digest_line = bytes(
            _read_bytes(digest_path, "source manifest.sha256", limit=256)
        ).decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise ValueError(f"invalid source manifest.sha256: {exc}") from exc
    match = re.fullmatch(r"([0-9a-f]{64})  manifest\.json", digest_line)
    if match is None:
        raise ValueError("invalid source manifest.sha256 line")
    expected_digest = match.group(1)
    manifest_bytes, digest = _read_hashed_bytes(manifest_path, "source manifest.json")
    if digest != expected_digest:
        raise ValueError("source manifest.sha256 does not match manifest.json")
    manifest = _parse_json(manifest_bytes, manifest_path, "source manifest.json")
    if manifest.get("manifest_version") != 3 or manifest.get("status") != "complete":
        raise ValueError("source manifest.json is not a complete version 3 manifest")
    return manifest, digest


def _ratio(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid {label}")
    ratio = float(value)
    if ratio < 0.0 or ratio > 1.0:
        raise ValueError(f"invalid {label}")
    return ratio


def _logical_profile(profile: dict[str, object]) -> dict[str, object]:
    scale = profile.get("scale_factor")
    engine = profile.get("engine")
    if scale not in SCALES or engine not in ENGINES:
        raise ValueError(f"invalid canonical profile identity: scale={scale!r} engine={engine!r}")
    cpu_ratio = _ratio(profile.get("cpu_ratio"), "cpu_ratio")
    gpu_ratio = _ratio(profile.get("gpu_ratio"), "gpu_ratio")
    if abs(cpu_ratio + gpu_ratio - 1.0) > 1e-12:
        raise ValueError("invalid canonical profile CPU/GPU ratio pair")
    if engine in {"copy", "managed", "mapped"} and (cpu_ratio, gpu_ratio) != (0.0, 1.0):
        raise ValueError(f"invalid ratio for canonical {engine} profile")
    if engine == "hybrid-fixed" and abs(cpu_ratio - 0.5) > 1e-12:
        raise ValueError("invalid ratio for canonical hybrid-fixed profile")
    if engine == "hybrid-auto" and not 0.0 < cpu_ratio < 1.0:
        raise ValueError("invalid ratio for canonical hybrid-auto profile")
    return {
        "scale_factor": scale,
        "engine": engine,
        "cpu_ratio": cpu_ratio,
        "gpu_ratio": gpu_ratio,
    }


def _validate_coverage(index: dict[str, object]) -> list[dict[str, object]]:
    if index.get("schema_version") != 3 or index.get("status") != "complete":
        raise ValueError("profiles.json is not a complete schema version 3 index")
    profiles_value = index.get("profiles")
    if not isinstance(profiles_value, list) or any(
        not isinstance(profile, dict) for profile in profiles_value
    ):
        raise ValueError("profiles.json profiles must be an object array")
    profiles = list(profiles_value)
    expected_pairs = {(scale, engine) for scale in SCALES for engine in ENGINES}
    actual_pairs: set[tuple[object, object]] = set()
    ids: set[str] = set()
    for profile in profiles:
        logical = _logical_profile(profile)
        pair = (logical["scale_factor"], logical["engine"])
        profile_id = profile.get("id")
        expected_id = f"sf{logical['scale_factor']}-{logical['engine']}"
        if (
            not isinstance(profile_id, str)
            or PROFILE_ID_RE.fullmatch(profile_id) is None
            or profile_id != expected_id
        ):
            raise ValueError(
                f"profile id must be a safe canonical profile id: expected {expected_id!r}"
            )
        if (
            pair in actual_pairs
            or profile_id in ids
        ):
            raise ValueError("source does not have unique canonical 10-profile coverage")
        actual_pairs.add(pair)
        ids.add(profile_id)
    if len(profiles) != 10 or actual_pairs != expected_pairs:
        raise ValueError(
            "source does not have canonical 10-profile coverage "
            f"missing={sorted(expected_pairs - actual_pairs)} "
            f"extra={sorted(actual_pairs - expected_pairs)}"
        )
    declared = index.get("expected_profiles")
    if not isinstance(declared, list) or len(declared) != 10:
        raise ValueError("expected_profiles does not declare canonical 10-profile coverage")
    try:
        declared_pairs = {
            (logical["scale_factor"], logical["engine"])
            for item in declared
            if isinstance(item, dict)
            for logical in [_logical_profile(item)]
        }
    except ValueError as exc:
        raise ValueError(f"invalid expected_profiles canonical coverage: {exc}") from exc
    if declared_pairs != expected_pairs:
        raise ValueError("expected_profiles does not declare canonical 10-profile coverage")
    engine_order = {engine: index for index, engine in enumerate(ENGINES)}
    return sorted(
        profiles,
        key=lambda item: (int(str(item["scale_factor"])), engine_order[str(item["engine"])]),
    )


def _identity_assertion(profile: dict[str, object], label: str) -> dict[str, object]:
    missing = [field for field in IDENTITY_FIELDS if field not in profile]
    if missing:
        raise ValueError(f"{label} is missing identity fields: {missing}")
    return {field: profile[field] for field in IDENTITY_FIELDS}


def _manifest_profile_assertions(
    profiles_value: object, label: str
) -> dict[str, dict[str, object]]:
    if not isinstance(profiles_value, list) or len(profiles_value) != 10:
        raise ValueError(f"{label} must contain ten profiles")
    assertions: dict[str, dict[str, object]] = {}
    for position, value in enumerate(profiles_value):
        if not isinstance(value, dict):
            raise ValueError(f"{label}[{position}] must be an object")
        profile_id = value.get("id")
        if not isinstance(profile_id, str) or profile_id in assertions:
            raise ValueError(f"{label} contains an invalid or duplicate profile id")
        assertions[profile_id] = _identity_assertion(value, f"{label}[{position}]")
    return assertions


def _validate_source(
    root: Path,
) -> tuple[dict[str, object], str, dict[str, object], list[dict[str, object]]]:
    manifest, manifest_digest = _validate_manifest_checksum(root)
    artifacts_value = manifest.get("artifacts")
    if not isinstance(artifacts_value, dict):
        raise ValueError("source manifest artifacts must be an object")
    artifacts: dict[str, object] = artifacts_value
    index_path = root / "profiles.json"
    orchestration_path = root / "orchestration.json"
    index, index_digest = _read_verified_json(
        root, artifacts, index_path, "profiles.json"
    )
    profiles = _validate_coverage(index)
    orchestration, _ = _read_verified_json(
        root, artifacts, orchestration_path, "orchestration.json"
    )
    if orchestration.get("schema_version") != 1 or orchestration.get("status") != "complete":
        raise ValueError("orchestration.json is not complete schema version 1")
    profile_ids = {str(profile["id"]) for profile in profiles}
    completed = orchestration.get("completed_profiles")
    if (
        orchestration.get("profile_count") != 10
        or not isinstance(completed, list)
        or len(completed) != len(profile_ids)
        or any(not isinstance(profile_id, str) for profile_id in completed)
        or set(completed) != profile_ids
    ):
        raise ValueError(
            "orchestration.json does not cover all canonical profiles exactly once"
        )
    source_index = manifest.get("source_index")
    if (
        not isinstance(source_index, dict)
        or source_index.get("path") != "profiles.json"
        or source_index.get("sha256") != index_digest
    ):
        raise ValueError("source manifest profiles.json binding is invalid")
    if manifest.get("profile_count") != 10:
        raise ValueError("source manifest does not contain ten complete profiles")
    index_assertions = {
        str(profile["id"]): _identity_assertion(profile, f"profiles.json {profile['id']}")
        for profile in profiles
    }
    manifest_assertions = _manifest_profile_assertions(
        manifest.get("profiles"), "source manifest profiles"
    )
    if manifest_assertions != index_assertions:
        raise ValueError("source manifest profiles do not match profiles.json")
    return manifest, manifest_digest, orchestration, profiles


def _metadata(
    root: Path,
    artifacts: dict[str, object],
    profile: dict[str, object],
    tool: str,
) -> tuple[Path, dict[str, object]]:
    config = profile.get(tool)
    if not isinstance(config, dict):
        raise ValueError(f"{profile['id']} has no {tool} collector configuration")
    if tool == "ncu" and config.get("status") != "ok":
        raise ValueError(f"{profile['id']} has no successful NCU report")
    path = _source_path(root, config.get("metadata_path"), f"{tool} metadata_path")
    metadata, _ = _read_verified_json(
        root, artifacts, path, f"{tool} collector metadata"
    )
    identity = metadata.get("metadata")
    if not isinstance(identity, dict) or any(
        identity.get(field) != profile.get(field) for field in IDENTITY_FIELDS
    ):
        raise ValueError(f"{profile['id']} {tool} collector identity mismatch")
    return path, metadata


def _tool_version(metadata: dict[str, object], tool: str) -> str:
    versions = metadata.get("tool_versions")
    version = versions.get(tool) if isinstance(versions, dict) else None
    if not isinstance(version, str) or not version:
        raise ValueError(f"missing {tool} tool version")
    return version


def _copy_verified_file(
    source_root: Path,
    output_root: Path,
    artifacts: dict[str, object],
    source: Path,
    relative: Path,
) -> str:
    expected = _artifact_expected(
        source_root, artifacts, source, f"compact evidence {source.name}"
    )
    destination = output_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_descriptor = _open_regular(source, f"compact evidence {source.name}")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        destination_descriptor = os.open(destination, flags, 0o644)
    except Exception:
        os.close(source_descriptor)
        raise
    digest = hashlib.sha256()
    with os.fdopen(source_descriptor, "rb") as source_handle, os.fdopen(
        destination_descriptor, "wb"
    ) as destination_handle:
        for block in iter(lambda: source_handle.read(READ_BLOCK_BYTES), b""):
            digest.update(block)
            destination_handle.write(block)
    actual = digest.hexdigest()
    if actual != expected:
        source_relative = source.relative_to(source_root).as_posix()
        raise ValueError(f"source manifest artifact checksum mismatch: {source_relative}")
    destination.chmod(0o644)
    return actual


def _copy_files(
    source_root: Path,
    output_root: Path,
    artifacts: dict[str, object],
    source_directory: Path,
    destination_relative: Path,
    required_names: tuple[str, ...],
    optional_names: tuple[str, ...],
) -> dict[str, str]:
    names = list(required_names)
    names.extend(name for name in optional_names if (source_directory / name).is_file())
    copied: dict[str, str] = {}
    for name in names:
        source = source_directory / name
        if not source.is_file():
            raise ValueError(f"missing required compact evidence file: {source}")
        relative = destination_relative / name
        copied[relative.as_posix()] = _copy_verified_file(
            source_root, output_root, artifacts, source, relative
        )
    return dict(sorted(copied.items()))


def _profile_record(
    source_root: Path,
    output_root: Path,
    artifacts: dict[str, object],
    profile: dict[str, object],
) -> dict[str, object]:
    profile_id = str(profile["id"])
    identity = {field: profile[field] for field in IDENTITY_FIELDS}
    app_command = _command(profile.get("command"), f"{profile_id}.command")

    nsys_path, nsys_metadata = _metadata(source_root, artifacts, profile, "nsys")
    nsys_destination = Path("captures") / profile_id / "nsys"
    nsys_copied = _copy_files(
        source_root,
        output_root,
        artifacts,
        nsys_path.parent,
        nsys_destination,
        NSYS_REQUIRED_FILES,
        NSYS_OPTIONAL_FILES,
    )
    staged_nsys = output_root / nsys_destination
    nvtx_rows = parse_nsys_csv(staged_nsys / "stats_nvtx_sum.csv")
    observed_ranges = sorted(
        {
            str(row["name"])
            for row in nvtx_rows
            if row.get("kind") == "range" and row.get("name")
        }
    )
    memory_rows = parse_nsys_csv(staged_nsys / "stats_cuda_gpu_mem_time_sum.csv")
    kernel_rows = parse_nsys_csv(staged_nsys / "stats_cuda_gpu_kern_sum.csv")
    q5_kernel_total = sum(
        float(row["total_ns"])
        for row in kernel_rows
        if "q5_kernel" in str(row.get("name", ""))
        and isinstance(row.get("total_ns"), (int, float))
    )
    if q5_kernel_total <= 0:
        raise ValueError(f"{profile_id} has no positive q5 kernel total time")
    if q5_kernel_total.is_integer():
        q5_kernel_total = int(q5_kernel_total)

    ncu_path, ncu_metadata = _metadata(source_root, artifacts, profile, "ncu")
    ncu_destination = Path("captures") / profile_id / "ncu"
    ncu_copied = _copy_files(
        source_root,
        output_root,
        artifacts,
        ncu_path.parent,
        ncu_destination,
        NCU_REQUIRED_FILES,
        NCU_OPTIONAL_FILES,
    )
    staged_ncu = output_root / ncu_destination
    selected_value = ncu_metadata.get("selected_metrics")
    if not isinstance(selected_value, dict) or not selected_value or any(
        not isinstance(role, str) or not isinstance(metric, str)
        for role, metric in selected_value.items()
    ):
        raise ValueError(f"{profile_id} has invalid selected NCU metrics")
    selected_metrics: dict[str, str] = dict(selected_value)
    selected_file = _load_json(staged_ncu / "selected_metrics.json", "selected_metrics.json")
    if selected_file != selected_metrics:
        raise ValueError(f"{profile_id} selected_metrics.json does not match collector metadata")
    parsed_ncu = parse_ncu_csv(staged_ncu / "report.csv", "q5_kernel")
    parsed_metrics = parsed_ncu.get("metrics")
    if not isinstance(parsed_metrics, dict) or any(
        metric not in parsed_metrics for metric in selected_metrics.values()
    ):
        raise ValueError(f"{profile_id} NCU report does not contain all selected metrics")
    replay = ncu_metadata.get("replay")
    replay_mode = replay.get("mode") if isinstance(replay, dict) else None
    if (
        replay_mode not in {"application", "kernel"}
        or replay.get("return_code") != 0
        or replay.get("succeeded") is not True
    ):
        raise ValueError(f"{profile_id} has invalid NCU replay provenance")
    gpu = ncu_metadata.get("gpu")
    if not isinstance(gpu, dict) or gpu.get("status") != "ok":
        raise ValueError(f"{profile_id} has invalid NCU GPU provenance")
    if gpu.get("uuid") != profile.get("gpu_uuid"):
        raise ValueError(
            f"{profile_id} NCU GPU UUID does not match profile identity"
        )

    return {
        "id": profile_id,
        "identity": identity,
        "app_command": app_command,
        "copied_files": dict(sorted({**nsys_copied, **ncu_copied}.items())),
        "nsys": {
            "tool_version": _tool_version(nsys_metadata, "nsys"),
            "profile_command": _command(
                nsys_metadata.get("profile_command"), f"{profile_id} NSYS profile_command"
            ),
            "observed_nvtx_ranges": observed_ranges,
            "cuda_memory_operation_rows": memory_rows,
            "q5_kernel_total_time_ns": q5_kernel_total,
        },
        "ncu": {
            "tool_version": _tool_version(ncu_metadata, "ncu"),
            "profile_command": _command(
                ncu_metadata.get("profile_command"), f"{profile_id} NCU profile_command"
            ),
            "replay_mode": replay_mode,
            "gpu": gpu,
            "selected_metrics": selected_metrics,
            "kernel_name": parsed_ncu["kernel_name"],
            "metrics": parsed_metrics,
            "selected_metric_values": {
                role: parsed_metrics[metric] for role, metric in selected_metrics.items()
            },
        },
    }


def _readme() -> str:
    return """# V7 compact profiler evidence

This directory is a compact publication copy of the audited full V7 profiler bundle.
It preserves the raw NSYS report, four NSYS CSV exports, selected NCU report data,
collector logs, parsed summary values, and checksums needed to inspect the evidence.

The following full-bundle content is intentionally omitted:

- `datasets/` and `evidence/` inputs.
- `profile.sqlite` and SQLite sidecars.
- `supported_metrics.txt`.
- The NCU metadata metric universe.
- Large collector metadata.json files.

Audit the retained full bundle before publication with exactly:

```text
python3 scripts/v7_profiler_bundle.py audit --directory FULL_BUNDLE
```
"""


def _write_checksums(root: Path) -> None:
    paths = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.name != "checksums.sha256"
    )
    lines = [f"{_sha256(path)}  {path.relative_to(root).as_posix()}" for path in paths]
    (root / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="ascii")


def _normalize_modes(root: Path) -> None:
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            raise ValueError(f"symlink is forbidden in compact output: {path}")
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            path.chmod(0o644)
        else:
            raise ValueError(f"unsupported compact output path: {path}")


def _build(source: Path, output: Path) -> None:
    manifest, manifest_digest, orchestration, profiles = _validate_source(source)
    artifacts_value = manifest["artifacts"]
    assert isinstance(artifacts_value, dict)
    records = [
        _profile_record(source, output, artifacts_value, profile) for profile in profiles
    ]
    coverage = [_logical_profile(profile) for profile in profiles]
    summary = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "source_manifest_sha256": manifest_digest,
        "source_bundle_name": source.name,
        "canonical_profile_count": len(profiles),
        "canonical_coverage": coverage,
        "source_orchestration_status": orchestration["status"],
        "profiles": records,
    }
    _write_json(output / "summary.json", summary)
    (output / "README.md").write_text(_readme(), encoding="utf-8")
    _write_checksums(output)
    _normalize_modes(output)


def export(source_value: Path, output_value: Path) -> None:
    if not source_value.is_dir():
        raise ValueError(f"source bundle directory does not exist: {source_value}")
    source = source_value.resolve()
    requested_output = Path(os.path.abspath(output_value))
    if requested_output.is_symlink():
        raise ValueError(f"output directory must not be a symlink: {requested_output}")
    output = requested_output.parent.resolve() / requested_output.name
    if output.is_symlink():
        raise ValueError(f"output directory must not be a symlink: {output}")
    if output == source or output.is_relative_to(source):
        raise ValueError("output directory must be outside the source bundle")
    if output.exists():
        if not output.is_dir():
            raise ValueError(f"output exists and is not a directory: {output}")
        if any(output.iterdir()):
            raise ValueError(f"output directory must not exist or must be empty: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        _build(source, staging)
        if output.exists():
            output.rmdir()
        os.replace(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        export(args.source, args.output)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
