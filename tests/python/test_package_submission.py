from __future__ import annotations

import tarfile
import subprocess
import hashlib
import io
from pathlib import Path

from scripts.package_submission import (
    REQUIRED_SUBMISSION_PATHS,
    audit_archive,
    create_archive,
    excluded_from_submission,
    tracked_files,
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
