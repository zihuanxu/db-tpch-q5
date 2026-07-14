from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from scripts.release_audit import (
    REQUIRED_PATHS,
    check_profiler_checksums,
    check_required_paths,
    run_release_audit,
)


def test_missing_release_metadata_is_a_failure(tmp_path: Path) -> None:
    failures = check_required_paths(tmp_path)
    assert any("LICENSE" in failure for failure in failures)
    assert any("CITATION.cff" in failure for failure in failures)
    assert any(".dockerignore" in failure for failure in failures)


def test_release_metadata_requires_regular_files(tmp_path: Path) -> None:
    (tmp_path / "LICENSE").mkdir()
    failures = check_required_paths(tmp_path)
    assert any("LICENSE" in failure for failure in failures)


def test_release_requires_v7_evidence_paths() -> None:
    assert "docs/artifacts/v7_sf1_resident/manifest.json" in REQUIRED_PATHS
    assert "docs/artifacts/v7_sf10_resident/manifest.json" in REQUIRED_PATHS
    assert "docs/artifacts/v7_profiler/checksums.sha256" in REQUIRED_PATHS


def test_compact_profiler_checksums_detect_tampering(tmp_path: Path) -> None:
    compact = tmp_path / "compact"
    compact.mkdir()
    summary = compact / "summary.json"
    summary.write_text("{}\n", encoding="utf-8")
    import hashlib

    digest = hashlib.sha256(summary.read_bytes()).hexdigest()
    (compact / "checksums.sha256").write_text(
        f"{digest}  summary.json\n", encoding="ascii"
    )
    assert check_profiler_checksums(compact) == []

    summary.write_text('{"tampered":true}\n', encoding="utf-8")
    assert any("checksum mismatch" in error for error in check_profiler_checksums(compact))


def test_repository_release_has_no_engineering_failure() -> None:
    root = Path(__file__).resolve().parents[2]
    report = run_release_audit(root)
    assert report["fail"] == [], report["fail"]
    assert report["external_action_required"]
    assert any("Tencent" in item for item in report["external_action_required"])


def test_release_audit_cli_runs_from_repository_root() -> None:
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, "scripts/release_audit.py", "--json"],
        cwd=root,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert '"engineering_ready": true' in completed.stdout
