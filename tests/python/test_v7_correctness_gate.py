from __future__ import annotations

import json
from pathlib import Path

from scripts.verify_q5_oracle import result_hash_hex


ROWS = [
    {"nation": "JAPAN", "revenue_1e4": 1900000},
    {"nation": "INDIA", "revenue_1e4": 900000},
]
RESULT_HASH = result_hash_hex([(row["nation"], row["revenue_1e4"]) for row in ROWS])
BASE_BACKENDS = [
    "cpu-specialized",
    "arrow-acero",
    "gpu-copy",
    "gpu-managed",
    "gpu-mapped",
    "hybrid-fixed",
    "cudf",
]
IDENTITY = {
    "experiment_id": "v7-sf10-correctness",
    "scale_factor": "10",
    "dataset_path": "data/tpch_sf10_arrow",
    "dataset_manifest_sha256": "a" * 64,
    "region": "ASIA",
    "date": "1994-01-01",
}


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_matrix(tmp_path: Path, *, hybrid_auto: bool = False) -> Path:
    matrix = tmp_path / "matrix.yml"
    _write_json(
        matrix,
        {
            "schema_version": 1,
            "experiment_id": IDENTITY["experiment_id"],
            "dataset": {
                "path": IDENTITY["dataset_path"],
                "scale_factor": IDENTITY["scale_factor"],
                "manifest_sha256": IDENTITY["dataset_manifest_sha256"],
            },
            "query": {"region": IDENTITY["region"], "date": IDENTITY["date"]},
            "required_backends": BASE_BACKENDS,
            "hybrid_auto": {"enabled": hybrid_auto},
        },
    )
    return matrix


def _write_oracle(tmp_path: Path) -> Path:
    oracle = tmp_path / "oracle.json"
    _write_json(oracle, {"rows": ROWS, "result_hash": RESULT_HASH})
    return oracle


def _write_run(
    directory: Path,
    backend: str,
    *,
    status: str = "ok",
    return_code: int = 0,
    result_hash: str = RESULT_HASH,
    rows: list[dict[str, object]] | None = None,
    identity: dict[str, str] | None = None,
) -> None:
    _write_json(
        directory / f"{backend}.json",
        {
            "schema_version": 1,
            "backend": backend,
            "identity": IDENTITY if identity is None else identity,
            "process": {"status": status, "return_code": return_code},
            "output": {
                "result_hash": result_hash,
                "rows": ROWS if rows is None else rows,
            },
        },
    )


def _write_passing_runs(tmp_path: Path, *, hybrid_auto: bool = False) -> Path:
    directory = tmp_path / "runs"
    directory.mkdir()
    for backend in BASE_BACKENDS + (["hybrid-auto"] if hybrid_auto else []):
        _write_run(directory, backend)
    return directory


def test_compare_rows_reports_ordered_value_mismatch_and_duplicate_nation() -> None:
    from scripts.v7_correctness_gate import compare_rows

    errors = compare_rows(
        ROWS,
        [
            {"nation": "JAPAN", "revenue_1e4": 1900001},
            {"nation": "JAPAN", "revenue_1e4": 900000},
        ],
    )

    assert any("row 0" in error and "revenue_1e4" in error for error in errors)
    assert any("duplicate nation JAPAN" in error for error in errors)


def test_gate_marks_missing_required_backend_failed(tmp_path: Path) -> None:
    from scripts.v7_correctness_gate import verify_correctness

    directory = _write_passing_runs(tmp_path)
    (directory / "gpu-mapped.json").unlink()

    report = verify_correctness(directory, _write_matrix(tmp_path), _write_oracle(tmp_path))

    assert report["ok"] is False
    assert report["backends"]["gpu-mapped"]["status"] == "failed"
    assert "missing run record" in report["backends"]["gpu-mapped"]["errors"]


def test_gate_marks_wrong_hash_failed(tmp_path: Path) -> None:
    from scripts.v7_correctness_gate import verify_correctness

    directory = _write_passing_runs(tmp_path)
    _write_run(directory, "cudf", result_hash="0000000000000000")

    report = verify_correctness(directory, _write_matrix(tmp_path), _write_oracle(tmp_path))

    assert report["ok"] is False
    assert report["backends"]["cudf"]["status"] == "failed"
    assert any(
        "result_hash mismatch" in error
        for error in report["backends"]["cudf"]["errors"]
    )


def test_gate_rejects_oracle_hash_not_derived_from_rows(tmp_path: Path) -> None:
    from scripts.v7_correctness_gate import verify_correctness

    oracle = _write_oracle(tmp_path)
    _write_json(oracle, {"rows": ROWS, "result_hash": "0000000000000000"})

    try:
        verify_correctness(
            _write_passing_runs(tmp_path), _write_matrix(tmp_path), oracle
        )
    except ValueError as exc:
        assert "oracle result_hash does not match rows" in str(exc)
    else:
        raise AssertionError("stale oracle hash was accepted")


