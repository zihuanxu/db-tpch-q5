from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.parse_nsys_stats import parse_nsys_csv, validate_ranges
from scripts.profile_nsys import collect_nsys


FIXTURE = Path(__file__).parents[1] / "fixtures" / "nsys_stats_sample.csv"


def test_parser_handles_localized_numbers_and_quoted_kernel_names() -> None:
    rows = parse_nsys_csv(FIXTURE)

    assert rows[0]["name"] == 'void q5_kernel<float>(char const*, "quoted")'
    assert rows[0]["total_ns"] == 1_250_000
    assert rows[0]["average_ns"] == 625_000
    assert rows[0]["time_percent"] == 62.5


def test_parser_calculates_cuda_kernel_and_memcpy_totals() -> None:
    rows = parse_nsys_csv(FIXTURE)

    assert sum(row["total_ns"] for row in rows if row["kind"] == "kernel") == 1_250_000
    assert sum(row["total_ns"] for row in rows if row["kind"] == "memcpy") == 750_000


def test_parser_reads_realistic_nvtx_range_column() -> None:
    rows = parse_nsys_csv(FIXTURE)

    assert validate_ranges(rows, {"request", "q5_kernel"}) == []
    assert [row["name"] for row in rows if row["kind"] == "range"] == ["request", "q5_kernel"]


def test_validate_ranges_reports_an_absent_required_range() -> None:
    errors = validate_ranges([{"name": "request", "kind": "range", "total_ns": 1}], {"request", "q5_kernel"})

    assert errors == ["missing required range: q5_kernel"]


def test_validate_ranges_does_not_count_a_cuda_kernel_as_an_nvtx_range() -> None:
    errors = validate_ranges([{"name": "q5_kernel", "kind": "kernel", "total_ns": 1}], {"q5_kernel"})

    assert errors == ["missing required range: q5_kernel"]


def test_parser_rejects_an_empty_gpu_report(tmp_path: Path) -> None:
    report = tmp_path / "cuda_gpu_kern_sum.csv"
    report.write_text('"Total Time (ns)","Operation"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="no data rows"):
        parse_nsys_csv(report)


def test_collector_records_argv_logs_hashes_versions_and_failed_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1:2] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, "nsys 2026.1\n", "")
        if command[1] == "profile":
            prefix = Path(command[command.index("--output") + 1])
            prefix.with_suffix(".nsys-rep").write_bytes(b"report")
            return subprocess.CompletedProcess(command, 9, "profile output", "profile error")
        raise AssertionError(command)

    monkeypatch.setattr("scripts.profile_nsys.subprocess.run", fake_run)

    result = collect_nsys(["resident-q5", "--requests", "1"], tmp_path, {"scale_factor": "SF1"})

    assert result["return_code"] == 9
    assert result["profile_command"][:6] == [
        "nsys", "profile", "--force-overwrite=true", "--trace=cuda,nvtx,osrt", "--sample=none", "--output"
    ]
    assert result["profile_command"][-3:] == ["resident-q5", "--requests", "1"]
    assert result["tool_versions"]["nsys"] == "nsys 2026.1"
    assert (tmp_path / "profile.stdout.log").read_text(encoding="utf-8") == "profile output"
    assert (tmp_path / "profile.stderr.log").read_text(encoding="utf-8") == "profile error"
    assert result["files"]["profile.nsys-rep"]["sha256"]
    assert json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))["metadata"]["scale_factor"] == "SF1"
    assert all(isinstance(command, list) for command in calls)


def test_collector_uses_one_stats_command_for_all_required_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[1:2] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, "nsys 2026.1\n", "")
        if command[1] == "profile":
            Path(command[command.index("--output") + 1]).with_suffix(".nsys-rep").write_bytes(b"report")
        if command[1] == "stats":
            (tmp_path / "cuda_gpu_kern_sum.csv").write_text("export", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "output", "")

    monkeypatch.setattr("scripts.profile_nsys.subprocess.run", fake_run)

    result = collect_nsys(["resident-q5"], tmp_path, {})

    assert result["stats_commands"] == [[
        "nsys", "stats", "--force-export=true", "--report",
        "cuda_api_sum,cuda_gpu_kern_sum,cuda_gpu_mem_time_sum,nvtx_sum",
        "--format", "csv", "--output", str(tmp_path / "stats"), str(tmp_path / "profile.nsys-rep"),
    ]]
    assert result["stats"] == [{"return_code": 0}]
    assert result["files"]["cuda_gpu_kern_sum.csv"]["sha256"]


def test_collector_forces_c_locale_for_every_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("NSYS_TEST_PARENT", "preserved")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(kwargs)
        if command[1] == "profile":
            Path(command[command.index("--output") + 1]).with_suffix(".nsys-rep").write_bytes(b"report")
        return subprocess.CompletedProcess(command, 0, "nsys 2026.1\n", "")

    monkeypatch.setattr("scripts.profile_nsys.subprocess.run", fake_run)

    collect_nsys(["resident-q5"], tmp_path, {})

    assert len(calls) == 3
    assert all(call.get("env", {}).get("LC_ALL") == "C" for call in calls)
    assert all(call.get("env", {}).get("LANG") == "C" for call in calls)
    assert all(call.get("env", {}).get("NSYS_TEST_PARENT") == "preserved" for call in calls)


def test_collector_records_version_argv_and_return_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[1:2] == ["--version"]:
            return subprocess.CompletedProcess(command, 3, "", "version failed")
        return subprocess.CompletedProcess(command, 9, "", "profile failed")

    monkeypatch.setattr("scripts.profile_nsys.subprocess.run", fake_run)

    collect_nsys(["resident-q5"], tmp_path, {})

    provenance = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
    assert provenance["tool_version_provenance"]["nsys"]["command"] == ["nsys", "--version"]
    assert provenance["tool_version_provenance"]["nsys"]["return_code"] == 3
