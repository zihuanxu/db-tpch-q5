#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shlex
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

try:
    from scripts.capture_environment import capture
    from scripts.process_monitor import MonitoredProcessResult, run_monitored
    from scripts.resident_protocol import ResidentSession, parse_resident_jsonl
    from scripts.v7_benchmark_schema import (
        REQUEST_FIELDS,
        SETUP_FIELDS,
        V7BenchmarkRecord,
        V7SetupRecord,
        read_requests,
        read_setups,
        validate_request,
        validate_setup,
    )
except ModuleNotFoundError:
    from capture_environment import capture
    from process_monitor import MonitoredProcessResult, run_monitored
    from resident_protocol import ResidentSession, parse_resident_jsonl
    from v7_benchmark_schema import (
        REQUEST_FIELDS,
        SETUP_FIELDS,
        V7BenchmarkRecord,
        V7SetupRecord,
        read_requests,
        read_setups,
        validate_request,
        validate_setup,
    )


HASH_LENGTH = 16
SHA256_LENGTH = 64
CPP_ENGINES = {
    "cpu-specialized",
    "arrow-acero",
    "gpu-copy",
    "gpu-managed",
    "gpu-mapped",
    "hybrid-arrow",
}
ALL_ENGINES = CPP_ENGINES | {"cudf"}
CORRECTNESS_BACKENDS = {
    "cpu-specialized": "cpu-specialized",
    "arrow-acero": "arrow-acero",
    "gpu-copy": "gpu-copy",
    "gpu-managed": "gpu-managed",
    "gpu-mapped": "gpu-mapped",
    "cudf": "cudf",
}


@dataclass(frozen=True)
class Configuration:
    engine: str
    runner: str
    threads: int
    cpu_ratio: float
    correctness_backend: str
    mode_options: dict[str, object]
    config_id: str = ""
    ratio_mode: str = "fixed"


@dataclass(frozen=True)
class BundleResult:
    setups: tuple[V7SetupRecord, ...]
    warmups: tuple[V7BenchmarkRecord, ...]
    measured: tuple[V7BenchmarkRecord, ...]
    failures: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _payload_sha256(payload: object) -> str:
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_lower_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _strict_fields(
    raw: dict[str, Any],
    *,
    required: set[str],
    optional: set[str] = frozenset(),
    label: str,
) -> None:
    missing = sorted(required - set(raw))
    unknown = sorted(set(raw) - required - optional)
    if missing:
        raise ValueError(f"{label} missing fields: {missing}")
    if unknown:
        raise ValueError(f"{label} unknown fields: {unknown}")


def _validate_engine_options(
    engine: str,
    options: dict[str, Any],
    *,
    label: str,
    explicit: bool,
) -> tuple[str, int, str, float, str, dict[str, object], bool]:
    if engine not in ALL_ENGINES:
        raise ValueError(f"unsupported V7 resident engine: {engine}")
    required = {"runner", "threads", "correctness_backend"}
    optional = {"cpu_ratio", "mode_options"}
    if explicit:
        required |= {"config_id", "engine", "ratio_mode"}
        optional |= {"enabled", "disabled_reason"}
    _strict_fields(options, required=required, optional=optional, label=label)

    runner = options.get("runner")
    expected_runner = "cudf" if engine == "cudf" else "cpp"
    if runner != expected_runner:
        raise ValueError(f"runner for {engine} must be {expected_runner}")
    threads = options.get("threads")
    if isinstance(threads, bool) or not isinstance(threads, int) or threads < 1:
        raise ValueError(f"threads for {engine} must be positive")

    ratio_mode = options.get("ratio_mode", "fixed")
    if ratio_mode not in {"fixed", "auto"}:
        raise ValueError(f"ratio_mode for {engine} must be fixed or auto")
    if ratio_mode == "auto":
        if engine != "hybrid-arrow":
            raise ValueError("ratio_mode=auto is only valid for hybrid-arrow")
        if "cpu_ratio" in options:
            raise ValueError("hybrid auto configuration must omit cpu_ratio")
        ratio = 0.0
    else:
        raw_ratio = options.get("cpu_ratio")
        if (
            isinstance(raw_ratio, bool)
            or not isinstance(raw_ratio, (int, float))
            or not math.isfinite(float(raw_ratio))
            or raw_ratio < 0
            or raw_ratio > 1
        ):
            raise ValueError(f"cpu_ratio for {engine} must be between 0 and 1")
        ratio = float(raw_ratio)
        if engine in {"cpu-specialized", "arrow-acero"} and ratio != 1.0:
            raise ValueError(f"{engine} requires cpu_ratio=1")
        if engine in {"gpu-copy", "gpu-managed", "gpu-mapped", "cudf"} and ratio != 0.0:
            raise ValueError(f"{engine} requires cpu_ratio=0")

    backend = options.get("correctness_backend")
    if not isinstance(backend, str) or not backend:
        raise ValueError(f"correctness_backend for {engine} must be nonempty")
    mode_options = options.get("mode_options", {})
    if not isinstance(mode_options, dict):
        raise ValueError(f"mode_options for {engine} must be an object")
    if engine == "cudf" and threads != 1:
        raise ValueError("cudf requires threads=1 because its runner has no thread option")
    if engine == "cudf" and mode_options != {"framework": "cudf"}:
        raise ValueError("cudf mode_options must be exactly {'framework': 'cudf'}")
    if ratio_mode == "auto" and mode_options.get("selection") != "auto":
        raise ValueError("hybrid auto mode_options.selection must be auto")
    expected_backend = (
        "hybrid-auto"
        if engine == "hybrid-arrow" and ratio_mode == "auto"
        else "hybrid-fixed"
        if engine == "hybrid-arrow"
        else CORRECTNESS_BACKENDS[engine]
    )
    if backend != expected_backend:
        raise ValueError(
            f"correctness_backend for {engine} must be {expected_backend}"
        )

    enabled = options.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError(f"enabled for {engine} must be boolean")
    disabled_reason = options.get("disabled_reason")
    if not enabled and (not isinstance(disabled_reason, str) or not disabled_reason):
        raise ValueError(f"disabled configuration for {engine} requires disabled_reason")
    if enabled and disabled_reason is not None:
        raise ValueError(f"enabled configuration for {engine} cannot have disabled_reason")
    return str(runner), int(threads), str(ratio_mode), ratio, str(backend), mode_options, enabled


