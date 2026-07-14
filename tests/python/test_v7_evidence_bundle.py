from __future__ import annotations

import csv
import hashlib
import json
import shutil
import uuid
from pathlib import Path

import pytest

from scripts.run_v7_benchmarks import _payload_sha256
from scripts.summarize_v7_records import summary_fieldnames
from scripts.v7_benchmark_schema import (
    REQUEST_FIELDS,
    SETUP_FIELDS,
    validate_request,
    validate_setup,
)
from test_v7_benchmark_schema import RESULT_HASH, ROWS, valid_request, valid_setup


REQUIRED_BUNDLE_FILES = {
    "setups.csv",
    "warmups.csv",
    "raw.csv",
    "summary.csv",
    "summary.md",
    "correctness.json",
    "environment.json",
    "commands.txt",
    "matrix.yml",
    "dataset-manifest.json",
    "oracle.json",
    "manifest.json",
    "manifest.sha256",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, fields: list[str], records: list) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for record in records:
            writer.writerow(record.as_dict())


def _make_source(tmp_path: Path) -> tuple[Path, Path, Path]:
    dataset_manifest = tmp_path / "source" / "dataset-manifest.json"
    _write_json(
        dataset_manifest,
        {"scale_factor": "1", "tables": {"lineitem": {"rows": 6}}},
    )
    oracle = tmp_path / "source" / "oracle.json"
    _write_json(oracle, {"result_hash": RESULT_HASH, "rows": ROWS})
    matrix = tmp_path / "source" / "matrix.yml"
    payload = {
        "schema_version": 2,
        "experiment_id": "v7-formal-test",
        "dataset": {
            "path": "data/test-arrow",
            "scale_factor": "1",
            "manifest_sha256": _sha256(dataset_manifest),
            "expected_hash": RESULT_HASH,
        },
        "oracle": {"path": str(oracle), "sha256": _sha256(oracle)},
        "query": {"region": "ASIA", "date": "1994-01-01"},
        "configurations": [
            {
                "config_id": "gpu-copy",
                "engine": "gpu-copy",
                "runner": "cpp",
                "threads": 1,
                "ratio_mode": "fixed",
                "cpu_ratio": 0.0,
                "correctness_backend": "gpu-copy",
                "mode_options": {"memory_mode": "copy"},
            }
        ],
        "protocol": {"warmup": 3, "repeat": 10, "timeout_seconds": 1800},
    }
    _write_json(matrix, payload)
    return matrix, dataset_manifest, oracle


def _make_bundle(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    matrix, dataset_manifest, oracle = _make_source(tmp_path)
    matrix_payload = json.loads(matrix.read_text(encoding="utf-8"))
    root = tmp_path / "bundle"
    logs = root / "logs"
    logs.mkdir(parents=True)
    (logs / "gpu-copy.stdout.jsonl").write_text("session output\n", encoding="utf-8")
    (logs / "gpu-copy.stderr.txt").write_text("", encoding="utf-8")

    setup = validate_setup(
        valid_setup(
            experiment_id="v7-formal-test",
            dataset_path="data/test-arrow",
            initial_h2d_ms=0.5,
        )
    )
    warmups = [
        validate_request(
            valid_request(
                experiment_id="v7-formal-test",
                run_uuid=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"warmup-{index}")),
                sample_index=index,
                request_index=index,
                is_warmup=True,
                oracle_status="expected_hash_match",
                query_total_ms=float(index + 1),
            )
        )
        for index in range(3)
    ]
    measured = [
        validate_request(
            valid_request(
                experiment_id="v7-formal-test",
                run_uuid=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"measured-{index}")),
                sample_index=index,
                request_index=index + 3,
                is_warmup=False,
                oracle_status="expected_hash_match",
                query_total_ms=float(index + 1),
            )
        )
        for index in range(10)
    ]
    _write_csv(root / "setups.csv", SETUP_FIELDS, [setup])
    _write_csv(root / "warmups.csv", REQUEST_FIELDS, warmups)
    _write_csv(root / "raw.csv", REQUEST_FIELDS, measured)
    (root / "commands.txt").write_text(
        "CUDA_VISIBLE_DEVICES=0 /opt/memq5_arrow_session --engine gpu-copy "
        "--threads 1 --dataset /data/test-arrow --region ASIA --date 1994-01-01 "
        "--warmup 3 --repeat 10\n",
        encoding="utf-8",
    )
    cli_sha = "b" * 64
    git_commit = "a" * 40
    environment = {
        "git": {"commit": git_commit, "branch": "codex/test", "dirty": False, "dirty_paths": []},
        "session_cli": {"path": "/opt/memq5_arrow_session", "sha256": cli_sha},
        "cudf_environment": {"name": "memq5-cudf", "details": {"cudf": "25.06"}},
        "gpu": {
            "requested_index": 0,
            "cuda_visible_devices": "0",
            "devices": [
                {
                    "index": 0,
                    "uuid": "GPU-abc",
                    "name": "NVIDIA RTX 4090",
                    "driver_version": "555.42",
                }
            ],
        },
        "v7_runner": {
            "matrix": str(matrix.resolve()),
            "matrix_sha256": _sha256(matrix),
            "matrix_payload": matrix_payload,
            "matrix_payload_sha256": _payload_sha256(matrix_payload),
            "session_cli": "/opt/memq5_arrow_session",
            "session_cli_sha256": cli_sha,
            "dataset": "/data/test-arrow",
            "cudf_env": "memq5-cudf",
            "git_commit": git_commit,
            "gpu_index": 0,
            "cuda_visible_devices": "0",
        },
    }
    _write_json(root / "environment.json", environment)
    correctness = tmp_path / "source" / "correctness.json"
    _write_json(
        correctness,
        {
            "ok": True,
            "expected_hash": RESULT_HASH,
            "observed_hashes": [RESULT_HASH],
            "backends": {"gpu-copy": {"status": "passed", "errors": []}},
        },
    )
    return root, matrix, dataset_manifest, oracle, correctness


