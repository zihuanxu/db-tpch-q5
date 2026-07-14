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
    from scripts.export_v7_profiler_evidence import (
        ENGINES as COMPACT_ENGINES,
        IDENTITY_FIELDS as COMPACT_IDENTITY_FIELDS,
        NCU_REQUIRED_FILES as COMPACT_NCU_REQUIRED_FILES,
        NSYS_REQUIRED_FILES as COMPACT_NSYS_REQUIRED_FILES,
        SCALES as COMPACT_SCALES,
        SCHEMA as COMPACT_SCHEMA,
        SCHEMA_VERSION as COMPACT_SCHEMA_VERSION,
    )
    from scripts.v7_evidence_bundle import audit_bundle as audit_v7_evidence
    from scripts.validate_claim_ledger import validate_ledger
    from scripts.validate_process_docs import validate_process_docs
except ModuleNotFoundError:
    from check_learning_links import check_learning_materials
    from check_paper import check_paper
    from export_v7_profiler_evidence import (
        ENGINES as COMPACT_ENGINES,
        IDENTITY_FIELDS as COMPACT_IDENTITY_FIELDS,
        NCU_REQUIRED_FILES as COMPACT_NCU_REQUIRED_FILES,
        NSYS_REQUIRED_FILES as COMPACT_NSYS_REQUIRED_FILES,
        SCALES as COMPACT_SCALES,
        SCHEMA as COMPACT_SCHEMA,
        SCHEMA_VERSION as COMPACT_SCHEMA_VERSION,
    )
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


