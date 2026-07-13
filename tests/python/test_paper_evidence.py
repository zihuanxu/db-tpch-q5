from __future__ import annotations

from pathlib import Path

import pytest

from scripts.import_paper_evidence import import_evidence


def test_import_generates_provenance_and_verified_values(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "generated"

    import_evidence(
        root / "docs/artifacts/v5_sf1",
        root / "docs/research/CLAIM_LEDGER.md",
        output,
        root,
    )

    text = (output / "results.tex").read_text(encoding="utf-8")
    assert "% experiment_id=v5-sf1-final" in text
    assert "% manifest_sha256=e3337842" in text
    assert r"\newcommand{\FormalMeasuredRuns}{190}" in text
    assert r"\newcommand{\CpuBestQueryMedian}{61.414}" in text
    assert r"\newcommand{\HybridBestQueryMedian}{222.832}" in text
    assert str(root) not in text


def test_import_rejects_wrong_claim_state(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    ledger = tmp_path / "ledger.md"
    source = (root / "docs/research/CLAIM_LEDGER.md").read_text(encoding="utf-8")
    ledger.write_text(source.replace("| C007 | RQ5/H4 |", "| C107 | RQ5/H4 |"), encoding="utf-8")

    with pytest.raises(ValueError, match="C007"):
        import_evidence(root / "docs/artifacts/v5_sf1", ledger, tmp_path / "out", root)