def _finalize(tmp_path: Path) -> Path:
    from scripts.v7_evidence_bundle import finalize_bundle

    root, matrix, dataset_manifest, oracle, correctness = _make_bundle(tmp_path)
    manifest = finalize_bundle(
        root,
        matrix=matrix,
        dataset_manifest=dataset_manifest,
        oracle=oracle,
        correctness=correctness,
    )
    assert manifest["status"] == "complete"
    return root


def test_finalize_and_audit_complete_v7_bundle(tmp_path: Path) -> None:
    from scripts.v7_evidence_bundle import audit_bundle

    root = _finalize(tmp_path)
    report = audit_bundle(root)

    assert report == {"ok": True, "errors": []}
    assert REQUIRED_BUNDLE_FILES <= {path.name for path in root.iterdir()}
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["protocol"] == {
        "warmup": 3,
        "repeat": 10,
        "expected_configurations": 1,
    }
    assert manifest["identity"]["git_commit"] == "a" * 40
    assert manifest["identity"]["session_cli_sha256"] == "b" * 64
    assert manifest["identity"]["gpu_uuid"] == "GPU-abc"
    assert manifest["matrix"]["payload"] == json.loads(
        (root / "matrix.yml").read_text(encoding="utf-8")
    )
    assert manifest["dataset"]["path"] == "data/test-arrow"
    assert manifest["dataset"]["manifest_sha256"] == manifest["dataset"]["sha256"]
    assert manifest["oracle"]["path"].endswith("source/oracle.json")
    assert manifest["correctness"]["counts"] == {
        "configurations": 1,
        "correctness_backends": 1,
        "setups": 1,
        "successful_setups": 1,
        "warmups": 3,
        "successful_warmups": 3,
        "measured": 10,
        "successful_measured": 10,
    }
    assert manifest["summary"] == {
        "source_files": ["summary.csv", "summary.md"],
        "fields": summary_fieldnames(),
        "row_count": 1,
    }


