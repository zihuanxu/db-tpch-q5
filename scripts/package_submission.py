#!/usr/bin/env python3

from __future__ import annotations

import argparse
import tarfile
from pathlib import Path


INCLUDE_DIRS = ["baselines", "docs", "scripts", "src", "tests"]
INCLUDE_FILES = [".gitignore", "CMakeLists.txt", "README.md"]


def add_path(tar: tarfile.TarFile, path: Path, arc_root: str) -> None:
    if path.is_dir():
        for child in sorted(path.rglob("*")):
            if child.is_file() and "__pycache__" not in child.parts:
                tar.add(child, arcname=str(Path(arc_root) / child))
    elif path.is_file():
        tar.add(path, arcname=str(Path(arc_root) / path))


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a MEMQ5 source submission archive")
    parser.add_argument("--output", type=Path, default=Path("dist/memq5_submission.tar.gz"))
    parser.add_argument("--root-name", default="memory-db-tpch-q5")
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(args.output, "w:gz") as tar:
        for file_name in INCLUDE_FILES:
            add_path(tar, Path(file_name), args.root_name)
        for dir_name in INCLUDE_DIRS:
            add_path(tar, Path(dir_name), args.root_name)

    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
