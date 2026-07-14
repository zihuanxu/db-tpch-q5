from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASELINES = ROOT / "baselines"
if str(BASELINES) not in sys.path:
    sys.path.insert(0, str(BASELINES))

from common import ResultRow
from cudf_q5 import run_benchmark, run_q5
import cudf_q5_session
from cudf_q5_session import CudfQ5Session


def _require_cudf_device():
    cudf = pytest.importorskip("cudf")
    from numba import cuda

    if not cuda.is_available():
        pytest.skip("CUDA device is unavailable")
    return cudf


def test_cudf_consumes_all_canonical_arrow_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    cudf = _require_cudf_device()
    schemas: list[object] = []

    def recording_from_arrow(table):
        schemas.append(table.schema)
        return original(table)

    original = cudf.DataFrame.from_arrow
    monkeypatch.setattr(cudf.DataFrame, "from_arrow", staticmethod(recording_from_arrow))
    rows = run_q5(
        ROOT / "tests" / "fixtures" / "tpch_q5_tiny_arrow",
        "ASIA",
        "1994-01-01",
    )

    assert rows == [ResultRow("JAPAN", 1900000), ResultRow("INDIA", 900000)]
    assert len(schemas) == 6


def test_cudf_resident_session_converts_once_and_emits_stable_request_hashes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cudf = _require_cudf_device()
    conversions = 0
    original = cudf.DataFrame.from_arrow

    def recording_from_arrow(table):
        nonlocal conversions
        conversions += 1
        return original(table)

    monkeypatch.setattr(cudf.DataFrame, "from_arrow", staticmethod(recording_from_arrow))
    exit_code = cudf_q5_session.main(
        [
            "--dataset",
            str(ROOT / "tests" / "fixtures" / "tpch_q5_tiny_arrow"),
            "--warmup",
            "1",
            "--repeat",
            "2",
        ]
    )

    assert exit_code == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert conversions == 6
    assert records[0]["record_type"] == "session_setup"
    assert records[0]["engine"] == "cudf"
    assert records[0]["dataset_load_ms"] > 0
    assert records[0]["session_setup_ms"] > 0
    assert records[0]["resident_gpu_bytes"] > 0
    assert [record["record_type"] for record in records[1:]] == ["request"] * 3
    assert [record["request_index"] for record in records[1:]] == [0, 1, 2]
    assert [record["is_warmup"] for record in records[1:]] == [True, False, False]
    assert len({record["result_hash"] for record in records[1:]}) == 1


def test_cudf_resident_session_execute_reports_only_request_timing() -> None:
    _require_cudf_device()
    session = CudfQ5Session(
        ROOT / "tests" / "fixtures" / "tpch_q5_tiny_arrow",
        "ASIA",
        "1994-01-01",
    )

    result = session.execute()

    assert session.dataset_load_ms > 0
    assert session.resident_gpu_bytes > 0
    assert result.load_ms == 0
    assert result.query_ms > 0
    assert result.rows == [ResultRow("JAPAN", 1900000), ResultRow("INDIA", 900000)]


def test_cudf_resident_session_returns_77_without_a_cuda_device(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cudf_q5_session, "_cuda_is_available", lambda: False)

    exit_code = cudf_q5_session.main(
        [
            "--dataset",
            str(ROOT / "tests" / "fixtures" / "tpch_q5_tiny_arrow"),
            "--warmup",
            "0",
            "--repeat",
            "1",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 77
    assert captured.out == ""
    assert "CUDA device is unavailable" in captured.err


@pytest.mark.parametrize(
    ("option", "value"),
    [("--warmup", "-1"), ("--repeat", "0")],
)
def test_cudf_resident_session_rejects_invalid_request_counts(
    option: str,
    value: str,
) -> None:
    with pytest.raises(SystemExit):
        cudf_q5_session.main(
            [
                "--dataset",
                str(ROOT / "tests" / "fixtures" / "tpch_q5_tiny_arrow"),
                option,
                value,
            ]
        )


def test_cudf_benchmark_separates_load_and_query_and_counts_rows() -> None:
    _require_cudf_device()
    result = run_benchmark(
        ROOT / "tests" / "fixtures" / "tpch_q5_tiny_arrow",
        "ASIA",
        "1994-01-01",
    )
    assert result.input_lineitem_rows == 6
    assert result.matched_lineitem_rows == 2
    assert result.load_ms > 0
    assert result.query_ms > 0
    assert result.rows == [ResultRow("JAPAN", 1900000), ResultRow("INDIA", 900000)]
