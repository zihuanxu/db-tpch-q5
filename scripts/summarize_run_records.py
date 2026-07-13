#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path

try:
    from scripts.benchmark_schema import BenchmarkRecord, read_records
except ModuleNotFoundError:
    from benchmark_schema import BenchmarkRecord, read_records


GROUP_FIELDS = [
    "experiment_id", "scale_factor", "engine", "scenario", "threads",
    "cpu_ratio", "gpu_ratio",
]
METRICS = [
    "query_total_ms", "process_elapsed_ms", "plan_build_ms", "h2d_ms",
    "cpu_scan_ms", "gpu_kernel_ms", "d2h_ms", "overlap_wall_ms",
    "cpu_peak_rss_bytes", "gpu_peak_memory_bytes", "throughput_rows_per_second",
]
STAT_NAMES = ["min", "median", "max", "p95", "stddev"]


def summarize_values(values: list[float]) -> dict[str, float]:
    if not values:
        raise ValueError("cannot summarize an empty value list")
    ordered = sorted(values)
    position = 0.95 * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    fraction = position - lower
    p95 = ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
    return {
        "min": ordered[0],
        "median": statistics.median(ordered),
        "max": ordered[-1],
        "p95": p95,
        "stddev": statistics.stdev(ordered) if len(ordered) > 1 else 0.0,
    }


def _group_key(record: BenchmarkRecord) -> tuple[object, ...]:
    return tuple(getattr(record, name) for name in GROUP_FIELDS)


def summarize_records(records: list[BenchmarkRecord]) -> list[dict[str, object]]:
    groups: dict[tuple[object, ...], list[BenchmarkRecord]] = defaultdict(list)
    for record in records:
        if not record.is_warmup:
            groups[_group_key(record)].append(record)

    output: list[dict[str, object]] = []
    for key in sorted(groups):
        group = groups[key]
        successful = [record for record in group if record.status == "ok"]
        row: dict[str, object] = dict(zip(GROUP_FIELDS, key, strict=True))
        row["success_count"] = len(successful)
        row["failure_count"] = len(group) - len(successful)
        hashes = sorted({record.result_hash for record in successful})
        row["result_hash"] = hashes[0] if len(hashes) == 1 else ""
        for metric in METRICS:
            if successful:
                stats = summarize_values(
                    [float(getattr(record, metric)) for record in successful]
                )
                for name in STAT_NAMES:
                    row[f"{metric}_{name}"] = stats[name]
            else:
                for name in STAT_NAMES:
                    row[f"{metric}_{name}"] = ""
        output.append(row)
    return output


def write_summary(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = summary_fieldnames()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def summary_fieldnames() -> list[str]:
    fieldnames = GROUP_FIELDS + ["success_count", "failure_count", "result_hash"]
    for metric in METRICS:
        fieldnames.extend(f"{metric}_{name}" for name in STAT_NAMES)
    return fieldnames


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize schema-version 1 run records")
    parser.add_argument("raw_csv", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = summarize_records(read_records(args.raw_csv))
    write_summary(args.output, rows)
    print(f"ok groups={len(rows)} output={args.output}")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
