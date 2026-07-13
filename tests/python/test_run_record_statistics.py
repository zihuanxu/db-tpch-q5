from __future__ import annotations

import math

from scripts.summarize_run_records import summarize_values


def test_statistics_use_linear_p95_and_sample_stddev() -> None:
    summary = summarize_values([1.0, 2.0, 3.0, 4.0, 100.0])
    assert summary["min"] == 1.0
    assert summary["max"] == 100.0
    assert summary["median"] == 3.0
    assert math.isclose(summary["p95"], 80.8)
    assert math.isclose(summary["stddev"], 43.617656975128774)


def test_single_value_has_zero_sample_stddev() -> None:
    assert summarize_values([5.0])["stddev"] == 0.0
