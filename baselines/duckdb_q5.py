#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

from common import (
    ResultRow,
    emit_benchmark,
    emit_json,
    emit_rows,
    parse_decimal_scaled,
    read_tbl,
)


def add_year(value: str) -> str:
    parsed = date.fromisoformat(value)
    try:
        return parsed.replace(year=parsed.year + 1).isoformat()
    except ValueError:
        return parsed.replace(year=parsed.year + 1, day=28).isoformat()


def load_duckdb(con, data_dir: Path) -> None:
    con.execute("create table region(r_regionkey integer, r_name varchar)")
    con.executemany(
        "insert into region values (?, ?)",
        [(int(row[0]), row[1]) for row in read_tbl(data_dir / "region.tbl")],
    )

    con.execute(
        "create table nation(n_nationkey integer, n_name varchar, n_regionkey integer)"
    )
    con.executemany(
        "insert into nation values (?, ?, ?)",
        [(int(row[0]), row[1], int(row[2])) for row in read_tbl(data_dir / "nation.tbl")],
    )

    con.execute("create table supplier(s_suppkey integer, s_nationkey integer)")
    con.executemany(
        "insert into supplier values (?, ?)",
        [(int(row[0]), int(row[3])) for row in read_tbl(data_dir / "supplier.tbl")],
    )

    con.execute("create table customer(c_custkey integer, c_nationkey integer)")
    con.executemany(
        "insert into customer values (?, ?)",
        [(int(row[0]), int(row[3])) for row in read_tbl(data_dir / "customer.tbl")],
    )

    con.execute("create table orders(o_orderkey integer, o_custkey integer, o_orderdate date)")
    con.executemany(
        "insert into orders values (?, ?, ?)",
        [(int(row[0]), int(row[1]), row[4]) for row in read_tbl(data_dir / "orders.tbl")],
    )

    con.execute(
        "create table lineitem("
        "l_orderkey integer, l_suppkey integer, "
        "l_extendedprice_cents bigint, l_discount_bp integer)"
    )
    con.executemany(
        "insert into lineitem values (?, ?, ?, ?)",
        [
            (
                int(row[0]),
                int(row[2]),
                parse_decimal_scaled(row[5], 100),
                parse_decimal_scaled(row[6], 10000),
            )
            for row in read_tbl(data_dir / "lineitem.tbl")
        ],
    )


def run_q5(data_dir: Path, region: str, start_date: str) -> list[ResultRow]:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("duckdb Python package is not installed") from exc

    con = duckdb.connect(":memory:")
    load_duckdb(con, data_dir)
    end_date = add_year(start_date)

    rows = con.execute(
        """
        select
          n.n_name,
          sum(cast(floor((l.l_extendedprice_cents * (10000 - l.l_discount_bp)) / 10000) as bigint)) as revenue_cents
        from customer c
        join orders o on c.c_custkey = o.o_custkey
        join lineitem l on l.l_orderkey = o.o_orderkey
        join supplier s on l.l_suppkey = s.s_suppkey
        join nation n on s.s_nationkey = n.n_nationkey
        join region r on n.n_regionkey = r.r_regionkey
        where c.c_nationkey = s.s_nationkey
          and r.r_name = ?
          and o.o_orderdate >= cast(? as date)
          and o.o_orderdate < cast(? as date)
        group by n.n_name
        order by revenue_cents desc, n.n_name
        """,
        [region, start_date, end_date],
    ).fetchall()

    return [ResultRow(str(nation), int(revenue)) for nation, revenue in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description="DuckDB baseline for TPC-H Q5")
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
        emit_benchmark("duckdb", args.region, args.date, rows, total_ms=total_ms)
    return 0


if __name__ == "__main__":
    sys.exit(main())
