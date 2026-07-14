from __future__ import annotations

from pathlib import Path

from scripts.check_paper import (
    check_generated_results,
    check_paper,
    check_paper_provenance,
    check_source_text,
    write_paper_provenance,
)


def test_stale_or_pending_paper_text_is_rejected() -> None:
    errors = check_source_text("result pending; old hash 9f1f5f7578dd816e")
    assert any("pending" in error for error in errors)
    assert any("stale result hash" in error for error in errors)


def test_repository_paper_is_evidence_linked() -> None:
    root = Path(__file__).resolve().parents[2]
    errors = check_paper(root)
    assert not errors, "\n".join(errors)


def test_paper_provenance_detects_source_changed_after_pdf_build(tmp_path: Path) -> None:
    paper_dir = tmp_path / "docs/paper/generated"
    paper_dir.mkdir(parents=True)
    (tmp_path / "docs/paper/paper.tex").write_text("source\n", encoding="utf-8")
    (paper_dir / "results.tex").write_text("results\n", encoding="utf-8")
    (tmp_path / "docs/paper/paper.pdf").write_bytes(b"%PDF demo")
    write_paper_provenance(tmp_path)
    assert check_paper_provenance(tmp_path) == []

    (tmp_path / "docs/paper/paper.tex").write_text("changed\n", encoding="utf-8")
    assert any("paper.tex" in error for error in check_paper_provenance(tmp_path))


def test_generated_results_must_match_full_evidence_regeneration(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "docs/paper/generated/results.tex").read_text(encoding="utf-8")
    tampered = tmp_path / "results.tex"
    tampered.write_text(
        source.replace(
            r"\newcommand{\SfTenGpuManagedMedian}{15.066}",
            r"\newcommand{\SfTenGpuManagedMedian}{0.001}",
        ),
        encoding="utf-8",
    )
    assert tampered.read_text(encoding="utf-8") != source

    errors = check_generated_results(root, tampered)

    assert any("does not match audited evidence" in error for error in errors)
