#!/usr/bin/env python3

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ResultRow:
    nation: str
    revenue_1e4: int


def parse_decimal_scaled(value: str, scale: int) -> int:
    return int((Decimal(value) * scale).to_integral_value())


def _round_revenue_1e4_to_cents(revenue_1e4: int) -> int:
    sign = -1 if revenue_1e4 < 0 else 1
    return sign * ((abs(revenue_1e4) + 50) // 100)


def format_revenue_1e4(revenue_1e4: int) -> str:
    cents = _round_revenue_1e4_to_cents(revenue_1e4)
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def result_hash(rows: Iterable[ResultRow]) -> str:
    hash_value = 14695981039346656037
    prime = 1099511628211

    def update_byte(byte: int) -> None:
        nonlocal hash_value
        hash_value ^= byte
        hash_value = (hash_value * prime) & 0xFFFFFFFFFFFFFFFF

    for row in rows:
        for byte in row.nation.encode("utf-8"):
            update_byte(byte)
        update_byte(0xFF)
        value = row.revenue_1e4 & 0xFFFFFFFFFFFFFFFF
        for shift in range(0, 64, 8):
            update_byte((value >> shift) & 0xFF)
    return f"{hash_value:016x}"


def read_tbl(path: Path) -> list[list[str]]:
    rows: list[list[str]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line:
                continue
            fields = line.split("|")
            if fields and fields[-1] == "":
                fields.pop()
            rows.append(fields)
    return rows


def emit_rows(rows: list[ResultRow]) -> None:
    print("nation,revenue_1e4,revenue")
    for row in rows:
        print(f"{row.nation},{row.revenue_1e4},{format_revenue_1e4(row.revenue_1e4)}")
    print(f"result_hash,{result_hash(rows)}")


def emit_json(rows: list[ResultRow]) -> None:
    print(
        json.dumps(
            {
                "result_hash": result_hash(rows),
                "rows": [
                    {
                        "nation": row.nation,
                        "revenue_1e4": row.revenue_1e4,
                        "revenue": format_revenue_1e4(row.revenue_1e4),
                    }
                    for row in rows
                ],
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def emit_benchmark(
    engine: str,
    region: str,
    date: str,
    rows: list[ResultRow],
    threads: int = 1,
    total_ms: float = 0.0,
) -> None:
    print("engine,region,date,threads,result_rows,result_hash,build_ms,h2d_ms,kernel_ms,d2h_ms,scan_ms,total_ms")
    print(
        f"{engine},{region},{date},{threads},{len(rows)},{result_hash(rows)},"
        f"0,0,0,0,{total_ms:.6f},{total_ms:.6f}"
    )
