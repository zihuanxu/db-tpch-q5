#!/usr/bin/env python3

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
from pathlib import Path

try:
    from scripts.evidence_bundle import audit
    from scripts.validate_claim_ledger import validate_ledger
except ModuleNotFoundError:
    from evidence_bundle import audit
    from validate_claim_ledger import validate_ledger


REQUIRED_CLAIM_STATES = {
    "C001": "VERIFIED",
    "C002": "VERIFIED",
    "C003": "VERIFIED",
    "C004": "VERIFIED",
    "C005": "VERIFIED",
    "C006": "VERIFIED",
    "C007": "REJECTED",
    "C008": "VERIFIED",
    "C009": "REJECTED",
}


def _row(
    rows: list[dict[str, str]], engine: str, *, threads: int = 1, cpu_ratio: float = 0.0
) -> dict[str, str]:
    matches = [
        row
        for row in rows
        if row["engine"] == engine
        and int(row["threads"]) == threads
        and float(row["cpu_ratio"]) == cpu_ratio
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one summary row for {engine}/threads={threads}/cpu_ratio={cpu_ratio}"
        )
    return matches[0]


def _number(row: dict[str, str], field: str) -> str:
    return f"{float(row[field]):.3f}"


def import_evidence(
    evidence_dir: Path, ledger_path: Path, output_dir: Path, repo_root: Path
) -> Path:
    ledger = validate_ledger(ledger_path, repo_root)
    if not ledger.ok:
        raise ValueError("invalid claim ledger: " + "; ".join(ledger.errors))
    states = {claim.claim_id: claim.state for claim in ledger.claims}
    for claim_id, expected in REQUIRED_CLAIM_STATES.items():
        if states.get(claim_id) != expected:
            raise ValueError(f"{claim_id} must be {expected} before paper import")

    with contextlib.redirect_stdout(io.StringIO()):
        audit_result = audit(argparse.Namespace(directory=evidence_dir))
    if audit_result != 0:
        raise ValueError("evidence bundle audit failed")

    correctness = json.loads(
        (evidence_dir / "correctness.json").read_text(encoding="utf-8")
    )
    manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))
    if not correctness.get("ok") or manifest.get("status") != "complete":
        raise ValueError("evidence bundle is not complete and correct")
    with (evidence_dir / "summary.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    cpu16 = _row(rows, "cpu-specialized", threads=16, cpu_ratio=1.0)
    acero32 = _row(rows, "arrow-acero", threads=32, cpu_ratio=1.0)
    cudf = _row(rows, "cudf")
    copy = _row(rows, "gpu-copy")
    managed = _row(rows, "gpu-managed")
    mapped = _row(rows, "gpu-mapped")
    hybrid25 = _row(rows, "hybrid-arrow", threads=8, cpu_ratio=0.25)
    hybrid50 = _row(rows, "hybrid-arrow", threads=8, cpu_ratio=0.5)
    hybrid75 = _row(rows, "hybrid-arrow", threads=8, cpu_ratio=0.75)
    manifest_sha = (evidence_dir / "manifest.sha256").read_text(encoding="ascii").split()[0]
    try:
        summary_source = evidence_dir.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError as error:
        raise ValueError("evidence directory must be inside the repository") from error

    values = {
        "FormalExperimentId": manifest["experiment_id"],
        "FormalManifestSha": manifest_sha,
        "FormalManifestShort": manifest_sha[:12],
        "FormalResultHash": correctness["expected_hash"],
        "FormalMeasuredRuns": str(correctness["measured_records"]),
        "FormalWarmupRuns": str(correctness["warmup_records"]),
        "CpuBestThreads": "16",
        "CpuBestQueryMedian": _number(cpu16, "query_total_ms_median"),
        "CpuBestProcessMedian": _number(cpu16, "process_elapsed_ms_median"),
        "AceroBestThreads": "32",
        "AceroBestQueryMedian": _number(acero32, "query_total_ms_median"),
        "AceroBestProcessMedian": _number(acero32, "process_elapsed_ms_median"),
        "CuDFQueryMedian": _number(cudf, "query_total_ms_median"),
        "CuDFProcessMedian": _number(cudf, "process_elapsed_ms_median"),
        "GpuCopyQueryMedian": _number(copy, "query_total_ms_median"),
        "GpuManagedQueryMedian": _number(managed, "query_total_ms_median"),
        "GpuMappedQueryMedian": _number(mapped, "query_total_ms_median"),
        "GpuCopyHtoDMedian": _number(copy, "h2d_ms_median"),
        "GpuManagedHtoDMedian": _number(managed, "h2d_ms_median"),
        "GpuMappedHtoDMedian": _number(mapped, "h2d_ms_median"),
        "GpuCopyKernelMedian": _number(copy, "gpu_kernel_ms_median"),
        "GpuManagedKernelMedian": _number(managed, "gpu_kernel_ms_median"),
        "GpuMappedKernelMedian": _number(mapped, "gpu_kernel_ms_median"),
        "HybridQuarterQueryMedian": _number(hybrid25, "query_total_ms_median"),
        "HybridHalfQueryMedian": _number(hybrid50, "query_total_ms_median"),
        "HybridBestQueryMedian": _number(hybrid75, "query_total_ms_median"),
        "HybridBestOverlapMedian": _number(hybrid75, "overlap_wall_ms_median"),
    }
    lines = [
        "% Generated by scripts/import_paper_evidence.py. Do not edit by hand.",
        f"% experiment_id={manifest['experiment_id']}",
        f"% manifest_sha256={manifest_sha}",
        f"% summary_source={summary_source}/summary.csv",
    ]
    lines.extend(
        rf"\newcommand{{\{name}}}{{{value}}}" for name, value in values.items()
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "results.tex"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Import audited experiment values into LaTeX")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    output = import_evidence(
        args.evidence.resolve(),
        args.ledger.resolve(),
        args.output.resolve(),
        args.repo_root.resolve(),
    )
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
