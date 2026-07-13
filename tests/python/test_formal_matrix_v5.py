from __future__ import annotations

from pathlib import Path

from scripts.formal_matrix import expected_configuration_keys, load_matrix


ROOT = Path(__file__).resolve().parents[2]


def test_committed_sf1_matrix_has_exact_protocol_and_19_configurations() -> None:
    matrix = load_matrix(ROOT / "experiments" / "v5_formal_sf1.yml")
    assert matrix["protocol"] == {
        "warmup": 3,
        "repeat": 10,
        "timeout_seconds": 1800,
    }
    keys = expected_configuration_keys(matrix)
    assert len(keys) == 19
    assert ("cpu-specialized", "cold", 32, 1.0, 0.0) in keys
    assert ("gpu-copy", "cold", 1, 0.0, 1.0) in keys
    assert ("hybrid-arrow", "cold", 8, 0.25, 0.75) in keys
    assert ("cudf", "cold", 1, 0.0, 1.0) in keys
