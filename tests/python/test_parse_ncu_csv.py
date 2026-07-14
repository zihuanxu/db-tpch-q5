from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts import profile_ncu
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


def test_collector_rejects_duplicate_requests_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"subprocess must not run: {command}")

    monkeypatch.setattr("scripts.profile_ncu.subprocess.run", unexpected_run)

    with pytest.raises(ValueError, match="exactly one --requests option with value 1"):
        collect_ncu(
            ["resident-q5", "--requests", "1", "--requests", "1"],
            tmp_path,
            "q5_kernel",
            {},
        )


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


def test_parser_ignores_ncu_and_profiled_application_preamble(tmp_path: Path) -> None:
    report = tmp_path / "report.csv"
    report.write_text(
        "==PROF== Connected to process 42 (resident-q5)\n"
        '{"record_type":"session_setup","status":"ok"}\n'
        + FIXTURE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    parsed = parse_ncu_csv(report, "q5_kernel")

    assert parsed["kernel_name"] == "void memq5::q5_kernel<float>(...)"
    assert parsed["metrics"]["gpu__time_duration.sum"] == 1_250_000.0


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


def test_parser_rejects_localized_decimal_comma_values(tmp_path: Path) -> None:
    report = tmp_path / "localized.csv"
    report.write_text(
        '"ID";"Process ID";"Kernel Name";"Context";"Stream";"Metric Name";"Metric Value"\n'
        '"1";"42";"q5_kernel";"1";"7";"gpu__time_duration.sum";"51,25"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="localized/non-C metric value"):
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
        if command[1:] == ["--query-metrics", "--query-metrics-mode", "all", "--devices", "0"]:
            return subprocess.CompletedProcess(command, 0, supported, "")
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, "NVIDIA Nsight Compute 2026.1", "")
        if command[0] == "nvidia-smi":
            return subprocess.CompletedProcess(command, 0, "0, GPU-abc, 555.1", "")
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
    kernel_filter_index = manifest["profile_command"].index("--kernel-name")
    assert manifest["profile_command"][kernel_filter_index + 1] == "regex:.*q5_kernel.*"
    launch_count_index = manifest["profile_command"].index("--launch-count")
    assert manifest["profile_command"][launch_count_index + 1] == "1"
    assert "--launch-skip" not in manifest["profile_command"]
    assert manifest["profile_command"][-3:] == ["resident-q5", "--requests", "1"]
    assert manifest["replay"] == {"mode": "application", "return_code": 1, "succeeded": False}
    assert manifest["tool_versions"]["ncu"] == "NVIDIA Nsight Compute 2026.1"
    assert manifest["tool_version_provenance"]["ncu"] == {
        "command": ["ncu", "--version"],
        "return_code": 0,
        "stdout": "NVIDIA Nsight Compute 2026.1",
        "stderr": "",
    }
    assert manifest["metric_query"] == {
        "command": manifest["query_command"],
        "return_code": 0,
        "stdout": supported,
        "stderr": "",
    }
    assert manifest["gpu"] == {
        "status": "ok",
        "requested_index": 0,
        "index": 0,
        "uuid": "GPU-abc",
        "driver_version": "555.1",
        "query_command": [
            "nvidia-smi", "--id=0", "--query-gpu=index,uuid,driver_version",
            "--format=csv,noheader,nounits",
        ],
        "return_code": 0,
        "stdout": "0, GPU-abc, 555.1",
        "stderr": "",
    }
    assert (tmp_path / "supported_metrics.txt").read_text(encoding="utf-8") == "\n".join(sorted(supported.splitlines())) + "\n"
    assert all(isinstance(command, list) for command in calls)


def test_hybrid_auto_filters_to_worker_thread_measured_kernel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supported = "\n".join([
        "gpu__time_duration.sum",
        "dram__bytes_read.sum",
        "dram__throughput.avg.pct_of_peak_sustained_elapsed",
        "sm__throughput.avg.pct_of_peak_sustained_elapsed",
        "sm__warps_active.avg.pct_of_peak_sustained_active",
    ])

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[1:2] == ["--query-metrics"]:
            return subprocess.CompletedProcess(command, 0, supported, "")
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, "NVIDIA Nsight Compute 2026.1", "")
        if command[0] == "nvidia-smi":
            return subprocess.CompletedProcess(command, 0, "0, GPU-abc, 555.1", "")
        if command[1] == "--csv":
            return subprocess.CompletedProcess(command, 1, "", "Replay failed")
        raise AssertionError(command)

    monkeypatch.setattr("scripts.profile_ncu.subprocess.run", fake_run)
    profiled_command = [
        "resident-q5", "--engine", "hybrid-arrow", "--hybrid-selection",
        "auto", "--warmup", "0", "--requests", "1",
    ]

    with pytest.raises(RuntimeError, match="replay failed"):
        collect_ncu(profiled_command, tmp_path, "q5_kernel", {})

    manifest = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    profile_command = manifest["profile_command"]
    replay_index = profile_command.index("--replay-mode")
    assert profile_command[replay_index + 1] == "kernel"
    assert manifest["replay"]["mode"] == "kernel"
    assert "--launch-skip" not in profile_command
    count_index = profile_command.index("--launch-count")
    assert profile_command[count_index + 1] == "1"
    nvtx_index = profile_command.index("--nvtx")
    include_index = profile_command.index("--nvtx-include")
    assert profile_command[include_index + 1] == "hybrid_gpu_request/"
    assert nvtx_index < include_index < count_index < profile_command.index("resident-q5")


