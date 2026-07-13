from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from tpch_arrow_schema import q5_schemas, schema_string


TABLE_NAMES = tuple(q5_schemas().keys())


def _read_manifest(path: Path) -> dict[str, object]:
    manifest_path = path / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"missing manifest: {manifest_path}")
    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    if manifest.get("format_version") != 1:
        raise ValueError("unsupported manifest format_version")
    tables = manifest.get("tables")
    if not isinstance(tables, dict):
        raise ValueError("manifest tables must be a mapping")
    missing = [name for name in TABLE_NAMES if name not in tables]
    if missing:
        raise ValueError(f"missing table metadata: {', '.join(missing)}")
    return manifest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_required_columns_non_null(table_name: str, table: pa.Table) -> None:
    for field in table.schema:
        if field.nullable:
            continue
        column = table.column(field.name)
        if column.null_count:
            raise ValueError(f"null value in required column {table_name}.{field.name}")


def load_arrow_dataset(path: Path, verify_checksums: bool = True) -> dict[str, pa.Table]:
    root = Path(path)
    manifest = _read_manifest(root)
    schemas = q5_schemas()
    tables: dict[str, pa.Table] = {}

    for table_name in TABLE_NAMES:
        entry = manifest["tables"][table_name]
        file_name = entry.get("file")
        if not isinstance(file_name, str):
            raise ValueError(f"invalid file entry for {table_name}")
        table_path = root / file_name
        if not table_path.exists():
            raise ValueError(f"missing table file for {table_name}: {table_path}")
        if verify_checksums:
            expected_hash = entry.get("sha256")
            actual_hash = _sha256(table_path)
            if expected_hash != actual_hash:
                raise ValueError(f"SHA-256 mismatch for {table_name}")

        with ipc.open_file(table_path) as reader:
            table = reader.read_all()
            record_batches = reader.num_record_batches

        expected_schema = schemas[table_name]
        expected_schema_text = schema_string(expected_schema)
        if table.schema != expected_schema:
            raise ValueError(f"schema mismatch for {table_name}")
        if entry.get("schema") != expected_schema_text:
            raise ValueError(f"schema mismatch for {table_name}")
        if table.num_rows != entry.get("rows"):
            raise ValueError(f"row count mismatch for {table_name}")
        if record_batches != entry.get("record_batches"):
            raise ValueError(f"record batch mismatch for {table_name}")
        if table_path.stat().st_size != entry.get("bytes"):
            raise ValueError(f"byte size mismatch for {table_name}")

        _ensure_required_columns_non_null(table_name, table)
        tables[table_name] = table

    return tables
