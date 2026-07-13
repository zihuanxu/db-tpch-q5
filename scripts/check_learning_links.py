#!/usr/bin/env python3

from __future__ import annotations

import argparse
import re
from pathlib import Path


LESSONS = [
    "01-Q5与六表连接.md",
    "02-Apache-Arrow内存布局.md",
    "03-CPU物理计划.md",
    "04-CUDA三种内存模式.md",
    "05-CPU-GPU混合执行.md",
    "06-实验方法与统计.md",
    "07-论文与答辩问答.md",
]
SECTIONS = ["## 你需要先知道", "## 核心原理", "## 代码入口", "## 自己检查", "## 老师可能追问", "## 一分钟复述"]
CODE_PATH_RE = re.compile(r"`((?:src|scripts|tests|baselines|docs|experiments)/[^`]+)`")


def check_learning_materials(directory: Path, repo_root: Path) -> list[str]:
    errors: list[str] = []
    for name in LESSONS:
        path = directory / name
        if not path.is_file():
            errors.append(f"missing lesson: {name}")
            continue
        text = path.read_text(encoding="utf-8")
        for section in SECTIONS:
            if section not in text:
                errors.append(f"{name} missing section: {section}")
        if "**检查答案：**" not in text:
            errors.append(f"{name} missing check answer")
        for reference in CODE_PATH_RE.findall(text):
            if not (repo_root / reference).exists():
                errors.append(f"{name} broken path: {reference}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Check progressive learning materials")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    errors = check_learning_materials(args.directory, args.repo_root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"learning materials ok: {len(LESSONS)} lessons")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
