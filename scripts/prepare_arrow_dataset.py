#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import mkdtemp
from typing import Callable

import pyarrow as pa
import pyarrow.ipc as ipc

from tpch_arrow_schema import q5_schemas, schema_string


Parser = Callable[[str], object]

TABLE_SPECS: dict[str, dict[str, object]] = {
    "region": {
        "file_name": "region.arrow",
        "fields": [("r_regionkey", 0, int), ("r_name", 1, str)],
    },
    "nation": {
        "file_name": "nation.arrow",
        "fields": [("n_nationkey", 0, int), ("n_name", 1, str), ("n_regionkey", 2, int)],
    },
    "supplier": {
        "file_name": "supplier.arrow",
        "fields": [("s_suppkey", 0, int), ("s_nationkey", 3, int)],
    },
    "customer": {
        "file_name": "customer.arrow",
        "fields": [("c_custkey", 0, int), ("c_nationkey", 3, int)],
    },
    "orders": {
        "file_name": "orders.arrow",
        "fields": [("o_orderkey", 0, int), ("o_custkey", 1, int), ("o_orderdate", 4, "date32")],
    },
    "lineitem": {
        "file_name": "lineitem.arrow",
        "fields": [
            ("l_orderkey", 0, int),
            ("l_suppkey", 2, int),
            ("l_extendedprice", 5, ("decimal128", 15, 2)),
            ("l_discount", 6, ("decimal128", 15, 2)),
        ],
    },
}


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _parse_decimal(value: str, precision: int, scale: int, table_name: str, column_name: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{table_name}.{column_name} invalid decimal value {value!r}") from exc

    exponent = parsed.as_tuple().exponent
    fractional_digits = -exponent if exponent < 0 else 0
    if fractional_digits > scale:
        raise ValueError(
            f"{table_name}.{column_name} requires scale {scale}, got {value}"
        )

    normalized = parsed.quantize(Decimal(1).scaleb(-scale))
    digits = normalized.as_tuple().digits
    if len(digits) > precision:
        raise ValueError(
            f"{table_name}.{column_name} exceeds precision {precision}, got {value}"
        )
    return normalized


def _parser_for(table_name: str, field_name: str, spec: object) -> Parser:
    if spec is int:
        return int
    if spec == "date32":
        return _parse_date
    if isinstance(spec, tuple) and spec[0] == "decimal128":
        _, precision, scale = spec
        return lambda value: _parse_decimal(value, precision, scale, table_name, field_name)
    return str


def _build_dictionary_array(values: list[str], field: pa.Field, dictionary_values: list[str]) -> pa.Array:
    index_by_value = {value: index for index, value in enumerate(dictionary_values)}
    indices = pa.array([index_by_value[value] for value in values], type=pa.int32())
    dictionary = pa.array(dictionary_values, type=pa.string())
    return pa.DictionaryArray.from_arrays(indices, dictionary, ordered=False)


def _build_batch(
    schema: pa.Schema,
    rows: list[dict[str, object]],
    dictionary_values_by_field: dict[str, list[str]] | None = None,
) -> pa.RecordBatch:
    arrays = []
    for field in schema:
        values = [row[field.name] for row in rows]
        if pa.types.is_dictionary(field.type):
            dictionary_values = dictionary_values_by_field[field.name]
            arrays.append(_build_dictionary_array(values, field, dictionary_values))
        else:
            arrays.append(pa.array(values, type=field.type))
    return pa.RecordBatch.from_arrays(arrays, schema=schema)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_tbl_rows(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.rstrip("\n")
            if not line:
                continue
            parts = line.split("|")
            if parts and parts[-1] == "":
                parts.pop()
            yield parts


def _convert_table(input_dir: Path, temp_dir: Path, table_name: str, batch_rows: int) -> dict[str, object]:
    if batch_rows <= 0:
        raise ValueError("batch_rows must be positive")

    schema = q5_schemas()[table_name]
    spec = TABLE_SPECS[table_name]
    source_path = input_dir / f"{table_name}.tbl"
    if not source_path.exists():
        raise FileNotFoundError(f"missing source table: {source_path}")

    file_name = str(spec["file_name"])
    output_path = temp_dir / file_name
    dictionary_fields = [field.name for field in schema if pa.types.is_dictionary(field.type)]
    row_count = 0
    batch_count = 0

    if dictionary_fields:
        all_rows: list[dict[str, object]] = []
        for parts in _iter_tbl_rows(source_path):
            row: dict[str, object] = {}
            for field_name, index, parser_spec in spec["fields"]:
                parser = _parser_for(table_name, field_name, parser_spec)
                row[field_name] = parser(parts[index])
            all_rows.append(row)

        dictionary_values_by_field: dict[str, list[str]] = {}
        for field_name in dictionary_fields:
            dictionary_values_by_field[field_name] = sorted({str(row[field_name]) for row in all_rows})

        row_count = len(all_rows)
        with pa.OSFile(str(output_path), "wb") as sink:
            with ipc.new_file(sink, schema) as writer:
                for start in range(0, row_count, batch_rows):
                    batch_rows_slice = all_rows[start : start + batch_rows]
                    writer.write_batch(
                        _build_batch(
                            schema,
                            batch_rows_slice,
                            dictionary_values_by_field=dictionary_values_by_field,
                        )
                    )
                    batch_count += 1
    else:
        rows_buffer: list[dict[str, object]] = []
        with pa.OSFile(str(output_path), "wb") as sink:
            with ipc.new_file(sink, schema) as writer:
                for parts in _iter_tbl_rows(source_path):
                    row: dict[str, object] = {}
                    for field_name, index, parser_spec in spec["fields"]:
                        parser = _parser_for(table_name, field_name, parser_spec)
                        row[field_name] = parser(parts[index])
                    rows_buffer.append(row)
                    if len(rows_buffer) == batch_rows:
                        writer.write_batch(_build_batch(schema, rows_buffer))
                        row_count += len(rows_buffer)
                        batch_count += 1
                        rows_buffer = []

                if rows_buffer:
                    writer.write_batch(_build_batch(schema, rows_buffer))
                    row_count += len(rows_buffer)
                    batch_count += 1

    return {
        "file": file_name,
        "rows": row_count,
        "record_batches": batch_count,
        "schema": schema_string(schema),
        "bytes": output_path.stat().st_size,
        "sha256": _sha256(output_path),
    }


def prepare_dataset(
    input_dir: Path,
    output_dir: Path,
    scale_factor: str,
    batch_rows: int,
    source_command: str,
    replace: bool = False,
) -> dict[str, object]:
    input_root = Path(input_dir)
    output_root = Path(output_dir)
    if output_root.exists():
        if not replace:
            raise FileExistsError(f"output already exists: {output_root}")
        shutil.rmtree(output_root)

    parent = output_root.parent
    parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(mkdtemp(prefix=f".{output_root.name}.tmp-", dir=parent))
    manifest: dict[str, object] = {
        "batch_rows": batch_rows,
        "format_version": 1,
        "scale_factor": scale_factor,
        "source_command": source_command,
        "source_kind": "command",
        "tables": {},
    }

    try:
        for table_name in q5_schemas():
            manifest["tables"][table_name] = _convert_table(
                input_dir=input_root,
                temp_dir=temp_root,
                table_name=table_name,
                batch_rows=batch_rows,
            )

        manifest_path = temp_root / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_root.replace(output_root)
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare canonical Arrow IPC data for TPC-H Q5.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--scale-factor", required=True)
    parser.add_argument("--batch-rows", required=True, type=int)
    parser.add_argument("--source-command", required=True)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    prepare_dataset(
        input_dir=args.input,
        output_dir=args.output,
        scale_factor=args.scale_factor,
        batch_rows=args.batch_rows,
        source_command=args.source_command,
        replace=args.replace,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
