#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path


GROUP_FIELDS = ["engine", "region", "date", "threads", "result_hash"]
METRIC_FIELDS = [
    "build_ms",
    "h2d_ms",
    "kernel_ms",
    "d2h_ms",
    "scan_ms",
    "total_ms",
    "elapsed_ms",
]


def parse_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def load_groups(path: Path):
    groups: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    errors: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("status") != "ok":
                errors.append(row)
                continue
            key = tuple(row.get(field, "") for field in GROUP_FIELDS)
            groups[key].append(row)
    return groups, errors


def summarize_group(key: tuple[str, ...], rows: list[dict[str, str]]) -> dict[str, str]:
    summary = {field: key[index] for index, field in enumerate(GROUP_FIELDS)}
    summary["runs"] = str(len(rows))
    for metric in METRIC_FIELDS:
        values = [parse_float(row.get(metric, "")) for row in rows]
        summary[f"{metric}_min"] = f"{min(values):.6f}"
        summary[f"{metric}_median"] = f"{statistics.median(values):.6f}"
        summary[f"{metric}_mean"] = f"{statistics.mean(values):.6f}"
    return summary


def write_csv(rows: list[dict[str, str]]) -> None:
    fields = GROUP_FIELDS + ["runs"]
    for metric in METRIC_FIELDS:
        fields.extend([f"{metric}_min", f"{metric}_median", f"{metric}_mean"])
    writer = csv.DictWriter(sys.stdout, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)


def write_markdown(rows: list[dict[str, str]]) -> None:
    headers = [
        "engine",
        "threads",
        "runs",
        "hash",
        "total_ms_median",
        "scan_ms_median",
        "h2d_ms_median",
        "kernel_ms_median",
        "d2h_ms_median",
        "elapsed_ms_median",
    ]
    print("| " + " | ".join(headers) + " |")
    print("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        values = [
            row["engine"],
            row["threads"],
            row["runs"],
            row["result_hash"],
            row["total_ms_median"],
            row["scan_ms_median"],
            row["h2d_ms_median"],
            row["kernel_ms_median"],
            row["d2h_ms_median"],
            row["elapsed_ms_median"],
        ]
        print("| " + " | ".join(values) + " |")


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize MEMQ5 benchmark CSV")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--format", choices=["csv", "markdown"], default="markdown")
    parser.add_argument("--show-errors", action="store_true")
    args = parser.parse_args()

    groups, errors = load_groups(args.csv_path)
    rows = [summarize_group(key, value) for key, value in sorted(groups.items())]

    if args.format == "csv":
        write_csv(rows)
    else:
        write_markdown(rows)

    if args.show_errors and errors:
        print()
        print("Errors:")
        for row in errors:
            print(f"- {row.get('engine')} threads={row.get('threads')}: {row.get('error')}")

    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
