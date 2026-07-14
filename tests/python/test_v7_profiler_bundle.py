from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts import v7_profiler_bundle
from scripts.profile_ncu import collect_ncu
from scripts.profile_nsys import collect_nsys
from scripts.v7_profiler_bundle import artifact_checksums, audit, finalize, sha256_file


SESSION_COMMIT = "a" * 40
GPU_UUID = "GPU-test-uuid"
ORACLE_HASH = "542abf4003633c7c"
ORACLE_HASHES = {"1": ORACLE_HASH, "10": "b1351a421ba8dcfd"}
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
    "hybrid-auto": {
        "request", "cpu_scan", "q5_kernel", "hybrid_gpu_request", "merge"
    },
}
NSYS_REPORTS = ["cuda_api_sum", "cuda_gpu_kern_sum", "cuda_gpu_mem_time_sum", "nvtx_sum"]
NSYS_REPORT_MAGIC = b"NVIDIA Tegra Profiler Report "
NCU_REPORT = """\
"ID","Process ID","Kernel Name","Context","Stream","Metric Name","Metric Value"
"1","42","void q5_kernel()","1","7","gpu__time_duration.sum","1250000"
"1","42","void q5_kernel()","1","7","dram__bytes_read.sum","1048576"
"1","42","void q5_kernel()","1","7","dram__throughput.avg.pct_of_peak_sustained_elapsed","51.25"
"1","42","void q5_kernel()","1","7","sm__throughput.avg.pct_of_peak_sustained_elapsed","62.5"
"1","42","void q5_kernel()","1","7","sm__warps_active.avg.pct_of_peak_sustained_active","48.0"
"""


def _emit_nsys_exports(
    output_prefix: Path, ranges: set[str] | None = None
) -> None:
    ranges = ranges or {"request", "q5_kernel"}
    nvtx_rows = "".join(f'"1000","{name}"\n' for name in sorted(ranges))
    contents = {
        "cuda_api_sum": '"Total Time (ns)","Name"\n"100","cudaLaunchKernel"\n',
        "cuda_gpu_kern_sum": (
            '"Total Time (ns)","Operation"\n"1250","void q5_kernel()"\n'
        ),
        "cuda_gpu_mem_time_sum": (
            '"Total Time (ns)","Operation"\n"500","[CUDA memcpy HtoD]"\n'
        ),
        "nvtx_sum": f'"Total Time (ns)","Range"\n{nvtx_rows}',
    }
    for report, content in contents.items():
        (output_prefix.parent / f"{output_prefix.name}_{report}.csv").write_text(
            content, encoding="utf-8"
        )


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


def _option(command: list[str], name: str) -> str:
    position = command.index(name)
    return command[position + 1]


@pytest.fixture(autouse=True)
def _dependency_free_nsys_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    executable = "/test/bin/nsys"
    monkeypatch.setattr(v7_profiler_bundle, "_find_nsys", lambda: executable, raising=False)

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if command == [executable, "--version"]:
            return subprocess.CompletedProcess(
                command, 0, "NVIDIA Nsight Systems 2026.1\n", ""
            )
        if len(command) > 1 and command[0] == executable and command[1] == "stats":
            output_prefix = Path(command[command.index("--output") + 1])
            source = Path(command[-1]).parent
            for report in NSYS_REPORTS:
                source_path = source / f"stats_{report}.csv"
                destination = output_prefix.parent / f"{output_prefix.name}_{report}.csv"
                destination.write_bytes(source_path.read_bytes())
            return subprocess.CompletedProcess(command, 0, "re-exported\n", "")
        raise AssertionError(command)

    monkeypatch.setattr(v7_profiler_bundle.subprocess, "run", fake_run)


class BundleFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.captures = root / "captures"
        self.datasets = root / "datasets"
        self.evidence = root / "evidence"
        for directory in (self.captures, self.datasets, self.evidence):
            directory.mkdir(parents=True, exist_ok=True)
        executable = root / "bin/memq5_arrow_session"
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_bytes(b"\x7fELF\x02\x01fixture memq5_arrow_session\n")
        executable.chmod(0o755)
        self.execution = {
            "cwd": str(root.resolve()),
            "executable": {
                "path": "bin/memq5_arrow_session",
                "sha256": sha256_file(executable),
                "build_commit": SESSION_COMMIT,
            },
        }
        self.collector_execution = {
            "status": "ok",
            "cwd": str(root.resolve()),
            "command_path": "bin/memq5_arrow_session",
            "resolved_path": str(executable.resolve()),
            "executable_sha256": sha256_file(executable),
        }
        self.expected: list[dict[str, object]] = []
        self.profiles: list[dict[str, object]] = []

    def _manifests(self, scale: str) -> tuple[dict[str, str], dict[str, str]]:
        dataset_path = self.datasets / scale / "manifest.json"
        evidence_path = self.evidence / scale / "manifest.json"
        if not dataset_path.exists():
            _write_json(
                dataset_path,
                {
                    "format_version": 1,
                    "scale_factor": scale,
                    "batch_rows": 262144,
                    "tables": {"lineitem": {"file": "lineitem.arrow", "rows": 1}},
                },
            )
        if not evidence_path.exists():
            _write_json(
                evidence_path,
                {
                    "manifest_version": 1,
                    "status": "complete",
                    "git": {"commit": SESSION_COMMIT},
                    "dataset": {
                        "path": str(dataset_path.parent.resolve()),
                        "scale_factor": scale,
                        "manifest_sha256": sha256_file(dataset_path),
                    },
                    "correctness": {
                        "ok": True,
                        "expected_hash": ORACLE_HASHES[scale],
                        "observed_hashes": [ORACLE_HASHES[scale]],
                    },
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
            "bin/memq5_arrow_session",
            "--dataset",
            str(dataset_dir),
            "--engine",
            ENGINE_COMMANDS[engine],
            "--requests",
            "1",
        ]
        if engine != "hybrid-auto":
            command.extend(["--cpu-ratio", str(cpu)])
        command.extend(
            ["--hybrid-selection", "auto" if engine == "hybrid-auto" else "fixed"]
            if engine.startswith("hybrid-")
            else []
        )
        oracle_hash = ORACLE_HASHES[scale]
        identity: dict[str, object] = {
            "scale_factor": scale,
            "engine": engine,
            "cpu_ratio": cpu,
            "gpu_ratio": gpu,
            "session_commit": SESSION_COMMIT,
            "dataset_manifest": dataset_manifest,
            "evidence_manifest": evidence_manifest,
            "oracle_hash": oracle_hash,
            "result_hash": oracle_hash,
            "gpu_uuid": GPU_UUID,
            "execution": self.execution,
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
            "oracle_hash": oracle_hash,
            "result_hash": oracle_hash,
            "gpu_uuid": GPU_UUID,
            "execution": self.execution,
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
        raw_report.write_bytes(
            NSYS_REPORT_MAGIC + b"2024@5@1@fixturev0.\n" + b"\x00" * 8192
        )
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
        setup = {
            "record_type": "session_setup",
            "session_id": "00000000-0000-4000-8000-000000000001",
            "lifecycle": "resident",
            "status": "ok",
            "engine": _option(command, "--engine"),
            "dataset": _option(command, "--dataset"),
            "warmup": 0,
            "repeat": 1,
            "selected_cpu_ratio": identity["cpu_ratio"],
        }
        request = {
            "record_type": "request",
            "session_id": setup["session_id"],
            "lifecycle": "resident",
            "status": "ok",
            "request_index": 0,
            "is_warmup": False,
            "selected_cpu_ratio": identity["cpu_ratio"],
            "result_hash": identity["result_hash"],
        }
        (directory / "profile.stdout.log").write_text(
            json.dumps(setup, sort_keys=True)
            + "\n"
            + json.dumps(request, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        (directory / "profile.stderr.log").write_text("", encoding="utf-8")
        (directory / "stats.stdout.log").write_text(
            "Generated Nsight Systems CSV reports\n", encoding="utf-8"
        )
        (directory / "stats.stderr.log").write_text("", encoding="utf-8")
        profile_command = [
            "nsys",
            "profile",
            "--force-overwrite=true",
            "--trace=cuda,nvtx,osrt",
            "--sample=none",
            "--env-var=NSYS_NVTX_PROFILER_REGISTER_ONLY=0",
            "--capture-range=nvtx",
            "--nvtx-capture=measured_request",
            "--capture-range-end=stop",
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
            "collector_execution": self.collector_execution,
            "profile_command": profile_command,
            "stats_commands": [stats_command],
            "return_code": 0,
            "stats": [
                {
                    "command": stats_command,
                    "return_code": 0,
                    "stdout": "Generated Nsight Systems CSV reports\n",
                    "stderr": "",
                }
            ],
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
        query_stdout = "\n".join(sorted(SELECTED_METRICS.values())) + "\n"
        profile_command = [
            "ncu",
            "--csv",
            "--target-processes",
            "all",
            "--replay-mode",
            "kernel" if identity["engine"] == "hybrid-auto" else "application",
            "--kernel-name-base",
            "demangled",
            "--kernel-name",
            "regex:.*q5_kernel.*",
            *(
                ["--nvtx", "--nvtx-include", "hybrid_gpu_request/"]
                if identity["engine"] == "hybrid-auto"
                else []
            ),
            "--launch-count",
            "1",
            "--metrics",
            ",".join(SELECTED_METRICS.values()),
            "--devices",
            "0",
            *command,
        ]
        metadata: dict[str, object] = {
            "metadata": identity,
            "collector_execution": self.collector_execution,
            "device_index": 0,
            "query_command": query_command,
            "metric_query": {
                "command": query_command,
                "return_code": 0,
                "stdout": query_stdout,
                "stderr": "",
            },
            "supported_metrics": sorted(SELECTED_METRICS.values()),
            "selected_metrics": SELECTED_METRICS,
            "launch_selection": {
                "skip_matching_kernels": 0,
                "profile_matching_kernels": 1,
                "nvtx_include": (
                    "hybrid_gpu_request/"
                    if identity["engine"] == "hybrid-auto"
                    else None
                ),
            },
            "profile_command": profile_command,
            "tool_versions": {"ncu": "NVIDIA Nsight Compute 2026.1"},
            "tool_version_provenance": {
                "ncu": {
                    "command": ["ncu", "--version"],
                    "return_code": 0,
                    "stdout": "NVIDIA Nsight Compute 2026.1\n",
                    "stderr": "",
                }
            },
            "gpu": {
                "status": "ok",
                "requested_index": 0,
                "index": 0,
                "uuid": GPU_UUID,
                "driver_version": "555.1",
                "return_code": 0,
                "stdout": f"0, {GPU_UUID}, 555.1\n",
                "stderr": "",
                "query_command": [
                    "nvidia-smi",
                    "--id=0",
                    "--query-gpu=index,uuid,driver_version",
                    "--format=csv,noheader,nounits",
                ],
            },
            "return_code": 0 if status == "ok" else 13,
            "replay": {
                "mode": "kernel" if identity["engine"] == "hybrid-auto" else "application",
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
        roots: dict[str, str] | None = None,
    ) -> None:
        expected_profiles = self.expected if expected is None else expected
        if hybrid_auto_status is None:
            hybrid_auto_status = "enabled"
        _write_json(
            self.root / "profiles.json",
            {
                "schema_version": 3,
                "status": "complete",
                "roots": roots
                or {
                    "captures": "captures",
                    "datasets": "datasets",
                    "evidence": "evidence",
                },
                "hybrid_auto": {
                    "status": hybrid_auto_status,
                    "reason": (
                        "canonical hybrid-auto profiles captured"
                        if hybrid_auto_status == "enabled"
                        else "invalid test-only policy"
                    ),
                },
                "expected_profiles": expected_profiles,
                "profiles": self.profiles if profiles is None else profiles,
            },
        )


def _args(root: Path) -> argparse.Namespace:
    return argparse.Namespace(directory=root, index=None)


def _canonical_specs(*, include_auto: bool = True) -> list[tuple[str, str]]:
    specs = [
        (scale, engine)
        for scale in ("1", "10")
        for engine in ("copy", "managed", "mapped", "hybrid-fixed")
    ]
    if include_auto:
        specs.extend((scale, "hybrid-auto") for scale in ("1", "10"))
    return specs


def _single_bundle(
    root: Path,
    *,
    engine: str = "copy",
    ncu_status: str = "ok",
    ranges: set[str] | None = None,
) -> BundleFixture:
    bundle = BundleFixture(root)
    specs = _canonical_specs()
    target = ("1", engine)
    specs.remove(target)
    for scale, current_engine in [target, *specs]:
        bundle.add_profile(
            scale,
            current_engine,
            ncu_status=ncu_status if (scale, current_engine) == target else "ok",
            ranges=ranges if (scale, current_engine) == target else None,
        )
    bundle.write_index()
    return bundle


def _two_by_five(root: Path) -> BundleFixture:
    bundle = BundleFixture(root)
    for scale, engine in _canonical_specs(include_auto=True):
        bundle.add_profile(scale, engine)
    bundle.write_index()
    return bundle


def _refresh_metadata_files(metadata_path: Path) -> dict[str, object]:
    metadata = _read_json(metadata_path)
    metadata["files"] = _files(metadata_path.parent)
    _write_json(metadata_path, metadata)
    return metadata


def _set_profile_identity(
    root: Path, profile: dict[str, object], field: str, value: object
) -> None:
    profile[field] = copy.deepcopy(value)
    for tool in ("nsys", "ncu"):
        config = profile[tool]
        assert isinstance(config, dict)
        metadata_path = root / str(config["metadata_path"])
        metadata = _read_json(metadata_path)
        identity = metadata["metadata"]
        assert isinstance(identity, dict)
        identity[field] = copy.deepcopy(value)
        _write_json(metadata_path, metadata)


def _refresh_manifest_reference(
    root: Path, profile: dict[str, object], field: str
) -> None:
    reference = profile[field]
    assert isinstance(reference, dict)
    path = root / str(reference["path"])
    _set_profile_identity(
        root,
        profile,
        field,
        {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)},
    )


def _rewrite_control_hashes(root: Path, manifest: dict[str, object]) -> None:
    manifest["artifacts"] = artifact_checksums(root)
    _write_json(root / "manifest.json", manifest)
    (root / "manifest.sha256").write_text(
        f"{sha256_file(root / 'manifest.json')}  manifest.json\n", encoding="ascii"
    )


def test_finalize_and_audit_freeze_exact_two_by_five_coverage(tmp_path: Path) -> None:
    _two_by_five(tmp_path)

    assert finalize(_args(tmp_path)) == 0
    assert audit(_args(tmp_path)) == 0

    manifest = _read_json(tmp_path / "manifest.json")
    assert manifest["status"] == "complete"
    assert manifest["profile_count"] == 10
    assert len(manifest["expected_profiles"]) == 10
    assert manifest["coverage_policy"] == {
        "canonical_matrix": "SF1/SF10 x copy/managed/mapped/hybrid-fixed/hybrid-auto",
        "hybrid_fixed_cpu_ratio": 0.5,
        "hybrid_auto": "mandatory",
    }
    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, dict)
    assert "datasets/1/manifest.json" in artifacts
    assert "evidence/10/manifest.json" in artifacts
    first = manifest["profiles"][0]
    assert first["nsys"]["derivation"]["status"] == "verified"
    assert first["nsys"]["derivation"]["replay_validation"] == "matched"
    assert {item["id"] for item in manifest["residuals"]} >= {
        "nsys-export-linkage",
        "executable-build-commit",
        "concurrent-bundle-root-replacement",
    }


@pytest.mark.parametrize("ncu_status", ["ok", "unavailable"])
def test_collectors_generate_bundle_compatible_provenance_directly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ncu_status: str
) -> None:
    bundle = _two_by_five(tmp_path)
    profile = next(
        item
        for item in bundle.profiles
        if item["scale_factor"] == "1" and item["engine"] == "hybrid-auto"
    )
    command = list(profile["command"])
    old_nsys = profile["nsys"]
    assert isinstance(old_nsys, dict)
    old_nsys_dir = (tmp_path / str(old_nsys["metadata_path"])).parent
    app_stdout = (old_nsys_dir / "profile.stdout.log").read_text(encoding="utf-8")
    identity = {
        key: copy.deepcopy(profile[key])
        for key in (
            "scale_factor",
            "engine",
            "cpu_ratio",
            "gpu_ratio",
            "session_commit",
            "dataset_manifest",
            "evidence_manifest",
            "oracle_hash",
            "result_hash",
            "gpu_uuid",
            "execution",
        )
    }
    direct_nsys = tmp_path / "captures/direct-collector/nsys"
    direct_ncu = tmp_path / "captures/direct-collector/ncu"
    supported = "\n".join(sorted(SELECTED_METRICS.values())) + "\n"
    monkeypatch.chdir(tmp_path)

    def fake_run(command_line: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        tool = command_line[0]
        if tool in {"nsys", "/test/bin/nsys"}:
            if command_line[1:] == ["--version"]:
                return subprocess.CompletedProcess(
                    command_line, 0, "NVIDIA Nsight Systems 2026.1\n", ""
                )
            if command_line[1] == "profile":
                prefix = Path(command_line[command_line.index("--output") + 1])
                prefix.with_suffix(".nsys-rep").write_bytes(
                    NSYS_REPORT_MAGIC + b"2026@1@collector\n" + b"\x00" * 8192
                )
                return subprocess.CompletedProcess(command_line, 0, app_stdout, "")
            if command_line[1] == "stats":
                prefix = Path(command_line[command_line.index("--output") + 1])
                source = Path(command_line[-1]).parent
                source_exports = [source / f"stats_{report}.csv" for report in NSYS_REPORTS]
                if all(path.exists() for path in source_exports):
                    for report, source_path in zip(NSYS_REPORTS, source_exports, strict=True):
                        (prefix.parent / f"{prefix.name}_{report}.csv").write_bytes(
                            source_path.read_bytes()
                        )
                else:
                    _emit_nsys_exports(prefix, ENGINE_RANGES[str(profile["engine"])])
                return subprocess.CompletedProcess(command_line, 0, "stats exported\n", "")
        if tool == "ncu":
            if command_line[1:] == ["--version"]:
                return subprocess.CompletedProcess(
                    command_line, 0, "NVIDIA Nsight Compute 2026.1", ""
                )
            if command_line[1:2] == ["--query-metrics"]:
                return subprocess.CompletedProcess(command_line, 0, supported, "")
            if command_line[1] == "--csv":
                if ncu_status == "unavailable":
                    return subprocess.CompletedProcess(
                        command_line,
                        13,
                        "",
                        "ERR_NVGPUCTRPERM: performance counter permission denied\n",
                    )
                return subprocess.CompletedProcess(command_line, 0, NCU_REPORT, "")
        if tool == "nvidia-smi":
            return subprocess.CompletedProcess(
                command_line, 0, f"0, {GPU_UUID}, 555.1\n", ""
            )
        raise AssertionError(command_line)

    monkeypatch.setattr(subprocess, "run", fake_run)
    collect_nsys(command, direct_nsys, identity)
    if ncu_status == "ok":
        collect_ncu(command, direct_ncu, "q5_kernel", identity)
    else:
        with pytest.raises(RuntimeError, match="replay failed"):
            collect_ncu(command, direct_ncu, "q5_kernel", identity)
    profile["nsys"] = {
        "metadata_path": (direct_nsys / "metadata.json").relative_to(tmp_path).as_posix()
    }
    profile["ncu"] = {
        "status": ncu_status,
        "metadata_path": (direct_ncu / "metadata.json").relative_to(tmp_path).as_posix(),
    }
    bundle.write_index()

    assert finalize(_args(tmp_path)) == 0
    assert audit(_args(tmp_path)) == 0
    nsys_metadata = _read_json(direct_nsys / "metadata.json")
    ncu_metadata = _read_json(direct_ncu / "metadata.json")
    assert nsys_metadata["stats"][0]["command"] == nsys_metadata["stats_commands"][0]
    assert ncu_metadata["metric_query"]["stdout"] == supported
    assert ncu_metadata["gpu"]["stdout"] == f"0, {GPU_UUID}, 555.1\n"
    assert ncu_metadata["launch_selection"] == {
        "skip_matching_kernels": 0,
        "profile_matching_kernels": 1,
        "nvtx_include": "hybrid_gpu_request/",
    }


def test_hybrid_auto_command_binds_selected_ratio_without_a_fixed_ratio_option(
    tmp_path: Path,
) -> None:
    bundle = _single_bundle(tmp_path, engine="hybrid-auto")
    profile = bundle.profiles[0]

    assert profile["engine"] == "hybrid-auto"
    assert "--cpu-ratio" not in profile["command"]
    assert finalize(_args(tmp_path)) == 0
    assert audit(_args(tmp_path)) == 0


def test_complete_coverage_must_equal_the_explicit_matrix(tmp_path: Path) -> None:
    bundle = _two_by_five(tmp_path)
    bundle.write_index(profiles=bundle.profiles[:-1])

    with pytest.raises(ValueError, match="coverage mismatch"):
        finalize(_args(tmp_path))


def test_self_declared_expected_profiles_cannot_shrink_canonical_fixed_matrix(
    tmp_path: Path,
) -> None:
    bundle = _single_bundle(tmp_path)
    bundle.write_index(profiles=bundle.profiles[:1], expected=bundle.expected[:1])

    with pytest.raises(ValueError, match="canonical profiler coverage mismatch"):
        finalize(_args(tmp_path))


def test_enabled_hybrid_auto_requires_both_canonical_scales(tmp_path: Path) -> None:
    bundle = BundleFixture(tmp_path)
    for scale, engine in _canonical_specs(include_auto=False):
        bundle.add_profile(scale, engine)
    bundle.add_profile("1", "hybrid-auto")
    bundle.write_index(hybrid_auto_status="enabled")

    with pytest.raises(ValueError, match="hybrid-auto coverage mismatch"):
        finalize(_args(tmp_path))


def test_fixed_matrix_rejects_noncanonical_hybrid_ratio(tmp_path: Path) -> None:
    bundle = _single_bundle(tmp_path, engine="hybrid-fixed")
    profile = bundle.profiles[0]
    profile["cpu_ratio"] = 0.25
    profile["gpu_ratio"] = 0.75
    bundle.expected[0] = {
        "scale_factor": "1",
        "engine": "hybrid-fixed",
        "cpu_ratio": 0.25,
        "gpu_ratio": 0.75,
    }
    bundle.write_index()

    with pytest.raises(
        ValueError, match="(canonical profiler coverage mismatch|invalid ratio for hybrid-fixed)"
    ):
        finalize(_args(tmp_path))


@pytest.mark.parametrize("status", ["disabled", "unavailable"])
def test_hybrid_auto_cannot_be_disabled_or_unavailable(
    tmp_path: Path, status: str
) -> None:
    bundle = BundleFixture(tmp_path)
    for scale in ("1", "10"):
        for engine in ("copy", "managed", "mapped", "hybrid-fixed"):
            bundle.add_profile(scale, engine)
    bundle.write_index(hybrid_auto_status=status)

    with pytest.raises(ValueError, match="hybrid_auto.status must be enabled"):
        finalize(_args(tmp_path))


def test_hybrid_auto_omission_fails_even_with_an_explicit_policy_reason(tmp_path: Path) -> None:
    bundle = _single_bundle(tmp_path)
    index = _read_json(tmp_path / "profiles.json")
    index["hybrid_auto"] = {
        "status": "disabled",
        "reason": "capture deliberately omitted",
    }
    _write_json(tmp_path / "profiles.json", index)

    with pytest.raises(ValueError, match="hybrid_auto.status must be enabled"):
        finalize(_args(tmp_path))


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
        ("result_hash", "0000000000000000"),
        ("gpu_uuid", "GPU-other"),
        ("execution", {}),
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


@pytest.mark.parametrize("field", ["oracle_hash", "result_hash", "session_commit"])
def test_profile_identity_must_match_evidence_and_app_result(
    tmp_path: Path, field: str
) -> None:
    bundle = _single_bundle(tmp_path)
    profile = bundle.profiles[0]
    replacement = "b" * 40 if field == "session_commit" else "0000000000000000"
    _set_profile_identity(tmp_path, profile, field, replacement)
    bundle.write_index()

    with pytest.raises(ValueError, match="(evidence manifest|app result|session commit|oracle)"):
        finalize(_args(tmp_path))


@pytest.mark.parametrize("manifest_kind", ["dataset_scale", "evidence_scale", "evidence_commit"])
def test_real_manifest_schema_is_bound_to_profile_identity(
    tmp_path: Path, manifest_kind: str
) -> None:
    bundle = _single_bundle(tmp_path)
    profile = bundle.profiles[0]
    dataset_ref = profile["dataset_manifest"]
    evidence_ref = profile["evidence_manifest"]
    assert isinstance(dataset_ref, dict) and isinstance(evidence_ref, dict)
    dataset_path = tmp_path / str(dataset_ref["path"])
    evidence_path = tmp_path / str(evidence_ref["path"])
    if manifest_kind == "dataset_scale":
        dataset = _read_json(dataset_path)
        dataset["scale_factor"] = "10"
        _write_json(dataset_path, dataset)
        evidence = _read_json(evidence_path)
        evidence_dataset = evidence["dataset"]
        assert isinstance(evidence_dataset, dict)
        evidence_dataset["manifest_sha256"] = sha256_file(dataset_path)
        _write_json(evidence_path, evidence)
        _refresh_manifest_reference(tmp_path, profile, "dataset_manifest")
    else:
        evidence = _read_json(evidence_path)
        if manifest_kind == "evidence_scale":
            evidence_dataset = evidence["dataset"]
            assert isinstance(evidence_dataset, dict)
            evidence_dataset["scale_factor"] = "10"
        else:
            git = evidence["git"]
            assert isinstance(git, dict)
            git["commit"] = "b" * 40
        _write_json(evidence_path, evidence)
    _refresh_manifest_reference(tmp_path, profile, "evidence_manifest")
    bundle.write_index()

    with pytest.raises(ValueError, match="(dataset|evidence).*(scale|commit)"):
        finalize(_args(tmp_path))


@pytest.mark.parametrize("field", ["engine", "dataset", "selected_cpu_ratio"])
def test_app_stdout_jsonl_is_bound_to_command_and_identity(tmp_path: Path, field: str) -> None:
    bundle = _single_bundle(tmp_path)
    profile = bundle.profiles[0]
    config = profile["nsys"]
    assert isinstance(config, dict)
    metadata_path = tmp_path / str(config["metadata_path"])
    stdout_path = metadata_path.parent / "profile.stdout.log"
    records = [json.loads(line) for line in stdout_path.read_text(encoding="utf-8").splitlines()]
    if field == "engine":
        records[0][field] = "gpu-mapped"
    elif field == "dataset":
        records[0][field] = str(tmp_path / "datasets/10")
    else:
        records[0][field] = 0.25
        records[1][field] = 0.25
    stdout_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    _refresh_metadata_files(metadata_path)

    with pytest.raises(ValueError, match="app stdout"):
        finalize(_args(tmp_path))


def test_app_stdout_result_hash_cannot_be_replaced(tmp_path: Path) -> None:
    bundle = _single_bundle(tmp_path)
    profile = bundle.profiles[0]
    config = profile["nsys"]
    assert isinstance(config, dict)
    metadata_path = tmp_path / str(config["metadata_path"])
    stdout_path = metadata_path.parent / "profile.stdout.log"
    records = [json.loads(line) for line in stdout_path.read_text(encoding="utf-8").splitlines()]
    records[1]["result_hash"] = "0000000000000000"
    stdout_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    _refresh_metadata_files(metadata_path)

    with pytest.raises(ValueError, match="app result_hash"):
        finalize(_args(tmp_path))


@pytest.mark.parametrize("attack", ["sha256", "build_commit", "cwd"])
def test_execution_provenance_is_verified_against_real_executable(
    tmp_path: Path, attack: str
) -> None:
    bundle = _single_bundle(tmp_path)
    profile = bundle.profiles[0]
    execution = copy.deepcopy(profile["execution"])
    assert isinstance(execution, dict)
    executable = execution["executable"]
    assert isinstance(executable, dict)
    if attack == "sha256":
        executable["sha256"] = "0" * 64
    elif attack == "build_commit":
        executable["build_commit"] = "b" * 40
    else:
        execution["cwd"] = str(tmp_path.parent.resolve())
    _set_profile_identity(tmp_path, profile, "execution", execution)
    bundle.write_index()

    with pytest.raises(ValueError, match="(executable|build commit|working directory)"):
        finalize(_args(tmp_path))


def test_unrelated_executable_cannot_be_relabelled_with_valid_hash(tmp_path: Path) -> None:
    bundle = _single_bundle(tmp_path)
    profile = bundle.profiles[0]
    old_command = list(profile["command"])
    unrelated = Path("/usr/bin/true")
    new_command = [str(unrelated), *old_command[1:]]
    profile["command"] = new_command
    execution = copy.deepcopy(profile["execution"])
    assert isinstance(execution, dict)
    executable = execution["executable"]
    assert isinstance(executable, dict)
    executable.update({"path": str(unrelated), "sha256": sha256_file(unrelated)})
    _set_profile_identity(tmp_path, profile, "execution", execution)
    for tool in ("nsys", "ncu"):
        config = profile[tool]
        assert isinstance(config, dict)
        metadata_path = tmp_path / str(config["metadata_path"])
        metadata = _read_json(metadata_path)
        profile_command = metadata["profile_command"]
        assert isinstance(profile_command, list)
        metadata["profile_command"] = [*profile_command[: -len(old_command)], *new_command]
        _write_json(metadata_path, metadata)
    bundle.write_index()

    with pytest.raises(ValueError, match="memq5_arrow_session executable"):
        finalize(_args(tmp_path))


def test_profiled_executable_must_be_elf(tmp_path: Path) -> None:
    bundle = _single_bundle(tmp_path)
    profile = bundle.profiles[0]
    executable = tmp_path / "bin/memq5_arrow_session"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    execution = copy.deepcopy(profile["execution"])
    assert isinstance(execution, dict)
    executable_provenance = execution["executable"]
    assert isinstance(executable_provenance, dict)
    executable_provenance["sha256"] = sha256_file(executable)
    _set_profile_identity(tmp_path, profile, "execution", execution)
    for tool in ("nsys", "ncu"):
        config = profile[tool]
        assert isinstance(config, dict)
        metadata_path = tmp_path / str(config["metadata_path"])
        metadata = _read_json(metadata_path)
        collector_execution = metadata["collector_execution"]
        assert isinstance(collector_execution, dict)
        collector_execution["executable_sha256"] = sha256_file(executable)
        _write_json(metadata_path, metadata)
    bundle.write_index()

    with pytest.raises(ValueError, match="ELF"):
        finalize(_args(tmp_path))


def test_collector_executable_path_must_match_frozen_execution(tmp_path: Path) -> None:
    bundle = _single_bundle(tmp_path)
    profile = bundle.profiles[0]
    config = profile["nsys"]
    assert isinstance(config, dict)
    metadata_path = tmp_path / str(config["metadata_path"])
    metadata = _read_json(metadata_path)
    collector_execution = metadata["collector_execution"]
    assert isinstance(collector_execution, dict)
    collector_execution["resolved_path"] = str((tmp_path / "bin/other").resolve())
    _write_json(metadata_path, metadata)

    with pytest.raises(ValueError, match="collector executable"):
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
    "roots",
    [
        {"captures": ".", "datasets": ".", "evidence": "."},
        {"captures": "captures", "datasets": "captures", "evidence": "evidence"},
    ],
)
def test_declared_roots_are_fixed_and_nonoverlapping(
    tmp_path: Path, roots: dict[str, str]
) -> None:
    bundle = _single_bundle(tmp_path)
    bundle.write_index(roots=roots)

    with pytest.raises(ValueError, match="roots must be exactly"):
        finalize(_args(tmp_path))


def test_audit_requires_the_canonical_profiles_index(tmp_path: Path) -> None:
    _single_bundle(tmp_path)
    assert finalize(_args(tmp_path)) == 0
    alternate = tmp_path / "alternate-profiles.json"
    alternate.write_bytes((tmp_path / "profiles.json").read_bytes())
    manifest = _read_json(tmp_path / "manifest.json")
    manifest["source_index"] = {
        "path": alternate.name,
        "sha256": sha256_file(alternate),
    }
    _rewrite_control_hashes(tmp_path, manifest)

    assert audit(_args(tmp_path)) == 1


def test_control_io_uses_dirfd_nofollow_static_protection() -> None:
    source = Path(v7_profiler_bundle.__file__).read_text(encoding="utf-8")

    assert hasattr(os, "O_NOFOLLOW")
    assert "dir_fd=" in source
    assert "src_dir_fd=" in source
    assert "dst_dir_fd=" in source
    assert "os.O_NOFOLLOW" in source or 'getattr(os, "O_NOFOLLOW"' in source


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


def test_nsys_rejects_text_placeholder_as_raw_report(tmp_path: Path) -> None:
    bundle = _single_bundle(tmp_path)
    profile = bundle.profiles[0]
    config = profile["nsys"]
    assert isinstance(config, dict)
    metadata_path = tmp_path / str(config["metadata_path"])
    raw_report = metadata_path.parent / "profile.nsys-rep"
    raw_report.write_bytes(b"nsys raw report\n" * 1024)
    _refresh_metadata_files(metadata_path)

    with pytest.raises(ValueError, match="Nsight Systems raw report signature"):
        finalize(_args(tmp_path))


def test_nsys_kernel_export_must_contain_q5_kernel(tmp_path: Path) -> None:
    bundle = _single_bundle(tmp_path)
    profile = bundle.profiles[0]
    config = profile["nsys"]
    assert isinstance(config, dict)
    metadata_path = tmp_path / str(config["metadata_path"])
    kernel_csv = metadata_path.parent / "stats_cuda_gpu_kern_sum.csv"
    kernel_csv.write_text(
        '"Total Time (ns)","Operation"\n"1250","void unrelated_kernel()"\n',
        encoding="utf-8",
    )
    _refresh_metadata_files(metadata_path)

    with pytest.raises(ValueError, match="q5_kernel"):
        finalize(_args(tmp_path))


def test_nsys_stats_success_requires_exact_command_and_output_provenance(
    tmp_path: Path,
) -> None:
    bundle = _single_bundle(tmp_path)
    profile = bundle.profiles[0]
    config = profile["nsys"]
    assert isinstance(config, dict)
    metadata_path = tmp_path / str(config["metadata_path"])
    metadata = _read_json(metadata_path)
    stats = metadata["stats"]
    assert isinstance(stats, list) and isinstance(stats[0], dict)
    stats[0]["command"] = ["nsys", "stats", "unrelated.nsys-rep"]
    _write_json(metadata_path, metadata)

    with pytest.raises(ValueError, match="stats.*provenance"):
        finalize(_args(tmp_path))


def test_nsys_reexport_mismatch_fails_when_compatible_tool_is_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _single_bundle(tmp_path)
    monkeypatch.setattr(v7_profiler_bundle, "_find_nsys", lambda: "/usr/bin/nsys")

    def fake_run(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        if command == ["/usr/bin/nsys", "--version"]:
            return subprocess.CompletedProcess(
                command, 0, "NVIDIA Nsight Systems 2026.1\n", ""
            )
        output_prefix = Path(command[command.index("--output") + 1])
        source = Path(command[-1]).parent
        for report in NSYS_REPORTS:
            data = (source / f"stats_{report}.csv").read_bytes()
            if report == "cuda_api_sum":
                data += b"tampered re-export\n"
            (output_prefix.parent / f"{output_prefix.name}_{report}.csv").write_bytes(data)
        return subprocess.CompletedProcess(command, 0, "re-exported\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ValueError, match="Nsight Systems re-export mismatch"):
        finalize(_args(tmp_path))


def test_finalize_requires_nsys_on_the_current_audit_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _single_bundle(tmp_path)
    monkeypatch.setattr(v7_profiler_bundle, "_find_nsys", lambda: None)

    with pytest.raises(ValueError, match="compatible nsys.*required"):
        finalize(_args(tmp_path))


def test_audit_is_unverified_without_nsys_on_the_current_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _single_bundle(tmp_path)
    assert finalize(_args(tmp_path)) == 0
    monkeypatch.setattr(v7_profiler_bundle, "_find_nsys", lambda: None)

    assert audit(_args(tmp_path)) == 1


def test_finalize_rejects_an_incompatible_nsys_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _single_bundle(tmp_path)

    def incompatible_version(
        command: list[str], **_: object
    ) -> subprocess.CompletedProcess[str]:
        assert command == ["/test/bin/nsys", "--version"]
        return subprocess.CompletedProcess(command, 0, "NVIDIA Nsight Systems 2025.1\n", "")

    monkeypatch.setattr(v7_profiler_bundle.subprocess, "run", incompatible_version)

    with pytest.raises(ValueError, match="version.*not compatible"):
        finalize(_args(tmp_path))


def test_invented_ncu_metrics_are_rejected_against_canonical_selection(tmp_path: Path) -> None:
    bundle = _single_bundle(tmp_path)
    metadata_path = tmp_path / bundle.profiles[0]["ncu"]["metadata_path"]
    metadata = _read_json(metadata_path)
    selected = dict(SELECTED_METRICS)
    selected["duration"] = "invented__duration.sum"
    metadata["selected_metrics"] = selected
    metadata["supported_metrics"] = sorted(selected.values())
    metric_query = metadata["metric_query"]
    assert isinstance(metric_query, dict)
    metric_query["stdout"] = "\n".join(sorted(selected.values())) + "\n"
    selected_path = metadata_path.parent / "selected_metrics.json"
    supported_path = metadata_path.parent / "supported_metrics.txt"
    _write_json(selected_path, selected)
    supported_path.write_text("\n".join(sorted(selected.values())) + "\n", encoding="utf-8")
    metadata["files"] = _files(metadata_path.parent)
    _write_json(metadata_path, metadata)

    with pytest.raises(ValueError, match="canonical NCU metric selection"):
        finalize(_args(tmp_path))


def test_ncu_ok_requires_successful_gpu_query_with_consistent_identity(
    tmp_path: Path,
) -> None:
    bundle = _single_bundle(tmp_path)
    profile = bundle.profiles[0]
    config = profile["ncu"]
    assert isinstance(config, dict)
    metadata_path = tmp_path / str(config["metadata_path"])
    metadata = _read_json(metadata_path)
    gpu = metadata["gpu"]
    assert isinstance(gpu, dict)
    gpu.update({"status": "failed", "return_code": 1, "stdout": "", "stderr": "query failed"})
    _write_json(metadata_path, metadata)

    with pytest.raises(ValueError, match="ncu GPU provenance must be successful"):
        finalize(_args(tmp_path))


@pytest.mark.parametrize("field", ["index", "uuid", "driver_version"])
def test_ncu_gpu_query_output_must_match_parsed_metadata(
    tmp_path: Path, field: str
) -> None:
    bundle = _single_bundle(tmp_path)
    profile = bundle.profiles[0]
    config = profile["ncu"]
    assert isinstance(config, dict)
    metadata_path = tmp_path / str(config["metadata_path"])
    metadata = _read_json(metadata_path)
    gpu = metadata["gpu"]
    assert isinstance(gpu, dict)
    gpu[field] = 1 if field == "index" else "GPU-other" if field == "uuid" else "999.0"
    _write_json(metadata_path, metadata)

    with pytest.raises(ValueError, match="ncu GPU query output"):
        finalize(_args(tmp_path))


@pytest.mark.parametrize("attack", ["return_code", "missing_metric"])
def test_ncu_selected_metrics_require_successful_query_stdout_proof(
    tmp_path: Path, attack: str
) -> None:
    bundle = _single_bundle(tmp_path)
    profile = bundle.profiles[0]
    config = profile["ncu"]
    assert isinstance(config, dict)
    metadata_path = tmp_path / str(config["metadata_path"])
    metadata = _read_json(metadata_path)
    metric_query = metadata["metric_query"]
    assert isinstance(metric_query, dict)
    if attack == "return_code":
        metric_query["return_code"] = 1
        metric_query["stderr"] = "metric query failed"
    else:
        metric_query["stdout"] = "gpu__time_duration.sum\n"
    _write_json(metadata_path, metadata)

    with pytest.raises(ValueError, match="ncu metric query"):
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


@pytest.mark.parametrize(
    ("return_code", "message"),
    [
        (-11, "Segmentation fault"),
        (139, "Segmentation fault (core dumped)"),
        (13, "segfaulted while attaching to target"),
        (13, "target application crashed"),
        (13, "ERR_NVGPUCTRPERM: target application crashed"),
        (13, "failed to launch application"),
        (13, "application exited with an error"),
        (2, "Error: unknown option --metricz"),
    ],
)
def test_ncu_unavailable_rejects_crashes_and_command_errors(
    tmp_path: Path, return_code: int, message: str
) -> None:
    bundle = _single_bundle(tmp_path, ncu_status="unavailable")
    profile = bundle.profiles[0]
    config = profile["ncu"]
    assert isinstance(config, dict)
    metadata_path = tmp_path / str(config["metadata_path"])
    metadata = _read_json(metadata_path)
    metadata["return_code"] = return_code
    metadata["replay"] = {
        "mode": "application",
        "return_code": return_code,
        "succeeded": False,
    }
    (metadata_path.parent / "profile.stderr.log").write_text(message + "\n", encoding="utf-8")
    metadata["files"] = _files(metadata_path.parent)
    _write_json(metadata_path, metadata)

    with pytest.raises(ValueError, match="recognized counter permission or hardware support failure"):
        finalize(_args(tmp_path))


def test_ncu_return_code_13_without_known_diagnostic_is_not_unavailable(
    tmp_path: Path,
) -> None:
    bundle = _single_bundle(tmp_path, ncu_status="unavailable")
    profile = bundle.profiles[0]
    config = profile["ncu"]
    assert isinstance(config, dict)
    metadata_path = tmp_path / str(config["metadata_path"])
    (metadata_path.parent / "profile.stderr.log").write_text(
        "collector returned status 13\n", encoding="utf-8"
    )
    _refresh_metadata_files(metadata_path)

    with pytest.raises(ValueError, match="recognized counter permission or hardware support failure"):
        finalize(_args(tmp_path))


def test_structured_ncu_unavailable_cannot_hide_a_fatal_failure(tmp_path: Path) -> None:
    bundle = _single_bundle(tmp_path, ncu_status="unavailable")
    profile = bundle.profiles[0]
    config = profile["ncu"]
    assert isinstance(config, dict)
    metadata_path = tmp_path / str(config["metadata_path"])
    metadata = _read_json(metadata_path)
    metadata["return_code"] = 0
    metadata["replay"] = {"mode": "application", "return_code": 0, "succeeded": True}
    metadata["access"] = {
        "status": "unavailable",
        "code": "ERR_NVGPUCTRPERM",
        "message": "Segmentation fault (core dumped)",
    }
    _write_json(metadata_path, metadata)

    with pytest.raises(ValueError, match="recognized counter permission or hardware support failure"):
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
    _single_bundle(tmp_path, engine=engine, ranges=ranges)

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
