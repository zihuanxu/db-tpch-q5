#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINES_DIR = ROOT / "baselines"
if str(BASELINES_DIR) not in sys.path:
    sys.path.insert(0, str(BASELINES_DIR))

from common import ResultRow, result_hash
from duckdb_q5 import Q5_SQL, QUERY_VERSION, TABLE_SPECS, run_q5

HASH_CHUNK_SIZE = 1024 * 1024


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_table_hashes(data_dir: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for name in TABLE_SPECS:
        path = data_dir / f"{name}.tbl"
        if not path.is_file():
            raise FileNotFoundError(path)
        hashes[name] = _sha256_file(path)
    return hashes


def _validate_round_trip(payload: dict) -> None:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("oracle rows must be a list")
    exact_rows = [
        ResultRow(str(row["nation"]), int(row["revenue_1e4"]))
        for row in rows
    ]
    if payload.get("result_hash") != result_hash(exact_rows):
        raise ValueError("oracle result hash does not match exact rows")
    if payload.get("sql_sha256") != hashlib.sha256(
        payload.get("sql", "").encode("utf-8")
    ).hexdigest():
        raise ValueError("oracle SQL hash does not match SQL text")


def write_oracle(data_dir: Path, output: Path, region: str, start_date: str) -> dict:
    try:
        import duckdb
    except ImportError as exc:
        raise SystemExit("duckdb Python package is not installed") from exc

    source_table_hashes = _source_table_hashes(data_dir)
    rows = run_q5(data_dir, region, start_date)
    report = {
        "query_version": QUERY_VERSION,
        "sql": Q5_SQL,
        "sql_sha256": hashlib.sha256(Q5_SQL.encode("utf-8")).hexdigest(),
        "source_table_hashes": source_table_hashes,
        "rows": [
            {"nation": row.nation, "revenue_1e4": row.revenue_1e4} for row in rows
        ],
        "result_hash": result_hash(rows),
        "duckdb_version": duckdb.__version__,
        "region": region,
        "date": start_date,
    }

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        with temp_path.open("r", encoding="utf-8") as handle:
            _validate_round_trip(json.load(handle))
        os.replace(temp_path, output)
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Write an auditable DuckDB TPC-H Q5 oracle")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--region", default="ASIA")
    parser.add_argument("--date", default="1994-01-01")
    args = parser.parse_args()

    report = write_oracle(Path(args.data_dir), Path(args.output), args.region, args.date)
    print(f"wrote rows={len(report['rows'])} result_hash={report['result_hash']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
