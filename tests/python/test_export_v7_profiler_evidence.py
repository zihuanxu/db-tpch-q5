from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import export_v7_profiler_evidence as exporter
from scripts.parse_ncu_csv import parse_ncu_csv as parse_ncu_csv_real
from scripts.parse_nsys_stats import parse_nsys_csv as parse_nsys_csv_real


REPO_ROOT = Path(__file__).parents[2]
EXPORTER = REPO_ROOT / "scripts/export_v7_profiler_evidence.py"
ENGINES = ("copy", "managed", "mapped", "hybrid-fixed", "hybrid-auto")
SELECTED_METRICS = {
    "duration": "gpu__time_duration.sum",
    "dram_read_bytes": "dram__bytes_read.sum",
    "dram_throughput": "dram__throughput.avg.pct_of_peak_sustained_elapsed",
    "sm_throughput": "sm__throughput.avg.pct_of_peak_sustained_elapsed",
    "achieved_occupancy": "sm__warps_active.avg.pct_of_peak_sustained_active",
}
METRIC_VALUES = {
    "gpu__time_duration.sum": 1_250_000.0,
    "dram__bytes_read.sum": 1_048_576.0,
    "dram__throughput.avg.pct_of_peak_sustained_elapsed": 51.25,
    "sm__throughput.avg.pct_of_peak_sustained_elapsed": 62.5,
    "sm__warps_active.avg.pct_of_peak_sustained_active": 48.0,
}
IDENTITY_FIELDS = (
    "scale_factor",
    "engine",
    "cpu_ratio",
    "gpu_ratio",
    "session_commit",
    "dataset_manifest",
    "evidence_manifest",
    "oracle_hash",
    "result_hash",
    "gpu_uuid",
    "execution",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _collector_files(directory: Path) -> dict[str, dict[str, object]]:
    return {
        path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
        for path in sorted(directory.iterdir())
        if path.is_file() and path.name != "metadata.json"
    }


def _write_nsys(directory: Path, command: list[str], identity: dict[str, object]) -> None:
    directory.mkdir(parents=True)
    (directory / "profile.nsys-rep").write_bytes(b"synthetic nsys report\x00")
    (directory / "stats_cuda_api_sum.csv").write_text(
        '"Total Time (ns)","Name"\n"100","cudaLaunchKernel"\n', encoding="utf-8"
    )
    (directory / "stats_cuda_gpu_kern_sum.csv").write_text(
        '"Total Time (ns)","Operation"\n"1250","void q5_kernel()"\n',
        encoding="utf-8",
    )
    (directory / "stats_cuda_gpu_mem_time_sum.csv").write_text(
        '"Total Time (ns)","Operation"\n"500","[CUDA memcpy HtoD]"\n',
        encoding="utf-8",
    )
    (directory / "stats_nvtx_sum.csv").write_text(
        '"Total Time (ns)","Range"\n"1000","request"\n"800","q5_kernel"\n',
        encoding="utf-8",
    )
    for name, content in {
        "profile.stdout.log": '{"record_type":"request","status":"ok"}\n',
        "profile.tool.stdout.log": "nsys progress\n",
        "profile.stderr.log": "",
        "stats.stdout.log": "Generated reports\n",
        "stats.stderr.log": "",
        "orchestrator.stdout.log": "collector complete\n",
        "orchestrator.stderr.log": "",
    }.items():
        (directory / name).write_text(content, encoding="utf-8")
    (directory / "profile.sqlite").write_bytes(b"x" * 65_536)
    (directory / "profile.sqlite-wal").write_bytes(b"wal")
    profile_command = ["nsys", "profile", "--output", str(directory / "profile"), *command]
    _write_json(
        directory / "metadata.json",
        {
            "metadata": identity,
            "profile_command": profile_command,
            "tool_versions": {"nsys": "NVIDIA Nsight Systems 2026.1"},
            "files": _collector_files(directory),
            "metric_universe_padding": [f"nsys-field-{index}" for index in range(2_000)],
        },
    )


def _ncu_csv() -> str:
    header = (
        '"ID","Process ID","Kernel Name","Context","Stream",'
        '"Metric Name","Metric Value"\n'
    )
    rows = "".join(
        f'"1","42","void q5_kernel()","1","7","{name}","{value}"\n'
        for name, value in METRIC_VALUES.items()
    )
    return header + rows


def _write_ncu(directory: Path, command: list[str], identity: dict[str, object]) -> None:
    directory.mkdir(parents=True)
    report = _ncu_csv()
    (directory / "report.csv").write_text(report, encoding="utf-8")
    _write_json(directory / "selected_metrics.json", SELECTED_METRICS)
    supported = sorted(
        [*SELECTED_METRICS.values(), *(f"unused__metric_{i}" for i in range(4_000))]
    )
    (directory / "supported_metrics.txt").write_text(
        "\n".join(supported) + "\n", encoding="utf-8"
    )
    (directory / "profile.stdout.log").write_text(report, encoding="utf-8")
    (directory / "profile.stderr.log").write_text("", encoding="utf-8")
    (directory / "orchestrator.stdout.log").write_text(
        "collector complete\n", encoding="utf-8"
    )
    (directory / "orchestrator.stderr.log").write_text("", encoding="utf-8")
    replay_mode = "kernel" if identity["engine"] == "hybrid-auto" else "application"
    profile_command = [
        "ncu",
        "--csv",
        "--replay-mode",
        replay_mode,
        "--metrics",
        ",".join(SELECTED_METRICS.values()),
        *command,
    ]
    _write_json(
        directory / "metadata.json",
        {
            "metadata": identity,
            "profile_command": profile_command,
            "tool_versions": {"ncu": "NVIDIA Nsight Compute 2026.1"},
            "replay": {"mode": replay_mode, "return_code": 0, "succeeded": True},
            "gpu": {
                "status": "ok",
                "index": 0,
                "uuid": "GPU-synthetic",
                "driver_version": "555.1",
            },
            "selected_metrics": SELECTED_METRICS,
            "supported_metrics": supported,
            "files": _collector_files(directory),
        },
    )


def _source_artifacts(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in {"manifest.json", "manifest.sha256"}
    }


def _write_manifest(root: Path, profiles: list[dict[str, object]]) -> None:
    manifest = {
        "manifest_version": 3,
        "status": "complete",
        "source_index": {"path": "profiles.json", "sha256": _sha256(root / "profiles.json")},
        "profile_count": len(profiles),
        "profiles": [
            {
                "id": profile["id"],
                **{field: profile[field] for field in IDENTITY_FIELDS},
            }
            for profile in profiles
        ],
        "artifacts": _source_artifacts(root),
    }
    _write_json(root / "manifest.json", manifest)
    _resign_manifest(root)


def _resign_manifest(root: Path) -> None:
    (root / "manifest.sha256").write_text(
        f"{_sha256(root / 'manifest.json')}  manifest.json\n", encoding="ascii"
    )


def _canonical_source(root: Path) -> list[dict[str, object]]:
    (root / "datasets/1").mkdir(parents=True)
    (root / "datasets/10").mkdir(parents=True)
    (root / "evidence/1").mkdir(parents=True)
    (root / "evidence/10").mkdir(parents=True)
    for scale in ("1", "10"):
        _write_json(root / f"datasets/{scale}/manifest.json", {"scale_factor": scale})
        _write_json(root / f"evidence/{scale}/manifest.json", {"status": "complete"})
        (root / f"datasets/{scale}/lineitem.arrow").write_bytes(b"dataset" * 8_192)
    profiles: list[dict[str, object]] = []
    expected: list[dict[str, object]] = []
    for scale in ("1", "10"):
        for engine in ENGINES:
            cpu_ratio = 0.5 if engine.startswith("hybrid-") else 0.0
            profile_id = f"sf{scale}-{engine}"
            command = [
                "bin/memq5_arrow_session",
                "--dataset",
                str(root / f"datasets/{scale}"),
                "--engine",
                engine,
                "--requests",
                "1",
            ]
            identity: dict[str, object] = {
                "scale_factor": scale,
                "engine": engine,
                "cpu_ratio": cpu_ratio,
                "gpu_ratio": 1.0 - cpu_ratio,
                "session_commit": "a" * 40,
                "dataset_manifest": {
                    "path": f"datasets/{scale}/manifest.json",
                    "sha256": _sha256(root / f"datasets/{scale}/manifest.json"),
                },
                "evidence_manifest": {
                    "path": f"evidence/{scale}/manifest.json",
                    "sha256": _sha256(root / f"evidence/{scale}/manifest.json"),
                },
                "oracle_hash": "0123456789abcdef",
                "result_hash": "0123456789abcdef",
                "gpu_uuid": "GPU-synthetic",
                "execution": {
                    "cwd": str(root),
                    "executable": {
                        "path": "bin/memq5_arrow_session",
                        "sha256": "b" * 64,
                        "build_commit": "a" * 40,
                    },
                },
            }
            nsys_dir = root / f"captures/{profile_id}/nsys"
            ncu_dir = root / f"captures/{profile_id}/ncu"
            _write_nsys(nsys_dir, command, identity)
            _write_ncu(ncu_dir, command, identity)
            logical = {
                "scale_factor": scale,
                "engine": engine,
                "cpu_ratio": cpu_ratio,
                "gpu_ratio": 1.0 - cpu_ratio,
            }
            expected.append(logical)
            profiles.append(
                {
                    "id": profile_id,
                    **identity,
                    "command": command,
                    "nsys": {
                        "metadata_path": f"captures/{profile_id}/nsys/metadata.json"
                    },
                    "ncu": {
                        "status": "ok",
                        "metadata_path": f"captures/{profile_id}/ncu/metadata.json",
                    },
                }
            )
    _write_json(
        root / "profiles.json",
        {
            "schema_version": 3,
            "status": "complete",
            "roots": {"captures": "captures", "datasets": "datasets", "evidence": "evidence"},
            "hybrid_auto": {"status": "enabled", "reason": "captured"},
            "expected_profiles": expected,
            "profiles": profiles,
        },
    )
    orchestration = {
        "schema_version": 1,
        "status": "complete",
        "profile_count": 10,
        "completed_profiles": [profile["id"] for profile in profiles],
        "ncu_unavailable_profiles": [],
    }
    _write_json(root / "orchestration.json", orchestration)
    _write_manifest(root, profiles)
    return profiles


@pytest.fixture
def full_bundle(tmp_path: Path) -> tuple[Path, list[dict[str, object]]]:
    root = tmp_path / "full-bundle"
    root.mkdir()
    return root, _canonical_source(root)


def _run(source: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(EXPORTER), "--source", str(source), "--output", str(output)],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_exports_compact_canonical_profiler_evidence(
    full_bundle: tuple[Path, list[dict[str, object]]], tmp_path: Path
) -> None:
    source, source_profiles = full_bundle
    output = tmp_path / "compact"

    completed = _run(source, output)

    assert completed.returncode == 0, completed.stderr
    summary = _read_json(output / "summary.json")
    assert summary["schema"] == "memq5.v7.compact-profiler-evidence"
    assert summary["schema_version"] == 1
    assert summary["source_bundle_name"] == "full-bundle"
    assert summary["source_manifest_sha256"] == _sha256(source / "manifest.json")
    assert summary["source_orchestration_status"] == "complete"
    assert summary["canonical_profile_count"] == 10
    assert len(summary["canonical_coverage"]) == 10
    records = summary["profiles"]
    assert isinstance(records, list) and len(records) == 10
    first = next(record for record in records if record["id"] == "sf1-copy")
    source_first = next(profile for profile in source_profiles if profile["id"] == "sf1-copy")
    assert first["identity"] == {field: source_first[field] for field in IDENTITY_FIELDS}
    assert first["app_command"] == source_first["command"]
    assert first["nsys"]["tool_version"] == "NVIDIA Nsight Systems 2026.1"
    assert first["nsys"]["profile_command"][0:2] == ["nsys", "profile"]
    assert first["nsys"]["observed_nvtx_ranges"] == ["q5_kernel", "request"]
    assert first["nsys"]["cuda_memory_operation_rows"] == [
        {
            "average_ns": 0,
            "instances": 0,
            "kind": "memcpy",
            "name": "[CUDA memcpy HtoD]",
            "report": "stats_cuda_gpu_mem_time_sum",
            "time_percent": 0,
            "total_ns": 500,
        }
    ]
    assert first["nsys"]["q5_kernel_total_time_ns"] == 1250
    assert first["ncu"]["tool_version"] == "NVIDIA Nsight Compute 2026.1"
    assert first["ncu"]["replay_mode"] == "application"
    assert first["ncu"]["gpu"]["uuid"] == "GPU-synthetic"
    assert first["ncu"]["kernel_name"] == "void q5_kernel()"
    assert first["ncu"]["metrics"] == METRIC_VALUES
    assert first["ncu"]["selected_metric_values"] == {
        role: METRIC_VALUES[metric] for role, metric in SELECTED_METRICS.items()
    }
    assert all(_sha256(output / path) == digest for path, digest in first["copied_files"].items())

    expected_nsys = {
        "profile.nsys-rep",
        "stats_cuda_api_sum.csv",
        "stats_cuda_gpu_kern_sum.csv",
        "stats_cuda_gpu_mem_time_sum.csv",
        "stats_nvtx_sum.csv",
        "profile.stdout.log",
        "profile.tool.stdout.log",
        "profile.stderr.log",
        "stats.stdout.log",
        "stats.stderr.log",
        "orchestrator.stdout.log",
        "orchestrator.stderr.log",
    }
    expected_ncu = {
        "report.csv",
        "selected_metrics.json",
        "profile.stdout.log",
        "profile.stderr.log",
        "orchestrator.stdout.log",
        "orchestrator.stderr.log",
    }
    assert {path.name for path in (output / "captures/sf1-copy/nsys").iterdir()} == expected_nsys
    assert {path.name for path in (output / "captures/sf1-copy/ncu").iterdir()} == expected_ncu


def test_redacts_secret_shaped_values_from_public_nsys_reports(
    full_bundle: tuple[Path, list[dict[str, object]]], tmp_path: Path
) -> None:
    source, profiles = full_bundle
    report = source / "captures/sf1-copy/nsys/profile.nsys-rep"
    secret = b"sk-" + b"x" * 32
    report.write_bytes(b"synthetic nsys report\x00DEEPSEEK_API_KEY=" + secret + b"\x00")
    metadata_path = report.parent / "metadata.json"
    metadata = _read_json(metadata_path)
    metadata["files"] = _collector_files(report.parent)
    _write_json(metadata_path, metadata)
    _write_manifest(source, profiles)
    output = tmp_path / "compact"

    completed = _run(source, output)

    assert completed.returncode == 0, completed.stderr
    public_report = output / "captures/sf1-copy/nsys/profile.nsys-rep"
    public_bytes = public_report.read_bytes()
    assert len(public_bytes) == report.stat().st_size
    assert secret not in public_bytes
    summary = _read_json(output / "summary.json")
    first = next(record for record in summary["profiles"] if record["id"] == "sf1-copy")
    assert first["nsys"]["redacted_secret_count"] == 1


def test_checksums_are_sorted_and_cover_every_other_output_file(
    full_bundle: tuple[Path, list[dict[str, object]]], tmp_path: Path
) -> None:
    source, _ = full_bundle
    output = tmp_path / "compact"

    assert _run(source, output).returncode == 0

    lines = (output / "checksums.sha256").read_text(encoding="ascii").splitlines()
    paths = [line.split("  ", 1)[1] for line in lines]
    expected_paths = sorted(
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file() and path.name != "checksums.sha256"
    )
    assert paths == sorted(paths)
    assert paths == expected_paths
    assert lines == [f"{_sha256(output / path)}  {path}" for path in expected_paths]


def test_rejects_source_manifest_checksum_mismatch(
    full_bundle: tuple[Path, list[dict[str, object]]], tmp_path: Path
) -> None:
    source, _ = full_bundle
    manifest = _read_json(source / "manifest.json")
    manifest["status"] = "tampered"
    _write_json(source / "manifest.json", manifest)

    completed = _run(source, tmp_path / "compact")

    assert completed.returncode != 0
    assert "manifest.sha256 does not match manifest.json" in completed.stderr


def test_rejects_incomplete_canonical_coverage(
    full_bundle: tuple[Path, list[dict[str, object]]], tmp_path: Path
) -> None:
    source, profiles = full_bundle
    index = _read_json(source / "profiles.json")
    indexed = index["profiles"]
    assert isinstance(indexed, list)
    indexed.pop()
    expected = index["expected_profiles"]
    assert isinstance(expected, list)
    expected.pop()
    _write_json(source / "profiles.json", index)
    _write_manifest(source, profiles[:-1])

    completed = _run(source, tmp_path / "compact")

    assert completed.returncode != 0
    assert "canonical 10-profile coverage" in completed.stderr


def test_omits_large_inputs_metadata_metric_universe_and_sqlite_sidecars(
    full_bundle: tuple[Path, list[dict[str, object]]], tmp_path: Path
) -> None:
    source, _ = full_bundle
    output = tmp_path / "compact"

    assert _run(source, output).returncode == 0

    relative_files = {
        path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()
    }
    assert not any(
        path.startswith("datasets/") or path.startswith("evidence/")
        for path in relative_files
    )
    assert not any(Path(path).name == "metadata.json" for path in relative_files)
    assert not any(Path(path).name == "supported_metrics.txt" for path in relative_files)
    assert not any(".sqlite" in Path(path).name for path in relative_files)
    readme = (output / "README.md").read_text(encoding="utf-8")
    for omitted in (
        "datasets/",
        "evidence/",
        "profile.sqlite",
        "supported_metrics.txt",
        "NCU metadata metric universe",
        "SQLite sidecars",
        "collector metadata.json",
    ):
        assert omitted in readme
    assert "python3 scripts/v7_profiler_bundle.py audit --directory FULL_BUNDLE" in readme


@pytest.mark.parametrize(
    "unsafe_id",
    [
        "/tmp/sf1-copy",
        "../sf1-copy",
        "sf1-copy/../sf1-copy",
        "sf10-copy",
    ],
)
def test_rejects_unsafe_or_noncanonical_profile_ids(
    full_bundle: tuple[Path, list[dict[str, object]]], unsafe_id: str
) -> None:
    source, _ = full_bundle
    index = _read_json(source / "profiles.json")
    profiles = index["profiles"]
    assert isinstance(profiles, list)
    first = profiles[0]
    assert isinstance(first, dict)
    first["id"] = unsafe_id

    with pytest.raises(ValueError, match="safe canonical profile id"):
        exporter._validate_coverage(index)


def test_rejects_orchestration_not_bound_by_source_manifest(
    full_bundle: tuple[Path, list[dict[str, object]]], tmp_path: Path
) -> None:
    source, _ = full_bundle
    orchestration = _read_json(source / "orchestration.json")
    orchestration["post_manifest_tamper"] = True
    _write_json(source / "orchestration.json", orchestration)

    completed = _run(source, tmp_path / "compact")

    assert completed.returncode != 0
    assert "source manifest artifact checksum mismatch: orchestration.json" in completed.stderr


def test_rejects_manifest_profile_identity_not_matching_index(
    full_bundle: tuple[Path, list[dict[str, object]]], tmp_path: Path
) -> None:
    source, _ = full_bundle
    manifest = _read_json(source / "manifest.json")
    profiles = manifest["profiles"]
    assert isinstance(profiles, list)
    first = profiles[0]
    assert isinstance(first, dict)
    first["result_hash"] = "f" * 16
    _write_json(source / "manifest.json", manifest)
    _resign_manifest(source)

    completed = _run(source, tmp_path / "compact")

    assert completed.returncode != 0
    assert "source manifest profiles do not match profiles.json" in completed.stderr


def test_rejects_ncu_gpu_uuid_not_matching_profile_identity(
    full_bundle: tuple[Path, list[dict[str, object]]], tmp_path: Path
) -> None:
    source, profiles = full_bundle
    metadata_path = source / "captures/sf1-copy/ncu/metadata.json"
    metadata = _read_json(metadata_path)
    gpu = metadata["gpu"]
    assert isinstance(gpu, dict)
    gpu["uuid"] = "GPU-wrong-device"
    _write_json(metadata_path, metadata)
    _write_manifest(source, profiles)

    completed = _run(source, tmp_path / "compact")

    assert completed.returncode != 0
    assert "NCU GPU UUID does not match profile identity" in completed.stderr


def test_rejects_duplicate_completed_orchestration_profile_ids(
    full_bundle: tuple[Path, list[dict[str, object]]], tmp_path: Path
) -> None:
    source, profiles = full_bundle
    orchestration_path = source / "orchestration.json"
    orchestration = _read_json(orchestration_path)
    completed_profiles = orchestration["completed_profiles"]
    assert isinstance(completed_profiles, list)
    completed_profiles.append(completed_profiles[0])
    _write_json(orchestration_path, orchestration)
    _write_manifest(source, profiles)

    completed = _run(source, tmp_path / "compact")

    assert completed.returncode != 0
    assert "does not cover all canonical profiles exactly once" in completed.stderr


def test_hashes_manifest_before_parsing_it(
    full_bundle: tuple[Path, list[dict[str, object]]], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _ = full_bundle
    events: list[str] = []
    original_sha256 = exporter.hashlib.sha256
    original_loads = exporter.json.loads

    class TrackingHash:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.inner = original_sha256(*args, **kwargs)

        def update(self, value: bytes) -> None:
            self.inner.update(value)

        def hexdigest(self) -> str:
            events.append("hash")
            return self.inner.hexdigest()

        def __getattr__(self, name: str) -> object:
            return getattr(self.inner, name)

    def tracking_loads(value: object, *args: object, **kwargs: object) -> object:
        events.append("parse")
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(exporter.hashlib, "sha256", TrackingHash)
    monkeypatch.setattr(exporter.json, "loads", tracking_loads)

    exporter._validate_manifest_checksum(source)

    assert events == ["hash", "parse"]


def test_profiles_json_is_parsed_from_the_bytes_that_were_hashed(
    full_bundle: tuple[Path, list[dict[str, object]]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _ = full_bundle
    index_path = source / "profiles.json"
    original_index = _read_json(index_path)
    original_profiles = original_index["profiles"]
    assert isinstance(original_profiles, list)
    original_first = original_profiles[0]
    assert isinstance(original_first, dict)
    original_command = list(original_first["command"])
    tampered_index = json.loads(json.dumps(original_index))
    tampered_profiles = tampered_index["profiles"]
    assert isinstance(tampered_profiles, list)
    tampered_first = tampered_profiles[0]
    assert isinstance(tampered_first, dict)
    tampered_first["command"] = [*original_command, "--tampered-after-hash"]
    tampered_bytes = (json.dumps(tampered_index, indent=2, sort_keys=True) + "\n").encode()
    original_read_hashed_bytes = exporter._read_hashed_bytes
    swapped = False

    def swap_after_hash(path: Path, label: str) -> tuple[bytearray, str]:
        nonlocal swapped
        data, digest = original_read_hashed_bytes(path, label)
        if path == index_path:
            path.write_bytes(tampered_bytes)
            swapped = True
        return data, digest

    monkeypatch.setattr(exporter, "_read_hashed_bytes", swap_after_hash)
    output = tmp_path / "compact"

    exporter.export(source, output)

    summary = _read_json(output / "summary.json")
    records = summary["profiles"]
    assert isinstance(records, list)
    first = records[0]
    assert isinstance(first, dict)
    assert swapped
    assert first["app_command"] == original_command


def test_csv_and_selected_json_are_parsed_from_staging_copies(
    full_bundle: tuple[Path, list[dict[str, object]]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _ = full_bundle
    parsed_paths: list[Path] = []
    selected_paths: list[Path] = []
    original_load_json = exporter._load_json

    def tracking_nsys(path: Path) -> list[dict[str, object]]:
        parsed_paths.append(path)
        return parse_nsys_csv_real(path)

    def tracking_ncu(path: Path, kernel: str) -> dict[str, object]:
        parsed_paths.append(path)
        return parse_ncu_csv_real(path, kernel)

    def tracking_json(path: Path, label: str) -> dict[str, object]:
        if label == "selected_metrics.json":
            selected_paths.append(path)
        return original_load_json(path, label)

    monkeypatch.setattr(exporter, "parse_nsys_csv", tracking_nsys)
    monkeypatch.setattr(exporter, "parse_ncu_csv", tracking_ncu)
    monkeypatch.setattr(exporter, "_load_json", tracking_json)

    exporter.export(source, tmp_path / "compact")

    assert parsed_paths and selected_paths
    assert all(not path.is_relative_to(source.resolve()) for path in parsed_paths)
    assert all(not path.is_relative_to(source.resolve()) for path in selected_paths)


def test_rejects_output_whose_real_parent_is_inside_source(
    full_bundle: tuple[Path, list[dict[str, object]]], tmp_path: Path
) -> None:
    source, _ = full_bundle
    source_alias = tmp_path / "source-alias"
    source_alias.symlink_to(source, target_is_directory=True)

    completed = _run(source, source_alias / "compact")

    assert completed.returncode != 0
    assert "output directory must be outside the source bundle" in completed.stderr
    assert not (source / "compact").exists()


def test_only_copies_explicitly_allowed_optional_logs(
    full_bundle: tuple[Path, list[dict[str, object]]], tmp_path: Path
) -> None:
    source, profiles = full_bundle
    unexpected = source / "captures/sf1-copy/nsys/debug.stdout.log"
    unexpected.write_text("not publication evidence\n", encoding="utf-8")
    _write_manifest(source, profiles)
    output = tmp_path / "compact"

    completed = _run(source, output)

    assert completed.returncode == 0, completed.stderr
    assert not (output / "captures/sf1-copy/nsys/debug.stdout.log").exists()
    assert (output / "captures/sf1-copy/nsys/profile.tool.stdout.log").is_file()
    assert (output / "captures/sf1-copy/nsys/orchestrator.stdout.log").is_file()


def test_publishes_directories_0755_and_files_0644(
    full_bundle: tuple[Path, list[dict[str, object]]], tmp_path: Path
) -> None:
    source, _ = full_bundle
    output = tmp_path / "compact"

    completed = _run(source, output)

    assert completed.returncode == 0, completed.stderr
    paths = [output, *output.rglob("*")]
    assert all(
        stat.S_IMODE(path.stat().st_mode) == (0o755 if path.is_dir() else 0o644)
        for path in paths
    )
