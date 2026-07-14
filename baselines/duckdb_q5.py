#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

from common import ResultRow, emit_benchmark, emit_json, emit_rows

QUERY_VERSION = "tpch-q5-duckdb-decimal-v1"

TABLE_SPECS: dict[str, tuple[tuple[tuple[str, str], ...], tuple[str, ...]]] = {
    "region": (
        (("r_regionkey", "BIGINT"), ("r_name", "VARCHAR"), ("r_comment", "VARCHAR")),
        ("r_regionkey", "r_name"),
    ),
    "nation": (
        (
            ("n_nationkey", "BIGINT"),
            ("n_name", "VARCHAR"),
            ("n_regionkey", "BIGINT"),
            ("n_comment", "VARCHAR"),
        ),
        ("n_nationkey", "n_name", "n_regionkey"),
    ),
    "supplier": (
        (
            ("s_suppkey", "BIGINT"),
            ("s_name", "VARCHAR"),
            ("s_address", "VARCHAR"),
            ("s_nationkey", "BIGINT"),
            ("s_phone", "VARCHAR"),
            ("s_acctbal", "DECIMAL(15,2)"),
            ("s_comment", "VARCHAR"),
        ),
        ("s_suppkey", "s_nationkey"),
    ),
    "customer": (
        (
            ("c_custkey", "BIGINT"),
            ("c_name", "VARCHAR"),
            ("c_address", "VARCHAR"),
            ("c_nationkey", "BIGINT"),
            ("c_phone", "VARCHAR"),
            ("c_acctbal", "DECIMAL(15,2)"),
            ("c_mktsegment", "VARCHAR"),
            ("c_comment", "VARCHAR"),
        ),
        ("c_custkey", "c_nationkey"),
    ),
    "orders": (
        (
            ("o_orderkey", "BIGINT"),
            ("o_custkey", "BIGINT"),
            ("o_orderstatus", "VARCHAR"),
            ("o_totalprice", "DECIMAL(15,2)"),
            ("o_orderdate", "DATE"),
            ("o_orderpriority", "VARCHAR"),
            ("o_clerk", "VARCHAR"),
            ("o_shippriority", "INTEGER"),
            ("o_comment", "VARCHAR"),
        ),
        ("o_orderkey", "o_custkey", "o_orderdate"),
    ),
    "lineitem": (
        (
            ("l_orderkey", "BIGINT"),
            ("l_partkey", "BIGINT"),
            ("l_suppkey", "BIGINT"),
            ("l_linenumber", "INTEGER"),
            ("l_quantity", "DECIMAL(15,2)"),
            ("l_extendedprice", "DECIMAL(15,2)"),
            ("l_discount", "DECIMAL(15,2)"),
            ("l_tax", "DECIMAL(15,2)"),
            ("l_returnflag", "VARCHAR"),
            ("l_linestatus", "VARCHAR"),
            ("l_shipdate", "DATE"),
            ("l_commitdate", "DATE"),
            ("l_receiptdate", "DATE"),
            ("l_shipinstruct", "VARCHAR"),
            ("l_shipmode", "VARCHAR"),
            ("l_comment", "VARCHAR"),
        ),
        ("l_orderkey", "l_suppkey", "l_extendedprice", "l_discount"),
    ),
}

Q5_SQL = """
select
  n.n_name,
  cast(sum(
    cast(l.l_extendedprice * 100 as bigint) *
    (100 - cast(l.l_discount * 100 as bigint))
  ) as bigint) as revenue_1e4
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
order by revenue_1e4 desc, n.n_name
""".strip()


def add_year(value: str) -> str:
    parsed = date.fromisoformat(value)
    try:
        return parsed.replace(year=parsed.year + 1).isoformat()
    except ValueError:
        return parsed.replace(year=parsed.year + 1, day=28).isoformat()


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def create_tpch_views(con, data_dir: Path) -> None:
    files = {name: data_dir / f"{name}.tbl" for name in TABLE_SPECS}
    for name, path in files.items():
        if not path.is_file():
            raise FileNotFoundError(path)

    for name, (columns, projection) in TABLE_SPECS.items():
        # TPC-H .tbl files end every row with a pipe; declare it then omit it.
        declared_columns = (*columns, ("_trailing_delimiter", "VARCHAR"))
        column_types = ", ".join(
            f"{_sql_string(column)}: {_sql_string(column_type)}"
            for column, column_type in declared_columns
        )
        path = _sql_string(str(files[name]))
        con.execute(
            f"create or replace view {name} as "
            f"select {', '.join(projection)} "
            f"from read_csv({path}, delim='|', header=false, "
            f"columns={{ {column_types} }})"
        )


def run_q5(data_dir: Path, region: str, start_date: str) -> list[ResultRow]:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("duckdb Python package is not installed") from exc

    con = duckdb.connect(":memory:")
    try:
        create_tpch_views(con, data_dir)
        rows = con.execute(Q5_SQL, [region, start_date, add_year(start_date)]).fetchall()
    finally:
        con.close()
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
