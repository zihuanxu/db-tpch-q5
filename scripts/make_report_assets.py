#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import html
import statistics
from collections import defaultdict
from pathlib import Path


GROUP_FIELDS = ["engine", "region", "date", "threads", "result_hash"]
METRICS = ["build_ms", "h2d_ms", "kernel_ms", "d2h_ms", "scan_ms", "total_ms", "elapsed_ms"]
COLORS = {
    "build_ms": "#4c78a8",
    "h2d_ms": "#f58518",
    "kernel_ms": "#54a24b",
    "d2h_ms": "#e45756",
    "scan_cpu_ms": "#72b7b2",
    "total_ms": "#4c78a8",
}


def parse_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def load_summary(path: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    groups: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    errors: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("status") != "ok":
                errors.append(row)
                continue
            key = tuple(row.get(field, "") for field in GROUP_FIELDS)
            groups[key].append(row)

    rows: list[dict[str, str]] = []
    for key, samples in sorted(groups.items()):
        row = {field: key[index] for index, field in enumerate(GROUP_FIELDS)}
        row["runs"] = str(len(samples))
        for metric in METRICS:
            values = [parse_float(sample.get(metric, "")) for sample in samples]
            row[f"{metric}_median"] = f"{median(values):.6f}"
            row[f"{metric}_mean"] = f"{statistics.mean(values):.6f}"
            row[f"{metric}_min"] = f"{min(values):.6f}"
        rows.append(row)
    return rows, errors


def row_label(row: dict[str, str]) -> str:
    engine = row["engine"]
    threads = row.get("threads", "1")
    if engine == "cpu":
        return f"cpu-t{threads}"
    return engine


def write_summary(path: Path, rows: list[dict[str, str]], errors: list[dict[str, str]]) -> None:
    lines = [
        "# Benchmark Summary",
        "",
        "| engine | threads | runs | result_hash | total_ms median | scan_ms median | h2d/kernel/d2h median | elapsed_ms median |",
        "| --- | --- | --- | --- | ---: | ---: | --- | ---: |",
    ]
    for row in rows:
        lines.append(
            "| {engine} | {threads} | {runs} | `{hash}` | {total} | {scan} | {h2d}/{kernel}/{d2h} | {elapsed} |".format(
                engine=row["engine"],
                threads=row["threads"],
                runs=row["runs"],
                hash=row["result_hash"],
                total=row["total_ms_median"],
                scan=row["scan_ms_median"],
                h2d=row["h2d_ms_median"],
                kernel=row["kernel_ms_median"],
                d2h=row["d2h_ms_median"],
                elapsed=row["elapsed_ms_median"],
            )
        )

    if errors:
        lines.extend(["", "## Unavailable Or Failed Runs", ""])
        for error in errors:
            lines.append(
                f"- `{error.get('engine', '')}` threads={error.get('threads', '')}: {error.get('error', '')}"
            )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def svg_text(x: float, y: float, text: str, size: int = 12, anchor: str = "middle") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" '
        f'font-family="Arial, sans-serif" text-anchor="{anchor}">{html.escape(text)}</text>'
    )


def write_total_time_svg(path: Path, rows: list[dict[str, str]], title: str) -> None:
    width = 920
    height = 460
    left = 80
    right = 30
    top = 58
    bottom = 90
    chart_w = width - left - right
    chart_h = height - top - bottom
    values = [parse_float(row["total_ms_median"]) for row in rows]
    max_value = max(values) if values else 1.0
    max_value = max(max_value, 1e-9)
    bar_gap = 12
    bar_w = max(16, (chart_w - bar_gap * max(len(rows) - 1, 0)) / max(len(rows), 1))

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        svg_text(width / 2, 28, title, 18),
        svg_text(width / 2, 49, "Median total runtime by engine", 13),
        f'<line x1="{left}" y1="{top + chart_h}" x2="{width - right}" y2="{top + chart_h}" stroke="#333"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + chart_h}" stroke="#333"/>',
    ]

    for tick in range(0, 5):
        value = max_value * tick / 4
        y = top + chart_h - chart_h * tick / 4
        parts.append(f'<line x1="{left - 5}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="#e5e5e5"/>')
        parts.append(svg_text(left - 10, y + 4, f"{value:.2f}", 11, "end"))

    for index, row in enumerate(rows):
        value = parse_float(row["total_ms_median"])
        bar_h = chart_h * value / max_value
        x = left + index * (bar_w + bar_gap)
        y = top + chart_h - bar_h
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" fill="{COLORS["total_ms"]}"/>')
        parts.append(svg_text(x + bar_w / 2, y - 5, f"{value:.2f}", 11))
        parts.append(svg_text(x + bar_w / 2, top + chart_h + 20, row_label(row), 11))

    parts.append(svg_text(22, top + chart_h / 2, "ms", 12))
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def breakdown_components(row: dict[str, str]) -> list[tuple[str, float, str]]:
    h2d = parse_float(row["h2d_ms_median"])
    kernel = parse_float(row["kernel_ms_median"])
    d2h = parse_float(row["d2h_ms_median"])
    build = parse_float(row["build_ms_median"])
    scan = parse_float(row["scan_ms_median"])
    if h2d > 0 or kernel > 0 or d2h > 0:
        return [
            ("build", build, COLORS["build_ms"]),
            ("h2d", h2d, COLORS["h2d_ms"]),
            ("kernel", kernel, COLORS["kernel_ms"]),
            ("d2h", d2h, COLORS["d2h_ms"]),
        ]
    return [
        ("build", build, COLORS["build_ms"]),
        ("scan", scan, COLORS["scan_cpu_ms"]),
    ]


