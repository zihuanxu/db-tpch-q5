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
                "timing_build_ms,1.0",
                "timing_h2d_ms,0",
                "timing_kernel_ms,0.0",
                "timing_d2h_ms,0",
                "timing_scan_ms,2.0",
                "timing_total_ms,3.0",
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


def test_verify_q5_oracle_accepts_official_n_name_header(tmp_path: Path) -> None:
    actual = write_text(
        tmp_path / "actual.csv",
        "\n".join(
            [
                "nation,revenue_1e4,revenue",
                "INDONESIA,555020411697,55502041.17",
                "result_hash,542abf4003633c7c",
                "",
            ]
        ),
    )
    oracle = write_text(
        tmp_path / "q5.out",
        "\n".join(
            [
                "n_name                   |revenue",
                "INDONESIA                |55502041.17",
                "",
            ]
        ),
    )

    completed = run_verify(actual, oracle)

    assert completed.returncode == 0, completed.stderr


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


def test_verify_q5_oracle_rejects_non_timing_row_after_hash(tmp_path: Path) -> None:
    actual = write_text(
        tmp_path / "actual.csv",
        "\n".join(
            [
                "nation,revenue_1e4,revenue",
                "INDONESIA,555500,55.55",
                "result_hash,deadbeefcafebabe",
                "VIETNAM,123400,12.34",
                "",
            ]
        ),
    )
    oracle = write_text(
        tmp_path / "q5.out",
        "\n".join(["nation|revenue|", "INDONESIA|55.55|", ""]),
    )

    completed = run_verify(actual, oracle)

    assert completed.returncode != 0
    assert "unexpected row after result_hash" in completed.stderr


def test_verify_q5_oracle_rejects_revenue_without_exactly_two_decimals(tmp_path: Path) -> None:
    oracle = write_text(
        tmp_path / "q5.out",
        "\n".join(["nation|revenue|", "INDONESIA|55.55|", ""]),
    )
    malformed_actual = write_text(
        tmp_path / "actual.csv",
        "\n".join(
            [
                "nation,revenue_1e4,revenue",
                "INDONESIA,555000,55.5",
                "result_hash,deadbeefcafebabe",
                "",
            ]
        ),
    )

    actual_result = run_verify(malformed_actual, oracle)

    assert actual_result.returncode != 0
    assert "actual row 2 revenue must have exactly two decimal places" in actual_result.stderr

    valid_actual = write_text(
        tmp_path / "valid-actual.csv",
        "\n".join(
            [
                "nation,revenue_1e4,revenue",
                "INDONESIA,555500,55.55",
                "result_hash,deadbeefcafebabe",
                "",
            ]
        ),
    )
    malformed_oracle = write_text(
        tmp_path / "malformed-q5.out",
        "\n".join(["nation|revenue|", "INDONESIA|55.550|", ""]),
    )

    oracle_result = run_verify(valid_actual, malformed_oracle)

    assert oracle_result.returncode != 0
    assert "oracle row 2 revenue must have exactly two decimal places" in oracle_result.stderr


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