def _validate_matrix(matrix: object) -> dict[str, Any]:
    if not isinstance(matrix, dict) or matrix.get("schema_version") != 2:
        raise ValueError("V7 resident matrix schema_version must be 2")
    _strict_fields(
        matrix,
        required={"schema_version", "experiment_id", "dataset", "query", "protocol"},
        optional={"engines", "configurations", "oracle"},
        label="V7 resident matrix",
    )
    if not isinstance(matrix.get("experiment_id"), str) or not matrix["experiment_id"]:
        raise ValueError("V7 resident matrix experiment_id must be nonempty")

    dataset = matrix.get("dataset")
    if not isinstance(dataset, dict):
        raise ValueError("V7 resident matrix dataset must be an object")
    _strict_fields(
        dataset,
        required={"path", "scale_factor", "manifest_sha256", "expected_hash"},
        label="V7 resident matrix dataset",
    )
    for name in ("path", "scale_factor"):
        if not isinstance(dataset.get(name), str) or not dataset[name]:
            raise ValueError(f"V7 resident matrix dataset.{name} must be nonempty")
    if not _is_lower_hex(dataset.get("manifest_sha256"), SHA256_LENGTH):
        raise ValueError("V7 resident matrix requires a lowercase manifest_sha256")
    if not _is_lower_hex(dataset.get("expected_hash"), HASH_LENGTH):
        raise ValueError("V7 resident matrix requires a 16-hex expected_hash")

    oracle = matrix.get("oracle")
    if oracle is not None:
        if not isinstance(oracle, dict):
            raise ValueError("V7 resident matrix oracle must be an object")
        _strict_fields(
            oracle,
            required={"path", "sha256"},
            label="V7 resident matrix oracle",
        )
        if not isinstance(oracle.get("path"), str) or not oracle["path"]:
            raise ValueError("V7 resident matrix oracle.path must be nonempty")
        if not _is_lower_hex(oracle.get("sha256"), SHA256_LENGTH):
            raise ValueError("V7 resident matrix oracle.sha256 must be lowercase SHA256")

    query = matrix.get("query")
    if isinstance(query, dict):
        _strict_fields(
            query,
            required={"region", "date"},
            label="V7 resident matrix query",
        )
    if not isinstance(query, dict) or any(
        not isinstance(query.get(name), str) or not query[name]
        for name in ("region", "date")
    ):
        raise ValueError("V7 resident matrix query identity is invalid")

    protocol = matrix.get("protocol")
    if not isinstance(protocol, dict):
        raise ValueError("V7 resident matrix protocol must be an object")
    _strict_fields(
        protocol,
        required={"warmup", "repeat", "timeout_seconds"},
        label="V7 resident matrix protocol",
    )
    warmup = protocol.get("warmup")
    repeat = protocol.get("repeat")
    timeout = protocol.get("timeout_seconds")
    if isinstance(warmup, bool) or not isinstance(warmup, int) or warmup < 0:
        raise ValueError("V7 resident matrix warmup must be nonnegative")
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 1:
        raise ValueError("V7 resident matrix repeat must be positive")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(float(timeout))
        or timeout <= 0
    ):
        raise ValueError("V7 resident matrix timeout_seconds must be positive")

    engines = matrix.get("engines")
    explicit = matrix.get("configurations")
    if (engines is None) == (explicit is None):
        raise ValueError("V7 resident matrix requires exactly one of engines or configurations")
    if engines is not None:
        if not isinstance(engines, dict) or not engines:
            raise ValueError("V7 resident matrix engines must be a nonempty object")
        for engine, options in engines.items():
            if not isinstance(options, dict):
                raise ValueError(f"V7 resident engine options for {engine} must be an object")
            _validate_engine_options(
                engine, options, label=f"V7 resident engine {engine}", explicit=False
            )
    else:
        if not isinstance(explicit, list) or not explicit:
            raise ValueError("V7 resident matrix configurations must be a nonempty array")
        config_ids: set[str] = set()
        logical_keys: set[tuple[object, ...]] = set()
        for index, options in enumerate(explicit):
            if not isinstance(options, dict):
                raise ValueError(f"configuration {index} must be an object")
            engine = options.get("engine")
            if not isinstance(engine, str):
                raise ValueError(f"configuration {index} engine must be a string")
            config_id = options.get("config_id")
            if not isinstance(config_id, str) or not re.fullmatch(
                r"[a-z0-9][a-z0-9-]{0,63}", config_id
            ):
                raise ValueError(f"configuration {index} config_id must be a lowercase slug")
            if config_id in config_ids:
                raise ValueError(f"duplicate config_id: {config_id}")
            config_ids.add(config_id)
            _, threads, ratio_mode, ratio, _, _, _ = _validate_engine_options(
                engine,
                options,
                label=f"configuration {config_id}",
                explicit=True,
            )
            logical_key = (engine, threads, ratio_mode, ratio)
            if logical_key in logical_keys:
                raise ValueError(f"duplicate configuration: {logical_key}")
            logical_keys.add(logical_key)
    return matrix


def load_matrix(path: Path) -> dict[str, Any]:
    try:
        matrix = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read V7 resident matrix {path}: {exc}") from exc
    return _validate_matrix(matrix)


