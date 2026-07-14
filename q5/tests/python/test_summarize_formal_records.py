from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from scripts.formal_benchmark_schema import (
    REQUEST_EXECUTION_MEASUREMENT_FIELDS,
    validate_request,
    validate_setup,
)
from test_formal_benchmark_schema import valid_request, valid_setup


ROOT = Path(__file__).resolve().parents[3]


def _measured_records() -> list:
    return [
        validate_request(
            valid_request(
                sample_index=index,
                request_index=index + 3,
                query_total_ms=float(index + 1),
            )
        )
        for index in range(10)
    ]


def test_summary_reports_population_statistics_setup_residency_and_amortization() -> None:
    from scripts.summarize_formal_records import summarize_records

    setup = validate_setup(valid_setup(tune_ms=0.5))

    rows = summarize_records(_measured_records(), [setup])

    assert len(rows) == 1
    row = rows[0]
    assert row["scale_factor"] == "1"
    assert row["config_id"] == "gpu-copy"
    assert row["engine"] == "gpu-copy"
    assert row["threads"] == 1
    assert row["ratio_mode"] == "fixed"
    assert row["cpu_ratio"] == 0.0
    assert row["lifecycle"] == "resident"
    assert row["query_total_ms_median"] == 5.5
    assert row["query_total_ms_mean"] == 5.5
    assert row["query_total_ms_min"] == 1.0
    assert row["query_total_ms_max"] == 10.0
    assert math.isclose(row["query_total_ms_pstdev"], math.sqrt(8.25))
    assert row["query_total_ms_p25"] == 3.25
    assert row["query_total_ms_p75"] == 7.75
    assert row["query_total_ms_p95"] == pytest.approx(9.55)
    assert row["dataset_load_ms"] == 1.0
    assert row["session_setup_ms"] == 2.0
    assert row["tune_ms"] == 0.5
    assert row["setup_cost_ms"] == 3.0
    assert row["resident_host_bytes"] == 1024
    assert row["resident_gpu_bytes"] == 2048
    assert row["resident_pinned_bytes"] == 512
    assert row["resident_total_bytes"] == 3584
    assert row["amortized_1_request_ms"] == 8.5
    assert row["amortized_10_requests_ms"] == 5.8
    assert row["amortized_100_requests_ms"] == 5.53


def test_summary_rejects_profiler_rows_instead_of_polluting_latency() -> None:
    from scripts.summarize_formal_records import summarize_records

    profiled = validate_request(
        valid_request(mode_options_json='{"profiler":"nsys"}')
    )

    with pytest.raises(ValueError, match="profiler rows"):
        summarize_records([profiled], [validate_setup(valid_setup())])


def test_summary_requires_exactly_one_matching_setup_per_configuration() -> None:
    from scripts.summarize_formal_records import summarize_records

    records = _measured_records()
    setup = validate_setup(valid_setup())

    with pytest.raises(ValueError, match="one setup"):
        summarize_records(records, [])
    with pytest.raises(ValueError, match="one setup"):
        summarize_records(records, [setup, setup])


def test_summary_ignores_configuration_with_only_failed_samples() -> None:
    from scripts.summarize_formal_records import summarize_records

    statuses = json.loads(valid_request()["measurement_status_json"])
    statuses.update(
        {name: "unavailable" for name in REQUEST_EXECUTION_MEASUREMENT_FIELDS}
    )
    failed = validate_request(
        valid_request(
            status="error",
            error_class="ERROR_REQUEST_FAILED",
            result_rows=0,
            result_hash="",
            rows_json="[]",
            oracle_status="not_run",
            **{name: None for name in REQUEST_EXECUTION_MEASUREMENT_FIELDS},
            measurement_status_json=json.dumps(
                statuses, separators=(",", ":"), sort_keys=True
            ),
        )
    )

    assert summarize_records([failed], [validate_setup(valid_setup())]) == []


def test_committed_formal_matrices_have_complete_unique_sweeps() -> None:
    from scripts.run_formal_benchmarks import configurations, load_matrix

    for scale in ("1", "10"):
        matrix = load_matrix(ROOT / "q5" / "experiments" / f"formal_sf{scale}.json")
        enabled = configurations(matrix)
        declared = configurations(matrix, include_disabled=True)

        assert matrix["dataset"]["scale_factor"] == scale
        assert matrix["oracle"]["path"]
        assert len(matrix["oracle"]["sha256"]) == 64
        assert matrix["protocol"] == {
            "warmup": 3,
            "repeat": 10,
            "timeout_seconds": 1800,
        }
        assert len(enabled) == 18
        assert len(declared) == 18
        assert len({config.config_id for config in declared}) == 18
        for engine in ("cpu-specialized", "arrow-acero"):
            assert {
                config.threads for config in enabled if config.engine == engine
            } == {8, 16, 32}
        assert {
            config.engine
            for config in enabled
            if config.engine in {"gpu-copy", "gpu-managed", "gpu-mapped", "cudf"}
        } == {"gpu-copy", "gpu-managed", "gpu-mapped", "cudf"}

        fixed_hybrid = [
            config
            for config in enabled
            if config.engine == "hybrid-arrow" and config.ratio_mode == "fixed"
        ]
        assert {config.cpu_ratio for config in fixed_hybrid} == {
            0.125,
            0.25,
            0.375,
            0.5,
            0.625,
            0.75,
            0.875,
        }
        assert {
            config.cpu_ratio
            for config in fixed_hybrid
            if config.mode_options["sweep"] == "core"
        } == {0.25, 0.5, 0.75}
        auto = [config for config in enabled if config.ratio_mode == "auto"]
        assert len(auto) == 1
        assert auto[0].correctness_backend == "hybrid-auto"


def test_committed_formal_oracles_recompute_expected_hash_from_ordered_rows() -> None:
    from scripts.run_formal_benchmarks import load_matrix
    from scripts.verify_q5_oracle import result_hash_hex

    for scale in ("1", "10"):
        matrix = load_matrix(ROOT / "q5" / "experiments" / f"formal_sf{scale}.json")
        oracle_path = ROOT / matrix["oracle"]["path"]
        oracle = __import__("json").loads(oracle_path.read_text(encoding="utf-8"))
        rows = oracle["rows"]
        exact = [(row["nation"], row["revenue_1e4"]) for row in rows]

        assert len(rows) == len({row["nation"] for row in rows})
        assert result_hash_hex(exact) == oracle["result_hash"]
        assert oracle["result_hash"] == matrix["dataset"]["expected_hash"]
