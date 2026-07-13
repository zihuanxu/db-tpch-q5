from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
BASELINES_DIR = ROOT / "baselines"

for path in (SCRIPTS_DIR, BASELINES_DIR):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from arrow_dataset import load_arrow_dataset
from prepare_arrow_dataset import prepare_dataset
from tpch_arrow_schema import q5_schemas


FIXTURE_DIR = ROOT / "tests" / "fixtures" / "tpch_q5_tiny"
TABLE_NAMES = ["region", "nation", "supplier", "customer", "orders", "lineitem"]


def _write_bytes(path: Path, data: bytes) -> None:
    path.write_bytes(data)


def _write_table(path: Path, table: pa.Table) -> None:
    with pa.OSFile(str(path), "wb") as sink:
        with ipc.new_file(sink, table.schema) as writer:
            writer.write_table(table)


def _prepare_tiny_dataset(tmp_path: Path, replace: bool = False) -> tuple[Path, dict[str, object]]:
    output_dir = tmp_path / "dataset"
    metadata = prepare_dataset(
        input_dir=FIXTURE_DIR,
        output_dir=output_dir,
        scale_factor="tiny",
        batch_rows=2,
        source_command="fixture",
        replace=replace,
    )
    return output_dir, metadata


def _manifest(path: Path) -> dict[str, object]:
    return json.loads((path / "manifest.json").read_text(encoding="utf-8"))


def _table_file(dataset_dir: Path, table_name: str) -> Path:
    entry = _manifest(dataset_dir)["tables"][table_name]
    return dataset_dir / entry["file"]


def test_q5_schemas_match_contract() -> None:
    schemas = q5_schemas()

    assert list(schemas) == TABLE_NAMES
    assert schemas["region"] == pa.schema(
        [
            pa.field("r_regionkey", pa.int32(), nullable=False),
            pa.field("r_name", pa.dictionary(pa.int32(), pa.string()), nullable=False),
        ]
    )
    assert schemas["nation"] == pa.schema(
        [
            pa.field("n_nationkey", pa.int32(), nullable=False),
            pa.field("n_name", pa.dictionary(pa.int32(), pa.string()), nullable=False),
            pa.field("n_regionkey", pa.int32(), nullable=False),
        ]
    )
    assert schemas["supplier"] == pa.schema(
        [
            pa.field("s_suppkey", pa.int32(), nullable=False),
            pa.field("s_nationkey", pa.int32(), nullable=False),
        ]
    )
    assert schemas["customer"] == pa.schema(
        [
            pa.field("c_custkey", pa.int32(), nullable=False),
            pa.field("c_nationkey", pa.int32(), nullable=False),
        ]
    )
    assert schemas["orders"] == pa.schema(
        [
            pa.field("o_orderkey", pa.int32(), nullable=False),
            pa.field("o_custkey", pa.int32(), nullable=False),
            pa.field("o_orderdate", pa.date32(), nullable=False),
        ]
    )
    assert schemas["lineitem"] == pa.schema(
        [
            pa.field("l_orderkey", pa.int32(), nullable=False),
            pa.field("l_suppkey", pa.int32(), nullable=False),
            pa.field("l_extendedprice", pa.decimal128(15, 2), nullable=False),
            pa.field("l_discount", pa.decimal128(15, 2), nullable=False),
        ]
    )


def test_prepare_dataset_writes_batched_ipc_files_and_dictionary_columns(tmp_path: Path) -> None:
    dataset_dir, metadata = _prepare_tiny_dataset(tmp_path)

    assert metadata["scale_factor"] == "tiny"
    tables = load_arrow_dataset(dataset_dir)
    assert tables["lineitem"].num_rows == 6

    lineitem_file = ipc.open_file(_table_file(dataset_dir, "lineitem"))
    assert lineitem_file.num_record_batches >= 3
    assert pa.types.is_dictionary(tables["region"].schema.field("r_name").type)
    assert pa.types.is_dictionary(tables["nation"].schema.field("n_name").type)


def test_manifest_contains_all_tables_sorted_keys_and_sha256(tmp_path: Path) -> None:
    dataset_dir, _ = _prepare_tiny_dataset(tmp_path)
    manifest_path = dataset_dir / "manifest.json"
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)

    assert list(manifest) == sorted(manifest)
    assert manifest["format_version"] == 1
    assert manifest["batch_rows"] == 2
    assert manifest["source_command"] == "fixture"
    assert manifest["source_kind"] == "command"
    assert set(manifest["tables"]) == set(TABLE_NAMES)
    for table_name in TABLE_NAMES:
        entry = manifest["tables"][table_name]
        assert entry["rows"] >= 1
        assert entry["record_batches"] >= 1
        assert len(entry["sha256"]) == 64
        int(entry["sha256"], 16)
        assert entry["schema"]
        path = dataset_dir / entry["file"]
        assert path.exists()
        assert entry["bytes"] == path.stat().st_size


