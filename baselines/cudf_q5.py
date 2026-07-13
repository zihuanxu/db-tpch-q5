#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc

from arrow_dataset import load_arrow_dataset
from common import ResultRow, emit_benchmark, emit_json, emit_rows


def add_year(value: str) -> str:
    parsed = date.fromisoformat(value)
    try:
        return parsed.replace(year=parsed.year + 1).isoformat()
    except ValueError:
        return parsed.replace(year=parsed.year + 1, day=28).isoformat()


def _replace_column(table: pa.Table, column_name: str, column: pa.Array | pa.ChunkedArray) -> pa.Table:
    index = table.schema.get_field_index(column_name)
    return table.set_column(index, column_name, column)


def _decode_name_column(table: pa.Table, column_name: str) -> pa.Table:
    return _replace_column(table, column_name, pc.cast(table[column_name], pa.string()))


def _decimal_column_to_scaled_int(
    column: pa.Array | pa.ChunkedArray,
    multiplier: int,
    integer_type: pa.DataType = pa.int64(),
) -> pa.Array | pa.ChunkedArray:
    decimal_scalar = pa.scalar(Decimal(str(multiplier)), type=pa.decimal128(len(str(multiplier)), 0))
    multiplied = pc.multiply(column, decimal_scalar)
    precision = max(18, getattr(column.type, "precision", 18) + len(str(multiplier)))
    scaled = pc.cast(multiplied, pa.decimal128(precision, 0))
    return pc.cast(scaled, integer_type)


def _load_cudf_tables(dataset_path: Path, cudf):
    tables = load_arrow_dataset(dataset_path)
    tables["region"] = _decode_name_column(tables["region"], "r_name")
    tables["nation"] = _decode_name_column(tables["nation"], "n_name")

    lineitem = tables["lineitem"].select(["l_orderkey", "l_suppkey"])
    # Derive exact integer columns in Arrow first so cuDF does not depend on Decimal128 behavior.
    lineitem = lineitem.append_column(
        "l_extendedprice_cents",
        _decimal_column_to_scaled_int(tables["lineitem"]["l_extendedprice"], 100),
    )
    lineitem = lineitem.append_column(
        "l_discount_hundredths",
        _decimal_column_to_scaled_int(tables["lineitem"]["l_discount"], 100, pa.int32()),
    )
    tables["lineitem"] = lineitem
    return {name: _cudf_from_arrow(cudf, table) for name, table in tables.items()}


def _cudf_from_arrow(cudf, table: pa.Table):
    if hasattr(cudf, "from_arrow"):
        return cudf.from_arrow(table)
    return cudf.DataFrame.from_arrow(table)


def run_q5(dataset_path: Path, region_name: str, start_date: str) -> list[ResultRow]:
    try:
        import cudf
    except ImportError as exc:
        raise SystemExit("RAPIDS cudf Python package is not installed") from exc

    tables = _load_cudf_tables(dataset_path, cudf)
    region = tables["region"]
    nation = tables["nation"]
    supplier = tables["supplier"]
    customer = tables["customer"]
    orders = tables["orders"]
    lineitem = tables["lineitem"]

    selected_region = region[region["r_name"] == region_name]
    selected_nation = nation.merge(
        selected_region, left_on="n_regionkey", right_on="r_regionkey"
    )[["n_nationkey", "n_name"]]

    supplier = supplier.merge(
        selected_nation, left_on="s_nationkey", right_on="n_nationkey"
    )[["s_suppkey", "s_nationkey", "n_name"]]

    customer = customer.merge(
        selected_nation[["n_nationkey"]],
        left_on="c_nationkey",
        right_on="n_nationkey",
    )[["c_custkey", "c_nationkey"]]

    start = cudf.to_datetime(start_date)
    end = cudf.to_datetime(add_year(start_date))
    orders["o_orderdate"] = cudf.to_datetime(orders["o_orderdate"])
    orders = orders[(orders["o_orderdate"] >= start) & (orders["o_orderdate"] < end)]

    customer_orders = customer.merge(orders, left_on="c_custkey", right_on="o_custkey")
    joined = lineitem.merge(
        customer_orders[["o_orderkey", "c_nationkey"]],
        left_on="l_orderkey",
        right_on="o_orderkey",
    )
    joined = joined.merge(supplier, left_on="l_suppkey", right_on="s_suppkey")
    joined = joined[joined["c_nationkey"] == joined["s_nationkey"]]

    joined["revenue_1e4"] = (
        joined["l_extendedprice_cents"]
        * (100 - joined["l_discount_hundredths"].astype("int64"))
    ).astype("int64")

    result = (
        joined.groupby("n_name")
        .agg({"revenue_1e4": "sum"})
        .reset_index()
        .sort_values(["revenue_1e4", "n_name"], ascending=[False, True])
    )

    pdf = result.to_pandas()
    return [ResultRow(str(row.n_name), int(row.revenue_1e4)) for row in pdf.itertuples(index=False)]


def main() -> int:
    parser = argparse.ArgumentParser(description="RAPIDS cuDF baseline for TPC-H Q5")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--region", default="ASIA")
    parser.add_argument("--date", default="1994-01-01")
    parser.add_argument("--format", choices=["rows", "json", "benchmark"], default="rows")
    args = parser.parse_args()

    started = time.perf_counter()
    rows = run_q5(Path(args.dataset), args.region, args.date)
    total_ms = (time.perf_counter() - started) * 1000.0
    if args.format == "rows":
        emit_rows(rows)
    elif args.format == "json":
        emit_json(rows)
    else:
        emit_benchmark("cudf", args.region, args.date, rows, total_ms=total_ms)
    return 0


if __name__ == "__main__":
    sys.exit(main())
