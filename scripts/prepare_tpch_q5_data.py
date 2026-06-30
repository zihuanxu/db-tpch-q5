#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from validate_tpch_q5_data import validate


REQUIRED_TABLES = [
    "region.tbl",
    "nation.tbl",
    "supplier.tbl",
    "customer.tbl",
    "orders.tbl",
    "lineitem.tbl",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def count_rows(path: Path) -> int:
    rows = 0
    with path.open("rb") as handle:
        for line in handle:
            if line.strip():
                rows += 1
    return rows


def require_source_files(source_dir: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    missing: list[str] = []
    for table in REQUIRED_TABLES:
        path = source_dir / table
        if path.exists():
            files[table] = path
        else:
            missing.append(table)
    if missing:
        raise SystemExit(
            "missing required TPC-H Q5 files in "
            f"{source_dir}: {', '.join(missing)}"
        )
    return files


def prepare_output(output_dir: Path, force: bool) -> None:
    if output_dir.exists():
        if not force:
            raise SystemExit(f"output directory already exists: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)


def place_file(source: Path, dest: Path, mode: str) -> None:
    if mode == "copy":
        shutil.copy2(source, dest)
    elif mode == "symlink":
        dest.symlink_to(source.resolve())
    else:
        raise ValueError(f"unknown mode: {mode}")


def build_manifest(
    source_dir: Path,
    output_dir: Path,
    mode: str,
    scale_factor: str,
    validation_report: dict,
) -> dict:
    files = {}
    for table in REQUIRED_TABLES:
        path = output_dir / table
        files[table] = {
            "rows": count_rows(path),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "source": str((source_dir / table).resolve()),
        }

    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(source_dir.resolve()),
        "output_dir": str(output_dir.resolve()),
        "mode": mode,
        "scale_factor": scale_factor,
        "required_tables": REQUIRED_TABLES,
        "files": files,
        "validation": validation_report,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare an existing TPC-H dbgen output directory for MEMQ5"
    )
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=["copy", "symlink"], default="copy")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--scale-factor", default="unknown")
    parser.add_argument("--region", default="ASIA")
    parser.add_argument("--date", default="1994-01-01")
    args = parser.parse_args()

    source_files = require_source_files(args.source_dir)
    prepare_output(args.output_dir, args.force)

    for table, source in source_files.items():
        place_file(source, args.output_dir / table, args.mode)

    validation_report = validate(args.output_dir, args.region, args.date)
    manifest = build_manifest(
        args.source_dir,
        args.output_dir,
        args.mode,
        args.scale_factor,
        validation_report,
    )
    manifest_path = args.output_dir / "memq5_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"prepared {args.output_dir}")
    print(f"manifest {manifest_path}")
    print(f"validation ok: {validation_report['ok']}")
    if not validation_report["ok"]:
        for error in validation_report["errors"][:20]:
            print(f"- {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
