#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as csv

from common import ResultRow, emit_benchmark, emit_json, emit_rows


def add_year(value: str) -> str:
    parsed = date.fromisoformat(value)
    try:
        return parsed.replace(year=parsed.year + 1).isoformat()
    except ValueError:
        return parsed.replace(year=parsed.year + 1, day=28).isoformat()


def read_arrow_table(
    path: Path,
    names: list[str],
    usecols: list[str],
    column_types: dict[str, pa.DataType],
) -> pa.Table:
    return csv.read_csv(
        path,
        read_options=csv.ReadOptions(column_names=names),
        parse_options=csv.ParseOptions(delimiter="|"),
        convert_options=csv.ConvertOptions(
            include_columns=usecols,
            column_types=column_types,
            strings_can_be_null=False,
        ),
    )


def run_q5(data_dir: Path, region_name: str, start_date: str) -> list[ResultRow]:
    region = read_arrow_table(
        data_dir / "region.tbl",
        ["r_regionkey", "r_name", "r_comment", "_empty"],
        ["r_regionkey", "r_name"],
        {"r_regionkey": pa.int64(), "r_name": pa.string()},
    )
    nation = read_arrow_table(
        data_dir / "nation.tbl",
        ["n_nationkey", "n_name", "n_regionkey", "n_comment", "_empty"],
        ["n_nationkey", "n_name", "n_regionkey"],
        {"n_nationkey": pa.int64(), "n_name": pa.string(), "n_regionkey": pa.int64()},
    )
    supplier = read_arrow_table(
        data_dir / "supplier.tbl",
        [
            "s_suppkey",
            "s_name",
            "s_address",
            "s_nationkey",
            "s_phone",
            "s_acctbal",
            "s_comment",
            "_empty",
        ],
        ["s_suppkey", "s_nationkey"],
        {"s_suppkey": pa.int64(), "s_nationkey": pa.int64()},
    )
    customer = read_arrow_table(
        data_dir / "customer.tbl",
        [
            "c_custkey",
            "c_name",
            "c_address",
            "c_nationkey",
            "c_phone",
            "c_acctbal",
            "c_mktsegment",
            "c_comment",
            "_empty",
        ],
        ["c_custkey", "c_nationkey"],
        {"c_custkey": pa.int64(), "c_nationkey": pa.int64()},
    )
    orders = read_arrow_table(
        data_dir / "orders.tbl",
        [
            "o_orderkey",
            "o_custkey",
            "o_orderstatus",
            "o_totalprice",
            "o_orderdate",
            "o_orderpriority",
            "o_clerk",
            "o_shippriority",
            "o_comment",
            "_empty",
        ],
        ["o_orderkey", "o_custkey", "o_orderdate"],
        {"o_orderkey": pa.int64(), "o_custkey": pa.int64(), "o_orderdate": pa.string()},
    )
    lineitem = read_arrow_table(
        data_dir / "lineitem.tbl",
        [
            "l_orderkey",
            "l_partkey",
            "l_suppkey",
            "l_linenumber",
            "l_quantity",
            "l_extendedprice",
            "l_discount",
            "l_tax",
            "l_returnflag",
            "l_linestatus",
            "l_shipdate",
            "l_commitdate",
            "l_receiptdate",
            "l_shipinstruct",
            "l_shipmode",
            "l_comment",
            "_empty",
        ],
        ["l_orderkey", "l_suppkey", "l_extendedprice", "l_discount"],
        {
            "l_orderkey": pa.int64(),
            "l_suppkey": pa.int64(),
            "l_extendedprice": pa.float64(),
            "l_discount": pa.float64(),
        },
    )

    selected_region = region.filter(pc.equal(region["r_name"], region_name))
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
        selected_nation,
        keys="c_nationkey",
        right_keys="n_nationkey",
        join_type="inner",
    ).select(["c_custkey", "c_nationkey"])

    end_date = add_year(start_date)
    orders = orders.filter(
        pc.and_(
            pc.greater_equal(orders["o_orderdate"], start_date),
            pc.less(orders["o_orderdate"], end_date),
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

    extendedprice_cents = pc.cast(
        pc.round(pc.multiply(joined["l_extendedprice"], 100)),
        pa.int64(),
    )
    discount_bp = pc.cast(
        pc.round(pc.multiply(joined["l_discount"], 10000)),
        pa.int64(),
    )
    revenue_cents = pc.divide(
        pc.multiply(extendedprice_cents, pc.subtract(10000, discount_bp)),
        10000,
    )
    joined = joined.append_column("revenue_cents", revenue_cents)

    result = (
        joined.group_by("n_name")
        .aggregate([("revenue_cents", "sum")])
        .sort_by([("revenue_cents_sum", "descending"), ("n_name", "ascending")])
    )

    names = result["n_name"].to_pylist()
    revenues = result["revenue_cents_sum"].to_pylist()
    return [ResultRow(str(name), int(revenue)) for name, revenue in zip(names, revenues)]


def main() -> int:
    parser = argparse.ArgumentParser(description="PyArrow baseline for TPC-H Q5")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--region", default="ASIA")
    parser.add_argument("--date", default="1994-01-01")
    parser.add_argument("--format", choices=["rows", "json", "benchmark"], default="rows")
    args = parser.parse_args()

    started = time.perf_counter()
    rows = run_q5(Path(args.data_dir), args.region, args.date)
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
