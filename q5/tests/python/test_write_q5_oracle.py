from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BASELINES_DIR = ROOT / "q5" / "baselines"
SCRIPTS_DIR = ROOT / "scripts"
FIXTURE_DIR = ROOT / "q5" / "tests" / "fixtures" / "tpch_q5_tiny"

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


def test_source_hashing_reads_all_tables_in_fixed_size_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from duckdb_q5 import TABLE_SPECS
    from write_q5_oracle import _source_table_hashes

    chunk_reads: list[tuple[str, int]] = []
    original_open = Path.open

    class RecordingReader:
        def __init__(self, path: Path, handle) -> None:
            self.path = path
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            self.handle.close()

        def read(self, size: int = -1) -> bytes:
            chunk_reads.append((self.path.name, size))
            return self.handle.read(size)

    def tracked_open(path: Path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        if path.parent == FIXTURE_DIR and path.suffix == ".tbl":
            return RecordingReader(path, handle)
        return handle

    def fail_on_full_buffer_read(self: Path) -> bytes:
        raise AssertionError(f"full-buffer read attempted for {self}")

    monkeypatch.setattr(Path, "open", tracked_open)
    monkeypatch.setattr(Path, "read_bytes", fail_on_full_buffer_read)

    _source_table_hashes(FIXTURE_DIR)

    assert {name for name, _ in chunk_reads} == {
        f"{name}.tbl" for name in TABLE_SPECS
    }
    assert chunk_reads
    assert all(size == 1024 * 1024 for _, size in chunk_reads)
