#!/usr/bin/env python3
"""Parse Nsight Systems CSV exports without depending on the active locale."""

from __future__ import annotations

import csv
import re
from pathlib import Path


def _normalise_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _number(value: str, *, single_separator_decimal: bool = False) -> int | float:
    value = value.strip().replace("\u00a0", "").replace(" ", "")
    if not value:
        return 0
    sign = -1 if value.startswith("-") else 1
    if value[:1] in "+-":
        value = value[1:]
    comma, dot = value.rfind(","), value.rfind(".")
    if comma >= 0 and dot >= 0:
        decimal = max(comma, dot)
        value = value[:decimal].replace(",", "").replace(".", "") + "." + value[decimal + 1 :]
    elif comma >= 0 or dot >= 0:
        marker = "," if comma >= 0 else "."
        groups = value.split(marker)
        if single_separator_decimal and len(groups) == 2:
            value = value.replace(marker, ".")
        elif len(groups) > 2 and all(len(group) == 3 for group in groups[1:]):
            value = "".join(groups)
        elif len(groups) == 2 and len(groups[1]) == 3 and len(groups[0]) <= 3:
            value = "".join(groups)
        else:
            value = value.replace(marker, ".")
    parsed = float(value) * sign
    return int(parsed) if parsed.is_integer() else parsed


def _value(row: dict[str, str], *headers: str) -> str:
    normalized = {_normalise_header(key): value for key, value in row.items() if key is not None}
    for header in headers:
        if header in normalized:
            return normalized[header]
    return ""


def _kind(name: str, report: str) -> str:
    lower = f"{report} {name}".lower()
    if "mem" in lower and "cpy" in lower or "memcpy" in lower:
        return "memcpy"
    if "kern" in lower or "void " in name.lower():
        return "kernel"
    if "nvtx" in lower or "range" in lower:
        return "range"
    return "other"


def parse_nsys_csv(path: Path) -> list[dict[str, object]]:
    """Return normalized rows from one `nsys stats --format csv` export."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        if not sample.strip():
            raise ValueError(f"empty Nsight CSV: {path}")
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(handle, dialect=dialect)
        rows: list[dict[str, object]] = []
        for raw in reader:
            if not raw or not any(value and value.strip() for value in raw.values() if value):
                continue
            range_name = _value(raw, "range", "rangename")
            name = range_name or _value(raw, "operation", "name")
            report = path.stem
            rows.append(
                {
                    "report": report,
                    "name": name,
                    "kind": "range" if range_name else _kind(name, report),
                    "total_ns": _number(_value(raw, "totaltimens", "totaltimens", "durationns")),
                    "average_ns": _number(_value(raw, "averagens", "averagetimens")),
                    "instances": _number(_value(raw, "instances", "calls")),
                    "time_percent": _number(
                        _value(raw, "time", "timepercent"), single_separator_decimal=True
                    ),
                }
            )
    if not rows:
        raise ValueError(f"no data rows in Nsight CSV: {path}")
    return rows


def validate_ranges(rows: list[dict], required: set[str]) -> list[str]:
    """Report required NVTX range names absent from the parsed exports."""
    present = {str(row.get("name", "")) for row in rows if row.get("kind") == "range"}
    return [f"missing required range: {name}" for name in sorted(required - present)]
