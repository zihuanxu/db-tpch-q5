from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_q5_oracle.py"
UINT64_MASK = (1 << 64) - 1


def result_hash(rows: list[tuple[str, int]]) -> str:
    value = 14695981039346656037
    for nation, revenue_1e4 in rows:
        for byte in nation.encode("utf-8") + b"\xff":
            value = ((value ^ byte) * 1099511628211) & UINT64_MASK
        unsigned_revenue = revenue_1e4 & UINT64_MASK
        for shift in range(0, 64, 8):
            value = ((value ^ ((unsigned_revenue >> shift) & 0xFF)) * 1099511628211) & UINT64_MASK
    return f"{value:016x}"


HASH_TWO = result_hash([("INDONESIA", 555500), ("VIETNAM", 123400)])
HASH_ONE = result_hash([("INDONESIA", 555500)])
HASH_OFFICIAL_ONE = result_hash([("INDONESIA", 555020411697)])
HASH_MISMATCH = result_hash([("INDONESIA", 555500), ("VIETNAM", 123500)])
HASH_REVERSED = result_hash([("VIETNAM", 123400), ("INDONESIA", 555500)])


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
                f"result_hash,{HASH_TWO}",
                "timing_build_ms,1.0",
                "timing_h2d_ms,0",
                "timing_kernel_ms,0.0",
                "timing_d2h_ms,0",
                "timing_scan_ms,2.0",
                "timing_total_ms,3.0",
                "input_lineitem_rows,6001215",
                "matched_lineitem_rows,7243",
                "cpu_input_rows,6001215",
                "gpu_input_rows,0",
                "h2d_bytes,0",
                "d2h_bytes,0",
                "mapped_remote_read_bytes,0",
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
    assert completed.stdout.strip() == f"ok rows=2 result_hash={HASH_TWO}"


def test_verify_q5_oracle_accepts_official_n_name_header(tmp_path: Path) -> None:
    actual = write_text(
        tmp_path / "actual.csv",
        "\n".join(
            [
                "nation,revenue_1e4,revenue",
                "INDONESIA,555020411697,55502041.17",
                f"result_hash,{HASH_OFFICIAL_ONE}",
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
                f"result_hash,{HASH_MISMATCH}",
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
        "result_hash": HASH_MISMATCH,
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
                f"result_hash,{HASH_REVERSED}",
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
                f"result_hash,{HASH_ONE}",
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


def test_verify_q5_oracle_rejects_invalid_exact_revenue(tmp_path: Path) -> None:
    actual = write_text(
        tmp_path / "actual.csv",
        "\n".join(
            [
                "nation,revenue_1e4,revenue",
                "INDONESIA,not-an-integer,55.55",
                "result_hash,0000000000000000",
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
    assert "revenue_1e4 must be an int64" in completed.stderr


def test_verify_q5_oracle_rejects_tampered_hash(tmp_path: Path) -> None:
    actual = write_text(
        tmp_path / "actual.csv",
        "\n".join(
            [
                "nation,revenue_1e4,revenue",
                "INDONESIA,555500,55.55",
                "result_hash,0000000000000000",
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
    assert "result_hash does not match exact rows" in completed.stderr


def test_verify_q5_oracle_rejects_negative_or_fractional_counter(
    tmp_path: Path,
) -> None:
    oracle = write_text(
        tmp_path / "q5.out",
        "\n".join(["nation|revenue|", "INDONESIA|55.55|", ""]),
    )
    for value in ("-1", "1.5"):
        actual = write_text(
            tmp_path / f"actual-{value}.csv",
            "\n".join(
                [
                    "nation,revenue_1e4,revenue",
                    "INDONESIA,555500,55.55",
                    "result_hash,0000000000000000",
                    f"input_lineitem_rows,{value}",
                    "",
                ]
            ),
        )

        completed = run_verify(actual, oracle)

        assert completed.returncode != 0
        assert "counter must be a non-negative integer" in completed.stderr


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
                f"result_hash,{HASH_ONE}",
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
                f"result_hash,{HASH_ONE}",
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
                f"result_hash,{HASH_TWO}",
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
        "result_hash": HASH_TWO,
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
        f'  "result_hash": "{HASH_TWO}",',
        '  "row_count": 2',
        "}",
    ]
