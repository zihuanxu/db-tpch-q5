from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PUBLIC_ROOT = {
    ".gitignore",
    "CMakeLists.txt",
    "README.md",
    "hashjoin-cpu",
    "q5",
    "results",
    "scripts",
}


def tracked_files() -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, text=True, capture_output=True
    )
    return completed.stdout.splitlines()


def test_public_root_is_compact() -> None:
    top_level = {path.split("/", 1)[0] for path in tracked_files()}
    assert top_level == PUBLIC_ROOT


def test_required_course_material_is_present() -> None:
    required = {
        "hashjoin-cpu/src/main.c",
        "q5/src/cuda/q5_arrow_cuda.cu",
        "q5/baselines/cudf_q5.py",
        "q5/experiments/formal_sf1.json",
        "q5/experiments/formal_sf10.json",
        "q5/tests/fixtures/tpch_q5_tiny/lineitem.tbl",
        "scripts/generate_synthetic_tpch_q5.py",
        "scripts/prepare_arrow_dataset.py",
        "scripts/run_formal_benchmarks.py",
        "results/sf1/summary.csv",
        "results/sf10/summary.csv",
    }
    assert not [path for path in required if not (ROOT / path).is_file()]


def test_submission_has_no_report_or_runtime_artifacts() -> None:
    tracked = tracked_files()
    forbidden_suffixes = (".docx", ".pdf", ".log", ".nsys-rep", ".pyc")
    assert not [path for path in tracked if path.endswith(forbidden_suffixes)]
    assert not [path for path in tracked if path.startswith((".github/", "hashjoin-cpu/docs/"))]


def test_readme_is_the_only_entry_document() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for phrase in (
        "TPC-H Q5",
        "hashjoin-cpu/",
        "q5/",
        "results/",
        "scripts/",
        "gpu-copy",
        "gpu-managed",
        "gpu-mapped",
        "cuDF",
        "hybrid",
        "542abf4003633c7c",
        "b1351a421ba8dcfd",
        "已知不足",
    ):
        assert phrase in text
