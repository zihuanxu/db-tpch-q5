from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.validate_claim_ledger import validate_ledger


HEADER = """# Claims

| ID | RQ/H | Statement | State | Code | Test | Evidence | Paper Location | Limitation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
"""


def _write(path: Path, row: str) -> Path:
    path.write_text(HEADER + row + "\n", encoding="utf-8")
    return path


def test_verified_claim_requires_evidence(tmp_path: Path) -> None:
    (tmp_path / "code.cpp").write_text("", encoding="utf-8")
    (tmp_path / "test.cpp").write_text("", encoding="utf-8")
    ledger = _write(
        tmp_path / "ledger.md",
        "| C001 | RQ1 | claim | VERIFIED | `code.cpp` | `test.cpp` | - | - | scope |",
    )

    report = validate_ledger(ledger, tmp_path)

    assert not report.ok
    assert any("VERIFIED requires evidence" in error for error in report.errors)


def test_planned_claim_cannot_be_a_final_paper_result(tmp_path: Path) -> None:
    (tmp_path / "paper.tex").write_text("", encoding="utf-8")
    ledger = _write(
        tmp_path / "ledger.md",
        "| C001 | H1 | hypothesis | PLANNED | - | - | - | `paper.tex` | scope |",
    )

    report = validate_ledger(ledger, tmp_path)

    assert not report.ok
    assert any("PLANNED cannot have a paper location" in error for error in report.errors)


def test_verified_evidence_manifest_digest_is_checked(tmp_path: Path) -> None:
    (tmp_path / "code.cpp").write_text("", encoding="utf-8")
    (tmp_path / "test.cpp").write_text("", encoding="utf-8")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    manifest = evidence / "manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")
    digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
    (evidence / "manifest.sha256").write_text(
        f"{digest}  manifest.json\n", encoding="ascii"
    )
    ledger = _write(
        tmp_path / "ledger.md",
        "| C001 | RQ1 | claim | VERIFIED | `code.cpp` | `test.cpp` | `evidence` | - | scope |",
    )
    assert validate_ledger(ledger, tmp_path).ok

    manifest.write_text('{"tampered": true}\n', encoding="utf-8")
    report = validate_ledger(ledger, tmp_path)

    assert not report.ok
    assert any("manifest digest mismatch" in error for error in report.errors)


def test_repository_claim_ledger_is_valid() -> None:
    root = Path(__file__).resolve().parents[2]
    report = validate_ledger(root / "docs/research/CLAIM_LEDGER.md", root)
    assert report.ok, "\n".join(report.errors)
    assert len(report.claims) == 21
