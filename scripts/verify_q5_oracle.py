#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

HASH_RE = re.compile(r"^[0-9a-fA-F]{16}$")
TWO_DECIMAL_RE = re.compile(r"^-?\d+\.\d{2}$")
NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
INTEGER_RE = re.compile(r"^-?\d+$")
NON_NEGATIVE_INTEGER_RE = re.compile(r"^\d+$")
INT64_MIN = -(1 << 63)
INT64_MAX = (1 << 63) - 1
UINT64_MASK = (1 << 64) - 1
FNV_OFFSET_BASIS = 14695981039346656037
FNV_PRIME = 1099511628211
TIMING_KEYS = {
    "timing_build_ms",
    "timing_h2d_ms",
    "timing_kernel_ms",
    "timing_d2h_ms",
    "timing_scan_ms",
    "timing_total_ms",
}
COUNTER_KEYS = {
    "input_lineitem_rows",
    "matched_lineitem_rows",
    "cpu_input_rows",
    "gpu_input_rows",
    "h2d_bytes",
    "d2h_bytes",
    "mapped_remote_read_bytes",
}


def format_revenue_1e4(value: int) -> str:
    if value > INT64_MAX - 50 or value < INT64_MIN + 50:
        raise ValueError("revenue_1e4 cannot be formatted without int64 overflow")
    cents = (value + 50) // 100 if value >= 0 else -((-value + 50) // 100)
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def result_hash_hex(rows: list[tuple[str, int]]) -> str:
    result = FNV_OFFSET_BASIS
    for nation, revenue_1e4 in rows:
        for byte in nation.encode("utf-8") + b"\xff":
            result = ((result ^ byte) * FNV_PRIME) & UINT64_MASK
        unsigned_revenue = revenue_1e4 & UINT64_MASK
        for shift in range(0, 64, 8):
            byte = (unsigned_revenue >> shift) & 0xFF
            result = ((result ^ byte) * FNV_PRIME) & UINT64_MASK
    return f"{result:016x}"


