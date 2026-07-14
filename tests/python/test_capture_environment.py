from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_capture_binds_git_cli_cudf_and_gpu_identity(
    tmp_path: Path, monkeypatch
) -> None:
    import scripts.capture_environment as module

    session_cli = tmp_path / "memq5_arrow_session"
    session_cli.write_bytes(b"resident-cli")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "2")

    original = module.run_command

    def fake_run(command: list[str]) -> dict:
        if command[:2] == ["nvidia-smi", "--query-gpu=index,uuid,name,driver_version"]:
            return {
                "available": True,
                "command": command,
                "returncode": 0,
                "stdout": "0, GPU-abc, NVIDIA RTX 4090, 555.42",
                "stderr": "",
            }
        if command[:4] == ["conda", "run", "-n", "memq5-cudf"]:
            return {
                "available": True,
                "command": command,
                "returncode": 0,
                "stdout": json.dumps(
                    {"prefix": "/envs/memq5-cudf", "python": "3.11", "cudf": "25.06"}
                ),
                "stderr": "",
            }
        return original(command)

    monkeypatch.setattr(module, "run_command", fake_run)

    captured = module.capture(
        project_root=ROOT,
        session_cli=session_cli,
        cudf_env="memq5-cudf",
        gpu_index=0,
    )

    assert captured["git"]["commit"]
    assert captured["session_cli"] == {
        "path": str(session_cli.resolve()),
        "sha256": hashlib.sha256(b"resident-cli").hexdigest(),
    }
    assert captured["cudf_environment"]["name"] == "memq5-cudf"
    assert captured["cudf_environment"]["details"]["cudf"] == "25.06"
    assert captured["gpu"]["requested_index"] == 0
    assert captured["gpu"]["cuda_visible_devices"] == "2"
    assert captured["gpu"]["devices"] == [
        {
            "index": 0,
            "uuid": "GPU-abc",
            "name": "NVIDIA RTX 4090",
            "driver_version": "555.42",
        }
    ]
