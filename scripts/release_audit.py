#!/usr/bin/env python3

from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path

try:
    from scripts.check_learning_links import check_learning_materials
    from scripts.check_paper import check_paper
    from scripts.evidence_bundle import audit as audit_evidence
    from scripts.validate_claim_ledger import validate_ledger
    from scripts.validate_process_docs import validate_process_docs
except ModuleNotFoundError:
    from check_learning_links import check_learning_materials
    from check_paper import check_paper
    from evidence_bundle import audit as audit_evidence
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
    "docs/artifacts/v5_sf1/manifest.json",
)


def check_required_paths(repo_root: Path) -> list[str]:
    return [
        f"missing required release file: {path}"
        for path in REQUIRED_PATHS
        if not (repo_root / path).is_file()
    ]


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
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            result = audit_evidence(
                argparse.Namespace(directory=root / "docs/artifacts/v5_sf1")
            )
        if result != 0:
            evidence_errors.append("formal V5 evidence audit returned failure")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        evidence_errors.append(str(error))
    _record(report, "formal evidence", evidence_errors)

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
    parser = argparse.ArgumentParser(description="Audit the MEMQ5 V6 release")
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
