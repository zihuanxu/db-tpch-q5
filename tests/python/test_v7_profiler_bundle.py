from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

import pytest

from scripts.v7_profiler_bundle import audit, finalize, sha256_file


SESSION_COMMIT = "a" * 40
GPU_UUID = "GPU-test-uuid"
ORACLE_HASH = "542abf4003633c7c"
SELECTED_METRICS = {
    "duration": "gpu__time_duration.sum",
    "dram_read_bytes": "dram__bytes_read.sum",
    "dram_throughput": "dram__throughput.avg.pct_of_peak_sustained_elapsed",
    "sm_throughput": "sm__throughput.avg.pct_of_peak_sustained_elapsed",
    "achieved_occupancy": "sm__warps_active.avg.pct_of_peak_sustained_active",
}
ENGINE_COMMANDS = {
    "copy": "gpu-copy",
    "managed": "gpu-managed",
    "mapped": "gpu-mapped",
    "hybrid-fixed": "hybrid-arrow",
    "hybrid-auto": "hybrid-arrow",
}
ENGINE_RANGES = {
    "copy": {"request", "q5_kernel"},
    "managed": {"request", "managed_prefetch", "q5_kernel"},
    "mapped": {"request", "q5_kernel"},
    "hybrid-fixed": {"request", "cpu_scan", "q5_kernel", "merge"},
    "hybrid-auto": {"request", "cpu_scan", "q5_kernel", "merge"},
}
NSYS_REPORTS = ["cuda_api_sum", "cuda_gpu_kern_sum", "cuda_gpu_mem_time_sum", "nvtx_sum"]
NCU_REPORT = """\
"ID","Process ID","Kernel Name","Context","Stream","Metric Name","Metric Value"
"1","42","void q5_kernel()","1","7","gpu__time_duration.sum","1250000"
"1","42","void q5_kernel()","1","7","dram__bytes_read.sum","1048576"
"1","42","void q5_kernel()","1","7","dram__throughput.avg.pct_of_peak_sustained_elapsed","51.25"
"1","42","void q5_kernel()","1","7","sm__throughput.avg.pct_of_peak_sustained_elapsed","62.5"
"1","42","void q5_kernel()","1","7","sm__warps_active.avg.pct_of_peak_sustained_active","48.0"
"""


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _files(directory: Path) -> dict[str, dict[str, object]]:
    return {
        path.name: {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for path in sorted(directory.iterdir())
        if path.is_file() and path.name != "metadata.json"
    }


def _ratio(engine: str, cpu_ratio: float | None) -> tuple[float, float]:
    cpu = (0.5 if engine.startswith("hybrid-") else 0.0) if cpu_ratio is None else cpu_ratio
    return cpu, 1.0 - cpu


class BundleFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.captures = root / "captures"
        self.datasets = root / "datasets"
        self.evidence = root / "evidence"
        for directory in (self.captures, self.datasets, self.evidence):
            directory.mkdir(parents=True, exist_ok=True)
        self.expected: list[dict[str, object]] = []
        self.profiles: list[dict[str, object]] = []

    def _manifests(self, scale: str) -> tuple[dict[str, str], dict[str, str]]:
        dataset_path = self.datasets / scale / "manifest.json"
        evidence_path = self.evidence / scale / "manifest.json"
        if not dataset_path.exists():
            _write_json(dataset_path, {"schema_version": 1, "scale_factor": scale})
        if not evidence_path.exists():
            _write_json(
                evidence_path,
                {
                    "manifest_version": 1,
                    "scale_factor": scale,
                    "dataset_manifest_sha256": sha256_file(dataset_path),
                },
            )
        return (
            {
                "path": dataset_path.relative_to(self.root).as_posix(),
                "sha256": sha256_file(dataset_path),
            },
            {
                "path": evidence_path.relative_to(self.root).as_posix(),
                "sha256": sha256_file(evidence_path),
            },
        )

    def add_profile(
        self,
        scale: str,
        engine: str,
        *,
        cpu_ratio: float | None = None,
        ncu_status: str = "ok",
        ranges: set[str] | None = None,
        capture_id: str | None = None,
    ) -> dict[str, object]:
        cpu, gpu = _ratio(engine, cpu_ratio)
        capture_id = capture_id or f"sf{scale}-{engine}"
        dataset_manifest, evidence_manifest = self._manifests(scale)
        dataset_dir = self.root / Path(dataset_manifest["path"]).parent
        command = [
            "build/memq5_arrow_session",
            "--dataset",
            str(dataset_dir),
            "--engine",
            ENGINE_COMMANDS[engine],
            "--cpu-ratio",
            str(cpu),
            "--requests",
            "1",
        ]
        identity: dict[str, object] = {
            "scale_factor": scale,
            "engine": engine,
            "cpu_ratio": cpu,
            "gpu_ratio": gpu,
            "session_commit": SESSION_COMMIT,
            "dataset_manifest": dataset_manifest,
            "evidence_manifest": evidence_manifest,
            "oracle_hash": ORACLE_HASH,
            "gpu_uuid": GPU_UUID,
        }
        nsys_dir = self.captures / capture_id / "nsys"
        self._write_nsys(nsys_dir, command, identity, ranges or ENGINE_RANGES[engine])
        ncu_dir = self.captures / capture_id / "ncu"
        self._write_ncu(ncu_dir, command, identity, ncu_status)
        logical = {
            "scale_factor": scale,
            "engine": engine,
            "cpu_ratio": cpu,
            "gpu_ratio": gpu,
        }
        profile = {
            "id": capture_id,
            **logical,
            "session_commit": SESSION_COMMIT,
            "dataset_manifest": dataset_manifest,
            "evidence_manifest": evidence_manifest,
            "oracle_hash": ORACLE_HASH,
            "gpu_uuid": GPU_UUID,
            "command": command,
            "nsys": {
                "metadata_path": (nsys_dir / "metadata.json").relative_to(self.root).as_posix()
            },
            "ncu": {
                "status": ncu_status,
                "metadata_path": (ncu_dir / "metadata.json").relative_to(self.root).as_posix(),
            },
        }
        self.expected.append(logical)
        self.profiles.append(profile)
        return profile

    def _write_nsys(
        self,
        directory: Path,
        command: list[str],
        identity: dict[str, object],
        ranges: set[str],
    ) -> None:
        directory.mkdir(parents=True)
        raw_report = directory / "profile.nsys-rep"
        raw_report.write_bytes(b"nsys raw report")
        (directory / "stats_cuda_api_sum.csv").write_text(
            '"Total Time (ns)","Name"\n"100","cudaLaunchKernel"\n', encoding="utf-8"
        )
        (directory / "stats_cuda_gpu_kern_sum.csv").write_text(
            '"Total Time (ns)","Operation"\n"1250","void q5_kernel()"\n',
            encoding="utf-8",
        )
        (directory / "stats_cuda_gpu_mem_time_sum.csv").write_text(
            '"Total Time (ns)","Operation"\n"500","[CUDA memcpy HtoD]"\n',
            encoding="utf-8",
        )
        nvtx_rows = "".join(f'"1000","{name}"\n' for name in sorted(ranges))
        (directory / "stats_nvtx_sum.csv").write_text(
            f'"Total Time (ns)","Range"\n{nvtx_rows}', encoding="utf-8"
        )
        (directory / "profile.stdout.log").write_text("", encoding="utf-8")
        (directory / "profile.stderr.log").write_text("", encoding="utf-8")
        profile_command = [
            "nsys",
            "profile",
            "--force-overwrite=true",
            "--trace=cuda,nvtx,osrt",
            "--sample=none",
            "--output",
            str(directory / "profile"),
            *command,
        ]
        stats_command = [
            "nsys",
            "stats",
            "--force-export=true",
            "--report",
            ",".join(NSYS_REPORTS),
            "--format",
            "csv",
            "--output",
            str(directory / "stats"),
            str(raw_report),
        ]
        metadata = {
            "metadata": identity,
            "profile_command": profile_command,
            "stats_commands": [stats_command],
            "return_code": 0,
            "stats": [{"return_code": 0}],
            "tool_versions": {"nsys": "NVIDIA Nsight Systems 2026.1"},
            "tool_version_provenance": {
                "nsys": {
                    "command": ["nsys", "--version"],
                    "return_code": 0,
                    "stdout": "NVIDIA Nsight Systems 2026.1\n",
                    "stderr": "",
                }
            },
            "files": _files(directory),
        }
        _write_json(directory / "metadata.json", metadata)

    def _write_ncu(
        self,
        directory: Path,
        command: list[str],
        identity: dict[str, object],
        status: str,
    ) -> None:
        directory.mkdir(parents=True)
        _write_json(directory / "selected_metrics.json", SELECTED_METRICS)
        (directory / "supported_metrics.txt").write_text(
            "\n".join(sorted(SELECTED_METRICS.values())) + "\n", encoding="utf-8"
        )
        stdout = NCU_REPORT if status == "ok" else ""
        stderr = "" if status == "ok" else "ERR_NVGPUCTRPERM: counters unavailable\n"
        (directory / "profile.stdout.log").write_text(stdout, encoding="utf-8")
        (directory / "profile.stderr.log").write_text(stderr, encoding="utf-8")
        query_command = [
            "ncu",
            "--query-metrics",
            "--query-metrics-mode",
            "all",
            "--devices",
            "0",
        ]
        profile_command = [
            "ncu",
            "--csv",
            "--target-processes",
            "all",
            "--replay-mode",
            "application",
            "--kernel-name-base",
            "demangled",
            "--kernel-name",
            "regex:.*q5_kernel.*",
            "--metrics",
            ",".join(SELECTED_METRICS.values()),
            "--devices",
            "0",
            *command,
        ]
        metadata: dict[str, object] = {
            "metadata": identity,
            "device_index": 0,
            "query_command": query_command,
            "supported_metrics": sorted(SELECTED_METRICS.values()),
            "selected_metrics": SELECTED_METRICS,
            "profile_command": profile_command,
            "tool_versions": {"ncu": "NVIDIA Nsight Compute 2026.1"},
            "gpu": {
                "status": "ok",
                "requested_index": 0,
                "index": 0,
                "uuid": GPU_UUID,
                "driver_version": "555.1",
                "query_command": [
                    "nvidia-smi",
                    "--id=0",
                    "--query-gpu=index,uuid,driver_version",
                    "--format=csv,noheader,nounits",
                ],
            },
            "return_code": 0 if status == "ok" else 13,
            "replay": {
                "mode": "application",
                "return_code": 0 if status == "ok" else 13,
                "succeeded": status == "ok",
            },
        }
        if status == "ok":
            report = directory / "report.csv"
            report.write_text(NCU_REPORT, encoding="utf-8")
            metadata["report"] = {
                "path": "report.csv",
                "sha256": sha256_file(report),
                "parsed": {
                    "kernel_name": "void q5_kernel()",
                    "metrics": {
                        "gpu__time_duration.sum": 1250000.0,
                        "dram__bytes_read.sum": 1048576.0,
                        "dram__throughput.avg.pct_of_peak_sustained_elapsed": 51.25,
                        "sm__throughput.avg.pct_of_peak_sustained_elapsed": 62.5,
                        "sm__warps_active.avg.pct_of_peak_sustained_active": 48.0,
                    },
                },
            }
        metadata["files"] = _files(directory)
        _write_json(directory / "metadata.json", metadata)

    def write_index(
        self,
        *,
        profiles: list[dict[str, object]] | None = None,
        expected: list[dict[str, object]] | None = None,
        hybrid_auto_status: str | None = None,
    ) -> None:
        expected_profiles = self.expected if expected is None else expected
        if hybrid_auto_status is None:
            hybrid_auto_status = (
                "enabled"
                if any(item["engine"] == "hybrid-auto" for item in expected_profiles)
                else "disabled"
            )
        _write_json(
            self.root / "profiles.json",
            {
                "schema_version": 2,
                "status": "complete",
                "roots": {
                    "captures": "captures",
                    "datasets": "datasets",
                    "evidence": "evidence",
                },
                "hybrid_auto": {"status": hybrid_auto_status},
                "expected_profiles": expected_profiles,
                "profiles": self.profiles if profiles is None else profiles,
            },
        )


def _args(root: Path) -> argparse.Namespace:
    return argparse.Namespace(directory=root, index=None)


def _single_bundle(root: Path, *, engine: str = "copy", ncu_status: str = "ok") -> BundleFixture:
    bundle = BundleFixture(root)
    bundle.add_profile("1", engine, ncu_status=ncu_status)
    bundle.write_index()
    return bundle


def _two_by_five(root: Path) -> BundleFixture:
    bundle = BundleFixture(root)
    for scale in ("1", "10"):
        for engine in ENGINE_COMMANDS:
            bundle.add_profile(scale, engine)
    bundle.write_index()
    return bundle


def test_finalize_and_audit_freeze_exact_two_by_five_coverage(tmp_path: Path) -> None:
    _two_by_five(tmp_path)

    assert finalize(_args(tmp_path)) == 0
    assert audit(_args(tmp_path)) == 0

    manifest = _read_json(tmp_path / "manifest.json")
    assert manifest["status"] == "complete"
    assert manifest["profile_count"] == 10
    assert len(manifest["expected_profiles"]) == 10
    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, dict)
    assert "datasets/1/manifest.json" in artifacts
    assert "evidence/10/manifest.json" in artifacts
    first = manifest["profiles"][0]
    assert first["nsys"]["derivation"]["status"] == "not_cryptographically_proven"


