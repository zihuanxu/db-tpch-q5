#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def run_command(command: list[str]) -> dict:
    if shutil.which(command[0]) is None:
        return {"available": False, "command": command, "stdout": "", "stderr": ""}
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return {
        "available": True,
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def import_version(module_name: str) -> dict:
    try:
        module = __import__(module_name)
    except ImportError:
        return {"available": False}
    return {"available": True, "version": getattr(module, "__version__", "unknown")}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_metadata(project_root: Path) -> dict[str, object]:
    def git(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=project_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            raise ValueError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
        return completed.stdout.strip()

    dirty_paths = [line for line in git("status", "--short").splitlines() if line]
    return {
        "commit": git("rev-parse", "HEAD"),
        "branch": git("branch", "--show-current"),
        "dirty": bool(dirty_paths),
        "dirty_paths": dirty_paths,
    }


def _gpu_identity(gpu_index: int) -> dict[str, object]:
    query = run_command(
        [
            "nvidia-smi",
            "--query-gpu=index,uuid,name,driver_version",
            "--format=csv,noheader,nounits",
        ]
    )
    devices: list[dict[str, object]] = []
    if query.get("available") and query.get("returncode") == 0:
        for line in str(query.get("stdout", "")).splitlines():
            parts = [part.strip() for part in line.split(",", 3)]
            if len(parts) != 4:
                continue
            try:
                index = int(parts[0])
            except ValueError:
                continue
            devices.append(
                {
                    "index": index,
                    "uuid": parts[1],
                    "name": parts[2],
                    "driver_version": parts[3],
                }
            )
    return {
        "requested_index": gpu_index,
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
        "devices": devices,
        "query": query,
    }


def _cudf_environment(name: str) -> dict[str, object]:
    command = [
        "conda",
        "run",
        "-n",
        name,
        "python",
        "-c",
        (
            "import json,sys; "
            "import cudf; "
            "print(json.dumps({'prefix':sys.prefix,'python':sys.version.split()[0],"
            "'cudf':cudf.__version__},sort_keys=True))"
        ),
    ]
    result = run_command(command)
    details: dict[str, object] | None = None
    if result.get("available") and result.get("returncode") == 0:
        try:
            parsed = json.loads(str(result.get("stdout", "")))
            if isinstance(parsed, dict):
                details = parsed
        except json.JSONDecodeError:
            pass
    return {"name": name, "details": details, "query": result}


def capture(
    *,
    project_root: Path | None = None,
    session_cli: Path | None = None,
    cudf_env: str = "memq5-cudf",
    gpu_index: int = 0,
) -> dict:
    root = (project_root or Path.cwd()).resolve()
    data = {
        "python": {
            "version": sys.version,
            "executable": sys.executable,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "processor": platform.processor(),
        },
        "commands": {
            "uname": run_command(["uname", "-a"]),
            "lscpu": run_command(["lscpu"]),
            "nvidia_smi": run_command(["nvidia-smi"]),
            "nvcc": run_command(["nvcc", "--version"]),
            "cmake": run_command(["cmake", "--version"]),
        },
        "python_packages": {
            "pyarrow": import_version("pyarrow"),
            "duckdb": import_version("duckdb"),
            "cudf": import_version("cudf"),
        },
        "git": _git_metadata(root),
        "cudf_environment": _cudf_environment(cudf_env),
        "gpu": _gpu_identity(gpu_index),
    }
    if session_cli is not None:
        resolved_cli = session_cli.resolve()
        if not resolved_cli.is_file():
            raise ValueError(f"session CLI is missing: {resolved_cli}")
        data["session_cli"] = {
            "path": str(resolved_cli),
            "sha256": _sha256(resolved_cli),
        }
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture benchmark environment metadata")
    parser.add_argument("--output", type=Path, default=Path("results/environment.json"))
    args = parser.parse_args()

    data = capture()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
