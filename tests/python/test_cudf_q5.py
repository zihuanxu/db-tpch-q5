from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASELINES = ROOT / "baselines"
if str(BASELINES) not in sys.path:
    sys.path.insert(0, str(BASELINES))

from common import ResultRow
import cudf_q5
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


@pytest.mark.parametrize("invalid_date", ["1994-02-30", "19940101", "9999-12-31"])
def test_cudf_resident_session_rejects_invalid_date_before_device_or_load(
    invalid_date: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        cudf_q5_session,
        "_cuda_is_available",
        lambda: calls.append("device_check") or False,
    )
    monkeypatch.setattr(
        cudf_q5_session,
        "CudfQ5Session",
        lambda *_args: calls.append("dataset_load"),
    )

    with pytest.raises(SystemExit) as raised:
        cudf_q5_session.main(
            [
                "--dataset",
                str(ROOT / "tests" / "fixtures" / "tpch_q5_tiny_arrow"),
                "--date",
                invalid_date,
            ]
        )

    captured = capsys.readouterr()
    assert raised.value.code != 0
    assert captured.out == ""
    assert calls == []


def test_cudf_session_validates_date_before_loading_dataset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setitem(sys.modules, "cudf", SimpleNamespace())
    monkeypatch.setattr(
        cudf_q5_session,
        "_load_cudf_tables",
        lambda *_args, **_kwargs: calls.append("dataset_load") or {},
    )

    with pytest.raises(ValueError, match="one-year"):
        CudfQ5Session(Path("unused"), "ASIA", "9999-12-31")

    assert calls == []


def test_cudf_q5_prepares_order_dates_and_fixed_bounds_once() -> None:
    conversions: list[object] = []

    class FakeOrders:
        def __init__(self) -> None:
            self.orderdate: object = "raw-order-dates"

        def __getitem__(self, name: str) -> object:
            assert name == "o_orderdate"
            return self.orderdate

        def __setitem__(self, name: str, value: object) -> None:
            assert name == "o_orderdate"
            self.orderdate = value

    def fake_to_datetime(value: object) -> object:
        conversions.append(value)
        return f"datetime({value})"

    orders = FakeOrders()
    tables = {"orders": orders}
    prepared = cudf_q5._prepare_cudf_q5(
        tables,
        SimpleNamespace(to_datetime=fake_to_datetime),
        "1992-02-29",
        "1993-02-28",
    )

    assert prepared.tables is tables
    assert prepared.start == "datetime(1992-02-29)"
    assert prepared.end == "datetime(1993-02-28)"
    assert orders.orderdate == "datetime(raw-order-dates)"
    assert conversions == ["raw-order-dates", "1992-02-29", "1993-02-28"]


def test_cudf_session_synchronizes_date_preparation_before_setup_timer_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    clock = iter([10.0, 10.1, 10.4])

    def perf_counter() -> float:
        events.append("timer")
        return next(clock)

    def load_tables(_dataset, _cudf, on_arrow_tables_prepared):
        on_arrow_tables_prepared()
        events.append("arrow_converted")
        return {"orders": object(), "lineitem": [1]}

    def prepare_tables(tables, _cudf, start: str, end: str):
        assert (start, end) == ("1994-01-01", "1995-01-01")
        events.append("date_prepared")
        return SimpleNamespace(tables=tables)

    monkeypatch.setitem(sys.modules, "cudf", SimpleNamespace())
    monkeypatch.setattr(cudf_q5_session.time, "perf_counter", perf_counter)
    monkeypatch.setattr(cudf_q5_session, "_load_cudf_tables", load_tables)
    monkeypatch.setattr(
        cudf_q5_session, "_prepare_cudf_q5", prepare_tables, raising=False
    )
    monkeypatch.setattr(
        cudf_q5_session,
        "_synchronize_cuda_device",
        lambda: events.append("synchronized"),
        raising=False,
    )
    monkeypatch.setattr(cudf_q5_session, "_frame_memory_usage_bytes", lambda _frame: 1)

    session = CudfQ5Session(Path("unused"), "ASIA", "1994-01-01")

    assert events[-3:] == ["date_prepared", "synchronized", "timer"]
    assert session.session_setup_ms == pytest.approx(300.0)


def test_cudf_request_synchronizes_after_materialization_before_timer_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    clock = iter([20.0, 20.25])
    prepared = SimpleNamespace(tables={"lineitem": [1, 2, 3]})
    session = object.__new__(CudfQ5Session)
    session._prepared = prepared
    session.region = "ASIA"
    session.resident_gpu_bytes = 123

    def perf_counter() -> float:
        events.append("timer")
        return next(clock)

    def execute_q5(actual_prepared, region: str):
        assert actual_prepared is prepared
        assert region == "ASIA"
        events.append("materialized")
        return [ResultRow("JAPAN", 1900000)], 1

    monkeypatch.setattr(cudf_q5_session.time, "perf_counter", perf_counter)
    monkeypatch.setattr(cudf_q5_session, "_execute_q5", execute_q5)
    monkeypatch.setattr(
        cudf_q5_session,
        "_synchronize_cuda_device",
        lambda: events.append("synchronized"),
        raising=False,
    )

    result = session.execute()

    assert events == ["timer", "materialized", "synchronized", "timer"]
    assert result.load_ms == 0
    assert result.query_ms == pytest.approx(250.0)
    assert result.input_lineitem_rows == 3


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
