from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASELINES = ROOT / "baselines"
if str(BASELINES) not in sys.path:
    sys.path.insert(0, str(BASELINES))

from common import ResultRow
from cudf_q5 import run_q5


def test_cudf_consumes_all_canonical_arrow_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    cudf = pytest.importorskip("cudf")
    schemas: list[object] = []

    def recording_from_arrow(table):
        schemas.append(table.schema)
        return original(table)

    if hasattr(cudf, "from_arrow"):
        original = cudf.from_arrow
        monkeypatch.setattr(cudf, "from_arrow", recording_from_arrow)
    else:
        original = cudf.DataFrame.from_arrow
        monkeypatch.setattr(
            cudf.DataFrame, "from_arrow", classmethod(lambda _cls, table: recording_from_arrow(table))
        )
    rows = run_q5(
        ROOT / "tests" / "fixtures" / "tpch_q5_tiny_arrow",
        "ASIA",
        "1994-01-01",
    )

    assert rows == [ResultRow("JAPAN", 1900000), ResultRow("INDIA", 900000)]
    assert len(schemas) == 6
