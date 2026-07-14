from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts import run_formal_profilers


SESSION_COMMIT = "a" * 40
GPU_UUID = "GPU-test-uuid"
HASHES = {"1": "542abf4003633c7c", "10": "b1351a421ba8dcfd"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _make_inputs(tmp_path: Path) -> dict[str, Path]:
    executable = tmp_path / "build/memq5_arrow_session"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"\x7fELF\x02\x01NVTX session fixture\n")
    executable.chmod(0o755)

    values: dict[str, Path] = {"session_cli": executable}
    for scale, auto_ratio, threads in (("1", 0.30, 8), ("10", 0.40, 16)):
        data = tmp_path / f"source-data/sf{scale}"
        data.mkdir(parents=True)
        table = data / "lineitem.arrow"
        table.write_bytes(f"arrow-sf{scale}\n".encode("ascii"))
        _write_json(
            data / "manifest.json",
            {
                "format_version": 1,
                "scale_factor": scale,
                "tables": {
                    "lineitem": {
                        "file": table.name,
                        "rows": 1,
                        "bytes": table.stat().st_size,
                        "sha256": _sha256(table),
                    }
                },
            },
        )

        evidence = tmp_path / f"evidence/sf{scale}"
        evidence.mkdir(parents=True)
        _write_json(
            evidence / "manifest.json",
            {
                "manifest_version": 1,
                "status": "complete",
                "identity": {
                    "git_commit": SESSION_COMMIT,
                    "session_cli_sha256": "b" * 64,
                    "gpu_uuid": GPU_UUID,
                },
                "dataset": {
                    "sha256": _sha256(data / "manifest.json"),
                    "scale_factor": scale,
                },
                "oracle": {"result_hash": HASHES[scale]},
                "correctness": {
                    "expected_hash": HASHES[scale],
                    "observed_hashes": [HASHES[scale]],
                },
            },
        )
        (evidence / "manifest.sha256").write_text(
            f"{_sha256(evidence / 'manifest.json')}  manifest.json\n", encoding="ascii"
        )
        (evidence / "setups.csv").write_text(
            "config_id,ratio_mode,status,selected_cpu_ratio,threads\n"
            f"hybrid-auto-t{threads:02d},auto,ok,{auto_ratio},{threads}\n",
            encoding="utf-8",
        )
        values[f"sf{scale}_data"] = data
        values[f"sf{scale}_evidence"] = evidence
    return values


def _argv(tmp_path: Path, inputs: dict[str, Path]) -> list[str]:
    return [
        "--output-dir",
        str(tmp_path / "profiler-bundle"),
        "--session-cli",
        str(inputs["session_cli"]),
        "--sf1-evidence",
        str(inputs["sf1_evidence"]),
        "--sf10-evidence",
        str(inputs["sf10_evidence"]),
        "--sf1-data",
        str(inputs["sf1_data"]),
        "--sf10-data",
        str(inputs["sf10_data"]),
        "--gpu-index",
        "0",
        "--gpu-uuid",
        GPU_UUID,
        "--sf1-hybrid-fixed-ratio",
        "0.5",
        "--sf10-hybrid-fixed-ratio",
        "0.5",
        "--working-directory",
        str(tmp_path),
    ]


def _successful_evidence_audit(_: Path) -> dict[str, object]:
    return {"ok": True, "errors": []}


def _collector_output_dir(command: list[str]) -> Path:
    return Path(command[command.index("--output-dir") + 1])


def _collector_metadata(command: list[str]) -> dict[str, object]:
    value = json.loads(command[command.index("--metadata-json") + 1])
    assert isinstance(value, dict)
    return value


