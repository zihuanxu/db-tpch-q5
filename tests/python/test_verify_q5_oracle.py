from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_q5_oracle.py"


def write_text(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def run_verify(actual: Path, oracle: Path, output_json: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(SCRIPT),
        "--actual",
        str(actual),
        "--oracle",
        str(oracle),
    ]
    if output_json is not None:
        command.extend(["--output-json", str(output_json)])

    return subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_verify_q5_oracle_accepts_matching_rows_and_hash(tmp_path: Path) -> None:
    actual = write_text(
        tmp_path / "actual.csv",
        "\n".join(
            [
                "nation,revenue_1e4,revenue",
                "INDONESIA,555500,55.55",
                "VIETNAM,123400,12.34",
                "result_hash,deadbeefcafebabe",
                "elapsed_ms,10.0",
                "",
            ]
        ),
    )
    oracle = write_text(
        tmp_path / "q5.out",
        "\n".join(
            [
                "nation|revenue|",
                "INDONESIA|55.55|",
                "VIETNAM|12.34|",
                "",
            ]
        ),
    )

    completed = run_verify(actual, oracle)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ok rows=2 result_hash=deadbeefcafebabe"


def test_verify_q5_oracle_reports_revenue_mismatch_and_writes_json(tmp_path: Path) -> None:
    actual = write_text(
        tmp_path / "actual.csv",
        "\n".join(
            [
                "nation,revenue_1e4,revenue",
                "INDONESIA,555500,55.55",
                "VIETNAM,123500,12.35",
                "result_hash,deadbeefcafebabe",
                "",
            ]
        ),
    )
    oracle = write_text(
        tmp_path / "q5.out",
        "\n".join(
            [
                "nation|revenue|",
                "INDONESIA|55.55|",
                "VIETNAM|12.34|",
                "",
            ]
        ),
    )
    output_json = tmp_path / "result.json"

    completed = run_verify(actual, oracle, output_json=output_json)

    assert completed.returncode != 0
    assert "row 2 revenue mismatch" in completed.stderr

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload == {
        "actual_rows": [
            {"nation": "INDONESIA", "revenue": "55.55"},
            {"nation": "VIETNAM", "revenue": "12.35"},
        ],
        "matched": False,
        "mismatch": {
            "actual": {"nation": "VIETNAM", "revenue": "12.35"},
            "expected": {"nation": "VIETNAM", "revenue": "12.34"},
            "message": "row 2 revenue mismatch",
            "row": 2,
        },
        "oracle_rows": [
            {"nation": "INDONESIA", "revenue": "55.55"},
            {"nation": "VIETNAM", "revenue": "12.34"},
        ],
        "result_hash": "deadbeefcafebabe",
        "row_count": 2,
    }


def test_verify_q5_oracle_reports_ordering_or_nation_mismatch(tmp_path: Path) -> None:
    actual = write_text(
        tmp_path / "actual.csv",
        "\n".join(
            [
                "nation,revenue_1e4,revenue",
                "VIETNAM,123400,12.34",
                "INDONESIA,555500,55.55",
                "result_hash,deadbeefcafebabe",
                "",
            ]
        ),
    )
    oracle = write_text(
        tmp_path / "q5.out",
        "\n".join(
            [
                "nation|revenue|",
                "INDONESIA|55.55|",
                "VIETNAM|12.34|",
                "",
            ]
        ),
    )

    completed = run_verify(actual, oracle)

    assert completed.returncode != 0
    assert "row 1 nation mismatch" in completed.stderr


def test_verify_q5_oracle_rejects_malformed_result_hash(tmp_path: Path) -> None:
    actual = write_text(
        tmp_path / "actual.csv",
        "\n".join(
            [
                "nation,revenue_1e4,revenue",
                "INDONESIA,555500,55.55",
                "result_hash,not-a-hash",
                "",
            ]
        ),
    )
    oracle = write_text(
        tmp_path / "q5.out",
        "\n".join(
            [
                "nation|revenue|",
                "INDONESIA|55.55|",
                "",
            ]
        ),
    )

    completed = run_verify(actual, oracle)

    assert completed.returncode != 0
    assert "expected exactly one 16-hex result_hash row" in completed.stderr


def test_verify_q5_oracle_writes_stable_sorted_json_on_match(tmp_path: Path) -> None:
    actual = write_text(
        tmp_path / "actual.csv",
        "\n".join(
            [
                "nation,revenue_1e4,revenue",
                "INDONESIA,555500,55.55",
                "VIETNAM,123400,12.34",
                "result_hash,deadbeefcafebabe",
                "",
            ]
        ),
    )
    oracle = write_text(
        tmp_path / "q5.out",
        "\n".join(
            [
                "nation|revenue|",
                "INDONESIA|55.55|",
                "VIETNAM|12.34|",
                "",
            ]
        ),
    )
    output_json = tmp_path / "result.json"

    completed = run_verify(actual, oracle, output_json=output_json)

    assert completed.returncode == 0, completed.stderr
    assert json.loads(output_json.read_text(encoding="utf-8")) == {
        "actual_rows": [
            {"nation": "INDONESIA", "revenue": "55.55"},
            {"nation": "VIETNAM", "revenue": "12.34"},
        ],
        "matched": True,
        "oracle_rows": [
            {"nation": "INDONESIA", "revenue": "55.55"},
            {"nation": "VIETNAM", "revenue": "12.34"},
        ],
        "result_hash": "deadbeefcafebabe",
        "row_count": 2,
    }
    assert output_json.read_text(encoding="utf-8").splitlines() == [
        "{",
        '  "actual_rows": [',
        "    {",
        '      "nation": "INDONESIA",',
        '      "revenue": "55.55"',
        "    },",
        "    {",
        '      "nation": "VIETNAM",',
        '      "revenue": "12.34"',
        "    }",
        "  ],",
        '  "matched": true,',
        '  "oracle_rows": [',
        "    {",
        '      "nation": "INDONESIA",',
        '      "revenue": "55.55"',
        "    },",
        "    {",
        '      "nation": "VIETNAM",',
        '      "revenue": "12.34"',
        "    }",
        "  ],",
        '  "result_hash": "deadbeefcafebabe",',
        '  "row_count": 2',
        "}",
    ]
