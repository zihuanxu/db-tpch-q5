#!/usr/bin/env python3

from __future__ import annotations

import argparse
import subprocess
import tarfile
from pathlib import Path


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return [Path(line) for line in result.stdout.splitlines() if line]


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
