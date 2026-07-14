from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from scripts.v7_profiler_bundle import audit, finalize, sha256_file


COMMAND = ["build/q5_session", "--engine", "copy", "--requests", "1"]
SELECTED_METRICS = {
    "duration": "gpu__time_duration.sum",
    "dram_read_bytes": "dram__bytes_read.sum",
    "dram_throughput": "dram__throughput.avg.pct_of_peak_sustained_elapsed",
    "sm_throughput": "sm__throughput.avg.pct_of_peak_sustained_elapsed",
    "achieved_occupancy": "sm__warps_active.avg.pct_of_peak_sustained_active",
}
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


def _files(directory: Path) -> dict[str, dict[str, object]]:
    return {
        path.name: {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for path in sorted(directory.iterdir())
        if path.is_file() and path.name != "metadata.json"
    }


def _write_nsys(root: Path) -> Path:
    directory = root / "captures" / "sf1-copy" / "nsys"
    directory.mkdir(parents=True)
    (directory / "profile.nsys-rep").write_bytes(b"nsys raw report")
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
    (directory / "stats_nvtx_sum.csv").write_text(
        '"Total Time (ns)","Range"\n"2000","request"\n"1000","q5_kernel"\n',
        encoding="utf-8",
    )
    (directory / "profile.stdout.log").write_text("", encoding="utf-8")
    (directory / "profile.stderr.log").write_text("", encoding="utf-8")
    metadata = {
        "profile_command": [
            "nsys",
            "profile",
            "--trace=cuda,nvtx,osrt",
            "--output",
            str(directory / "profile"),
            *COMMAND,
        ],
        "stats_commands": [["nsys", "stats", "--format", "csv", "profile.nsys-rep"]],
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
    return directory


def _write_ncu(root: Path) -> Path:
    directory = root / "captures" / "sf1-copy" / "ncu"
    directory.mkdir(parents=True)
    (directory / "report.csv").write_text(NCU_REPORT, encoding="utf-8")
    _write_json(directory / "selected_metrics.json", SELECTED_METRICS)
    (directory / "supported_metrics.txt").write_text(
        "\n".join(sorted(SELECTED_METRICS.values())) + "\n", encoding="utf-8"
    )
    (directory / "profile.stdout.log").write_text(NCU_REPORT, encoding="utf-8")
    (directory / "profile.stderr.log").write_text("", encoding="utf-8")
    parsed = {
        "kernel_name": "void q5_kernel()",
        "metrics": {
            "gpu__time_duration.sum": 1250000.0,
            "dram__bytes_read.sum": 1048576.0,
            "dram__throughput.avg.pct_of_peak_sustained_elapsed": 51.25,
            "sm__throughput.avg.pct_of_peak_sustained_elapsed": 62.5,
            "sm__warps_active.avg.pct_of_peak_sustained_active": 48.0,
        },
    }
    metadata = {
        "query_command": [
            "ncu",
            "--query-metrics",
            "--query-metrics-mode",
            "all",
            "--devices",
            "0",
        ],
        "selected_metrics": SELECTED_METRICS,
        "profile_command": ["ncu", "--csv", "--devices", "0", *COMMAND],
        "tool_versions": {"ncu": "NVIDIA Nsight Compute 2026.1"},
        "gpu": {
            "status": "ok",
            "uuid": "GPU-test-uuid",
            "query_command": ["nvidia-smi", "--id=0", "--query-gpu=uuid"],
        },
        "return_code": 0,
        "replay": {"mode": "application", "return_code": 0, "succeeded": True},
        "report": {
            "path": "report.csv",
            "sha256": sha256_file(directory / "report.csv"),
            "parsed": parsed,
        },
        "files": _files(directory),
    }
    _write_json(directory / "metadata.json", metadata)
    return directory


def _profile(ncu: dict[str, object]) -> dict[str, object]:
    return {
        "id": "sf1-copy",
        "scale_factor": "SF1",
        "engine": "copy",
        "session_commit": "a" * 40,
        "dataset_manifest": {"path": "data/tpch-sf1/manifest.json", "sha256": "b" * 64},
        "oracle_hash": "542abf4003633c7c",
        "gpu_uuid": "GPU-test-uuid",
        "command": COMMAND,
        "required_nvtx_ranges": ["request", "q5_kernel"],
        "nsys": {"metadata_path": "captures/sf1-copy/nsys/metadata.json"},
        "ncu": ncu,
    }


def _write_index(root: Path, profile: dict[str, object]) -> None:
    _write_json(root / "profiles.json", {"schema_version": 1, "profiles": [profile]})


def _complete_bundle(root: Path) -> None:
    _write_nsys(root)
    _write_ncu(root)
    _write_index(
        root,
        _profile({"status": "ok", "metadata_path": "captures/sf1-copy/ncu/metadata.json"}),
    )


def _args(root: Path) -> argparse.Namespace:
    return argparse.Namespace(directory=root, index=None)


def test_finalize_and_audit_freeze_the_complete_profiler_contract(tmp_path: Path) -> None:
    _complete_bundle(tmp_path)

    assert finalize(_args(tmp_path)) == 0
    assert audit(_args(tmp_path)) == 0

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    profile = manifest["profiles"][0]
    assert manifest["status"] == "complete"
    assert profile["scale_factor"] == "SF1"
    assert profile["engine"] == "copy"
    assert profile["session_commit"] == "a" * 40
    assert profile["dataset_manifest"] == {
        "path": "data/tpch-sf1/manifest.json",
        "sha256": "b" * 64,
    }
    assert profile["oracle_hash"] == "542abf4003633c7c"
    assert profile["gpu_uuid"] == "GPU-test-uuid"
    assert profile["command"] == COMMAND
    assert profile["nsys"]["raw_report"]["sha256"]
    assert set(profile["nsys"]["exports"]) == {
        "cuda_api_sum",
        "cuda_gpu_kern_sum",
        "cuda_gpu_mem_time_sum",
        "nvtx_sum",
    }
    assert profile["nsys"]["required_nvtx_ranges"] == ["q5_kernel", "request"]
    assert profile["nsys"]["observed_nvtx_ranges"] == ["q5_kernel", "request"]
    assert profile["nsys"]["tool_version"] == "NVIDIA Nsight Systems 2026.1"
    assert profile["nsys"]["tool_version_provenance"]["command"] == ["nsys", "--version"]
    assert profile["ncu"]["selected_metrics"] == SELECTED_METRICS
    assert profile["ncu"]["report"]["sha256"]
    assert profile["ncu"]["tool_version"] == "NVIDIA Nsight Compute 2026.1"
    assert profile["ncu"]["tool_version_provenance"]["version_command"] == [
        "ncu",
        "--version",
    ]
    assert profile["ncu"]["tool_version_provenance"]["metric_query_command"][0] == "ncu"
    assert (tmp_path / "manifest.sha256").read_text(encoding="ascii") == (
        f"{sha256_file(tmp_path / 'manifest.json')}  manifest.json\n"
    )


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (("scale_factor",), "scale_factor"),
        (("engine",), "engine"),
        (("session_commit",), "session_commit"),
        (("dataset_manifest", "path"), "dataset_manifest.path"),
        (("dataset_manifest", "sha256"), "dataset_manifest.sha256"),
        (("oracle_hash",), "oracle_hash"),
        (("gpu_uuid",), "gpu_uuid"),
        (("command",), "command"),
        (("required_nvtx_ranges",), "required_nvtx_ranges"),
    ],
)
def test_finalize_rejects_missing_required_profile_metadata(
    tmp_path: Path, path: tuple[str, ...], expected: str
) -> None:
    _complete_bundle(tmp_path)
    index = json.loads((tmp_path / "profiles.json").read_text(encoding="utf-8"))
    target = index["profiles"][0]
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    _write_json(tmp_path / "profiles.json", index)

    with pytest.raises(ValueError, match=expected.replace(".", r"\.")):
        finalize(_args(tmp_path))


