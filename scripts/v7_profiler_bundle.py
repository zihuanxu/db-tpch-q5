#!/usr/bin/env python3
"""Finalize and audit V7 Nsight profiler evidence bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.parse_ncu_csv import parse_ncu_csv
    from scripts.parse_nsys_stats import parse_nsys_csv, validate_ranges
except ModuleNotFoundError:
    from parse_ncu_csv import parse_ncu_csv
    from parse_nsys_stats import parse_nsys_csv, validate_ranges


NSYS_EXPORTS = (
    "cuda_api_sum",
    "cuda_gpu_kern_sum",
    "cuda_gpu_mem_time_sum",
    "nvtx_sum",
)
NCU_METRIC_ROLES = {
    "duration",
    "dram_read_bytes",
    "dram_throughput",
    "sm_throughput",
    "achieved_occupancy",
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_ORACLE_HASH_RE = re.compile(r"[0-9a-f]{16}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
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


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"missing {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {label} JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _command(value: object, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise ValueError(f"{label} must be a non-empty exact command array")
    return list(value)


def _command_has_suffix(wrapper: list[str], command: list[str]) -> bool:
    return len(wrapper) >= len(command) and wrapper[-len(command) :] == command


def _inside(root: Path, value: object, label: str, *, require_file: bool = True) -> Path:
    text = _text(value, label)
    relative = Path(text)
    if relative.is_absolute():
        raise ValueError(f"{label} must be relative to the profiler bundle")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the profiler bundle: {text}") from exc
    if require_file and not resolved.is_file():
        raise ValueError(f"missing {label}: {text}")
    return resolved


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def _artifact(root: Path, path: Path) -> dict[str, object]:
    return {
        "path": _relative(root, path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _collector_files(
    root: Path,
    metadata_path: Path,
    metadata: dict[str, object],
    label: str,
) -> dict[str, Path]:
    entries = metadata.get("files")
    if not isinstance(entries, dict) or not entries:
        raise ValueError(f"{label} collector files must be a non-empty object")
    paths: dict[str, Path] = {}
    for name, raw_entry in entries.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"{label} collector file name must be non-empty")
        if not isinstance(raw_entry, dict):
            raise ValueError(f"{label} collector file entry must be an object: {name}")
        relative = Path(name)
        if relative.is_absolute():
            raise ValueError(f"{label} collector file path must be relative: {name}")
        path = (metadata_path.parent / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"{label} collector file escapes bundle: {name}") from exc
        paths[name] = path
    return paths


def _verify_collector_files(
    paths: dict[str, Path], metadata: dict[str, object], label: str
) -> None:
    entries = metadata["files"]
    assert isinstance(entries, dict)
    for name, path in paths.items():
        if not path.is_file():
            raise ValueError(f"missing {label} collector artifact: {name}")
        entry = entries[name]
        assert isinstance(entry, dict)
        expected_hash = entry.get("sha256")
        if not isinstance(expected_hash, str) or not _SHA256_RE.fullmatch(expected_hash):
            raise ValueError(f"{label} collector artifact has invalid sha256: {name}")
        actual_hash = sha256_file(path)
        if actual_hash != expected_hash:
            raise ValueError(f"{label} collector checksum mismatch: {name}")
        expected_bytes = entry.get("bytes")
        if not isinstance(expected_bytes, int) or expected_bytes < 0:
            raise ValueError(f"{label} collector artifact has invalid byte count: {name}")
        if path.stat().st_size != expected_bytes:
            raise ValueError(f"{label} collector byte count mismatch: {name}")


def _tool_version(metadata: dict[str, object], tool: str) -> str:
    versions = metadata.get("tool_versions")
    if not isinstance(versions, dict):
        raise ValueError(f"{tool} tool version is required")
    return _text(versions.get(tool), f"{tool} tool version")


def _nsys_provenance(metadata: dict[str, object]) -> dict[str, object]:
    provenances = metadata.get("tool_version_provenance")
    if not isinstance(provenances, dict) or not isinstance(provenances.get("nsys"), dict):
        raise ValueError("nsys tool version provenance is required")
    provenance = provenances["nsys"]
    assert isinstance(provenance, dict)
    _command(provenance.get("command"), "nsys tool version provenance command")
    if not isinstance(provenance.get("return_code"), int):
        raise ValueError("nsys tool version provenance return_code must be an integer")
    return dict(provenance)


def _export_path(
    paths: dict[str, Path], report: str, *, require_exists: bool = True
) -> Path:
    candidates = [
        path
        for name, path in paths.items()
        if name.lower().endswith(".csv") and report in Path(name).stem.lower()
    ]
    if len(candidates) != 1 or (require_exists and not candidates[0].is_file()):
        raise ValueError(f"missing Nsight Systems export: {report}")
    return candidates[0]


def _compile_nsys(
    root: Path,
    profile: dict[str, object],
    command: list[str],
    required_ranges: set[str],
) -> dict[str, object]:
    config = profile.get("nsys")
    if not isinstance(config, dict):
        raise ValueError("nsys must be an object")
    metadata_path = _inside(root, config.get("metadata_path"), "nsys.metadata_path")
    metadata = _load_json(metadata_path, "Nsight Systems metadata")
    profile_command = _command(metadata.get("profile_command"), "nsys profile command")
    if not _command_has_suffix(profile_command, command):
        raise ValueError("nsys profile command does not contain the exact command array")
    stats_commands = metadata.get("stats_commands")
    if (
        not isinstance(stats_commands, list)
        or not stats_commands
        or any(not isinstance(item, list) for item in stats_commands)
    ):
        raise ValueError("nsys stats command provenance is required")
    normalized_stats_commands = [
        _command(item, "nsys stats command") for item in stats_commands
    ]
    if metadata.get("return_code") != 0:
        raise ValueError("Nsight Systems profile did not succeed")
    stats = metadata.get("stats")
    if (
        not isinstance(stats, list)
        or not stats
        or any(not isinstance(item, dict) or item.get("return_code") != 0 for item in stats)
    ):
        raise ValueError("Nsight Systems export did not succeed")
    version = _tool_version(metadata, "nsys")
    provenance = _nsys_provenance(metadata)
    paths = _collector_files(root, metadata_path, metadata, "nsys")

    raw_reports = [path for name, path in paths.items() if name.lower().endswith(".nsys-rep")]
    if len(raw_reports) != 1 or not raw_reports[0].is_file():
        raise ValueError("missing Nsight Systems raw report")
    exports = {report: _export_path(paths, report) for report in NSYS_EXPORTS}
    _verify_collector_files(paths, metadata, "nsys")
    if raw_reports[0].stat().st_size == 0:
        raise ValueError("Nsight Systems raw report is empty")

    parsed: dict[str, list[dict[str, object]]] = {}
    for report, path in exports.items():
        try:
            parsed[report] = parse_nsys_csv(path)
        except (OSError, ValueError) as exc:
            raise ValueError(f"invalid Nsight Systems export {report}: {exc}") from exc
    range_errors = validate_ranges(parsed["nvtx_sum"], required_ranges)
    if range_errors:
        raise ValueError("; ".join(range_errors))
    kernel_rows = [
        row
        for row in parsed["cuda_gpu_kern_sum"]
        if row.get("kind") == "kernel"
        and isinstance(row.get("total_ns"), (int, float))
        and row["total_ns"] > 0
    ]
    if not kernel_rows:
        raise ValueError("Nsight Systems evidence has no positive CUDA kernel activity")
    observed_ranges = sorted(
        {
            str(row.get("name"))
            for row in parsed["nvtx_sum"]
            if row.get("kind") == "range" and row.get("name")
        }
    )
    return {
        "status": "ok",
        "metadata_path": _relative(root, metadata_path),
        "metadata_sha256": sha256_file(metadata_path),
        "profile_command": profile_command,
        "stats_commands": normalized_stats_commands,
        "tool_version": version,
        "tool_version_provenance": provenance,
        "raw_report": _artifact(root, raw_reports[0]),
        "exports": {name: _artifact(root, path) for name, path in sorted(exports.items())},
        "required_nvtx_ranges": sorted(required_ranges),
        "observed_nvtx_ranges": observed_ranges,
        "cuda_kernel_rows": len(kernel_rows),
    }


def _selected_metrics(value: object, *, unavailable: bool = False) -> dict[str, str]:
    label = "unavailable ncu selected metrics" if unavailable else "selected NCU metrics"
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    if value and set(value) != NCU_METRIC_ROLES:
        raise ValueError(
            f"{label} must contain exactly: {', '.join(sorted(NCU_METRIC_ROLES))}"
        )
    if not unavailable and not value:
        raise ValueError("selected NCU metrics must not be empty")
    if any(not isinstance(metric, str) or not metric for metric in value.values()):
        raise ValueError(f"{label} values must be non-empty metric names")
    if len(set(value.values())) != len(value):
        raise ValueError(f"{label} must select distinct metric names")
    return dict(value)


def _required_collector_artifact(
    paths: dict[str, Path], name: str, label: str
) -> Path:
    path = paths.get(name)
    if path is None or not path.is_file():
        raise ValueError(f"missing {label}: {name}")
    return path


def _compile_ncu_ok(
    root: Path, config: dict[str, object], command: list[str], gpu_uuid: str
) -> dict[str, object]:
    metadata_path = _inside(root, config.get("metadata_path"), "ncu.metadata_path")
    metadata = _load_json(metadata_path, "Nsight Compute metadata")
    selected = _selected_metrics(metadata.get("selected_metrics"))
    version = _tool_version(metadata, "ncu")
    query_command = _command(metadata.get("query_command"), "ncu metric query command")
    profile_command = _command(metadata.get("profile_command"), "ncu profile command")
    if not _command_has_suffix(profile_command, command):
        raise ValueError("ncu profile command does not contain the exact command array")
    gpu = metadata.get("gpu")
    if not isinstance(gpu, dict) or gpu.get("status") != "ok":
        raise ValueError("ncu GPU provenance must have status ok")
    if gpu.get("uuid") != gpu_uuid:
        raise ValueError("ncu GPU UUID does not match the profile GPU UUID")
    gpu_query_command = _command(gpu.get("query_command"), "ncu GPU query command")
    replay = metadata.get("replay")
    if (
        metadata.get("return_code") != 0
        or not isinstance(replay, dict)
        or replay.get("return_code") != 0
        or replay.get("succeeded") is not True
    ):
        raise ValueError("Nsight Compute replay did not succeed")
    paths = _collector_files(root, metadata_path, metadata, "ncu")
    selected_path = _required_collector_artifact(
        paths, "selected_metrics.json", "Nsight Compute selected-metric export"
    )
    supported_path = _required_collector_artifact(
        paths, "supported_metrics.txt", "Nsight Compute supported-metric export"
    )
    report = metadata.get("report")
    if not isinstance(report, dict):
        raise ValueError("Nsight Compute report provenance is required")
    report_name = _text(report.get("path"), "Nsight Compute report path")
    report_path = _required_collector_artifact(paths, report_name, "Nsight Compute report")
    _verify_collector_files(paths, metadata, "ncu")
    if report.get("sha256") != sha256_file(report_path):
        raise ValueError("Nsight Compute report checksum mismatch")
    selected_export = _load_json(selected_path, "Nsight Compute selected metrics")
    if selected_export != selected:
        raise ValueError("selected_metrics.json does not match selected NCU metrics")
    supported = {
        line.strip()
        for line in supported_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    if not set(selected.values()).issubset(supported):
        raise ValueError("selected NCU metrics are absent from supported_metrics.txt")
    try:
        parsed = parse_ncu_csv(report_path, "q5_kernel")
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid Nsight Compute report: {exc}") from exc
    if report.get("parsed") != parsed:
        raise ValueError("Nsight Compute parsed report metadata does not match report.csv")
    parsed_metrics = parsed.get("metrics")
    if not isinstance(parsed_metrics, dict) or not set(selected.values()).issubset(parsed_metrics):
        raise ValueError("Nsight Compute report is missing selected NCU metrics")
    if any(
        not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in parsed_metrics.values()
    ):
        raise ValueError("Nsight Compute report contains non-finite metrics")
    return {
        "status": "ok",
        "metadata_path": _relative(root, metadata_path),
        "metadata_sha256": sha256_file(metadata_path),
        "profile_command": profile_command,
        "tool_version": version,
        "tool_version_provenance": {
            "metadata_path": _relative(root, metadata_path),
            "metadata_sha256": sha256_file(metadata_path),
            "version_command": ["ncu", "--version"],
            "metric_query_command": query_command,
            "gpu_query_command": gpu_query_command,
            "profile_command": profile_command,
        },
        "selected_metrics": selected,
        "report": _artifact(root, report_path),
        "metrics": parsed_metrics,
    }


def _compile_ncu_unavailable(config: dict[str, object]) -> dict[str, object]:
    reason = config.get("reason")
    if not isinstance(reason, dict):
        raise ValueError("unavailable ncu reason must be an object")
    _text(reason.get("code"), "unavailable ncu reason code")
    _text(reason.get("message"), "unavailable ncu reason message")
    selected = _selected_metrics(config.get("selected_metrics"), unavailable=True)
    version = _text(config.get("tool_version"), "unavailable ncu tool version")
    provenance = config.get("tool_version_provenance")
    if not isinstance(provenance, dict):
        raise ValueError("unavailable ncu tool version provenance must be an object")
    _command(
        provenance.get("command"),
        "unavailable ncu tool version provenance command",
    )
    if not isinstance(provenance.get("return_code"), int):
        raise ValueError("unavailable ncu tool version provenance return_code must be an integer")
    return {
        "status": "unavailable",
        "reason": dict(reason),
        "selected_metrics": selected,
        "tool_version": version,
        "tool_version_provenance": dict(provenance),
    }


def _compile_profile(root: Path, raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ValueError("each profiler profile must be an object")
    profile_id = _text(raw.get("id"), "profile id")
    scale_factor = _text(raw.get("scale_factor"), f"{profile_id}.scale_factor")
    engine = _text(raw.get("engine"), f"{profile_id}.engine")
    session_commit = _text(raw.get("session_commit"), f"{profile_id}.session_commit")
    if not _COMMIT_RE.fullmatch(session_commit):
        raise ValueError(f"{profile_id}.session_commit must be a full lowercase git commit")
    dataset = raw.get("dataset_manifest")
    if not isinstance(dataset, dict):
        raise ValueError(f"{profile_id}.dataset_manifest must be an object")
    dataset_path = _text(dataset.get("path"), f"{profile_id}.dataset_manifest.path")
    dataset_sha = _text(dataset.get("sha256"), f"{profile_id}.dataset_manifest.sha256")
    if not _SHA256_RE.fullmatch(dataset_sha):
        raise ValueError(f"{profile_id}.dataset_manifest.sha256 must be 64 lowercase hex characters")
    oracle_hash = _text(raw.get("oracle_hash"), f"{profile_id}.oracle_hash")
    if not _ORACLE_HASH_RE.fullmatch(oracle_hash):
        raise ValueError(f"{profile_id}.oracle_hash must be 16 lowercase hex characters")
    gpu_uuid = _text(raw.get("gpu_uuid"), f"{profile_id}.gpu_uuid")
    if not gpu_uuid.startswith("GPU-"):
        raise ValueError(f"{profile_id}.gpu_uuid must be a physical GPU UUID")
    command = _command(raw.get("command"), f"{profile_id}.command")
    ranges = raw.get("required_nvtx_ranges")
    if (
        not isinstance(ranges, list)
        or not ranges
        or any(not isinstance(value, str) or not value for value in ranges)
    ):
        raise ValueError(f"{profile_id}.required_nvtx_ranges must be a non-empty string array")
    required_ranges = set(ranges)
    if len(required_ranges) != len(ranges):
        raise ValueError(f"{profile_id}.required_nvtx_ranges must not contain duplicates")
    baseline_ranges = {"request", "q5_kernel"}
    if not baseline_ranges.issubset(required_ranges):
        raise ValueError(
            f"{profile_id}.required_nvtx_ranges must include request and q5_kernel"
        )
    nsys = _compile_nsys(root, raw, command, required_ranges)
    ncu_config = raw.get("ncu")
    if not isinstance(ncu_config, dict):
        raise ValueError(f"{profile_id}.ncu must be an object")
    status = ncu_config.get("status")
    if status == "ok":
        ncu = _compile_ncu_ok(root, ncu_config, command, gpu_uuid)
    elif status == "unavailable":
        ncu = _compile_ncu_unavailable(ncu_config)
    else:
        raise ValueError(f"{profile_id}.ncu.status must be ok or unavailable")
    return {
        "id": profile_id,
        "scale_factor": scale_factor,
        "engine": engine,
        "session_commit": session_commit,
        "dataset_manifest": {"path": dataset_path, "sha256": dataset_sha},
        "oracle_hash": oracle_hash,
        "gpu_uuid": gpu_uuid,
        "command": command,
        "nsys": nsys,
        "ncu": ncu,
    }


def _compile_profiles(root: Path, index: dict[str, object]) -> list[dict[str, object]]:
    if index.get("schema_version") != 1:
        raise ValueError("profiles.json schema_version must be 1")
    raw_profiles = index.get("profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise ValueError("profiles.json profiles must be a non-empty array")
    profiles = [_compile_profile(root, raw) for raw in raw_profiles]
    ids = [profile["id"] for profile in profiles]
    if len(ids) != len(set(ids)):
        raise ValueError("profiler profile ids must be unique")
    return profiles


def finalize(args: argparse.Namespace) -> int:
    root = Path(args.directory).resolve()
    if not root.is_dir():
        raise ValueError(f"profiler bundle directory does not exist: {root}")
    canonical_index = root / "profiles.json"
    requested_index = getattr(args, "index", None)
    if requested_index is not None:
        source = Path(requested_index).resolve()
        if source != canonical_index.resolve():
            shutil.copyfile(source, canonical_index)
    index = _load_json(canonical_index, "profiler profile index")
    profiles = _compile_profiles(root, index)
    manifest = {
        "manifest_version": 1,
        "status": "complete",
        "created_at_utc": _utc_now(),
        "source_index": {
            "path": "profiles.json",
            "sha256": sha256_file(canonical_index),
        },
        "profile_count": len(profiles),
        "ncu_unavailable_profiles": sum(
            profile["ncu"]["status"] == "unavailable" for profile in profiles
        ),
        "profiles": profiles,
        "artifacts": artifact_checksums(root),
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (root / "manifest.sha256").write_text(
        f"{sha256_file(root / 'manifest.json')}  manifest.json\n", encoding="ascii"
    )
    print(
        f"finalized status=complete profiles={len(profiles)} "
        f"ncu_unavailable={manifest['ncu_unavailable_profiles']} "
        f"artifacts={len(manifest['artifacts'])}"
    )
    return 0


def _audit_errors(root: Path) -> list[str]:
    errors: list[str] = []
    try:
        manifest = _load_json(root / "manifest.json", "profiler manifest")
    except (OSError, ValueError) as exc:
        return [str(exc)]
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or any(
        not isinstance(path, str) or not isinstance(digest, str)
        for path, digest in artifacts.items()
    ):
        errors.append("manifest artifacts must be a path-to-sha256 object")
    else:
        mismatches = audit_checksums(root, artifacts)
        if mismatches:
            errors.append(f"artifact checksum mismatches: {mismatches}")
    try:
        digest_line = (root / "manifest.sha256").read_text(encoding="ascii").strip()
        expected_line = f"{sha256_file(root / 'manifest.json')}  manifest.json"
        if digest_line != expected_line:
            errors.append("manifest digest mismatch")
    except OSError as exc:
        errors.append(f"missing or unreadable manifest.sha256: {exc}")
    source = manifest.get("source_index")
    if not isinstance(source, dict):
        errors.append("manifest source_index is missing")
    else:
        try:
            index_path = _inside(root, source.get("path"), "source_index.path")
            if source.get("sha256") != sha256_file(index_path):
                errors.append("source index checksum mismatch")
            rebuilt = _compile_profiles(
                root, _load_json(index_path, "profiler profile index")
            )
            if rebuilt != manifest.get("profiles"):
                errors.append("manifest profiles do not match revalidated profiler evidence")
        except (OSError, ValueError) as exc:
            errors.append(f"profiler evidence validation failed: {exc}")
    profiles = manifest.get("profiles")
    if manifest.get("manifest_version") != 1:
        errors.append("manifest_version must be 1")
    if manifest.get("status") != "complete":
        errors.append("manifest status is not complete")
    if not isinstance(profiles, list) or manifest.get("profile_count") != len(profiles):
        errors.append("manifest profile_count mismatch")
    elif manifest.get("ncu_unavailable_profiles") != sum(
        isinstance(profile, dict)
        and isinstance(profile.get("ncu"), dict)
        and profile["ncu"].get("status") == "unavailable"
        for profile in profiles
    ):
        errors.append("manifest ncu_unavailable_profiles mismatch")
    return errors


def audit(args: argparse.Namespace) -> int:
    root = Path(args.directory).resolve()
    errors = _audit_errors(root)
    ok = not errors
    print(f"audit ok={str(ok).lower()} errors={errors}")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Finalize or audit a V7 profiler bundle")
    subparsers = parser.add_subparsers(dest="command", required=True)
    finalize_parser = subparsers.add_parser("finalize")
    finalize_parser.add_argument("--directory", type=Path, required=True)
    finalize_parser.add_argument("--index", type=Path)
    audit_parser = subparsers.add_parser("audit")
    audit_parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    return finalize(args) if args.command == "finalize" else audit(args)


if __name__ == "__main__":
    raise SystemExit(main())
