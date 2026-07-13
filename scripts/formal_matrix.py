#!/usr/bin/env python3

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any


HASH_RE = re.compile(r"^[0-9a-f]{16}$")
CPU_ENGINES = {"cpu-specialized", "arrow-acero"}
GPU_ENGINES = {"gpu-copy", "gpu-managed", "gpu-mapped", "cudf"}
ALL_ENGINES = CPU_ENGINES | GPU_ENGINES | {"hybrid-arrow"}
ConfigurationKey = tuple[str, str, int, float, float]


def load_matrix(path: Path) -> dict[str, Any]:
    try:
        matrix = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read formal matrix {path}: {exc}") from exc
    if not isinstance(matrix, dict) or matrix.get("schema_version") != 1:
        raise ValueError("formal matrix schema_version must be 1")
    for name in ("experiment_id", "dataset", "query", "scenario", "engines", "protocol"):
        if name not in matrix:
            raise ValueError(f"formal matrix is missing {name}")
    dataset = matrix["dataset"]
    if not isinstance(dataset, dict) or not HASH_RE.fullmatch(
        str(dataset.get("expected_hash", ""))
    ):
        raise ValueError("formal matrix requires a 16-hex expected_hash")
    protocol = matrix["protocol"]
    if (
        not isinstance(protocol, dict)
        or int(protocol.get("warmup", -1)) < 0
        or int(protocol.get("repeat", 0)) <= 0
        or float(protocol.get("timeout_seconds", 0)) <= 0
    ):
        raise ValueError("formal matrix protocol is invalid")
    expected_configuration_keys(matrix)
    return matrix


def expected_configuration_keys(matrix: dict[str, Any]) -> set[ConfigurationKey]:
    scenarios = matrix.get("scenario", {}).get("enabled", [])
    engines = matrix.get("engines", {})
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("formal matrix requires scenarios")
    if not isinstance(engines, dict) or set(engines) - ALL_ENGINES:
        raise ValueError("formal matrix contains unsupported engines")

    keys: set[ConfigurationKey] = set()
    for scenario in scenarios:
        if scenario not in {"cold", "resident"}:
            raise ValueError("formal matrix scenario is invalid")
        for engine, options in engines.items():
            if not isinstance(options, dict):
                raise ValueError(f"formal matrix options for {engine} are invalid")
            threads = options.get("threads", [])
            if not isinstance(threads, list) or not threads or any(
                not isinstance(value, int) or value <= 0 for value in threads
            ):
                raise ValueError(f"formal matrix threads for {engine} are invalid")
            if engine == "hybrid-arrow":
                ratios = options.get("cpu_ratios", [])
                if not isinstance(ratios, list) or not ratios:
                    raise ValueError("formal matrix hybrid ratios are missing")
                for thread in threads:
                    for raw_ratio in ratios:
                        ratio = float(raw_ratio)
                        if not math.isfinite(ratio) or ratio < 0.0 or ratio > 1.0:
                            raise ValueError("formal matrix hybrid ratio is invalid")
                        keys.add((engine, scenario, thread, ratio, 1.0 - ratio))
            else:
                cpu_ratio = 1.0 if engine in CPU_ENGINES else 0.0
                for thread in threads:
                    keys.add((engine, scenario, thread, cpu_ratio, 1.0 - cpu_ratio))
    return keys