def parse_actual_rows(path: Path) -> tuple[list[dict[str, str]], str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError("actual rows file is empty") from exc

        normalized_header = [cell.strip() for cell in header]
        if normalized_header != ["nation", "revenue_1e4", "revenue"]:
            raise ValueError("actual rows header must be nation,revenue_1e4,revenue")

        rows: list[dict[str, str]] = []
        exact_rows: list[tuple[str, int]] = []
        hashes: list[str] = []
        seen_metadata: set[str] = set()
        counter_values: dict[str, int] = {}
        seen_hash = False

        for line_number, raw_row in enumerate(reader, start=2):
            row = [cell.strip() for cell in raw_row]
            if not row or all(cell == "" for cell in row):
                continue

            key = row[0]
            if key == "result_hash":
                seen_hash = True
                if len(row) != 2 or not HASH_RE.fullmatch(row[1]):
                    raise ValueError("expected exactly one 16-hex result_hash row")
                hashes.append(row[1].lower())
                continue

            if seen_hash:
                if len(row) != 2 or key not in TIMING_KEYS | COUNTER_KEYS:
                    raise ValueError(f"unexpected row after result_hash at actual row {line_number}")
                if key in seen_metadata:
                    raise ValueError(f"duplicate metadata key: {key}")
                seen_metadata.add(key)
                if key in COUNTER_KEYS:
                    if not NON_NEGATIVE_INTEGER_RE.fullmatch(row[1]):
                        raise ValueError("counter must be a non-negative integer")
                    counter = int(row[1])
                    if counter > INT64_MAX:
                        raise ValueError("counter must fit int64")
                    counter_values[key] = counter
                else:
                    if not NUMBER_RE.fullmatch(row[1]):
                        raise ValueError("timing must be a non-negative finite number")
                    try:
                        timing = Decimal(row[1])
                    except InvalidOperation as exc:
                        raise ValueError("timing must be a non-negative finite number") from exc
                    if not timing.is_finite() or timing < 0:
                        raise ValueError("timing must be a non-negative finite number")
                continue

            if len(row) != 3:
                raise ValueError(f"actual row {line_number} must have exactly 3 columns")
            if not TWO_DECIMAL_RE.fullmatch(row[2]):
                raise ValueError(
                    f"actual row {line_number} revenue must have exactly two decimal places"
                )

            if not INTEGER_RE.fullmatch(row[1]):
                raise ValueError(f"actual row {line_number} revenue_1e4 must be an int64")
            revenue_1e4 = int(row[1])
            if revenue_1e4 < INT64_MIN or revenue_1e4 > INT64_MAX:
                raise ValueError(f"actual row {line_number} revenue_1e4 must be an int64")
            if format_revenue_1e4(revenue_1e4) != row[2]:
                raise ValueError(
                    f"actual row {line_number} revenue does not match revenue_1e4"
                )

            rows.append({"nation": row[0], "revenue": row[2]})
            exact_rows.append((row[0], revenue_1e4))

        if len(hashes) != 1:
            raise ValueError("expected exactly one 16-hex result_hash row")
        if hashes[0] != result_hash_hex(exact_rows):
            raise ValueError("result_hash does not match exact rows")
        if counter_values:
            if set(counter_values) != COUNTER_KEYS:
                raise ValueError("counter metadata must contain every counter exactly once")
            if (
                counter_values["matched_lineitem_rows"]
                > counter_values["input_lineitem_rows"]
            ):
                raise ValueError(
                    "matched_lineitem_rows exceeds input_lineitem_rows"
                )
            if (
                counter_values["cpu_input_rows"]
                + counter_values["gpu_input_rows"]
                != counter_values["input_lineitem_rows"]
            ):
                raise ValueError(
                    "cpu_input_rows plus gpu_input_rows must equal input_lineitem_rows"
                )

        return rows, hashes[0]


def parse_oracle_rows(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    nonempty_lines = [line for line in lines if line.strip()]
    if not nonempty_lines:
        raise ValueError("oracle file is empty")

    header_fields = [field.strip().lower() for field in nonempty_lines[0].split("|")]
    while header_fields and header_fields[-1] == "":
        header_fields.pop()
    if (
        len(header_fields) < 2
        or header_fields[0] not in {"nation", "n_name"}
        or header_fields[1] != "revenue"
    ):
        raise ValueError("oracle header must begin with n_name|revenue or nation|revenue")

    rows: list[dict[str, str]] = []
    for line_number, line in enumerate(nonempty_lines[1:], start=2):
        fields = [field.strip() for field in line.split("|")]
        while fields and fields[-1] == "":
            fields.pop()
        if len(fields) < 2:
            raise ValueError(f"oracle row {line_number} must contain nation and revenue")
        if not TWO_DECIMAL_RE.fullmatch(fields[1]):
            raise ValueError(
                f"oracle row {line_number} revenue must have exactly two decimal places"
            )
        rows.append({"nation": fields[0], "revenue": fields[1]})

    return rows


def build_mismatch(
    actual_rows: list[dict[str, str]], oracle_rows: list[dict[str, str]]
) -> dict[str, object] | None:
    if len(actual_rows) != len(oracle_rows):
        return {
            "message": f"row count mismatch actual={len(actual_rows)} expected={len(oracle_rows)}",
            "actual_row_count": len(actual_rows),
            "expected_row_count": len(oracle_rows),
        }

    for index, (actual, expected) in enumerate(zip(actual_rows, oracle_rows, strict=True), start=1):
        if actual["nation"] != expected["nation"]:
            return {
                "message": f"row {index} nation mismatch",
                "row": index,
                "actual": actual,
                "expected": expected,
            }
        if actual["revenue"] != expected["revenue"]:
            return {
                "message": f"row {index} revenue mismatch",
                "row": index,
                "actual": actual,
                "expected": expected,
            }

    return None


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify MEMQ5 rows output against official TPC-H Q5 oracle")
    parser.add_argument("--actual", type=Path, required=True)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()

    try:
        actual_rows, result_hash = parse_actual_rows(args.actual)
        oracle_rows = parse_oracle_rows(args.oracle)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    mismatch = build_mismatch(actual_rows, oracle_rows)
    payload: dict[str, object] = {
        "actual_rows": actual_rows,
        "matched": mismatch is None,
        "oracle_rows": oracle_rows,
        "result_hash": result_hash,
        "row_count": len(actual_rows),
    }
    if mismatch is not None:
        payload["mismatch"] = mismatch

    if args.output_json is not None:
        write_json(args.output_json, payload)

    if mismatch is not None:
        print(str(mismatch["message"]), file=sys.stderr)
        return 1

    print(f"ok rows={len(actual_rows)} result_hash={result_hash}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
