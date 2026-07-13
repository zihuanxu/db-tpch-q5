#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import io
import subprocess
import sys
import time
from pathlib import Path


CPP_ENGINES = {"cpu", "gpu-copy", "gpu-managed", "gpu-mapped"}
ARROW_DATASET_ENGINES = {"arrow", "cudf"}
PYTHON_BASELINES = {
    "python": "baselines/python_q5.py",
    "arrow": "baselines/arrow_q5.py",
    "duckdb": "baselines/duckdb_q5.py",
    "cudf": "baselines/cudf_q5.py",
}

OUTPUT_FIELDS = [
    "run_id",
    "status",
    "engine",
    "region",
    "date",
    "threads",
    "result_rows",
    "result_hash",
    "build_ms",
    "h2d_ms",
    "kernel_ms",
    "d2h_ms",
    "scan_ms",
    "total_ms",
    "elapsed_ms",
    "error",
]


def build_command(args: argparse.Namespace, engine: str, threads: int) -> list[str]:
    if engine in CPP_ENGINES:
        return [
            str(args.memq5),
            "--engine",
            engine,
            "--data-dir",
            str(args.data_dir),
            "--region",
            args.region,
            "--date",
            args.date,
            "--threads",
            str(threads),
            "--format",
            "benchmark",
        ]

    if engine in PYTHON_BASELINES:
        command = [
            sys.executable,
            PYTHON_BASELINES[engine],
            "--region",
            args.region,
            "--date",
            args.date,
            "--format",
            "benchmark",
        ]
        if engine in ARROW_DATASET_ENGINES:
            if args.arrow_dataset is None:
                raise ValueError("--arrow-dataset is required for arrow and cudf engines")
            command.extend(["--dataset", str(args.arrow_dataset)])
        else:
            command.extend(["--data-dir", str(args.data_dir)])
        return command

    raise ValueError(f"unknown engine: {engine}")


def _error_row(
    args: argparse.Namespace,
    engine: str,
    run_id: int,
    threads: int,
    error: str,
) -> dict[str, str]:
    return {
        "run_id": str(run_id),
        "status": "error",
        "engine": engine,
        "region": args.region,
        "date": args.date,
        "threads": str(threads if engine in CPP_ENGINES else 1),
        "elapsed_ms": "0.000000",
        "error": error,
    }


def parse_benchmark(stdout: str) -> dict[str, str]:
    reader = csv.DictReader(io.StringIO(stdout.strip()))
    rows = list(reader)
    if len(rows) != 1:
        raise ValueError(f"expected one benchmark row, got {len(rows)}")
    return rows[0]


def run_one(args: argparse.Namespace, engine: str, run_id: int, threads: int) -> dict[str, str]:
    try:
        command = build_command(args, engine, threads)
    except ValueError as exc:
        return _error_row(args, engine, run_id, threads, str(exc))
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=args.project_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout).strip().replace("\n", " ")
        return {
            "run_id": str(run_id),
            "status": "error",
            "engine": engine,
            "region": args.region,
            "date": args.date,
            "threads": str(threads if engine in CPP_ENGINES else 1),
            "elapsed_ms": f"{elapsed_ms:.6f}",
            "error": error,
        }

    try:
        row = parse_benchmark(completed.stdout)
    except ValueError as exc:
        return {
            "run_id": str(run_id),
            "status": "error",
            "engine": engine,
            "region": args.region,
            "date": args.date,
            "threads": str(threads if engine in CPP_ENGINES else 1),
            "elapsed_ms": f"{elapsed_ms:.6f}",
            "error": str(exc),
        }

    row["run_id"] = str(run_id)
    row["status"] = "ok"
    row.setdefault("threads", str(threads if engine in CPP_ENGINES else 1))
    row["elapsed_ms"] = f"{elapsed_ms:.6f}"
    row["error"] = ""
    return row


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            normalized = {field: row.get(field, "") for field in OUTPUT_FIELDS}
            writer.writerow(normalized)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run MEMQ5 benchmark matrix")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--memq5", type=Path, default=Path("build/memq5"))
    parser.add_argument("--data-dir", type=Path, default=Path("tests/fixtures/tpch_q5_tiny"))
    parser.add_argument("--arrow-dataset", type=Path)
    parser.add_argument("--region", default="ASIA")
    parser.add_argument("--date", default="1994-01-01")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument(
        "--thread-list",
        default="",
        help="Comma-separated CPU/C++ thread counts, for example 1,2,4,8",
    )
    parser.add_argument(
        "--engines",
        default="cpu,python",
        help="Comma-separated list: cpu,python,arrow,gpu-copy,gpu-managed,gpu-mapped,duckdb,cudf",
    )
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=0)
    parser.add_argument("--output", type=Path, default=Path("results/benchmarks.csv"))
    parser.add_argument("--fail-on-error", action="store_true")
    return parser.parse_args()


def iter_engine_runs(engines: list[str], thread_values: list[int]):
    for engine in engines:
        engine_threads = thread_values if engine in CPP_ENGINES else [1]
        for threads in engine_threads:
            yield engine, threads


def main() -> int:
    args = parse_args()
    args.project_root = args.project_root.resolve()
    engines = [engine.strip() for engine in args.engines.split(",") if engine.strip()]
    if args.thread_list:
        thread_values = [int(value) for value in args.thread_list.split(",") if value.strip()]
    else:
        thread_values = [args.threads]

    warmup_errors: list[dict[str, str]] = []
    for _ in range(args.warmup):
        for engine, threads in iter_engine_runs(engines, thread_values):
            row = run_one(args, engine, -1, threads)
            if row.get("status") != "ok":
                warmup_errors.append(row)

    rows: list[dict[str, str]] = []
    run_id = 0
    for _ in range(args.repeat):
        for engine, threads in iter_engine_runs(engines, thread_values):
            rows.append(run_one(args, engine, run_id, threads))
            run_id += 1

    write_rows(args.output, rows)

    errors = [row for row in rows if row.get("status") != "ok"]
    if warmup_errors:
        print(f"{len(warmup_errors)} warmup runs failed or were unavailable")
        for row in warmup_errors:
            print(f"- {row.get('engine')}: {row.get('error')}")
    print(f"wrote {len(rows)} rows to {args.output}")
    if errors:
        print(f"{len(errors)} runs failed or were unavailable")
        for row in errors:
            print(f"- {row.get('engine')}: {row.get('error')}")
        return 1 if args.fail_on_error else 0
    if warmup_errors:
        return 1 if args.fail_on_error else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