@pytest.mark.parametrize(
    ("metadata_path", "field", "expected"),
    [
        ("captures/sf1-copy/nsys/metadata.json", "tool_versions", "nsys tool version"),
        (
            "captures/sf1-copy/nsys/metadata.json",
            "tool_version_provenance",
            "nsys tool version provenance",
        ),
        ("captures/sf1-copy/ncu/metadata.json", "selected_metrics", "selected NCU metrics"),
        ("captures/sf1-copy/ncu/metadata.json", "tool_versions", "ncu tool version"),
        ("captures/sf1-copy/ncu/metadata.json", "query_command", "ncu metric query command"),
    ],
)
def test_finalize_requires_profiler_tool_metric_and_provenance_fields(
    tmp_path: Path, metadata_path: str, field: str, expected: str
) -> None:
    _complete_bundle(tmp_path)
    path = tmp_path / metadata_path
    metadata = json.loads(path.read_text(encoding="utf-8"))
    del metadata[field]
    _write_json(path, metadata)

    with pytest.raises(ValueError, match=expected):
        finalize(_args(tmp_path))


def test_finalize_rejects_missing_required_nsys_export(tmp_path: Path) -> None:
    _complete_bundle(tmp_path)
    (tmp_path / "captures/sf1-copy/nsys/stats_cuda_api_sum.csv").unlink()

    with pytest.raises(ValueError, match="missing Nsight Systems export: cuda_api_sum"):
        finalize(_args(tmp_path))