def test_complete_coverage_must_equal_the_explicit_matrix(tmp_path: Path) -> None:
    bundle = _two_by_five(tmp_path)
    bundle.write_index(profiles=bundle.profiles[:-1])

    with pytest.raises(ValueError, match="coverage mismatch"):
        finalize(_args(tmp_path))


@pytest.mark.parametrize("status", ["disabled", "unavailable"])
def test_hybrid_auto_can_be_explicitly_disabled_or_unavailable(
    tmp_path: Path, status: str
) -> None:
    bundle = BundleFixture(tmp_path)
    for scale in ("1", "10"):
        for engine in ("copy", "managed", "mapped", "hybrid-fixed"):
            bundle.add_profile(scale, engine)
    bundle.write_index(hybrid_auto_status=status)

    assert finalize(_args(tmp_path)) == 0
    assert audit(_args(tmp_path)) == 0


def test_duplicate_logical_tuple_is_rejected(tmp_path: Path) -> None:
    bundle = _single_bundle(tmp_path)
    duplicate = copy.deepcopy(bundle.profiles[0])
    duplicate["id"] = "duplicate-logical-profile"
    bundle.write_index(profiles=[*bundle.profiles, duplicate])

    with pytest.raises(ValueError, match="duplicate logical profile tuple"):
        finalize(_args(tmp_path))


