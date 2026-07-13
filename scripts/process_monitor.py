#!/usr/bin/env python3

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import psutil


@dataclass(frozen=True)
class MonitoredProcessResult:
    return_code: int
    timed_out: bool
    elapsed_ms: float
    peak_rss_bytes: int
    peak_gpu_bytes: int
    started_at_utc: str
    finished_at_utc: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _rss_tree_bytes(pid: int) -> int:
    try:
        process = psutil.Process(pid)
        processes = [process, *process.children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0
    total = 0
    for child in processes:
        try:
            total += child.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2.0)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait()


def run_monitored(
    command: Sequence[str],
    timeout_s: float,
    stdout_path: Path,
    stderr_path: Path,
    *,
    cwd: Path | None = None,
    poll_ms: int = 20,
) -> MonitoredProcessResult:
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    if poll_ms <= 0:
        raise ValueError("poll_ms must be positive")
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)

    started_at = _utc_now()
    started = time.perf_counter()
    peak_rss = 0
    timed_out = False
    with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
        try:
            process = subprocess.Popen(
                list(command),
                cwd=cwd,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
        except OSError as exc:
            stderr.write(f"launch failed: {exc}\n".encode("utf-8", errors="replace"))
            return MonitoredProcessResult(
                return_code=127,
                timed_out=False,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
                peak_rss_bytes=0,
                peak_gpu_bytes=0,
                started_at_utc=started_at,
                finished_at_utc=_utc_now(),
            )
        while process.poll() is None:
            peak_rss = max(peak_rss, _rss_tree_bytes(process.pid))
            if time.perf_counter() - started >= timeout_s:
                timed_out = True
                _terminate_process_group(process)
                break
            time.sleep(poll_ms / 1000.0)
        peak_rss = max(peak_rss, _rss_tree_bytes(process.pid))
        return_code = process.wait()

    return MonitoredProcessResult(
        return_code=return_code,
        timed_out=timed_out,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
        peak_rss_bytes=peak_rss,
        peak_gpu_bytes=0,
        started_at_utc=started_at,
        finished_at_utc=_utc_now(),
    )
