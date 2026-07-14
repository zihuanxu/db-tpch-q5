#!/usr/bin/env python3
"""Finalize and audit V7 profiler evidence across a strict trust boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.parse_ncu_csv import parse_ncu_csv
    from scripts.parse_nsys_stats import parse_nsys_csv, validate_ranges
    from scripts.profile_ncu import METRIC_CANDIDATES, select_metrics
except ModuleNotFoundError:
    from parse_ncu_csv import parse_ncu_csv
    from parse_nsys_stats import parse_nsys_csv, validate_ranges
    from profile_ncu import METRIC_CANDIDATES, select_metrics


NSYS_EXPORTS = (
    "cuda_api_sum",
    "cuda_gpu_kern_sum",
    "cuda_gpu_mem_time_sum",
    "nvtx_sum",
)
ENGINE_COMMANDS = {
    "copy": "gpu-copy",
    "managed": "gpu-managed",
    "mapped": "gpu-mapped",
    "hybrid-fixed": "hybrid-arrow",
    "hybrid-auto": "hybrid-arrow",
}
ENGINE_NVTX_RANGES = {
    "copy": {"request", "q5_kernel"},
    "managed": {"request", "managed_prefetch", "q5_kernel"},
    "mapped": {"request", "q5_kernel"},
    "hybrid-fixed": {"request", "cpu_scan", "q5_kernel", "merge"},
    "hybrid-auto": {"request", "cpu_scan", "q5_kernel", "merge"},
}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_ORACLE_HASH_RE = re.compile(r"[0-9a-f]{16}")
_SCALE_RE = re.compile(r"[1-9][0-9]*(?:\.[0-9]+)?")


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _assert_no_symlink_components(
    path: Path, label: str, *, allow_missing_leaf: bool = False
) -> None:
    absolute = _absolute(path)
    current = Path(absolute.anchor)
    parts = absolute.parts[1:]
    for index, part in enumerate(parts):
        current /= part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            if allow_missing_leaf and index == len(parts) - 1:
                return
            raise ValueError(f"missing {label}: {current}")
        if stat.S_ISLNK(mode):
            raise ValueError(f"symlink is forbidden in {label}: {current}")


def _assert_bundle_tree(root: Path) -> None:
    _assert_no_symlink_components(root, "profiler bundle root")
    try:
        root_mode = os.lstat(root).st_mode
    except FileNotFoundError as exc:
        raise ValueError(f"profiler bundle directory does not exist: {root}") from exc
    if not stat.S_ISDIR(root_mode):
        raise ValueError(f"profiler bundle root is not a directory: {root}")
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise ValueError(f"cannot scan profiler bundle directory {directory}: {exc}") from exc
        for entry in entries:
            try:
                if entry.is_symlink():
                    raise ValueError(f"symlink is forbidden in profiler bundle: {entry.path}")
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
            except OSError as exc:
                raise ValueError(f"cannot inspect profiler bundle path {entry.path}: {exc}") from exc


def _bundle_root(value: object) -> Path:
    root = _absolute(Path(value))
    _assert_bundle_tree(root)
    return root


def _open_regular(path: Path, label: str) -> int:
    _assert_no_symlink_components(path, label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot open {label} without following links: {path}: {exc}") from exc
    mode = os.fstat(descriptor).st_mode
    if not stat.S_ISREG(mode):
        os.close(descriptor)
        raise ValueError(f"{label} is not a regular file: {path}")
    return descriptor


def _read_bytes(path: Path, label: str) -> bytes:
    descriptor = _open_regular(path, label)
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read()


def sha256_file(path: Path) -> str:
    descriptor = _open_regular(path, "artifact")
    digest = hashlib.sha256()
    with os.fdopen(descriptor, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    parent = path.parent
    _assert_no_symlink_components(parent, "control-file parent")
    _assert_no_symlink_components(path, "control file", allow_missing_leaf=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            target_mode = os.lstat(path).st_mode
        except FileNotFoundError:
            target_mode = None
        if target_mode is not None and stat.S_ISLNK(target_mode):
            raise ValueError(f"symlink is forbidden for control file: {path}")
        os.replace(temporary, path)
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def artifact_checksums(root: Path) -> dict[str, str]:
    _assert_bundle_tree(root)
    generated = {root / "manifest.json", root / "manifest.sha256"}
    checksums: dict[str, str] = {}
    for directory, _, names in os.walk(root, followlinks=False):
        for name in sorted(names):
            path = Path(directory) / name
            if path in generated:
                continue
            checksums[path.relative_to(root).as_posix()] = sha256_file(path)
    return dict(sorted(checksums.items()))


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
        value = json.loads(_read_bytes(path, label).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
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


def _inside_root(root: Path, value: object, label: str, *, require_file: bool) -> Path:
    text = _text(value, label)
    relative = Path(text)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"{label} must be a bundle-relative path")
    path = _absolute(root / relative)
    if not path.is_relative_to(root):
        raise ValueError(f"{label} escapes the profiler bundle: {text}")
    _assert_no_symlink_components(path, label)
    if require_file and not path.is_file():
        raise ValueError(f"missing {label}: {text}")
    return path


def _under_declared_root(
    root: Path,
    declared_root: Path,
    value: object,
    label: str,
    *,
    require_file: bool,
) -> Path:
    path = _inside_root(root, value, label, require_file=require_file)
    if not path.is_relative_to(declared_root):
        root_name = declared_root.name
        raise ValueError(f"{label} is outside declared {root_name} root")
    return path


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _artifact(root: Path, path: Path) -> dict[str, object]:
    return {
        "path": _relative(root, path),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _declared_roots(root: Path, index: dict[str, object]) -> dict[str, Path]:
    values = index.get("roots")
    if not isinstance(values, dict) or set(values) != {"captures", "datasets", "evidence"}:
        raise ValueError("profiles.json roots must declare captures, datasets, and evidence")
    roots: dict[str, Path] = {}
    for name in ("captures", "datasets", "evidence"):
        path = _inside_root(root, values[name], f"roots.{name}", require_file=False)
        if not path.is_dir():
            raise ValueError(f"declared {name} root is not a directory: {path}")
        roots[name] = path
    return roots


def _ratio(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid ratio for {label}")
    ratio = float(value)
    if not math.isfinite(ratio) or ratio < 0.0 or ratio > 1.0:
        raise ValueError(f"invalid ratio for {label}")
    return ratio


def _logical_profile(raw: object, label: str) -> tuple[dict[str, object], tuple[object, ...]]:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be an object")
    scale = _text(raw.get("scale_factor"), f"{label}.scale_factor")
    if not _SCALE_RE.fullmatch(scale):
        raise ValueError(f"invalid scale factor for {label}: {scale}")
    engine = _text(raw.get("engine"), f"{label}.engine")
    if engine not in ENGINE_COMMANDS:
        raise ValueError(f"invalid profiler engine for {label}: {engine}")
    cpu_ratio = _ratio(raw.get("cpu_ratio"), f"{label}.cpu_ratio")
    gpu_ratio = _ratio(raw.get("gpu_ratio"), f"{label}.gpu_ratio")
    if not math.isclose(cpu_ratio + gpu_ratio, 1.0, abs_tol=1e-12):
        raise ValueError(f"invalid ratio pair for {label}")
    if engine in {"copy", "managed", "mapped"} and (cpu_ratio, gpu_ratio) != (0.0, 1.0):
        raise ValueError(f"invalid ratio for non-hybrid engine {engine}")
    if engine.startswith("hybrid-") and not (0.0 < cpu_ratio < 1.0):
        raise ValueError(f"invalid ratio for hybrid engine {engine}")
    logical = {
        "scale_factor": scale,
        "engine": engine,
        "cpu_ratio": cpu_ratio,
        "gpu_ratio": gpu_ratio,
    }
    return logical, (scale, engine, cpu_ratio, gpu_ratio)


def _option(command: list[str], name: str) -> str:
    positions = [index for index, value in enumerate(command) if value == name]
    if len(positions) != 1 or positions[0] + 1 >= len(command):
        raise ValueError(f"profile command must contain exactly one {name} option")
    return command[positions[0] + 1]


def _validate_app_command(
    root: Path,
    command: list[str],
    logical: dict[str, object],
    dataset_manifest: Path,
) -> None:
    if "--warmup" in command or "--repeat" in command:
        raise ValueError("profile command must use --requests 1 without warmup/repeat")
    if _option(command, "--requests") != "1":
        raise ValueError("profile command must contain --requests 1")
    if _option(command, "--engine") != ENGINE_COMMANDS[str(logical["engine"])]:
        raise ValueError("profile command engine does not match logical profiler engine")
    try:
        command_ratio = float(_option(command, "--cpu-ratio"))
    except ValueError as exc:
        raise ValueError("profile command has invalid --cpu-ratio") from exc
    if not math.isclose(command_ratio, float(logical["cpu_ratio"]), abs_tol=1e-12):
        raise ValueError("profile command ratio does not match logical profiler ratio")
    dataset_value = Path(_option(command, "--dataset"))
    dataset_path = _absolute(dataset_value if dataset_value.is_absolute() else root / dataset_value)
    if dataset_path != dataset_manifest.parent:
        raise ValueError("profile command dataset does not match dataset manifest path")


def _manifest_reference(
    root: Path,
    declared_root: Path,
    value: object,
    label: str,
) -> tuple[dict[str, str], Path]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    path = _under_declared_root(
        root, declared_root, value.get("path"), f"{label}.path", require_file=True
    )
    expected = _text(value.get("sha256"), f"{label}.sha256")
    if not _SHA256_RE.fullmatch(expected):
        raise ValueError(f"{label}.sha256 must be 64 lowercase hex characters")
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"{label.replace('_', ' ')} digest mismatch")
    return {"path": _relative(root, path), "sha256": actual}, path


def _identity(
    raw: dict[str, object],
    logical: dict[str, object],
    dataset: dict[str, str],
    evidence: dict[str, str],
) -> dict[str, object]:
    session_commit = _text(raw.get("session_commit"), "session_commit")
    if not _COMMIT_RE.fullmatch(session_commit):
        raise ValueError("session_commit must be a full lowercase git commit")
    oracle_hash = _text(raw.get("oracle_hash"), "oracle_hash")
    if not _ORACLE_HASH_RE.fullmatch(oracle_hash):
        raise ValueError("oracle_hash must be 16 lowercase hex characters")
    gpu_uuid = _text(raw.get("gpu_uuid"), "gpu_uuid")
    if not gpu_uuid.startswith("GPU-"):
        raise ValueError("gpu_uuid must be a physical GPU UUID")
    return {
        **logical,
        "session_commit": session_commit,
        "dataset_manifest": dataset,
        "evidence_manifest": evidence,
        "oracle_hash": oracle_hash,
        "gpu_uuid": gpu_uuid,
    }


def _validate_collector_identity(
    metadata: dict[str, object], expected: dict[str, object], tool: str
) -> None:
    actual = metadata.get("metadata")
    if not isinstance(actual, dict) or any(actual.get(key) != value for key, value in expected.items()):
        raise ValueError(f"{tool} collector metadata identity mismatch")


def _collector_metadata(
    root: Path,
    captures_root: Path,
    config: object,
    label: str,
) -> tuple[Path, dict[str, object]]:
    if not isinstance(config, dict):
        raise ValueError(f"{label} must be an object")
    path = _under_declared_root(
        root,
        captures_root,
        config.get("metadata_path"),
        f"{label}.metadata_path",
        require_file=True,
    )
    return path, _load_json(path, f"{label} collector metadata")


def _collector_files(
    root: Path,
    captures_root: Path,
    metadata_path: Path,
    metadata: dict[str, object],
    label: str,
) -> dict[str, Path]:
    entries = metadata.get("files")
    if not isinstance(entries, dict) or not entries:
        raise ValueError(f"{label} collector files must be a non-empty object")
    paths: dict[str, Path] = {}
    for name, entry in entries.items():
        if not isinstance(name, str) or not name or not isinstance(entry, dict):
            raise ValueError(f"{label} collector file entry is invalid")
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"{label} collector file path is invalid: {name}")
        path = _absolute(metadata_path.parent / relative)
        if not path.is_relative_to(metadata_path.parent) or not path.is_relative_to(captures_root):
            raise ValueError(f"{label} collector file is outside declared captures root: {name}")
        _assert_no_symlink_components(path, f"{label} collector artifact")
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
        expected_bytes = entry.get("bytes")
        if not isinstance(expected_hash, str) or not _SHA256_RE.fullmatch(expected_hash):
            raise ValueError(f"{label} collector artifact has invalid sha256: {name}")
        if not isinstance(expected_bytes, int) or expected_bytes < 0:
            raise ValueError(f"{label} collector artifact has invalid byte count: {name}")
        if sha256_file(path) != expected_hash:
            raise ValueError(f"{label} collector checksum mismatch: {name}")
        if path.stat().st_size != expected_bytes:
            raise ValueError(f"{label} collector byte count mismatch: {name}")


def _required_file(paths: dict[str, Path], name: str, label: str) -> Path:
    path = paths.get(name)
    if path is None or not path.is_file():
        raise ValueError(f"missing {label}: {name}")
    return path


def _tool_version(metadata: dict[str, object], tool: str) -> str:
    versions = metadata.get("tool_versions")
    if not isinstance(versions, dict):
        raise ValueError(f"{tool} tool version is required")
    return _text(versions.get(tool), f"{tool} tool version")


def _compile_nsys(
    root: Path,
    captures_root: Path,
    raw: dict[str, object],
    command: list[str],
    identity: dict[str, object],
) -> dict[str, object]:
    metadata_path, metadata = _collector_metadata(root, captures_root, raw.get("nsys"), "nsys")
    _validate_collector_identity(metadata, identity, "nsys")
    directory = metadata_path.parent
    raw_report = directory / "profile.nsys-rep"
    expected_profile_command = [
        "nsys",
        "profile",
        "--force-overwrite=true",
        "--trace=cuda,nvtx,osrt",
        "--sample=none",
        "--output",
        str(directory / "profile"),
        *command,
    ]
    if metadata.get("profile_command") != expected_profile_command:
        raise ValueError("Nsight Systems profile command options do not match collector contract")
    expected_stats_command = [
        "nsys",
        "stats",
        "--force-export=true",
        "--report",
        ",".join(NSYS_EXPORTS),
        "--format",
        "csv",
        "--output",
        str(directory / "stats"),
        str(raw_report),
    ]
    if metadata.get("stats_commands") != [expected_stats_command]:
        raise ValueError("Nsight Systems stats command options do not match collector contract")
    if metadata.get("return_code") != 0 or metadata.get("stats") != [{"return_code": 0}]:
        raise ValueError("Nsight Systems collection did not succeed")
    version = _tool_version(metadata, "nsys")
    provenances = metadata.get("tool_version_provenance")
    provenance = provenances.get("nsys") if isinstance(provenances, dict) else None
    if not isinstance(provenance, dict):
        raise ValueError("nsys tool version provenance is required")
    if provenance.get("command") != ["nsys", "--version"] or provenance.get("return_code") != 0:
        raise ValueError("nsys tool version provenance is invalid")
    observed_version = str(provenance.get("stdout") or provenance.get("stderr") or "").strip()
    if observed_version != version:
        raise ValueError("nsys tool version does not match version provenance")
    paths = _collector_files(root, captures_root, metadata_path, metadata, "nsys")
    _verify_collector_files(paths, metadata, "nsys")
    raw_path = _required_file(paths, "profile.nsys-rep", "Nsight Systems raw report")
    if raw_path.stat().st_size == 0:
        raise ValueError("Nsight Systems raw report is empty")
    exports: dict[str, Path] = {}
    for report in NSYS_EXPORTS:
        candidates = [
            path
            for name, path in paths.items()
            if name.endswith(".csv") and report in Path(name).stem
        ]
        if len(candidates) != 1:
            raise ValueError(f"missing Nsight Systems export: {report}")
        exports[report] = candidates[0]
    parsed = {name: parse_nsys_csv(path) for name, path in exports.items()}
    required_ranges = ENGINE_NVTX_RANGES[str(identity["engine"])]
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
        str(row["name"])
        for row in parsed["nvtx_sum"]
        if row.get("kind") == "range" and row.get("name")
    )
    return {
        "status": "ok",
        "metadata_path": _relative(root, metadata_path),
        "metadata_sha256": sha256_file(metadata_path),
        "profile_command": expected_profile_command,
        "stats_commands": [expected_stats_command],
        "tool_version": version,
        "tool_version_provenance": dict(provenance),
        "raw_report": _artifact(root, raw_path),
        "exports": {name: _artifact(root, path) for name, path in sorted(exports.items())},
        "required_nvtx_ranges": sorted(required_ranges),
        "observed_nvtx_ranges": observed_ranges,
        "cuda_kernel_rows": len(kernel_rows),
        "derivation": {
            "status": "not_cryptographically_proven",
            "reason": (
                "Stored Nsight Systems commands and hashes do not cryptographically bind "
                "the exported CSV files to the .nsys-rep raw report."
            ),
        },
    }


def _gpu_provenance(
    metadata: dict[str, object], gpu_uuid: str, device_index: int
) -> dict[str, object]:
    gpu = metadata.get("gpu")
    if not isinstance(gpu, dict):
        raise ValueError("ncu GPU provenance is required")
    expected_query = [
        "nvidia-smi",
        f"--id={device_index}",
        "--query-gpu=index,uuid,driver_version",
        "--format=csv,noheader,nounits",
    ]
    if gpu.get("requested_index") != device_index or gpu.get("query_command") != expected_query:
        raise ValueError("ncu GPU provenance query does not match device index")
    status_value = gpu.get("status")
    if status_value == "ok":
        if gpu.get("index") != device_index or gpu.get("uuid") != gpu_uuid:
            raise ValueError("ncu GPU UUID does not match frozen identity")
        _text(gpu.get("driver_version"), "ncu GPU driver version")
    elif status_value not in {"failed", "unavailable", "invalid_output", "device_mismatch"}:
        raise ValueError("ncu GPU provenance status is invalid")
    return dict(gpu)


def _ncu_metric_state(
    metadata: dict[str, object],
    paths: dict[str, Path],
    *,
    allow_empty: bool,
) -> tuple[dict[str, str], list[str]]:
    supported_raw = metadata.get("supported_metrics", [])
    selected_raw = metadata.get("selected_metrics", {})
    if not isinstance(supported_raw, list) or any(not isinstance(value, str) for value in supported_raw):
        raise ValueError("ncu supported_metrics metadata is invalid")
    if not isinstance(selected_raw, dict):
        raise ValueError("selected NCU metrics must be an object")
    supported = sorted(set(supported_raw))
    selected = dict(selected_raw)
    if not supported and not selected and allow_empty:
        return {}, []
    if set(selected) != set(METRIC_CANDIDATES):
        raise ValueError("selected NCU metrics do not contain canonical metric roles")
    try:
        canonical = select_metrics(set(supported))
    except ValueError as exc:
        raise ValueError(f"canonical NCU metric selection failed: {exc}") from exc
    if selected != canonical:
        raise ValueError("selected metrics do not match canonical NCU metric selection")
    selected_path = _required_file(
        paths, "selected_metrics.json", "Nsight Compute selected-metric export"
    )
    supported_path = _required_file(
        paths, "supported_metrics.txt", "Nsight Compute supported-metric export"
    )
    if _load_json(selected_path, "Nsight Compute selected metrics") != selected:
        raise ValueError("selected_metrics.json does not match selected NCU metrics")
    supported_file = sorted(
        line.strip()
        for line in _read_bytes(supported_path, "supported metrics").decode("utf-8").splitlines()
        if line.strip()
    )
    if supported_file != supported:
        raise ValueError("supported_metrics.txt does not match collector metadata")
    return selected, supported


def _ncu_query_command(device_index: int) -> list[str]:
    return [
        "ncu",
        "--query-metrics",
        "--query-metrics-mode",
        "all",
        "--devices",
        str(device_index),
    ]


def _ncu_profile_command(
    selected: dict[str, str], device_index: int, command: list[str]
) -> list[str]:
    canonical_order = [selected[role] for role in METRIC_CANDIDATES]
    return [
        "ncu",
        "--csv",
        "--target-processes",
        "all",
        "--replay-mode",
        "application",
        "--kernel-name-base",
        "demangled",
        "--kernel-name",
        f"regex:.*{re.escape('q5_kernel')}.*",
        "--metrics",
        ",".join(canonical_order),
        "--devices",
        str(device_index),
        *command,
    ]


def _ncu_common(
    root: Path,
    captures_root: Path,
    raw: dict[str, object],
    identity: dict[str, object],
) -> tuple[Path, dict[str, object], dict[str, Path], int, str, dict[str, object]]:
    metadata_path, metadata = _collector_metadata(root, captures_root, raw.get("ncu"), "ncu")
    _validate_collector_identity(metadata, identity, "ncu")
    device_index = metadata.get("device_index")
    if isinstance(device_index, bool) or not isinstance(device_index, int) or device_index < 0:
        raise ValueError("ncu device_index must be non-negative")
    if metadata.get("query_command") != _ncu_query_command(device_index):
        raise ValueError("ncu metric query command does not match collector contract")
    version = _tool_version(metadata, "ncu")
    gpu = _gpu_provenance(metadata, str(identity["gpu_uuid"]), device_index)
    paths = _collector_files(root, captures_root, metadata_path, metadata, "ncu")
    _verify_collector_files(paths, metadata, "ncu")
    return metadata_path, metadata, paths, device_index, version, gpu


def _compile_ncu_ok(
    root: Path,
    captures_root: Path,
    raw: dict[str, object],
    command: list[str],
    identity: dict[str, object],
) -> dict[str, object]:
    metadata_path, metadata, paths, device_index, version, gpu = _ncu_common(
        root, captures_root, raw, identity
    )
    selected, _ = _ncu_metric_state(metadata, paths, allow_empty=False)
    expected_profile_command = _ncu_profile_command(selected, device_index, command)
    if metadata.get("profile_command") != expected_profile_command:
        raise ValueError("ncu profile command options do not match collector contract")
    replay = metadata.get("replay")
    if (
        metadata.get("return_code") != 0
        or replay != {"mode": "application", "return_code": 0, "succeeded": True}
    ):
        raise ValueError("Nsight Compute replay did not succeed")
    stdout_path = _required_file(paths, "profile.stdout.log", "Nsight Compute stdout log")
    stderr_path = _required_file(paths, "profile.stderr.log", "Nsight Compute stderr log")
    report = metadata.get("report")
    if not isinstance(report, dict) or report.get("path") != "report.csv":
        raise ValueError("Nsight Compute report provenance is required")
    report_path = _required_file(paths, "report.csv", "Nsight Compute report")
    if report.get("sha256") != sha256_file(report_path):
        raise ValueError("Nsight Compute report checksum mismatch")
    if _read_bytes(stdout_path, "ncu stdout log") != _read_bytes(report_path, "ncu report"):
        raise ValueError("Nsight Compute report is not identical to captured stdout")
    try:
        parsed = parse_ncu_csv(report_path, "q5_kernel")
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid Nsight Compute report: {exc}") from exc
    if report.get("parsed") != parsed:
        raise ValueError("Nsight Compute parsed report metadata does not match report.csv")
    metrics = parsed.get("metrics")
    if not isinstance(metrics, dict) or set(metrics) != set(selected.values()):
        raise ValueError("Nsight Compute report metrics do not match selected metrics")
    return {
        "status": "ok",
        "metadata_path": _relative(root, metadata_path),
        "metadata_sha256": sha256_file(metadata_path),
        "profile_command": expected_profile_command,
        "tool_version": version,
        "tool_version_provenance": {
            "metadata_path": _relative(root, metadata_path),
            "metadata_sha256": sha256_file(metadata_path),
            "version_command": ["ncu", "--version"],
            "metric_query_command": _ncu_query_command(device_index),
            "gpu_query_command": gpu["query_command"],
            "profile_command": expected_profile_command,
        },
        "gpu": gpu,
        "selected_metrics": selected,
        "report": _artifact(root, report_path),
        "stdout": _artifact(root, stdout_path),
        "stderr": _artifact(root, stderr_path),
        "metrics": metrics,
    }


def _structured_access_failure(metadata: dict[str, object]) -> dict[str, object] | None:
    access = metadata.get("access")
    if not isinstance(access, dict) or access.get("status") not in {"failed", "unavailable"}:
        return None
    code = access.get("code")
    message = access.get("message")
    if not isinstance(code, str) or not code or not isinstance(message, str) or not message:
        return None
    return dict(access)


def _compile_ncu_unavailable(
    root: Path,
    captures_root: Path,
    raw: dict[str, object],
    command: list[str],
    identity: dict[str, object],
) -> dict[str, object]:
    metadata_path, metadata, paths, device_index, version, gpu = _ncu_common(
        root, captures_root, raw, identity
    )
    stdout_path = _required_file(paths, "profile.stdout.log", "Nsight Compute stdout log")
    stderr_path = _required_file(paths, "profile.stderr.log", "Nsight Compute stderr log")
    selected, _ = _ncu_metric_state(metadata, paths, allow_empty=True)
    profile_command: list[str] | None = None
    if selected:
        profile_command = _ncu_profile_command(selected, device_index, command)
        if metadata.get("profile_command") != profile_command:
            raise ValueError("ncu profile command options do not match collector contract")
    return_code = metadata.get("return_code")
    access_failure = _structured_access_failure(metadata)
    if (not isinstance(return_code, int) or return_code == 0) and access_failure is None:
        raise ValueError("unavailable NCU claim lacks failed collector evidence")
    replay = metadata.get("replay")
    if isinstance(return_code, int) and return_code != 0:
        if (
            not isinstance(replay, dict)
            or replay.get("return_code") != return_code
            or replay.get("succeeded") is not False
        ):
            raise ValueError("unavailable NCU replay provenance is inconsistent")
        stdout = _read_bytes(stdout_path, "ncu stdout log")
        stderr = _read_bytes(stderr_path, "ncu stderr log")
        if not stdout and not stderr:
            raise ValueError("unavailable NCU claim requires nonempty failure logs")
        failure: dict[str, object] = {
            "status": "failed",
            "return_code": return_code,
            "message": (stderr or stdout).decode("utf-8", errors="replace").strip(),
        }
    else:
        assert access_failure is not None
        failure = access_failure
    return {
        "status": "unavailable",
        "metadata_path": _relative(root, metadata_path),
        "metadata_sha256": sha256_file(metadata_path),
        "return_code": return_code,
        "failure": failure,
        "profile_command": profile_command,
        "tool_version": version,
        "tool_version_provenance": {
            "metadata_path": _relative(root, metadata_path),
            "metadata_sha256": sha256_file(metadata_path),
            "version_command": ["ncu", "--version"],
            "metric_query_command": _ncu_query_command(device_index),
            "profile_command": profile_command,
        },
        "gpu": gpu,
        "selected_metrics": selected,
        "stdout": _artifact(root, stdout_path),
        "stderr": _artifact(root, stderr_path),
    }


def _compile_profile(
    root: Path,
    roots: dict[str, Path],
    raw: dict[str, object],
    logical: dict[str, object],
) -> dict[str, object]:
    profile_id = _text(raw.get("id"), "profile id")
    dataset, dataset_path = _manifest_reference(
        root, roots["datasets"], raw.get("dataset_manifest"), "dataset_manifest"
    )
    evidence, _ = _manifest_reference(
        root, roots["evidence"], raw.get("evidence_manifest"), "evidence_manifest"
    )
    identity = _identity(raw, logical, dataset, evidence)
    command = _command(raw.get("command"), f"{profile_id}.command")
    _validate_app_command(root, command, logical, dataset_path)
    nsys = _compile_nsys(root, roots["captures"], raw, command, identity)
    ncu_config = raw.get("ncu")
    if not isinstance(ncu_config, dict):
        raise ValueError(f"{profile_id}.ncu must be an object")
    if ncu_config.get("status") == "ok":
        ncu = _compile_ncu_ok(root, roots["captures"], raw, command, identity)
    elif ncu_config.get("status") == "unavailable":
        ncu = _compile_ncu_unavailable(root, roots["captures"], raw, command, identity)
    else:
        raise ValueError(f"{profile_id}.ncu.status must be ok or unavailable")
    return {
        "id": profile_id,
        **identity,
        "command": command,
        "nsys": nsys,
        "ncu": ncu,
    }


def _capture_paths(
    root: Path, captures_root: Path, raw: dict[str, object]
) -> list[Path]:
    paths: list[Path] = []
    for tool in ("nsys", "ncu"):
        config = raw.get(tool)
        if not isinstance(config, dict):
            raise ValueError(f"{tool} must be an object")
        paths.append(
            _under_declared_root(
                root,
                captures_root,
                config.get("metadata_path"),
                f"{tool}.metadata_path",
                require_file=True,
            )
        )
    return paths


def _compile_bundle(
    root: Path, index: dict[str, object]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    if index.get("schema_version") != 2:
        raise ValueError("profiles.json schema_version must be 2")
    if index.get("status") != "complete":
        raise ValueError("profiler index status must be complete")
    roots = _declared_roots(root, index)
    expected_raw = index.get("expected_profiles")
    profiles_raw = index.get("profiles")
    if not isinstance(expected_raw, list) or not expected_raw:
        raise ValueError("expected_profiles must be a non-empty array")
    if not isinstance(profiles_raw, list) or not profiles_raw:
        raise ValueError("profiles must be a non-empty array")

    expected: list[dict[str, object]] = []
    expected_keys: set[tuple[object, ...]] = set()
    for index_number, item in enumerate(expected_raw):
        logical, key = _logical_profile(item, f"expected_profiles[{index_number}]")
        if key in expected_keys:
            raise ValueError(f"duplicate expected logical profile tuple: {key}")
        expected_keys.add(key)
        expected.append(logical)
    hybrid_auto = index.get("hybrid_auto")
    hybrid_status = hybrid_auto.get("status") if isinstance(hybrid_auto, dict) else None
    if hybrid_status not in {"enabled", "disabled", "unavailable"}:
        raise ValueError("hybrid_auto.status must be enabled, disabled, or unavailable")
    expected_has_auto = any(item["engine"] == "hybrid-auto" for item in expected)
    if hybrid_status == "enabled" and not expected_has_auto:
        raise ValueError("enabled hybrid-auto is absent from expected coverage")
    if hybrid_status != "enabled" and expected_has_auto:
        raise ValueError("disabled/unavailable hybrid-auto appears in expected coverage")

    raw_with_logical: list[tuple[dict[str, object], dict[str, object]]] = []
    actual_keys: set[tuple[object, ...]] = set()
    ids: set[str] = set()
    capture_paths: set[Path] = set()
    for index_number, item in enumerate(profiles_raw):
        if not isinstance(item, dict):
            raise ValueError(f"profiles[{index_number}] must be an object")
        logical, key = _logical_profile(item, f"profiles[{index_number}]")
        if key in actual_keys:
            raise ValueError(f"duplicate logical profile tuple: {key}")
        actual_keys.add(key)
        profile_id = _text(item.get("id"), f"profiles[{index_number}].id")
        if profile_id in ids:
            raise ValueError(f"duplicate profiler profile id: {profile_id}")
        ids.add(profile_id)
        for metadata_path in _capture_paths(root, roots["captures"], item):
            if metadata_path in capture_paths:
                raise ValueError(
                    f"capture metadata path reused: {_relative(root, metadata_path)}"
                )
            capture_paths.add(metadata_path)
        raw_with_logical.append((item, logical))
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        raise ValueError(f"profiler coverage mismatch missing={missing} extra={extra}")
    profiles = [
        _compile_profile(root, roots, raw, logical)
        for raw, logical in raw_with_logical
    ]
    return profiles, expected


def finalize(args: argparse.Namespace) -> int:
    root = _bundle_root(args.directory)
    canonical_index = root / "profiles.json"
    requested_index = getattr(args, "index", None)
    if requested_index is not None:
        source = _absolute(Path(requested_index))
        if source != canonical_index:
            _atomic_write(canonical_index, _read_bytes(source, "profiler index"), 0o644)
    _assert_bundle_tree(root)
    index = _load_json(canonical_index, "profiler profile index")
    profiles, expected = _compile_bundle(root, index)
    manifest = {
        "manifest_version": 2,
        "status": "complete",
        "created_at_utc": _utc_now(),
        "source_index": {
            "path": "profiles.json",
            "sha256": sha256_file(canonical_index),
        },
        "expected_profiles": expected,
        "profile_count": len(profiles),
        "ncu_unavailable_profiles": sum(
            profile["ncu"]["status"] == "unavailable" for profile in profiles
        ),
        "profiles": profiles,
        "artifacts": artifact_checksums(root),
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_write(root / "manifest.json", manifest_bytes, 0o644)
    digest = sha256_file(root / "manifest.json")
    _atomic_write(root / "manifest.sha256", f"{digest}  manifest.json\n".encode("ascii"), 0o644)
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
    except ValueError as exc:
        return [str(exc)]
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or any(
        not isinstance(path, str)
        or not isinstance(digest, str)
        or not _SHA256_RE.fullmatch(digest)
        for path, digest in artifacts.items()
    ):
        errors.append("manifest artifacts must be a path-to-sha256 object")
    else:
        try:
            mismatches = audit_checksums(root, artifacts)
            if mismatches:
                errors.append(f"artifact checksum mismatches: {mismatches}")
        except ValueError as exc:
            errors.append(str(exc))
    try:
        digest_line = _read_bytes(root / "manifest.sha256", "manifest digest").decode("ascii").strip()
        expected_line = f"{sha256_file(root / 'manifest.json')}  manifest.json"
        if digest_line != expected_line:
            errors.append("manifest digest mismatch")
    except (UnicodeDecodeError, ValueError) as exc:
        errors.append(f"invalid manifest digest: {exc}")
    source = manifest.get("source_index")
    if not isinstance(source, dict):
        errors.append("manifest source_index is missing")
    else:
        try:
            index_path = _inside_root(root, source.get("path"), "source_index.path", require_file=True)
            if source.get("sha256") != sha256_file(index_path):
                errors.append("source index checksum mismatch")
            profiles, expected = _compile_bundle(
                root, _load_json(index_path, "profiler profile index")
            )
            if profiles != manifest.get("profiles"):
                errors.append("manifest profiles do not match revalidated profiler evidence")
            if expected != manifest.get("expected_profiles"):
                errors.append("manifest expected coverage does not match source index")
        except ValueError as exc:
            errors.append(f"profiler evidence validation failed: {exc}")
    profiles_value = manifest.get("profiles")
    if manifest.get("manifest_version") != 2:
        errors.append("manifest_version must be 2")
    if manifest.get("status") != "complete":
        errors.append("manifest status is not complete")
    if not isinstance(profiles_value, list) or manifest.get("profile_count") != len(profiles_value):
        errors.append("manifest profile_count mismatch")
    elif manifest.get("ncu_unavailable_profiles") != sum(
        isinstance(profile, dict)
        and isinstance(profile.get("ncu"), dict)
        and profile["ncu"].get("status") == "unavailable"
        for profile in profiles_value
    ):
        errors.append("manifest ncu_unavailable_profiles mismatch")
    return errors


def audit(args: argparse.Namespace) -> int:
    try:
        root = _bundle_root(args.directory)
        errors = _audit_errors(root)
    except ValueError as exc:
        errors = [str(exc)]
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
