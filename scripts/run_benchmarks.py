#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import shlex
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

try:
    from scripts.benchmark_schema import RAW_FIELDS, BenchmarkRecord, validate_record
    from scripts.formal_matrix import expected_configuration_keys, load_matrix
    from scripts.process_monitor import run_monitored
except ModuleNotFoundError:
    from benchmark_schema import RAW_FIELDS, BenchmarkRecord, validate_record
    from formal_matrix import expected_configuration_keys, load_matrix
    from process_monitor import run_monitored


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
    "cpu_ms",
    "gpu_ms",
    "overlap_ms",
    "input_lineitem_rows",
    "matched_lineitem_rows",
    "cpu_input_rows",
    "gpu_input_rows",
    "h2d_bytes",
    "d2h_bytes",
    "mapped_remote_read_bytes",
    "elapsed_ms",
    "error",
]


def _legacy_build_command(args: argparse.Namespace, engine: str, threads: int) -> list[str]:
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
        command = _legacy_build_command(args, engine, threads)
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


def _legacy_parse_args() -> argparse.Namespace:
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


def _legacy_main() -> int:
    args = _legacy_parse_args()
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


@dataclass(frozen=True)
class Configuration:
    engine: str
    threads: int
    cpu_ratio: float


V5_CPU_ENGINES = {"cpu-specialized", "arrow-acero"}
V5_GPU_ENGINES = {"gpu-copy", "gpu-managed", "gpu-mapped"}
V5_ENGINES = V5_CPU_ENGINES | V5_GPU_ENGINES | {"hybrid-arrow", "cudf"}
V5_REQUIRED_BENCHMARK_FIELDS = {
    "engine", "region", "date", "threads", "result_rows", "result_hash",
    "build_ms", "h2d_ms", "kernel_ms", "d2h_ms", "scan_ms", "total_ms",
    "cpu_ms", "gpu_ms", "overlap_ms", "input_lineitem_rows",
    "matched_lineitem_rows", "cpu_input_rows", "gpu_input_rows", "h2d_bytes",
    "d2h_bytes", "mapped_remote_read_bytes",
}


def build_command(args: argparse.Namespace, config: Configuration) -> list[str]:
    if config.engine in V5_CPU_ENGINES | V5_GPU_ENGINES | {"hybrid-arrow"}:
        command = [
            str(args.arrow_cli),
            "--dataset",
            str(args.arrow_dataset),
            "--engine",
            config.engine,
            "--region",
            args.region,
            "--date",
            args.date,
            "--threads",
            str(config.threads),
            "--format",
            "benchmark",
        ]
        if config.engine == "hybrid-arrow":
            command.extend(["--cpu-ratio", str(config.cpu_ratio)])
        return command
    if config.engine == "cudf":
        return [
            "conda",
            "run",
            "-n",
            args.cudf_env,
            "python",
            "baselines/cudf_q5.py",
            "--dataset",
            str(args.arrow_dataset),
            "--region",
            args.region,
            "--date",
            args.date,
            "--format",
            "benchmark",
        ]
    raise ValueError(f"unsupported V5 engine: {config.engine}")


def expand_configurations(
    engines: list[str],
    thread_values: list[int],
    hybrid_ratios: list[float],
    *,
    hybrid_threads: int,
) -> list[Configuration]:
    if not engines:
        raise ValueError("at least one engine is required")
    if not thread_values or any(threads <= 0 for threads in thread_values):
        raise ValueError("thread values must be positive")
    if hybrid_threads <= 0:
        raise ValueError("hybrid_threads must be positive")
    if not hybrid_ratios or any(
        not math.isfinite(ratio) or ratio < 0.0 or ratio > 1.0
        for ratio in hybrid_ratios
    ):
        raise ValueError("hybrid ratios must be between 0 and 1")
    configurations: list[Configuration] = []
    for engine in engines:
        if engine not in V5_ENGINES:
            raise ValueError(f"unsupported V5 engine: {engine}")
        if engine in V5_CPU_ENGINES:
            configurations.extend(
                Configuration(engine, threads, 1.0) for threads in thread_values
            )
        elif engine in V5_GPU_ENGINES or engine == "cudf":
            configurations.append(Configuration(engine, 1, 0.0))
        else:
            configurations.extend(
                Configuration(engine, hybrid_threads, ratio)
                for ratio in hybrid_ratios
            )
    return configurations