def configurations(
    matrix: dict[str, Any], *, include_disabled: bool = False
) -> list[Configuration]:
    if "engines" in matrix:
        return [
            Configuration(
                engine=engine,
                runner=str(options["runner"]),
                threads=int(options["threads"]),
                cpu_ratio=float(options["cpu_ratio"]),
                correctness_backend=str(options["correctness_backend"]),
                mode_options=dict(options.get("mode_options", {})),
                config_id=engine,
                ratio_mode="fixed",
            )
            for engine, options in matrix["engines"].items()
        ]
    output: list[Configuration] = []
    for options in matrix["configurations"]:
        if not options.get("enabled", True) and not include_disabled:
            continue
        output.append(
            Configuration(
                engine=str(options["engine"]),
                runner=str(options["runner"]),
                threads=int(options["threads"]),
                cpu_ratio=float(options.get("cpu_ratio", 0.0)),
                correctness_backend=str(options["correctness_backend"]),
                mode_options=dict(options.get("mode_options", {})),
                config_id=str(options["config_id"]),
                ratio_mode=str(options["ratio_mode"]),
            )
        )
    return output


def expected_configuration_ids(matrix: dict[str, Any]) -> set[str]:
    return {config.config_id for config in configurations(matrix)}


def build_command(
    config: Configuration,
    *,
    session_cli: Path,
    dataset: Path,
    region: str,
    date: str,
    warmup: int,
    repeat: int,
    cudf_env: str,
) -> list[str]:
    common = [
        "--dataset",
        str(dataset),
        "--region",
        region,
        "--date",
        date,
        "--warmup",
        str(warmup),
        "--repeat",
        str(repeat),
    ]
    if config.runner == "cpp":
        command = [
            str(session_cli),
            "--engine",
            config.engine,
            "--threads",
            str(config.threads),
            *common,
        ]
        if config.engine == "hybrid-arrow" and config.ratio_mode == "fixed":
            command.extend(["--cpu-ratio", str(config.cpu_ratio)])
        elif config.engine == "hybrid-arrow":
            command.extend(["--hybrid-selection", "auto"])
        return command
    if config.runner == "cudf":
        if config.threads != 1 or config.mode_options != {"framework": "cudf"}:
            raise ValueError("cudf command requires threads=1 and fixed mode_options")
        return [
            "conda",
            "run",
            "-n",
            cudf_env,
            "python",
            "baselines/cudf_q5_session.py",
            *common,
        ]
    raise ValueError(f"unsupported resident runner: {config.runner}")


def _validate_dataset(dataset: Path, matrix: dict[str, Any]) -> int:
    manifest_path = dataset / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"resident dataset manifest is missing: {manifest_path}")
    if _sha256(manifest_path) != matrix["dataset"]["manifest_sha256"]:
        raise ValueError("resident dataset manifest SHA256 does not match matrix")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if str(manifest["scale_factor"]) != matrix["dataset"]["scale_factor"]:
            raise ValueError("resident dataset scale_factor does not match matrix")
        rows = int(manifest["tables"]["lineitem"]["rows"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("resident dataset"):
            raise
        raise ValueError(f"resident dataset manifest is invalid: {exc}") from exc
    if rows < 0:
        raise ValueError("resident dataset lineitem rows must be nonnegative")
    return rows


def _validate_oracle(project_root: Path, matrix: dict[str, Any]) -> Path | None:
    raw = matrix.get("oracle")
    if raw is None:
        return None
    path = Path(raw["path"])
    if not path.is_absolute():
        path = project_root / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"independent oracle is missing: {path}")
    if _sha256(path) != raw["sha256"]:
        raise ValueError("independent oracle SHA256 does not match matrix")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"independent oracle is invalid: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("result_hash") != matrix["dataset"]["expected_hash"]
    ):
        raise ValueError("independent oracle result_hash does not match matrix")
    return path


def _log_paths(output_dir: Path, config_id: str) -> tuple[Path, Path, str, str]:
    stdout_relative = f"logs/{config_id}.stdout.jsonl"
    stderr_relative = f"logs/{config_id}.stderr.txt"
    return (
        output_dir / stdout_relative,
        output_dir / stderr_relative,
        stdout_relative,
        stderr_relative,
    )


def _shared_values(
    matrix: dict[str, Any],
    config: Configuration,
    monitored: MonitoredProcessResult,
    stdout_log: str,
    stderr_log: str,
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "experiment_id": matrix["experiment_id"],
        "config_id": config.config_id or config.engine,
        "ratio_mode": config.ratio_mode,
        "return_code": monitored.return_code,
        "engine": config.engine,
        "scale_factor": matrix["dataset"]["scale_factor"],
        "region": matrix["query"]["region"],
        "date": matrix["query"]["date"],
        "threads": config.threads,
        "cpu_ratio": config.cpu_ratio,
        "gpu_ratio": 1.0 - config.cpu_ratio,
        "gpu_chunk_rows": int(config.mode_options.get("gpu_chunk_rows", 0)),
        "memory_scope": "process_tree",
        "mode_options_json": json.dumps(
            config.mode_options, separators=(",", ":"), sort_keys=True
        ),
        "process_elapsed_ms": monitored.elapsed_ms,
        "cpu_peak_rss_bytes": monitored.peak_rss_bytes,
        "gpu_peak_memory_bytes": monitored.peak_gpu_bytes,
        "cpu_peak_rss_status": monitored.peak_rss_status,
        "gpu_peak_memory_status": monitored.peak_gpu_status,
        "gpu_peak_memory_source": monitored.peak_gpu_source,
        "stdout_log": stdout_log,
        "stderr_log": stderr_log,
        "started_at_utc": monitored.started_at_utc,
        "finished_at_utc": monitored.finished_at_utc,
    }


