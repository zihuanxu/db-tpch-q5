#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
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


def capture() -> dict:
    return {
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
            "duckdb": import_version("duckdb"),
            "cudf": import_version("cudf"),
        },
    }


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
