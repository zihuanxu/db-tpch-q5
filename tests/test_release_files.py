from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_open_source_metadata_exists() -> None:
    required = [
        "LICENSE",
        "CITATION.cff",
        "CONTRIBUTING.md",
        "CHANGELOG.md",
        "Dockerfile",
        ".dockerignore",
        "CMakePresets.json",
        "environment-gpu.yml",
    ]
    assert not [name for name in required if not (ROOT / name).is_file()]
    assert "Apache License" in (ROOT / "LICENSE").read_text(encoding="utf-8")
    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    for field in ("cff-version:", "title:", "authors:", "version:", "repository-code:"):
        assert field in citation
    json.loads((ROOT / "CMakePresets.json").read_text(encoding="utf-8"))


def test_no_official_or_large_generated_data_is_tracked() -> None:
    completed = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, text=True, capture_output=True
    )
    if completed.returncode == 0:
        tracked = completed.stdout.splitlines()
    else:
        tracked = [path.relative_to(ROOT).as_posix() for path in ROOT.rglob("*") if path.is_file()]
    bad_tbl = [path for path in tracked if path.endswith(".tbl") and not path.startswith("tests/fixtures/")]
    bad_arrow = [
        path
        for path in tracked
        if path.endswith((".arrow", ".ipc")) and not path.startswith("tests/fixtures/")
    ]
    assert not bad_tbl
    assert not bad_arrow


def test_readme_has_public_reproduction_and_release_entries() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for phrase in (
        "Apache-2.0",
        "CITATION.cff",
        "scripts/ci_cpu.sh",
        "scripts/release_audit.py",
        "docs/artifacts/v5_sf1",
    ):
        assert phrase in text
