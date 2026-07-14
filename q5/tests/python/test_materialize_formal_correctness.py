from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from scripts.materialize_formal_correctness import materialize_correctness
from test_formal_evidence_bundle import _make_bundle


def test_materializer_recomputes_formal_request_gate(tmp_path: Path) -> None:
    root, matrix, _, oracle, _ = _make_bundle(tmp_path)
    output = tmp_path / "formal-correctness.json"

    report = materialize_correctness(root, matrix, oracle, output)

    assert report == {
        "ok": True,
        "expected_hash": "248d10b6ee352953",
        "observed_hashes": ["248d10b6ee352953"],
        "backends": {"gpu-copy": {"status": "passed", "errors": []}},
    }
    assert json.loads(output.read_text(encoding="utf-8")) == report


def test_materializer_rejects_incomplete_formal_coverage(tmp_path: Path) -> None:
    root, matrix, _, oracle, _ = _make_bundle(tmp_path)
    raw_path = root / "raw.csv"
    with raw_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
        fieldnames = list(rows[0])
    with raw_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows[:-1])

    with pytest.raises(ValueError, match="measured samples.*expected=10 actual=9"):
        materialize_correctness(
            root, matrix, oracle, tmp_path / "formal-correctness.json"
        )
