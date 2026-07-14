#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class SourceIdentity:
    experiment_id: str
    scale_factor: str
    dataset_path: str
    dataset_absolute_path: str
    dataset_manifest_sha256: str
    expected_hash: str
    git_commit: str


@dataclass(frozen=True)
class Observation:
    identity: SourceIdentity
    config_id: str
    ratio_mode: str
    cpu_ratio: float
    p50_request_ms: float
    sample_count: int
    result_hash: str
    predicted_cpu_ratio: float | None = None
    selected_cpu_ratio: float | None = None
    realized_cpu_ratio: float | None = None


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value.strip()


def _float(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _ratio(value: object, label: str) -> float:
    result = _float(value, label)
    if result < 0.0 or result > 1.0:
        raise ValueError(f"{label} must be between 0 and 1")
    return result


def _positive_count(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a positive integer")
    try:
        result = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a positive integer") from exc
    if str(value).strip() != str(result) or result < 1:
        raise ValueError(f"{label} must be a positive integer")
    return result


def _load_environment(directory: Path) -> SourceIdentity:
    candidates = [directory / "environment.json", directory.parent / "environment.json"]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        searched = ", ".join(str(candidate) for candidate in candidates)
        raise ValueError(f"cannot find V7 environment identity; searched: {searched}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        runner = payload["v7_runner"]
        matrix = runner["matrix_payload"]
        dataset = matrix["dataset"]
        git_commit = _text(payload["git"]["commit"], "environment git commit")
        runner_commit = _text(runner["git_commit"], "runner git commit")
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read V7 environment identity from {path}: {exc}") from exc
    if git_commit != runner_commit:
        raise ValueError("environment git commit does not match runner git commit")
    manifest = _text(dataset.get("manifest_sha256"), "dataset manifest SHA256")
    expected_hash = _text(dataset.get("expected_hash"), "dataset expected result hash")
    if len(manifest) != 64 or any(character not in "0123456789abcdef" for character in manifest):
        raise ValueError("dataset manifest SHA256 must be 64 lowercase hex characters")
    if len(expected_hash) != 16 or any(
        character not in "0123456789abcdef" for character in expected_hash
    ):
        raise ValueError("dataset expected result hash must be 16 lowercase hex characters")
    return SourceIdentity(
        experiment_id=_text(matrix.get("experiment_id"), "experiment_id"),
        scale_factor=_text(dataset.get("scale_factor"), "scale_factor"),
        dataset_path=_text(dataset.get("path"), "dataset path"),
        dataset_absolute_path=_text(runner.get("dataset"), "runner dataset path"),
        dataset_manifest_sha256=manifest,
        expected_hash=expected_hash,
        git_commit=git_commit,
    )


def _check_record_identity(record: dict[str, object], identity: SourceIdentity) -> None:
    checks = (
        ("experiment_id", identity.experiment_id, "experiment"),
        ("scale_factor", identity.scale_factor, "scale factor"),
    )
    for field, expected, label in checks:
        if field in record and str(record[field]) != expected:
            raise ValueError(f"record {label} does not match environment")
    raw_dataset = record.get("dataset_path", record.get("dataset"))
    if raw_dataset is not None and str(raw_dataset) not in {
        identity.dataset_path,
        identity.dataset_absolute_path,
    }:
        raise ValueError("record dataset does not match environment dataset")


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"{path}:{line_number} must contain a JSON object")
                rows.append(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSONL in {path}:{exc.lineno}: {exc.msg}") from exc
    if not rows:
        raise ValueError(f"JSONL input is empty: {path}")
    return rows


def _mode_from_setup(setup: dict[str, object]) -> str:
    raw_mode = setup.get("ratio_mode")
    if raw_mode is None:
        auto_markers = (
            "hybrid_model_version",
            "calibration_rows",
            "realized_cpu_ratio",
            "selected_batch_boundary_rows",
        )
        raw_mode = (
            "auto"
            if any(setup.get(field) is not None for field in auto_markers)
            else "fixed"
        )
    mode = str(raw_mode)
    if mode not in {"fixed", "auto"}:
        raise ValueError("hybrid ratio_mode must be fixed or auto")
    return mode


def _observation(
    *,
    identity: SourceIdentity,
    config_id: str,
    mode: str,
    cpu_ratio: object,
    latency: object,
    sample_count: object,
    result_hash: object,
    setup: dict[str, object],
) -> Observation:
    setup_mode = _mode_from_setup(setup)
    if setup_mode != mode:
        raise ValueError(f"{config_id} ratio_mode does not match setup")
    ratio = _ratio(cpu_ratio, f"{config_id} cpu ratio")
    p50 = _float(latency, f"{config_id} p50 request latency")
    if p50 < 0.0:
        raise ValueError(f"{config_id} p50 request latency must be nonnegative")
    count = _positive_count(sample_count, f"{config_id} sample count")
    hash_value = _text(result_hash, f"{config_id} result hash")
    if hash_value != identity.expected_hash:
        raise ValueError(f"{config_id} result hash does not match environment")
    setup_selected = _ratio(
        setup.get("selected_cpu_ratio"), f"{config_id} setup selected_cpu_ratio"
    )
    if not math.isclose(ratio, setup_selected, abs_tol=1e-9):
        raise ValueError(f"{config_id} CPU ratio does not match setup selection")
    predicted: float | None = None
    selected: float | None = None
    realized: float | None = None
    if mode == "auto":
        predicted = _ratio(setup.get("predicted_cpu_ratio"), "auto predicted_cpu_ratio")
        selected = setup_selected
        realized = _ratio(setup.get("realized_cpu_ratio"), "auto realized_cpu_ratio")
        if not math.isclose(selected, realized, abs_tol=1e-9):
            raise ValueError("auto selected and realized CPU ratios do not match")
        if not math.isclose(ratio, selected, abs_tol=1e-9):
            raise ValueError("auto request CPU ratio does not match setup selection")
    return Observation(
        identity=identity,
        config_id=config_id,
        ratio_mode=mode,
        cpu_ratio=ratio,
        p50_request_ms=p50,
        sample_count=count,
        result_hash=hash_value,
        predicted_cpu_ratio=predicted,
        selected_cpu_ratio=selected,
        realized_cpu_ratio=realized,
    )


def _load_jsonl(path: Path) -> list[Observation]:
    identity = _load_environment(path.parent)
    rows = _read_jsonl(path)
    setups = [
        row
        for row in rows
        if row.get("record_type") in {"session_setup", "setup"}
    ]
    if len(setups) != 1:
        raise ValueError(f"{path} requires exactly one session setup record")
    setup = setups[0]
    _check_record_identity(setup, identity)
    if setup.get("status") != "ok":
        raise ValueError(f"{path} setup is not successful")
    if setup.get("engine") != "hybrid-arrow":
        raise ValueError(f"{path} is not a hybrid-arrow session")
    mode = _mode_from_setup(setup)
    selected = _ratio(setup.get("selected_cpu_ratio"), "setup selected_cpu_ratio")
    requests = [
        row
        for row in rows
        if row.get("record_type") == "request"
        and row.get("status") == "ok"
        and row.get("is_warmup") is False
    ]
    if not requests:
        raise ValueError(f"{path} has no successful measured requests")
    for request in requests:
        _check_record_identity(request, identity)
        request_ratio = _ratio(
            request.get("selected_cpu_ratio"), "request selected_cpu_ratio"
        )
        if not math.isclose(request_ratio, selected, abs_tol=1e-9):
            raise ValueError("request selected CPU ratio does not match setup")
    hashes = {_text(row.get("result_hash"), "request result hash") for row in requests}
    if len(hashes) != 1:
        raise ValueError(f"{path} contains unstable result hashes")
    latencies = [_float(row.get("query_total_ms"), "request latency") for row in requests]
    return [
        _observation(
            identity=identity,
            config_id=str(setup.get("config_id", path.stem)),
            mode=mode,
            cpu_ratio=selected,
            latency=statistics.median(latencies),
            sample_count=len(latencies),
            result_hash=next(iter(hashes)),
            setup=setup,
        )
    ]


def _read_csv(path: Path) -> list[dict[str, object]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise ValueError(f"CSV has no header: {path}")
            return [dict(row) for row in reader]
    except OSError as exc:
        raise ValueError(f"cannot read CSV {path}: {exc}") from exc


def _setup_rows(directory: Path) -> dict[str, dict[str, object]]:
    candidates = [directory / "setups.csv", directory / "setup.csv"]
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        raise ValueError(f"summary/raw input requires setups.csv or setup.csv in {directory}")
    setups: dict[str, dict[str, object]] = {}
    for row in _read_csv(path):
        config_id = _text(row.get("config_id"), "setup config_id")
        if config_id in setups:
            raise ValueError(f"duplicate setup config_id: {config_id}")
        setups[config_id] = row
    return setups


def _validate_setup(
    setup: dict[str, object], identity: SourceIdentity, config_id: str
) -> None:
    _check_record_identity(setup, identity)
    if setup.get("status") != "ok":
        raise ValueError(f"setup for {config_id} is not successful")
    if setup.get("engine") != "hybrid-arrow":
        raise ValueError(f"setup for {config_id} is not hybrid-arrow")


def _load_summary_csv(path: Path) -> list[Observation]:
    identity = _load_environment(path.parent)
    setups = _setup_rows(path.parent)
    output: list[Observation] = []
    for row in _read_csv(path):
        if row.get("engine") != "hybrid-arrow":
            continue
        _check_record_identity(row, identity)
        config_id = _text(row.get("config_id"), "summary config_id")
        if config_id not in setups:
            raise ValueError(f"summary configuration {config_id} has no setup")
        setup = setups[config_id]
        _validate_setup(setup, identity, config_id)
        mode = _text(row.get("ratio_mode"), f"{config_id} ratio_mode")
        if mode not in {"fixed", "auto"}:
            raise ValueError(f"{config_id} ratio_mode must be fixed or auto")
        output.append(
            _observation(
                identity=identity,
                config_id=config_id,
                mode=mode,
                cpu_ratio=row.get("cpu_ratio"),
                latency=row.get("query_total_ms_median"),
                sample_count=row.get("success_count"),
                result_hash=row.get("result_hash"),
                setup=setup,
            )
        )
    if not output:
        raise ValueError(f"summary has no successful hybrid observations: {path}")
    return output


def _load_raw_csv(path: Path) -> list[Observation]:
    identity = _load_environment(path.parent)
    setups = _setup_rows(path.parent)
    groups: dict[str, list[dict[str, object]]] = {}
    for row in _read_csv(path):
        if row.get("engine") != "hybrid-arrow" or row.get("status") != "ok":
            continue
        _check_record_identity(row, identity)
        config_id = _text(row.get("config_id"), "request config_id")
        groups.setdefault(config_id, []).append(row)
    output: list[Observation] = []
    for config_id, rows in sorted(groups.items()):
        if config_id not in setups:
            raise ValueError(f"raw configuration {config_id} has no setup")
        setup = setups[config_id]
        _validate_setup(setup, identity, config_id)
        mode = _text(rows[0].get("ratio_mode"), f"{config_id} ratio_mode")
        hashes = {_text(row.get("result_hash"), "request result hash") for row in rows}
        ratios = {_ratio(row.get("cpu_ratio"), "request cpu_ratio") for row in rows}
        if len(hashes) != 1:
            raise ValueError(f"{config_id} contains unstable result hashes")
        if len(ratios) != 1:
            raise ValueError(f"{config_id} contains unstable CPU ratios")
        output.append(
            _observation(
                identity=identity,
                config_id=config_id,
                mode=mode,
                cpu_ratio=next(iter(ratios)),
                latency=statistics.median(
                    _float(row.get("query_total_ms"), "request latency") for row in rows
                ),
                sample_count=len(rows),
                result_hash=next(iter(hashes)),
                setup=setup,
            )
        )
    if not output:
        raise ValueError(f"raw CSV has no successful hybrid requests: {path}")
    return output


def _load_input(path: Path) -> list[Observation]:
    if path.is_dir():
        summary = path / "summary.csv"
        raw = path / "raw.csv"
        if summary.is_file():
            return _load_summary_csv(summary)
        if raw.is_file():
            return _load_raw_csv(raw)
        raise ValueError(f"input directory has neither summary.csv nor raw.csv: {path}")
    if not path.is_file():
        raise ValueError(f"input does not exist: {path}")
    if path.suffix.lower() == ".jsonl":
        return _load_jsonl(path)
    if path.suffix.lower() == ".csv":
        rows = _read_csv(path)
        if not rows:
            raise ValueError(f"CSV input is empty: {path}")
        if "query_total_ms_median" in rows[0]:
            return _load_summary_csv(path)
        if "query_total_ms" in rows[0]:
            return _load_raw_csv(path)
    raise ValueError(f"unsupported input format: {path}")


def _same_identity(left: SourceIdentity, right: SourceIdentity) -> None:
    comparisons = (
        (left.experiment_id, right.experiment_id, "experiment"),
        (left.dataset_path, right.dataset_path, "dataset path"),
        (
            left.dataset_manifest_sha256,
            right.dataset_manifest_sha256,
            "dataset manifest",
        ),
        (left.expected_hash, right.expected_hash, "expected result hash"),
        (left.git_commit, right.git_commit, "git commit"),
    )
    for first, second, label in comparisons:
        if first != second:
            raise ValueError(f"mixed {label} values for one scale factor")


def _break_even(
    curve: list[dict[str, object]], auto_p50: float
) -> dict[str, object]:
    definition = "fixed-curve segments crossing auto p50 request latency"
    if len(curve) < 2:
        return {
            "definition": definition,
            "status": "insufficient_fixed_curve",
            "ratio_intervals": None,
        }
    intervals: list[list[float]] = []
    for left, right in zip(curve, curve[1:]):
        left_ratio = float(left["cpu_ratio"])
        right_ratio = float(right["cpu_ratio"])
        left_delta = float(left["p50_request_ms"]) - auto_p50
        right_delta = float(right["p50_request_ms"]) - auto_p50
        if math.isclose(left_delta, 0.0, abs_tol=1e-12):
            point = [left_ratio, left_ratio]
            if point not in intervals:
                intervals.append(point)
        if left_delta * right_delta < 0.0:
            intervals.append([left_ratio, right_ratio])
    final = curve[-1]
    if math.isclose(float(final["p50_request_ms"]) - auto_p50, 0.0, abs_tol=1e-12):
        point = [float(final["cpu_ratio"]), float(final["cpu_ratio"])]
        if point not in intervals:
            intervals.append(point)
    return {
        "definition": definition,
        "status": "observed" if intervals else "not_observed",
        "ratio_intervals": intervals or None,
    }


def _scale_sort_key(scale: str) -> tuple[int, float | str]:
    try:
        return (0, float(scale))
    except ValueError:
        return (1, scale)


def evaluate_inputs(paths: Sequence[Path]) -> dict[str, object]:
    if not paths:
        raise ValueError("at least one input is required")
    observations = [observation for path in paths for observation in _load_input(Path(path))]
    by_scale: dict[str, list[Observation]] = {}
    for observation in observations:
        by_scale.setdefault(observation.identity.scale_factor, []).append(observation)
    scales: list[dict[str, object]] = []
    for scale_factor in sorted(by_scale, key=_scale_sort_key):
        group = by_scale[scale_factor]
        identity = group[0].identity
        for observation in group[1:]:
            _same_identity(identity, observation.identity)
        hashes = {observation.result_hash for observation in group}
        if len(hashes) != 1:
            raise ValueError(f"mixed result hash values for scale factor {scale_factor}")
        auto_candidates = [row for row in group if row.ratio_mode == "auto"]
        fixed_candidates = [row for row in group if row.ratio_mode == "fixed"]
        if len(auto_candidates) != 1:
            raise ValueError(
                f"scale factor {scale_factor} requires exactly one hybrid auto candidate"
            )
        if not fixed_candidates:
            raise ValueError(
                f"scale factor {scale_factor} requires at least one hybrid fixed candidate"
            )
        ratios = [row.cpu_ratio for row in fixed_candidates]
        if len(ratios) != len(set(ratios)):
            raise ValueError(f"scale factor {scale_factor} has duplicate fixed CPU ratios")
        auto = auto_candidates[0]
        curve = [
            {
                "cpu_ratio": row.cpu_ratio,
                "p50_request_ms": row.p50_request_ms,
                "sample_count": row.sample_count,
            }
            for row in sorted(fixed_candidates, key=lambda row: row.cpu_ratio)
        ]
        best = min(fixed_candidates, key=lambda row: (row.p50_request_ms, row.cpu_ratio))
        regret_ms = auto.p50_request_ms - best.p50_request_ms
        if best.p50_request_ms == 0.0:
            regret_percent: float | None = None
            regret_percent_status = "unavailable_zero_best_fixed_latency"
        else:
            regret_percent = regret_ms / best.p50_request_ms * 100.0
            regret_percent_status = "measured"
        scales.append(
            {
                "identity": {
                    "experiment_id": identity.experiment_id,
                    "scale_factor": identity.scale_factor,
                    "dataset_path": identity.dataset_path,
                    "dataset_manifest_sha256": identity.dataset_manifest_sha256,
                    "result_hash": next(iter(hashes)),
                    "git_commit": identity.git_commit,
                },
                "status": "ok",
                "auto": {
                    "predicted_cpu_ratio": auto.predicted_cpu_ratio,
                    "selected_cpu_ratio": auto.selected_cpu_ratio,
                    "realized_cpu_ratio": auto.realized_cpu_ratio,
                    "p50_request_ms": auto.p50_request_ms,
                    "sample_count": auto.sample_count,
                },
                "best_fixed": {
                    "cpu_ratio": best.cpu_ratio,
                    "p50_request_ms": best.p50_request_ms,
                    "sample_count": best.sample_count,
                },
                "regret_ms": regret_ms,
                "regret_percent": regret_percent,
                "regret_percent_status": regret_percent_status,
                "fixed_curve": curve,
                "break_even": _break_even(curve, auto.p50_request_ms),
            }
        )
    return {"schema_version": 1, "status": "ok", "scales": scales}


CSV_FIELDS = [
    "scale_factor",
    "experiment_id",
    "dataset_path",
    "dataset_manifest_sha256",
    "result_hash",
    "git_commit",
    "auto_predicted_cpu_ratio",
    "auto_selected_cpu_ratio",
    "auto_realized_cpu_ratio",
    "auto_p50_request_ms",
    "best_fixed_cpu_ratio",
    "best_fixed_p50_request_ms",
    "regret_ms",
    "regret_percent",
    "regret_percent_status",
    "break_even_status",
    "break_even_ratio_intervals_json",
    "fixed_curve_json",
]


def write_json(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _csv_rows(report: dict[str, object]) -> Iterable[dict[str, object]]:
    for scale in report["scales"]:  # type: ignore[index]
        identity = scale["identity"]
        auto = scale["auto"]
        best = scale["best_fixed"]
        break_even = scale["break_even"]
        yield {
            **identity,
            "auto_predicted_cpu_ratio": auto["predicted_cpu_ratio"],
            "auto_selected_cpu_ratio": auto["selected_cpu_ratio"],
            "auto_realized_cpu_ratio": auto["realized_cpu_ratio"],
            "auto_p50_request_ms": auto["p50_request_ms"],
            "best_fixed_cpu_ratio": best["cpu_ratio"],
            "best_fixed_p50_request_ms": best["p50_request_ms"],
            "regret_ms": scale["regret_ms"],
            "regret_percent": scale["regret_percent"],
            "regret_percent_status": scale["regret_percent_status"],
            "break_even_status": break_even["status"],
            "break_even_ratio_intervals_json": json.dumps(
                break_even["ratio_intervals"], separators=(",", ":")
            ),
            "fixed_curve_json": json.dumps(scale["fixed_curve"], separators=(",", ":")),
        }


def write_csv(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(_csv_rows(report))


def _display(value: object) -> str:
    return "n/a" if value is None else str(value)


def write_markdown(path: Path, report: dict[str, object]) -> None:
    columns = [
        "SF",
        "auto predicted",
        "auto selected/realized",
        "auto p50 ms",
        "best fixed ratio",
        "best fixed p50 ms",
        "regret ms",
        "regret %",
        "break-even",
    ]
    lines = [
        "# Hybrid Model Offline Evaluation",
        "",
        "Break-even means an observed adjacent fixed-ratio segment crosses the auto p50 latency.",
        "No extrapolation is used; unavailable intervals remain null in JSON and CSV.",
        "",
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for scale in report["scales"]:  # type: ignore[index]
        identity = scale["identity"]
        auto = scale["auto"]
        best = scale["best_fixed"]
        break_even = scale["break_even"]
        intervals = break_even["ratio_intervals"]
        break_even_text = (
            json.dumps(intervals, separators=(",", ":"))
            if intervals is not None
            else f"n/a ({break_even['status']})"
        )
        regret_percent = (
            _display(scale["regret_percent"])
            if scale["regret_percent"] is not None
            else f"n/a ({scale['regret_percent_status']})"
        )
        values = [
            identity["scale_factor"],
            _display(auto["predicted_cpu_ratio"]),
            f"{_display(auto['selected_cpu_ratio'])}/{_display(auto['realized_cpu_ratio'])}",
            auto["p50_request_ms"],
            best["cpu_ratio"],
            best["p50_request_ms"],
            scale["regret_ms"],
            regret_percent,
            break_even_text,
        ]
        lines.append("| " + " | ".join(str(value) for value in values) + " |")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate V7 hybrid auto selection against fixed-ratio sweeps"
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="runner directories, resident JSONL files, raw.csv, or summary.csv files",
    )
    parser.add_argument("--json-out", required=True, type=Path)
    parser.add_argument("--csv-out", required=True, type=Path)
    parser.add_argument("--markdown-out", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    try:
        report = evaluate_inputs(args.inputs)
        write_json(args.json_out, report)
        write_csv(args.csv_out, report)
        write_markdown(args.markdown_out, report)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"ok scales={len(report['scales'])} json={args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
