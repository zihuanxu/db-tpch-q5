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


def add_year(value: str) -> date:
    parsed = date.fromisoformat(value)
    try:
        return parsed.replace(year=parsed.year + 1)
    except ValueError:
        return parsed.replace(year=parsed.year + 1, day=28)


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


def _load_q5_tables(dataset_path: Path) -> dict[str, pa.Table]:
    tables = load_arrow_dataset(dataset_path)
    tables["region"] = _decode_name_column(tables["region"], "r_name")
    tables["nation"] = _decode_name_column(tables["nation"], "n_name")

    lineitem = tables["lineitem"]
    lineitem = lineitem.append_column(
        "l_extendedprice_cents",
        _decimal_column_to_scaled_int(lineitem["l_extendedprice"], 100),
    )
    lineitem = lineitem.append_column(
        "l_discount_hundredths",
        _decimal_column_to_scaled_int(lineitem["l_discount"], 100, pa.int32()),
    )
    tables["lineitem"] = lineitem
    return tables


def run_q5(dataset_path: Path, region_name: str, start_date: str) -> list[ResultRow]:
    tables = _load_q5_tables(dataset_path)
    region = tables["region"]
    nation = tables["nation"]
    supplier = tables["supplier"]
    customer = tables["customer"]
    orders = tables["orders"]
    lineitem = tables["lineitem"].select(
        ["l_orderkey", "l_suppkey", "l_extendedprice_cents", "l_discount_hundredths"]
    )

    selected_region = region.filter(pc.equal(region["r_name"], pa.scalar(region_name, type=pa.string())))
    selected_nation = nation.join(
        selected_region,
        keys="n_regionkey",
        right_keys="r_regionkey",
        join_type="inner",
    ).select(["n_nationkey", "n_name"])

    supplier = supplier.join(
        selected_nation,
        keys="s_nationkey",
        right_keys="n_nationkey",
        join_type="inner",
    ).select(["s_suppkey", "s_nationkey", "n_name"])

    customer = customer.join(
        selected_nation.select(["n_nationkey"]),
        keys="c_nationkey",
        right_keys="n_nationkey",
        join_type="inner",
    ).select(["c_custkey", "c_nationkey"])

    start = date.fromisoformat(start_date)
    end = add_year(start_date)
    orders = orders.filter(
        pc.and_(
            pc.greater_equal(orders["o_orderdate"], pa.scalar(start, type=pa.date32())),
            pc.less(orders["o_orderdate"], pa.scalar(end, type=pa.date32())),
        )
    )

    customer_orders = customer.join(
        orders,
        keys="c_custkey",
        right_keys="o_custkey",
        join_type="inner",
    ).select(["o_orderkey", "c_nationkey"])

    joined = lineitem.join(
        customer_orders,
        keys="l_orderkey",
        right_keys="o_orderkey",
        join_type="inner",
    )
    joined = joined.join(
        supplier,
        keys="l_suppkey",
        right_keys="s_suppkey",
        join_type="inner",
    )
    joined = joined.filter(pc.equal(joined["c_nationkey"], joined["s_nationkey"]))

    revenue_1e4 = pc.multiply(
        joined["l_extendedprice_cents"],
        pc.subtract(pa.scalar(100, type=pa.int32()), joined["l_discount_hundredths"]),
    )
    result = (
        joined.append_column("revenue_1e4", revenue_1e4)
        .group_by("n_name")
        .aggregate([("revenue_1e4", "sum")])
        .sort_by([("revenue_1e4_sum", "descending"), ("n_name", "ascending")])
    )

    names = result["n_name"].to_pylist()
    revenues = result["revenue_1e4_sum"].to_pylist()
    return [ResultRow(str(name), int(revenue)) for name, revenue in zip(names, revenues)]


def main() -> int:
    parser = argparse.ArgumentParser(description="PyArrow baseline for TPC-H Q5")
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
        emit_benchmark("arrow", args.region, args.date, rows, total_ms=total_ms)
    return 0


if __name__ == "__main__":
    sys.exit(main())