def test_capture_metadata_cannot_be_reused_between_profiles(tmp_path: Path) -> None:
    bundle = BundleFixture(tmp_path)
    copy_profile = bundle.add_profile("1", "copy")
    managed_profile = bundle.add_profile("1", "managed")
    managed_profile["nsys"] = copy.deepcopy(copy_profile["nsys"])
    bundle.write_index()

    with pytest.raises(ValueError, match="capture metadata path reused"):
        finalize(_args(tmp_path))


def test_capture_reuse_rejects_lexically_different_equivalent_paths(tmp_path: Path) -> None:
    bundle = BundleFixture(tmp_path)
    copy_profile = bundle.add_profile("1", "copy")
    managed_profile = bundle.add_profile("1", "managed")
    copy_path = copy_profile["nsys"]["metadata_path"]
    managed_profile["nsys"] = {
        "metadata_path": f"{Path(copy_path).parent.as_posix()}/./metadata.json"
    }
    bundle.write_index()

    with pytest.raises(ValueError, match="capture metadata path reused"):
        finalize(_args(tmp_path))


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("scale_factor", "10"),
        ("engine", "mapped"),
        ("cpu_ratio", 0.25),
        ("gpu_ratio", 0.75),
        ("session_commit", "b" * 40),
        (
            "dataset_manifest",
            {"path": "datasets/1/manifest.json", "sha256": "0" * 64},
        ),
        (
            "evidence_manifest",
            {"path": "evidence/1/manifest.json", "sha256": "0" * 64},
        ),
        ("oracle_hash", "0000000000000000"),
        ("gpu_uuid", "GPU-other"),
    ],
)
@pytest.mark.parametrize("tool", ["nsys", "ncu"])
def test_collector_metadata_must_repeat_frozen_identity(
    tmp_path: Path, tool: str, field: str, bad_value: object
) -> None:
    bundle = _single_bundle(tmp_path)
    metadata_path = tmp_path / bundle.profiles[0][tool]["metadata_path"]
    metadata = _read_json(metadata_path)
    identity = metadata["metadata"]
    assert isinstance(identity, dict)
    identity[field] = bad_value
    _write_json(metadata_path, metadata)

    with pytest.raises(ValueError, match="collector metadata identity mismatch"):
        finalize(_args(tmp_path))


