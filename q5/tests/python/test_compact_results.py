from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.compact_results import (
    PROFILE_FIELDS,
    RAW_FIELDS,
    compact_csv,
    compact_resident,
    main,
    profile_rows,
)


def sample_payload() -> dict[str, object]:
    return {
        "profiles": [
            {
                "identity": {
                    "scale_factor": "1",
                    "engine": "copy",
                    "cpu_ratio": 0.0,
                    "gpu_ratio": 1.0,
                },
                "ncu": {
                    "selected_metric_values": {
                        "duration": 1308640.0,
                        "dram_read_bytes": 54495232.0,
                        "dram_throughput": 4.38,
                        "sm_throughput": 3.13,
                        "achieved_occupancy": 38.62,
                    }
                },
                "nsys": {"q5_kernel_total_time_ns": 1208067},
            }
        ]
    }


def test_profile_rows_keep_only_course_metrics() -> None:
    rows = profile_rows(sample_payload())
    assert rows == [
        {
            "scale_factor": "1",
            "engine": "copy",
            "cpu_ratio": 0.0,
            "gpu_ratio": 1.0,
            "nsys_kernel_time_ns": 1208067,
            "ncu_kernel_duration_ns": 1308640.0,
            "dram_read_bytes": 54495232.0,
            "dram_throughput_pct": 4.38,
            "sm_throughput_pct": 3.13,
            "achieved_occupancy_pct": 38.62,
        }
    ]
    assert list(rows[0]) == PROFILE_FIELDS


def test_compact_csv_drops_log_and_process_fields(tmp_path: Path) -> None:
    source = tmp_path / "raw.csv"
    output = tmp_path / "compact.csv"
    source.write_text(
        "engine,scale_factor,status,result_hash,query_total_ms,stdout_log,run_uuid\n"
        "gpu-copy,1,ok,abc,1.25,logs/run.log,private-id\n",
        encoding="utf-8",
    )
    fields = [
        "engine",
        "scale_factor",
        "status",
        "result_hash",
        "query_total_ms",
    ]
    compact_csv(source, output, fields)
    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows == [
        {
            "engine": "gpu-copy",
            "scale_factor": "1",
            "status": "ok",
            "result_hash": "abc",
            "query_total_ms": "1.25",
        }
    ]
    assert "stdout_log" not in rows[0]
    assert "run_uuid" not in rows[0]