def test_loader_rejects_tampered_bytes_missing_table_row_count_schema_and_nulls(tmp_path: Path) -> None:
    dataset_dir, _ = _prepare_tiny_dataset(tmp_path)

    tampered_dir = tmp_path / "tampered"
    shutil.copytree(dataset_dir, tampered_dir)
    lineitem_path = _table_file(tampered_dir, "lineitem")
    data = bytearray(lineitem_path.read_bytes())
    data[-1] ^= 0x01
    _write_bytes(lineitem_path, bytes(data))
    with pytest.raises(ValueError, match="SHA-256.*lineitem"):
        load_arrow_dataset(tampered_dir)

    missing_dir = tmp_path / "missing"
    shutil.copytree(dataset_dir, missing_dir)
    _table_file(missing_dir, "orders").unlink()
    with pytest.raises(ValueError, match="missing table.*orders"):
        load_arrow_dataset(missing_dir)

    row_count_dir = tmp_path / "row-count"
    shutil.copytree(dataset_dir, row_count_dir)
    manifest = _manifest(row_count_dir)
    manifest["tables"]["supplier"]["rows"] += 1
    (row_count_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    with pytest.raises(ValueError, match="row count.*supplier"):
        load_arrow_dataset(row_count_dir, verify_checksums=False)

    schema_dir = tmp_path / "schema"
    shutil.copytree(dataset_dir, schema_dir)
    supplier_path = _table_file(schema_dir, "supplier")
    supplier_table = load_arrow_dataset(schema_dir)["supplier"]
    wrong_schema = pa.schema(
        [
            pa.field("s_suppkey", pa.int64(), nullable=False),
            pa.field("s_nationkey", pa.int32(), nullable=False),
        ]
    )
    _write_table(
        supplier_path,
        pa.Table.from_arrays(
            [supplier_table["s_suppkey"], supplier_table["s_nationkey"]],
            schema=wrong_schema,
        ),
    )
    manifest = _manifest(schema_dir)
    manifest["tables"]["supplier"]["sha256"] = hashlib.sha256(supplier_path.read_bytes()).hexdigest()
    manifest["tables"]["supplier"]["bytes"] = supplier_path.stat().st_size
    manifest["tables"]["supplier"]["schema"] = str(wrong_schema)
    (schema_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    with pytest.raises(ValueError, match="schema.*supplier"):
        load_arrow_dataset(schema_dir, verify_checksums=False)

    null_dir = tmp_path / "nulls"
    shutil.copytree(dataset_dir, null_dir)
    customer_path = _table_file(null_dir, "customer")
    customer_table = load_arrow_dataset(null_dir)["customer"]
    null_table = pa.Table.from_arrays(
        [
            pa.array([10, None, 30, 40], type=pa.int32()),
            customer_table["c_nationkey"],
        ],
        schema=customer_table.schema,
    )
    _write_table(customer_path, null_table)
    manifest = _manifest(null_dir)
    manifest["tables"]["customer"]["sha256"] = hashlib.sha256(customer_path.read_bytes()).hexdigest()
    manifest["tables"]["customer"]["bytes"] = customer_path.stat().st_size
    (null_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    with pytest.raises(ValueError, match="null.*customer.*c_custkey"):
        load_arrow_dataset(null_dir, verify_checksums=False)


def test_prepare_dataset_rejects_decimal_scale_overflow_with_context(tmp_path: Path) -> None:
    input_dir = tmp_path / "bad-input"
    shutil.copytree(FIXTURE_DIR, input_dir)
    lineitem_path = input_dir / "lineitem.tbl"
    original_lines = lineitem_path.read_text(encoding="utf-8").splitlines()
    original_lines[0] = original_lines[0].replace("|100.00|0.10|", "|12.345|0.10|")
    lineitem_path.write_text("\n".join(original_lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"lineitem.*l_extendedprice.*scale 2.*12\.345"):
        prepare_dataset(
            input_dir=input_dir,
            output_dir=tmp_path / "bad-output",
            scale_factor="tiny",
            batch_rows=2,
            source_command="fixture",
        )


def test_prepare_dataset_is_non_destructive_without_replace(tmp_path: Path) -> None:
    output_dir, _ = _prepare_tiny_dataset(tmp_path)

    with pytest.raises(FileExistsError):
        prepare_dataset(
            input_dir=FIXTURE_DIR,
            output_dir=output_dir,
            scale_factor="tiny",
            batch_rows=2,
            source_command="fixture",
        )


def test_cli_creates_dataset_compatible_with_loader(tmp_path: Path) -> None:
    output_dir = tmp_path / "cli-dataset"
    command = [
        sys.executable,
        str(SCRIPTS_DIR / "prepare_arrow_dataset.py"),
        "--input",
        str(FIXTURE_DIR),
        "--output",
        str(output_dir),
        "--scale-factor",
        "tiny",
        "--batch-rows",
        "2",
        "--source-command",
        "fixture",
    ]
    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)

    dataset = load_arrow_dataset(output_dir)
    assert dataset["lineitem"].num_rows == 6