def test_dataset_and_evidence_manifest_digests_are_recomputed(tmp_path: Path) -> None:
    bundle = _single_bundle(tmp_path)
    dataset_path = tmp_path / bundle.profiles[0]["dataset_manifest"]["path"]
    dataset_path.write_text("tampered\n", encoding="utf-8")

    with pytest.raises(ValueError, match="dataset manifest digest mismatch"):
        finalize(_args(tmp_path))


@pytest.mark.parametrize("kind", ["dataset_manifest", "evidence_manifest"])
def test_nested_manifest_tampering_fails_audit(tmp_path: Path, kind: str) -> None:
    bundle = _single_bundle(tmp_path)
    assert finalize(_args(tmp_path)) == 0
    path = tmp_path / bundle.profiles[0][kind]["path"]
    path.write_text("tampered\n", encoding="utf-8")

    assert audit(_args(tmp_path)) == 1


def test_profile_paths_must_stay_within_declared_roots(tmp_path: Path) -> None:
    bundle = _single_bundle(tmp_path)
    profile = bundle.profiles[0]
    profile["dataset_manifest"] = copy.deepcopy(profile["evidence_manifest"])
    bundle.write_index()

    with pytest.raises(ValueError, match="outside declared datasets root"):
        finalize(_args(tmp_path))


