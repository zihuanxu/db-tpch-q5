from __future__ import annotations

import hashlib
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
        "overlap_ms": 0.0,
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
    from scripts.run_v7_benchmarks import load_matrix

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


def test_build_commands_consume_current_cpp_and_cudf_session_clis(tmp_path: Path) -> None:
    from scripts.run_v7_benchmarks import Configuration, build_command

    cpp = Configuration("gpu-copy", "cpp", 1, 0.0, "gpu-copy", {})
    cudf = Configuration("cudf", "cudf", 1, 0.0, "cudf", {})

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
    warmups = read_requests(output / "warmups.csv")
    measured = read_requests(output / "raw.csv")
    assert len(setups) == 1
    assert [row.sample_index for row in warmups] == [0]
    assert [row.sample_index for row in measured] == [0, 1]
    assert [row.request_index for row in [*warmups, *measured]] == [0, 1, 2]
    assert {row.session_id for row in [*warmups, *measured]} == {setups[0].session_id}
    assert {row.process_elapsed_ms for row in [*warmups, *measured]} == {
        setups[0].process_elapsed_ms
    }
    assert measured[0].rows == ROWS
    assert measured[0].stdout_log == "logs/cpu-specialized.stdout.jsonl"
    assert (output / measured[0].stdout_log).is_file()
    assert (output / measured[0].stderr_log).is_file()
    assert (output / "commands.txt").read_text(encoding="utf-8").count("\n") == 1
    environment = json.loads((output / "environment.json").read_text(encoding="utf-8"))
    assert environment["v7_runner"]["matrix_payload"] == json.loads(
        matrix.read_text(encoding="utf-8")
    )


def test_runner_retains_launch_failure_in_setup_and_every_request_slot(
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
    assert [row.status for row in [*warmups, *measured]] == ["error"] * 3
    assert [row.request_index for row in [*warmups, *measured]] == [0, 1, 2]
    assert "session launch failed" in (output / setups[0].stderr_log).read_text(
        encoding="utf-8"
    )


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
