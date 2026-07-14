from __future__ import annotations

from pathlib import Path

import pytest

from scripts.import_paper_evidence import import_evidence, import_v7_evidence


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


def test_v7_import_generates_two_scale_resident_macros(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "generated"

    import_v7_evidence(
        root / "docs/artifacts/v7_sf1_resident",
        root / "docs/artifacts/v7_sf10_resident",
        root / "docs/artifacts/v7_hybrid_model/memq5-v7-hybrid-model.json",
        root / "docs/research/CLAIM_LEDGER.md",
        output,
        root,
    )

    text = (output / "results.tex").read_text(encoding="utf-8")
    assert "% sf1_experiment_id=v7-formal-sf1-resident" in text
    assert "% sf10_experiment_id=v7-formal-sf10-resident" in text
    assert "% sf1_manifest_sha256=81c48484b397" in text
    assert "% sf10_manifest_sha256=2a9e07aaaeb8" in text
    assert r"\newcommand{\VSevenSfOneMeasuredRuns}{180}" in text
    assert r"\newcommand{\VSevenSfTenResultHash}{b1351a421ba8dcfd}" in text
    assert r"\newcommand{\SfOneGpuCopyMedian}{1.267}" in text
    assert r"\newcommand{\SfTenHybridBestRatio}{0.375}" in text
    assert r"\newcommand{\SfOneHybridAutoRegretPercent}{33.89}" in text
    assert str(root) not in text


def test_v7_import_rejects_model_identity_mismatch(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    model = tmp_path / "model.json"
    text = (
        root / "docs/artifacts/v7_hybrid_model/memq5-v7-hybrid-model.json"
    ).read_text(encoding="utf-8")
    model.write_text(text.replace("021becd1", "ffffffff", 1), encoding="utf-8")

    with pytest.raises(ValueError, match="model identity"):
        import_v7_evidence(
            root / "docs/artifacts/v7_sf1_resident",
            root / "docs/artifacts/v7_sf10_resident",
            model,
            root / "docs/research/CLAIM_LEDGER.md",
            tmp_path / "generated",
            root,
        )