@pytest.mark.parametrize(
    "setup",
    ["bundle_root", "index", "artifact", "control"],
)
def test_symlinks_are_rejected_before_finalize_writes(
    tmp_path: Path, setup: str
) -> None:
    real_root = tmp_path / "real"
    bundle = _single_bundle(real_root)
    root = real_root
    victim = tmp_path / "victim"
    victim.write_text("do not overwrite\n", encoding="utf-8")
    if setup == "bundle_root":
        root = tmp_path / "bundle-link"
        root.symlink_to(real_root, target_is_directory=True)
    elif setup == "index":
        index = real_root / "profiles.json"
        outside = tmp_path / "outside-index.json"
        outside.write_bytes(index.read_bytes())
        index.unlink()
        index.symlink_to(outside)
    elif setup == "artifact":
        export = real_root / "captures/sf1-copy/nsys/stats_nvtx_sum.csv"
        export.unlink()
        export.symlink_to(victim)
    else:
        (real_root / "manifest.json").symlink_to(victim)

    with pytest.raises(ValueError, match="symlink"):
        finalize(_args(root))
    assert victim.read_text(encoding="utf-8") == "do not overwrite\n"
    assert not (real_root / "manifest.sha256").exists()


def test_audit_rejects_a_symlink_substituted_after_finalize(tmp_path: Path) -> None:
    _single_bundle(tmp_path)
    assert finalize(_args(tmp_path)) == 0
    report = tmp_path / "captures/sf1-copy/ncu/report.csv"
    outside = tmp_path.parent / f"{tmp_path.name}-outside-report.csv"
    outside.write_text(NCU_REPORT, encoding="utf-8")
    report.unlink()
    report.symlink_to(outside)

    assert audit(_args(tmp_path)) == 1


@pytest.mark.parametrize(
    ("scale", "engine", "cpu_ratio"),
    [
        ("SF1", "copy", 0.0),
        ("0", "copy", 0.0),
        ("1", "gpu-copy", 0.0),
        ("1", "copy", 0.5),
        ("1", "hybrid-fixed", 0.0),
        ("1", "hybrid-auto", 1.0),
    ],
)
def test_invalid_scales_engines_and_ratios_are_rejected(
    tmp_path: Path, scale: str, engine: str, cpu_ratio: float
) -> None:
    bundle = _single_bundle(tmp_path)
    logical = bundle.expected[0]
    profile = bundle.profiles[0]
    logical.update(
        {
            "scale_factor": scale,
            "engine": engine,
            "cpu_ratio": cpu_ratio,
            "gpu_ratio": 1.0 - cpu_ratio,
        }
    )
    for field, value in logical.items():
        profile[field] = value
    bundle.write_index()

    with pytest.raises(ValueError, match="invalid (scale factor|profiler engine|ratio)"):
        finalize(_args(tmp_path))