def test_compact_resident_writes_lf_terminated_csvs(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    source_dir.mkdir()
    source_dir.joinpath("raw.csv").write_bytes(
        (",".join(RAW_FIELDS) + "\r\n" + ",".join("value" for _ in RAW_FIELDS) + "\r\n").encode()
    )
    setup_fields = [
        "scale_factor", "config_id", "engine", "threads", "ratio_mode",
        "cpu_ratio", "gpu_ratio", "dataset_load_ms", "session_setup_ms",
        "plan_build_ms", "host_staging_ms", "allocation_ms", "initial_h2d_ms",
        "tune_ms", "resident_host_bytes", "resident_gpu_bytes",
        "resident_pinned_bytes", "selected_cpu_ratio", "predicted_cpu_ratio",
        "realized_cpu_ratio",
    ]
    source_dir.joinpath("setup.csv").write_bytes(
        (",".join(setup_fields) + "\r\n" + ",".join("value" for _ in setup_fields) + "\r\n").encode()
    )
    source_dir.joinpath("summary.csv").write_bytes(
        b"config_id,result_hash\r\nexample,abc\r\n"
    )

    compact_resident(source_dir, output_dir)

    for name in ("raw.csv", "setup.csv", "summary.csv"):
        assert b"\r" not in output_dir.joinpath(name).read_bytes()


def test_main_writes_stable_csv(tmp_path: Path) -> None:
    source = tmp_path / "summary.json"
    output = tmp_path / "profiler.csv"
    source.write_text(json.dumps(sample_payload()), encoding="utf-8")
    assert main(["profiler", "--input", str(source), "--output", str(output)]) == 0
    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["engine"] == "copy"
    assert rows[0]["nsys_kernel_time_ns"] == "1208067"
    assert b"\r" not in output.read_bytes()


def test_main_reads_raw_profiles_json_and_capture_files(tmp_path: Path) -> None:
    nsys_dir = tmp_path / "captures" / "sf1-copy" / "nsys"
    ncu_dir = tmp_path / "captures" / "sf1-copy" / "ncu"
    nsys_dir.mkdir(parents=True)
    ncu_dir.mkdir(parents=True)
    (nsys_dir / "metadata.json").write_text(
        json.dumps({"metadata": {"scale_factor": "1", "engine": "copy"}}),
        encoding="utf-8",
    )
    (nsys_dir / "stats_cuda_gpu_kern_sum.csv").write_text(
        "Range Name,Total Time (ns)\nq5_kernel,1208067\nother_kernel,99\n",
        encoding="utf-8",
    )
    selected_metrics = {
        "duration": "gpu__time_duration.sum",
        "dram_read_bytes": "dram__bytes_read.sum",
        "dram_throughput": "dram__throughput.avg.pct_of_peak_sustained_elapsed",
        "sm_throughput": "sm__throughput.avg.pct_of_peak_sustained_elapsed",
        "achieved_occupancy": "sm__warps_active.avg.pct_of_peak_sustained_active",
    }
    (ncu_dir / "metadata.json").write_text(
        json.dumps({"selected_metrics": selected_metrics}),
        encoding="utf-8",
    )
    (ncu_dir / "selected_metrics.json").write_text(
        json.dumps(selected_metrics), encoding="utf-8"
    )
    ncu_rows = [
        ("gpu__time_duration.sum", "1308640.0"),
        ("dram__bytes_read.sum", "54495232.0"),
        ("dram__throughput.avg.pct_of_peak_sustained_elapsed", "4.38"),
        ("sm__throughput.avg.pct_of_peak_sustained_elapsed", "3.13"),
        ("sm__warps_active.avg.pct_of_peak_sustained_active", "38.62"),
    ]
    report = ["Kernel Name,Metric Name,Metric Value,ID,Process ID,Context,Stream"]
    report.extend(f"q5_kernel,{metric},{value},1,2,3,4" for metric, value in ncu_rows)
    (ncu_dir / "report.csv").write_text("\n".join(report) + "\n", encoding="utf-8")
    source = tmp_path / "profiles.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "profiles": [
                    {
                        "id": "sf1-copy",
                        "scale_factor": "1",
                        "engine": "copy",
                        "cpu_ratio": 0.0,
                        "gpu_ratio": 1.0,
                        "nsys": {
                            "metadata_path": "captures/sf1-copy/nsys/metadata.json"
                        },
                        "ncu": {
                            "status": "ok",
                            "metadata_path": "captures/sf1-copy/ncu/metadata.json",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "profiler.csv"

    assert main(["profiler", "--input", str(source), "--output", str(output)]) == 0
    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows == [
        {
            "scale_factor": "1",
            "engine": "copy",
            "cpu_ratio": "0.0",
            "gpu_ratio": "1.0",
            "nsys_kernel_time_ns": "1208067",
            "ncu_kernel_duration_ns": "1308640.0",
            "dram_read_bytes": "54495232.0",
            "dram_throughput_pct": "4.38",
            "sm_throughput_pct": "3.13",
            "achieved_occupancy_pct": "38.62",
        }
    ]


def test_direct_script_help_works() -> None:
    script = Path(__file__).parents[3] / "scripts" / "compact_results.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "usage:" in result.stdout


def test_raw_profiles_require_positive_q5_kernel_total(tmp_path: Path) -> None:
    nsys_dir = tmp_path / "captures" / "sf1-copy" / "nsys"
    nsys_dir.mkdir(parents=True)
    (nsys_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (nsys_dir / "stats_cuda_gpu_kern_sum.csv").write_text(
        "Range Name,Total Time (ns)\nother_kernel,1208067\n",
        encoding="utf-8",
    )
    source = tmp_path / "profiles.json"
    source.write_text(
        json.dumps(
            {
                "profiles": [
                    {
                        "scale_factor": "1",
                        "engine": "copy",
                        "cpu_ratio": 0.0,
                        "gpu_ratio": 1.0,
                        "nsys": {
                            "metadata_path": "captures/sf1-copy/nsys/metadata.json"
                        },
                        "ncu": {"metadata_path": "captures/sf1-copy/ncu/metadata.json"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="positive q5 kernel total"):
        main(
            [
                "profiler",
                "--input",
                str(source),
                "--output",
                str(tmp_path / "profiler.csv"),
            ]
        )
