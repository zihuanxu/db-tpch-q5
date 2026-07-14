#!/usr/bin/env python3
"""Run and freeze the canonical V7 SF1/SF10 profiler matrix."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from scripts.v7_evidence_bundle import audit_bundle as _audit_evidence_bundle
    from scripts.v7_profiler_bundle import (
        HYBRID_FIXED_CPU_RATIO,
        _recognized_ncu_unavailable,
        audit as _profiler_audit,
        finalize as _profiler_finalize,
    )
except ModuleNotFoundError:
    from v7_evidence_bundle import audit_bundle as _audit_evidence_bundle
    from v7_profiler_bundle import (  # type: ignore[no-redef]
        HYBRID_FIXED_CPU_RATIO,
        _recognized_ncu_unavailable,
        audit as _profiler_audit,
        finalize as _profiler_finalize,
    )


ENGINES = (
    ("copy", "gpu-copy"),
    ("managed", "gpu-managed"),
    ("mapped", "gpu-mapped"),
    ("hybrid-fixed", "hybrid-arrow"),
    ("hybrid-auto", "hybrid-arrow"),
)
SHA256_LENGTH = 64


class OrchestrationError(RuntimeError):
    """A collection or provenance failure that must stop the formal run."""


@dataclass(frozen=True)
class ScaleInput:
    scale: str
    data: Path
    evidence: Path
    fixed_ratio: float
    auto_ratio: float
    session_commit: str
    result_hash: str
    dataset_manifest_sha256: str
    evidence_manifest_sha256: str
    evidence_audit: dict[str, object]


@dataclass(frozen=True)
class RunConfig:
    output_dir: Path
    session_cli: Path
    sf1_evidence: Path
    sf10_evidence: Path
    sf1_data: Path
    sf10_data: Path
    gpu_index: int
    gpu_uuid: str
    sf1_fixed_ratio: float
    sf10_fixed_ratio: float
    working_directory: Path
    dry_run: bool


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(_json_bytes(value))
    os.replace(temporary, path)


def _load_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OrchestrationError(f"invalid {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise OrchestrationError(f"{label} must be a JSON object: {path}")
    return value


def _require_directory(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if path.is_symlink() or not resolved.is_dir():
        raise OrchestrationError(f"{label} must be a regular directory: {path}")
    return resolved


def _require_file(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if path.is_symlink() or not resolved.is_file():
        raise OrchestrationError(f"{label} must be a regular file: {path}")
    return resolved


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise OrchestrationError(f"{label} must be a non-empty string")
    return value


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise OrchestrationError(f"{label} must be an object")
    return value


def _ratio(value: float, label: str, *, interior: bool = False) -> float:
    if not math.isfinite(value) or value < 0.0 or value > 1.0:
        raise OrchestrationError(f"{label} must be a finite ratio in [0, 1]")
    if interior and not 0.0 < value < 1.0:
        raise OrchestrationError(f"{label} must be strictly between 0 and 1")
    return value


def _auto_ratio(evidence: Path, manifest: dict[str, object]) -> float:
    matrix = manifest.get("matrix")
    auto_ids: set[str] = set()
    if isinstance(matrix, dict) and isinstance(matrix.get("configurations"), list):
        for configuration in matrix["configurations"]:
            if isinstance(configuration, dict) and configuration.get("ratio_mode") == "auto":
                config_id = configuration.get("config_id")
                if isinstance(config_id, str) and config_id:
                    auto_ids.add(config_id)
    setups = evidence / "setups.csv"
    try:
        with setups.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise OrchestrationError(f"cannot read hybrid-auto setup from {setups}: {exc}") from exc
    candidates = [
        row
        for row in rows
        if row.get("ratio_mode") == "auto"
        and row.get("status") == "ok"
        and (not auto_ids or row.get("config_id") in auto_ids)
    ]
    if len(candidates) != 1:
        raise OrchestrationError(
            f"evidence must contain exactly one successful hybrid-auto setup: {setups}"
        )
    try:
        return _ratio(
            float(candidates[0]["selected_cpu_ratio"]),
            f"hybrid-auto selected ratio in {setups}",
            interior=True,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise OrchestrationError(f"invalid hybrid-auto selected ratio in {setups}") from exc


def _validate_evidence(
    scale: str,
    evidence_value: Path,
    data_value: Path,
    fixed_ratio: float,
    gpu_uuid: str,
) -> ScaleInput:
    evidence = _require_directory(evidence_value, f"SF{scale} evidence bundle")
    data = _require_directory(data_value, f"SF{scale} dataset")
    audit = _audit_evidence_bundle(evidence)
    if not isinstance(audit, dict) or audit.get("ok") is not True:
        errors = audit.get("errors") if isinstance(audit, dict) else audit
        raise OrchestrationError(f"SF{scale} evidence audit failed: {errors}")

    manifest_path = _require_file(evidence / "manifest.json", "evidence manifest")
    manifest = _load_json(manifest_path, "evidence manifest")
    if manifest.get("manifest_version") != 1 or manifest.get("status") != "complete":
        raise OrchestrationError(f"SF{scale} evidence manifest is not complete version 1")
    identity = _mapping(manifest.get("identity"), "evidence identity")
    session_commit = _string(identity.get("git_commit"), "evidence git commit")
    if len(session_commit) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in session_commit
    ):
        raise OrchestrationError("evidence git commit must be a full lowercase commit")
    evidence_gpu_uuid = _string(identity.get("gpu_uuid"), "evidence GPU UUID")
    if evidence_gpu_uuid != gpu_uuid:
        raise OrchestrationError(
            f"SF{scale} evidence GPU UUID mismatch: expected={gpu_uuid} actual={evidence_gpu_uuid}"
        )

    dataset_manifest = _require_file(data / "manifest.json", "dataset manifest")
    dataset_sha = _sha256(dataset_manifest)
    dataset = _mapping(manifest.get("dataset"), "evidence dataset")
    if str(dataset.get("scale_factor")) != scale or dataset.get("sha256") != dataset_sha:
        raise OrchestrationError(f"SF{scale} dataset manifest does not match evidence")
    dataset_payload = _load_json(dataset_manifest, "dataset manifest")
    if (
        dataset_payload.get("format_version") != 1
        or str(dataset_payload.get("scale_factor")) != scale
    ):
        raise OrchestrationError(f"SF{scale} dataset manifest identity is invalid")

    oracle = _mapping(manifest.get("oracle"), "evidence oracle")
    correctness = _mapping(manifest.get("correctness"), "evidence correctness")
    result_hash = _string(oracle.get("result_hash"), "evidence oracle result hash")
    if (
        len(result_hash) != 16
        or any(character not in "0123456789abcdef" for character in result_hash)
        or correctness.get("expected_hash") != result_hash
        or correctness.get("observed_hashes") != [result_hash]
    ):
        raise OrchestrationError(f"SF{scale} evidence oracle/correctness hashes disagree")
    if not math.isclose(fixed_ratio, HYBRID_FIXED_CPU_RATIO, abs_tol=1e-12):
        raise OrchestrationError(
            f"SF{scale} hybrid fixed ratio must be {HYBRID_FIXED_CPU_RATIO} "
            "for the current profiler bundle schema"
        )
    return ScaleInput(
        scale=scale,
        data=data,
        evidence=evidence,
        fixed_ratio=fixed_ratio,
        auto_ratio=_auto_ratio(evidence, manifest),
        session_commit=session_commit,
        result_hash=result_hash,
        dataset_manifest_sha256=dataset_sha,
        evidence_manifest_sha256=_sha256(manifest_path),
        evidence_audit=audit,
    )


def _validate_config(config: RunConfig) -> tuple[Path, Path, list[ScaleInput]]:
    if config.gpu_index < 0:
        raise OrchestrationError("--gpu-index must be non-negative")
    if not config.gpu_uuid.startswith("GPU-"):
        raise OrchestrationError("--gpu-uuid must be a physical GPU UUID")
    working_directory = _require_directory(config.working_directory, "working directory")
    executable = _require_file(config.session_cli, "NVTX session CLI")
    if executable.name != "memq5_arrow_session":
        raise OrchestrationError("session CLI must be named memq5_arrow_session")
    if not os.access(executable, os.X_OK):
        raise OrchestrationError("session CLI must be executable")
    with executable.open("rb") as handle:
        elf_magic = handle.read(4)
    if elf_magic != b"\x7fELF":
        raise OrchestrationError("session CLI must be an ELF binary")
    output = config.output_dir.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise OrchestrationError("output directory must not exist or must be empty")

    scales = [
        _validate_evidence(
            "1", config.sf1_evidence, config.sf1_data, config.sf1_fixed_ratio, config.gpu_uuid
        ),
        _validate_evidence(
            "10",
            config.sf10_evidence,
            config.sf10_data,
            config.sf10_fixed_ratio,
            config.gpu_uuid,
        ),
    ]
    commits = {item.session_commit for item in scales}
    if len(commits) != 1:
        raise OrchestrationError("SF1 and SF10 evidence use different session commits")
    return output, executable, scales


def _evidence_reference(
    scale: ScaleInput, staged_dataset: Path
) -> dict[str, object]:
    return {
        "manifest_version": 1,
        "status": "complete",
        "git": {"commit": scale.session_commit},
        "dataset": {
            "path": str(staged_dataset),
            "scale_factor": scale.scale,
            "manifest_sha256": scale.dataset_manifest_sha256,
        },
        "correctness": {
            "ok": True,
            "expected_hash": scale.result_hash,
            "observed_hashes": [scale.result_hash],
        },
        "source_evidence_manifest": {
            "path": "source-manifest.json",
            "sha256": scale.evidence_manifest_sha256,
        },
    }


def _ratio_text(value: float) -> str:
    return format(value, ".17g")


def _app_command(
    executable: Path, dataset: Path, engine: str, cpu_ratio: float
) -> list[str]:
    engine_command = dict(ENGINES)[engine]
    command = [
        str(executable),
        "--dataset",
        str(dataset),
        "--engine",
        engine_command,
        "--requests",
        "1",
    ]
    if engine != "hybrid-auto":
        command.extend(["--cpu-ratio", _ratio_text(cpu_ratio)])
    if engine.startswith("hybrid-"):
        command.extend(
            ["--hybrid-selection", "auto" if engine == "hybrid-auto" else "fixed"]
        )
    return command


def _collector_command(
    tool: str,
    output: Path,
    identity: dict[str, object],
    app_command: list[str],
    gpu_index: int,
) -> list[str]:
    script = Path(__file__).resolve().with_name(f"profile_{tool}.py")
    command = [
        sys.executable,
        str(script),
        "--output-dir",
        str(output),
    ]
    if tool == "ncu":
        command.extend(["--kernel-name", "q5_kernel", "--device-index", str(gpu_index)])
    command.extend(
        [
            "--metadata-json",
            json.dumps(identity, sort_keys=True, separators=(",", ":")),
            "--",
            *app_command,
        ]
    )
    return command


def _profile_plan(
    root: Path,
    executable: Path,
    working_directory: Path,
    gpu_index: int,
    gpu_uuid: str,
    scales: list[ScaleInput],
) -> list[dict[str, object]]:
    executable_sha = _sha256(executable)
    profiles: list[dict[str, object]] = []
    for scale in scales:
        staged_dataset = (root / "datasets" / scale.scale).resolve()
        evidence_sha = hashlib.sha256(
            _json_bytes(_evidence_reference(scale, staged_dataset))
        ).hexdigest()
        for engine, _ in ENGINES:
            if engine == "hybrid-fixed":
                cpu_ratio = scale.fixed_ratio
            elif engine == "hybrid-auto":
                cpu_ratio = scale.auto_ratio
            else:
                cpu_ratio = 0.0
            identity: dict[str, object] = {
                "scale_factor": scale.scale,
                "engine": engine,
                "cpu_ratio": cpu_ratio,
                "gpu_ratio": 1.0 - cpu_ratio,
                "session_commit": scale.session_commit,
                "dataset_manifest": {
                    "path": f"datasets/{scale.scale}/manifest.json",
                    "sha256": scale.dataset_manifest_sha256,
                },
                "evidence_manifest": {
                    "path": f"evidence/{scale.scale}/manifest.json",
                    "sha256": evidence_sha,
                },
                "oracle_hash": scale.result_hash,
                "result_hash": scale.result_hash,
                "gpu_uuid": gpu_uuid,
                "execution": {
                    "cwd": str(working_directory),
                    "executable": {
                        "path": str(executable),
                        "sha256": executable_sha,
                        "build_commit": scale.session_commit,
                    },
                },
            }
            profile_id = f"sf{scale.scale}-{engine}"
            app = _app_command(executable, staged_dataset, engine, cpu_ratio)
            nsys_dir = root / "captures" / profile_id / "nsys"
            ncu_dir = root / "captures" / profile_id / "ncu"
            profiles.append(
                {
                    "id": profile_id,
                    "identity": identity,
                    "identity_binding": {
                        "cpu_ratio": (
                            "evidence_setup_then_nsys_observed"
                            if engine == "hybrid-auto"
                            else "command"
                        ),
                        "dataset": "dataset_manifest_sha256",
                        "evidence": "source_evidence_manifest_sha256",
                        "executable": "elf_sha256",
                    },
                    "app_command": app,
                    "nsys_collector_command": _collector_command(
                        "nsys", nsys_dir, identity, app, gpu_index
                    ),
                    "ncu_collector_command": _collector_command(
                        "ncu", ncu_dir, identity, app, gpu_index
                    ),
                }
            )
    return profiles


def _public_plan(
    root: Path, gpu_index: int, profiles: list[dict[str, object]], dry_run: bool
) -> dict[str, object]:
    bundle_script = Path(__file__).resolve().with_name("v7_profiler_bundle.py")
    return {
        "dry_run": dry_run,
        "output_dir": str(root),
        "environment": {"CUDA_VISIBLE_DEVICES": str(gpu_index)},
        "profile_count": len(profiles),
        "profiles": profiles,
        "finalize_command": [
            sys.executable,
            str(bundle_script),
            "finalize",
            "--directory",
            str(root),
        ],
        "audit_command": [
            sys.executable,
            str(bundle_script),
            "audit",
            "--directory",
            str(root),
        ],
    }


def _stage_dataset(source: Path, destination: Path) -> None:
    manifest_path = source / "manifest.json"
    manifest = _load_json(manifest_path, "dataset manifest")
    tables = manifest.get("tables")
    if not isinstance(tables, dict) or not tables:
        raise OrchestrationError(f"dataset manifest has no tables: {manifest_path}")
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(manifest_path, destination / "manifest.json")
    for name, raw in sorted(tables.items()):
        if not isinstance(raw, dict):
            raise OrchestrationError(f"invalid dataset table entry: {name}")
        relative = Path(_string(raw.get("file"), f"dataset table file for {name}"))
        if relative.is_absolute() or ".." in relative.parts:
            raise OrchestrationError(f"unsafe dataset table path: {relative}")
        source_file = _require_file(source / relative, f"dataset table {name}")
        expected_sha = _string(raw.get("sha256"), f"dataset table SHA256 for {name}")
        if len(expected_sha) != SHA256_LENGTH or _sha256(source_file) != expected_sha:
            raise OrchestrationError(f"dataset table checksum mismatch: {source_file}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source_file, target)
        except OSError as exc:
            raise OrchestrationError(
                f"cannot hard-link dataset table into profiler bundle: {source_file}: {exc}"
            ) from exc


def _stage_inputs(root: Path, scales: list[ScaleInput]) -> None:
    (root / "captures").mkdir(parents=True)
    (root / "datasets").mkdir()
    (root / "evidence").mkdir()
    for scale in scales:
        staged_dataset = (root / "datasets" / scale.scale).resolve()
        _stage_dataset(scale.data, staged_dataset)
        evidence_dir = root / "evidence" / scale.scale
        evidence_dir.mkdir()
        shutil.copyfile(scale.evidence / "manifest.json", evidence_dir / "source-manifest.json")
        _write_json(
            evidence_dir / "manifest.json",
            _evidence_reference(scale, staged_dataset),
        )


def _index_profile(profile: dict[str, object], ncu_status: str = "ok") -> dict[str, object]:
    identity = profile["identity"]
    assert isinstance(identity, dict)
    return {
        "id": profile["id"],
        **identity,
        "command": profile["app_command"],
        "nsys": {
            "metadata_path": f"captures/{profile['id']}/nsys/metadata.json"
        },
        "ncu": {
            "status": ncu_status,
            "metadata_path": f"captures/{profile['id']}/ncu/metadata.json",
        },
    }


def _write_index(
    root: Path,
    profiles: list[dict[str, object]],
    statuses: dict[str, str],
    status: str,
) -> None:
    indexed = [
        _index_profile(profile, statuses.get(str(profile["id"]), "not-run"))
        for profile in profiles
    ]
    expected = [
        {
            key: profile[key]
            for key in ("scale_factor", "engine", "cpu_ratio", "gpu_ratio")
        }
        for profile in indexed
    ]
    _write_json(
        root / "profiles.json",
        {
            "schema_version": 3,
            "status": status,
            "roots": {
                "captures": "captures",
                "datasets": "datasets",
                "evidence": "evidence",
            },
            "hybrid_auto": {
                "status": "enabled",
                "reason": "canonical hybrid-auto profiles captured",
            },
            "expected_profiles": expected,
            "profiles": indexed,
        },
    )


def _run_command(
    command: list[str], *, cwd: Path, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _save_invocation_output(
    output_dir: Path, completed: subprocess.CompletedProcess[str]
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "orchestrator.stdout.log").write_text(
        completed.stdout or "", encoding="utf-8"
    )
    (output_dir / "orchestrator.stderr.log").write_text(
        completed.stderr or "", encoding="utf-8"
    )


def _nsys_observation(output_dir: Path, expected_hash: str) -> float:
    path = output_dir / "profile.stdout.log"
    try:
        records = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OrchestrationError(f"invalid NSYS application output: {path}: {exc}") from exc
    setups = [record for record in records if record.get("record_type") == "session_setup"]
    requests = [record for record in records if record.get("record_type") == "request"]
    if len(setups) != 1 or len(requests) != 1:
        raise OrchestrationError(
            f"NSYS application output must contain one setup and one request: {path}"
        )
    setup, request = setups[0], requests[0]
    if (
        setup.get("status") != "ok"
        or request.get("status") != "ok"
        or request.get("result_hash") != expected_hash
    ):
        raise OrchestrationError(f"NSYS application result gate failed: {path}")
    try:
        setup_ratio = float(setup["selected_cpu_ratio"])
        request_ratio = float(request["selected_cpu_ratio"])
    except (KeyError, TypeError, ValueError) as exc:
        raise OrchestrationError(f"NSYS application ratio is invalid: {path}") from exc
    if not math.isclose(setup_ratio, request_ratio, abs_tol=1e-12):
        raise OrchestrationError(f"NSYS setup/request ratios disagree: {path}")
    return _ratio(setup_ratio, f"NSYS observed ratio in {path}")


def _replace_collector_identity(metadata_path: Path, identity: dict[str, object]) -> None:
    metadata = _load_json(metadata_path, "collector metadata")
    metadata["metadata"] = identity
    _write_json(metadata_path, metadata)


def _ncu_unavailable(output_dir: Path) -> tuple[bool, str]:
    metadata_path = output_dir / "metadata.json"
    if not metadata_path.is_file():
        return False, "NCU collector did not preserve metadata.json"
    metadata = _load_json(metadata_path, "NCU collector metadata")
    return_code = metadata.get("return_code")
    replay = metadata.get("replay")
    if (
        isinstance(return_code, bool)
        or not isinstance(return_code, int)
        or return_code == 0
        or not isinstance(replay, dict)
        or replay.get("return_code") != return_code
        or replay.get("succeeded") is not False
    ):
        return False, "NCU failure metadata is incomplete"
    messages: list[str] = []
    for name in ("profile.stderr.log", "profile.stdout.log"):
        path = output_dir / name
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                messages.append(text)
    message = "\n".join(messages)
    return _recognized_ncu_unavailable(message), message or "NCU failure logs are empty"


def _failure(
    profile_id: str,
    tool: str,
    command: list[str],
    completed: subprocess.CompletedProcess[str],
    message: str,
) -> dict[str, object]:
    return {
        "profile_id": profile_id,
        "tool": tool,
        "command": command,
        "collector_return_code": completed.returncode,
        "stdout": completed.stdout or "",
        "stderr": completed.stderr or "",
        "message": message,
    }


def _finalize_bundle(root: Path) -> int:
    return _profiler_finalize(argparse.Namespace(directory=root, index=None))


def _audit_profiler_bundle(root: Path) -> int:
    return _profiler_audit(argparse.Namespace(directory=root, index=None))


def _execute(
    config: RunConfig,
    root: Path,
    executable: Path,
    scales: list[ScaleInput],
    profiles: list[dict[str, object]],
) -> None:
    statuses: dict[str, str] = {}
    unavailable: list[str] = []
    _write_index(root, profiles, statuses, "collecting")
    state: dict[str, object] = {
        "schema_version": 1,
        "status": "collecting",
        "profile_count": len(profiles),
        "completed_profiles": [],
        "ncu_unavailable_profiles": unavailable,
        "evidence_audits": {
            f"sf{scale.scale}": scale.evidence_audit for scale in scales
        },
    }
    _write_json(root / "orchestration.json", state)
    environment = os.environ.copy()
    environment.update(
        {
            "CUDA_VISIBLE_DEVICES": str(config.gpu_index),
            "LC_ALL": "C",
            "LANG": "C",
        }
    )
    working_directory = config.working_directory.resolve()

    try:
        _stage_inputs(root, scales)
        for profile in profiles:
            profile_id = str(profile["id"])
            identity = profile["identity"]
            assert isinstance(identity, dict)
            nsys_command = list(profile["nsys_collector_command"])
            nsys_dir = root / "captures" / profile_id / "nsys"
            completed = _run_command(
                nsys_command, cwd=working_directory, env=environment
            )
            _save_invocation_output(nsys_dir, completed)
            if completed.returncode != 0:
                message = (completed.stderr or completed.stdout).strip() or "NSYS collector failed"
                failure = _failure(
                    profile_id, "nsys", nsys_command, completed, message
                )
                raise OrchestrationError(json.dumps(failure, sort_keys=True))

            observed_ratio = _nsys_observation(
                nsys_dir, str(identity["result_hash"])
            )
            if identity["engine"] == "hybrid-auto":
                identity["cpu_ratio"] = observed_ratio
                identity["gpu_ratio"] = 1.0 - observed_ratio
                _replace_collector_identity(nsys_dir / "metadata.json", identity)
                ncu_dir = root / "captures" / profile_id / "ncu"
                profile["ncu_collector_command"] = _collector_command(
                    "ncu",
                    ncu_dir,
                    identity,
                    list(profile["app_command"]),
                    config.gpu_index,
                )
            elif not math.isclose(
                observed_ratio, float(identity["cpu_ratio"]), abs_tol=1e-12
            ):
                raise OrchestrationError(
                    f"{profile_id} observed CPU ratio does not match command identity"
                )

            ncu_command = list(profile["ncu_collector_command"])
            ncu_dir = root / "captures" / profile_id / "ncu"
            completed = _run_command(
                ncu_command, cwd=working_directory, env=environment
            )
            _save_invocation_output(ncu_dir, completed)
            if completed.returncode != 0:
                recognized, message = _ncu_unavailable(ncu_dir)
                if recognized:
                    statuses[profile_id] = "unavailable"
                    unavailable.append(profile_id)
                else:
                    statuses[profile_id] = "failed"
                    failure = _failure(
                        profile_id, "ncu", ncu_command, completed, message
                    )
                    raise OrchestrationError(json.dumps(failure, sort_keys=True))
            else:
                statuses[profile_id] = "ok"
            completed_profiles = state["completed_profiles"]
            assert isinstance(completed_profiles, list)
            completed_profiles.append(profile_id)
            _write_index(root, profiles, statuses, "collecting")
            _write_json(root / "orchestration.json", state)

        _write_index(root, profiles, statuses, "complete")
        if _finalize_bundle(root) != 0:
            raise OrchestrationError("profiler bundle finalize returned nonzero")
        if _audit_profiler_bundle(root) != 0:
            raise OrchestrationError("profiler bundle audit returned nonzero")
    except Exception as exc:
        try:
            failure = json.loads(str(exc))
        except json.JSONDecodeError:
            failure = {"phase": "orchestration", "message": str(exc)}
        state["status"] = "failed"
        state["failure"] = failure
        _write_index(root, profiles, statuses, "failed")
        _write_json(root / "orchestration.json", state)
        if isinstance(exc, OrchestrationError):
            raise
        raise OrchestrationError(str(exc)) from exc

    state["status"] = "complete"
    _write_json(root / "orchestration.json", state)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect and audit the canonical V7 SF1/SF10 profiler bundle"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--session-cli", type=Path, required=True)
    parser.add_argument("--sf1-evidence", type=Path, required=True)
    parser.add_argument("--sf10-evidence", type=Path, required=True)
    parser.add_argument("--sf1-data", type=Path, required=True)
    parser.add_argument("--sf10-data", type=Path, required=True)
    parser.add_argument("--gpu-index", type=int, required=True)
    parser.add_argument("--gpu-uuid", required=True)
    parser.add_argument("--sf1-hybrid-fixed-ratio", type=float, required=True)
    parser.add_argument("--sf10-hybrid-fixed-ratio", type=float, required=True)
    parser.add_argument("--working-directory", type=Path, default=Path.cwd())
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = RunConfig(
        output_dir=args.output_dir,
        session_cli=args.session_cli,
        sf1_evidence=args.sf1_evidence,
        sf10_evidence=args.sf10_evidence,
        sf1_data=args.sf1_data,
        sf10_data=args.sf10_data,
        gpu_index=args.gpu_index,
        gpu_uuid=args.gpu_uuid,
        sf1_fixed_ratio=args.sf1_hybrid_fixed_ratio,
        sf10_fixed_ratio=args.sf10_hybrid_fixed_ratio,
        working_directory=args.working_directory,
        dry_run=args.dry_run,
    )
    try:
        root, executable, scales = _validate_config(config)
        profiles = _profile_plan(
            root,
            executable,
            config.working_directory.resolve(),
            config.gpu_index,
            config.gpu_uuid,
            scales,
        )
        if config.dry_run:
            print(
                json.dumps(
                    _public_plan(root, config.gpu_index, profiles, True),
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        root.mkdir(parents=True, exist_ok=True)
        _execute(config, root, executable, scales, profiles)
    except (OSError, OrchestrationError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "output_dir": str(root),
                "profile_count": len(profiles),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
