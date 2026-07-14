#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Mapping, Sequence

try:
    from scripts.parse_ncu_csv import parse_ncu_csv
    from scripts.parse_nsys_stats import parse_nsys_csv
except ModuleNotFoundError:
    from parse_ncu_csv import parse_ncu_csv  # type: ignore[no-redef]
    from parse_nsys_stats import parse_nsys_csv  # type: ignore[no-redef]


RAW_FIELDS = [
    "scale_factor", "config_id", "engine", "threads", "ratio_mode",
    "cpu_ratio", "gpu_ratio", "request_index", "status", "result_hash",
    "query_total_ms", "cpu_scan_ms", "gpu_kernel_ms", "h2d_ms", "d2h_ms",
    "overlap_wall_ms", "input_lineitem_rows", "matched_lineitem_rows",
    "cpu_input_rows", "gpu_input_rows", "h2d_bytes", "d2h_bytes",
    "mapped_remote_read_bytes", "throughput_rows_per_second",
    "selected_cpu_ratio",
]

SETUP_FIELDS = [
    "scale_factor", "config_id", "engine", "threads", "ratio_mode",
    "cpu_ratio", "gpu_ratio", "dataset_load_ms", "session_setup_ms",
    "plan_build_ms", "host_staging_ms", "allocation_ms", "initial_h2d_ms",
    "tune_ms", "resident_host_bytes", "resident_gpu_bytes",
    "resident_pinned_bytes", "selected_cpu_ratio", "predicted_cpu_ratio",
    "realized_cpu_ratio",
]

PROFILE_FIELDS = [
    "scale_factor",
    "engine",
    "cpu_ratio",
    "gpu_ratio",
    "nsys_kernel_time_ns",
    "ncu_kernel_duration_ns",
    "dram_read_bytes",
    "dram_throughput_pct",
    "sm_throughput_pct",
    "achieved_occupancy_pct",
]


def normalize_csv(source: Path, output: Path) -> None:
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        if fieldnames is None:
            raise ValueError(f"CSV has no header: {source}")
        rows = list(reader)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def compact_csv(source: Path, output: Path, fields: Sequence[str]) -> None:
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [field for field in fields if field not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"missing CSV fields: {', '.join(missing)}")
        rows = [{field: row[field] for field in fields} for row in reader]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def compact_resident(source_dir: Path, output_dir: Path) -> None:
    compact_csv(source_dir / "raw.csv", output_dir / "raw.csv", RAW_FIELDS)
    compact_csv(source_dir / "setup.csv", output_dir / "setup.csv", SETUP_FIELDS)
    normalize_csv(source_dir / "summary.csv", output_dir / "summary.csv")


def _profile_row(
    identity: Mapping[str, object],
    nsys_kernel_time_ns: object,
    metrics: Mapping[str, object],
) -> dict[str, object]:
    return {
        "scale_factor": identity["scale_factor"],
        "engine": identity["engine"],
        "cpu_ratio": identity["cpu_ratio"],
        "gpu_ratio": identity["gpu_ratio"],
        "nsys_kernel_time_ns": nsys_kernel_time_ns,
        "ncu_kernel_duration_ns": metrics["duration"],
        "dram_read_bytes": metrics["dram_read_bytes"],
        "dram_throughput_pct": metrics["dram_throughput"],
        "sm_throughput_pct": metrics["sm_throughput"],
        "achieved_occupancy_pct": metrics["achieved_occupancy"],
    }


def _safe_path(root: Path, relative: object, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"{label} must be a non-empty relative path")
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"unsafe {label}: {relative}")
    resolved_root = root.resolve()
    resolved = (resolved_root / candidate).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"unsafe {label}: {relative}") from exc
    return resolved


def _load_object(path: Path, label: str) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object: {path}")
    return value


