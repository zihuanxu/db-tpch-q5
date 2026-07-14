#!/usr/bin/env python3
"""Discover device-supported Nsight Compute metrics and capture one Q5 request."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.parse_ncu_csv import parse_ncu_csv
except ModuleNotFoundError:
    from parse_ncu_csv import parse_ncu_csv


METRIC_CANDIDATES = {
    "duration": ("gpu__time_duration.sum",),
    "dram_read_bytes": ("dram__bytes_read.sum",),
    "dram_throughput": ("dram__throughput.avg.pct_of_peak_sustained_elapsed",),
    "sm_throughput": ("sm__throughput.avg.pct_of_peak_sustained_elapsed",),
    "achieved_occupancy": ("sm__warps_active.avg.pct_of_peak_sustained_active",),
}
_METRIC_TOKEN = re.compile(r"^([A-Za-z][A-Za-z0-9_]*__[A-Za-z0-9_.]+)")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _files(output_dir: Path) -> dict[str, dict[str, object]]:
    return {
        path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in sorted(output_dir.iterdir())
        if path.is_file() and path.name != "metadata.json"
    }


def _metric_names(query_output: str) -> set[str]:
    return {
        match.group(1)
        for line in query_output.splitlines()
        if (match := _METRIC_TOKEN.match(line.strip())) is not None
    }


def select_metrics(supported_metrics: set[str]) -> dict[str, str]:
    """Choose the first canonical metric supported by the active device per role."""
    selection: dict[str, str] = {}
    for role, candidates in METRIC_CANDIDATES.items():
        metric = next((candidate for candidate in candidates if candidate in supported_metrics), None)
        if metric is None:
            raise ValueError(f"no device-supported metric for {role}")
        selection[role] = metric
    return selection


def _version(command: list[str]) -> str:
    try:
        result = _run(command)
    except OSError as exc:
        return f"unavailable: {exc}"
    return (result.stdout or result.stderr).strip()


def _gpu_provenance() -> dict[str, str]:
    command = ["nvidia-smi", "--query-gpu=driver_version,uuid", "--format=csv,noheader"]
    try:
        result = _run(command)
    except OSError as exc:
        return {"unavailable": str(exc)}
    if result.returncode != 0 or not result.stdout.strip():
        return {"unavailable": (result.stderr or "nvidia-smi failed").strip()}
    fields = [field.strip() for field in result.stdout.splitlines()[0].split(",")]
    if len(fields) != 2:
        return {"unavailable": f"unexpected nvidia-smi output: {result.stdout.strip()}"}
    return {"driver_version": fields[0], "uuid": fields[1]}


def _write_manifest(output_dir: Path, manifest: dict[str, object]) -> None:
    manifest["files"] = _files(output_dir)
    (output_dir / "metadata.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def collect_ncu(command: list[str], output_dir: Path, q5_kernel: str, metadata: dict) -> dict:
    """Discover metrics, profile one command, and reject failed replay or invalid CSV."""
    if not command:
        raise ValueError("command must not be empty")
    try:
        request_count = command[command.index("--requests") + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError("profiled command must include --requests 1") from exc
    if request_count != "1":
        raise ValueError("profiled command must include --requests 1")
    output_dir.mkdir(parents=True, exist_ok=True)
    query_command = ["ncu", "--query-metrics"]
    try:
        query = _run(query_command)
    except OSError as exc:
        raise RuntimeError(f"ncu metric discovery failed: {exc}") from exc
    if query.returncode != 0:
        raise RuntimeError(f"ncu metric discovery failed: {(query.stderr or query.stdout).strip()}")
    supported = sorted(_metric_names(query.stdout))
    selected = select_metrics(set(supported))
    (output_dir / "supported_metrics.txt").write_text("\n".join(supported) + "\n", encoding="utf-8")
    (output_dir / "selected_metrics.json").write_text(json.dumps(selected, indent=2, sort_keys=True), encoding="utf-8")

    profile_command = [
        "ncu", "--csv", "--target-processes", "all", "--replay-mode", "application",
        "--kernel-name-base", "demangled", "--kernel-name", q5_kernel,
        "--metrics", ",".join(selected.values()), *command,
    ]
    manifest: dict[str, object] = {
        "metadata": metadata,
        "started_at_utc": _utc_now(),
        "query_command": query_command,
        "supported_metrics": supported,
        "selected_metrics": selected,
        "profile_command": profile_command,
        "tool_versions": {"ncu": _version(["ncu", "--version"])},
        "gpu": _gpu_provenance(),
    }
    try:
        profile = _run(profile_command)
    except OSError as exc:
        profile = subprocess.CompletedProcess(profile_command, 127, "", f"launch failed: {exc}")
    (output_dir / "profile.stdout.log").write_text(profile.stdout or "", encoding="utf-8")
    (output_dir / "profile.stderr.log").write_text(profile.stderr or "", encoding="utf-8")
    manifest["return_code"] = profile.returncode
    manifest["replay"] = {"mode": "application", "return_code": profile.returncode, "succeeded": profile.returncode == 0}
    if profile.returncode != 0:
        manifest["finished_at_utc"] = _utc_now()
        _write_manifest(output_dir, manifest)
        raise RuntimeError(f"ncu replay failed: {(profile.stderr or profile.stdout).strip()}")

    report = output_dir / "report.csv"
    report.write_text(profile.stdout, encoding="utf-8")
    parsed = parse_ncu_csv(report, q5_kernel)
    missing = sorted(set(selected.values()) - set(parsed["metrics"]))
    if missing:
        manifest["finished_at_utc"] = _utc_now()
        _write_manifest(output_dir, manifest)
        raise ValueError(f"profile report missing selected metrics: {', '.join(missing)}")
    manifest["report"] = {"path": report.name, "sha256": _sha256(report), "parsed": parsed}
    manifest["finished_at_utc"] = _utc_now()
    _write_manifest(output_dir, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture one Q5 Nsight Compute CSV report")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--kernel-name", default="q5_kernel")
    parser.add_argument("--metadata-json", default="{}")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a profiled command is required after --")
    try:
        metadata = json.loads(args.metadata_json)
    except json.JSONDecodeError as exc:
        parser.error(f"invalid --metadata-json: {exc}")
    collect_ncu(command, args.output_dir, args.kernel_name, metadata)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
