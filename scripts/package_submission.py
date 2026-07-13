#!/usr/bin/env python3

from __future__ import annotations

import argparse
import subprocess
import tarfile
from pathlib import Path


OPTIONAL_DELIVERY_FILES = [
    Path("docs/DELIVERY_GUIDE.md"),
    Path("docs/DEFENSE_CHEATSHEET.md"),
    Path("docs/FINAL_REPORT.pdf"),
    Path("docs/PROJECT_HANDOVER_GUIDE.md"),
    Path("docs/artifacts/self_check_report.json"),
]

OPTIONAL_DELIVERY_DIRS = [
    Path("docs/paper"),
    Path("docs/artifacts/mvp_sf1"),
]

OPTIONAL_EXCLUDED_SUFFIXES = {".aux", ".log", ".out"}

EXCLUDED_PREFIXES = (
    Path(".superpowers"),
    Path("docs/assets"),
    Path("docs/superpowers"),
    Path("hashjoin-cpu"),
)

EXCLUDED_TRACKED_FILES = {
    Path("docs/BENCHMARK_PROTOCOL.md"),
    Path("docs/COMPLETION_AUDIT.md"),
    Path("docs/CURRENT_STATUS.md"),
    Path("docs/FINAL_IMPLEMENTATION_PLAN.md"),
    Path("docs/FINAL_REPORT.docx"),
    Path("docs/FINAL_REPORT.md"),
    Path("docs/FINAL_REPORT_DRAFT.md"),
    Path("docs/GPU_SERVER_RUNBOOK.md"),
    Path("docs/IMPLEMENTATION_CHECKLIST.md"),
    Path("docs/INTEGRATED_SUBMISSION.docx"),
    Path("docs/INTEGRATED_SUBMISSION.md"),
    Path("docs/REPORT_TEMPLATE.md"),
    Path("docs/SUBMISSION_CHECKLIST.md"),
    Path("docs/TECHNICAL_DECISIONS.md"),
    Path("docs/artifacts/README.md"),
}


def excluded_from_minimal_package(path: Path) -> bool:
    return path in EXCLUDED_TRACKED_FILES or any(
        path == prefix or prefix in path.parents for prefix in EXCLUDED_PREFIXES
    )


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    files = [
        path
        for line in result.stdout.splitlines()
        if line
        for path in [Path(line)]
        if not excluded_from_minimal_package(path)
    ]
    files.extend(path for path in OPTIONAL_DELIVERY_FILES if path.is_file())
    for directory in OPTIONAL_DELIVERY_DIRS:
        if directory.is_dir():
            files.extend(
                path
                for path in directory.rglob("*")
                if path.is_file() and path.suffix not in OPTIONAL_EXCLUDED_SUFFIXES
            )
    return sorted(set(files))


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a MEMQ5 source submission archive")
    parser.add_argument("--output", type=Path, default=Path("dist/memq5_submission.tar.gz"))
    parser.add_argument("--root-name", default="memory-db-tpch-q5")
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    files = tracked_files()
    with tarfile.open(args.output, "w:gz") as tar:
        for path in files:
            if path.is_file():
                tar.add(path, arcname=str(Path(args.root_name) / path))

    print(f"wrote {args.output} ({len(files)} tracked files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
