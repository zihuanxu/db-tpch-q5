#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Sequence

try:
    from scripts.formal_benchmark_schema import (
        FormalBenchmarkRecord,
        FormalSetupRecord,
        read_requests,
        read_setups,
    )
except ModuleNotFoundError:
    from formal_benchmark_schema import (  # type: ignore[no-redef]
        FormalBenchmarkRecord,
        FormalSetupRecord,
        read_requests,
        read_setups,
    )


GROUP_FIELDS = [
    "experiment_id",
    "scale_factor",
    "config_id",
    "engine",
    "threads",
    "ratio_mode",
    "cpu_ratio",
    "gpu_ratio",
    "lifecycle",
]
STAT_NAMES = ["median", "mean", "min", "max", "pstdev", "p25", "p75", "p95"]
SUMMARY_FIELDS = [
    *GROUP_FIELDS,
    "success_count",
    "failure_count",
    "result_hash",
    *(f"query_total_ms_{name}" for name in STAT_NAMES),
    "dataset_load_ms",
    "session_setup_ms",
    "tune_ms",
    "setup_cost_ms",
    "resident_host_bytes",
    "resident_gpu_bytes",
    "resident_pinned_bytes",
    "resident_total_bytes",
    "amortized_1_request_ms",
    "amortized_10_requests_ms",
    "amortized_100_requests_ms",
]


def _percentile(ordered: list[float], quantile: float) -> float:
    position = quantile * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def summarize_values(values: Sequence[float]) -> dict[str, float]:
    if not values:
        raise ValueError("cannot summarize an empty value list")
    ordered = sorted(float(value) for value in values)
    return {
        "median": statistics.median(ordered),
        "mean": statistics.fmean(ordered),
        "min": ordered[0],
        "max": ordered[-1],
        "pstdev": statistics.pstdev(ordered),
        "p25": _percentile(ordered, 0.25),
        "p75": _percentile(ordered, 0.75),
        "p95": _percentile(ordered, 0.95),
    }


def _group_key(record: FormalBenchmarkRecord) -> tuple[object, ...]:
    return tuple(getattr(record, field) for field in GROUP_FIELDS)


def _is_profiler_row(record: FormalBenchmarkRecord) -> bool:
    if record.scenario != "resident" or record.lifecycle != "resident":
        return True
    options = json.loads(record.mode_options_json)
    return (
        any(name in options for name in ("profiler", "profiler_tool", "profiled"))
        or options.get("collection") == "profiler"
    )


def summarize_records(
    records: Sequence[FormalBenchmarkRecord], setups: Sequence[FormalSetupRecord]
) -> list[dict[str, object]]:
    if any(record.is_warmup for record in records):
        raise ValueError("latency summary accepts measured rows only")
    if any(_is_profiler_row(record) for record in records):
        raise ValueError("profiler rows are forbidden in resident latency summaries")

    setup_groups: dict[str, list[FormalSetupRecord]] = defaultdict(list)
    for setup in setups:
        setup_groups[setup.config_id].append(setup)
    record_groups: dict[tuple[object, ...], list[FormalBenchmarkRecord]] = defaultdict(list)
    for record in records:
        record_groups[_group_key(record)].append(record)

    output: list[dict[str, object]] = []
    for key in sorted(record_groups):
        group = record_groups[key]
        successful = [record for record in group if record.status == "ok"]
        if not successful:
            continue
        config_id = str(key[GROUP_FIELDS.index("config_id")])
        matching_setups = setup_groups.get(config_id, [])
        if len(matching_setups) != 1:
            raise ValueError(f"configuration {config_id} requires exactly one setup row")
        setup = matching_setups[0]
        if any(
            record.session_id != setup.session_id
            or record.engine != setup.engine
            or record.threads != setup.threads
            or record.ratio_mode != setup.ratio_mode
            or record.lifecycle != setup.lifecycle
            for record in group
        ):
            raise ValueError(f"configuration {config_id} requests do not match its setup")

        hashes = {record.result_hash for record in successful}
        if len(hashes) > 1:
            raise ValueError(f"configuration {config_id} has unstable result hashes")
        row: dict[str, object] = dict(zip(GROUP_FIELDS, key, strict=True))
        row["success_count"] = len(successful)
        row["failure_count"] = len(group) - len(successful)
        row["result_hash"] = next(iter(hashes), "")
        if successful:
            stats = summarize_values([record.query_total_ms for record in successful])
            for name in STAT_NAMES:
                row[f"query_total_ms_{name}"] = stats[name]
        else:
            for name in STAT_NAMES:
                row[f"query_total_ms_{name}"] = ""

        required_setup = (
            setup.dataset_load_ms,
            setup.session_setup_ms,
            setup.tune_ms,
            setup.resident_host_bytes,
            setup.resident_gpu_bytes,
            setup.resident_pinned_bytes,
        )
        if any(value is None for value in required_setup):
            raise ValueError(f"configuration {config_id} has unavailable setup metrics")
        setup_cost = setup.dataset_load_ms + setup.session_setup_ms  # type: ignore[operator]
        resident_total = (
            setup.resident_host_bytes
            + setup.resident_gpu_bytes
            + setup.resident_pinned_bytes
        )  # type: ignore[operator]
        row.update(
            dataset_load_ms=setup.dataset_load_ms,
            session_setup_ms=setup.session_setup_ms,
            tune_ms=setup.tune_ms,
            setup_cost_ms=setup_cost,
            resident_host_bytes=setup.resident_host_bytes,
            resident_gpu_bytes=setup.resident_gpu_bytes,
            resident_pinned_bytes=setup.resident_pinned_bytes,
            resident_total_bytes=resident_total,
        )
        median = row["query_total_ms_median"]
        for request_count in (1, 10, 100):
            row[f"amortized_{request_count}_request{'s' if request_count != 1 else ''}_ms"] = (
                float(median) + setup_cost / request_count if median != "" else ""
            )
        output.append(row)
    return output


def summary_fieldnames() -> list[str]:
    return list(SUMMARY_FIELDS)


def write_summary(path: Path, rows: Sequence[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, rows: Sequence[dict[str, object]]) -> None:
    columns = [
        "scale_factor",
        "config_id",
        "engine",
        "threads",
        "ratio_mode",
        "cpu_ratio",
        "query_total_ms_median",
        "query_total_ms_p95",
        "setup_cost_ms",
        "resident_total_bytes",
        "amortized_10_requests_ms",
    ]
    lines = [
        "# Formal Resident Summary",
        "",
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(row[column]) for column in columns) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize Formal resident request records")
    parser.add_argument("raw_csv", type=Path)
    parser.add_argument("--setups", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    rows = summarize_records(read_requests(args.raw_csv), read_setups(args.setups))
    write_summary(args.output, rows)
    if args.markdown is not None:
        write_markdown(args.markdown, rows)
    print(f"ok groups={len(rows)} output={args.output}")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