def write_breakdown_svg(path: Path, rows: list[dict[str, str]], title: str) -> None:
    width = 920
    height = 500
    left = 80
    right = 30
    top = 68
    bottom = 110
    chart_w = width - left - right
    chart_h = height - top - bottom
    totals = [sum(value for _, value, _ in breakdown_components(row)) for row in rows]
    max_value = max(totals) if totals else 1.0
    max_value = max(max_value, 1e-9)
    bar_gap = 12
    bar_w = max(16, (chart_w - bar_gap * max(len(rows) - 1, 0)) / max(len(rows), 1))

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        svg_text(width / 2, 28, title, 18),
        svg_text(width / 2, 50, "Median execution-time breakdown", 13),
        f'<line x1="{left}" y1="{top + chart_h}" x2="{width - right}" y2="{top + chart_h}" stroke="#333"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + chart_h}" stroke="#333"/>',
    ]

    for tick in range(0, 5):
        value = max_value * tick / 4
        y = top + chart_h - chart_h * tick / 4
        parts.append(f'<line x1="{left - 5}" y1="{y:.1f}" x2="{width - right}" y2="{y:.1f}" stroke="#e5e5e5"/>')
        parts.append(svg_text(left - 10, y + 4, f"{value:.2f}", 11, "end"))

    for index, row in enumerate(rows):
        x = left + index * (bar_w + bar_gap)
        current_y = top + chart_h
        for _, value, color in breakdown_components(row):
            bar_h = chart_h * value / max_value
            current_y -= bar_h
            parts.append(f'<rect x="{x:.1f}" y="{current_y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" fill="{color}"/>')
        total = sum(value for _, value, _ in breakdown_components(row))
        parts.append(svg_text(x + bar_w / 2, current_y - 5, f"{total:.2f}", 11))
        parts.append(svg_text(x + bar_w / 2, top + chart_h + 20, row_label(row), 11))

    legend = [("build", COLORS["build_ms"]), ("scan", COLORS["scan_cpu_ms"]), ("h2d", COLORS["h2d_ms"]), ("kernel", COLORS["kernel_ms"]), ("d2h", COLORS["d2h_ms"])]
    legend_x = left
    legend_y = height - 42
    for label, color in legend:
        parts.append(f'<rect x="{legend_x}" y="{legend_y - 10}" width="12" height="12" fill="{color}"/>')
        parts.append(svg_text(legend_x + 18, legend_y, label, 12, "start"))
        legend_x += 95

    parts.append(svg_text(22, top + chart_h / 2, "ms", 12))
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate report assets from MEMQ5 benchmark CSV")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("results/report_assets"))
    parser.add_argument("--title", default="MEMQ5 Benchmark")
    args = parser.parse_args()

    rows, errors = load_summary(args.csv_path)
    if not rows:
        raise SystemExit("no successful benchmark rows found")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_summary(args.output_dir / "summary.md", rows, errors)
    write_total_time_svg(args.output_dir / "total_time.svg", rows, args.title)
    write_breakdown_svg(args.output_dir / "time_breakdown.svg", rows, args.title)

    print(f"wrote {args.output_dir / 'summary.md'}")
    print(f"wrote {args.output_dir / 'total_time.svg'}")
    print(f"wrote {args.output_dir / 'time_breakdown.svg'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
