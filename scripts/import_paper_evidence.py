#!/usr/bin/env python3

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import io
import json
import math
from pathlib import Path

try:
    from scripts.evidence_bundle import audit
    from scripts.v7_evidence_bundle import audit_bundle as audit_v7
    from scripts.validate_claim_ledger import validate_ledger
except ModuleNotFoundError:
    from evidence_bundle import audit
    from v7_evidence_bundle import audit_bundle as audit_v7
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

REQUIRED_V7_CLAIM_STATES = {
    "C010": "VERIFIED",
    "C011": "VERIFIED",
    "C012": "VERIFIED",
    "C013": "VERIFIED",
    "C014": "VERIFIED",
    "C015": "REJECTED",
    "C016": "VERIFIED",
    "C017": "VERIFIED",
    "C018": "VERIFIED",
    "C019": "VERIFIED",
    "C020": "REJECTED",
    "C021": "VERIFIED",
}

CROSS_SCALE_IDENTITY_FIELDS = (
    "git_commit",
    "gpu_uuid",
    "gpu_name",
    "gpu_driver",
    "session_cli_sha256",
    "cudf_env",
    "cudf_details",
)
MODEL_ABS_TOLERANCE = 1e-9


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_claim_states(ledger_path: Path, repo_root: Path) -> None:
    ledger = validate_ledger(ledger_path, repo_root)
    if not ledger.ok:
        raise ValueError("invalid claim ledger: " + "; ".join(ledger.errors))
    states = {claim.claim_id: claim.state for claim in ledger.claims}
    for claim_id, expected in REQUIRED_V7_CLAIM_STATES.items():
        if states.get(claim_id) != expected:
            raise ValueError(f"{claim_id} must be {expected} before V7 paper import")


