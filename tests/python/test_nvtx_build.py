from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
RANGE_NAMES = {
    "session_setup",
    "host_staging",
    "allocation",
    "initial_h2d",
    "request",
    "cpu_scan",
    "output_reset",
    "managed_prefetch",
    "q5_kernel",
    "hybrid_gpu_request",
    "d2h",
    "merge",
}


def _run(command: list[str], *, cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True)
    assert completed.returncode == 0, (
        f"command failed: {' '.join(command)}\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    return completed


def _cache_hint(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    for cache in sorted(ROOT.glob("build*/CMakeCache.txt")):
        match = re.search(
            rf"^{re.escape(name)}:[^=]*=(.+)$",
            cache.read_text(encoding="utf-8", errors="replace"),
            re.MULTILINE,
        )
        if match and Path(match.group(1)).exists():
            return match.group(1)
    return None


def _configure(build_dir: Path, *, arrow: bool, cuda: bool, nvtx: bool) -> None:
    command = [
        "cmake",
        "-S",
        str(ROOT),
        "-B",
        str(build_dir),
        "-G",
        "Ninja",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DMEMQ5_ENABLE_TESTS=OFF",
        f"-DMEMQ5_ENABLE_ARROW={'ON' if arrow else 'OFF'}",
        f"-DMEMQ5_ENABLE_CUDA={'ON' if cuda else 'OFF'}",
        f"-DMEMQ5_ENABLE_NVTX={'ON' if nvtx else 'OFF'}",
    ]
    if arrow:
        for name in (
            "Arrow_DIR",
            "ArrowAcero_DIR",
            "ArrowCompute_DIR",
            "nlohmann_json_DIR",
            "OPENSSL_CRYPTO_LIBRARY",
            "OPENSSL_INCLUDE_DIR",
            "OPENSSL_SSL_LIBRARY",
        ):
            if value := _cache_hint(name):
                command.append(f"-D{name}={value}")
    completed = _run(command)
    assert "Manually-specified variables were not used" not in completed.stderr


def _binary_strings(path: Path) -> set[str]:
    return set(_run(["strings", "-n", "3", str(path)]).stdout.splitlines())


def test_declared_range_names_are_stable() -> None:
    source = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in (
            "src/cuda/q5_arrow_cuda.cu",
            "src/hybrid/q5_hybrid.cpp",
            "src/session/q5_cpu_session.cpp",
        )
    )
    declared = set(re.findall(r'NvtxRange\s+\w+\("([^"]+)"\)', source))
    assert declared == RANGE_NAMES


def test_nvtx_off_is_an_optimized_noop_without_nvtx_dependency(
    tmp_path: Path,
) -> None:
    build_dir = tmp_path / "off"
    _configure(build_dir, arrow=True, cuda=False, nvtx=False)
    _run(
        ["cmake", "--build", str(build_dir), "--target", "memq5_arrow_session"]
    )

    smoke_source = tmp_path / "nvtx_off_smoke.cpp"
    smoke_source.write_text(
        '#include "common/nvtx_range.hpp"\n'
        "int main() { memq5::NvtxRange range(\"request\"); return 0; }\n",
        encoding="utf-8",
    )
    smoke_binary = tmp_path / "nvtx_off_smoke"
    compiler = os.environ.get("CXX", "c++")
    _run(
        [
            compiler,
            "-std=c++17",
            "-O2",
            "-I",
            str(ROOT / "src"),
            str(smoke_source),
            "-o",
            str(smoke_binary),
        ]
    )

    assert "request" not in _binary_strings(smoke_binary)
    undefined = _run(["nm", "-u", str(smoke_binary)]).stdout.lower()
    assert "nvtx" not in undefined
    dependencies = _run(
        ["readelf", "-d", str(build_dir / "memq5_arrow_session")]
    ).stdout.lower()
    assert "nvtoolsext" not in dependencies


@pytest.mark.skipif(shutil.which("nvcc") is None, reason="CUDA compiler unavailable")
def test_nvtx_on_cuda_build_contains_all_declared_ranges(tmp_path: Path) -> None:
    build_dir = tmp_path / "on"
    _configure(build_dir, arrow=True, cuda=True, nvtx=True)
    _run(
        [
            "cmake",
            "--build",
            str(build_dir),
            "--target",
            "memq5_arrow_session",
            "--parallel",
            "2",
        ]
    )

    binary_strings = _binary_strings(build_dir / "memq5_arrow_session")
    missing = {
        name
        for name in RANGE_NAMES
        if not any(value == name or value.endswith("_" + name) for value in binary_strings)
    }
    assert not sorted(missing)