def test_collector_persists_parse_error_and_report_log_hashes_after_successful_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supported = "\n".join([
        "gpu__time_duration.sum",
        "dram__bytes_read.sum",
        "dram__throughput.avg.pct_of_peak_sustained_elapsed",
        "sm__throughput.avg.pct_of_peak_sustained_elapsed",
        "sm__warps_active.avg.pct_of_peak_sustained_active",
    ])

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[1:] == ["--query-metrics", "--query-metrics-mode", "all", "--devices", "0"]:
            return subprocess.CompletedProcess(command, 0, supported, "")
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, "NVIDIA Nsight Compute 2026.1", "")
        if command[0] == "nvidia-smi":
            return subprocess.CompletedProcess(command, 0, "0, GPU-abc, 555.1", "")
        if command[1] == "--csv":
            return subprocess.CompletedProcess(command, 0, "not,a,valid,ncu,report\n", "profile warning")
        raise AssertionError(command)

    monkeypatch.setattr("scripts.profile_ncu.subprocess.run", fake_run)

    with pytest.raises(ValueError, match="no Q5 kernel"):
        collect_ncu(["resident-q5", "--requests", "1"], tmp_path, "q5_kernel", {})

    manifest = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    assert manifest["parse_error"] == {
        "type": "ValueError",
        "message": f"no Q5 kernel matching 'q5_kernel' in {tmp_path / 'report.csv'}",
    }
    assert manifest["report"]["path"] == "report.csv"
    assert manifest["report"]["sha256"]
    for filename in ["report.csv", "profile.stdout.log", "profile.stderr.log"]:
        assert manifest["files"][filename]["sha256"]