def _profiler_checksums(directory: Path) -> tuple[dict[Path, str], list[str]]:
    checksum_path = directory / "checksums.sha256"
    if not checksum_path.is_file():
        return {}, ["compact profiler checksums.sha256 is missing"]
    errors: list[str] = []
    expected: dict[Path, str] = {}
    try:
        lines = checksum_path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as error:
        return {}, [f"compact profiler checksum file is unreadable: {error}"]
    for line in lines:
        digest, separator, name = line.partition("  ")
        relative = Path(name)
        if (
            not separator
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not name
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
    return expected, errors


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _logical_profile(
    value: object, label: str, errors: list[str]
) -> tuple[str, str, float, float] | None:
    fields = {"scale_factor", "engine", "cpu_ratio", "gpu_ratio"}
    if not isinstance(value, dict) or not fields.issubset(value):
        errors.append(f"compact profiler summary schema is invalid: {label}")
        return None
    scale = value.get("scale_factor")
    engine = value.get("engine")
    cpu_value = value.get("cpu_ratio")
    gpu_value = value.get("gpu_ratio")
    if (
        scale not in COMPACT_SCALES
        or engine not in COMPACT_ENGINES
        or isinstance(cpu_value, bool)
        or not isinstance(cpu_value, (int, float))
        or isinstance(gpu_value, bool)
        or not isinstance(gpu_value, (int, float))
    ):
        errors.append(f"compact profiler summary schema is invalid: {label}")
        return None
    cpu_ratio = float(cpu_value)
    gpu_ratio = float(gpu_value)
    valid_ratio = abs(cpu_ratio + gpu_ratio - 1.0) <= 1e-12
    if engine in {"copy", "managed", "mapped"}:
        valid_ratio = valid_ratio and (cpu_ratio, gpu_ratio) == (0.0, 1.0)
    elif engine == "hybrid-fixed":
        valid_ratio = valid_ratio and (cpu_ratio, gpu_ratio) == (0.5, 0.5)
    else:
        valid_ratio = valid_ratio and 0.0 < cpu_ratio < 1.0
    if not valid_ratio:
        errors.append(f"compact profiler summary schema is invalid: {label} ratios")
        return None
    return str(scale), str(engine), cpu_ratio, gpu_ratio


def _required_profile_captures(profile_id: str) -> tuple[Path, ...]:
    root = Path("captures") / profile_id
    return tuple(
        [root / "nsys" / name for name in COMPACT_NSYS_REQUIRED_FILES]
        + [root / "ncu" / name for name in COMPACT_NCU_REQUIRED_FILES]
    )


def _check_profiler_summary(
    directory: Path, checksums: dict[Path, str]
) -> list[str]:
    summary_path = directory / "summary.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return [f"compact profiler summary schema is invalid: {error}"]
    if not isinstance(summary, dict):
        return ["compact profiler summary schema is invalid: root must be an object"]

    coverage = summary.get("canonical_coverage")
    profiles = summary.get("profiles")
    schema_valid = (
        summary.get("schema") == COMPACT_SCHEMA
        and type(summary.get("schema_version")) is int
        and summary.get("schema_version") == COMPACT_SCHEMA_VERSION
        and _is_sha256(summary.get("source_manifest_sha256"))
        and isinstance(summary.get("source_bundle_name"), str)
        and bool(summary.get("source_bundle_name"))
        and type(summary.get("canonical_profile_count")) is int
        and summary.get("canonical_profile_count") == 10
        and summary.get("source_orchestration_status") == "complete"
        and isinstance(coverage, list)
        and len(coverage) == 10
        and isinstance(profiles, list)
        and len(profiles) == 10
    )
    if not schema_valid:
        return ["compact profiler summary schema is invalid"]
    assert isinstance(coverage, list)
    assert isinstance(profiles, list)

    errors: list[str] = []
    expected_pairs = {
        (scale, engine) for scale in COMPACT_SCALES for engine in COMPACT_ENGINES
    }
    coverage_by_pair: dict[tuple[str, str], tuple[float, float]] = {}
    for index, item in enumerate(coverage):
        logical = _logical_profile(item, f"canonical_coverage[{index}]", errors)
        if logical is None:
            continue
        scale, engine, cpu_ratio, gpu_ratio = logical
        pair = (scale, engine)
        if pair in coverage_by_pair:
            errors.append(f"duplicate compact profiler canonical coverage: {pair}")
        coverage_by_pair[pair] = (cpu_ratio, gpu_ratio)
    if set(coverage_by_pair) != expected_pairs:
        errors.append("compact profiler canonical 10-profile coverage is incomplete")

    profiles_by_pair: dict[tuple[str, str], tuple[float, float]] = {}
    profile_ids: set[str] = set()
    for index, item in enumerate(profiles):
        if not isinstance(item, dict):
            errors.append(
                f"compact profiler summary schema is invalid: profiles[{index}]"
            )
            continue
        profile_id = item.get("id")
        identity_value = item.get("identity")
        if not isinstance(identity_value, dict) or any(
            field not in identity_value for field in COMPACT_IDENTITY_FIELDS
        ):
            errors.append(
                "compact profiler summary schema is invalid: "
                f"profiles[{index}].identity fields"
            )
            continue
        identity = _logical_profile(
            identity_value, f"profiles[{index}].identity", errors
        )
        app_command = item.get("app_command")
        copied_files = item.get("copied_files")
        if (
            identity is None
            or not isinstance(profile_id, str)
            or not isinstance(app_command, list)
            or not app_command
            or any(not isinstance(argument, str) or not argument for argument in app_command)
            or not isinstance(copied_files, dict)
            or not isinstance(item.get("nsys"), dict)
            or not isinstance(item.get("ncu"), dict)
        ):
            errors.append(
                f"compact profiler summary schema is invalid: profiles[{index}]"
            )
            continue
        scale, engine, cpu_ratio, gpu_ratio = identity
        expected_id = f"sf{scale}-{engine}"
        pair = (scale, engine)
        if profile_id != expected_id or profile_id in profile_ids or pair in profiles_by_pair:
            errors.append(f"invalid or duplicate compact profiler profile id: {profile_id}")
            continue
        profile_ids.add(profile_id)
        profiles_by_pair[pair] = (cpu_ratio, gpu_ratio)
        if coverage_by_pair.get(pair) != (cpu_ratio, gpu_ratio):
            errors.append(f"compact profiler profile identity disagrees with coverage: {profile_id}")

        for name, digest in copied_files.items():
            relative = Path(name) if isinstance(name, str) else Path()
            expected_prefix = Path("captures") / profile_id
            if (
                not isinstance(name, str)
                or not name
                or not _is_sha256(digest)
                or relative.is_absolute()
                or ".." in relative.parts
                or expected_prefix not in relative.parents
                or checksums.get(relative) != digest
            ):
                errors.append(
                    f"invalid compact profiler copied_files entry: {profile_id}: {name}"
                )
        for relative in _required_profile_captures(profile_id):
            if not (directory / relative).is_file() or relative not in checksums:
                errors.append(
                    "missing required compact profiler capture: "
                    f"{relative.as_posix()}"
                )
            elif copied_files.get(relative.as_posix()) != checksums[relative]:
                errors.append(
                    "compact profiler summary does not bind required capture: "
                    f"{relative.as_posix()}"
                )

    if set(profiles_by_pair) != expected_pairs:
        errors.append("compact profiler profiles do not have canonical 10-profile coverage")
    return errors


def audit_compact_profiler_evidence(directory: Path) -> list[str]:
    checksums, errors = _profiler_checksums(directory)
    errors.extend(_check_profiler_summary(directory, checksums))
    return errors


def check_profiler_checksums(directory: Path) -> list[str]:
    return audit_compact_profiler_evidence(directory)


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
        audit_compact_profiler_evidence(root / "docs/artifacts/v7_profiler"),
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
