from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import pytest

from scripts.prepare_v7_sf10 import Sf10Paths, parse_args, preflight, prepare_sf10, stage_state


TABLES = ("region", "nation", "supplier", "customer", "orders", "lineitem")


def _write_raw_manifest(directory: Path, scale_factor: str = "10") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    tables = {}
    for table in TABLES:
        path = directory / f"{table}.tbl"
        path.write_text(f"{table}|\n", encoding="utf-8")
        tables[f"{table}.tbl"] = {"bytes": path.stat().st_size}
    (directory / "sf10_raw_manifest.json").write_text(
        json.dumps({"scale_factor": scale_factor, "tables": tables}),
        encoding="utf-8",
    )


def _write_q5_manifest(directory: Path, scale_factor: str = "10", complete: bool = True) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    files = {}
    for table in TABLES if complete else TABLES[:-1]:
        path = directory / f"{table}.tbl"
        path.write_text(f"{table}|\n", encoding="utf-8")
        files[f"{table}.tbl"] = {"bytes": path.stat().st_size}
    (directory / "memq5_manifest.json").write_text(
        json.dumps({"scale_factor": scale_factor, "required_tables": list(files), "files": files}),
        encoding="utf-8",
    )


def _write_arrow_manifest(directory: Path, scale_factor: str = "10", complete: bool = True) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    tables = {}
    for table in TABLES if complete else TABLES[:-1]:
        path = directory / f"{table}.arrow"
        path.write_bytes(table.encode("ascii"))
        tables[table] = {"file": path.name, "bytes": path.stat().st_size}
    (directory / "manifest.json").write_text(
        json.dumps({"scale_factor": scale_factor, "tables": tables}),
        encoding="utf-8",
    )


def _paths(tmp_path: Path) -> Sf10Paths:
    return Sf10Paths(tmp_path / "raw", tmp_path / "q5", tmp_path / "arrow")


def test_preflight_rejects_less_than_40_gib(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "scripts.prepare_v7_sf10.shutil.disk_usage",
        lambda _: shutil._ntuple_diskusage(100 * 1024**3, 61 * 1024**3, 39 * 1024**3),
    )
    with pytest.raises(ValueError, match="free space"):
        preflight(_paths(tmp_path), 40 * 1024**3)


def test_preflight_rejects_nonempty_unmanifested_output(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.q5.mkdir()
    (paths.q5 / "partial.tbl").write_text("partial\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unmanifested"):
        preflight(paths, 0)


@pytest.mark.parametrize("writer", (_write_raw_manifest, _write_q5_manifest, _write_arrow_manifest))
def test_preflight_rejects_wrong_scale_factor(tmp_path: Path, writer: object) -> None:
    paths = _paths(tmp_path)
    writer(paths.raw if writer is _write_raw_manifest else paths.q5 if writer is _write_q5_manifest else paths.arrow, "1")

    with pytest.raises(ValueError, match="scale factor"):
        preflight(paths, 0)


@pytest.mark.parametrize("writer", (_write_raw_manifest, _write_q5_manifest, _write_arrow_manifest))
def test_preflight_rejects_incomplete_six_table_output(tmp_path: Path, writer: object) -> None:
    paths = _paths(tmp_path)
    if writer is _write_raw_manifest:
        writer(paths.raw)
        (paths.raw / "lineitem.tbl").unlink()
    else:
        writer(paths.q5 if writer is _write_q5_manifest else paths.arrow, complete=False)

    with pytest.raises(ValueError, match="expected table"):
        preflight(paths, 0)


def test_preflight_rejects_attempt_to_reuse_sf1_manifest(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_q5_manifest(paths.q5, scale_factor="1")

    with pytest.raises(ValueError, match="scale factor"):
        preflight(paths, 0)


def test_stage_state_identifies_complete_and_missing_stages(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_raw_manifest(paths.raw)
    _write_q5_manifest(paths.q5)

    assert stage_state(paths) == {"raw": "complete", "q5": "complete", "arrow": "missing"}


def test_prepare_dry_run_lists_only_missing_stages(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_raw_manifest(paths.raw)
    args = argparse.Namespace(
        dbgen=Path("dbgen"),
        raw_dir=paths.raw,
        q5_dir=paths.q5,
        arrow_dir=paths.arrow,
        minimum_free_gib=0,
        dry_run=True,
    )

    summary = prepare_sf10(args)

    assert [stage["name"] for stage in summary["stages"]] == ["q5", "arrow"]
    assert all(stage["status"] == "dry-run" for stage in summary["stages"])
    arrow_command = summary["stages"][1]["command"]
    assert arrow_command[arrow_command.index("--batch-rows") + 1] == "262144"


def test_prepare_resolves_dbgen_before_changing_to_raw_directory(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    args = argparse.Namespace(
        dbgen=Path("data/tpch_tools/TPC-H V3.0.1/dbgen/dbgen"),
        raw_dir=paths.raw,
        q5_dir=paths.q5,
        arrow_dir=paths.arrow,
        minimum_free_gib=0,
        dry_run=True,
    )

    summary = prepare_sf10(args)

    assert Path(summary["stages"][0]["command"][0]).is_absolute()


def test_defaults_follow_the_sf10_raw_q5_arrow_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["prepare_v7_sf10.py", "--dbgen", "dbgen"])

    args = parse_args()

    assert args.raw_dir == Path("data/tpch_sf10_raw")
    assert args.q5_dir == Path("data/tpch_sf10")
    assert args.arrow_dir == Path("data/tpch_sf10_arrow")
