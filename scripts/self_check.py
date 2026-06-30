#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CheckResult:
    name: str
    command: list[str]
    returncode: int
    log_path: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_check(name: str, command: list[str], cwd: Path, log_dir: Path) -> CheckResult:
    log_dir.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    log_path = log_dir / f"{name}.log"
    log_path.write_text(completed.stdout, encoding="utf-8")
    status = "ok" if completed.returncode == 0 else "failed"
    print(f"{status}: {name}")
    return CheckResult(name, command, completed.returncode, str(log_path))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MEMQ5 local self-checks")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--log-dir", type=Path, default=Path("results/self_check_logs"))
    parser.add_argument("--skip-cuda", action="store_true")
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    log_dir = args.log_dir
    if not log_dir.is_absolute():
        log_dir = project_root / log_dir

    checks: list[CheckResult] = []
    checks.append(
        run_check(
            "python_compile",
            [sys.executable, "-m", "py_compile", *map(str, sorted(Path("scripts").glob("*.py"))), *map(str, sorted(Path("baselines").glob("*.py")))],
            project_root,
            log_dir,
        )
    )
    checks.append(
        run_check(
            "cmake_configure_cpu",
            ["cmake", "-S", ".", "-B", "build", "-DMEMQ5_ENABLE_CUDA=OFF", "-DMEMQ5_ENABLE_TESTS=ON"],
            project_root,
            log_dir,
        )
    )
    checks.append(run_check("cmake_build_cpu", ["cmake", "--build", "build"], project_root, log_dir))
    checks.append(
        run_check(
            "ctest_cpu",
            ["ctest", "--test-dir", "build", "--output-on-failure"],
            project_root,
            log_dir,
        )
    )

    if not args.skip_cuda and shutil.which("nvcc") is not None:
        checks.append(
            run_check(
                "cmake_configure_cuda",
                [
                    "cmake",
                    "-S",
                    ".",
                    "-B",
                    "build-cuda",
                    "-DMEMQ5_ENABLE_CUDA=ON",
                    "-DMEMQ5_ENABLE_TESTS=ON",
                    "-DCMAKE_CUDA_ARCHITECTURES=75",
                ],
                project_root,
                log_dir,
            )
        )
        checks.append(
            run_check("cmake_build_cuda", ["cmake", "--build", "build-cuda"], project_root, log_dir)
        )
        checks.append(
            run_check(
                "ctest_cuda",
                ["ctest", "--test-dir", "build-cuda", "--output-on-failure"],
                project_root,
                log_dir,
            )
        )

    checks.append(
        run_check(
            "validate_tiny_data",
            [sys.executable, "scripts/validate_tpch_q5_data.py", "--data-dir", "tests/fixtures/tpch_q5_tiny"],
            project_root,
            log_dir,
        )
    )
    checks.append(
        run_check(
            "tiny_pipeline",
            [
                sys.executable,
                "scripts/run_experiment_pipeline.py",
                "--name",
                "self_check_tiny",
                "--data-dir",
                "tests/fixtures/tpch_q5_tiny",
                "--engines",
                "cpu,python",
                "--repeat",
                "1",
                "--force",
            ],
            project_root,
            log_dir,
        )
    )

    report = {
        "ok": all(check.ok for check in checks),
        "checks": [
            {
                "name": check.name,
                "command": check.command,
                "returncode": check.returncode,
                "log_path": check.log_path,
            }
            for check in checks
        ],
    }
    report_path = log_dir / "self_check_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"report: {report_path}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