def _ratio_pair(config: Configuration) -> tuple[float, float]:
    if config.engine in V5_CPU_ENGINES:
        return 1.0, 0.0
    if config.engine == "hybrid-arrow":
        return config.cpu_ratio, 1.0 - config.cpu_ratio
    return 0.0, 1.0


def _record_base(
    args: argparse.Namespace,
    config: Configuration,
    scenario: str,
    run_uuid: str,
    sample_index: int,
    is_warmup: bool,
    stdout_path: Path,
    stderr_path: Path,
) -> dict[str, object]:
    cpu_ratio, gpu_ratio = _ratio_pair(config)
    not_applicable = ["gpu_memory_monitor"]
    if config.engine != "cudf":
        not_applicable.append("load_timing")
    return {
        "schema_version": 1,
        "experiment_id": args.experiment_id,
        "run_uuid": run_uuid,
        "status": "error",
        "error_class": "ERROR_NOT_RUN",
        "return_code": 1,
        "engine": config.engine,
        "scenario": scenario,
        "scale_factor": args.scale_factor,
        "region": args.region,
        "date": args.date,
        "sample_index": sample_index,
        "is_warmup": is_warmup,
        "threads": config.threads,
        "cpu_ratio": cpu_ratio,
        "gpu_ratio": gpu_ratio,
        "gpu_chunk_rows": 0,
        "memory_scope": "process",
        "mode_options_json": json.dumps(
            {"cpu_ratio": cpu_ratio}, sort_keys=True, separators=(",", ":")
        ),
        "not_applicable_phases": json.dumps(
            not_applicable, separators=(",", ":")
        ),
        "result_rows": 0,
        "result_hash": "",
        "oracle_status": "not_run",
        "load_ms": 0.0,
        "plan_build_ms": 0.0,
        "host_prepare_ms": 0.0,
        "h2d_ms": 0.0,
        "cpu_scan_ms": 0.0,
        "gpu_kernel_ms": 0.0,
        "d2h_ms": 0.0,
        "overlap_wall_ms": 0.0,
        "query_total_ms": 0.0,
        "process_elapsed_ms": 0.0,
        "input_lineitem_rows": 0,
        "matched_lineitem_rows": 0,
        "cpu_input_rows": 0,
        "gpu_input_rows": 0,
        "h2d_bytes": 0,
        "d2h_bytes": 0,
        "mapped_remote_read_bytes": 0,
        "cpu_peak_rss_bytes": 0,
        "gpu_peak_memory_bytes": 0,
        "throughput_rows_per_second": 0.0,
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
        "started_at_utc": "1970-01-01T00:00:00Z",
        "finished_at_utc": "1970-01-01T00:00:00Z",
    }


def _classify_failure(return_code: int, timed_out: bool, output: str) -> tuple[str, str]:
    lowered = output.lower()
    if timed_out:
        return "timeout", "ERROR_TIMEOUT"
    if return_code == 77:
        return "skipped_no_gpu", "SKIPPED_NO_GPU"
    if "launch failed" in lowered:
        return "error", "ERROR_PROCESS_LAUNCH"
    if "out of memory" in lowered or "memory allocation" in lowered:
        return "oom", "ERROR_CUDA_OOM"
    return "error", "ERROR_PROCESS_EXIT"


def _float(row: dict[str, str], name: str) -> float:
    value = row.get(name, "")
    return float(value) if value else 0.0


def _int(row: dict[str, str], name: str) -> int:
    value = row.get(name, "")
    return int(value) if value else 0


def parse_v5_benchmark(
    stdout: str, config: Configuration, args: argparse.Namespace
) -> dict[str, str]:
    reader = csv.DictReader(io.StringIO(stdout.strip()))
    if reader.fieldnames is None or len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise ValueError("benchmark header is missing or has duplicate fields")
    missing = sorted(V5_REQUIRED_BENCHMARK_FIELDS - set(reader.fieldnames))
    if missing:
        raise ValueError(f"missing benchmark fields: {missing}")
    rows = list(reader)
    if len(rows) != 1:
        raise ValueError(f"expected one benchmark row, got {len(rows)}")
    row = rows[0]
    expected = {
        "engine": config.engine,
        "region": args.region,
        "date": args.date,
        "threads": str(config.threads),
    }
    for field, value in expected.items():
        if row.get(field) != value:
            raise ValueError(
                f"benchmark {field} mismatch expected={value} actual={row.get(field, '')}"
            )
    return row


