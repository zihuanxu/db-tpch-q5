#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

from common import ResultRow, emit_benchmark, emit_json, emit_rows


def add_year(value: str) -> str:
    parsed = date.fromisoformat(value)
    try:
        return parsed.replace(year=parsed.year + 1).isoformat()
    except ValueError:
        return parsed.replace(year=parsed.year + 1, day=28).isoformat()


def read_cudf_table(cudf, path: Path, names: list[str], usecols: list[int]):
    return cudf.read_csv(
        str(path),
        sep="|",
        header=None,
        names=names,
        usecols=usecols,
    )


def run_q5(data_dir: Path, region_name: str, start_date: str) -> list[ResultRow]:
    try:
        import cudf
    except ImportError as exc:
        raise SystemExit("RAPIDS cudf Python package is not installed") from exc

    region = read_cudf_table(
        cudf,
        data_dir / "region.tbl",
        ["r_regionkey", "r_name", "r_comment", "_empty"],
        [0, 1],
    )
    nation = read_cudf_table(
        cudf,
        data_dir / "nation.tbl",
        ["n_nationkey", "n_name", "n_regionkey", "n_comment", "_empty"],
        [0, 1, 2],
    )
    supplier = read_cudf_table(
        cudf,
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
        [0, 3],
    )
    customer = read_cudf_table(
        cudf,
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
        [0, 3],
    )
    orders = read_cudf_table(
        cudf,
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
        [0, 1, 4],
    )
    lineitem = read_cudf_table(
        cudf,
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
        [0, 2, 5, 6],
    )

    selected_region = region[region["r_name"] == region_name]
    selected_nation = nation.merge(
        selected_region, left_on="n_regionkey", right_on="r_regionkey"
    )[["n_nationkey", "n_name"]]

    supplier = supplier.merge(
        selected_nation, left_on="s_nationkey", right_on="n_nationkey"
    )[["s_suppkey", "s_nationkey", "n_name"]]

    customer = customer.merge(
        selected_nation, left_on="c_nationkey", right_on="n_nationkey"
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

    joined["extendedprice_cents"] = (joined["l_extendedprice"] * 100).round().astype("int64")
    joined["discount_bp"] = (joined["l_discount"] * 10000).round().astype("int32")
    joined["revenue_cents"] = (
        joined["extendedprice_cents"] * (10000 - joined["discount_bp"]) / 10000
    ).astype("int64")

    result = (
        joined.groupby("n_name")
        .agg({"revenue_cents": "sum"})
        .reset_index()
        .sort_values(["revenue_cents", "n_name"], ascending=[False, True])
    )

    pdf = result.to_pandas()
    return [
        ResultRow(str(row.n_name), int(row.revenue_cents))
        for row in pdf.itertuples(index=False)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="RAPIDS cuDF baseline for TPC-H Q5")
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
        emit_benchmark("cudf", args.region, args.date, rows, total_ms=total_ms)
    return 0


if __name__ == "__main__":
    sys.exit(main())
