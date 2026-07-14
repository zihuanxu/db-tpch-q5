from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

from scripts.package_submission import (
    REQUIRED_SUBMISSION_PATHS,
    audit_archive,
    create_archive,
    excluded_from_submission,
    tracked_files,
)


SCALES = ("1", "10")
ENGINES = ("copy", "managed", "mapped", "hybrid-fixed", "hybrid-auto")
NSYS_REQUIRED_FILES = (
    "profile.nsys-rep",
    "stats_cuda_api_sum.csv",
    "stats_cuda_gpu_kern_sum.csv",
    "stats_cuda_gpu_mem_time_sum.csv",
    "stats_nvtx_sum.csv",
    "profile.stdout.log",
    "profile.stderr.log",
    "stats.stdout.log",
    "stats.stderr.log",
)
NCU_REQUIRED_FILES = (
    "report.csv",
    "selected_metrics.json",
    "profile.stdout.log",
    "profile.stderr.log",
)
COMPACT_ROOT = Path("docs/artifacts/v7_profiler")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rewrite_compact_checksums(compact: Path) -> None:
    paths = sorted(
        path
        for path in compact.rglob("*")
        if path.is_file() and path.name != "checksums.sha256"
    )
    (compact / "checksums.sha256").write_text(
        "".join(
            f"{_sha256(path)}  {path.relative_to(compact).as_posix()}\n"
            for path in paths
        ),
        encoding="ascii",
    )


def _profile_identity(logical: dict[str, object], scale: str) -> dict[str, object]:
    return {
        **logical,
        "session_commit": "b" * 40,
        "dataset_manifest": {
            "path": f"datasets/{scale}/manifest.json",
            "sha256": "c" * 64,
        },
        "evidence_manifest": {
            "path": f"evidence/{scale}/manifest.json",
            "sha256": "d" * 64,
        },
        "oracle_hash": "0123456789abcdef",
        "result_hash": "0123456789abcdef",
        "gpu_uuid": "GPU-synthetic",
        "execution": {
            "cwd": "/repo",
            "executable": {
                "path": "bin/memq5_arrow_session",
                "sha256": "e" * 64,
                "build_commit": "b" * 40,
            },
        },
    }


def _write_compact_fixture(repo_root: Path) -> Path:
    compact = repo_root / COMPACT_ROOT
    compact.mkdir(parents=True)
    coverage: list[dict[str, object]] = []
    profiles: list[dict[str, object]] = []
    for scale in SCALES:
        for engine in ENGINES:
            cpu_ratio = 0.5 if engine.startswith("hybrid-") else 0.0
            logical = {
                "scale_factor": scale,
                "engine": engine,
                "cpu_ratio": cpu_ratio,
                "gpu_ratio": 1.0 - cpu_ratio,
            }
            profile_id = f"sf{scale}-{engine}"
            copied_files: dict[str, str] = {}
            for tool, names in (
                ("nsys", NSYS_REQUIRED_FILES),
                ("ncu", NCU_REQUIRED_FILES),
            ):
                for name in names:
                    relative = Path("captures") / profile_id / tool / name
                    path = compact / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(f"{profile_id} {tool} {name}\n", encoding="utf-8")
                    copied_files[relative.as_posix()] = _sha256(path)
            coverage.append(logical)
            profiles.append(
                {
                    "id": profile_id,
                    "identity": _profile_identity(logical, scale),
                    "app_command": ["memq5_arrow_session"],
                    "copied_files": copied_files,
                    "nsys": {},
                    "ncu": {},
                }
            )
    summary = {
        "schema": "memq5.v7.compact-profiler-evidence",
        "schema_version": 1,
        "source_manifest_sha256": "a" * 64,
        "source_bundle_name": "synthetic-full-bundle",
        "canonical_profile_count": 10,
        "canonical_coverage": coverage,
        "source_orchestration_status": "complete",
        "profiles": profiles,
    }
    (compact / "summary.json").write_text(
        json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8"
    )
    (compact / "README.md").write_text("compact profiler evidence\n", encoding="utf-8")
    _rewrite_compact_checksums(compact)
    return compact


def _all_files(root: Path) -> list[Path]:
    return sorted(path.relative_to(root) for path in root.rglob("*") if path.is_file())


def _write_outer_archive(root: Path, output: Path, root_name: str) -> None:
    manifest_lines: list[str] = []
    with tarfile.open(output, "w:gz") as archive:
        for relative in _all_files(root):
            data = (root / relative).read_bytes()
            info = tarfile.TarInfo(f"{root_name}/{relative.as_posix()}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
            manifest_lines.append(f"{hashlib.sha256(data).hexdigest()}  {relative.as_posix()}")
        manifest = ("\n".join(manifest_lines) + "\n").encode("utf-8")
        info = tarfile.TarInfo(f"{root_name}/MANIFEST.sha256")
        info.size = len(manifest)
        archive.addfile(info, io.BytesIO(manifest))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{digest}  {output.name}\n", encoding="ascii"
    )


