#!/usr/bin/env python3
"""Validate and normalize a single-kernel Nsight Compute CSV report."""

from __future__ import annotations

import csv
import math
import re
from pathlib import Path


def _header(row: dict[str | None, str | None], name: str) -> str:
    for key, value in row.items():
        if key is not None and key.strip().lower() == name.lower():
            return (value or "").strip()
    return ""


def _number(value: str, metric: str) -> float:
    normalized = value.strip()
    if "," in normalized:
        raise ValueError(f"localized/non-C metric value for {metric}: {value!r}")
    try:
        parsed = float(normalized)
    except ValueError as exc:
        raise ValueError(f"non-numeric metric value for {metric}: {value!r}") from exc
    if not normalized or not math.isfinite(parsed):
        raise ValueError(f"non-numeric metric value for {metric}: {value!r}")
    return parsed


def parse_ncu_csv(path: Path, q5_kernel: str) -> dict[str, object]:
    """Return metrics for exactly one kernel whose name includes `q5_kernel`."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        if not sample.strip():
            raise ValueError(f"empty Nsight Compute CSV: {path}")
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.DictReader(handle, dialect=dialect))

    matching = [row for row in rows if q5_kernel in _header(row, "Kernel Name")]
    if not matching:
        raise ValueError(f"no Q5 kernel matching {q5_kernel!r} in {path}")

    kernels = {
        (
            _header(row, "ID"),
            _header(row, "Process ID"),
            _header(row, "Context"),
            _header(row, "Stream"),
            _header(row, "Kernel Name"),
        )
        for row in matching
    }
    if len(kernels) != 1:
        raise ValueError(f"expected exactly one Q5 kernel, found {len(kernels)}")

    kernel_name = next(iter(kernels))[4]
    metrics: dict[str, float] = {}
    for row in matching:
        name = _header(row, "Metric Name")
        if not name:
            raise ValueError(f"missing Metric Name in Nsight Compute CSV: {path}")
        value = _number(_header(row, "Metric Value"), name)
        previous = metrics.get(name)
        if previous is not None and previous != value:
            raise ValueError(f"conflicting metric values for {name}")
        metrics[name] = value
    return {"kernel_name": kernel_name, "metrics": metrics}
