#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from validate_tpch_q5_data import validate


def run_command(command: list[str], cwd: Path, log_path: Path, allow_failure: bool = False) -> int:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    log_path.write_text(completed.stdout, encoding="utf-8")
    if completed.returncode != 0 and not allow_failure:
        print(completed.stdout)
        raise SystemExit(f"command failed ({completed.returncode}): {' '.join(command)}")
    return completed.returncode


def make_run_dir(output_root: Path, name: str, force: bool) -> Path:
    if not name:
        name = datetime.now().strftime("run_%Y%m%d_%H%M%S")
    run_dir = output_root / name
    if run_dir.exists():
        if not force:
            raise SystemExit(f"run directory already exists: {run_dir}")
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    (run_dir / "logs").mkdir()
    return run_dir


def write_readme(run_dir: Path, args: argparse.Namespace, validation_ok: bool) -> None:
    text = f"""# MEMQ5 Experiment Run

- name: `{run_dir.name}`
- data_dir: `{args.data_dir}`
- memq5: `{args.memq5}`
- engines: `{args.engines}`
- repeat: `{args.repeat}`
- threads: `{args.thread_list or args.threads}`
- region: `{args.region}`
- date: `{args.date}`
- validation_ok: `{validation_ok}`

## Files

- `validation.json`: TPC-H Q5 data validation report.
- `environment.json`: machine and software environment metadata.
- `benchmarks.csv`: raw benchmark rows.
- `hash_check.txt`: result-hash consistency check.
- `summary.md`: median/mean/min benchmark summary.
- `assets/summary.md`: report-ready summary table.
- `assets/total_time.svg`: total runtime figure.
- `assets/time_breakdown.svg`: execution breakdown figure.
"""
    (run_dir / "README.md").write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a complete MEMQ5 experiment pipeline")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--name", default="")
    parser.add_argument("--output-root", type=Path, default=Path("results/experiments"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--memq5", type=Path, default=Path("build/memq5"))
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--engines", default="cpu,python")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--thread-list", default="")
    parser.add_argument("--region", default="ASIA")
    parser.add_argument("--date", default="1994-01-01")
    parser.add_argument("--allow-benchmark-errors", action="store_true")
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    output_root = args.output_root
    if not output_root.is_absolute():
        output_root = project_root / output_root
    run_dir = make_run_dir(output_root, args.name, args.force)

    validation_report = validate(args.data_dir, args.region, args.date)
    (run_dir / "validation.json").write_text(
        json.dumps(validation_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if not validation_report["ok"]:
        write_readme(run_dir, args, False)
        raise SystemExit(f"data validation failed; see {run_dir / 'validation.json'}")

    run_command(
        [
            sys.executable,
            "scripts/capture_environment.py",
            "--output",
            str(run_dir / "environment.json"),
        ],
        project_root,
        run_dir / "logs" / "capture_environment.log",
    )

    benchmark_command = [
        sys.executable,
        "scripts/run_benchmarks.py",
        "--project-root",
        str(project_root),
        "--memq5",
        str(args.memq5),
        "--data-dir",
        str(args.data_dir),
        "--region",
        args.region,
        "--date",
        args.date,
        "--engines",
        args.engines,
        "--repeat",
        str(args.repeat),
        "--threads",
        str(args.threads),
        "--output",
        str(run_dir / "benchmarks.csv"),
    ]
    if args.thread_list:
        benchmark_command.extend(["--thread-list", args.thread_list])
    if not args.allow_benchmark_errors:
        benchmark_command.append("--fail-on-error")

    run_command(
        benchmark_command,
        project_root,
        run_dir / "logs" / "run_benchmarks.log",
        allow_failure=args.allow_benchmark_errors,
    )

    run_command(
        [
            sys.executable,
            "scripts/verify_benchmark_hashes.py",
            str(run_dir / "benchmarks.csv"),
        ],
        project_root,
        run_dir / "hash_check.txt",
    )

    with (run_dir / "summary.md").open("w", encoding="utf-8") as handle:
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/summarize_benchmarks.py",
                str(run_dir / "benchmarks.csv"),
                "--show-errors",
            ],
            cwd=project_root,
            text=True,
            stdout=handle,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if completed.returncode != 0:
        raise SystemExit("summary generation failed")

    run_command(
        [
            sys.executable,
            "scripts/make_report_assets.py",
            str(run_dir / "benchmarks.csv"),
            "--output-dir",
            str(run_dir / "assets"),
            "--title",
            run_dir.name,
        ],
        project_root,
        run_dir / "logs" / "make_report_assets.log",
    )

    write_readme(run_dir, args, True)
    print(f"experiment complete: {run_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