def test_submission_filter_rejects_generated_and_unfinished_files() -> None:
    rejected = (
        Path("build/app"),
        Path("data/tpch_sf1/lineitem.tbl"),
        Path("results/run/raw.csv"),
        Path("docs/FINAL_REPORT.md"),
        Path("hashjoin-cpu/main.cpp"),
        Path("docs/artifacts/mvp_sf1/raw.csv"),
    )
    assert all(excluded_from_submission(path) for path in rejected)
    assert not excluded_from_submission(Path("docs/artifacts/v5_sf1/raw.csv"))
    assert not excluded_from_submission(
        Path("docs/artifacts/v7_profiler/captures/sf10-copy/nsys/profile.nsys-rep")
    )


def test_v7_submission_requires_resident_model_and_profiler_evidence() -> None:
    required = {path.as_posix() for path in REQUIRED_SUBMISSION_PATHS}
    assert "docs/artifacts/v7_sf1_resident/manifest.json" in required
    assert "docs/artifacts/v7_sf10_resident/manifest.json" in required
    assert "docs/artifacts/v7_hybrid_model/memq5-v7-hybrid-model.json" in required
    assert "docs/artifacts/v7_profiler/summary.json" in required
    assert "docs/artifacts/v7_profiler/checksums.sha256" in required


def test_archive_contains_internal_manifest_and_sidecar(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("demo\n", encoding="utf-8")
    (root / "LICENSE").write_text("license\n", encoding="utf-8")
    output = tmp_path / "submission.tar.gz"

    create_archive(root, output, "project", [Path("README.md"), Path("LICENSE")])

    with tarfile.open(output, "r:gz") as archive:
        names = set(archive.getnames())
    assert "project/MANIFEST.sha256" in names
    assert output.with_suffix(output.suffix + ".sha256").is_file()
    assert audit_archive(output, "project") == []


def test_tracked_files_preserve_unicode_paths(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", tmp_path], check=True)
    path = tmp_path / "docs/process/01-选题.md"
    path.parent.mkdir(parents=True)
    path.write_text("record\n", encoding="utf-8")
    subprocess.run(["git", "add", path.relative_to(tmp_path)], cwd=tmp_path, check=True)

    assert Path("docs/process/01-选题.md") in tracked_files(tmp_path)


def test_archive_audit_reports_manifest_member_missing(tmp_path: Path) -> None:
    output = tmp_path / "broken.tar.gz"
    manifest = f"{'0' * 64}  missing.txt\n".encode("ascii")
    with tarfile.open(output, "w:gz") as archive:
        info = tarfile.TarInfo("project/MANIFEST.sha256")
        info.size = len(manifest)
        archive.addfile(info, io.BytesIO(manifest))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{digest}  {output.name}\n", encoding="ascii"
    )

    errors = audit_archive(output, "project")
    assert any("missing archive payload" in error for error in errors)


def test_create_archive_rejects_rewritten_checksum_empty_compact_summary(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    compact = _write_compact_fixture(root)
    (compact / "summary.json").write_text("{}\n", encoding="utf-8")
    _rewrite_compact_checksums(compact)

    with pytest.raises(ValueError, match="summary schema"):
        create_archive(root, tmp_path / "submission.tar.gz", "project", _all_files(root))


def test_create_archive_rejects_omitted_compact_capture(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _write_compact_fixture(root)
    omitted = COMPACT_ROOT / "captures/sf10-copy/nsys/profile.nsys-rep"
    files = [path for path in _all_files(root) if path != omitted]

    with pytest.raises(ValueError, match="omits compact profiler file"):
        create_archive(root, tmp_path / "submission.tar.gz", "project", files)


def test_archive_audit_checks_nested_compact_checksum_manifest(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    compact = _write_compact_fixture(root)
    capture = compact / "captures/sf10-copy/nsys/profile.nsys-rep"
    capture.write_text("tampered after compact checksums\n", encoding="utf-8")
    output = tmp_path / "submission.tar.gz"
    _write_outer_archive(root, output, "project")

    errors = audit_archive(output, "project")

    assert any(
        "compact profiler checksum mismatch" in error
        and "captures/sf10-copy/nsys/profile.nsys-rep" in error
        for error in errors
    )


def test_archive_audit_checks_nested_compact_summary_semantics(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    compact = _write_compact_fixture(root)
    (compact / "summary.json").write_text("{}\n", encoding="utf-8")
    _rewrite_compact_checksums(compact)
    output = tmp_path / "submission.tar.gz"
    _write_outer_archive(root, output, "project")

    errors = audit_archive(output, "project")

    assert any("compact profiler summary schema" in error for error in errors)