def test_gate_rejects_wrong_row_when_hash_is_claimed_equal(tmp_path: Path) -> None:
    from scripts.v7_correctness_gate import verify_correctness

    directory = _write_passing_runs(tmp_path)
    _write_run(
        directory,
        "arrow-acero",
        rows=[
            {"nation": "JAPAN", "revenue_1e4": 1900000},
            {"nation": "INDIA", "revenue_1e4": 899999},
        ],
    )

    report = verify_correctness(directory, _write_matrix(tmp_path), _write_oracle(tmp_path))

    assert report["ok"] is False
    assert report["backends"]["arrow-acero"]["status"] == "failed"
    assert any("row 1" in error for error in report["backends"]["arrow-acero"]["errors"])
    assert any(
        "output result_hash does not match rows" in error
        for error in report["backends"]["arrow-acero"]["errors"]
    )


def test_gate_rejects_duplicate_nation(tmp_path: Path) -> None:
    from scripts.v7_correctness_gate import verify_correctness

    directory = _write_passing_runs(tmp_path)
    _write_run(
        directory,
        "hybrid-fixed",
        rows=[
            {"nation": "JAPAN", "revenue_1e4": 1900000},
            {"nation": "JAPAN", "revenue_1e4": 900000},
        ],
    )

    report = verify_correctness(directory, _write_matrix(tmp_path), _write_oracle(tmp_path))

    assert report["ok"] is False
    assert report["backends"]["hybrid-fixed"]["status"] == "failed"
    assert "duplicate nation JAPAN" in report["backends"]["hybrid-fixed"]["errors"]


def test_gate_marks_failed_process_failed(tmp_path: Path) -> None:
    from scripts.v7_correctness_gate import verify_correctness

    directory = _write_passing_runs(tmp_path)
    _write_run(directory, "gpu-copy", status="error", return_code=1)

    report = verify_correctness(directory, _write_matrix(tmp_path), _write_oracle(tmp_path))

    assert report["ok"] is False
    assert report["backends"]["gpu-copy"] == {
        "status": "failed",
        "errors": ["process status=error return_code=1"],
    }


def test_gate_marks_skipped_gpu_unavailable(tmp_path: Path) -> None:
    from scripts.v7_correctness_gate import verify_correctness

    directory = _write_passing_runs(tmp_path)
    _write_run(directory, "gpu-managed", status="skipped_gpu", return_code=77)

    report = verify_correctness(directory, _write_matrix(tmp_path), _write_oracle(tmp_path))

    assert report["ok"] is False
    assert report["backends"]["gpu-managed"] == {
        "status": "unavailable",
        "errors": ["GPU backend unavailable"],
    }


def test_gate_marks_skipped_hybrid_gpu_unavailable(tmp_path: Path) -> None:
    from scripts.v7_correctness_gate import verify_correctness

    directory = _write_passing_runs(tmp_path, hybrid_auto=True)
    _write_run(directory, "hybrid-fixed", status="skipped_gpu", return_code=77)
    _write_run(directory, "hybrid-auto", status="skipped_gpu", return_code=77)

    report = verify_correctness(
        directory, _write_matrix(tmp_path, hybrid_auto=True), _write_oracle(tmp_path)
    )

    assert report["backends"]["hybrid-fixed"]["status"] == "unavailable"
    assert report["backends"]["hybrid-auto"]["status"] == "unavailable"


def test_gate_rejects_run_identity_not_matching_matrix(tmp_path: Path) -> None:
    from scripts.v7_correctness_gate import verify_correctness

    directory = _write_passing_runs(tmp_path)
    wrong_identity = {**IDENTITY, "scale_factor": "1"}
    _write_run(directory, "gpu-copy", identity=wrong_identity)

    report = verify_correctness(directory, _write_matrix(tmp_path), _write_oracle(tmp_path))

    assert report["ok"] is False
    assert report["backends"]["gpu-copy"]["status"] == "failed"
    assert "run identity does not match matrix" in report["backends"]["gpu-copy"]["errors"]


def test_gate_rejects_unrecognized_json_record(tmp_path: Path) -> None:
    from scripts.v7_correctness_gate import verify_correctness

    directory = _write_passing_runs(tmp_path)
    _write_run(directory, "gpu-copy-retry")

    try:
        verify_correctness(directory, _write_matrix(tmp_path), _write_oracle(tmp_path))
    except ValueError as exc:
        assert "unexpected run record" in str(exc)
    else:
        raise AssertionError("unrecognized run record was accepted")


def test_gate_requires_hybrid_auto_only_when_matrix_enables_it(tmp_path: Path) -> None:
    from scripts.v7_correctness_gate import verify_correctness

    directory = _write_passing_runs(tmp_path, hybrid_auto=True)
    (directory / "hybrid-auto.json").unlink()

    report = verify_correctness(
        directory,
        _write_matrix(tmp_path, hybrid_auto=True),
        _write_oracle(tmp_path),
    )

    assert report["ok"] is False
    assert report["backends"]["hybrid-auto"]["status"] == "failed"


def test_gate_reports_full_pass_for_every_required_backend(tmp_path: Path) -> None:
    from scripts.v7_correctness_gate import verify_correctness

    report = verify_correctness(
        _write_passing_runs(tmp_path, hybrid_auto=True),
        _write_matrix(tmp_path, hybrid_auto=True),
        _write_oracle(tmp_path),
    )

    assert report == {
        "ok": True,
        "expected_hash": RESULT_HASH,
        "observed_hashes": [RESULT_HASH],
        "backends": {
            backend: {"status": "passed", "errors": []}
            for backend in BASE_BACKENDS + ["hybrid-auto"]
        },
    }