def test_finalize_rejects_missing_required_nvtx_range(tmp_path: Path) -> None:
    _complete_bundle(tmp_path)
    nsys = tmp_path / "captures/sf1-copy/nsys"
    export = nsys / "stats_nvtx_sum.csv"
    export.write_text(
        export.read_text(encoding="utf-8").replace('"1000","q5_kernel"\n', ""),
        encoding="utf-8",
    )
    metadata = json.loads((nsys / "metadata.json").read_text(encoding="utf-8"))
    metadata["files"][export.name] = {
        "bytes": export.stat().st_size,
        "sha256": sha256_file(export),
    }
    _write_json(nsys / "metadata.json", metadata)

    with pytest.raises(ValueError, match="missing required range: q5_kernel"):
        finalize(_args(tmp_path))


def test_finalize_rejects_command_or_gpu_identity_mismatch(tmp_path: Path) -> None:
    _complete_bundle(tmp_path)
    index = json.loads((tmp_path / "profiles.json").read_text(encoding="utf-8"))
    index["profiles"][0]["command"] = ["different", "--requests", "1"]
    _write_json(tmp_path / "profiles.json", index)
    with pytest.raises(ValueError, match="exact command"):
        finalize(_args(tmp_path))

    index["profiles"][0]["command"] = COMMAND
    index["profiles"][0]["gpu_uuid"] = "GPU-other"
    _write_json(tmp_path / "profiles.json", index)
    with pytest.raises(ValueError, match="GPU UUID"):
        finalize(_args(tmp_path))


def test_structured_unavailable_ncu_is_allowed_with_valid_nsys(tmp_path: Path) -> None:
    _write_nsys(tmp_path)
    unavailable = {
        "status": "unavailable",
        "reason": {
            "code": "ERR_NVGPUCTRPERM",
            "message": "GPU performance counters are not accessible",
        },
        "selected_metrics": {},
        "tool_version": "NVIDIA Nsight Compute 2026.1",
        "tool_version_provenance": {
            "command": ["ncu", "--version"],
            "return_code": 0,
            "stdout": "NVIDIA Nsight Compute 2026.1\n",
            "stderr": "",
        },
    }
    _write_index(tmp_path, _profile(unavailable))

    assert finalize(_args(tmp_path)) == 0
    assert audit(_args(tmp_path)) == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["ncu_unavailable_profiles"] == 1
    assert manifest["profiles"][0]["ncu"] == unavailable


@pytest.mark.parametrize("missing", ["reason", "selected_metrics", "tool_version", "tool_version_provenance"])
def test_unavailable_ncu_must_be_structured(tmp_path: Path, missing: str) -> None:
    _write_nsys(tmp_path)
    unavailable = {
        "status": "unavailable",
        "reason": {"code": "permission_denied", "message": "counters unavailable"},
        "selected_metrics": {},
        "tool_version": "NVIDIA Nsight Compute 2026.1",
        "tool_version_provenance": {"command": ["ncu", "--version"], "return_code": 0},
    }
    del unavailable[missing]
    _write_index(tmp_path, _profile(unavailable))

    with pytest.raises(ValueError, match=missing.replace("_", " ")):
        finalize(_args(tmp_path))


@pytest.mark.parametrize(
    "relative_path",
    [
        "captures/sf1-copy/nsys/stats_nvtx_sum.csv",
        "captures/sf1-copy/ncu/report.csv",
    ],
)
def test_audit_rejects_tampered_or_missing_exports(tmp_path: Path, relative_path: str) -> None:
    _complete_bundle(tmp_path)
    assert finalize(_args(tmp_path)) == 0
    artifact = tmp_path / relative_path
    if artifact.name == "report.csv":
        artifact.unlink()
    else:
        artifact.write_text("tampered\n", encoding="utf-8")

    assert audit(_args(tmp_path)) == 1