def run_v5_one(
    args: argparse.Namespace,
    config: Configuration,
    scenario: str,
    sample_index: int,
    is_warmup: bool,
) -> BenchmarkRecord:
    run_id = str(uuid.uuid4())
    stdout_path = args.logs_dir / f"{run_id}.stdout.txt"
    stderr_path = args.logs_dir / f"{run_id}.stderr.txt"
    record = _record_base(
        args,
        config,
        scenario,
        run_id,
        sample_index,
        is_warmup,
        stdout_path,
        stderr_path,
    )
    if scenario == "resident":
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text(
            "resident lifecycle is not implemented; no benchmark was substituted\n",
            encoding="utf-8",
        )
        record["error_class"] = "ERROR_RESIDENT_UNSUPPORTED"
        return validate_record(record)

    command = build_command(args, config)
    with args.commands_output.open("a", encoding="utf-8") as handle:
        handle.write(shlex.join(command) + "\n")
    monitored = run_monitored(
        command,
        args.timeout_seconds,
        stdout_path,
        stderr_path,
        cwd=args.project_root,
    )
    record.update(
        return_code=monitored.return_code,
        process_elapsed_ms=monitored.elapsed_ms,
        cpu_peak_rss_bytes=monitored.peak_rss_bytes,
        gpu_peak_memory_bytes=monitored.peak_gpu_bytes,
        started_at_utc=monitored.started_at_utc,
        finished_at_utc=monitored.finished_at_utc,
    )
    stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
    stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
    if monitored.return_code != 0 or monitored.timed_out:
        status, error_class = _classify_failure(
            monitored.return_code, monitored.timed_out, stderr + stdout
        )
        record.update(status=status, error_class=error_class)
        return validate_record(record)

    try:
        row = parse_v5_benchmark(stdout, config, args)
        total_ms = _float(row, "total_ms")
        input_rows = _int(row, "input_lineitem_rows")
        if config.engine == "cudf" and input_rows == 0:
            input_rows = args.dataset_lineitem_rows
        cpu_scan_ms = (
            _float(row, "cpu_ms")
            if config.engine == "hybrid-arrow"
            else _float(row, "scan_ms") if config.engine in V5_CPU_ENGINES else 0.0
        )
        record.update(
            status="ok",
            error_class="",
            result_rows=_int(row, "result_rows"),
            result_hash=row.get("result_hash", ""),
            oracle_status="not_run",
            load_ms=_float(row, "load_ms"),
            plan_build_ms=_float(row, "build_ms"),
            h2d_ms=_float(row, "h2d_ms"),
            cpu_scan_ms=cpu_scan_ms,
            gpu_kernel_ms=_float(row, "kernel_ms"),
            d2h_ms=_float(row, "d2h_ms"),
            overlap_wall_ms=_float(row, "overlap_ms"),
            query_total_ms=total_ms,
            input_lineitem_rows=input_rows,
            matched_lineitem_rows=_int(row, "matched_lineitem_rows"),
            cpu_input_rows=_int(row, "cpu_input_rows"),
            gpu_input_rows=_int(row, "gpu_input_rows"),
            h2d_bytes=_int(row, "h2d_bytes"),
            d2h_bytes=_int(row, "d2h_bytes"),
            mapped_remote_read_bytes=_int(row, "mapped_remote_read_bytes"),
            throughput_rows_per_second=(
                input_rows * 1000.0 / total_ms if total_ms > 0 else 0.0
            ),
        )
        if config.engine == "cudf":
            record["cpu_input_rows"] = 0
            record["gpu_input_rows"] = input_rows
        if args.expected_hash:
            if record["result_hash"] == args.expected_hash:
                record["oracle_status"] = "passed"
            else:
                record.update(
                    status="error",
                    error_class="ERROR_ORACLE_HASH_MISMATCH",
                    oracle_status="failed",
                )
    except (ValueError, csv.Error) as exc:
        record.update(status="error", error_class="ERROR_MALFORMED_OUTPUT")
        with stderr_path.open("a", encoding="utf-8") as handle:
            handle.write(f"malformed benchmark output: {exc}\n")
    return validate_record(record)