def _fake_collectors(
    calls: list[list[str]],
    *,
    unavailable_profile: str | None = None,
    fatal_profile: str | None = None,
):
    def run(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert cwd.is_absolute()
        assert env["CUDA_VISIBLE_DEVICES"] == "0"
        output = _collector_output_dir(command)
        output.mkdir(parents=True, exist_ok=True)
        identity = _collector_metadata(command)
        profile_id = output.parent.name
        script = Path(command[1]).name
        if script == "profile_nsys.py":
            ratio = float(identity["cpu_ratio"])
            if identity["engine"] == "hybrid-auto":
                ratio = 0.31 if identity["scale_factor"] == "1" else 0.41
            records = [
                {
                    "record_type": "session_setup",
                    "status": "ok",
                    "selected_cpu_ratio": ratio,
                },
                {
                    "record_type": "request",
                    "status": "ok",
                    "selected_cpu_ratio": ratio,
                    "result_hash": identity["result_hash"],
                },
            ]
            (output / "profile.stdout.log").write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            _write_json(output / "metadata.json", {"metadata": identity})
            return subprocess.CompletedProcess(command, 0, "", "")

        if profile_id == unavailable_profile:
            (output / "profile.stdout.log").write_text("", encoding="utf-8")
            (output / "profile.stderr.log").write_text(
                "ERR_NVGPUCTRPERM: performance counter permission denied\n",
                encoding="utf-8",
            )
            _write_json(
                output / "metadata.json",
                {
                    "metadata": identity,
                    "return_code": 13,
                    "replay": {
                        "mode": "application",
                        "return_code": 13,
                        "succeeded": False,
                    },
                },
            )
            return subprocess.CompletedProcess(command, 1, "", "collector failed")
        if profile_id == fatal_profile:
            (output / "profile.stdout.log").write_text("", encoding="utf-8")
            (output / "profile.stderr.log").write_text(
                "Segmentation fault (core dumped)\n", encoding="utf-8"
            )
            _write_json(
                output / "metadata.json",
                {
                    "metadata": identity,
                    "return_code": 139,
                    "replay": {
                        "mode": "application",
                        "return_code": 139,
                        "succeeded": False,
                    },
                },
            )
            return subprocess.CompletedProcess(command, 1, "", "collector failed")

        _write_json(output / "metadata.json", {"metadata": identity, "return_code": 0})
        return subprocess.CompletedProcess(command, 0, "", "")

    return run


def test_nsys_observation_ignores_profiler_progress_lines(tmp_path: Path) -> None:
    output = tmp_path / "nsys"
    output.mkdir()
    setup = {
        "record_type": "session_setup",
        "status": "ok",
        "selected_cpu_ratio": 0.25,
    }
    request = {
        "record_type": "request",
        "status": "ok",
        "selected_cpu_ratio": 0.25,
        "result_hash": HASHES["1"],
    }
    (output / "profile.stdout.log").write_text(
        "Capture range started in the application.\n"
        "[1/1] [50%] profile.nsys-rep\n"
        + json.dumps(setup)
        + "\n"
        + json.dumps(request)
        + "\nGenerated:\n    /tmp/profile.nsys-rep\n",
        encoding="utf-8",
    )

    assert run_formal_profilers._nsys_observation(output, HASHES["1"]) == 0.25


def test_dry_run_prints_exact_commands_and_ten_bound_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = _make_inputs(tmp_path)
    monkeypatch.setattr(
        run_formal_profilers, "_audit_evidence_bundle", _successful_evidence_audit
    )
    monkeypatch.setattr(
        run_formal_profilers,
        "_run_command",
        lambda *_args, **_kwargs: pytest.fail("dry-run executed a collector"),
    )

    assert run_formal_profilers.main([*_argv(tmp_path, inputs), "--dry-run"]) == 0

    plan = json.loads(capsys.readouterr().out)
    assert plan["dry_run"] is True
    assert plan["profile_count"] == 10
    assert [profile["id"] for profile in plan["profiles"]] == [
        "sf1-copy",
        "sf1-managed",
        "sf1-mapped",
        "sf1-hybrid-fixed",
        "sf1-hybrid-auto",
        "sf10-copy",
        "sf10-managed",
        "sf10-mapped",
        "sf10-hybrid-fixed",
        "sf10-hybrid-auto",
    ]
    assert plan["environment"] == {"CUDA_VISIBLE_DEVICES": "0"}
    assert not (tmp_path / "profiler-bundle").exists()

    for profile in plan["profiles"]:
        command = profile["app_command"]
        assert command[command.index("--requests") + 1] == "1"
        assert "--warmup" not in command
        assert "--repeat" not in command
        assert profile["nsys_collector_command"][-len(command) :] == command
        assert profile["ncu_collector_command"][-len(command) :] == command
        assert Path(profile["nsys_collector_command"][1]).name == "profile_nsys.py"
        assert Path(profile["ncu_collector_command"][1]).name == "profile_ncu.py"
        identity = profile["identity"]
        assert identity["session_commit"] == SESSION_COMMIT
        assert identity["gpu_uuid"] == GPU_UUID
        assert identity["oracle_hash"] == HASHES[identity["scale_factor"]]
        assert identity["result_hash"] == identity["oracle_hash"]
        assert len(identity["execution"]["executable"]["sha256"]) == 64

    auto = plan["profiles"][4]
    assert auto["identity"]["cpu_ratio"] == pytest.approx(0.30)
    assert auto["identity_binding"]["cpu_ratio"] == "evidence_setup_then_nsys_observed"
    assert "--cpu-ratio" not in auto["app_command"]
    assert auto["app_command"][auto["app_command"].index("--threads") + 1] == "8"
    assert auto["app_command"][-2:] == ["--hybrid-selection", "auto"]


def test_success_stages_inputs_binds_observed_auto_ratio_and_audits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _make_inputs(tmp_path)
    calls: list[list[str]] = []
    finalized: list[Path] = []
    audited: list[Path] = []
    monkeypatch.setattr(
        run_formal_profilers, "_audit_evidence_bundle", _successful_evidence_audit
    )
    monkeypatch.setattr(run_formal_profilers, "_run_command", _fake_collectors(calls))
    monkeypatch.setattr(
        run_formal_profilers,
        "_finalize_bundle",
        lambda root: finalized.append(root) or 0,
    )
    monkeypatch.setattr(
        run_formal_profilers,
        "_audit_profiler_bundle",
        lambda root: audited.append(root) or 0,
    )

    assert run_formal_profilers.main(_argv(tmp_path, inputs)) == 0

    root = tmp_path / "profiler-bundle"
    assert finalized == [root.resolve()]
    assert audited == [root.resolve()]
    assert len(calls) == 20
    assert [Path(command[1]).name for command in calls[:4]] == [
        "profile_nsys.py",
        "profile_ncu.py",
        "profile_nsys.py",
        "profile_ncu.py",
    ]
    assert os.stat(root / "datasets/1/lineitem.arrow").st_ino == os.stat(
        inputs["sf1_data"] / "lineitem.arrow"
    ).st_ino

    source_evidence = root / "evidence/1/source-manifest.json"
    assert _sha256(source_evidence) == _sha256(
        inputs["sf1_evidence"] / "manifest.json"
    )
    evidence_reference = _read_json(root / "evidence/1/manifest.json")
    assert evidence_reference["source_evidence_manifest"] == {
        "path": "source-manifest.json",
        "sha256": _sha256(source_evidence),
    }

    index = _read_json(root / "profiles.json")
    assert index["status"] == "complete"
    assert len(index["profiles"]) == 10
    assert len(index["expected_profiles"]) == 10
    sf1_auto = next(
        profile
        for profile in index["profiles"]
        if profile["id"] == "sf1-hybrid-auto"
    )
    assert sf1_auto["cpu_ratio"] == pytest.approx(0.31)
    assert sf1_auto["gpu_ratio"] == pytest.approx(0.69)
    ncu_call = next(
        command
        for command in calls
        if Path(command[1]).name == "profile_ncu.py"
        and "sf1-hybrid-auto" in str(_collector_output_dir(command))
    )
    assert _collector_metadata(ncu_call)["cpu_ratio"] == pytest.approx(0.31)
    assert sf1_auto["execution"]["executable"]["sha256"] == _sha256(
        inputs["session_cli"]
    )
    assert sf1_auto["session_commit"] == SESSION_COMMIT
    assert sf1_auto["gpu_uuid"] == GPU_UUID
    assert sf1_auto["evidence_manifest"]["sha256"] == _sha256(
        root / sf1_auto["evidence_manifest"]["path"]
    )
    assert _read_json(root / "orchestration.json")["status"] == "complete"


def test_ncu_counter_permission_failure_is_the_only_unavailable_downgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _make_inputs(tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        run_formal_profilers, "_audit_evidence_bundle", _successful_evidence_audit
    )
    monkeypatch.setattr(
        run_formal_profilers,
        "_run_command",
        _fake_collectors(calls, unavailable_profile="sf1-managed"),
    )
    monkeypatch.setattr(run_formal_profilers, "_finalize_bundle", lambda _root: 0)
    monkeypatch.setattr(run_formal_profilers, "_audit_profiler_bundle", lambda _root: 0)

    assert run_formal_profilers.main(_argv(tmp_path, inputs)) == 0

    index = _read_json(tmp_path / "profiler-bundle/profiles.json")
    managed = next(profile for profile in index["profiles"] if profile["id"] == "sf1-managed")
    assert managed["ncu"]["status"] == "unavailable"
    assert len(calls) == 20
    run_state = _read_json(tmp_path / "profiler-bundle/orchestration.json")
    assert run_state["status"] == "complete"
    assert run_state["ncu_unavailable_profiles"] == ["sf1-managed"]


def test_other_collector_failure_stops_and_preserves_failure_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _make_inputs(tmp_path)
    calls: list[list[str]] = []
    finalized: list[Path] = []
    monkeypatch.setattr(
        run_formal_profilers, "_audit_evidence_bundle", _successful_evidence_audit
    )
    monkeypatch.setattr(
        run_formal_profilers,
        "_run_command",
        _fake_collectors(calls, fatal_profile="sf1-copy"),
    )
    monkeypatch.setattr(
        run_formal_profilers,
        "_finalize_bundle",
        lambda root: finalized.append(root) or 0,
    )

    assert run_formal_profilers.main(_argv(tmp_path, inputs)) == 1

    root = tmp_path / "profiler-bundle"
    assert len(calls) == 2
    assert finalized == []
    assert (root / "captures/sf1-copy/ncu/metadata.json").is_file()
    state = _read_json(root / "orchestration.json")
    assert state["status"] == "failed"
    assert state["failure"]["profile_id"] == "sf1-copy"
    assert state["failure"]["tool"] == "ncu"
    assert "Segmentation fault" in state["failure"]["message"]
    index = _read_json(root / "profiles.json")
    assert index["status"] == "failed"
    assert index["profiles"][0]["ncu"]["status"] == "failed"
    assert index["profiles"][1]["ncu"]["status"] == "not-run"


def test_evidence_must_audit_before_dry_run_or_output_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _make_inputs(tmp_path)
    monkeypatch.setattr(
        run_formal_profilers,
        "_audit_evidence_bundle",
        lambda _root: {"ok": False, "errors": ["checksum mismatch"]},
    )

    assert run_formal_profilers.main([*_argv(tmp_path, inputs), "--dry-run"]) == 1
    assert not (tmp_path / "profiler-bundle").exists()


def test_dataset_staging_failure_is_recorded_before_any_collector_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _make_inputs(tmp_path)
    monkeypatch.setattr(
        run_formal_profilers, "_audit_evidence_bundle", _successful_evidence_audit
    )
    monkeypatch.setattr(
        run_formal_profilers,
        "_run_command",
        lambda *_args, **_kwargs: pytest.fail("collector ran after staging failure"),
    )
    monkeypatch.setattr(
        run_formal_profilers.os,
        "link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("cross-device link")),
    )

    assert run_formal_profilers.main(_argv(tmp_path, inputs)) == 1

    root = tmp_path / "profiler-bundle"
    state = _read_json(root / "orchestration.json")
    assert state["status"] == "failed"
    assert "cannot hard-link dataset table" in state["failure"]["message"]
    index = _read_json(root / "profiles.json")
    assert index["status"] == "failed"
    assert all(profile["ncu"]["status"] == "not-run" for profile in index["profiles"])
