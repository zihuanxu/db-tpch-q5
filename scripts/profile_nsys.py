#!/usr/bin/env python3
"""Collect reproducible Nsight Systems evidence around one command."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


REPORTS = ["cuda_api_sum", "cuda_gpu_kern_sum", "cuda_gpu_mem_time_sum", "nvtx_sum"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def _application_stdout(stdout: str) -> str:
    json_lines = [
        line.strip()
        for line in stdout.splitlines()
        if line.lstrip().startswith("{")
    ]
    if not json_lines:
        return stdout
    return "\n".join(json_lines) + "\n"


def _nsys_version() -> tuple[str, dict[str, object]]:
    command = ["nsys", "--version"]
    try:
        result = _run(command)
    except OSError as exc:
        result = subprocess.CompletedProcess(command, 127, "", f"launch failed: {exc}")
    version = (result.stdout or result.stderr).strip()
    provenance: dict[str, object] = {
        "command": command,
        "return_code": result.returncode,
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
    }
    return version, provenance


def _files(output_dir: Path) -> dict[str, dict[str, object]]:
    return {
        path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in sorted(output_dir.iterdir())
        if path.is_file() and path.name != "metadata.json"
    }


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


def collect_nsys(command: list[str], output_dir: Path, metadata: dict) -> dict:
    """Profile `command`, export stats on success, and write a provenance manifest."""
    if not command:
        raise ValueError("command must not be empty")
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = output_dir / "profile"
    profile_command = [
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
        str(prefix),
        *command,
    ]
    started_at = _utc_now()
    collector_execution = _profiled_execution(command)
    try:
        profile = _run(profile_command)
    except OSError as exc:
        profile = subprocess.CompletedProcess(profile_command, 127, "", f"launch failed: {exc}")
    tool_stdout = profile.stdout or ""
    (output_dir / "profile.tool.stdout.log").write_text(tool_stdout, encoding="utf-8")
    (output_dir / "profile.stdout.log").write_text(
        _application_stdout(tool_stdout), encoding="utf-8"
    )
    (output_dir / "profile.stderr.log").write_text(profile.stderr or "", encoding="utf-8")

    stats_commands: list[list[str]] = []
    stats_results: list[dict[str, object]] = []
    report_path = prefix.with_suffix(".nsys-rep")
    if profile.returncode == 0 and report_path.exists():
        stats_command = [
            "nsys", "stats", "--force-export=true", "--report", ",".join(REPORTS),
            "--format", "csv", "--output", str(output_dir / "stats"), str(report_path),
        ]
        stats_commands.append(stats_command)
        try:
            stats = _run(stats_command)
        except OSError as exc:
            stats = subprocess.CompletedProcess(stats_command, 127, "", f"launch failed: {exc}")
        (output_dir / "stats.stdout.log").write_text(stats.stdout or "", encoding="utf-8")
        (output_dir / "stats.stderr.log").write_text(stats.stderr or "", encoding="utf-8")
        stats_results.append(
            {
                "command": stats_command,
                "return_code": stats.returncode,
                "stdout": stats.stdout or "",
                "stderr": stats.stderr or "",
            }
        )

    nsys_version, nsys_version_provenance = _nsys_version()
    result: dict[str, object] = {
        "metadata": metadata,
        "collector_execution": collector_execution,
        "started_at_utc": started_at,
        "finished_at_utc": _utc_now(),
        "profile_command": profile_command,
        "stats_commands": stats_commands,
        "return_code": profile.returncode,
        "stats": stats_results,
        "tool_versions": {"nsys": nsys_version},
        "tool_version_provenance": {"nsys": nsys_version_provenance},
        "files": _files(output_dir),
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture one Q5 Nsight Systems report")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--metadata-json", default="{}")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a profiled command is required after --")
    try:
        metadata = json.loads(args.metadata_json)
    except json.JSONDecodeError as exc:
        parser.error(f"invalid --metadata-json: {exc}")
    if not isinstance(metadata, dict):
        parser.error("--metadata-json must decode to a JSON object")

    result = collect_nsys(command, args.output_dir, metadata)
    if result["return_code"] != 0:
        return 1
    if any(stats["return_code"] != 0 for stats in result["stats"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
