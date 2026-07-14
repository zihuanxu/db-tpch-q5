#!/usr/bin/env python3

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path

try:
    from scripts.check_learning_links import check_learning_materials
    from scripts.check_paper import check_paper
    from scripts.v7_evidence_bundle import audit_bundle as audit_v7_evidence
    from scripts.validate_claim_ledger import validate_ledger
    from scripts.validate_process_docs import validate_process_docs
except ModuleNotFoundError:
    from check_learning_links import check_learning_materials
    from check_paper import check_paper
    from v7_evidence_bundle import audit_bundle as audit_v7_evidence
    from validate_claim_ledger import validate_ledger
    from validate_process_docs import validate_process_docs


REQUIRED_PATHS = (
    "README.md",
    "LICENSE",
    "NOTICE",
    "CITATION.cff",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "CMakePresets.json",
    "environment-arrow-cpu.yml",
    "environment-gpu.yml",
    "Dockerfile",
    ".dockerignore",
    ".github/workflows/cpu-ci.yml",
    "scripts/ci_cpu.sh",
    "scripts/package_submission.py",
    "docs/GPU_SERVER_RUNBOOK.md",
    "docs/artifacts/v7_sf1_resident/manifest.json",
    "docs/artifacts/v7_sf1_resident/manifest.sha256",
    "docs/artifacts/v7_sf10_resident/manifest.json",
    "docs/artifacts/v7_sf10_resident/manifest.sha256",
    "docs/artifacts/v7_hybrid_model/memq5-v7-hybrid-model.json",
    "docs/artifacts/v7_profiler/summary.json",
    "docs/artifacts/v7_profiler/checksums.sha256",
)


def check_required_paths(repo_root: Path) -> list[str]:
    return [
        f"missing required release file: {path}"
        for path in REQUIRED_PATHS
        if not (repo_root / path).is_file()
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_profiler_checksums(directory: Path) -> list[str]:
    checksum_path = directory / "checksums.sha256"
    if not checksum_path.is_file():
        return ["compact profiler checksums.sha256 is missing"]
    errors: list[str] = []
    expected: dict[Path, str] = {}
    try:
        lines = checksum_path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as error:
        return [f"compact profiler checksum file is unreadable: {error}"]
    for line in lines:
        digest, separator, name = line.partition("  ")
        relative = Path(name)
        if (
            not separator
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or relative.is_absolute()
            or ".." in relative.parts
            or relative in expected
        ):
            errors.append(f"invalid compact profiler checksum line: {line}")
            continue
        expected[relative] = digest
    actual = {
        path.relative_to(directory)
        for path in directory.rglob("*")
        if path.is_file() and path != checksum_path
    }
    if set(expected) != actual:
        errors.append("compact profiler checksum file set does not match payload")
    for relative, digest in expected.items():
        path = directory / relative
        if not path.is_file():
            errors.append(f"compact profiler file is missing: {relative.as_posix()}")
        elif _sha256(path) != digest:
            errors.append(f"compact profiler checksum mismatch: {relative.as_posix()}")
    return errors


def _record(report: dict[str, list[str]], name: str, errors: list[str]) -> None:
    if errors:
        report["fail"].extend(f"{name}: {error}" for error in errors)
    else:
        report["pass"].append(name)


def run_release_audit(repo_root: Path) -> dict[str, object]:
    root = repo_root.resolve()
    report: dict[str, list[str]] = {
        "pass": [],
        "fail": [],
        "external_action_required": [],
    }

    _record(report, "release metadata", check_required_paths(root))

    evidence_errors: list[str] = []
    for scale in ("1", "10"):
        try:
            result = audit_v7_evidence(root / f"docs/artifacts/v7_sf{scale}_resident")
            if result.get("ok") is not True:
                evidence_errors.extend(
                    f"SF{scale}: {error}" for error in result.get("errors", ["audit failed"])
                )
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            evidence_errors.append(f"SF{scale}: {error}")
    _record(report, "formal evidence", evidence_errors)
    _record(
        report,
        "compact profiler evidence",
        check_profiler_checksums(root / "docs/artifacts/v7_profiler"),
    )

    ledger = validate_ledger(root / "docs/research/CLAIM_LEDGER.md", root)
    _record(report, "claim ledger", list(ledger.errors))
    _record(report, "paper", check_paper(root))
    _record(report, "process records", validate_process_docs(root / "docs/process"))
    _record(
        report,
        "learning materials",
        check_learning_materials(root / "docs/learning", root),
    )

    report["external_action_required"].extend(
        (
            "Tencent Docs: create/share the process document and record its URL and permission state",
            "GitHub: push the final tag/repository and create the public release",
        )
    )
    return {
        **report,
        "engineering_ready": not report["fail"],
        "course_handoff_ready": not report["fail"] and not report["external_action_required"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the MEMQ5 V7 release")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = run_release_audit(args.repo_root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for state in ("pass", "fail", "external_action_required"):
            for item in report[state]:
                print(f"{state.upper()}: {item}")
        print(f"engineering_ready={str(report['engineering_ready']).lower()}")
    return 0 if report["engineering_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