def _validate_session_identity(
    session: ResidentSession,
    config: Configuration,
    dataset: Path,
    matrix: dict[str, Any],
    project_root: Path,
) -> None:
    setup = session.setup
    expected = {
        "engine": config.engine,
        "region": matrix["query"]["region"],
        "date": matrix["query"]["date"],
    }
    for name, value in expected.items():
        if setup.get(name) != value:
            raise ValueError(
                f"session_setup {name} mismatch expected={value!r} actual={setup.get(name)!r}"
            )
    raw_dataset = Path(str(setup["dataset"]))
    emitted_dataset = (
        raw_dataset.resolve()
        if raw_dataset.is_absolute()
        else (project_root / raw_dataset).resolve()
    )
    if emitted_dataset != dataset.resolve():
        raise ValueError("session_setup dataset does not match matrix")
    for name, value in (
        ("threads", config.threads),
        ("warmup", matrix["protocol"]["warmup"]),
        ("repeat", matrix["protocol"]["repeat"]),
    ):
        if name in setup and setup[name] != value:
            raise ValueError(f"session_setup {name} does not match matrix")
    if not math.isclose(
        float(setup["selected_cpu_ratio"]), config.cpu_ratio, abs_tol=1e-9
    ) and config.ratio_mode == "fixed":
        raise ValueError("session_setup selected_cpu_ratio does not match matrix")


def _hybrid_provenance_values(
    config: Configuration, setup: dict[str, Any] | None = None
) -> dict[str, object | None]:
    unavailable: dict[str, object | None] = {
        "hybrid_provenance_status": "unavailable",
        "hybrid_model_version": None,
        "calibration_rows": None,
        "cpu_calibration_requests": None,
        "gpu_calibration_requests": None,
        "cpu_calibration_ms": None,
        "gpu_calibration_ms": None,
        "gpu_kernel_calibration_ms": None,
        "gpu_fixed_ms": None,
        "cpu_rows_per_ms": None,
        "gpu_kernel_rows_per_ms": None,
        "realized_cpu_ratio": None,
        "selected_batch_boundary_rows": None,
        "predicted_cpu_ratio": None,
    }
    if config.ratio_mode != "auto" or setup is None:
        return unavailable
    gpu_throughput = setup.get("gpu_kernel_rows_per_ms")
    if gpu_throughput is None:
        gpu_throughput = setup.get("gpu_rows_per_ms")
    return {
        "hybrid_provenance_status": "measured",
        "hybrid_model_version": setup.get("hybrid_model_version"),
        "calibration_rows": setup.get("calibration_rows"),
        "cpu_calibration_requests": setup.get("cpu_calibration_requests"),
        "gpu_calibration_requests": setup.get("gpu_calibration_requests"),
        "cpu_calibration_ms": setup.get("cpu_calibration_ms"),
        "gpu_calibration_ms": setup.get("gpu_calibration_ms"),
        "gpu_kernel_calibration_ms": setup.get("gpu_kernel_calibration_ms"),
        "gpu_fixed_ms": setup.get("gpu_fixed_ms"),
        "cpu_rows_per_ms": setup.get("cpu_rows_per_ms"),
        "gpu_kernel_rows_per_ms": gpu_throughput,
        "realized_cpu_ratio": setup.get("realized_cpu_ratio"),
        "selected_batch_boundary_rows": setup.get("selected_batch_boundary_rows"),
        "predicted_cpu_ratio": setup.get("predicted_cpu_ratio"),
    }


def _setup_from_session(
    matrix: dict[str, Any],
    config: Configuration,
    session: ResidentSession,
    monitored: MonitoredProcessResult,
    stdout_log: str,
    stderr_log: str,
) -> V7SetupRecord:
    setup = session.setup
    effective_ratio = (
        float(setup["selected_cpu_ratio"])
        if config.ratio_mode == "auto"
        else config.cpu_ratio
    )
    values = _shared_values(matrix, config, monitored, stdout_log, stderr_log)
    values["cpu_ratio"] = effective_ratio
    values["gpu_ratio"] = 1.0 - effective_ratio
    process_ok = monitored.return_code == 0 and not monitored.timed_out and session.complete
    if process_ok:
        status, error_class = "ok", ""
    elif session.missing_request_indexes:
        status, error_class = "error", "ERROR_NOT_EXECUTED"
    else:
        status, error_class = _failure_metadata(monitored, None)
    values.update(
        session_id=session.session_id,
        lifecycle="resident",
        status=status,
        error_class=error_class,
        dataset_path=matrix["dataset"]["path"],
        dataset_load_ms=setup["dataset_load_ms"],
        session_setup_ms=setup["session_setup_ms"],
        plan_build_ms=setup.get("plan_build_ms", 0.0),
        host_staging_ms=setup.get("host_staging_ms", 0.0),
        allocation_ms=setup.get("allocation_ms", 0.0),
        initial_h2d_ms=setup.get("initial_h2d_ms", 0.0),
        tune_ms=setup["tune_ms"],
        resident_host_bytes=setup["resident_host_bytes"],
        resident_gpu_bytes=setup["resident_gpu_bytes"],
        resident_pinned_bytes=setup["resident_pinned_bytes"],
        selected_cpu_ratio=setup["selected_cpu_ratio"],
        **_hybrid_provenance_values(config, setup),
    )
    return validate_setup(values)


