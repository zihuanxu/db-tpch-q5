#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path


REQUIRED_FILES = {
    "region.tbl": 2,
    "nation.tbl": 3,
    "supplier.tbl": 4,
    "customer.tbl": 4,
    "orders.tbl": 5,
    "lineitem.tbl": 7,
}


@dataclass
class TableStats:
    rows: int = 0
    errors: list[str] = field(default_factory=list)


def split_tbl(line: str) -> list[str]:
    fields = line.rstrip("\n").split("|")
    if fields and fields[-1] == "":
        fields.pop()
    return fields


def parse_int(value: str, table: str, row: int, column: str, errors: list[str]) -> int | None:
    try:
        return int(value)
    except ValueError:
        errors.append(f"{table}:{row} invalid int {column}={value!r}")
        return None


def parse_date(value: str, table: str, row: int, column: str, errors: list[str]) -> str | None:
    try:
        date.fromisoformat(value)
        return value
    except ValueError:
        errors.append(f"{table}:{row} invalid date {column}={value!r}")
        return None


def parse_decimal(value: str, table: str, row: int, column: str, errors: list[str]) -> None:
    try:
        Decimal(value)
    except InvalidOperation:
        errors.append(f"{table}:{row} invalid decimal {column}={value!r}")


def add_year(value: str) -> str:
    parsed = date.fromisoformat(value)
    try:
        return parsed.replace(year=parsed.year + 1).isoformat()
    except ValueError:
        return parsed.replace(year=parsed.year + 1, day=28).isoformat()


def validate_table(path: Path, min_fields: int, table: str) -> tuple[TableStats, list[list[str]]]:
    stats = TableStats()
    sample_rows: list[list[str]] = []
    if not path.exists():
        stats.errors.append(f"missing file: {path}")
        return stats, sample_rows

    with path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            fields = split_tbl(line)
            stats.rows += 1
            if len(fields) < min_fields:
                stats.errors.append(
                    f"{table}:{index} expected at least {min_fields} fields, got {len(fields)}"
                )
                continue
            if len(sample_rows) < 5:
                sample_rows.append(fields)
    return stats, sample_rows


def validate(data_dir: Path, region: str, start_date: str) -> dict:
    end_date = add_year(start_date)
    result: dict = {
        "data_dir": str(data_dir),
        "region": region,
        "date": start_date,
        "end_date": end_date,
        "tables": {},
        "checks": {},
        "errors": [],
    }

    samples: dict[str, list[list[str]]] = {}
    for file_name, min_fields in REQUIRED_FILES.items():
        stats, sample_rows = validate_table(data_dir / file_name, min_fields, file_name)
        result["tables"][file_name] = {"rows": stats.rows, "errors": stats.errors}
        result["errors"].extend(stats.errors)
        samples[file_name] = sample_rows

    if result["errors"]:
        result["ok"] = False
        return result

    region_keys: dict[str, int] = {}
    nation_region: dict[int, int] = {}
    supplier_keys: set[int] = set()
    customer_keys: set[int] = set()
    order_keys: set[int] = set()
    orders_in_date = 0

    with (data_dir / "region.tbl").open("r", encoding="utf-8") as handle:
        for row, line in enumerate(handle, start=1):
            fields = split_tbl(line)
            key = parse_int(fields[0], "region.tbl", row, "r_regionkey", result["errors"])
            if key is not None:
                region_keys[fields[1]] = key

    with (data_dir / "nation.tbl").open("r", encoding="utf-8") as handle:
        for row, line in enumerate(handle, start=1):
            fields = split_tbl(line)
            nation = parse_int(fields[0], "nation.tbl", row, "n_nationkey", result["errors"])
            nation_region_key = parse_int(
                fields[2], "nation.tbl", row, "n_regionkey", result["errors"]
            )
            if nation is not None and nation_region_key is not None:
                nation_region[nation] = nation_region_key

    with (data_dir / "supplier.tbl").open("r", encoding="utf-8") as handle:
        for row, line in enumerate(handle, start=1):
            fields = split_tbl(line)
            suppkey = parse_int(fields[0], "supplier.tbl", row, "s_suppkey", result["errors"])
            parse_int(fields[3], "supplier.tbl", row, "s_nationkey", result["errors"])
            if suppkey is not None:
                supplier_keys.add(suppkey)

    with (data_dir / "customer.tbl").open("r", encoding="utf-8") as handle:
        for row, line in enumerate(handle, start=1):
            fields = split_tbl(line)
            custkey = parse_int(fields[0], "customer.tbl", row, "c_custkey", result["errors"])
            parse_int(fields[3], "customer.tbl", row, "c_nationkey", result["errors"])
            if custkey is not None:
                customer_keys.add(custkey)

    with (data_dir / "orders.tbl").open("r", encoding="utf-8") as handle:
        for row, line in enumerate(handle, start=1):
            fields = split_tbl(line)
            orderkey = parse_int(fields[0], "orders.tbl", row, "o_orderkey", result["errors"])
            parse_int(fields[1], "orders.tbl", row, "o_custkey", result["errors"])
            parsed_date = parse_date(fields[4], "orders.tbl", row, "o_orderdate", result["errors"])
            if orderkey is not None:
                order_keys.add(orderkey)
            if parsed_date is not None and start_date <= parsed_date < end_date:
                orders_in_date += 1

    with (data_dir / "lineitem.tbl").open("r", encoding="utf-8") as handle:
        for row, line in enumerate(handle, start=1):
            fields = split_tbl(line)
            parse_int(fields[0], "lineitem.tbl", row, "l_orderkey", result["errors"])
            parse_int(fields[2], "lineitem.tbl", row, "l_suppkey", result["errors"])
            parse_decimal(fields[5], "lineitem.tbl", row, "l_extendedprice", result["errors"])
            parse_decimal(fields[6], "lineitem.tbl", row, "l_discount", result["errors"])

    selected_region_key = region_keys.get(region)
    selected_nations = [
        nation for nation, region_key in nation_region.items() if region_key == selected_region_key
    ]

    result["checks"] = {
        "target_region_found": selected_region_key is not None,
        "selected_nations": len(selected_nations),
        "supplier_keys": len(supplier_keys),
        "customer_keys": len(customer_keys),
        "order_keys": len(order_keys),
        "orders_in_date_window": orders_in_date,
    }

    if selected_region_key is None:
        result["errors"].append(f"target region not found: {region}")
    if len(selected_nations) == 0:
        result["errors"].append(f"no nations found for region: {region}")
    if orders_in_date == 0:
        result["errors"].append(f"no orders in date window: [{start_date}, {end_date})")

    result["ok"] = len(result["errors"]) == 0
    return result


def print_text(report: dict) -> None:
    print(f"data_dir: {report['data_dir']}")
    print(f"region/date: {report['region']} {report['date']} to {report['end_date']}")
    print("tables:")
    for table, stats in report["tables"].items():
        print(f"  {table}: rows={stats['rows']} errors={len(stats['errors'])}")
    print("checks:")
    for name, value in report["checks"].items():
        print(f"  {name}: {value}")
    print(f"ok: {report['ok']}")
    if report["errors"]:
        print("errors:")
        for error in report["errors"][:20]:
            print(f"  - {error}")
        if len(report["errors"]) > 20:
            print(f"  ... {len(report['errors']) - 20} more")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a TPC-H Q5 data directory")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--region", default="ASIA")
    parser.add_argument("--date", default="1994-01-01")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    report = validate(args.data_dir, args.region, args.date)
    if args.format == "json":
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_text(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
