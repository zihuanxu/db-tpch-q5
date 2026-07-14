#!/usr/bin/env python3
"""Finalize and audit V7 profiler evidence across a strict trust boundary."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import stat
import subprocess
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
    "hybrid-auto": {
        "request", "cpu_scan", "q5_kernel", "hybrid_gpu_request", "merge"
    },
}
CANONICAL_SCALES = ("1", "10")
CANONICAL_FIXED_ENGINES = ("copy", "managed", "mapped", "hybrid-fixed")
HYBRID_FIXED_CPU_RATIO = 0.5
HYBRID_AUTO_POLICY = "mandatory"
CANONICAL_COVERAGE_POLICY = {
    "canonical_matrix": "SF1/SF10 x copy/managed/mapped/hybrid-fixed/hybrid-auto",
    "hybrid_fixed_cpu_ratio": HYBRID_FIXED_CPU_RATIO,
    "hybrid_auto": HYBRID_AUTO_POLICY,
}
BUNDLE_RESIDUALS = [
    {
        "id": "nsys-export-linkage",
        "status": "documented",
        "reason": (
            "Finalize and audit require a compatible local nsys, re-export the raw report, and compare every CSV hash; "
            "the external tool's interpretation is not a cryptographic proof of report semantics."
        ),
    },
    {
        "id": "executable-build-commit",
        "status": "documented",
        "reason": (
            "The profiled executable must be ELF, executable, hash-pinned, and identical to the path resolved by each collector. "
            "The declared build_commit is cross-checked with evidence git.commit but is not cryptographically embedded in the binary, "
            "so this is not source attestation."
        ),
    },
    {
        "id": "concurrent-bundle-root-replacement",
        "status": "documented",
        "reason": (
            "Individual reads and control writes walk directory descriptors with O_NOFOLLOW; "
            "independent whole-bundle operations are not serialized against a hostile concurrent root rename."
        ),
    },
]
NSYS_REPORT_MAGIC = b"NVIDIA Tegra Profiler Report "
NSYS_MIN_REPORT_BYTES = 4096
KNOWN_NCU_ACCESS_CODES = {
    "ERR_NVGPUCTRPERM",
    "COUNTERS_UNAVAILABLE",
    "UNSUPPORTED_DEVICE",
    "UNSUPPORTED_HARDWARE",
    "UNSUPPORTED_METRICS",
}
NCU_UNAVAILABLE_PATTERNS = (
    re.compile(r"err_nvgpuctrperm", re.IGNORECASE),
    re.compile(r"permission.*(?:gpu )?performance counters", re.IGNORECASE),
    re.compile(r"performance counters.*permission", re.IGNORECASE),
    re.compile(r"profil(?:ing|er).*(?:not supported|unsupported).*(?:gpu|device|hardware)", re.IGNORECASE),
    re.compile(r"(?:gpu|device|hardware).*(?:not supported|unsupported).*(?:profil|counter)", re.IGNORECASE),
)
NCU_FATAL_PATTERNS = (
    re.compile(r"segmentation fault", re.IGNORECASE),
    re.compile(r"\bsegfault(?:ed)?\b", re.IGNORECASE),
    re.compile(r"\bcrash(?:ed)?\b", re.IGNORECASE),
    re.compile(r"core dumped", re.IGNORECASE),
    re.compile(r"(?:failed|unable) to (?:launch|start|execute)", re.IGNORECASE),
    re.compile(r"launch failed", re.IGNORECASE),
    re.compile(
        r"(?:application|app|target (?:application|process)).*(?:failed|error|exited|returned)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:executable|command).*(?:not found|no such file|error)", re.IGNORECASE),
    re.compile(r"unknown (?:option|argument)", re.IGNORECASE),
    re.compile(r"unrecognized (?:option|argument)", re.IGNORECASE),
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_ORACLE_HASH_RE = re.compile(r"[0-9a-f]{16}")
_NCU_METRIC_TOKEN = re.compile(r"^([A-Za-z][A-Za-z0-9_]*__[A-Za-z0-9_.]+)")


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


def _open_directory_nofollow(path: Path, label: str) -> int:
    absolute = _absolute(path)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(absolute.anchor, flags)
    try:
        for part in absolute.parts[1:]:
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            try:
                if not stat.S_ISDIR(os.fstat(next_descriptor).st_mode):
                    raise ValueError(f"{label} component is not a directory: {part}")
            except Exception:
                os.close(next_descriptor)
                raise
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_regular(path: Path, label: str) -> int:
    absolute = _absolute(path)
    _assert_no_symlink_components(absolute, label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    parent_descriptor = _open_directory_nofollow(absolute.parent, f"{label} parent")
    try:
        descriptor = os.open(absolute.name, flags, dir_fd=parent_descriptor)
    except OSError as exc:
        raise ValueError(
            f"cannot open {label} without following links: {absolute}: {exc}"
        ) from exc
    finally:
        os.close(parent_descriptor)
    mode = os.fstat(descriptor).st_mode
    if not stat.S_ISREG(mode):
        os.close(descriptor)
        raise ValueError(f"{label} is not a regular file: {absolute}")
    return descriptor


def _read_bytes(path: Path, label: str) -> bytes:
    descriptor = _open_regular(path, label)
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read()


def _read_prefix(path: Path, size: int, label: str) -> bytes:
    descriptor = _open_regular(path, label)
    with os.fdopen(descriptor, "rb") as handle:
        return handle.read(size)


def _size_and_prefix(path: Path, size: int, label: str) -> tuple[int, bytes]:
    descriptor = _open_regular(path, label)
    file_size = os.fstat(descriptor).st_size
    with os.fdopen(descriptor, "rb") as handle:
        return file_size, handle.read(size)


def sha256_file(path: Path) -> str:
    descriptor = _open_regular(path, "artifact")
    digest = hashlib.sha256()
    with os.fdopen(descriptor, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    path = _absolute(path)
    parent = path.parent
    _assert_no_symlink_components(parent, "control-file parent")
    _assert_no_symlink_components(path, "control file", allow_missing_leaf=True)
    parent_descriptor = _open_directory_nofollow(parent, "control-file parent")
    temporary_name = f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(temporary_name, flags, mode, dir_fd=parent_descriptor)
    except Exception:
        os.close(parent_descriptor)
        raise
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            target_mode = os.stat(
                path.name, dir_fd=parent_descriptor, follow_symlinks=False
            ).st_mode
        except FileNotFoundError:
            target_mode = None
        if target_mode is not None and stat.S_ISLNK(target_mode):
            raise ValueError(f"symlink is forbidden for control file: {path}")
        if target_mode is not None and not stat.S_ISREG(target_mode):
            raise ValueError(f"control file target is not regular: {path}")
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        os.fsync(parent_descriptor)
    finally:
        try:
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass
        os.close(parent_descriptor)


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
    expected = {
        "captures": "captures",
        "datasets": "datasets",
        "evidence": "evidence",
    }
    if values != expected:
        raise ValueError(
            "profiles.json roots must be exactly captures, datasets, and evidence"
        )
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
    if scale not in CANONICAL_SCALES:
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
    if engine == "hybrid-fixed" and not math.isclose(
        cpu_ratio, HYBRID_FIXED_CPU_RATIO, abs_tol=1e-12
    ):
        raise ValueError(f"invalid ratio for hybrid-fixed engine: {cpu_ratio}")
    if engine == "hybrid-auto" and not (0.0 < cpu_ratio < 1.0):
        raise ValueError(f"invalid ratio for hybrid engine {engine}")
    logical = {
        "scale_factor": scale,
        "engine": engine,
        "cpu_ratio": cpu_ratio,
        "gpu_ratio": gpu_ratio,
    }
    return logical, (scale, engine, cpu_ratio, gpu_ratio)


def _canonical_fixed_profiles() -> list[dict[str, object]]:
    profiles: list[dict[str, object]] = []
    for scale in CANONICAL_SCALES:
        for engine in CANONICAL_FIXED_ENGINES:
            cpu_ratio = HYBRID_FIXED_CPU_RATIO if engine == "hybrid-fixed" else 0.0
            profiles.append(
                {
                    "scale_factor": scale,
                    "engine": engine,
                    "cpu_ratio": cpu_ratio,
                    "gpu_ratio": 1.0 - cpu_ratio,
                }
            )
    return profiles


def _logical_key(logical: dict[str, object]) -> tuple[object, ...]:
    return (
        logical["scale_factor"],
        logical["engine"],
        logical["cpu_ratio"],
        logical["gpu_ratio"],
    )


def _option(command: list[str], name: str) -> str:
    positions = [index for index, value in enumerate(command) if value == name]
    if len(positions) != 1 or positions[0] + 1 >= len(command):
        raise ValueError(f"profile command must contain exactly one {name} option")
    return command[positions[0] + 1]


def _validate_app_command(
    command: list[str],
    logical: dict[str, object],
    dataset_manifest: Path,
    execution: dict[str, object],
) -> None:
    if "--warmup" in command or "--repeat" in command:
        raise ValueError("profile command must use --requests 1 without warmup/repeat")
    if _option(command, "--requests") != "1":
        raise ValueError("profile command must contain --requests 1")
    engine = str(logical["engine"])
    if _option(command, "--engine") != ENGINE_COMMANDS[engine]:
        raise ValueError("profile command engine does not match logical profiler engine")
    ratio_positions = [index for index, value in enumerate(command) if value == "--cpu-ratio"]
    if engine == "hybrid-auto":
        if ratio_positions:
            raise ValueError("hybrid-auto profile command must not set --cpu-ratio")
    else:
        try:
            command_ratio = float(_option(command, "--cpu-ratio"))
        except ValueError as exc:
            raise ValueError("profile command has invalid --cpu-ratio") from exc
        if not math.isclose(command_ratio, float(logical["cpu_ratio"]), abs_tol=1e-12):
            raise ValueError("profile command ratio does not match logical profiler ratio")
    selection_positions = [
        index for index, value in enumerate(command) if value == "--hybrid-selection"
    ]
    if engine.startswith("hybrid-"):
        expected_selection = "auto" if engine == "hybrid-auto" else "fixed"
        if _option(command, "--hybrid-selection") != expected_selection:
            raise ValueError("profile command hybrid selection does not match logical engine")
    elif selection_positions:
        raise ValueError("non-hybrid profile command must not set --hybrid-selection")
    cwd = Path(str(execution["cwd"]))
    dataset_value = Path(_option(command, "--dataset"))
    dataset_path = _absolute(dataset_value if dataset_value.is_absolute() else cwd / dataset_value)
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
    command: list[str],
) -> dict[str, object]:
    session_commit = _text(raw.get("session_commit"), "session_commit")
    if not _COMMIT_RE.fullmatch(session_commit):
        raise ValueError("session_commit must be a full lowercase git commit")
    oracle_hash = _text(raw.get("oracle_hash"), "oracle_hash")
    if not _ORACLE_HASH_RE.fullmatch(oracle_hash):
        raise ValueError("oracle_hash must be 16 lowercase hex characters")
    result_hash = _text(raw.get("result_hash"), "result_hash")
    if not _ORACLE_HASH_RE.fullmatch(result_hash):
        raise ValueError("result_hash must be 16 lowercase hex characters")
    if result_hash != oracle_hash:
        raise ValueError("oracle hash does not match result_hash")
    gpu_uuid = _text(raw.get("gpu_uuid"), "gpu_uuid")
    if not gpu_uuid.startswith("GPU-"):
        raise ValueError("gpu_uuid must be a physical GPU UUID")
    execution = _execution_provenance(raw.get("execution"), command, session_commit)
    return {
        **logical,
        "session_commit": session_commit,
        "dataset_manifest": dataset,
        "evidence_manifest": evidence,
        "oracle_hash": oracle_hash,
        "result_hash": result_hash,
        "gpu_uuid": gpu_uuid,
        "execution": execution,
    }


def _execution_provenance(
    value: object,
    command: list[str],
    session_commit: str,
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("execution provenance must be an object")
    cwd_text = _text(value.get("cwd"), "execution working directory")
    cwd = Path(cwd_text)
    if not cwd.is_absolute():
        raise ValueError("execution working directory must be absolute")
    cwd = _absolute(cwd)
    _assert_no_symlink_components(cwd, "execution working directory")
    if not cwd.is_dir():
        raise ValueError("execution working directory is not a directory")
    executable = value.get("executable")
    if not isinstance(executable, dict):
        raise ValueError("execution executable provenance must be an object")
    executable_text = _text(executable.get("path"), "execution executable path")
    if command[0] != executable_text:
        raise ValueError("profile command executable does not match execution provenance")
    executable_value = Path(executable_text)
    executable_path = _absolute(
        executable_value if executable_value.is_absolute() else cwd / executable_value
    )
    if executable_path.name != "memq5_arrow_session":
        raise ValueError("profile command must use the memq5_arrow_session executable")
    expected_sha = _text(executable.get("sha256"), "execution executable sha256")
    if not _SHA256_RE.fullmatch(expected_sha):
        raise ValueError("execution executable sha256 must be lowercase SHA256")
    try:
        actual_sha = sha256_file(executable_path)
    except ValueError as exc:
        raise ValueError(f"execution executable cannot be verified: {exc}") from exc
    if actual_sha != expected_sha:
        raise ValueError("execution executable sha256 mismatch")
    if not os.access(executable_path, os.X_OK):
        raise ValueError("execution executable is not executable")
    if _read_prefix(executable_path, 4, "execution executable") != b"\x7fELF":
        raise ValueError("execution executable must be an ELF binary")
    build_commit = _text(executable.get("build_commit"), "execution build commit")
    if build_commit != session_commit:
        raise ValueError("execution build commit does not match session commit")
    return {
        "cwd": str(cwd),
        "executable": {
            "path": executable_text,
            "sha256": expected_sha,
            "build_commit": build_commit,
        },
    }


def _validate_collector_execution(
    metadata: dict[str, object],
    identity: dict[str, object],
    command: list[str],
    tool: str,
) -> dict[str, object]:
    collector = metadata.get("collector_execution")
    if not isinstance(collector, dict) or collector.get("status") != "ok":
        raise ValueError(f"{tool} collector execution provenance must be successful")
    execution = identity["execution"]
    assert isinstance(execution, dict)
    executable = execution["executable"]
    assert isinstance(executable, dict)
    cwd = Path(str(execution["cwd"]))
    command_path = str(executable["path"])
    expected_path_value = Path(command_path)
    expected_path = _absolute(
        expected_path_value if expected_path_value.is_absolute() else cwd / expected_path_value
    )
    if (
        collector.get("cwd") != str(cwd)
        or collector.get("command_path") != command[0]
        or command[0] != command_path
        or collector.get("resolved_path") != str(expected_path)
        or collector.get("executable_sha256") != executable["sha256"]
    ):
        raise ValueError(f"{tool} collector executable provenance mismatch")
    if expected_path.name != "memq5_arrow_session":
        raise ValueError(f"{tool} collector executable name mismatch")
    _assert_no_symlink_components(expected_path, f"{tool} collector executable")
    return dict(collector)


def _validate_dataset_manifest(path: Path, logical: dict[str, object]) -> dict[str, object]:
    manifest = _load_json(path, "dataset manifest")
    if manifest.get("format_version") != 1:
        raise ValueError("dataset manifest format_version must be 1")
    if str(manifest.get("scale_factor")) != logical["scale_factor"]:
        raise ValueError("dataset manifest scale mismatch")
    tables = manifest.get("tables")
    if not isinstance(tables, dict) or not tables:
        raise ValueError("dataset manifest tables must be a non-empty object")
    return manifest


def _validate_evidence_manifest(
    root: Path,
    path: Path,
    dataset_path: Path,
    dataset_sha256: str,
    identity: dict[str, object],
) -> dict[str, object]:
    manifest = _load_json(path, "evidence manifest")
    if manifest.get("manifest_version") != 1 or manifest.get("status") != "complete":
        raise ValueError("evidence manifest must be a complete version-1 bundle")
    git = manifest.get("git")
    if not isinstance(git, dict) or git.get("commit") != identity["session_commit"]:
        raise ValueError("evidence manifest session commit mismatch")
    dataset = manifest.get("dataset")
    if not isinstance(dataset, dict):
        raise ValueError("evidence manifest dataset is missing")
    if str(dataset.get("scale_factor")) != identity["scale_factor"]:
        raise ValueError("evidence manifest dataset scale mismatch")
    if dataset.get("manifest_sha256") != dataset_sha256:
        raise ValueError("evidence manifest dataset sha mismatch")
    dataset_text = _text(dataset.get("path"), "evidence manifest dataset path")
    declared_dataset = Path(dataset_text)
    declared_path = _absolute(
        declared_dataset if declared_dataset.is_absolute() else root / declared_dataset
    )
    _assert_no_symlink_components(declared_path, "evidence manifest dataset path")
    if declared_path != dataset_path.parent:
        raise ValueError("evidence manifest dataset path mismatch")
    correctness = manifest.get("correctness")
    if not isinstance(correctness, dict) or correctness.get("ok") is not True:
        raise ValueError("evidence manifest correctness is not complete")
    if correctness.get("expected_hash") != identity["oracle_hash"]:
        raise ValueError("evidence manifest oracle hash mismatch")
    if correctness.get("observed_hashes") != [identity["result_hash"]]:
        raise ValueError("evidence manifest result hash mismatch")
    return manifest


def _validate_app_stdout(
    path: Path,
    logical: dict[str, object],
    command: list[str],
    dataset_path: Path,
    execution: dict[str, object],
    result_hash: str,
) -> dict[str, object]:
    try:
        records = [
            json.loads(line)
            for line in _read_bytes(path, "app stdout JSONL").decode("utf-8").splitlines()
            if line.strip()
        ]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid app stdout JSONL: {exc}") from exc
    if len(records) != 2 or any(not isinstance(record, dict) for record in records):
        raise ValueError("app stdout JSONL must contain one setup and one request")
    setup, request = records
    if setup.get("record_type") != "session_setup" or request.get("record_type") != "request":
        raise ValueError("app stdout JSONL record types are invalid")
    session_id = setup.get("session_id")
    if not isinstance(session_id, str) or not session_id or request.get("session_id") != session_id:
        raise ValueError("app stdout JSONL session identity mismatch")
    if any(record.get("lifecycle") != "resident" or record.get("status") != "ok" for record in records):
        raise ValueError("app stdout JSONL does not contain a successful resident request")
    if setup.get("engine") != ENGINE_COMMANDS[str(logical["engine"])]:
        raise ValueError("app stdout engine does not match profile identity")
    cwd = Path(str(execution["cwd"]))
    app_dataset_value = Path(str(setup.get("dataset", "")))
    app_dataset = _absolute(
        app_dataset_value if app_dataset_value.is_absolute() else cwd / app_dataset_value
    )
    if app_dataset != dataset_path.parent or str(setup.get("dataset")) != _option(command, "--dataset"):
        raise ValueError("app stdout dataset does not match profile command")
    if setup.get("warmup") != 0 or setup.get("repeat") != 1:
        raise ValueError("app stdout request count does not match --requests 1")
    expected_ratio = float(logical["cpu_ratio"])
    for record in records:
        ratio = record.get("selected_cpu_ratio")
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not math.isclose(
            float(ratio), expected_ratio, abs_tol=1e-12
        ):
            raise ValueError("app stdout selected_cpu_ratio does not match profile identity")
    if request.get("request_index") != 0 or request.get("is_warmup") is not False:
        raise ValueError("app stdout request markers are invalid")
    if request.get("result_hash") != result_hash:
        raise ValueError("app result_hash does not match frozen profile identity")
    return {"session_id": session_id, "result_hash": result_hash}


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


def _tool_provenance(
    metadata: dict[str, object], tool: str, version: str
) -> dict[str, object]:
    provenances = metadata.get("tool_version_provenance")
    provenance = provenances.get(tool) if isinstance(provenances, dict) else None
    if not isinstance(provenance, dict):
        raise ValueError(f"{tool} tool version provenance is required")
    if provenance.get("command") != [tool, "--version"] or provenance.get("return_code") != 0:
        raise ValueError(f"{tool} tool version provenance is invalid")
    stdout = provenance.get("stdout")
    stderr = provenance.get("stderr")
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        raise ValueError(f"{tool} tool version provenance output is invalid")
    if (stdout or stderr).strip() != version:
        raise ValueError(f"{tool} tool version does not match version provenance")
    return dict(provenance)


def _find_nsys() -> str | None:
    return shutil.which("nsys")


def _replay_nsys_exports(
    stats_command: list[str],
    raw_report: Path,
    exports: dict[str, Path],
    cwd: Path,
    expected_version: str,
) -> dict[str, object]:
    executable = _find_nsys()
    if executable is None:
        raise ValueError(
            "a compatible nsys executable is required on the current validation machine"
        )
    environment = os.environ.copy()
    environment.update({"LC_ALL": "C", "LANG": "C"})
    version_command = [executable, "--version"]
    try:
        version_result = subprocess.run(
            version_command,
            cwd=cwd,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise ValueError(f"Nsight Systems version query launch failed: {exc}") from exc
    current_version = (version_result.stdout or version_result.stderr).strip()
    if version_result.returncode != 0 or current_version != expected_version:
        raise ValueError(
            "current nsys version is not compatible with collector provenance: "
            f"expected={expected_version!r} actual={current_version!r} "
            f"return_code={version_result.returncode}"
        )
    with tempfile.TemporaryDirectory(prefix="v7-nsys-replay-") as temporary:
        temporary_root = Path(temporary)
        replay_report = temporary_root / raw_report.name
        shutil.copyfile(raw_report, replay_report)
        for original in exports.values():
            shutil.copyfile(original, temporary_root / original.name)
        output_prefix = temporary_root / "stats"
        replay_command = list(stats_command)
        replay_command[0] = executable
        replay_command[replay_command.index("--output") + 1] = str(output_prefix)
        replay_command[-1] = str(replay_report)
        try:
            completed = subprocess.run(
                replay_command,
                cwd=cwd,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except OSError as exc:
            raise ValueError(f"Nsight Systems re-export launch failed: {exc}") from exc
        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout).strip()
            raise ValueError(
                f"Nsight Systems re-export failed return_code={completed.returncode}: {message}"
            )
        mismatches: list[str] = []
        replay_hashes: dict[str, str] = {}
        for report, original in exports.items():
            candidates = sorted(Path(temporary).glob(f"*{report}*.csv"))
            if len(candidates) != 1:
                raise ValueError(f"Nsight Systems re-export missing report: {report}")
            replay_hash = sha256_file(candidates[0])
            replay_hashes[report] = replay_hash
            if replay_hash != sha256_file(original):
                mismatches.append(report)
        if mismatches:
            raise ValueError(f"Nsight Systems re-export mismatch: {mismatches}")
        return {
            "status": "verified",
            "replay_validation": "matched",
            "command": stats_command,
            "audit_executable": executable,
            "version_provenance": {
                "command": version_command,
                "return_code": version_result.returncode,
                "stdout": version_result.stdout or "",
                "stderr": version_result.stderr or "",
            },
            "return_code": completed.returncode,
            "export_sha256": replay_hashes,
        }


def _compile_nsys(
    root: Path,
    captures_root: Path,
    raw: dict[str, object],
    command: list[str],
    identity: dict[str, object],
) -> dict[str, object]:
    metadata_path, metadata = _collector_metadata(root, captures_root, raw.get("nsys"), "nsys")
    _validate_collector_identity(metadata, identity, "nsys")
    collector_execution = _validate_collector_execution(metadata, identity, command, "nsys")
    directory = metadata_path.parent
    raw_report = directory / "profile.nsys-rep"
    expected_profile_command = [
        "nsys",
        "profile",
        "--force-overwrite=true",
        "--trace=cuda,nvtx,osrt",
        "--sample=none",
        "--inherit-environment=false",
        "--env-var=NSYS_NVTX_PROFILER_REGISTER_ONLY=0",
        "--capture-range=nvtx",
        "--nvtx-capture=measured_request",
        "--capture-range-end=stop",
        "--output",
        str(directory / "profile"),
        *command,
    ]
    legacy_profile_command = [
        argument
        for argument in expected_profile_command
        if argument != "--inherit-environment=false"
    ]
    if metadata.get("profile_command") not in (
        expected_profile_command,
        legacy_profile_command,
    ):
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
    stats = metadata.get("stats")
    if metadata.get("return_code") != 0 or not isinstance(stats, list) or len(stats) != 1:
        raise ValueError("Nsight Systems collection did not succeed")
    stats_provenance = stats[0]
    if (
        not isinstance(stats_provenance, dict)
        or stats_provenance.get("command") != expected_stats_command
        or stats_provenance.get("return_code") != 0
        or not isinstance(stats_provenance.get("stdout"), str)
        or not isinstance(stats_provenance.get("stderr"), str)
    ):
        raise ValueError("Nsight Systems stats success provenance is invalid")
    version = _tool_version(metadata, "nsys")
    provenance = _tool_provenance(metadata, "nsys", version)
    paths = _collector_files(root, captures_root, metadata_path, metadata, "nsys")
    _verify_collector_files(paths, metadata, "nsys")
    raw_path = _required_file(paths, "profile.nsys-rep", "Nsight Systems raw report")
    raw_size, raw_prefix = _size_and_prefix(
        raw_path, len(NSYS_REPORT_MAGIC), "Nsight Systems raw report"
    )
    if raw_size < NSYS_MIN_REPORT_BYTES or raw_prefix != NSYS_REPORT_MAGIC:
        raise ValueError("Nsight Systems raw report signature or size is invalid")
    stats_stdout = _required_file(paths, "stats.stdout.log", "Nsight Systems stats stdout")
    stats_stderr = _required_file(paths, "stats.stderr.log", "Nsight Systems stats stderr")
    if _read_bytes(stats_stdout, "nsys stats stdout").decode("utf-8") != stats_provenance["stdout"]:
        raise ValueError("Nsight Systems stats stdout provenance mismatch")
    if _read_bytes(stats_stderr, "nsys stats stderr").decode("utf-8") != stats_provenance["stderr"]:
        raise ValueError("Nsight Systems stats stderr provenance mismatch")
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
        and "q5_kernel" in str(row.get("name", ""))
        and isinstance(row.get("total_ns"), (int, float))
        and row["total_ns"] > 0
    ]
    if not kernel_rows:
        raise ValueError("Nsight Systems kernel export has no positive q5_kernel activity")
    app_stdout = _required_file(paths, "profile.stdout.log", "profiled app stdout JSONL")
    dataset_manifest_path = root / str(identity["dataset_manifest"]["path"])
    app_result = _validate_app_stdout(
        app_stdout,
        identity,
        command,
        dataset_manifest_path,
        identity["execution"],
        str(identity["result_hash"]),
    )
    derivation = _replay_nsys_exports(
        expected_stats_command,
        raw_path,
        exports,
        Path(str(identity["execution"]["cwd"])),
        version,
    )
    observed_ranges = sorted(
        str(row["name"])
        for row in parsed["nvtx_sum"]
        if row.get("kind") == "range" and row.get("name")
    )
    return {
        "status": "ok",
        "metadata_path": _relative(root, metadata_path),
        "metadata_sha256": sha256_file(metadata_path),
        "profile_command": list(metadata["profile_command"]),
        "stats_commands": [expected_stats_command],
        "tool_version": version,
        "tool_version_provenance": dict(provenance),
        "collector_execution": collector_execution,
        "raw_report": _artifact(root, raw_path),
        "exports": {name: _artifact(root, path) for name, path in sorted(exports.items())},
        "required_nvtx_ranges": sorted(required_ranges),
        "observed_nvtx_ranges": observed_ranges,
        "cuda_kernel_rows": len(kernel_rows),
        "app_stdout": _artifact(root, app_stdout),
        "app_result": app_result,
        "derivation": derivation,
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
    if gpu.get("status") != "ok" or gpu.get("return_code") != 0:
        raise ValueError("ncu GPU provenance must be successful")
    stdout = gpu.get("stdout")
    stderr = gpu.get("stderr")
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        raise ValueError("ncu GPU query output provenance is invalid")
    rows = [line.strip() for line in stdout.splitlines() if line.strip()]
    fields = [field.strip() for field in rows[0].split(",")] if len(rows) == 1 else []
    if len(fields) != 3 or not fields[0].isdigit():
        raise ValueError("ncu GPU query output is invalid")
    parsed_index = int(fields[0])
    if (
        gpu.get("index") != parsed_index
        or gpu.get("uuid") != fields[1]
        or gpu.get("driver_version") != fields[2]
    ):
        raise ValueError("ncu GPU query output does not match parsed metadata")
    if parsed_index != device_index or fields[1] != gpu_uuid:
        raise ValueError("ncu GPU query output does not match frozen identity")
    _text(gpu.get("driver_version"), "ncu GPU driver version")
    return dict(gpu)


def _ncu_metric_state(
    metadata: dict[str, object],
    paths: dict[str, Path],
    *,
    allow_empty: bool,
) -> tuple[dict[str, str], list[str]]:
    query = metadata.get("metric_query")
    device_index = metadata.get("device_index")
    expected_query = _ncu_query_command(device_index) if isinstance(device_index, int) else None
    if (
        not isinstance(query, dict)
        or query.get("command") != expected_query
        or query.get("return_code") != 0
        or not isinstance(query.get("stdout"), str)
        or not isinstance(query.get("stderr"), str)
    ):
        raise ValueError("ncu metric query provenance is not successful")
    discovered = sorted(
        {
            match.group(1)
            for line in query["stdout"].splitlines()
            if (match := _NCU_METRIC_TOKEN.match(line.strip())) is not None
        }
    )
    supported_raw = metadata.get("supported_metrics", [])
    selected_raw = metadata.get("selected_metrics", {})
    if not isinstance(supported_raw, list) or any(not isinstance(value, str) for value in supported_raw):
        raise ValueError("ncu supported_metrics metadata is invalid")
    if not isinstance(selected_raw, dict):
        raise ValueError("selected NCU metrics must be an object")
    supported = sorted(set(supported_raw))
    if discovered != supported:
        raise ValueError("ncu metric query stdout does not match supported_metrics")
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
    selected: dict[str, str], device_index: int, command: list[str], engine: str
) -> list[str]:
    canonical_order = [selected[role] for role in METRIC_CANDIDATES]
    launch_control = ["--launch-count", "1"]
    nvtx_filter = (
        ["--nvtx", "--nvtx-include", "hybrid_gpu_request/"]
        if engine == "hybrid-auto"
        else []
    )
    replay_mode = "kernel" if engine == "hybrid-auto" else "application"
    return [
        "ncu",
        "--csv",
        "--target-processes",
        "all",
        "--replay-mode",
        replay_mode,
        "--kernel-name-base",
        "demangled",
        "--kernel-name",
        f"regex:.*{re.escape('q5_kernel')}.*",
        *nvtx_filter,
        *launch_control,
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
    command: list[str],
    identity: dict[str, object],
) -> tuple[Path, dict[str, object], dict[str, Path], int, str, dict[str, object]]:
    metadata_path, metadata = _collector_metadata(root, captures_root, raw.get("ncu"), "ncu")
    _validate_collector_identity(metadata, identity, "ncu")
    _validate_collector_execution(metadata, identity, command, "ncu")
    device_index = metadata.get("device_index")
    if isinstance(device_index, bool) or not isinstance(device_index, int) or device_index < 0:
        raise ValueError("ncu device_index must be non-negative")
    if metadata.get("query_command") != _ncu_query_command(device_index):
        raise ValueError("ncu metric query command does not match collector contract")
    version = _tool_version(metadata, "ncu")
    _tool_provenance(metadata, "ncu", version)
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
        root, captures_root, raw, command, identity
    )
    selected, _ = _ncu_metric_state(metadata, paths, allow_empty=False)
    expected_profile_command = _ncu_profile_command(
        selected, device_index, command, str(identity["engine"])
    )
    if metadata.get("profile_command") != expected_profile_command:
        raise ValueError("ncu profile command options do not match collector contract")
    expected_launch_selection = {
        "skip_matching_kernels": 0,
        "profile_matching_kernels": 1,
        "nvtx_include": (
            "hybrid_gpu_request/" if identity["engine"] == "hybrid-auto" else None
        ),
    }
    if metadata.get("launch_selection") != expected_launch_selection:
        raise ValueError("ncu launch selection does not match profiler engine")
    replay = metadata.get("replay")
    expected_replay_mode = (
        "kernel" if identity["engine"] == "hybrid-auto" else "application"
    )
    if (
        metadata.get("return_code") != 0
        or replay
        != {"mode": expected_replay_mode, "return_code": 0, "succeeded": True}
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
        "tool_version_provenance": _tool_provenance(metadata, "ncu", version),
        "collector_execution": dict(metadata["collector_execution"]),
        "metric_query": dict(metadata["metric_query"]),
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
    if code not in KNOWN_NCU_ACCESS_CODES or not _recognized_ncu_unavailable(message):
        raise ValueError(
            "unavailable NCU claim is not a recognized counter permission or hardware support failure"
        )
    return dict(access)


def _recognized_ncu_unavailable(message: str) -> bool:
    if any(pattern.search(message) for pattern in NCU_FATAL_PATTERNS):
        return False
    return any(pattern.search(message) for pattern in NCU_UNAVAILABLE_PATTERNS)


def _compile_ncu_unavailable(
    root: Path,
    captures_root: Path,
    raw: dict[str, object],
    command: list[str],
    identity: dict[str, object],
) -> dict[str, object]:
    metadata_path, metadata, paths, device_index, version, gpu = _ncu_common(
        root, captures_root, raw, command, identity
    )
    stdout_path = _required_file(paths, "profile.stdout.log", "Nsight Compute stdout log")
    stderr_path = _required_file(paths, "profile.stderr.log", "Nsight Compute stderr log")
    selected, _ = _ncu_metric_state(metadata, paths, allow_empty=True)
    profile_command: list[str] | None = None
    if selected:
        profile_command = _ncu_profile_command(
            selected, device_index, command, str(identity["engine"])
        )
        if metadata.get("profile_command") != profile_command:
            raise ValueError("ncu profile command options do not match collector contract")
        expected_launch_selection = {
            "skip_matching_kernels": 0,
            "profile_matching_kernels": 1,
            "nvtx_include": (
                "hybrid_gpu_request/"
                if identity["engine"] == "hybrid-auto"
                else None
            ),
        }
        if metadata.get("launch_selection") != expected_launch_selection:
            raise ValueError("ncu launch selection does not match profiler engine")
    return_code = metadata.get("return_code")
    access_failure = _structured_access_failure(metadata)
    if (not isinstance(return_code, int) or return_code == 0) and access_failure is None:
        raise ValueError("unavailable NCU claim lacks failed collector evidence")
    replay = metadata.get("replay")
    if isinstance(return_code, int) and return_code != 0:
        expected_replay_mode = (
            "kernel" if identity["engine"] == "hybrid-auto" else "application"
        )
        if (
            not isinstance(replay, dict)
            or replay.get("mode") != expected_replay_mode
            or replay.get("return_code") != return_code
            or replay.get("succeeded") is not False
        ):
            raise ValueError("unavailable NCU replay provenance is inconsistent")
        stdout = _read_bytes(stdout_path, "ncu stdout log")
        stderr = _read_bytes(stderr_path, "ncu stderr log")
        if not stdout and not stderr:
            raise ValueError("unavailable NCU claim requires nonempty failure logs")
        message = "\n".join(
            value.decode("utf-8", errors="replace").strip()
            for value in (stderr, stdout)
            if value
        ).strip()
        if not _recognized_ncu_unavailable(message):
            raise ValueError(
                "unavailable NCU claim is not a recognized counter permission or hardware support failure"
            )
        failure: dict[str, object] = {
            "status": "failed",
            "return_code": return_code,
            "message": message,
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
        "tool_version_provenance": _tool_provenance(metadata, "ncu", version),
        "collector_execution": dict(metadata["collector_execution"]),
        "metric_query": dict(metadata["metric_query"]),
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
    evidence, evidence_path = _manifest_reference(
        root, roots["evidence"], raw.get("evidence_manifest"), "evidence_manifest"
    )
    command = _command(raw.get("command"), f"{profile_id}.command")
    identity = _identity(raw, logical, dataset, evidence, command)
    _validate_dataset_manifest(dataset_path, logical)
    _validate_evidence_manifest(
        root,
        evidence_path,
        dataset_path,
        dataset["sha256"],
        identity,
    )
    _validate_app_command(command, logical, dataset_path, identity["execution"])
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
    if index.get("schema_version") != 3:
        raise ValueError("profiles.json schema_version must be 3")
    if index.get("status") != "complete":
        raise ValueError("profiler index status must be complete")
    roots = _declared_roots(root, index)
    profiles_raw = index.get("profiles")
    if not isinstance(profiles_raw, list) or not profiles_raw:
        raise ValueError("profiles must be a non-empty array")
    hybrid_auto = index.get("hybrid_auto")
    hybrid_status = hybrid_auto.get("status") if isinstance(hybrid_auto, dict) else None
    if not isinstance(hybrid_auto, dict):
        raise ValueError("hybrid_auto policy must be an object")
    if hybrid_status != "enabled":
        raise ValueError("hybrid_auto.status must be enabled for canonical coverage")
    _text(hybrid_auto.get("reason"), "hybrid_auto.reason")

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

    canonical_fixed = _canonical_fixed_profiles()
    canonical_fixed_keys = {_logical_key(profile) for profile in canonical_fixed}
    actual_fixed_keys = {
        key
        for key in actual_keys
        if key[1] in CANONICAL_FIXED_ENGINES
    }
    if actual_fixed_keys != canonical_fixed_keys:
        missing = sorted(canonical_fixed_keys - actual_fixed_keys)
        extra = sorted(actual_fixed_keys - canonical_fixed_keys)
        raise ValueError(
            f"canonical profiler coverage mismatch missing={missing} extra={extra}"
        )
    auto_profiles = [
        logical for _, logical in raw_with_logical if logical["engine"] == "hybrid-auto"
    ]
    auto_scales = [str(profile["scale_factor"]) for profile in auto_profiles]
    if sorted(auto_scales) != sorted(CANONICAL_SCALES):
        raise ValueError(
            f"hybrid-auto coverage mismatch expected={list(CANONICAL_SCALES)} "
            f"actual={auto_scales}"
        )

    expected = [*canonical_fixed, *sorted(auto_profiles, key=lambda item: str(item["scale_factor"]))]
    expected_keys = {_logical_key(profile) for profile in expected}
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        raise ValueError(f"profiler coverage mismatch missing={missing} extra={extra}")

    declared_expected = index.get("expected_profiles")
    if not isinstance(declared_expected, list) or not declared_expected:
        raise ValueError("expected_profiles must be a non-empty consistency assertion")
    declared_keys: set[tuple[object, ...]] = set()
    for index_number, item in enumerate(declared_expected):
        _, key = _logical_profile(item, f"expected_profiles[{index_number}]")
        if key in declared_keys:
            raise ValueError(f"duplicate expected logical profile tuple: {key}")
        declared_keys.add(key)
    if declared_keys != expected_keys:
        raise ValueError(
            "self-declared expected_profiles do not match derived canonical coverage"
        )
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
            raise ValueError("profiler index must be the canonical profiles.json")
    _assert_bundle_tree(root)
    index = _load_json(canonical_index, "profiler profile index")
    profiles, expected = _compile_bundle(root, index)
    manifest = {
        "manifest_version": 3,
        "status": "complete",
        "created_at_utc": _utc_now(),
        "source_index": {
            "path": "profiles.json",
            "sha256": sha256_file(canonical_index),
        },
        "expected_profiles": expected,
        "coverage_policy": CANONICAL_COVERAGE_POLICY,
        "profile_count": len(profiles),
        "ncu_unavailable_profiles": sum(
            profile["ncu"]["status"] == "unavailable" for profile in profiles
        ),
        "profiles": profiles,
        "residuals": BUNDLE_RESIDUALS,
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
            if source.get("path") != "profiles.json":
                raise ValueError("source index must be canonical profiles.json")
            index_path = _inside_root(
                root, "profiles.json", "source_index.path", require_file=True
            )
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
    if manifest.get("manifest_version") != 3:
        errors.append("manifest_version must be 3")
    if manifest.get("status") != "complete":
        errors.append("manifest status is not complete")
    if manifest.get("coverage_policy") != CANONICAL_COVERAGE_POLICY:
        errors.append("manifest coverage_policy mismatch")
    if manifest.get("residuals") != BUNDLE_RESIDUALS:
        errors.append("manifest residuals mismatch")
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
