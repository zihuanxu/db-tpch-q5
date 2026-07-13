#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

HASH_RE = re.compile(r"^[0-9a-fA-F]{16}$")
TWO_DECIMAL_RE = re.compile(r"^-?\d+\.\d{2}$")
NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
TIMING_KEYS = {
    "timing_build_ms",
    "timing_h2d_ms",
    "timing_kernel_ms",
    "timing_d2h_ms",
    "timing_scan_ms",
    "timing_total_ms",
}


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
        hashes: list[str] = []
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
                if (
                    len(row) != 2
                    or key not in TIMING_KEYS
                    or not NUMBER_RE.fullmatch(row[1])
                ):
                    raise ValueError(f"unexpected row after result_hash at actual row {line_number}")
                continue

            if len(row) != 3:
                raise ValueError(f"actual row {line_number} must have exactly 3 columns")
            if not TWO_DECIMAL_RE.fullmatch(row[2]):
                raise ValueError(
                    f"actual row {line_number} revenue must have exactly two decimal places"
                )

            rows.append({"nation": row[0], "revenue": row[2]})

        if len(hashes) != 1:
            raise ValueError("expected exactly one 16-hex result_hash row")

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
