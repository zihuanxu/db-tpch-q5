from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "evaluate_hybrid_model.py"
COMMIT = "a" * 40
MANIFEST = "b" * 64
RESULT_HASH = "c" * 16


def _environment(
    directory: Path,
    *,
    scale_factor: str = "1",
    commit: str = COMMIT,
    dataset: str = "data/tpch_sf1_arrow",
    manifest: str = MANIFEST,
) -> None:
    payload = {
        "git": {"commit": commit},
        "v7_runner": {
            "git_commit": commit,
            "dataset": str((ROOT / dataset).resolve()),
            "matrix_payload": {
                "experiment_id": f"v7-sf{scale_factor}",
                "dataset": {
                    "path": dataset,
                    "scale_factor": scale_factor,
                    "manifest_sha256": manifest,
                    "expected_hash": RESULT_HASH,
                },
            },
        },
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "environment.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _jsonl_config(
    directory: Path,
    name: str,
    *,
    ratio_mode: str,
    selected_ratio: float,
    latencies: list[float],
    result_hash: str = RESULT_HASH,
    dataset: str = "data/tpch_sf1_arrow",
    predicted_ratio: float | None = None,
    realized_ratio: float | None = None,
) -> Path:
    setup = {
        "record_type": "session_setup",
        "status": "ok",
        "engine": "hybrid-arrow",
        "dataset": dataset,
        "ratio_mode": ratio_mode,
        "selected_cpu_ratio": selected_ratio,
    }
    if predicted_ratio is not None:
        setup["predicted_cpu_ratio"] = predicted_ratio
    if realized_ratio is not None:
        setup["realized_cpu_ratio"] = realized_ratio
    rows = [setup]
    rows.extend(
        {
            "record_type": "request",
            "status": "ok",
            "is_warmup": False,
            "query_total_ms": latency,
            "selected_cpu_ratio": selected_ratio,
            "result_hash": result_hash,
        }
        for latency in latencies
    )
    path = directory / f"{name}.jsonl"
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return path


def _jsonl_sweep(directory: Path) -> list[Path]:
    _environment(directory)
    return [
        _jsonl_config(
            directory,
            "hybrid-fixed-r0250",
            ratio_mode="fixed",
            selected_ratio=0.25,
            latencies=[11.0, 13.0],
        ),
        _jsonl_config(
            directory,
            "hybrid-fixed-r0500",
            ratio_mode="fixed",
            selected_ratio=0.5,
            latencies=[7.0, 9.0],
        ),
        _jsonl_config(
            directory,
            "hybrid-fixed-r0750",
            ratio_mode="fixed",
            selected_ratio=0.75,
            latencies=[13.0, 15.0],
        ),
        _jsonl_config(
            directory,
            "hybrid-auto",
            ratio_mode="auto",
            selected_ratio=0.5,
            predicted_ratio=0.46,
            realized_ratio=0.5,
            latencies=[9.0, 11.0],
        ),
    ]


def _evaluate(paths: list[Path]) -> dict[str, object]:
    from scripts.evaluate_hybrid_model import evaluate_inputs

    return evaluate_inputs(paths)


def test_jsonl_evaluation_reports_ratios_curve_regret_and_break_even(
    tmp_path: Path,
) -> None:
    report = _evaluate(_jsonl_sweep(tmp_path))

    assert report["schema_version"] == 1
    assert report["status"] == "ok"
    scale = report["scales"][0]
    assert scale["identity"] == {
        "experiment_id": "v7-sf1",
        "scale_factor": "1",
        "dataset_path": "data/tpch_sf1_arrow",
        "dataset_manifest_sha256": MANIFEST,
        "result_hash": RESULT_HASH,
        "git_commit": COMMIT,
    }
    assert scale["auto"] == {
        "predicted_cpu_ratio": 0.46,
        "selected_cpu_ratio": 0.5,
        "realized_cpu_ratio": 0.5,
        "p50_request_ms": 10.0,
        "sample_count": 2,
    }
    assert scale["best_fixed"] == {
        "cpu_ratio": 0.5,
        "p50_request_ms": 8.0,
        "sample_count": 2,
    }
    assert scale["regret_ms"] == 2.0
    assert scale["regret_percent"] == 25.0
    assert scale["fixed_curve"] == [
        {"cpu_ratio": 0.25, "p50_request_ms": 12.0, "sample_count": 2},
        {"cpu_ratio": 0.5, "p50_request_ms": 8.0, "sample_count": 2},
        {"cpu_ratio": 0.75, "p50_request_ms": 14.0, "sample_count": 2},
    ]
    assert scale["break_even"] == {
        "definition": "fixed-curve segments crossing auto p50 request latency",
        "status": "observed",
        "ratio_intervals": [[0.25, 0.5], [0.5, 0.75]],
    }


