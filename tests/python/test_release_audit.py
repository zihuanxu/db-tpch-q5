from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

from scripts.release_audit import (
    REQUIRED_PATHS,
    check_profiler_checksums,
    check_required_paths,
    run_release_audit,
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


def _write_compact_fixture(compact: Path) -> None:
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
    _write_compact_fixture(compact)
    summary = compact / "summary.json"
    assert check_profiler_checksums(compact) == []

    summary.write_text('{"tampered":true}\n', encoding="utf-8")
    assert any("checksum mismatch" in error for error in check_profiler_checksums(compact))


def test_compact_profiler_rejects_empty_summary_after_checksum_rewrite(
    tmp_path: Path,
) -> None:
    compact = tmp_path / "compact"
    _write_compact_fixture(compact)
    (compact / "summary.json").write_text("{}\n", encoding="utf-8")
    _rewrite_compact_checksums(compact)

    errors = check_profiler_checksums(compact)

    assert any("summary schema" in error for error in errors)


def test_compact_profiler_rejects_missing_required_capture_after_checksum_rewrite(
    tmp_path: Path,
) -> None:
    compact = tmp_path / "compact"
    _write_compact_fixture(compact)
    (compact / "captures/sf10-copy/nsys/profile.nsys-rep").unlink()
    _rewrite_compact_checksums(compact)

    errors = check_profiler_checksums(compact)

    assert any(
        "missing required compact profiler capture" in error
        and "captures/sf10-copy/nsys/profile.nsys-rep" in error
        for error in errors
    )


def test_compact_profiler_rejects_missing_profile_identity_provenance(
    tmp_path: Path,
) -> None:
    compact = tmp_path / "compact"
    _write_compact_fixture(compact)
    summary_path = compact / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    del summary["profiles"][0]["identity"]["session_commit"]
    summary_path.write_text(json.dumps(summary, sort_keys=True) + "\n", encoding="utf-8")
    _rewrite_compact_checksums(compact)

    errors = check_profiler_checksums(compact)

    assert any("summary schema" in error and "identity" in error for error in errors)


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