def _raw_profile_row(
    profile: Mapping[str, object], root: Path
) -> dict[str, object]:
    identity = profile
    nsys = profile.get("nsys")
    ncu = profile.get("ncu")
    if not isinstance(nsys, dict) or not isinstance(ncu, dict):
        raise ValueError("raw profiler entry is missing nsys or ncu metadata")

    nsys_metadata_path = _safe_path(root, nsys.get("metadata_path"), "Nsys metadata path")
    _load_object(nsys_metadata_path, "Nsys metadata")
    nsys_rows = parse_nsys_csv(nsys_metadata_path.parent / "stats_cuda_gpu_kern_sum.csv")
    nsys_kernel_time_ns = sum(
        row["total_ns"]
        for row in nsys_rows
        if "q5_kernel" in str(row.get("name", ""))
        and isinstance(row.get("total_ns"), (int, float))
    )
    if isinstance(nsys_kernel_time_ns, float) and nsys_kernel_time_ns.is_integer():
        nsys_kernel_time_ns = int(nsys_kernel_time_ns)
    if not isinstance(nsys_kernel_time_ns, (int, float)) or nsys_kernel_time_ns <= 0:
        raise ValueError("raw profiler entry has no positive q5 kernel total")

    ncu_metadata_path = _safe_path(root, ncu.get("metadata_path"), "NCU metadata path")
    _load_object(ncu_metadata_path, "NCU metadata")
    ncu_dir = ncu_metadata_path.parent
    selected = _load_object(ncu_dir / "selected_metrics.json", "selected metrics")
    parsed = parse_ncu_csv(ncu_dir / "report.csv", "q5_kernel")
    parsed_metrics = parsed.get("metrics")
    if not isinstance(parsed_metrics, dict):
        raise ValueError(f"NCU report has no metrics: {ncu_dir / 'report.csv'}")
    metrics: dict[str, object] = {}
    for role in PROFILE_FIELDS[5:]:
        source_role = {
            "ncu_kernel_duration_ns": "duration",
            "dram_read_bytes": "dram_read_bytes",
            "dram_throughput_pct": "dram_throughput",
            "sm_throughput_pct": "sm_throughput",
            "achieved_occupancy_pct": "achieved_occupancy",
        }[role]
        metric_name = selected.get(source_role)
        if not isinstance(metric_name, str) or metric_name not in parsed_metrics:
            raise ValueError(f"selected NCU metric is missing for role {source_role}")
        metrics[source_role] = parsed_metrics[metric_name]
    return _profile_row(identity, nsys_kernel_time_ns, metrics)


def profile_rows(payload: Mapping[str, object]) -> list[dict[str, object]]:
    profiles = payload.get("profiles")
    if not isinstance(profiles, list):
        raise ValueError("profiler summary must contain a profiles array")
    rows: list[dict[str, object]] = []
    for profile in profiles:
        if not isinstance(profile, dict):
            raise ValueError("each profiler entry must be an object")
        identity = profile.get("identity")
        ncu = profile.get("ncu")
        nsys = profile.get("nsys")
        if not isinstance(identity, dict) or not isinstance(ncu, dict) or not isinstance(nsys, dict):
            raise ValueError("profiler entry is missing identity, ncu, or nsys")
        metrics = ncu.get("selected_metric_values")
        if not isinstance(metrics, dict):
            raise ValueError("profiler entry is missing selected NCU metrics")
        rows.append(_profile_row(identity, nsys["q5_kernel_total_time_ns"], metrics))
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export compact Q5 results")
    commands = parser.add_subparsers(dest="command", required=True)
    resident = commands.add_parser("resident")
    resident.add_argument("--source-dir", type=Path, required=True)
    resident.add_argument("--output-dir", type=Path, required=True)
    profiler = commands.add_parser("profiler")
    profiler.add_argument("--input", type=Path, required=True)
    profiler.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "resident":
        compact_resident(args.source_dir, args.output_dir)
        return 0
    payload = _load_object(args.input, "profiler input")
    raw_profiles = payload.get("profiles")
    if isinstance(raw_profiles, list) and raw_profiles and isinstance(raw_profiles[0], dict):
        first_profile = raw_profiles[0]
        if "identity" not in first_profile and isinstance(first_profile.get("nsys"), dict):
            rows = [_raw_profile_row(profile, args.input.parent) for profile in raw_profiles]
        else:
            rows = profile_rows(payload)
    else:
        rows = profile_rows(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=PROFILE_FIELDS, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