def write_v5_records(path: Path, records: list[BenchmarkRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RAW_FIELDS)
        writer.writeheader()
        for record in records:
            writer.writerow(record.as_dict())


def _comma_strings(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _v5_parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run strict MEMQ5 V5 benchmarks")
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--arrow-cli", type=Path, default=Path("build-arrow-cuda-v3/memq5_arrow_query"))
    parser.add_argument("--arrow-dataset", type=Path, required=True)
    parser.add_argument("--cudf-env", default="memq5-cudf")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warmup-output", type=Path)
    parser.add_argument("--logs-dir", type=Path)
    parser.add_argument("--commands-output", type=Path)
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args()
    args.project_root = args.project_root.resolve()
    if not args.matrix.is_absolute():
        args.matrix = args.project_root / args.matrix
    try:
        matrix = load_matrix(args.matrix)
    except ValueError as exc:
        parser.error(str(exc))
    args.experiment_id = str(matrix["experiment_id"])
    args.scale_factor = str(matrix["dataset"]["scale_factor"])
    args.region = str(matrix["query"]["region"])
    args.date = str(matrix["query"]["date"])
    args.expected_hash = str(matrix["dataset"]["expected_hash"])
    args.warmup = int(matrix["protocol"]["warmup"])
    args.repeat = int(matrix["protocol"]["repeat"])
    args.timeout_seconds = float(matrix["protocol"]["timeout_seconds"])
    args.matrix_keys = expected_configuration_keys(matrix)
    if not args.arrow_cli.is_absolute():
        args.arrow_cli = args.project_root / args.arrow_cli
    if not args.arrow_dataset.is_absolute():
        args.arrow_dataset = args.project_root / args.arrow_dataset
    expected_dataset = (args.project_root / str(matrix["dataset"]["path"])).resolve()
    if args.arrow_dataset.resolve() != expected_dataset:
        parser.error(
            f"--arrow-dataset must match formal matrix path {expected_dataset}"
        )
    args.output = args.output.resolve()
    args.warmup_output = (
        args.warmup_output.resolve()
        if args.warmup_output
        else args.output.with_name("warmups.csv")
    )
    args.logs_dir = (
        args.logs_dir.resolve() if args.logs_dir else args.output.parent / "logs"
    )
    args.commands_output = (
        args.commands_output.resolve()
        if args.commands_output
        else args.output.parent / "commands.txt"
    )
    bundle_root = args.output.parent.resolve()
    for name, path in {
        "--warmup-output": args.warmup_output,
        "--logs-dir": args.logs_dir,
        "--commands-output": args.commands_output,
    }.items():
        try:
            path.resolve().relative_to(bundle_root)
        except ValueError:
            parser.error(f"{name} must remain inside the evidence directory {bundle_root}")
    try:
        manifest = json.loads((args.arrow_dataset / "manifest.json").read_text(encoding="utf-8"))
        args.dataset_lineitem_rows = int(manifest["tables"]["lineitem"]["rows"])
        if str(manifest["scale_factor"]) != args.scale_factor:
            parser.error("Arrow manifest scale_factor does not match formal matrix")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(f"cannot read lineitem row count from Arrow manifest: {exc}")
    return args


def _v5_main() -> int:
    args = _v5_parse_args()
    args.commands_output.parent.mkdir(parents=True, exist_ok=True)
    args.commands_output.write_text("", encoding="utf-8")
    scenarios = sorted({key[1] for key in args.matrix_keys})
    if len(scenarios) != 1:
        raise SystemExit("V5 runner currently requires one scenario per matrix")
    configurations = sorted(
        {
            Configuration(engine=key[0], threads=key[2], cpu_ratio=key[3])
            for key in args.matrix_keys
        },
        key=lambda value: (value.engine, value.threads, value.cpu_ratio),
    )

    warmups: list[BenchmarkRecord] = []
    measured: list[BenchmarkRecord] = []
    for scenario in scenarios:
        for config in configurations:
            for _ in range(args.warmup):
                warmups.append(run_v5_one(args, config, scenario, -1, True))
            for sample_index in range(args.repeat):
                measured.append(
                    run_v5_one(args, config, scenario, sample_index, False)
                )
    write_v5_records(args.warmup_output, warmups)
    write_v5_records(args.output, measured)
    failures = [record for record in measured if record.status != "ok"]
    print(
        f"wrote measured={len(measured)} warmups={len(warmups)} "
        f"failures={len(failures)} output={args.output}"
    )
    return 1 if failures and args.fail_on_error else 0


if __name__ == "__main__":
    sys.exit(_v5_main() if "--matrix" in sys.argv else _legacy_main())
