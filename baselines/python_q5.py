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


def run_q5(data_dir: Path, region_name: str, start_date: str) -> list[ResultRow]:
    end_date = add_year(start_date)

    region_key = None
    for row in read_tbl(data_dir / "region.tbl"):
        if row[1] == region_name:
            region_key = int(row[0])
            break
    if region_key is None:
        raise SystemExit(f"region not found: {region_name}")

    nation_in_region: set[int] = set()
    nation_name: dict[int, str] = {}
    for row in read_tbl(data_dir / "nation.tbl"):
        nation_key = int(row[0])
        nation_name[nation_key] = row[1]
        if int(row[2]) == region_key:
            nation_in_region.add(nation_key)

    supplier_nation: dict[int, int] = {}
    for row in read_tbl(data_dir / "supplier.tbl"):
        nation_key = int(row[3])
        if nation_key in nation_in_region:
            supplier_nation[int(row[0])] = nation_key

    customer_nation: dict[int, int] = {}
    for row in read_tbl(data_dir / "customer.tbl"):
        nation_key = int(row[3])
        if nation_key in nation_in_region:
            customer_nation[int(row[0])] = nation_key

    order_nation: dict[int, int] = {}
    for row in read_tbl(data_dir / "orders.tbl"):
        order_date = row[4]
        custkey = int(row[1])
        if start_date <= order_date < end_date and custkey in customer_nation:
            order_nation[int(row[0])] = customer_nation[custkey]

    revenue_by_nation: dict[int, int] = {}
    for row in read_tbl(data_dir / "lineitem.tbl"):
        orderkey = int(row[0])
        suppkey = int(row[2])
        order_nation_key = order_nation.get(orderkey)
        supplier_nation_key = supplier_nation.get(suppkey)
        if order_nation_key is None or order_nation_key != supplier_nation_key:
            continue
        extendedprice_cents = parse_decimal_scaled(row[5], 100)
        discount_bp = parse_decimal_scaled(row[6], 10000)
        revenue = extendedprice_cents * (10000 - discount_bp) // 10000
        revenue_by_nation[order_nation_key] = revenue_by_nation.get(order_nation_key, 0) + revenue

    rows = [
        ResultRow(nation_name[key], revenue)
        for key, revenue in revenue_by_nation.items()
        if revenue != 0
    ]
    rows.sort(key=lambda row: (-row.revenue_cents, row.nation))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Dependency-free Python reference for TPC-H Q5")
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
        emit_benchmark("python", args.region, args.date, rows, total_ms=total_ms)
    return 0


if __name__ == "__main__":
    sys.exit(main())
