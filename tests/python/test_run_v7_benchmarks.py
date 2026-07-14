from __future__ import annotations

import hashlib
import csv
import json
import sys
from pathlib import Path

import pytest

from scripts.v7_benchmark_schema import read_requests, read_setups
from scripts.verify_q5_oracle import result_hash_hex


ROOT = Path(__file__).resolve().parents[2]
ROWS = [
    {"nation": "JAPAN", "revenue_1e4": 1900000},
    {"nation": "INDIA", "revenue_1e4": 900000},
]
RESULT_HASH = result_hash_hex(
    [(row["nation"], row["revenue_1e4"]) for row in ROWS]
)
SESSION_ID = "00000000-0000-4000-8000-000000000001"
BASE_BACKENDS = [
    "cpu-specialized",
    "arrow-acero",
    "gpu-copy",
    "gpu-managed",
    "gpu-mapped",
    "hybrid-fixed",
    "cudf",
]


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_matrix(
    project_root: Path,
    *,
    engine: str = "cpu-specialized",
    expected_hash: str = RESULT_HASH,
    scale_factor: str = "1",
    dataset_path: str = "data/arrow",
) -> Path:
    dataset = project_root / dataset_path
    manifest = dataset / "manifest.json"
    _write_json(
        manifest,
        {
            "scale_factor": scale_factor,
            "tables": {"lineitem": {"rows": 6}},
        },
    )
    matrix = project_root / "matrix.yml"
    cpu_ratio = 1.0 if engine == "cpu-specialized" else 0.0
    _write_json(
        matrix,
        {
            "schema_version": 2,
            "experiment_id": "v7-resident-test",
            "dataset": {
                "path": dataset_path,
                "scale_factor": scale_factor,
                "manifest_sha256": _sha256(manifest),
                "expected_hash": expected_hash,
            },
            "query": {"region": "ASIA", "date": "1994-01-01"},
            "engines": {
                engine: {
                    "runner": "cpp",
                    "threads": 1,
                    "cpu_ratio": cpu_ratio,
                    "correctness_backend": engine,
                }
            },
            "protocol": {"warmup": 1, "repeat": 2, "timeout_seconds": 10},
        },
    )
    return matrix


