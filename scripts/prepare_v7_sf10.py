#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


SCALE_FACTOR = "10"
RAW_MANIFEST = "sf10_raw_manifest.json"
Q5_MANIFEST = "memq5_manifest.json"
ARROW_MANIFEST = "manifest.json"
TABLES = ("region", "nation", "supplier", "customer", "orders", "lineitem")


@dataclass(frozen=True)
class Sf10Paths:
    raw: Path
    q5: Path
    arrow: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _directory_bytes(path: Path) -> int:
    return sum(entry.stat().st_size for entry in path.rglob("*") if entry.is_file())


def _read_manifest(path: Path) -> dict[str, object]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid manifest: {path}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"invalid manifest: {path}")
    return parsed


def _require_file_entries(directory: Path, entries: dict[str, object]) -> None:
    expected = {f"{table}.tbl" for table in TABLES}
    if set(entries) != expected:
        raise ValueError("manifest does not describe all six expected tables")
    for name in expected:
        entry = entries[name]
        if not isinstance(entry, dict):
            raise ValueError(f"invalid manifest entry: {name}")
        path = directory / name
        if not path.is_file():
            raise ValueError(f"missing expected table: {path}")
        if entry.get("bytes") != path.stat().st_size:
            raise ValueError(f"byte count mismatch: {path}")
        if entry.get("sha256") != _sha256(path):
            raise ValueError(f"sha256 mismatch: {path}")


def _validate_raw(directory: Path) -> None:
    manifest = _read_manifest(directory / RAW_MANIFEST)
    if manifest.get("scale_factor") != SCALE_FACTOR:
        raise ValueError("raw manifest has wrong scale factor")
    tables = manifest.get("tables")
    if not isinstance(tables, dict):
        raise ValueError("raw manifest has no table entries")
    _require_file_entries(directory, tables)


def _validate_q5(directory: Path) -> None:
    manifest = _read_manifest(directory / Q5_MANIFEST)
    if manifest.get("scale_factor") != SCALE_FACTOR:
        raise ValueError("Q5 manifest has wrong scale factor")
    expected = {f"{table}.tbl" for table in TABLES}
    if set(manifest.get("required_tables", [])) != expected:
        raise ValueError("Q5 manifest does not describe all six expected tables")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ValueError("Q5 manifest has no file entries")
    _require_file_entries(directory, files)


def _validate_arrow(directory: Path) -> None:
    manifest = _read_manifest(directory / ARROW_MANIFEST)
    if manifest.get("scale_factor") != SCALE_FACTOR:
        raise ValueError("Arrow manifest has wrong scale factor")
    if manifest.get("batch_rows") != 262144:
        raise ValueError("Arrow manifest has wrong batch_rows")
    tables = manifest.get("tables")
    if not isinstance(tables, dict) or set(tables) != set(TABLES):
        raise ValueError("Arrow manifest does not describe all six expected tables")
    for table in TABLES:
        entry = tables[table]
        if not isinstance(entry, dict) or entry.get("file") != f"{table}.arrow":
            raise ValueError(f"invalid Arrow manifest entry: {table}")
        path = directory / f"{table}.arrow"
        if not path.is_file():
            raise ValueError(f"missing expected Arrow table: {path}")
        if entry.get("bytes") != path.stat().st_size:
            raise ValueError(f"byte count mismatch: {path}")
        if entry.get("sha256") != _sha256(path):
            raise ValueError(f"sha256 mismatch: {path}")


def _stage_assessment(directory: Path, manifest_name: str, validator: object) -> tuple[str, str | None]:
    if not directory.exists():
        return "missing", None
    if not directory.is_dir():
        return "invalid", f"output path is not a directory: {directory}"
    if not any(directory.iterdir()):
        return "missing", None
    manifest_path = directory / manifest_name
    if not manifest_path.is_file():
        return "invalid", f"nonempty unmanifested output: {directory}"
    try:
        validator(directory)
    except ValueError as exc:
        return "invalid", str(exc)
    return "complete", None


def _assess_stages(paths: Sf10Paths) -> dict[str, tuple[str, str | None]]:
    return {
        "raw": _stage_assessment(paths.raw, RAW_MANIFEST, _validate_raw),
        "q5": _stage_assessment(paths.q5, Q5_MANIFEST, _validate_q5),
        "arrow": _stage_assessment(paths.arrow, ARROW_MANIFEST, _validate_arrow),
    }


def stage_state(paths: Sf10Paths) -> dict[str, str]:
    return {name: state for name, (state, _) in _assess_stages(paths).items()}


def preflight(paths: Sf10Paths, minimum_free_bytes: int) -> dict[str, object]:
    free_bytes = shutil.disk_usage(paths.raw.parent).free
    if free_bytes < minimum_free_bytes:
        raise ValueError(
            f"insufficient free space: {free_bytes} bytes available, "
            f"{minimum_free_bytes} bytes required"
        )

    assessments = _assess_stages(paths)
    for name, (state, reason) in assessments.items():
        if state == "invalid":
            raise ValueError(f"invalid {name} stage: {reason}")

    return {
        "free_bytes": free_bytes,
        "minimum_free_bytes": minimum_free_bytes,
        "stage_state": {name: state for name, (state, _) in assessments.items()},
    }


