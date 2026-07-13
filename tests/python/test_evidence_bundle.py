from __future__ import annotations

from pathlib import Path

from scripts.evidence_bundle import (
    _check_summary,
    artifact_checksums,
    audit_checksums,
    check_coverage,
)

from scripts.benchmark_schema import validate_record
from scripts.summarize_run_records import summarize_records, write_summary
from test_benchmark_schema import valid_record


def test_checksums_detect_artifact_tampering(tmp_path: Path) -> None:
    artifact = tmp_path / "raw.csv"
    artifact.write_text("before\n", encoding="utf-8")
    checksums = artifact_checksums(tmp_path)
    assert audit_checksums(tmp_path, checksums) == []
    artifact.write_text("after\n", encoding="utf-8")
    assert audit_checksums(tmp_path, checksums) == ["raw.csv"]


def test_manifest_is_excluded_from_its_own_checksum(tmp_path: Path) -> None:
    (tmp_path / "raw.csv").write_text("x\n", encoding="utf-8")
    (tmp_path / "manifest.json").write_text("{}\n", encoding="utf-8")
    assert set(artifact_checksums(tmp_path)) == {"raw.csv"}


def test_formal_coverage_rejects_partial_matrix() -> None:
    measured = [validate_record(valid_record(sample_index=0))]
    warmups = [validate_record(valid_record(sample_index=-1, is_warmup=True))]
    errors = check_coverage(
        measured,
        warmups,
        warmup=3,
        repeat=10,
        expected_configurations=1,
    )
    assert "measured samples" in " ".join(errors)
    assert "warmup samples" in " ".join(errors)


def test_summary_is_recomputed_from_raw_records(tmp_path: Path) -> None:
    measured = [validate_record(valid_record())]
    write_summary(tmp_path / "summary.csv", summarize_records(measured))
    assert _check_summary(tmp_path, measured) == []

    text = (tmp_path / "summary.csv").read_text(encoding="utf-8")
    (tmp_path / "summary.csv").write_text(
        text.replace("3.0,3.0,3.0", "3.0,999.0,3.0", 1), encoding="utf-8"
    )
    assert "does not match" in " ".join(_check_summary(tmp_path, measured))
