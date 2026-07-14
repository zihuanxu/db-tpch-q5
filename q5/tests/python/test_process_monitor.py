from __future__ import annotations

import sys
from pathlib import Path

from scripts.process_monitor import run_monitored


def test_monitor_records_peak_rss_and_streams(tmp_path: Path) -> None:
    stdout_path = tmp_path / "run.stdout.txt"
    stderr_path = tmp_path / "run.stderr.txt"
    command = [
        sys.executable,
        "-c",
        "import sys,time; x=bytearray(32*1024*1024); "
        "print('out'); print('err', file=sys.stderr); time.sleep(.2)",
    ]
    result = run_monitored(command, 5.0, stdout_path, stderr_path, poll_ms=5)
    assert result.return_code == 0
    assert result.peak_rss_bytes >= 32 * 1024 * 1024
    assert result.peak_rss_status == "measured"
    assert result.peak_gpu_bytes is None
    assert result.peak_gpu_status == "unavailable"
    assert result.peak_gpu_source == ""
    assert stdout_path.read_text(encoding="utf-8").strip() == "out"
    assert stderr_path.read_text(encoding="utf-8").strip() == "err"


def test_monitor_preserves_nonzero_return_code(tmp_path: Path) -> None:
    result = run_monitored(
        [sys.executable, "-c", "raise SystemExit(7)"],
        5.0,
        tmp_path / "out",
        tmp_path / "err",
    )
    assert result.return_code == 7
    assert result.timed_out is False


def test_monitor_passes_explicit_cuda_visibility_to_child(tmp_path: Path) -> None:
    stdout_path = tmp_path / "out"
    result = run_monitored(
        [
            sys.executable,
            "-c",
            "import os; print(os.environ.get('CUDA_VISIBLE_DEVICES', 'missing'))",
        ],
        5.0,
        stdout_path,
        tmp_path / "err",
        env={"CUDA_VISIBLE_DEVICES": "3"},
    )

    assert result.return_code == 0
    assert stdout_path.read_text(encoding="utf-8").strip() == "3"


def test_launch_failure_is_returned_and_logged(tmp_path: Path) -> None:
    stderr_path = tmp_path / "err"
    result = run_monitored(
        [str(tmp_path / "missing-executable")],
        5.0,
        tmp_path / "out",
        stderr_path,
    )
    assert result.return_code == 127
    assert "launch failed" in stderr_path.read_text(encoding="utf-8")


def test_monitor_times_out_and_keeps_logs(tmp_path: Path) -> None:
    stdout_path = tmp_path / "out"
    stderr_path = tmp_path / "err"
    result = run_monitored(
        [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(5)"],
        0.1,
        stdout_path,
        stderr_path,
        poll_ms=5,
    )
    assert result.timed_out is True
    assert stdout_path.exists()
    assert stderr_path.exists()