def test_collector_uses_configured_device_for_discovery_profile_and_provenance(
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
        if command[1:2] == ["--query-metrics"]:
            return subprocess.CompletedProcess(command, 0, supported, "")
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, "NVIDIA Nsight Compute 2026.1", "")
        if command[0] == "nvidia-smi":
            return subprocess.CompletedProcess(command, 0, "2, GPU-two, 555.2", "")
        if command[1] == "--csv":
            return subprocess.CompletedProcess(command, 1, "", "Replay failed")
        raise AssertionError(command)

    monkeypatch.setattr("scripts.profile_ncu.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="replay failed"):
        collect_ncu(
            ["resident-q5", "--requests", "1"],
            tmp_path,
            "q5_kernel",
            {},
            device_index=2,
        )

    manifest = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    assert manifest["device_index"] == 2
    assert manifest["query_command"] == [
        "ncu", "--query-metrics", "--query-metrics-mode", "all", "--devices", "2",
    ]
    assert manifest["profile_command"][
        manifest["profile_command"].index("--devices") + 1
    ] == "2"
    assert [
        "nvidia-smi", "--id=2", "--query-gpu=index,uuid,driver_version",
        "--format=csv,noheader,nounits",
    ] in calls
    assert manifest["gpu"] == {
        "status": "ok",
        "requested_index": 2,
        "index": 2,
        "uuid": "GPU-two",
        "driver_version": "555.2",
        "query_command": [
            "nvidia-smi", "--id=2", "--query-gpu=index,uuid,driver_version",
            "--format=csv,noheader,nounits",
        ],
        "return_code": 0,
        "stdout": "2, GPU-two, 555.2",
        "stderr": "",
    }


def test_collector_records_the_actual_profiled_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "memq5_arrow_session"
    executable.write_bytes(b"\x7fELF\x02\x01collector-test\n")
    executable.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    supported = "\n".join([
        "gpu__time_duration.sum",
        "dram__bytes_read.sum",
        "dram__throughput.avg.pct_of_peak_sustained_elapsed",
        "sm__throughput.avg.pct_of_peak_sustained_elapsed",
        "sm__warps_active.avg.pct_of_peak_sustained_active",
    ])

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[1:2] == ["--query-metrics"]:
            return subprocess.CompletedProcess(command, 0, supported, "")
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, "NVIDIA Nsight Compute 2026.1", "")
        if command[0] == "nvidia-smi":
            return subprocess.CompletedProcess(command, 0, "0, GPU-zero, 555.1", "")
        if command[1] == "--csv":
            return subprocess.CompletedProcess(command, 1, "", "Replay failed")
        raise AssertionError(command)

    monkeypatch.setattr("scripts.profile_ncu.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="replay failed"):
        collect_ncu(
            ["./memq5_arrow_session", "--requests", "1"],
            tmp_path / "capture",
            "q5_kernel",
            {},
        )

    manifest = json.loads((tmp_path / "capture/metadata.json").read_text(encoding="utf-8"))
    assert manifest["collector_execution"] == {
        "status": "ok",
        "cwd": str(tmp_path.resolve()),
        "command_path": "./memq5_arrow_session",
        "resolved_path": str(executable.resolve()),
        "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
    }


def test_collector_persists_failed_metric_query_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[1:2] == ["--query-metrics"]:
            return subprocess.CompletedProcess(
                command, 13, "", "ERR_NVGPUCTRPERM: permission denied"
            )
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(
                command, 0, "NVIDIA Nsight Compute 2026.1", ""
            )
        if command[0] == "nvidia-smi":
            return subprocess.CompletedProcess(command, 0, "0, GPU-zero, 555.1", "")
        raise AssertionError(command)

    monkeypatch.setattr("scripts.profile_ncu.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="metric discovery failed"):
        collect_ncu(
            ["resident-q5", "--requests", "1"],
            tmp_path,
            "q5_kernel",
            {},
        )

    manifest = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    assert manifest["metric_query"] == {
        "command": [
            "ncu", "--query-metrics", "--query-metrics-mode", "all", "--devices", "0",
        ],
        "return_code": 13,
        "stdout": "",
        "stderr": "ERR_NVGPUCTRPERM: permission denied",
    }
    assert manifest["query_failure"] is True
    assert manifest["tool_version_provenance"]["ncu"]["return_code"] == 0
    assert manifest["gpu"]["return_code"] == 0


def test_cli_passes_configured_device_index_to_collector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_collect(
        command: list[str],
        output_dir: Path,
        q5_kernel: str,
        metadata: dict,
        device_index: int = 0,
    ) -> dict:
        captured.update({
            "command": command,
            "output_dir": output_dir,
            "device_index": device_index,
        })
        return {}

    monkeypatch.setattr(profile_ncu, "collect_ncu", fake_collect)
    monkeypatch.setattr(
        "sys.argv",
        [
            "profile_ncu.py", "--output-dir", str(tmp_path), "--device-index", "3",
            "--", "resident-q5", "--requests", "1",
        ],
    )

    assert profile_ncu.main() == 0
    assert captured == {
        "command": ["resident-q5", "--requests", "1"],
        "output_dir": tmp_path,
        "device_index": 3,
    }


@pytest.mark.parametrize(
    ("smi_case", "expected_status"),
    [("failed", "failed"), ("unavailable", "unavailable"), ("multiple_rows", "invalid_output")],
)
def test_collector_records_structured_gpu_query_failures_without_naming_the_first_gpu(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    smi_case: str,
    expected_status: str,
) -> None:
    supported = "\n".join([
        "gpu__time_duration.sum",
        "dram__bytes_read.sum",
        "dram__throughput.avg.pct_of_peak_sustained_elapsed",
        "sm__throughput.avg.pct_of_peak_sustained_elapsed",
        "sm__warps_active.avg.pct_of_peak_sustained_active",
    ])

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[1:2] == ["--query-metrics"]:
            return subprocess.CompletedProcess(command, 0, supported, "")
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, "NVIDIA Nsight Compute 2026.1", "")
        if command[0] == "nvidia-smi":
            if smi_case == "unavailable":
                raise OSError("nvidia-smi missing")
            if smi_case == "failed":
                return subprocess.CompletedProcess(command, 9, "", "device query failed")
            return subprocess.CompletedProcess(
                command,
                0,
                "1, GPU-one, 555.1\n2, GPU-two, 555.2\n",
                "",
            )
        if command[1] == "--csv":
            return subprocess.CompletedProcess(command, 1, "", "Replay failed")
        raise AssertionError(command)

    monkeypatch.setattr("scripts.profile_ncu.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="replay failed"):
        collect_ncu(
            ["resident-q5", "--requests", "1"],
            tmp_path,
            "q5_kernel",
            {},
            device_index=1,
        )

    gpu = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))["gpu"]
    assert gpu["status"] == expected_status
    assert gpu["requested_index"] == 1
    assert gpu["query_command"][1] == "--id=1"
    assert "uuid" not in gpu
    assert "driver_version" not in gpu