def _v7_bundle(
    evidence_dir: Path, expected_scale: str
) -> tuple[dict[str, object], list[dict[str, str]], list[dict[str, str]], str]:
    audit_result = audit_v7(evidence_dir)
    if audit_result.get("ok") is not True:
        raise ValueError(f"SF{expected_scale} V7 evidence bundle audit failed")
    manifest = json.loads((evidence_dir / "manifest.json").read_text(encoding="utf-8"))
    if (
        manifest.get("status") != "complete"
        or str(manifest.get("dataset", {}).get("scale_factor")) != expected_scale
    ):
        raise ValueError(f"SF{expected_scale} V7 evidence identity is invalid")
    with (evidence_dir / "summary.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != 18 or any(row.get("lifecycle") != "resident" for row in rows):
        raise ValueError(f"SF{expected_scale} V7 summary must contain 18 resident rows")
    with (evidence_dir / "setups.csv").open(newline="", encoding="utf-8") as handle:
        setups = list(csv.DictReader(handle))
    if len(setups) != 18 or any(
        row.get("lifecycle") != "resident" or row.get("status") != "ok"
        for row in setups
    ):
        raise ValueError(f"SF{expected_scale} V7 setups must contain 18 successful resident rows")
    manifest_sha = (evidence_dir / "manifest.sha256").read_text(
        encoding="ascii"
    ).split()[0]
    return manifest, rows, setups, manifest_sha


def _best_row(
    rows: list[dict[str, str]], engine: str, *, ratio_mode: str | None = None
) -> dict[str, str]:
    matches = [row for row in rows if row["engine"] == engine]
    if ratio_mode is not None:
        matches = [row for row in matches if row["ratio_mode"] == ratio_mode]
    if not matches:
        raise ValueError(f"missing V7 summary rows for {engine}/{ratio_mode or '*'}")
    return min(matches, key=lambda row: float(row["query_total_ms_median"]))


def _only_row(rows: list[dict[str, str]], config_id: str) -> dict[str, str]:
    matches = [row for row in rows if row["config_id"] == config_id]
    if len(matches) != 1:
        raise ValueError(f"expected one V7 summary row for {config_id}")
    return matches[0]


def _model_scale(model: dict[str, object], scale: str) -> dict[str, object]:
    scales = model.get("scales")
    if not isinstance(scales, list):
        raise ValueError("hybrid model scales are missing")
    matches = [
        item
        for item in scales
        if isinstance(item, dict)
        and isinstance(item.get("identity"), dict)
        and str(item["identity"].get("scale_factor")) == scale
    ]
    if len(matches) != 1:
        raise ValueError(f"hybrid model must contain one SF{scale} result")
    return matches[0]


def _model_number(
    value: object,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"hybrid model {label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"hybrid model {label} must be finite")
    if minimum is not None and number < minimum:
        raise ValueError(f"hybrid model {label} is below {minimum}")
    if maximum is not None and number > maximum:
        raise ValueError(f"hybrid model {label} is above {maximum}")
    return number


def _model_sample_count(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"hybrid model {label}.sample_count must be a positive integer")
    return value


def _require_close(actual: float, expected: float, label: str) -> None:
    if not math.isclose(
        actual,
        expected,
        rel_tol=1e-12,
        abs_tol=MODEL_ABS_TOLERANCE,
    ):
        raise ValueError(f"hybrid model {label} disagrees with V7 evidence")


def _validate_cross_scale_identity(
    sf1_identity: dict[str, object], sf10_identity: dict[str, object]
) -> None:
    for field in CROSS_SCALE_IDENTITY_FIELDS:
        if sf1_identity.get(field) != sf10_identity.get(field):
            raise ValueError(f"SF1 and SF10 V7 evidence {field} values disagree")


def _validate_model_scale(
    model_scale: dict[str, object],
    manifest: dict[str, object],
    rows: list[dict[str, str]],
    setups: list[dict[str, str]],
) -> None:
    identity = model_scale.get("identity")
    manifest_identity = manifest.get("identity")
    dataset = manifest.get("dataset")
    oracle = manifest.get("oracle")
    if not all(isinstance(value, dict) for value in (identity, manifest_identity, dataset, oracle)):
        raise ValueError("model identity cannot be compared with evidence")
    assert isinstance(identity, dict)
    assert isinstance(manifest_identity, dict)
    assert isinstance(dataset, dict)
    assert isinstance(oracle, dict)
    expected = {
        "experiment_id": manifest.get("experiment_id"),
        "git_commit": manifest_identity.get("git_commit"),
        "dataset_manifest_sha256": dataset.get("manifest_sha256"),
        "result_hash": oracle.get("result_hash"),
        "scale_factor": str(dataset.get("scale_factor")),
    }
    if any(identity.get(key) != value for key, value in expected.items()):
        raise ValueError("hybrid model identity does not match V7 evidence")
    if model_scale.get("status") != "ok":
        raise ValueError("hybrid model scale status must be ok")
    if model_scale.get("regret_percent_status") != "measured":
        raise ValueError("hybrid model regret_percent_status must be measured")
    auto = model_scale.get("auto")
    best_fixed = model_scale.get("best_fixed")
    if not isinstance(auto, dict) or not isinstance(best_fixed, dict):
        raise ValueError("hybrid model results are incomplete")
    auto_row = _best_row(rows, "hybrid-arrow", ratio_mode="auto")
    fixed_row = _best_row(rows, "hybrid-arrow", ratio_mode="fixed")
    auto_selected = _model_number(
        auto.get("selected_cpu_ratio"), "auto.selected_cpu_ratio", minimum=0.0, maximum=1.0
    )
    auto_realized = _model_number(
        auto.get("realized_cpu_ratio"), "auto.realized_cpu_ratio", minimum=0.0, maximum=1.0
    )
    auto_predicted = _model_number(
        auto.get("predicted_cpu_ratio"),
        "auto.predicted_cpu_ratio",
        minimum=0.0,
        maximum=1.0,
    )
    auto_p50 = _model_number(auto.get("p50_request_ms"), "auto.p50_request_ms", minimum=0.0)
    fixed_ratio = _model_number(
        best_fixed.get("cpu_ratio"), "best_fixed.cpu_ratio", minimum=0.0, maximum=1.0
    )
    fixed_p50 = _model_number(
        best_fixed.get("p50_request_ms"), "best_fixed.p50_request_ms", minimum=0.0
    )
    if auto_p50 <= 0.0 or fixed_p50 <= 0.0:
        raise ValueError("hybrid model p50_request_ms values must be positive")
    auto_count = _model_sample_count(auto.get("sample_count"), "auto")
    fixed_count = _model_sample_count(best_fixed.get("sample_count"), "best_fixed")

    _require_close(auto_selected, float(auto_row["cpu_ratio"]), "auto.selected_cpu_ratio")
    _require_close(auto_realized, auto_selected, "auto.realized_cpu_ratio")
    _require_close(auto_p50, float(auto_row["query_total_ms_median"]), "auto.p50_request_ms")
    _require_close(fixed_ratio, float(fixed_row["cpu_ratio"]), "best_fixed.cpu_ratio")
    _require_close(
        fixed_p50,
        float(fixed_row["query_total_ms_median"]),
        "best_fixed.p50_request_ms",
    )
    if auto_count != int(auto_row["success_count"]):
        raise ValueError("hybrid model auto.sample_count disagrees with V7 evidence")
    if fixed_count != int(fixed_row["success_count"]):
        raise ValueError("hybrid model best_fixed.sample_count disagrees with V7 evidence")

    setup_matches = [
        row for row in setups if row.get("config_id") == auto_row.get("config_id")
    ]
    if len(setup_matches) != 1:
        raise ValueError("hybrid model auto setup provenance is missing")
    auto_setup = setup_matches[0]
    if (
        auto_setup.get("ratio_mode") != "auto"
        or auto_setup.get("hybrid_provenance_status") != "measured"
        or auto_setup.get("hybrid_model_version") != "hybrid-cost-v1-batch-v1"
        or auto_setup.get("cpu_calibration_requests") != "1"
        or auto_setup.get("gpu_calibration_requests") != "1"
    ):
        raise ValueError("hybrid model auto setup provenance is invalid")
    _require_close(
        auto_predicted,
        float(auto_setup["predicted_cpu_ratio"]),
        "auto.predicted_cpu_ratio",
    )
    _require_close(
        auto_selected,
        float(auto_setup["selected_cpu_ratio"]),
        "auto.selected_cpu_ratio setup provenance",
    )
    _require_close(
        auto_realized,
        float(auto_setup["realized_cpu_ratio"]),
        "auto.realized_cpu_ratio setup provenance",
    )
    calibration_rows = _model_number(
        int(auto_setup["calibration_rows"]),
        "auto setup calibration_rows",
        minimum=1.0,
    )
    tune_ms = _model_number(
        float(auto_setup["tune_ms"]), "auto setup tune_ms", minimum=0.0
    )
    session_setup_ms = _model_number(
        float(auto_setup["session_setup_ms"]),
        "auto setup session_setup_ms",
        minimum=0.0,
    )
    if calibration_rows <= 0.0 or tune_ms > session_setup_ms:
        raise ValueError("hybrid model auto setup timing provenance is invalid")

    fixed_rows = sorted(
        [
            row
            for row in rows
            if row["engine"] == "hybrid-arrow" and row["ratio_mode"] == "fixed"
        ],
        key=lambda row: float(row["cpu_ratio"]),
    )
    curve = model_scale.get("fixed_curve")
    if not isinstance(curve, list) or len(curve) != len(fixed_rows):
        raise ValueError("hybrid model fixed_curve does not cover the fixed sweep")
    for position, (entry, row) in enumerate(zip(curve, fixed_rows, strict=True)):
        if not isinstance(entry, dict):
            raise ValueError(f"hybrid model fixed_curve[{position}] must be an object")
        ratio = _model_number(
            entry.get("cpu_ratio"),
            f"fixed_curve[{position}].cpu_ratio",
            minimum=0.0,
            maximum=1.0,
        )
        p50 = _model_number(
            entry.get("p50_request_ms"),
            f"fixed_curve[{position}].p50_request_ms",
            minimum=0.0,
        )
        sample_count = _model_sample_count(
            entry.get("sample_count"), f"fixed_curve[{position}]"
        )
        _require_close(ratio, float(row["cpu_ratio"]), f"fixed_curve[{position}].cpu_ratio")
        _require_close(
            p50,
            float(row["query_total_ms_median"]),
            f"fixed_curve[{position}].p50_request_ms",
        )
        if sample_count != int(row["success_count"]):
            raise ValueError(
                f"hybrid model fixed_curve[{position}].sample_count disagrees with V7 evidence"
            )

    regret_ms = _model_number(model_scale.get("regret_ms"), "regret_ms")
    regret_percent = _model_number(model_scale.get("regret_percent"), "regret_percent")
    expected_regret_ms = auto_p50 - fixed_p50
    expected_regret_percent = expected_regret_ms / fixed_p50 * 100.0
    _require_close(regret_ms, expected_regret_ms, "regret_ms")
    _require_close(regret_percent, expected_regret_percent, "regret_percent")


def _macro_values(
    label: str,
    manifest: dict[str, object],
    rows: list[dict[str, str]],
    manifest_sha: str,
    model_scale: dict[str, object],
) -> dict[str, str]:
    correctness = manifest["correctness"]
    assert isinstance(correctness, dict)
    counts = correctness["counts"]
    assert isinstance(counts, dict)
    cpu = _best_row(rows, "cpu-specialized")
    acero = _best_row(rows, "arrow-acero")
    cudf = _only_row(rows, "cudf")
    copy = _only_row(rows, "gpu-copy")
    managed = _only_row(rows, "gpu-managed")
    mapped = _only_row(rows, "gpu-mapped")
    fixed = _best_row(rows, "hybrid-arrow", ratio_mode="fixed")
    auto_row = _best_row(rows, "hybrid-arrow", ratio_mode="auto")
    auto = model_scale["auto"]
    assert isinstance(auto, dict)

    values = {
        f"VSeven{label}ExperimentId": str(manifest["experiment_id"]),
        f"VSeven{label}ManifestSha": manifest_sha,
        f"VSeven{label}ManifestShort": manifest_sha[:12],
        f"VSeven{label}ResultHash": str(correctness["expected_hash"]),
        f"VSeven{label}MeasuredRuns": str(counts["measured"]),
        f"VSeven{label}WarmupRuns": str(counts["warmups"]),
        f"VSeven{label}Configurations": str(counts["configurations"]),
    }
    for name, row in (
        ("CpuBest", cpu),
        ("AceroBest", acero),
        ("CuDF", cudf),
        ("GpuCopy", copy),
        ("GpuManaged", managed),
        ("GpuMapped", mapped),
        ("HybridBest", fixed),
        ("HybridAuto", auto_row),
    ):
        values.update(
            {
                f"{label}{name}Median": _number(row, "query_total_ms_median"),
                f"{label}{name}Pninetyfive": _number(row, "query_total_ms_p95"),
                f"{label}{name}Setup": _number(row, "setup_cost_ms"),
                f"{label}{name}AmortizedOne": _number(row, "amortized_1_request_ms"),
                f"{label}{name}AmortizedTen": _number(row, "amortized_10_requests_ms"),
                f"{label}{name}AmortizedHundred": _number(
                    row, "amortized_100_requests_ms"
                ),
            }
        )
    values.update(
        {
            f"{label}CpuBestThreads": cpu["threads"],
            f"{label}AceroBestThreads": acero["threads"],
            f"{label}HybridBestRatio": f"{float(fixed['cpu_ratio']):.3f}",
            f"{label}HybridAutoPredictedRatio": f"{float(auto['predicted_cpu_ratio']):.3f}",
            f"{label}HybridAutoSelectedRatio": f"{float(auto['selected_cpu_ratio']):.3f}",
            f"{label}HybridAutoRegretPercent": f"{float(model_scale['regret_percent']):.2f}",
        }
    )
    return values


def import_v7_evidence(
    sf1_dir: Path,
    sf10_dir: Path,
    model_path: Path,
    ledger_path: Path,
    output_dir: Path,
    repo_root: Path,
) -> Path:
    _require_claim_states(ledger_path, repo_root)
    sf1_manifest, sf1_rows, sf1_setups, sf1_sha = _v7_bundle(sf1_dir, "1")
    sf10_manifest, sf10_rows, sf10_setups, sf10_sha = _v7_bundle(sf10_dir, "10")
    model = json.loads(model_path.read_text(encoding="utf-8"))
    if model.get("schema_version") != 1 or model.get("status") != "ok":
        raise ValueError("hybrid model is not a complete version-1 result")
    sf1_model = _model_scale(model, "1")
    sf10_model = _model_scale(model, "10")
    _validate_model_scale(sf1_model, sf1_manifest, sf1_rows, sf1_setups)
    _validate_model_scale(sf10_model, sf10_manifest, sf10_rows, sf10_setups)
    sf1_identity = sf1_manifest["identity"]
    sf10_identity = sf10_manifest["identity"]
    assert isinstance(sf1_identity, dict) and isinstance(sf10_identity, dict)
    _validate_cross_scale_identity(sf1_identity, sf10_identity)

    values = {
        "VSevenGitCommitShort": str(sf1_identity["git_commit"])[:12],
        "VSevenGpuName": str(sf1_identity["gpu_name"]),
        "VSevenGpuDriver": str(sf1_identity["gpu_driver"]),
        "VSevenModelSha": _sha256(model_path),
    }
    values.update(_macro_values("SfOne", sf1_manifest, sf1_rows, sf1_sha, sf1_model))
    values.update(_macro_values("SfTen", sf10_manifest, sf10_rows, sf10_sha, sf10_model))
    lines = [
        "% Generated by scripts/import_paper_evidence.py --sf1/--sf10. Do not edit.",
        f"% sf1_experiment_id={sf1_manifest['experiment_id']}",
        f"% sf1_manifest_sha256={sf1_sha}",
        f"% sf10_experiment_id={sf10_manifest['experiment_id']}",
        f"% sf10_manifest_sha256={sf10_sha}",
        f"% model_sha256={_sha256(model_path)}",
    ]
    lines.extend(
        rf"\newcommand{{\{name}}}{{{value}}}" for name, value in values.items()
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "results.tex"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


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
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--evidence", type=Path)
    source.add_argument("--sf1", type=Path)
    parser.add_argument("--sf10", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    if args.evidence is not None:
        if args.sf10 is not None or args.model is not None:
            parser.error("--sf10/--model require --sf1")
        output = import_evidence(
            args.evidence.resolve(),
            args.ledger.resolve(),
            args.output.resolve(),
            args.repo_root.resolve(),
        )
    else:
        if args.sf10 is None or args.model is None:
            parser.error("--sf1 requires --sf10 and --model")
        output = import_v7_evidence(
            args.sf1.resolve(),
            args.sf10.resolve(),
            args.model.resolve(),
            args.ledger.resolve(),
            args.output.resolve(),
            args.repo_root.resolve(),
        )
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
