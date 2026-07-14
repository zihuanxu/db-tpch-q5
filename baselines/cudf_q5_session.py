#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Sequence

from common import result_hash
from cudf_q5 import (
    CudfBenchmarkResult,
    _execute_q5,
    _load_cudf_tables,
    _prepare_cudf_q5,
    q5_date_bounds,
)


class CudfQ5Session:
    """Keeps the six Q5 tables on the GPU for repeated fixed-parameter queries."""

    def __init__(self, dataset: Path, region: str, start_date: str) -> None:
        self._start_date_bound, self._end_date_bound = q5_date_bounds(start_date)
        try:
            import cudf
        except ImportError as exc:
            raise SystemExit("RAPIDS cudf Python package is not installed") from exc

        self.dataset = Path(dataset)
        self.region = region
        self.start_date = start_date
        self.dataset_load_ms = 0.0
        setup_started = time.perf_counter()

        def record_dataset_load() -> None:
            self.dataset_load_ms = (time.perf_counter() - setup_started) * 1000.0

        self._tables = _load_cudf_tables(
            self.dataset, cudf, on_arrow_tables_prepared=record_dataset_load
        )
        self._prepared = _prepare_cudf_q5(
            self._tables,
            cudf,
            self._start_date_bound,
            self._end_date_bound,
        )
        _synchronize_cuda_device()
        total_setup_ms = (time.perf_counter() - setup_started) * 1000.0
        self.session_setup_ms = total_setup_ms - self.dataset_load_ms
        self.resident_host_bytes = 0
        self.resident_gpu_bytes = sum(
            _frame_memory_usage_bytes(frame) for frame in self._tables.values()
        )
        self.resident_pinned_bytes = 0

    def execute(self) -> CudfBenchmarkResult:
        query_started = time.perf_counter()
        rows, matched_lineitem_rows = _execute_q5(self._prepared, self.region)
        _synchronize_cuda_device()
        query_ms = (time.perf_counter() - query_started) * 1000.0
        return CudfBenchmarkResult(
            rows=rows,
            load_ms=0.0,
            query_ms=query_ms,
            input_lineitem_rows=len(self._prepared.tables["lineitem"]),
            matched_lineitem_rows=matched_lineitem_rows,
            resident_gpu_bytes=self.resident_gpu_bytes,
        )


def _frame_memory_usage_bytes(frame) -> int:
    usage = frame.memory_usage(deep=True)
    return int(usage.sum() if hasattr(usage, "sum") else usage)


def _cuda_is_available() -> bool:
    try:
        from numba import cuda

        return bool(cuda.is_available())
    except Exception:
        return False


def _synchronize_cuda_device() -> None:
    from numba import cuda

    cuda.synchronize()


def _nonnegative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be nonnegative")
    return parsed


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _query_date(value: str) -> str:
    try:
        q5_date_bounds(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return value


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resident RAPIDS cuDF TPC-H Q5 runner")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--region", default="ASIA")
    parser.add_argument("--date", type=_query_date, default="1994-01-01")
    parser.add_argument("--warmup", type=_nonnegative_int, default=3)
    parser.add_argument("--repeat", type=_positive_int, default=10)
    return parser.parse_args(argv)


def _setup_record(session: CudfQ5Session, session_id: str) -> dict[str, object]:
    return {
        "record_type": "session_setup",
        "session_id": session_id,
        "lifecycle": "resident",
        "status": "ok",
        "error_class": "",
        "engine": "cudf",
        "dataset": str(session.dataset),
        "region": session.region,
        "date": session.start_date,
        "dataset_load_ms": session.dataset_load_ms,
        "session_setup_ms": session.session_setup_ms,
        "tune_ms": 0.0,
        "resident_host_bytes": session.resident_host_bytes,
        "resident_gpu_bytes": session.resident_gpu_bytes,
        "resident_pinned_bytes": session.resident_pinned_bytes,
        "selected_cpu_ratio": 0.0,
        "predicted_cpu_ratio": 0.0,
    }


def _request_record(
    session_id: str,
    request_index: int,
    is_warmup: bool,
    result: CudfBenchmarkResult | None = None,
    error: Exception | None = None,
) -> dict[str, object]:
    if result is None:
        return {
            "record_type": "request",
            "session_id": session_id,
            "lifecycle": "resident",
            "status": "error",
            "error_class": type(error).__name__ if error is not None else "RuntimeError",
            "request_index": request_index,
            "is_warmup": is_warmup,
            "selected_cpu_ratio": 0.0,
            "result_rows": 0,
            "result_hash": "",
            "query_total_ms": 0.0,
            "input_lineitem_rows": 0,
            "matched_lineitem_rows": 0,
            "cpu_input_rows": 0,
            "gpu_input_rows": 0,
            "h2d_bytes": 0,
            "d2h_bytes": 0,
            "mapped_remote_read_bytes": 0,
        }
    return {
        "record_type": "request",
        "session_id": session_id,
        "lifecycle": "resident",
        "status": "ok",
        "error_class": "",
        "request_index": request_index,
        "is_warmup": is_warmup,
        "selected_cpu_ratio": 0.0,
        "result_rows": len(result.rows),
        "result_hash": result_hash(result.rows),
        "query_total_ms": result.query_ms,
        "input_lineitem_rows": result.input_lineitem_rows,
        "matched_lineitem_rows": result.matched_lineitem_rows,
        "cpu_input_rows": 0,
        "gpu_input_rows": result.input_lineitem_rows,
        "h2d_bytes": 0,
        "d2h_bytes": 0,
        "mapped_remote_read_bytes": 0,
    }


def _emit(record: dict[str, object]) -> None:
    print(json.dumps(record, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if not _cuda_is_available():
        print("CUDA device is unavailable; cuDF resident session was not started.", file=sys.stderr)
        return 77

    session = CudfQ5Session(args.dataset, args.region, args.date)
    session_id = str(uuid.uuid4())
    _emit(_setup_record(session, session_id))
    expected_hash: str | None = None
    for request_index in range(args.warmup + args.repeat):
        is_warmup = request_index < args.warmup
        try:
            result = session.execute()
            record = _request_record(session_id, request_index, is_warmup, result=result)
            current_hash = str(record["result_hash"])
            if expected_hash is None:
                expected_hash = current_hash
            elif current_hash != expected_hash:
                raise RuntimeError(
                    f"resident result hash changed from {expected_hash} to {current_hash}"
                )
        except Exception as exc:
            _emit(_request_record(session_id, request_index, is_warmup, error=exc))
            print(f"cuDF resident request failed: {exc}", file=sys.stderr)
            return 1
        _emit(record)
    return 0


if __name__ == "__main__":
    sys.exit(main())