def test_collector_forces_c_locale_for_all_ncu_and_nvidia_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environments: list[dict | None] = []
    supported = "\n".join([
        "gpu__time_duration.sum",
        "dram__bytes_read.sum",
        "dram__throughput.avg.pct_of_peak_sustained_elapsed",
        "sm__throughput.avg.pct_of_peak_sustained_elapsed",
        "sm__warps_active.avg.pct_of_peak_sustained_active",
    ])
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    monkeypatch.setenv("LANG", "de_DE.UTF-8")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        environment = kwargs.get("env")
        assert environment is None or isinstance(environment, dict)
        environments.append(environment)
        if command[1:2] == ["--query-metrics"]:
            return subprocess.CompletedProcess(command, 0, supported, "")
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, "NVIDIA Nsight Compute 2026.1", "")
        if command[0] == "nvidia-smi":
            return subprocess.CompletedProcess(command, 0, "0, GPU-zero, 555.1", "")
        if command[1] == "--csv":
            return subprocess.CompletedProcess(command, 1, "", "Replay failed")
        raise AssertionError(command)

    monkeypatch.setattr("scripts.profile_ncu.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="replay failed"):
        collect_ncu(["resident-q5", "--requests", "1"], tmp_path, "q5_kernel", {})

    assert environments
    assert all(environment is not None for environment in environments)
    assert all(environment["LC_ALL"] == "C" for environment in environments if environment)
    assert all(environment["LANG"] == "C" for environment in environments if environment)