def test_finalize_rejects_incomplete_request_coverage(tmp_path: Path) -> None:
    from scripts.v7_evidence_bundle import finalize_bundle

    root, matrix, dataset_manifest, oracle, correctness = _make_bundle(tmp_path)
    with (root / "raw.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    with (root / "raw.csv").open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle, lineterminator="\n").writerows(rows[:-1])

    with pytest.raises(ValueError, match="measured.*expected=10"):
        finalize_bundle(
            root,
            matrix=matrix,
            dataset_manifest=dataset_manifest,
            oracle=oracle,
            correctness=correctness,
        )


def test_finalize_rejects_missing_gpu_uuid_identity(tmp_path: Path) -> None:
    from scripts.v7_evidence_bundle import finalize_bundle

    root, matrix, dataset_manifest, oracle, correctness = _make_bundle(tmp_path)
    environment_path = root / "environment.json"
    environment = json.loads(environment_path.read_text(encoding="utf-8"))
    environment["gpu"]["devices"] = []
    _write_json(environment_path, environment)

    with pytest.raises(ValueError, match="GPU UUID"):
        finalize_bundle(
            root,
            matrix=matrix,
            dataset_manifest=dataset_manifest,
            oracle=oracle,
            correctness=correctness,
        )


@pytest.mark.parametrize(
    ("relative_path", "replacement"),
    [
        ("logs/gpu-copy.stdout.jsonl", b"tampered log\n"),
        ("summary.csv", b"tampered summary\n"),
        ("oracle.json", b"{}\n"),
        ("manifest.json", b"{}\n"),
    ],
)
def test_audit_rejects_any_artifact_tampering(
    tmp_path: Path, relative_path: str, replacement: bytes
) -> None:
    from scripts.v7_evidence_bundle import audit_bundle

    root = _finalize(tmp_path)
    (root / relative_path).write_bytes(replacement)

    report = audit_bundle(root)

    assert report["ok"] is False
    assert report["errors"]


def test_audit_recomputes_summary_even_if_attacker_rewrites_checksums(
    tmp_path: Path,
) -> None:
    from scripts.v7_evidence_bundle import audit_bundle

    root = _finalize(tmp_path)
    raw_path = root / "raw.csv"
    with raw_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        rows = list(reader)
    rows[0]["query_total_ms"] = "999.0"
    with raw_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["raw.csv"] = _sha256(raw_path)
    _write_json(manifest_path, manifest)
    (root / "manifest.sha256").write_text(
        f"{_sha256(manifest_path)}  manifest.json\n", encoding="ascii"
    )

    report = audit_bundle(root)

    assert report["ok"] is False
    assert any("summary" in error for error in report["errors"])


def test_audit_rejects_unmanifested_extra_artifact(tmp_path: Path) -> None:
    from scripts.v7_evidence_bundle import audit_bundle

    root = _finalize(tmp_path)
    (root / "injected.txt").write_text("extra\n", encoding="utf-8")

    assert audit_bundle(root)["ok"] is False


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("manifest_version",), 99),
        (("matrix", "payload_sha256"), "0" * 64),
        (("matrix", "configuration_count"), 99),
        (("matrix", "configurations"), []),
        (("dataset", "scale_factor"), "10"),
        (("oracle", "path"), "wrong/oracle.json"),
        (("correctness", "expected_hash"), "0" * 16),
    ],
)
def test_audit_rejects_resigned_manifest_semantic_tampering(
    tmp_path: Path, path: tuple[str, ...], replacement: object
) -> None:
    from scripts.v7_evidence_bundle import audit_bundle

    root = _finalize(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    target = manifest
    for name in path[:-1]:
        target = target[name]
    target[path[-1]] = replacement
    _write_json(manifest_path, manifest)
    (root / "manifest.sha256").write_text(
        f"{_sha256(manifest_path)}  manifest.json\n", encoding="ascii"
    )

    assert audit_bundle(root)["ok"] is False


def test_audit_rejects_resigned_unrelated_command(tmp_path: Path) -> None:
    from scripts.v7_evidence_bundle import audit_bundle

    root = _finalize(tmp_path)
    commands = root / "commands.txt"
    commands.write_text("CUDA_VISIBLE_DEVICES=0 echo unrelated\n", encoding="utf-8")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["commands.txt"] = _sha256(commands)
    _write_json(manifest_path, manifest)
    (root / "manifest.sha256").write_text(
        f"{_sha256(manifest_path)}  manifest.json\n", encoding="ascii"
    )

    report = audit_bundle(root)
    assert report["ok"] is False
    assert any("commands" in error for error in report["errors"])


def test_finalize_rejects_oracle_hash_not_derived_from_exact_rows(
    tmp_path: Path,
) -> None:
    from scripts.v7_evidence_bundle import finalize_bundle

    root, matrix_path, dataset_manifest, oracle_path, correctness = _make_bundle(
        tmp_path
    )
    oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
    oracle["rows"][0]["revenue_1e4"] += 1
    _write_json(oracle_path, oracle)
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    matrix["oracle"]["sha256"] = _sha256(oracle_path)
    _write_json(matrix_path, matrix)
    environment_path = root / "environment.json"
    environment = json.loads(environment_path.read_text(encoding="utf-8"))
    environment["v7_runner"]["matrix_sha256"] = _sha256(matrix_path)
    environment["v7_runner"]["matrix_payload"] = matrix
    environment["v7_runner"]["matrix_payload_sha256"] = _payload_sha256(matrix)
    _write_json(environment_path, environment)

    with pytest.raises(ValueError, match="oracle.*exact rows"):
        finalize_bundle(
            root,
            matrix=matrix_path,
            dataset_manifest=dataset_manifest,
            oracle=oracle_path,
            correctness=correctness,
        )
