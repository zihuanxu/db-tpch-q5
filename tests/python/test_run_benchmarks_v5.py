from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from scripts.run_benchmarks import (
    Configuration,
    build_command,
    expand_configurations,
    parse_v5_benchmark,
)


def args() -> argparse.Namespace:
    return argparse.Namespace(
        arrow_cli=Path("build/memq5_arrow_query"),
        arrow_dataset=Path("data/tpch_sf1_arrow"),
        region="ASIA",
        date="1994-01-01",
        cudf_env="memq5-cudf",
    )


def test_arrow_cpp_engines_use_canonical_dataset() -> None:
    command = build_command(args(), Configuration("cpu-specialized", 4, 1.0))
    assert command[0] == "build/memq5_arrow_query"
    assert "--dataset" in command
    assert "data/tpch_sf1_arrow" in command
    assert "--data-dir" not in command


def test_hybrid_command_carries_ratio() -> None:
    command = build_command(args(), Configuration("hybrid-arrow", 8, 0.25))
    assert command[command.index("--cpu-ratio") + 1] == "0.25"


def test_cudf_uses_named_rapids_environment() -> None:
    command = build_command(args(), Configuration("cudf", 1, 0.0))
    assert command[:5] == ["conda", "run", "-n", "memq5-cudf", "python"]
    assert "baselines/cudf_q5.py" in command


def test_matrix_does_not_apply_cpu_thread_sweep_to_gpu() -> None:
    configs = expand_configurations(
        ["cpu-specialized", "gpu-copy", "hybrid-arrow", "cudf"],
        [1, 2, 4],
        [0.25, 0.5, 0.75],
        hybrid_threads=4,
    )
    assert [c.threads for c in configs if c.engine == "cpu-specialized"] == [1, 2, 4]
    assert [c.threads for c in configs if c.engine == "gpu-copy"] == [1]
    assert [c.cpu_ratio for c in configs if c.engine == "hybrid-arrow"] == [0.25, 0.5, 0.75]
    assert [c.threads for c in configs if c.engine == "cudf"] == [1]


@pytest.mark.parametrize(
    ("threads", "ratios"),
    [([0], [0.5]), ([1], [-0.1]), ([1], [1.1])],
)
def test_matrix_rejects_invalid_dimensions(
    threads: list[int], ratios: list[float]
) -> None:
    with pytest.raises(ValueError):
        expand_configurations(
            ["cpu-specialized", "hybrid-arrow"],
            threads,
            ratios,
            hybrid_threads=4,
        )


def test_strict_parser_rejects_misattributed_engine() -> None:
    stdout = (
        "engine,region,date,threads,result_rows,result_hash,build_ms,h2d_ms,"
        "kernel_ms,d2h_ms,scan_ms,total_ms,cpu_ms,gpu_ms,overlap_ms,"
        "input_lineitem_rows,matched_lineitem_rows,cpu_input_rows,gpu_input_rows,"
        "h2d_bytes,d2h_bytes,mapped_remote_read_bytes\n"
        "gpu-copy,ASIA,1994-01-01,4,2,248d10b6ee352953,1,0,0,0,2,3,0,0,0,"
        "6,2,6,0,0,0,0\n"
    )
    with pytest.raises(ValueError, match="engine"):
        parse_v5_benchmark(stdout, Configuration("cpu-specialized", 4, 1.0), args())


def test_strict_parser_rejects_missing_counter_fields() -> None:
    stdout = (
        "engine,region,date,threads,result_rows,result_hash,build_ms,h2d_ms,"
        "kernel_ms,d2h_ms,scan_ms,total_ms\n"
        "cpu-specialized,ASIA,1994-01-01,4,2,248d10b6ee352953,1,0,0,0,2,3\n"
    )
    with pytest.raises(ValueError, match="missing benchmark fields"):
        parse_v5_benchmark(stdout, Configuration("cpu-specialized", 4, 1.0), args())
