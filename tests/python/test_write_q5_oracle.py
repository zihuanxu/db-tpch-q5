from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASELINES_DIR = ROOT / "baselines"
SCRIPTS_DIR = ROOT / "scripts"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "tpch_q5_tiny"

for path in (BASELINES_DIR, SCRIPTS_DIR):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)


def test_write_oracle_records_tiny_direct_scan_provenance_and_exact_rows(tmp_path: Path) -> None:
    from write_q5_oracle import write_oracle

    pytest.importorskip("duckdb")
    output = tmp_path / "oracle.json"

    report = write_oracle(FIXTURE_DIR, output, "ASIA", "1994-01-01")
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload == report
    assert payload["query_version"] == "tpch-q5-duckdb-decimal-v1"
    assert payload["sql_sha256"] == hashlib.sha256(payload["sql"].encode("utf-8")).hexdigest()
    assert payload["source_table_hashes"] == {
        name: hashlib.sha256((FIXTURE_DIR / f"{name}.tbl").read_bytes()).hexdigest()
        for name in ("region", "nation", "supplier", "customer", "orders", "lineitem")
    }
    assert payload["rows"] == [
        {"nation": "JAPAN", "revenue_1e4": 1900000},
        {"nation": "INDIA", "revenue_1e4": 900000},
    ]
    assert payload["result_hash"] == "248d10b6ee352953"
    assert isinstance(payload["duckdb_version"], str)
    assert payload["region"] == "ASIA"
    assert payload["date"] == "1994-01-01"
    assert not list(tmp_path.glob(".oracle.json.*.tmp"))


def test_write_oracle_preserves_existing_output_when_source_is_missing(tmp_path: Path) -> None:
    from write_q5_oracle import write_oracle

    pytest.importorskip("duckdb")
    output = tmp_path / "oracle.json"
    output.write_text('{"existing": true}\n', encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="region.tbl"):
        write_oracle(tmp_path / "missing", output, "ASIA", "1994-01-01")

    assert output.read_text(encoding="utf-8") == '{"existing": true}\n'
