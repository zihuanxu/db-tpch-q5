from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cpu_ci_workflow_and_script_cover_arrow_path() -> None:
    workflow = (ROOT / ".github/workflows/cpu-ci.yml").read_text(encoding="utf-8")
    script = (ROOT / "scripts/ci_cpu.sh").read_text(encoding="utf-8")
    for phrase in (
        "actions/checkout@",
        "conda-incubator/setup-miniconda@",
        "environment-arrow-cpu.yml",
        "bash scripts/ci_cpu.sh",
        "actions/upload-artifact@",
    ):
        assert phrase in workflow
    for phrase in (
        "set -euo pipefail",
        "MEMQ5_ENABLE_ARROW=ON",
        "MEMQ5_ENABLE_CUDA=OFF",
        "ctest",
        "pytest",
        "prepare_arrow_dataset.py",
        "verify_q5_oracle.py",
        "OPENSSL_ROOT_DIR",
        "CONDA_PREFIX",
    ):
        assert phrase in script
    assert "nvidia" not in workflow.lower()
