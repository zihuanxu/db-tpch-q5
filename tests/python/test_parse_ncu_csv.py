from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.parse_ncu_csv import parse_ncu_csv
from scripts.profile_ncu import collect_ncu, select_metrics


FIXTURE = Path(__file__).parents[1] / "fixtures" / "ncu_metrics_sample.csv"


def test_select_metrics_chooses_one_metric_from_each_device_supported_candidate_list() -> None:
    supported = {
        "gpu__time_duration.sum",
        "dram__bytes_read.sum",
        "dram__throughput.avg.pct_of_peak_sustained_elapsed",
        "sm__throughput.avg.pct_of_peak_sustained_elapsed",
        "sm__warps_active.avg.pct_of_peak_sustained_active",
        "l2tex__t_bytes.sum",
    }

    assert select_metrics(supported) == {
        "duration": "gpu__time_duration.sum",
        "dram_read_bytes": "dram__bytes_read.sum",
        "dram_throughput": "dram__throughput.avg.pct_of_peak_sustained_elapsed",
        "sm_throughput": "sm__throughput.avg.pct_of_peak_sustained_elapsed",
        "achieved_occupancy": "sm__warps_active.avg.pct_of_peak_sustained_active",
    }


def test_select_metrics_rejects_an_empty_candidate_intersection() -> None:
    with pytest.raises(ValueError, match="no device-supported metric for achieved_occupancy"):
        select_metrics({
            "gpu__time_duration.sum",
            "dram__bytes_read.sum",
            "dram__throughput.avg.pct_of_peak_sustained_elapsed",
            "sm__throughput.avg.pct_of_peak_sustained_elapsed",
        })


def test_collector_requires_exactly_one_resident_request(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="--requests 1"):
        collect_ncu(["resident-q5", "--requests", "2"], tmp_path, "q5_kernel", {})


def test_parser_returns_normalized_metrics_for_exactly_one_q5_kernel() -> None:
    assert parse_ncu_csv(FIXTURE, "q5_kernel") == {
        "kernel_name": "void memq5::q5_kernel<float>(...)",
        "metrics": {
            "gpu__time_duration.sum": 1_250_000.0,
            "dram__bytes_read.sum": 1_048_576.0,
            "dram__throughput.avg.pct_of_peak_sustained_elapsed": 51.25,
            "sm__throughput.avg.pct_of_peak_sustained_elapsed": 62.5,
            "sm__warps_active.avg.pct_of_peak_sustained_active": 48.0,
        },
    }


def test_parser_rejects_multiple_q5_kernels(tmp_path: Path) -> None:
    report = tmp_path / "report.csv"
    report.write_text(FIXTURE.read_text(encoding="utf-8").replace('"1","42"', '"2","42"', 1), encoding="utf-8")
    report.write_text(FIXTURE.read_text(encoding="utf-8") + report.read_text(encoding="utf-8").split("\n", 1)[1], encoding="utf-8")

    with pytest.raises(ValueError, match="expected exactly one Q5 kernel"):
        parse_ncu_csv(report, "q5_kernel")


@pytest.mark.parametrize("bad_value", ["N/A", "", "NaN", "inf"])
def test_parser_rejects_nonnumeric_metric_values(tmp_path: Path, bad_value: str) -> None:
    report = tmp_path / "report.csv"
    report.write_text(FIXTURE.read_text(encoding="utf-8").replace('"1250000"', f'"{bad_value}"'), encoding="utf-8")

    with pytest.raises(ValueError, match="non-numeric metric value"):
        parse_ncu_csv(report, "q5_kernel")


def test_collector_persists_discovery_capture_and_replay_failure_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    supported = "\n".join([
        "gpu__time_duration.sum",
        "dram__bytes_read.sum",
        "dram__throughput.avg.pct_of_peak_sustained_elapsed",
        "sm__throughput.avg.pct_of_peak_sustained_elapsed",
        "sm__warps_active.avg.pct_of_peak_sustained_active",
    ])

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1:] == ["--query-metrics"]:
            return subprocess.CompletedProcess(command, 0, supported, "")
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, "NVIDIA Nsight Compute 2026.1", "")
        if command[0] == "nvidia-smi":
            return subprocess.CompletedProcess(command, 0, "555.1, GPU-abc", "")
        if command[1] == "--csv":
            return subprocess.CompletedProcess(command, 1, "", "Replay failed")
        raise AssertionError(command)

    monkeypatch.setattr("scripts.profile_ncu.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="replay failed"):
        collect_ncu(["resident-q5", "--requests", "1"], tmp_path, "q5_kernel", {"scale_factor": "SF1"})

    manifest = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    assert manifest["supported_metrics"] == sorted(supported.splitlines())
    assert manifest["selected_metrics"]["duration"] == "gpu__time_duration.sum"
    assert manifest["profile_command"][:7] == ["ncu", "--csv", "--target-processes", "all", "--replay-mode", "application", "--kernel-name-base"]
    assert manifest["profile_command"][-3:] == ["resident-q5", "--requests", "1"]
    assert manifest["replay"] == {"mode": "application", "return_code": 1, "succeeded": False}
    assert manifest["tool_versions"]["ncu"] == "NVIDIA Nsight Compute 2026.1"
    assert manifest["gpu"] == {"driver_version": "555.1", "uuid": "GPU-abc"}
    assert (tmp_path / "supported_metrics.txt").read_text(encoding="utf-8") == "\n".join(sorted(supported.splitlines())) + "\n"
    assert all(isinstance(command, list) for command in calls)
