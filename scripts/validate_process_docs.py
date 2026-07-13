#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path


REQUIRED_FILES = [
    "01-选题与研究问题.md",
    "02-系统设计方案.md",
    "03-实验设计.md",
    "04-阶段讨论记录.md",
    "05-实验执行日志.md",
    "06-问题与决策记录.md",
    "07-最终完成情况.md",
]
REQUIRED_SECTIONS = ["**日期范围：**", "## 目标", "## 活动与证据", "## 未解决问题", "## 下一步决定"]


def validate_process_docs(directory: Path) -> list[str]:
    errors: list[str] = []
    for name in REQUIRED_FILES:
        path = directory / name
        if not path.is_file():
            errors.append(f"missing process document: {name}")
            continue
        text = path.read_text(encoding="utf-8")
        for section in REQUIRED_SECTIONS:
            if section not in text:
                errors.append(f"{name} missing section: {section}")
    experiment_log = directory / "05-实验执行日志.md"
    if experiment_log.is_file() and "v5-sf1-final" not in experiment_log.read_text(encoding="utf-8"):
        errors.append("experiment log must name v5-sf1-final")
    tencent = directory / "TENCENT_DOCS.md"
    if not tencent.is_file() or "EXTERNAL_ACTION_REQUIRED" not in tencent.read_text(encoding="utf-8"):
        errors.append("Tencent document state must remain explicit until shared")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate research process documents")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    errors = validate_process_docs(args.directory)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"process documents ok: {len(REQUIRED_FILES)} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
