from __future__ import annotations

import contextlib
import csv
import io
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINES_DIR = ROOT / "baselines"
SCRIPTS_DIR = ROOT / "scripts"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "tpch_q5_tiny"

for path in (BASELINES_DIR, SCRIPTS_DIR):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from arrow_q5 import run_q5 as run_arrow_q5
from common import ResultRow, emit_json, emit_rows, result_hash
from prepare_arrow_dataset import prepare_dataset
from python_q5 import run_q5 as run_python_q5
from run_benchmarks import write_rows


def test_result_contract_uses_raw_revenue_1e4_display_rounding_and_exact_hash() -> None:
    rows = [ResultRow("BRAZIL", 10049), ResultRow("ARGENTINA", -1050)]

    rows_stdout = io.StringIO()
    with contextlib.redirect_stdout(rows_stdout):
        emit_rows(rows)

    json_stdout = io.StringIO()
    with contextlib.redirect_stdout(json_stdout):
        emit_json(rows)

    rows_output = rows_stdout.getvalue()
    json_output = json_stdout.getvalue()

    assert rows_output.splitlines()[0] == "nation,revenue_1e4,revenue"
    assert "BRAZIL,10049,1.00" in rows_output
    assert "ARGENTINA,-1050,-0.11" in rows_output
    assert '"revenue_1e4": 10049' in json_output
    assert '"revenue": "-0.11"' in json_output
    assert result_hash(rows) != result_hash([ResultRow("BRAZIL", 10000), ResultRow("ARGENTINA", -1050)])


def test_python_and_arrow_match_tiny_exact_rows_and_hash(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "tiny-arrow"
    prepare_dataset(
        input_dir=FIXTURE_DIR,
        output_dir=dataset_dir,
        scale_factor="tiny",
        batch_rows=2,
        source_command="fixture",
        replace=False,
    )

    expected = [ResultRow("JAPAN", 1900000), ResultRow("INDIA", 900000)]

    python_rows = run_python_q5(FIXTURE_DIR, "ASIA", "1994-01-01")
    arrow_rows = run_arrow_q5(dataset_dir, "ASIA", "1994-01-01")

    assert python_rows == expected
    assert arrow_rows == expected
    assert result_hash(python_rows) == result_hash(arrow_rows)


def test_run_benchmarks_preserves_query_counters(tmp_path: Path) -> None:
    output = tmp_path / "counters.csv"
    write_rows(
        output,
        [
            {
                "run_id": "0",
                "status": "ok",
                "engine": "cpu-specialized",
                "input_lineitem_rows": "6",
                "matched_lineitem_rows": "2",
                "cpu_input_rows": "6",
                "gpu_input_rows": "0",
                "h2d_bytes": "0",
                "d2h_bytes": "0",
                "mapped_remote_read_bytes": "0",
            }
        ],
    )

    row = next(csv.DictReader(output.open("r", encoding="utf-8")))
    assert row["input_lineitem_rows"] == "6"
    assert row["matched_lineitem_rows"] == "2"
    assert row["cpu_input_rows"] == "6"
    assert row["mapped_remote_read_bytes"] == "0"


def test_run_benchmarks_requires_arrow_dataset_and_skips_warmups_in_csv(tmp_path: Path) -> None:
    dataset_dir = tmp_path / "tiny-arrow"
    prepare_dataset(
        input_dir=FIXTURE_DIR,
        output_dir=dataset_dir,
        scale_factor="tiny",
        batch_rows=2,
        source_command="fixture",
        replace=False,
    )

    missing_output = tmp_path / "missing.csv"
    missing = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "run_benchmarks.py"),
            "--project-root",
            str(ROOT),
            "--engines",
            "arrow",
            "--output",
            str(missing_output),
        ],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert missing.returncode == 0
    missing_rows = list(csv.DictReader(missing_output.open("r", encoding="utf-8")))
    assert len(missing_rows) == 1
    assert missing_rows[0]["status"] == "error"
    assert "--arrow-dataset is required" in missing_rows[0]["error"]

    output = tmp_path / "arrow.csv"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "run_benchmarks.py"),
            "--project-root",
            str(ROOT),
            "--engines",
            "arrow",
            "--arrow-dataset",
            str(dataset_dir),
            "--warmup",
            "2",
            "--repeat",
            "1",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert completed.returncode == 0, completed.stderr

    rows = list(csv.DictReader(output.open("r", encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["engine"] == "arrow"
    assert rows[0]["status"] == "ok"