def test_nsys_collector_command_options_are_validated(tmp_path: Path) -> None:
    bundle = _single_bundle(tmp_path)
    metadata_path = tmp_path / bundle.profiles[0]["nsys"]["metadata_path"]
    metadata = _read_json(metadata_path)
    metadata["profile_command"].remove("--sample=none")
    _write_json(metadata_path, metadata)

    with pytest.raises(ValueError, match="Nsight Systems profile command options"):
        finalize(_args(tmp_path))


def test_invented_ncu_metrics_are_rejected_against_canonical_selection(tmp_path: Path) -> None:
    bundle = _single_bundle(tmp_path)
    metadata_path = tmp_path / bundle.profiles[0]["ncu"]["metadata_path"]
    metadata = _read_json(metadata_path)
    selected = dict(SELECTED_METRICS)
    selected["duration"] = "invented__duration.sum"
    metadata["selected_metrics"] = selected
    metadata["supported_metrics"] = sorted(selected.values())
    selected_path = metadata_path.parent / "selected_metrics.json"
    supported_path = metadata_path.parent / "supported_metrics.txt"
    _write_json(selected_path, selected)
    supported_path.write_text("\n".join(sorted(selected.values())) + "\n", encoding="utf-8")
    metadata["files"] = _files(metadata_path.parent)
    _write_json(metadata_path, metadata)

    with pytest.raises(ValueError, match="canonical NCU metric selection"):
        finalize(_args(tmp_path))


def test_ncu_unavailable_requires_failed_collector_evidence(tmp_path: Path) -> None:
    bundle = _single_bundle(tmp_path, ncu_status="unavailable")
    metadata_path = tmp_path / bundle.profiles[0]["ncu"]["metadata_path"]
    metadata = _read_json(metadata_path)
    metadata["return_code"] = 0
    metadata["replay"] = {"mode": "application", "return_code": 0, "succeeded": True}
    stderr = metadata_path.parent / "profile.stderr.log"
    stderr.write_text("", encoding="utf-8")
    metadata["files"] = _files(metadata_path.parent)
    _write_json(metadata_path, metadata)

    with pytest.raises(ValueError, match="unavailable NCU claim lacks failed collector evidence"):
        finalize(_args(tmp_path))


def test_ncu_unavailable_is_bound_to_logs_tool_gpu_and_identity(tmp_path: Path) -> None:
    _single_bundle(tmp_path, ncu_status="unavailable")

    assert finalize(_args(tmp_path)) == 0
    assert audit(_args(tmp_path)) == 0
    manifest = _read_json(tmp_path / "manifest.json")
    ncu = manifest["profiles"][0]["ncu"]
    assert ncu["status"] == "unavailable"
    assert ncu["return_code"] == 13
    assert ncu["profile_command"][-len(manifest["profiles"][0]["command"]) :] == (
        manifest["profiles"][0]["command"]
    )
    assert ncu["stderr"]["sha256"]
    assert ncu["gpu"]["uuid"] == GPU_UUID
    assert ncu["tool_version"] == "NVIDIA Nsight Compute 2026.1"


@pytest.mark.parametrize(
    ("engine", "missing_range"),
    [
        ("managed", "managed_prefetch"),
        ("hybrid-fixed", "cpu_scan"),
        ("hybrid-fixed", "merge"),
        ("hybrid-auto", "cpu_scan"),
    ],
)
def test_required_nvtx_ranges_are_derived_from_the_trusted_engine(
    tmp_path: Path, engine: str, missing_range: str
) -> None:
    ranges = ENGINE_RANGES[engine] - {missing_range}
    bundle = BundleFixture(tmp_path)
    bundle.add_profile("1", engine, ranges=ranges)
    bundle.write_index()

    with pytest.raises(ValueError, match=f"missing required range: {missing_range}"):
        finalize(_args(tmp_path))


def test_audit_rejects_modified_export_or_missing_report(tmp_path: Path) -> None:
    _single_bundle(tmp_path)
    assert finalize(_args(tmp_path)) == 0
    export = tmp_path / "captures/sf1-copy/nsys/stats_nvtx_sum.csv"
    export.write_text("tampered\n", encoding="utf-8")
    assert audit(_args(tmp_path)) == 1

    _single_bundle(tmp_path / "second")
    assert finalize(_args(tmp_path / "second")) == 0
    (tmp_path / "second/captures/sf1-copy/ncu/report.csv").unlink()
    assert audit(_args(tmp_path / "second")) == 1