def _require_post_stage_free_space(directory: Path, minimum_free_bytes: int, stage: str) -> int:
    free_bytes = shutil.disk_usage(directory).free
    if free_bytes < minimum_free_bytes:
        raise ValueError(
            f"insufficient free space after {stage} stage: {free_bytes} bytes available, "
            f"{minimum_free_bytes} bytes required"
        )
    return free_bytes


def _raw_manifest(raw_dir: Path, command: list[str]) -> None:
    tables = {}
    for table in TABLES:
        path = raw_dir / f"{table}.tbl"
        if not path.is_file():
            raise ValueError(f"dbgen did not create expected table: {path}")
        tables[path.name] = {"bytes": path.stat().st_size, "sha256": _sha256(path)}
    (raw_dir / RAW_MANIFEST).write_text(
        json.dumps(
            {"scale_factor": SCALE_FACTOR, "command": command, "tables": tables},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _run_stage(name: str, command: list[str], cwd: Path, output_dir: Path) -> dict[str, object]:
    cwd.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    completed = subprocess.run(command, cwd=cwd, check=False)
    elapsed_seconds = time.monotonic() - started
    record = {
        "name": name,
        "status": "complete" if completed.returncode == 0 else "failed",
        "command": command,
        "return_code": completed.returncode,
        "elapsed_seconds": elapsed_seconds,
        "output_bytes": _directory_bytes(output_dir) if output_dir.exists() else 0,
        "free_bytes": shutil.disk_usage(cwd).free,
    }
    if completed.returncode != 0:
        raise RuntimeError(json.dumps(record, sort_keys=True))
    return record


def prepare_sf10(args: argparse.Namespace) -> dict[str, object]:
    paths = Sf10Paths(Path(args.raw_dir), Path(args.q5_dir), Path(args.arrow_dir))
    minimum_free_bytes = int(args.minimum_free_gib * 1024**3)
    result = preflight(paths, minimum_free_bytes)
    stages: list[dict[str, object]] = []

    commands = {
        "raw": [str(Path(args.dbgen).resolve()), "-s", SCALE_FACTOR, "-f"],
        "q5": [
            sys.executable,
            "scripts/prepare_tpch_q5_data.py",
            "--source-dir",
            str(paths.raw),
            "--output-dir",
            str(paths.q5),
            "--scale-factor",
            SCALE_FACTOR,
        ],
        "arrow": [
            sys.executable,
            "scripts/prepare_arrow_dataset.py",
            "--input",
            str(paths.q5),
            "--output",
            str(paths.arrow),
            "--scale-factor",
            SCALE_FACTOR,
            "--batch-rows",
            "262144",
            "--source-command",
            "dbgen -s 10 -f",
        ],
    }
    if paths.q5.exists():
        commands["q5"].append("--force")
    if paths.arrow.exists():
        commands["arrow"].append("--replace")

    for name, output_dir in (("raw", paths.raw), ("q5", paths.q5), ("arrow", paths.arrow)):
        state = stage_state(paths)[name]
        if state == "complete":
            continue
        if args.dry_run:
            stages.append({"name": name, "status": "dry-run", "command": commands[name]})
            continue
        cwd = paths.raw if name == "raw" else Path.cwd()
        record = _run_stage(name, commands[name], cwd, output_dir)
        if name == "raw":
            _raw_manifest(paths.raw, commands[name])
            record["output_bytes"] = _directory_bytes(paths.raw)
        if stage_state(paths)[name] != "complete":
            raise ValueError(f"{name} stage completed without a valid manifest")
        record["free_bytes"] = _require_post_stage_free_space(
            paths.raw.parent, minimum_free_bytes, name
        )
        stages.append(record)

    result["stages"] = stages
    result["stage_state"] = stage_state(paths)
    result["free_bytes"] = shutil.disk_usage(paths.raw.parent).free
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare manifest-validated TPC-H SF10 inputs for V7")
    parser.add_argument("--dbgen", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/tpch_sf10_raw"))
    parser.add_argument("--q5-dir", type=Path, default=Path("data/tpch_sf10"))
    parser.add_argument("--arrow-dir", type=Path, default=Path("data/tpch_sf10_arrow"))
    parser.add_argument("--minimum-free-gib", type=float, default=40.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    try:
        summary = prepare_sf10(parse_args())
    except (RuntimeError, ValueError) as exc:
        try:
            failure = json.loads(str(exc))
        except json.JSONDecodeError:
            failure = {"error": str(exc), "status": "failed"}
        if not isinstance(failure, dict):
            failure = {"error": str(exc), "status": "failed"}
        print(json.dumps(failure, indent=2, sort_keys=True))
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
