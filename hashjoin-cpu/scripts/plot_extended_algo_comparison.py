#!/usr/bin/env python3
"""Plot extended algorithm comparison results.

Input is the summary CSV produced by scripts/run_extended_algo_comparison.sh.
The script creates:
  - throughput curve by |R|
  - extra-space ratio curve by |R|

Rows with status not in {OK, PARTIAL} or missing metrics are skipped.
"""

from __future__ import annotations

import argparse
import csv
import html
import math
import os
import sys
from collections import defaultdict


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot PRVJ extended algorithm comparison CSV."
    )
    parser.add_argument("summary_csv", help="Summary CSV from run_extended_algo_comparison.sh")
    parser.add_argument(
        "--out-dir",
        default="plots",
        help="Directory for generated figures [plots]",
    )
    parser.add_argument(
        "--prefix",
        default="extended_algo_comparison",
        help="Output filename prefix [extended_algo_comparison]",
    )
    parser.add_argument(
        "--format",
        default="png",
        choices=("png", "pdf", "svg"),
        help="Output figure format [png]",
    )
    return parser.parse_args()


def load_rows(path: str) -> list[dict[str, str]]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def row_label(row: dict[str, str]) -> str:
    variant = row.get("variant") or row.get("algo") or "unknown"
    if variant.startswith("VJ_pw"):
        return variant.replace("_pw", " payload-width=")
    if variant == "PRVJ_best":
        pw = row.get("payload_width", "")
        rb = row.get("radix_bits", "")
        suffix = []
        if pw:
            suffix.append(f"payload-width={pw}")
        if rb:
            suffix.append(f"radix={rb}")
        return "PRVJ best" + (f" ({', '.join(suffix)})" if suffix else "")
    if variant == "sort-merge":
        return "sort-merge"
    return variant


def as_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        val = float(value)
    except ValueError:
        return None
    if not math.isfinite(val):
        return None
    return val


def group_metric(rows: list[dict[str, str]], metric: str) -> dict[str, list[tuple[int, float]]]:
    grouped: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for row in rows:
        if row.get("status") not in ("OK", "PARTIAL"):
            continue
        x_val = as_float(row.get("r_exp"))
        y_val = as_float(row.get(metric))
        if x_val is None or y_val is None:
            continue
        grouped[row_label(row)].append((int(x_val), y_val))

    for values in grouped.values():
        values.sort(key=lambda item: item[0])
    return grouped


def plot_metric_svg(rows: list[dict[str, str]], metric: str, ylabel: str, title: str, path: str) -> bool:
    grouped = group_metric(rows, metric)
    if not grouped:
        print(f"[WARN] No plottable rows for {metric}; skipped {path}", file=sys.stderr)
        return False

    width = 960
    height = 560
    left = 82
    right = 28
    top = 54
    bottom = 72
    plot_w = width - left - right
    plot_h = height - top - bottom
    colors = [
        "#1f77b4", "#d62728", "#2ca02c", "#9467bd",
        "#8c564b", "#e377c2", "#7f7f7f", "#17becf",
    ]

    xs = [x for values in grouped.values() for x, _ in values]
    ys = [y for values in grouped.values() for _, y in values]
    min_x = min(xs)
    max_x = max(xs)
    min_y = 0.0
    max_y = max(ys)
    if max_x == min_x:
        max_x += 1
    if max_y <= min_y:
        max_y = min_y + 1.0
    max_y *= 1.08

    def sx(x: float) -> float:
        return left + (x - min_x) * plot_w / (max_x - min_x)

    def sy(y: float) -> float:
        return top + plot_h - (y - min_y) * plot_h / (max_y - min_y)

    x_ticks = list(range(min_x, max_x + 1))
    y_ticks = [max_y * i / 5.0 for i in range(6)]

    out: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width / 2:.1f}" y="26" text-anchor="middle" font-family="sans-serif" font-size="18">{html.escape(title)}</text>',
    ]

    for tick in x_ticks:
        x = sx(tick)
        out.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + plot_h}" stroke="#e5e5e5" stroke-width="1"/>')
        out.append(f'<text x="{x:.1f}" y="{top + plot_h + 22}" text-anchor="middle" font-family="sans-serif" font-size="12">{tick}</text>')
    for tick in y_ticks:
        y = sy(tick)
        out.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_w}" y2="{y:.1f}" stroke="#e5e5e5" stroke-width="1"/>')
        out.append(f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" font-family="sans-serif" font-size="12">{tick:.3g}</text>')

    out.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_h}" stroke="#333" stroke-width="1.2"/>')
    out.append(f'<line x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}" stroke="#333" stroke-width="1.2"/>')
    out.append(f'<text x="{left + plot_w / 2:.1f}" y="{height - 22}" text-anchor="middle" font-family="sans-serif" font-size="14">log2(|R|)</text>')
    out.append(f'<text x="18" y="{top + plot_h / 2:.1f}" transform="rotate(-90 18 {top + plot_h / 2:.1f})" text-anchor="middle" font-family="sans-serif" font-size="14">{html.escape(ylabel)}</text>')

    legend_x = left + 12
    legend_y = top + 18
    for idx, (label, values) in enumerate(sorted(grouped.items())):
        color = colors[idx % len(colors)]
        points = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in values)
        out.append(f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{points}"/>')
        for x, y in values:
            out.append(f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="3.5" fill="{color}"/>')
        y = legend_y + idx * 18
        out.append(f'<line x1="{legend_x}" y1="{y}" x2="{legend_x + 18}" y2="{y}" stroke="{color}" stroke-width="2"/>')
        out.append(f'<text x="{legend_x + 24}" y="{y + 4}" font-family="sans-serif" font-size="12">{html.escape(label)}</text>')

    out.append("</svg>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print(f"[INFO] Wrote {path}")
    return True


def plot_metric(rows: list[dict[str, str]], metric: str, ylabel: str, title: str, path: str) -> bool:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        if path.lower().endswith(".svg"):
            print("[WARN] matplotlib is unavailable; using built-in SVG fallback.", file=sys.stderr)
            return plot_metric_svg(rows, metric, ylabel, title, path)
        print("[ERROR] matplotlib is required for plotting.", file=sys.stderr)
        return False

    grouped = group_metric(rows, metric)
    if not grouped:
        print(f"[WARN] No plottable rows for {metric}; skipped {path}", file=sys.stderr)
        return False

    fig, ax = plt.subplots(figsize=(10, 6))
    for label, values in sorted(grouped.items()):
        xs = [x for x, _ in values]
        ys = [y for _, y in values]
        ax.plot(xs, ys, marker="o", linewidth=1.8, markersize=4, label=label)

    ax.set_xlabel("log2(|R|)")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(True, which="both", linestyle=":", linewidth=0.7)
    ax.legend(loc="best", fontsize="small")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)
    print(f"[INFO] Wrote {path}")
    return True


def main() -> int:
    args = parse_args()
    rows = load_rows(args.summary_csv)
    os.makedirs(args.out_dir, exist_ok=True)

    throughput_path = os.path.join(
        args.out_dir, f"{args.prefix}_throughput.{args.format}"
    )
    space_path = os.path.join(
        args.out_dir, f"{args.prefix}_space.{args.format}"
    )

    ok = True
    ok &= plot_metric(
        rows,
        "throughput_mtps",
        "Throughput (million tuples/s)",
        "Throughput vs. build relation size",
        throughput_path,
    )
    ok &= plot_metric(
        rows,
        "extra_space_ratio",
        "Extra space / raw input bytes",
        "Vector extra-space ratio vs. build relation size",
        space_path,
    )

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