def _request_from_session(
    matrix: dict[str, Any],
    config: Configuration,
    session: ResidentSession,
    setup: V7SetupRecord,
    request: dict[str, Any],
    monitored: MonitoredProcessResult,
    stdout_log: str,
    stderr_log: str,
) -> V7BenchmarkRecord:
    shared = _shared_values(matrix, config, monitored, stdout_log, stderr_log)
    shared["cpu_ratio"] = setup.cpu_ratio
    shared["gpu_ratio"] = setup.gpu_ratio
    request_index = int(request["request_index"])
    is_warmup = bool(request["is_warmup"])
    warmup_count = int(matrix["protocol"]["warmup"])
    query_ms = float(request["query_total_ms"])
    input_rows = int(request["input_lineitem_rows"])
    if config.runner == "cudf":
        measurements: dict[str, object | None] = {
            name: None
            for name in (
                "plan_build_ms",
                "host_prepare_ms",
                "h2d_ms",
                "cpu_scan_ms",
                "gpu_kernel_ms",
                "d2h_ms",
                "overlap_wall_ms",
                "h2d_bytes",
                "d2h_bytes",
                "mapped_remote_read_bytes",
            )
        }
    else:
        cpu_scan_ms = (
            request["scan_ms"]
            if config.engine in {"cpu-specialized", "arrow-acero"}
            else request["cpu_ms"]
            if config.engine == "hybrid-arrow"
            else 0.0
        )
        measurements = {
            "plan_build_ms": request["build_ms"],
            "host_prepare_ms": None,
            "h2d_ms": request["h2d_ms"],
            "cpu_scan_ms": cpu_scan_ms,
            "gpu_kernel_ms": request["kernel_ms"],
            "d2h_ms": request["d2h_ms"],
            "overlap_wall_ms": request["overlap_wall_ms"],
            "h2d_bytes": request["h2d_bytes"],
            "d2h_bytes": request["d2h_bytes"],
            "mapped_remote_read_bytes": request["mapped_remote_read_bytes"],
        }
    measurement_status = {
        name: "measured" if value is not None else "unavailable"
        for name, value in measurements.items()
    }
    values: dict[str, object] = {
        **shared,
        "run_uuid": str(uuid.uuid4()),
        "status": request["status"],
        "error_class": request["error_class"],
        "scenario": "resident",
        "sample_index": request_index if is_warmup else request_index - warmup_count,
        "is_warmup": is_warmup,
        "not_applicable_phases": json.dumps(
            sorted(name for name, status in measurement_status.items() if status == "unavailable"),
            separators=(",", ":"),
        ),
        "result_rows": request["result_rows"],
        "result_hash": request["result_hash"],
        "rows_json": json.dumps(
            request["rows"], separators=(",", ":"), sort_keys=True
        ),
        "oracle_status": (
            "expected_hash_match" if request["status"] == "ok" else "not_run"
        ),
        "load_ms": 0.0,
        "plan_build_ms": measurements["plan_build_ms"],
        "host_prepare_ms": measurements["host_prepare_ms"],
        "h2d_ms": measurements["h2d_ms"],
        "cpu_scan_ms": measurements["cpu_scan_ms"],
        "gpu_kernel_ms": measurements["gpu_kernel_ms"],
        "d2h_ms": measurements["d2h_ms"],
        "overlap_wall_ms": measurements["overlap_wall_ms"],
        "query_total_ms": query_ms,
        "input_lineitem_rows": input_rows,
        "matched_lineitem_rows": request["matched_lineitem_rows"],
        "cpu_input_rows": request["cpu_input_rows"],
        "gpu_input_rows": request["gpu_input_rows"],
        "h2d_bytes": measurements["h2d_bytes"],
        "d2h_bytes": measurements["d2h_bytes"],
        "mapped_remote_read_bytes": measurements["mapped_remote_read_bytes"],
        "throughput_rows_per_second": input_rows * 1000.0 / query_ms if query_ms else 0.0,
        "session_id": session.session_id,
        "lifecycle": "resident",
        "dataset_load_ms": setup.dataset_load_ms,
        "session_setup_ms": setup.session_setup_ms,
        "tune_ms": setup.tune_ms,
        "resident_host_bytes": setup.resident_host_bytes,
        "resident_gpu_bytes": setup.resident_gpu_bytes,
        "resident_pinned_bytes": setup.resident_pinned_bytes,
        "selected_cpu_ratio": request["selected_cpu_ratio"],
        "predicted_cpu_ratio": setup.predicted_cpu_ratio,
        "request_index": request_index,
        "measurement_status_json": json.dumps(
            measurement_status, separators=(",", ":"), sort_keys=True
        ),
    }
    return validate_request(values)


