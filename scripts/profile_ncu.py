#!/usr/bin/env python3
"""Discover device-supported Nsight Compute metrics and capture one Q5 request."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.parse_ncu_csv import parse_ncu_csv
except ModuleNotFoundError:
    from parse_ncu_csv import parse_ncu_csv


METRIC_CANDIDATES = {
    "duration": ("gpu__time_duration.sum",),
    "dram_read_bytes": ("dram__bytes_read.sum",),
    "dram_throughput": ("dram__throughput.avg.pct_of_peak_sustained_elapsed",),
    "sm_throughput": ("sm__throughput.avg.pct_of_peak_sustained_elapsed",),
    "achieved_occupancy": ("sm__warps_active.avg.pct_of_peak_sustained_active",),
}
_METRIC_TOKEN = re.compile(r"^([A-Za-z][A-Za-z0-9_]*__[A-Za-z0-9_.]+)")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update({"LC_ALL": "C", "LANG": "C"})
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env=environment,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _files(output_dir: Path) -> dict[str, dict[str, object]]:
    return {
        path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in sorted(output_dir.iterdir())
        if path.is_file() and path.name != "metadata.json"
    }


def _metric_names(query_output: str) -> set[str]:
    return {
        match.group(1)
        for line in query_output.splitlines()
        if (match := _METRIC_TOKEN.match(line.strip())) is not None
    }


def select_metrics(supported_metrics: set[str]) -> dict[str, str]:
    """Choose the first canonical metric supported by the active device per role."""
    selection: dict[str, str] = {}
    for role, candidates in METRIC_CANDIDATES.items():
        metric = next((candidate for candidate in candidates if candidate in supported_metrics), None)
        if metric is None:
            raise ValueError(f"no device-supported metric for {role}")
        selection[role] = metric
    return selection


def _version(command: list[str]) -> tuple[str, dict[str, object]]:
    try:
        result = _run(command)
    except OSError as exc:
        result = subprocess.CompletedProcess(command, 127, "", f"launch failed: {exc}")
    provenance: dict[str, object] = {
        "command": command,
        "return_code": result.returncode,
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
    }
    return (result.stdout or result.stderr).strip(), provenance


def _profiled_execution(command: list[str]) -> dict[str, object]:
    cwd = Path.cwd().resolve()
    command_path = command[0]
    candidate: str | None
    if Path(command_path).is_absolute() or os.sep in command_path:
        candidate = str(
            Path(command_path) if Path(command_path).is_absolute() else cwd / command_path
        )
    else:
        candidate = shutil.which(command_path)
    base: dict[str, object] = {"cwd": str(cwd), "command_path": command_path}
    if candidate is None:
        return {
            **base,
            "status": "unavailable",
            "reason": "executable was not found on PATH",
        }
    resolved = Path(candidate).resolve()
    if not resolved.is_file():
        return {
            **base,
            "status": "unavailable",
            "resolved_path": str(resolved),
            "reason": "resolved executable is not a regular file",
        }
    if not os.access(resolved, os.X_OK):
        return {
            **base,
            "status": "unavailable",
            "resolved_path": str(resolved),
            "reason": "resolved executable is not executable",
        }
    return {
        **base,
        "status": "ok",
        "resolved_path": str(resolved),
        "executable_sha256": _sha256(resolved),
    }


def _gpu_provenance(device_index: int) -> dict[str, object]:
    command = [
        "nvidia-smi",
        f"--id={device_index}",
        "--query-gpu=index,uuid,driver_version",
        "--format=csv,noheader,nounits",
    ]
    base: dict[str, object] = {
        "requested_index": device_index,
        "query_command": command,
    }
    try:
        result = _run(command)
    except OSError as exc:
        return {
            **base,
            "status": "unavailable",
            "return_code": 127,
            "stdout": "",
            "stderr": f"launch failed: {exc}",
        }
    if result.returncode != 0 or not result.stdout.strip():
        return {
            **base,
            "status": "failed",
            "return_code": result.returncode,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
        }
    rows = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    fields = [field.strip() for field in rows[0].split(",")] if len(rows) == 1 else []
    if len(fields) != 3 or not fields[0].isdigit():
        return {
            **base,
            "status": "invalid_output",
            "return_code": result.returncode,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
        }
    actual_index = int(fields[0])
    if actual_index != device_index:
        return {
            **base,
            "status": "device_mismatch",
            "index": actual_index,
            "uuid": fields[1],
            "driver_version": fields[2],
            "return_code": result.returncode,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
        }
    return {
        **base,
        "status": "ok",
        "index": actual_index,
        "uuid": fields[1],
        "driver_version": fields[2],
        "return_code": result.returncode,
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
    }


def _write_manifest(output_dir: Path, manifest: dict[str, object]) -> None:
    manifest["files"] = _files(output_dir)
    (output_dir / "metadata.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def _is_hybrid_auto(command: list[str]) -> bool:
    return any(
        value == "--hybrid-selection"
        and index + 1 < len(command)
        and command[index + 1] == "auto"
        for index, value in enumerate(command)
    )


def collect_ncu(
    command: list[str],
    output_dir: Path,
    q5_kernel: str,
    metadata: dict,
    device_index: int = 0,
) -> dict:
    """Discover metrics, profile one command, and reject failed replay or invalid CSV."""
    if not command:
        raise ValueError("command must not be empty")
    if device_index < 0:
        raise ValueError("device_index must be non-negative")
    request_error = (
        "profiled command must include exactly one --requests option with value 1 (--requests 1)"
    )
    request_options = [index for index, value in enumerate(command) if value == "--requests"]
    if len(request_options) != 1:
        raise ValueError(request_error)
    request_option = request_options[0]
    if request_option + 1 >= len(command) or command[request_option + 1] != "1":
        raise ValueError(request_error)
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = _utc_now()
    collector_execution = _profiled_execution(command)
    query_command = [
        "ncu", "--query-metrics", "--query-metrics-mode", "all",
        "--devices", str(device_index),
    ]
    try:
        query = _run(query_command)
    except OSError as exc:
        query = subprocess.CompletedProcess(query_command, 127, "", f"launch failed: {exc}")
    metric_query = {
        "command": query_command,
        "return_code": query.returncode,
        "stdout": query.stdout or "",
        "stderr": query.stderr or "",
    }
    if query.returncode != 0:
        ncu_version, ncu_version_provenance = _version(["ncu", "--version"])
        failed_manifest: dict[str, object] = {
            "metadata": metadata,
            "started_at_utc": started_at,
            "finished_at_utc": _utc_now(),
            "collector_execution": collector_execution,
            "device_index": device_index,
            "query_command": query_command,
            "metric_query": metric_query,
            "supported_metrics": [],
            "selected_metrics": {},
            "tool_versions": {"ncu": ncu_version},
            "tool_version_provenance": {"ncu": ncu_version_provenance},
            "gpu": _gpu_provenance(device_index),
            "query_failure": True,
        }
        _write_manifest(output_dir, failed_manifest)
        raise RuntimeError(f"ncu metric discovery failed: {(query.stderr or query.stdout).strip()}")
    supported = sorted(_metric_names(query.stdout))
    selected = select_metrics(set(supported))
    (output_dir / "supported_metrics.txt").write_text("\n".join(supported) + "\n", encoding="utf-8")
    (output_dir / "selected_metrics.json").write_text(json.dumps(selected, indent=2, sort_keys=True), encoding="utf-8")

    hybrid_auto = _is_hybrid_auto(command)
    launch_control = ["--launch-count", "1"]
    if hybrid_auto:
        launch_control = ["--launch-skip", "1", *launch_control]
    profile_command = [
        "ncu", "--csv", "--target-processes", "all", "--replay-mode", "application",
        "--kernel-name-base", "demangled", "--kernel-name",
        f"regex:.*{re.escape(q5_kernel)}.*",
        *launch_control,
        "--metrics", ",".join(selected.values()), "--devices", str(device_index), *command,
    ]
    ncu_version, ncu_version_provenance = _version(["ncu", "--version"])
    manifest: dict[str, object] = {
        "metadata": metadata,
        "started_at_utc": started_at,
        "collector_execution": collector_execution,
        "device_index": device_index,
        "query_command": query_command,
        "metric_query": metric_query,
        "supported_metrics": supported,
        "selected_metrics": selected,
        "launch_selection": {
            "skip_matching_kernels": 1 if hybrid_auto else 0,
            "profile_matching_kernels": 1,
        },
        "profile_command": profile_command,
        "tool_versions": {"ncu": ncu_version},
        "tool_version_provenance": {"ncu": ncu_version_provenance},
        "gpu": _gpu_provenance(device_index),
    }
    try:
        profile = _run(profile_command)
    except OSError as exc:
        profile = subprocess.CompletedProcess(profile_command, 127, "", f"launch failed: {exc}")
    (output_dir / "profile.stdout.log").write_text(profile.stdout or "", encoding="utf-8")
    (output_dir / "profile.stderr.log").write_text(profile.stderr or "", encoding="utf-8")
    manifest["return_code"] = profile.returncode
    manifest["replay"] = {"mode": "application", "return_code": profile.returncode, "succeeded": profile.returncode == 0}
    if profile.returncode != 0:
        manifest["finished_at_utc"] = _utc_now()
        _write_manifest(output_dir, manifest)
        raise RuntimeError(f"ncu replay failed: {(profile.stderr or profile.stdout).strip()}")

    report = output_dir / "report.csv"
    report.write_text(profile.stdout, encoding="utf-8")
    manifest["report"] = {"path": report.name, "sha256": _sha256(report)}
    try:
        parsed = parse_ncu_csv(report, q5_kernel)
        missing = sorted(set(selected.values()) - set(parsed["metrics"]))
        if missing:
            raise ValueError(f"profile report missing selected metrics: {', '.join(missing)}")
    except (OSError, ValueError) as exc:
        manifest["parse_error"] = {"type": type(exc).__name__, "message": str(exc)}
        manifest["finished_at_utc"] = _utc_now()
        _write_manifest(output_dir, manifest)
        raise
    manifest["report"]["parsed"] = parsed
    manifest["finished_at_utc"] = _utc_now()
    _write_manifest(output_dir, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture one Q5 Nsight Compute CSV report")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--kernel-name", default="q5_kernel")
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--metadata-json", default="{}")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a profiled command is required after --")
    try:
        metadata = json.loads(args.metadata_json)
    except json.JSONDecodeError as exc:
        parser.error(f"invalid --metadata-json: {exc}")
    collect_ncu(command, args.output_dir, args.kernel_name, metadata, args.device_index)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