def test_summary_csv_uses_setup_provenance_for_auto_ratios(tmp_path: Path) -> None:
    _environment(tmp_path)
    fields = [
        "experiment_id",
        "scale_factor",
        "config_id",
        "engine",
        "ratio_mode",
        "cpu_ratio",
        "success_count",
        "result_hash",
        "query_total_ms_median",
    ]
    rows = [
        {
            "experiment_id": "v7-sf1",
            "scale_factor": "1",
            "config_id": "hybrid-fixed-r0250",
            "engine": "hybrid-arrow",
            "ratio_mode": "fixed",
            "cpu_ratio": "0.25",
            "success_count": "10",
            "result_hash": RESULT_HASH,
            "query_total_ms_median": "8.0",
        },
        {
            "experiment_id": "v7-sf1",
            "scale_factor": "1",
            "config_id": "hybrid-auto",
            "engine": "hybrid-arrow",
            "ratio_mode": "auto",
            "cpu_ratio": "0.5",
            "success_count": "10",
            "result_hash": RESULT_HASH,
            "query_total_ms_median": "10.0",
        },
    ]
    with (tmp_path / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    setup_fields = [
        "experiment_id",
        "scale_factor",
        "dataset_path",
        "config_id",
        "engine",
        "ratio_mode",
        "status",
        "selected_cpu_ratio",
        "predicted_cpu_ratio",
        "realized_cpu_ratio",
    ]
    setups = [
        {
            "experiment_id": "v7-sf1",
            "scale_factor": "1",
            "dataset_path": "data/tpch_sf1_arrow",
            "config_id": "hybrid-fixed-r0250",
            "engine": "hybrid-arrow",
            "ratio_mode": "fixed",
            "status": "ok",
            "selected_cpu_ratio": "0.25",
            "predicted_cpu_ratio": "",
            "realized_cpu_ratio": "",
        },
        {
            "experiment_id": "v7-sf1",
            "scale_factor": "1",
            "dataset_path": "data/tpch_sf1_arrow",
            "config_id": "hybrid-auto",
            "engine": "hybrid-arrow",
            "ratio_mode": "auto",
            "status": "ok",
            "selected_cpu_ratio": "0.5",
            "predicted_cpu_ratio": "0.45",
            "realized_cpu_ratio": "0.5",
        },
    ]
    with (tmp_path / "setups.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=setup_fields)
        writer.writeheader()
        writer.writerows(setups)

    report = _evaluate([tmp_path / "summary.csv"])

    scale = report["scales"][0]
    assert scale["auto"]["predicted_cpu_ratio"] == 0.45
    assert scale["auto"]["realized_cpu_ratio"] == 0.5
    assert scale["auto"]["p50_request_ms"] == 10.0
    assert scale["best_fixed"]["p50_request_ms"] == 8.0
    assert scale["regret_percent"] == 25.0


def test_jsonl_infers_auto_from_calibration_markers_not_predicted_ratio(
    tmp_path: Path,
) -> None:
    _environment(tmp_path)
    fixed = _jsonl_config(
        tmp_path,
        "hybrid-fixed",
        ratio_mode="fixed",
        selected_ratio=0.25,
        predicted_ratio=0.25,
        latencies=[8.0],
    )
    auto = _jsonl_config(
        tmp_path,
        "hybrid-auto",
        ratio_mode="auto",
        selected_ratio=0.5,
        predicted_ratio=0.45,
        realized_ratio=0.5,
        latencies=[9.0],
    )
    for path in (fixed, auto):
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        rows[0].pop("ratio_mode")
        if path == auto:
            rows[0]["hybrid_model_version"] = "hybrid-cost-v1-batch-v1"
        path.write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )

    report = _evaluate([fixed, auto])

    scale = report["scales"][0]
    assert scale["best_fixed"]["cpu_ratio"] == 0.25
    assert scale["auto"]["predicted_cpu_ratio"] == 0.45


def test_jsonl_discovers_environment_above_runner_logs_directory(
    tmp_path: Path,
) -> None:
    _environment(tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    fixed = _jsonl_config(
        logs,
        "hybrid-fixed.stdout",
        ratio_mode="fixed",
        selected_ratio=0.25,
        latencies=[8.0],
    )
    auto = _jsonl_config(
        logs,
        "hybrid-auto.stdout",
        ratio_mode="auto",
        selected_ratio=0.5,
        predicted_ratio=0.45,
        realized_ratio=0.5,
        latencies=[9.0],
    )

    report = _evaluate([fixed, auto])

    assert report["scales"][0]["identity"]["git_commit"] == COMMIT


@pytest.mark.parametrize(
    ("setup_mode", "setup_ratio", "predicted", "realized", "message"),
    [
        ("fixed", "0.25", "", "", "setup selection"),
        ("auto", "0.5", "0.45", "0.5", "ratio_mode"),
    ],
)
def test_summary_rejects_fixed_identity_not_matching_setup(
    tmp_path: Path,
    setup_mode: str,
    setup_ratio: str,
    predicted: str,
    realized: str,
    message: str,
) -> None:
    _environment(tmp_path)
    summary_fields = [
        "experiment_id",
        "scale_factor",
        "config_id",
        "engine",
        "ratio_mode",
        "cpu_ratio",
        "success_count",
        "result_hash",
        "query_total_ms_median",
    ]
    summary_rows = [
        ["v7-sf1", "1", "fixed", "hybrid-arrow", "fixed", "0.5", "10", RESULT_HASH, "8"],
        ["v7-sf1", "1", "auto", "hybrid-arrow", "auto", "0.5", "10", RESULT_HASH, "9"],
    ]
    with (tmp_path / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(summary_fields)
        writer.writerows(summary_rows)
    setup_fields = [
        "experiment_id",
        "scale_factor",
        "dataset_path",
        "config_id",
        "engine",
        "ratio_mode",
        "status",
        "selected_cpu_ratio",
        "predicted_cpu_ratio",
        "realized_cpu_ratio",
    ]
    setup_rows = [
        [
            "v7-sf1",
            "1",
            "data/tpch_sf1_arrow",
            "fixed",
            "hybrid-arrow",
            setup_mode,
            "ok",
            setup_ratio,
            predicted,
            realized,
        ],
        [
            "v7-sf1",
            "1",
            "data/tpch_sf1_arrow",
            "auto",
            "hybrid-arrow",
            "auto",
            "ok",
            "0.5",
            "0.45",
            "0.5",
        ],
    ]
    with (tmp_path / "setups.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(setup_fields)
        writer.writerows(setup_rows)

    with pytest.raises(ValueError, match=message):
        _evaluate([tmp_path / "summary.csv"])


@pytest.mark.parametrize("missing", ["auto", "fixed"])
def test_evaluation_rejects_missing_required_candidate(
    tmp_path: Path, missing: str
) -> None:
    paths = _jsonl_sweep(tmp_path)
    paths = [path for path in paths if missing not in path.name]

    with pytest.raises(ValueError, match=missing):
        _evaluate(paths)


def test_evaluation_rejects_mixed_result_hashes(tmp_path: Path) -> None:
    paths = _jsonl_sweep(tmp_path)
    bad = _jsonl_config(
        tmp_path,
        "hybrid-fixed-bad-hash",
        ratio_mode="fixed",
        selected_ratio=0.875,
        latencies=[12.0],
        result_hash="d" * 16,
    )

    with pytest.raises(ValueError, match="result hash"):
        _evaluate([*paths, bad])


def test_evaluation_rejects_mixed_commits_for_same_scale(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    _environment(left, commit="a" * 40)
    _environment(right, commit="f" * 40)
    fixed = _jsonl_config(
        left,
        "hybrid-fixed",
        ratio_mode="fixed",
        selected_ratio=0.5,
        latencies=[8.0],
    )
    auto = _jsonl_config(
        right,
        "hybrid-auto",
        ratio_mode="auto",
        selected_ratio=0.5,
        predicted_ratio=0.5,
        realized_ratio=0.5,
        latencies=[9.0],
    )

    with pytest.raises(ValueError, match="git commit"):
        _evaluate([fixed, auto])


def test_evaluation_rejects_setup_dataset_not_matching_environment(
    tmp_path: Path,
) -> None:
    paths = _jsonl_sweep(tmp_path)
    bad = _jsonl_config(
        tmp_path,
        "hybrid-fixed-bad-dataset",
        ratio_mode="fixed",
        selected_ratio=0.875,
        latencies=[12.0],
        dataset="data/not-the-recorded-dataset",
    )

    with pytest.raises(ValueError, match="dataset"):
        _evaluate([*paths, bad])


def test_break_even_is_null_when_fixed_curve_never_crosses_auto(tmp_path: Path) -> None:
    _environment(tmp_path)
    paths = [
        _jsonl_config(
            tmp_path,
            "hybrid-fixed-r0250",
            ratio_mode="fixed",
            selected_ratio=0.25,
            latencies=[12.0],
        ),
        _jsonl_config(
            tmp_path,
            "hybrid-fixed-r0750",
            ratio_mode="fixed",
            selected_ratio=0.75,
            latencies=[14.0],
        ),
        _jsonl_config(
            tmp_path,
            "hybrid-auto",
            ratio_mode="auto",
            selected_ratio=0.5,
            predicted_ratio=0.45,
            realized_ratio=0.5,
            latencies=[10.0],
        ),
    ]

    break_even = _evaluate(paths)["scales"][0]["break_even"]

    assert break_even["status"] == "not_observed"
    assert break_even["ratio_intervals"] is None


def test_cli_writes_json_csv_and_markdown(tmp_path: Path) -> None:
    inputs = _jsonl_sweep(tmp_path / "inputs")
    json_path = tmp_path / "out" / "model.json"
    csv_path = tmp_path / "out" / "model.csv"
    markdown_path = tmp_path / "out" / "model.md"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            *(str(path) for path in inputs),
            "--json-out",
            str(json_path),
            "--csv-out",
            str(csv_path),
            "--markdown-out",
            str(markdown_path),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(json_path.read_text(encoding="utf-8"))["status"] == "ok"
    with csv_path.open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["scale_factor"] == "1"
    assert row["regret_ms"] == "2.0"
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "Hybrid Model Offline Evaluation" in markdown
    assert "| 1 |" in markdown