def _partial_session_id(stdout: str) -> str:
    try:
        first = next(line for line in stdout.splitlines() if line.strip())
        value = json.loads(first).get("session_id")
        parsed = uuid.UUID(str(value))
        if str(parsed) == value:
            return value
    except (StopIteration, AttributeError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return str(uuid.uuid4())


def _failure_metadata(
    monitored: MonitoredProcessResult, protocol_error: ValueError | None
) -> tuple[str, str]:
    if monitored.timed_out:
        return "timeout", "ERROR_TIMEOUT"
    if monitored.return_code == 77:
        return "skipped_no_gpu", "ERROR_NO_GPU"
    if monitored.return_code != 0:
        return "error", "ERROR_PROCESS_EXIT"
    if protocol_error is not None:
        return "error", "ERROR_RESIDENT_PROTOCOL"
    raise ValueError("failure metadata requested for a successful process")


def _failure_records(
    matrix: dict[str, Any],
    config: Configuration,
    monitored: MonitoredProcessResult,
    stdout: str,
    stdout_log: str,
    stderr_log: str,
    protocol_error: ValueError | None,
) -> tuple[V7SetupRecord, list[V7BenchmarkRecord]]:
    status, error_class = _failure_metadata(monitored, protocol_error)
    session_id = _partial_session_id(stdout)
    shared = _shared_values(matrix, config, monitored, stdout_log, stderr_log)
    setup_values = {
        **shared,
        "session_id": session_id,
        "lifecycle": "resident",
        "status": status,
        "error_class": error_class,
        "dataset_path": matrix["dataset"]["path"],
        "dataset_load_ms": 0.0,
        "session_setup_ms": 0.0,
        "plan_build_ms": 0.0,
        "host_staging_ms": 0.0,
        "allocation_ms": 0.0,
        "initial_h2d_ms": 0.0,
        "tune_ms": 0.0,
        "resident_host_bytes": 0,
        "resident_gpu_bytes": 0,
        "resident_pinned_bytes": 0,
        "selected_cpu_ratio": config.cpu_ratio,
        **_hybrid_provenance_values(config),
    }
    setup = validate_setup(setup_values)
    return setup, []


def _run_configuration(
    matrix: dict[str, Any],
    config: Configuration,
    *,
    project_root: Path,
    session_cli: Path,
    dataset: Path,
    output_dir: Path,
    cudf_env: str,
    gpu_index: int,
    commands_path: Path,
) -> tuple[V7SetupRecord, list[V7BenchmarkRecord]]:
    stdout_path, stderr_path, stdout_log, stderr_log = _log_paths(
        output_dir, config.config_id or config.engine
    )
    command = build_command(
        config,
        session_cli=session_cli,
        dataset=dataset,
        region=matrix["query"]["region"],
        date=matrix["query"]["date"],
        warmup=matrix["protocol"]["warmup"],
        repeat=matrix["protocol"]["repeat"],
        cudf_env=cudf_env,
    )
    with commands_path.open("a", encoding="utf-8") as handle:
        handle.write(shlex.join(command) + "\n")
    monitored = run_monitored(
        command,
        float(matrix["protocol"]["timeout_seconds"]),
        stdout_path,
        stderr_path,
        cwd=project_root,
        env={"CUDA_VISIBLE_DEVICES": str(gpu_index)},
    )
    stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
    protocol_error: ValueError | None = None
    session: ResidentSession | None = None
    if stdout.strip():
        try:
            session = parse_resident_jsonl(
                stdout,
                warmup=matrix["protocol"]["warmup"],
                repeat=matrix["protocol"]["repeat"],
                expected_hash=matrix["dataset"]["expected_hash"],
                allow_partial=monitored.return_code != 0 or monitored.timed_out,
            )
            _validate_session_identity(session, config, dataset, matrix, project_root)
        except ValueError as exc:
            protocol_error = exc
            with stderr_path.open("a", encoding="utf-8") as handle:
                handle.write(f"resident protocol error: {exc}\n")
    if session is None:
        return _failure_records(
            matrix,
            config,
            monitored,
            stdout,
            stdout_log,
            stderr_log,
            protocol_error,
        )

    setup = _setup_from_session(
        matrix, config, session, monitored, stdout_log, stderr_log
    )
    requests = [
        _request_from_session(
            matrix,
            config,
            session,
            setup,
            request,
            monitored,
            stdout_log,
            stderr_log,
        )
        for request in (*session.warmups, *session.measured)
    ]
    return setup, requests


def _write_csv(path: Path, fields: list[str], records: Sequence[object]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for record in records:
            writer.writerow(record.as_dict())  # type: ignore[attr-defined]


def _prepare_output(output_dir: Path) -> None:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"V7 output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "logs").mkdir(exist_ok=True)


def run_matrix(
    *,
    matrix_path: Path,
    project_root: Path,
    session_cli: Path,
    output_dir: Path,
    cudf_env: str,
    gpu_index: int = 0,
) -> BundleResult:
    if isinstance(gpu_index, bool) or gpu_index < 0:
        raise ValueError("gpu_index must be nonnegative")
    project_root = project_root.resolve()
    matrix = load_matrix(matrix_path.resolve())
    dataset = Path(matrix["dataset"]["path"])
    if not dataset.is_absolute():
        dataset = project_root / dataset
    dataset = dataset.resolve()
    _validate_dataset(dataset, matrix)
    _validate_oracle(project_root, matrix)
    resolved_cli = session_cli if session_cli.is_absolute() else project_root / session_cli
    resolved_cli = resolved_cli.resolve()
    if not resolved_cli.is_file():
        raise ValueError(f"session CLI is missing: {resolved_cli}")
    _prepare_output(output_dir)
    commands_path = output_dir / "commands.txt"
    commands_path.write_text("", encoding="utf-8")
    code_root = Path(__file__).resolve().parents[1]
    identity_root = project_root if (project_root / ".git").exists() else code_root
    environment = capture(
        project_root=identity_root,
        session_cli=resolved_cli,
        cudf_env=cudf_env,
        gpu_index=gpu_index,
    )
    environment["gpu"]["cuda_visible_devices"] = str(gpu_index)
    environment["v7_runner"] = {
        "matrix": str(matrix_path.resolve()),
        "matrix_sha256": _sha256(matrix_path.resolve()),
        "matrix_payload": matrix,
        "matrix_payload_sha256": _payload_sha256(matrix),
        "session_cli": str(resolved_cli),
        "session_cli_sha256": _sha256(resolved_cli),
        "dataset": str(dataset),
        "cudf_env": cudf_env,
        "git_commit": environment["git"]["commit"],
        "gpu_index": gpu_index,
        "cuda_visible_devices": str(gpu_index),
    }
    (output_dir / "environment.json").write_text(
        json.dumps(environment, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    setups: list[V7SetupRecord] = []
    requests: list[V7BenchmarkRecord] = []
    for config in configurations(matrix):
        setup, config_requests = _run_configuration(
            matrix,
            config,
            project_root=project_root,
            session_cli=resolved_cli,
            dataset=dataset,
            output_dir=output_dir,
            cudf_env=cudf_env,
            gpu_index=gpu_index,
            commands_path=commands_path,
        )
        setups.append(setup)
        requests.extend(config_requests)
    warmups = [record for record in requests if record.is_warmup]
    measured = [record for record in requests if not record.is_warmup]
    _write_csv(output_dir / "setup.csv", SETUP_FIELDS, setups)
    _write_csv(output_dir / "setups.csv", SETUP_FIELDS, setups)
    _write_csv(output_dir / "warmups.csv", REQUEST_FIELDS, warmups)
    _write_csv(output_dir / "raw.csv", REQUEST_FIELDS, measured)
    failures = sum(setup.status != "ok" for setup in setups)
    return BundleResult(tuple(setups), tuple(warmups), tuple(measured), failures)


def _correctness_identity(matrix: dict[str, Any]) -> dict[str, str]:
    if matrix.get("schema_version") != 1:
        raise ValueError("correctness matrix schema_version must be 1")
    dataset = matrix.get("dataset")
    query = matrix.get("query")
    if not isinstance(dataset, dict) or not isinstance(query, dict):
        raise ValueError("correctness matrix requires dataset and query objects")
    identity = {
        "experiment_id": matrix.get("experiment_id"),
        "scale_factor": dataset.get("scale_factor"),
        "dataset_path": dataset.get("path"),
        "dataset_manifest_sha256": dataset.get("manifest_sha256"),
        "region": query.get("region"),
        "date": query.get("date"),
    }
    if any(not isinstance(value, str) or not value for value in identity.values()):
        raise ValueError("correctness matrix identity fields must be nonempty strings")
    if not _is_lower_hex(identity["dataset_manifest_sha256"], SHA256_LENGTH):
        raise ValueError("correctness matrix manifest_sha256 must be lowercase SHA256")
    return identity  # type: ignore[return-value]


def _audit_materialization_inputs(
    setups: list[V7SetupRecord],
    warmups: list[V7BenchmarkRecord],
    measured: list[V7BenchmarkRecord],
    *,
    expected_warmup: int,
    expected_repeat: int,
) -> None:
    setup_ids = [record.session_id for record in setups]
    if len(setup_ids) != len(set(setup_ids)):
        raise ValueError("correctness materialization requires one setup per session_id")
    known = set(setup_ids)
    if any(record.session_id not in known for record in [*warmups, *measured]):
        raise ValueError("correctness request has no matching setup")
    for setup in setups:
        if setup.status != "ok" or setup.return_code != 0:
            raise ValueError(
                f"correctness materialization requires a successful setup for {setup.engine}"
            )
        session_warmups = [row for row in warmups if row.session_id == setup.session_id]
        session_measured = [row for row in measured if row.session_id == setup.session_id]
        if len(session_warmups) != expected_warmup:
            raise ValueError(
                "source protocol warmup count mismatch "
                f"expected={expected_warmup} actual={len(session_warmups)}"
            )
        if len(session_measured) != expected_repeat:
            raise ValueError(
                "source protocol repeat count mismatch "
                f"expected={expected_repeat} actual={len(session_measured)}"
            )
        if not session_measured:
            raise ValueError(f"correctness session {setup.session_id} has no measured rows")
        if [row.sample_index for row in session_warmups] != list(range(len(session_warmups))):
            raise ValueError("correctness warmup sample indices are not contiguous")
        if [row.sample_index for row in session_measured] != list(range(len(session_measured))):
            raise ValueError("correctness measured sample indices are not contiguous")
        combined = [*session_warmups, *session_measured]
        if [row.request_index for row in combined] != list(range(len(combined))):
            raise ValueError("correctness request indices are not contiguous")
        if any(not row.is_warmup for row in session_warmups) or any(
            row.is_warmup for row in session_measured
        ):
            raise ValueError("correctness warmup flags do not match CSV placement")
        if any(
            row.status != "ok"
            or row.return_code != 0
            or row.lifecycle != "resident"
            or row.engine != setup.engine
            or row.result_hash != combined[0].result_hash
            or row.rows != combined[0].rows
            for row in combined
        ):
            raise ValueError("correctness session rows, hashes, or process status are unstable")


def _source_matrix_from_environment(setup_csv: Path) -> dict[str, Any]:
    environment_path = setup_csv.parent / "environment.json"
    try:
        environment = json.loads(environment_path.read_text(encoding="utf-8"))
        runner = environment["v7_runner"]
        matrix = runner["matrix_payload"]
        matrix_sha256 = runner["matrix_sha256"]
        matrix_payload_sha256 = runner["matrix_payload_sha256"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"cannot read source matrix provenance from {environment_path}: {exc}"
        ) from exc
    validated = _validate_matrix(matrix)
    if not _is_lower_hex(matrix_sha256, SHA256_LENGTH):
        raise ValueError("source matrix provenance has an invalid SHA256")
    if (
        not _is_lower_hex(matrix_payload_sha256, SHA256_LENGTH)
        or _payload_sha256(validated) != matrix_payload_sha256
    ):
        raise ValueError("embedded source matrix digest does not match its payload")
    source_path = runner.get("matrix")
    if isinstance(source_path, str) and Path(source_path).is_file():
        if _sha256(Path(source_path)) != matrix_sha256:
            raise ValueError("source matrix file no longer matches recorded SHA256")
        try:
            source_payload = json.loads(Path(source_path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("source matrix file is no longer valid JSON") from exc
        if source_payload != validated:
            raise ValueError("source matrix file does not match embedded matrix payload")
    return validated


def _bind_source_matrix(
    source: dict[str, Any],
    correctness_identity: dict[str, str],
    setups: list[V7SetupRecord],
    requests: list[V7BenchmarkRecord],
) -> dict[str, str]:
    source_dataset = source["dataset"]
    source_query = source["query"]
    source_identity = {
        "scale_factor": source_dataset["scale_factor"],
        "dataset_path": source_dataset["path"],
        "dataset_manifest_sha256": source_dataset["manifest_sha256"],
        "region": source_query["region"],
        "date": source_query["date"],
    }
    correctness_dataset_identity = {
        name: correctness_identity[name] for name in source_identity
    }
    if source_identity != correctness_dataset_identity:
        raise ValueError("source dataset identity does not match correctness matrix")

    source_configs = {config.config_id: config for config in configurations(source)}
    if {setup.config_id for setup in setups} != set(source_configs):
        raise ValueError("source matrix configurations do not match setup coverage")
    setup_by_session = {setup.session_id: setup for setup in setups}
    if any(
        request.session_id not in setup_by_session
        or request.config_id != setup_by_session[request.session_id].config_id
        for request in requests
    ):
        raise ValueError("request configuration does not match its source setup")
    backend_by_engine: dict[str, str] = {}
    for setup in setups:
        config = source_configs[setup.config_id]
        if (
            setup.experiment_id != source["experiment_id"]
            or setup.scale_factor != source_dataset["scale_factor"]
            or setup.dataset_path != source_dataset["path"]
            or setup.region != source_query["region"]
            or setup.date != source_query["date"]
            or setup.engine != config.engine
            or setup.threads != config.threads
            or setup.ratio_mode != config.ratio_mode
            or (
                config.ratio_mode == "fixed"
                and not math.isclose(setup.cpu_ratio, config.cpu_ratio, abs_tol=1e-9)
            )
        ):
            raise ValueError(f"setup for {setup.config_id} does not match source matrix")
        if setup.engine in backend_by_engine:
            raise ValueError(
                f"correctness materialization has multiple configurations for {setup.engine}"
            )
        backend_by_engine[setup.engine] = config.correctness_backend
    expected_hash = source_dataset["expected_hash"]
    if any(request.result_hash != expected_hash for request in requests):
        raise ValueError("request exact rows do not match source matrix expected_hash")
    return backend_by_engine


def materialize_correctness_records(
    *,
    setup_csv: Path,
    warmups_csv: Path,
    raw_csv: Path,
    correctness_matrix: Path,
    output_dir: Path,
    sample_index: int = 0,
) -> list[Path]:
    if isinstance(sample_index, bool) or sample_index < 0:
        raise ValueError("sample_index must be nonnegative")
    setups = read_setups(setup_csv)
    warmups = read_requests(warmups_csv)
    measured = read_requests(raw_csv)
    bundle_root = setup_csv.parent.resolve()
    if (
        warmups_csv.parent.resolve() != bundle_root
        or raw_csv.parent.resolve() != bundle_root
    ):
        raise ValueError("setup, warmup, and raw CSVs must come from one bundle")
    source_matrix = _source_matrix_from_environment(setup_csv)
    source_protocol = source_matrix["protocol"]
    _audit_materialization_inputs(
        setups,
        warmups,
        measured,
        expected_warmup=int(source_protocol["warmup"]),
        expected_repeat=int(source_protocol["repeat"]),
    )
    try:
        matrix = json.loads(correctness_matrix.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read correctness matrix: {exc}") from exc
    if not isinstance(matrix, dict):
        raise ValueError("correctness matrix must be an object")
    identity = _correctness_identity(matrix)
    backend_by_engine = _bind_source_matrix(
        source_matrix, identity, setups, [*warmups, *measured]
    )
    required = matrix.get("required_backends")
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise ValueError("correctness matrix required_backends must be a string list")
    enabled_backends = set(required)
    hybrid_auto = matrix.get("hybrid_auto", {})
    if isinstance(hybrid_auto, dict) and hybrid_auto.get("enabled") is True:
        enabled_backends.add("hybrid-auto")

    by_engine: dict[str, list[V7SetupRecord]] = {}
    for setup in setups:
        by_engine.setdefault(setup.engine, []).append(setup)
    if any(len(group) != 1 for group in by_engine.values()):
        raise ValueError("correctness materialization has ambiguous engine sessions")
    backend_set = {backend_by_engine[engine] for engine in by_engine}
    if len(backend_set) != len(by_engine):
        raise ValueError("correctness materialization maps multiple engines to one backend")
    unknown = sorted(backend_set - enabled_backends)
    if unknown:
        raise ValueError(f"correctness matrix does not enable backends: {unknown}")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"correctness output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    outputs: list[Path] = []
    for engine, group in sorted(by_engine.items()):
        setup = group[0]
        candidates = [
            row
            for row in measured
            if row.session_id == setup.session_id and row.sample_index == sample_index
        ]
        if len(candidates) != 1:
            raise ValueError(
                f"engine {engine} requires exactly one measured sample_index={sample_index}"
            )
        request = candidates[0]
        backend = backend_by_engine[engine]
        record = {
            "schema_version": 1,
            "backend": backend,
            "identity": identity,
            "process": {
                "status": "ok",
                "return_code": setup.return_code,
                "session_id": setup.session_id,
                "lifecycle": setup.lifecycle,
                "elapsed_ms": setup.process_elapsed_ms,
                "cpu_peak_rss_bytes": setup.cpu_peak_rss_bytes,
                "gpu_peak_memory_bytes": setup.gpu_peak_memory_bytes,
                "stdout_log": setup.stdout_log,
                "stderr_log": setup.stderr_log,
            },
            "output": {
                "result_hash": request.result_hash,
                "rows": request.rows,
            },
        }
        path = output_dir / f"{backend}.json"
        path.write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        outputs.append(path)
    return outputs


def _run_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run strict V7 resident benchmark sessions")
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--session-cli",
        type=Path,
        default=Path("build-arrow-cuda-v3/memq5_arrow_session"),
    )
    parser.add_argument("--cudf-env", default="memq5-cudf")
    parser.add_argument("--gpu-index", type=int, default=0)
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args(argv)


def _materialize_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize exact-row V7 correctness-gate run records"
    )
    parser.add_argument("--setup", required=True, type=Path)
    parser.add_argument("--warmups", required=True, type=Path)
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--matrix", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--sample-index", type=int, default=0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if arguments and arguments[0] == "materialize-correctness":
            args = _materialize_args(arguments[1:])
            outputs = materialize_correctness_records(
                setup_csv=args.setup,
                warmups_csv=args.warmups,
                raw_csv=args.raw,
                correctness_matrix=args.matrix,
                output_dir=args.output_dir,
                sample_index=args.sample_index,
            )
            print(f"materialized correctness_records={len(outputs)} output={args.output_dir}")
            return 0
        if arguments and arguments[0] == "run":
            arguments = arguments[1:]
        args = _run_args(arguments)
        result = run_matrix(
            matrix_path=args.matrix,
            project_root=args.project_root,
            session_cli=args.session_cli,
            output_dir=args.output_dir,
            cudf_env=args.cudf_env,
            gpu_index=args.gpu_index,
        )
    except (FileExistsError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"wrote setups={len(result.setups)} warmups={len(result.warmups)} "
        f"measured={len(result.measured)} failures={result.failures} "
        f"output={args.output_dir}"
    )
    return 1 if result.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