def _write_fake_session(path: Path, launches: Path) -> None:
    setup = {
        "record_type": "session_setup",
        "session_id": SESSION_ID,
        "lifecycle": "resident",
        "status": "ok",
        "error_class": "",
        "engine": "cpu-specialized",
        "region": "ASIA",
        "date": "1994-01-01",
        "threads": 1,
        "warmup": 1,
        "repeat": 2,
        "dataset_load_ms": 1.0,
        "session_setup_ms": 2.0,
        "plan_build_ms": 0.5,
        "host_staging_ms": 0.25,
        "allocation_ms": 0.0,
        "initial_h2d_ms": 0.0,
        "tune_ms": 0.0,
        "resident_host_bytes": 1024,
        "resident_gpu_bytes": 0,
        "resident_pinned_bytes": 0,
        "selected_cpu_ratio": 1.0,
        "predicted_cpu_ratio": 0.0,
    }
    request = {
        "record_type": "request",
        "session_id": SESSION_ID,
        "lifecycle": "resident",
        "status": "ok",
        "error_class": "",
        "selected_cpu_ratio": 1.0,
        "result_rows": len(ROWS),
        "result_hash": RESULT_HASH,
        "rows": ROWS,
        "build_ms": 0.0,
        "h2d_ms": 0.0,
        "kernel_ms": 0.0,
        "d2h_ms": 0.0,
        "scan_ms": 2.0,
        "query_total_ms": 2.2,
        "cpu_ms": 2.0,
        "gpu_ms": 0.0,
        "overlap_wall_ms": 0.75,
        "input_lineitem_rows": 6,
        "matched_lineitem_rows": 2,
        "cpu_input_rows": 6,
        "gpu_input_rows": 0,
        "h2d_bytes": 0,
        "d2h_bytes": 0,
        "mapped_remote_read_bytes": 0,
    }
    source = f"""#!{sys.executable}
import json
import sys

args = sys.argv[1:]
dataset = args[args.index('--dataset') + 1]
with open({str(launches)!r}, 'a', encoding='utf-8') as handle:
    handle.write('launch\\n')
setup = {setup!r}
setup['dataset'] = dataset
print(json.dumps(setup, sort_keys=True))
request = {request!r}
for index in range(3):
    row = dict(request)
    row['request_index'] = index
    row['is_warmup'] = index == 0
    print(json.dumps(row, sort_keys=True))
"""
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _write_partial_failing_session(path: Path, launches: Path) -> None:
    setup = {
        "record_type": "session_setup",
        "session_id": SESSION_ID,
        "lifecycle": "resident",
        "status": "ok",
        "error_class": "",
        "engine": "cpu-specialized",
        "region": "ASIA",
        "date": "1994-01-01",
        "threads": 1,
        "warmup": 1,
        "repeat": 2,
        "dataset_load_ms": 1.0,
        "session_setup_ms": 2.0,
        "plan_build_ms": 0.5,
        "host_staging_ms": 0.25,
        "allocation_ms": 0.0,
        "initial_h2d_ms": 0.0,
        "tune_ms": 0.0,
        "resident_host_bytes": 1024,
        "resident_gpu_bytes": 0,
        "resident_pinned_bytes": 0,
        "selected_cpu_ratio": 1.0,
        "predicted_cpu_ratio": 0.0,
    }
    success = {
        "record_type": "request",
        "session_id": SESSION_ID,
        "lifecycle": "resident",
        "status": "ok",
        "error_class": "",
        "request_index": 0,
        "is_warmup": True,
        "selected_cpu_ratio": 1.0,
        "result_rows": len(ROWS),
        "result_hash": RESULT_HASH,
        "rows": ROWS,
        "build_ms": 0.0,
        "h2d_ms": 0.0,
        "kernel_ms": 0.0,
        "d2h_ms": 0.0,
        "scan_ms": 2.0,
        "query_total_ms": 2.2,
        "cpu_ms": 2.0,
        "gpu_ms": 0.0,
        "overlap_wall_ms": 0.5,
        "input_lineitem_rows": 6,
        "matched_lineitem_rows": 2,
        "cpu_input_rows": 6,
        "gpu_input_rows": 0,
        "h2d_bytes": 0,
        "d2h_bytes": 0,
        "mapped_remote_read_bytes": 0,
    }
    failed = {
        **success,
        "status": "error",
        "error_class": "RuntimeError",
        "request_index": 1,
        "is_warmup": False,
        "result_rows": 0,
        "result_hash": "",
        "rows": [],
        "query_total_ms": 0.0,
        "input_lineitem_rows": 0,
        "matched_lineitem_rows": 0,
        "cpu_input_rows": 0,
    }
    source = f"""#!{sys.executable}
import json
import sys
args = sys.argv[1:]
with open({str(launches)!r}, 'a', encoding='utf-8') as handle:
    handle.write('launch\\n')
setup = {setup!r}
setup['dataset'] = args[args.index('--dataset') + 1]
print(json.dumps(setup, sort_keys=True))
print(json.dumps({success!r}, sort_keys=True))
print(json.dumps({failed!r}, sort_keys=True))
raise SystemExit(3)
"""
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _write_failing_session(path: Path, launches: Path) -> None:
    path.write_text(
        f"""#!{sys.executable}
import sys
with open({str(launches)!r}, 'a', encoding='utf-8') as handle:
    handle.write('launch\\n')
print('session launch failed', file=sys.stderr)
raise SystemExit(3)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_repository_sf1_smoke_matrix_has_exact_required_sessions() -> None:
    from scripts.run_v7_benchmarks import configurations, load_matrix

    matrix = load_matrix(ROOT / "experiments" / "v7_resident_sf1_smoke.yml")

    assert matrix["dataset"]["expected_hash"] == "542abf4003633c7c"
    assert matrix["protocol"] == {"warmup": 1, "repeat": 2, "timeout_seconds": 1800}
    assert set(matrix["engines"]) == {
        "cpu-specialized",
        "gpu-copy",
        "gpu-managed",
        "gpu-mapped",
        "hybrid-arrow",
        "cudf",
    }
    assert matrix["engines"]["hybrid-arrow"]["cpu_ratio"] == 0.5
    assert [config.config_id for config in configurations(matrix)] == list(
        matrix["engines"]
    )


def test_explicit_configurations_expand_same_engine_sweep_with_unique_ids(
    tmp_path: Path,
) -> None:
    from scripts.run_v7_benchmarks import configurations, load_matrix

    matrix_path = _write_matrix(tmp_path)
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    matrix["configurations"] = [
        {
            "config_id": "cpu-specialized-t08",
            "engine": "cpu-specialized",
            "runner": "cpp",
            "threads": 8,
            "ratio_mode": "fixed",
            "cpu_ratio": 1.0,
            "correctness_backend": "cpu-specialized",
            "mode_options": {"sweep": "threads"},
        },
        {
            "config_id": "cpu-specialized-t16",
            "engine": "cpu-specialized",
            "runner": "cpp",
            "threads": 16,
            "ratio_mode": "fixed",
            "cpu_ratio": 1.0,
            "correctness_backend": "cpu-specialized",
            "mode_options": {"sweep": "threads"},
        },
        {
            "config_id": "hybrid-auto-t16",
            "engine": "hybrid-arrow",
            "runner": "cpp",
            "threads": 16,
            "ratio_mode": "auto",
            "correctness_backend": "hybrid-auto",
            "mode_options": {"selection": "auto"},
            "enabled": False,
            "disabled_reason": "hybrid-auto session CLI is pending",
        },
    ]
    del matrix["engines"]
    _write_json(matrix_path, matrix)

    configs = configurations(load_matrix(matrix_path))

    assert [(config.config_id, config.threads) for config in configs] == [
        ("cpu-specialized-t08", 8),
        ("cpu-specialized-t16", 16),
    ]
    assert all(config.engine == "cpu-specialized" for config in configs)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda configs: configs.append(dict(configs[0])),
            "duplicate config_id",
        ),
        (
            lambda configs: configs.append(
                {**configs[0], "config_id": "cpu-specialized-alias"}
            ),
            "duplicate configuration",
        ),
        (
            lambda configs: configs[0].update({"mystery": True}),
            "unknown fields",
        ),
    ],
)
def test_explicit_configurations_reject_duplicates_and_unknown_fields(
    tmp_path: Path, mutate: object, message: str
) -> None:
    from scripts.run_v7_benchmarks import load_matrix

    matrix_path = _write_matrix(tmp_path)
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    options = next(iter(matrix.pop("engines").values()))
    matrix["configurations"] = [
        {
            "config_id": "cpu-specialized-t01",
            "engine": "cpu-specialized",
            "runner": options["runner"],
            "threads": options["threads"],
            "ratio_mode": "fixed",
            "cpu_ratio": options["cpu_ratio"],
            "correctness_backend": options["correctness_backend"],
            "mode_options": {},
        }
    ]
    mutate(matrix["configurations"])  # type: ignore[operator]
    _write_json(matrix_path, matrix)

    with pytest.raises(ValueError, match=message):
        load_matrix(matrix_path)


def test_hybrid_auto_configuration_builds_enableable_session_command(
    tmp_path: Path,
) -> None:
    from scripts.run_v7_benchmarks import Configuration, build_command

    config = Configuration(
        "hybrid-arrow",
        "cpp",
        16,
        0.0,
        "hybrid-auto",
        {"selection": "auto"},
        config_id="hybrid-auto-t16",
        ratio_mode="auto",
    )

    command = build_command(
        config,
        session_cli=Path("memq5_arrow_session"),
        dataset=tmp_path,
        region="ASIA",
        date="1994-01-01",
        warmup=3,
        repeat=10,
        cudf_env="memq5-cudf",
    )

    assert command[-2:] == ["--hybrid-selection", "auto"]
    assert "--cpu-ratio" not in command


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("threads", 8, "cudf.*threads=1"),
        ("mode_options", {}, "cudf.*mode_options"),
        ("mode_options", {"framework": "cudf", "threads": 8}, "cudf.*mode_options"),
    ],
)
def test_cudf_configuration_rejects_unexecuted_threads_and_options(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    from scripts.run_v7_benchmarks import load_matrix

    matrix_path = _write_matrix(tmp_path)
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    matrix["engines"] = {
        "cudf": {
            "runner": "cudf",
            "threads": 1,
            "cpu_ratio": 0.0,
            "correctness_backend": "cudf",
            "mode_options": {"framework": "cudf"},
        }
    }
    matrix["engines"]["cudf"][field] = value
    _write_json(matrix_path, matrix)

    with pytest.raises(ValueError, match=message):
        load_matrix(matrix_path)


def test_build_commands_consume_current_cpp_and_cudf_session_clis(tmp_path: Path) -> None:
    from scripts.run_v7_benchmarks import Configuration, build_command

    cpp = Configuration("gpu-copy", "cpp", 1, 0.0, "gpu-copy", {})
    cudf = Configuration(
        "cudf", "cudf", 1, 0.0, "cudf", {"framework": "cudf"}
    )

    cpp_command = build_command(
        cpp,
        session_cli=Path("build-arrow-cuda-v3/memq5_arrow_session"),
        dataset=tmp_path,
        region="ASIA",
        date="1994-01-01",
        warmup=1,
        repeat=2,
        cudf_env="memq5-cudf",
    )
    cudf_command = build_command(
        cudf,
        session_cli=Path("build-arrow-cuda-v3/memq5_arrow_session"),
        dataset=tmp_path,
        region="ASIA",
        date="1994-01-01",
        warmup=1,
        repeat=2,
        cudf_env="memq5-cudf",
    )

    assert cpp_command[0] == "build-arrow-cuda-v3/memq5_arrow_session"
    assert cpp_command[cpp_command.index("--warmup") + 1] == "1"
    assert cpp_command[cpp_command.index("--repeat") + 1] == "2"
    assert cudf_command[:5] == ["conda", "run", "-n", "memq5-cudf", "python"]
    assert "baselines/cudf_q5_session.py" in cudf_command


def test_runner_monitors_one_process_and_writes_complete_bundle(tmp_path: Path) -> None:
    from scripts.run_v7_benchmarks import run_matrix

    matrix = _write_matrix(tmp_path)
    launches = tmp_path / "launches.txt"
    executable = tmp_path / "fake-session"
    _write_fake_session(executable, launches)
    output = tmp_path / "bundle"

    result = run_matrix(
        matrix_path=matrix,
        project_root=tmp_path,
        session_cli=executable,
        output_dir=output,
        cudf_env="memq5-cudf",
    )

    assert result.failures == 0
    assert launches.read_text(encoding="utf-8").splitlines() == ["launch"]
    setups = read_setups(output / "setup.csv")
    assert (output / "setups.csv").read_bytes() == (output / "setup.csv").read_bytes()
    warmups = read_requests(output / "warmups.csv")
    measured = read_requests(output / "raw.csv")
    assert len(setups) == 1
    assert setups[0].hybrid_provenance_status == "unavailable"
    assert setups[0].hybrid_model_version is None
    assert setups[0].predicted_cpu_ratio is None
    assert [row.sample_index for row in warmups] == [0]
    assert [row.sample_index for row in measured] == [0, 1]
    assert [row.request_index for row in [*warmups, *measured]] == [0, 1, 2]
    assert {row.session_id for row in [*warmups, *measured]} == {setups[0].session_id}
    assert {row.process_elapsed_ms for row in [*warmups, *measured]} == {
        setups[0].process_elapsed_ms
    }
    assert measured[0].rows == ROWS
    assert measured[0].overlap_wall_ms == 0.75
    assert measured[0].oracle_status == "expected_hash_match"
    assert measured[0].stdout_log == "logs/cpu-specialized.stdout.jsonl"
    assert (output / measured[0].stdout_log).is_file()
    assert (output / measured[0].stderr_log).is_file()
    assert (output / "commands.txt").read_text(encoding="utf-8").count("\n") == 1
    environment = json.loads((output / "environment.json").read_text(encoding="utf-8"))
    assert environment["v7_runner"]["matrix_payload"] == json.loads(
        matrix.read_text(encoding="utf-8")
    )
    assert environment["v7_runner"]["session_cli_sha256"] == _sha256(executable)
    assert environment["v7_runner"]["git_commit"]
    assert environment["v7_runner"]["cudf_env"] == "memq5-cudf"
    assert environment["v7_runner"]["gpu_index"] == 0
    assert environment["v7_runner"]["cuda_visible_devices"] == "0"


def test_runner_round_trips_hybrid_auto_setup_provenance_with_gpu_alias() -> None:
    from scripts.process_monitor import MonitoredProcessResult
    from scripts.resident_protocol import ResidentSession
    from scripts.run_v7_benchmarks import Configuration, _setup_from_session

    raw_setup = {
        "dataset_load_ms": 1.0,
        "session_setup_ms": 2.0,
        "plan_build_ms": 0.5,
        "host_staging_ms": 0.25,
        "allocation_ms": 0.0,
        "initial_h2d_ms": 0.0,
        "tune_ms": 1.5,
        "resident_host_bytes": 1024,
        "resident_gpu_bytes": 2048,
        "resident_pinned_bytes": 512,
        "selected_cpu_ratio": 0.5,
        "predicted_cpu_ratio": 0.45,
        "hybrid_model_version": "hybrid-cost-v1-batch-v1",
        "calibration_rows": 6,
        "cpu_calibration_requests": 1,
        "gpu_calibration_requests": 1,
        "cpu_calibration_ms": 0.6,
        "gpu_calibration_ms": 0.8,
        "gpu_kernel_calibration_ms": 0.5,
        "gpu_fixed_ms": 0.3,
        "cpu_rows_per_ms": 10.0,
        "gpu_rows_per_ms": 12.0,
        "realized_cpu_ratio": 0.5,
        "selected_batch_boundary_rows": 3,
    }
    session = ResidentSession(
        session_id=SESSION_ID,
        setup=raw_setup,
        warmups=(),
        measured=(),
        rows=ROWS,
        result_hash=RESULT_HASH,
        complete=True,
        missing_request_indexes=(),
    )
    config = Configuration(
        "hybrid-arrow",
        "cpp",
        8,
        0.0,
        "hybrid-auto",
        {"selection": "auto"},
        config_id="hybrid-auto-t08",
        ratio_mode="auto",
    )
    matrix = {
        "schema_version": 2,
        "experiment_id": "v7-resident-test",
        "dataset": {"path": "data/arrow", "scale_factor": "1"},
        "query": {"region": "ASIA", "date": "1994-01-01"},
    }
    monitored = MonitoredProcessResult(
        return_code=0,
        timed_out=False,
        elapsed_ms=3.0,
        peak_rss_bytes=4096,
        peak_rss_status="measured",
        peak_gpu_bytes=None,
        peak_gpu_status="unavailable",
        peak_gpu_source="",
        started_at_utc="2026-07-14T00:00:00Z",
        finished_at_utc="2026-07-14T00:00:01Z",
    )

    setup = _setup_from_session(
        matrix,
        config,
        session,
        monitored,
        "logs/hybrid-auto-t08.stdout.jsonl",
        "logs/hybrid-auto-t08.stderr.txt",
    )

    assert setup.hybrid_provenance_status == "measured"
    assert setup.gpu_kernel_rows_per_ms == 12.0
    assert setup.predicted_cpu_ratio == 0.45
    assert setup.realized_cpu_ratio == setup.selected_cpu_ratio == 0.5


def test_runner_retains_launch_failure_without_fabricating_request_slots(
    tmp_path: Path,
) -> None:
    from scripts.run_v7_benchmarks import run_matrix

    matrix = _write_matrix(tmp_path)
    launches = tmp_path / "launches.txt"
    executable = tmp_path / "failing-session"
    _write_failing_session(executable, launches)
    output = tmp_path / "bundle"

    result = run_matrix(
        matrix_path=matrix,
        project_root=tmp_path,
        session_cli=executable,
        output_dir=output,
        cudf_env="memq5-cudf",
    )

    assert result.failures == 1
    assert launches.read_text(encoding="utf-8").splitlines() == ["launch"]
    setups = read_setups(output / "setup.csv")
    warmups = read_requests(output / "warmups.csv")
    measured = read_requests(output / "raw.csv")
    assert setups[0].status == "error"
    assert setups[0].return_code == 3
    assert warmups == []
    assert measured == []
    assert "session launch failed" in (output / setups[0].stderr_log).read_text(
        encoding="utf-8"
    )


def test_runner_preserves_partial_nonzero_session_without_fabricating_missing_rows(
    tmp_path: Path,
) -> None:
    from scripts.run_v7_benchmarks import run_matrix

    matrix = _write_matrix(tmp_path)
    executable = tmp_path / "partial-session"
    _write_partial_failing_session(executable, tmp_path / "launches.txt")
    output = tmp_path / "bundle"

    result = run_matrix(
        matrix_path=matrix,
        project_root=tmp_path,
        session_cli=executable,
        output_dir=output,
        cudf_env="memq5-cudf",
    )

    setups = read_setups(output / "setups.csv")
    warmups = read_requests(output / "warmups.csv")
    measured = read_requests(output / "raw.csv")
    assert result.failures == 1
    assert setups[0].status == "error"
    assert setups[0].error_class == "ERROR_NOT_EXECUTED"
    assert [(row.request_index, row.status) for row in warmups] == [(0, "ok")]
    assert [(row.request_index, row.status) for row in measured] == [(1, "error")]
    assert warmups[0].overlap_wall_ms == 0.5


def test_matrix_rejects_correctness_backend_relabeling(tmp_path: Path) -> None:
    from scripts.run_v7_benchmarks import load_matrix

    matrix_path = _write_matrix(tmp_path)
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    matrix["engines"]["cpu-specialized"]["correctness_backend"] = "cudf"
    _write_json(matrix_path, matrix)

    with pytest.raises(ValueError, match="correctness_backend.*cpu-specialized"):
        load_matrix(matrix_path)


def test_runner_validates_independent_oracle_before_launch(tmp_path: Path) -> None:
    from scripts.run_v7_benchmarks import run_matrix

    matrix_path = _write_matrix(tmp_path)
    oracle = tmp_path / "oracle.json"
    _write_json(oracle, {"result_hash": RESULT_HASH, "rows": ROWS})
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    matrix["oracle"] = {
        "path": "oracle.json",
        "sha256": _sha256(oracle),
    }
    _write_json(matrix_path, matrix)
    oracle.write_text("tampered\n", encoding="utf-8")
    launches = tmp_path / "launches.txt"
    executable = tmp_path / "fake-session"
    _write_fake_session(executable, launches)

    with pytest.raises(ValueError, match="oracle SHA256"):
        run_matrix(
            matrix_path=matrix_path,
            project_root=tmp_path,
            session_cli=executable,
            output_dir=tmp_path / "bundle",
            cudf_env="memq5-cudf",
        )
    assert not launches.exists()


def _write_correctness_inputs(
    tmp_path: Path,
    benchmark_matrix: Path,
    *,
    dataset_path: str | None = None,
) -> tuple[Path, Path]:
    benchmark = json.loads(benchmark_matrix.read_text(encoding="utf-8"))
    matrix = tmp_path / "correctness.yml"
    _write_json(
        matrix,
        {
            "schema_version": 1,
            "experiment_id": "v7-sf10-correctness",
            "dataset": {
                "path": dataset_path or benchmark["dataset"]["path"],
                "scale_factor": benchmark["dataset"]["scale_factor"],
                "manifest_sha256": benchmark["dataset"]["manifest_sha256"],
            },
            "query": {"region": "ASIA", "date": "1994-01-01"},
            "required_backends": BASE_BACKENDS,
            "hybrid_auto": {"enabled": False},
        },
    )
    oracle = tmp_path / "oracle.json"
    _write_json(oracle, {"rows": ROWS, "result_hash": RESULT_HASH})
    return matrix, oracle


def test_materializer_emits_existing_correctness_schema_with_exact_rows(
    tmp_path: Path,
) -> None:
    from scripts.run_v7_benchmarks import materialize_correctness_records, run_matrix
    from scripts.v7_correctness_gate import verify_correctness

    benchmark_matrix = _write_matrix(
        tmp_path,
        scale_factor="10",
        dataset_path="data/tpch_sf10_arrow",
    )
    benchmark_payload = json.loads(benchmark_matrix.read_text(encoding="utf-8"))
    options = benchmark_payload.pop("engines")["cpu-specialized"]
    benchmark_payload["configurations"] = [
        {
            "config_id": "cpu-specialized-t01",
            "engine": "cpu-specialized",
            "runner": options["runner"],
            "threads": options["threads"],
            "ratio_mode": "fixed",
            "cpu_ratio": options["cpu_ratio"],
            "correctness_backend": options["correctness_backend"],
            "mode_options": {},
        }
    ]
    _write_json(benchmark_matrix, benchmark_payload)
    executable = tmp_path / "fake-session"
    _write_fake_session(executable, tmp_path / "launches.txt")
    bundle = tmp_path / "bundle"
    run_matrix(
        matrix_path=benchmark_matrix,
        project_root=tmp_path,
        session_cli=executable,
        output_dir=bundle,
        cudf_env="memq5-cudf",
    )
    correctness_matrix, oracle = _write_correctness_inputs(tmp_path, benchmark_matrix)
    runs = tmp_path / "runs"

    outputs = materialize_correctness_records(
        setup_csv=bundle / "setup.csv",
        warmups_csv=bundle / "warmups.csv",
        raw_csv=bundle / "raw.csv",
        correctness_matrix=correctness_matrix,
        output_dir=runs,
        sample_index=0,
    )

    assert outputs == [runs / "cpu-specialized.json"]
    record = json.loads(outputs[0].read_text(encoding="utf-8"))
    assert record["identity"] == {
        "experiment_id": "v7-sf10-correctness",
        "scale_factor": "10",
        "dataset_path": "data/tpch_sf10_arrow",
        "dataset_manifest_sha256": json.loads(
            benchmark_matrix.read_text(encoding="utf-8")
        )["dataset"]["manifest_sha256"],
        "region": "ASIA",
        "date": "1994-01-01",
    }
    assert record["process"]["session_id"] == SESSION_ID
    assert record["output"] == {"result_hash": RESULT_HASH, "rows": ROWS}
    report = verify_correctness(runs, correctness_matrix, oracle)
    assert report["backends"]["cpu-specialized"] == {
        "status": "passed",
        "errors": [],
    }


def test_materializer_rejects_failed_or_ambiguous_engine_sessions(tmp_path: Path) -> None:
    from scripts.run_v7_benchmarks import materialize_correctness_records, run_matrix

    benchmark_matrix = _write_matrix(tmp_path)
    executable = tmp_path / "failing-session"
    _write_failing_session(executable, tmp_path / "launches.txt")
    bundle = tmp_path / "bundle"
    run_matrix(
        matrix_path=benchmark_matrix,
        project_root=tmp_path,
        session_cli=executable,
        output_dir=bundle,
        cudf_env="memq5-cudf",
    )
    correctness_matrix, _ = _write_correctness_inputs(tmp_path, benchmark_matrix)

    with pytest.raises(ValueError, match="successful setup"):
        materialize_correctness_records(
            setup_csv=bundle / "setup.csv",
            warmups_csv=bundle / "warmups.csv",
            raw_csv=bundle / "raw.csv",
            correctness_matrix=correctness_matrix,
            output_dir=tmp_path / "runs",
            sample_index=0,
        )


def test_materializer_rejects_session_truncated_below_source_protocol(tmp_path: Path) -> None:
    from scripts.run_v7_benchmarks import materialize_correctness_records, run_matrix

    benchmark_matrix = _write_matrix(tmp_path)
    executable = tmp_path / "fake-session"
    _write_fake_session(executable, tmp_path / "launches.txt")
    bundle = tmp_path / "bundle"
    run_matrix(
        matrix_path=benchmark_matrix,
        project_root=tmp_path,
        session_cli=executable,
        output_dir=bundle,
        cudf_env="memq5-cudf",
    )
    raw_path = bundle / "raw.csv"
    with raw_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    with raw_path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle, lineterminator="\n").writerows(rows[:-1])
    correctness_matrix, _ = _write_correctness_inputs(tmp_path, benchmark_matrix)

    with pytest.raises(ValueError, match="source protocol.*repeat"):
        materialize_correctness_records(
            setup_csv=bundle / "setups.csv",
            warmups_csv=bundle / "warmups.csv",
            raw_csv=raw_path,
            correctness_matrix=correctness_matrix,
            output_dir=tmp_path / "runs",
        )


def test_materializer_rejects_source_dataset_identity_relabeling(tmp_path: Path) -> None:
    from scripts.run_v7_benchmarks import materialize_correctness_records, run_matrix

    benchmark_matrix = _write_matrix(tmp_path)
    executable = tmp_path / "fake-session"
    _write_fake_session(executable, tmp_path / "launches.txt")
    bundle = tmp_path / "bundle"
    run_matrix(
        matrix_path=benchmark_matrix,
        project_root=tmp_path,
        session_cli=executable,
        output_dir=bundle,
        cudf_env="memq5-cudf",
    )
    correctness_matrix, _ = _write_correctness_inputs(
        tmp_path,
        benchmark_matrix,
        dataset_path="data/tpch_sf10_arrow",
    )

    with pytest.raises(ValueError, match="source dataset identity"):
        materialize_correctness_records(
            setup_csv=bundle / "setup.csv",
            warmups_csv=bundle / "warmups.csv",
            raw_csv=bundle / "raw.csv",
            correctness_matrix=correctness_matrix,
            output_dir=tmp_path / "runs",
            sample_index=0,
        )


def test_materializer_rejects_tampered_embedded_source_matrix(tmp_path: Path) -> None:
    from scripts.run_v7_benchmarks import materialize_correctness_records, run_matrix

    benchmark_matrix = _write_matrix(tmp_path)
    executable = tmp_path / "fake-session"
    _write_fake_session(executable, tmp_path / "launches.txt")
    bundle = tmp_path / "bundle"
    run_matrix(
        matrix_path=benchmark_matrix,
        project_root=tmp_path,
        session_cli=executable,
        output_dir=bundle,
        cudf_env="memq5-cudf",
    )
    correctness_matrix, _ = _write_correctness_inputs(tmp_path, benchmark_matrix)
    benchmark_matrix.unlink()
    environment_path = bundle / "environment.json"
    environment = json.loads(environment_path.read_text(encoding="utf-8"))
    environment["v7_runner"]["matrix_payload"]["dataset"]["path"] = "data/other"
    _write_json(environment_path, environment)

    with pytest.raises(ValueError, match="embedded source matrix digest"):
        materialize_correctness_records(
            setup_csv=bundle / "setup.csv",
            warmups_csv=bundle / "warmups.csv",
            raw_csv=bundle / "raw.csv",
            correctness_matrix=correctness_matrix,
            output_dir=tmp_path / "runs",
            sample_index=0,
        )
