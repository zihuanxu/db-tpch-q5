from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "results"
EXPECTED_HASHES = {"sf1": "542abf4003633c7c", "sf10": "b1351a421ba8dcfd"}
RESIDENT_FILES = {
    "raw.csv",
    "setup.csv",
    "summary.csv",
    "correctness.json",
    "matrix.yml",
    "oracle.json",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_resident_results_are_complete_and_correct() -> None:
    for scale, expected_hash in EXPECTED_HASHES.items():
        directory = RESULTS / scale
        assert {path.name for path in directory.iterdir()} == RESIDENT_FILES
        raw = read_csv(directory / "raw.csv")
        setups = read_csv(directory / "setup.csv")
        summary = read_csv(directory / "summary.csv")
        assert len(raw) == 180
        assert len(setups) == 18
        assert len(summary) == 18
        assert {row["status"] for row in raw} == {"ok"}
        assert {row["result_hash"] for row in raw} == {expected_hash}
        assert {row["result_hash"] for row in summary} == {expected_hash}


def test_resident_result_matrices_reference_retained_oracles() -> None:
    for scale in EXPECTED_HASHES:
        matrix = json.loads((RESULTS / scale / "matrix.yml").read_text(encoding="utf-8"))
        oracle = matrix["oracle"]["path"]
        assert oracle == f"q5/experiments/oracles/formal_{scale}_q5.json"
        assert (ROOT / oracle).is_file()


def test_model_and_profiler_summaries_are_compact() -> None:
    environment = json.loads((RESULTS / "environment.json").read_text(encoding="utf-8"))
    assert environment == {
        "cpu": "2 x AMD EPYC 9654 96-Core Processor",
        "logical_cpus": 384,
        "gpu": "NVIDIA GeForce RTX 4090",
        "gpu_memory_mib": 24564,
        "cuda_compiler": "12.6",
        "nvidia_driver": "595.71.05",
        "cudf": "26.06.00",
        "os": "Linux x86_64",
    }
    assert (RESULTS / "hybrid_model.csv").is_file()
    assert (RESULTS / "hybrid_model.json").is_file()
    profiles = read_csv(RESULTS / "profiler.csv")
    assert len(profiles) == 10
    assert {(row["scale_factor"], row["engine"]) for row in profiles} == {
        (scale, engine)
        for scale in ("1", "10")
        for engine in ("copy", "managed", "mapped", "hybrid-fixed", "hybrid-auto")
    }
    assert not list(RESULTS.rglob("*.log"))
    assert not list(RESULTS.rglob("*.nsys-rep"))
