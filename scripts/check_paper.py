#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


STALE_RESULT_HASH = "9f1f5f7578dd816e"
REQUIRED_GENERATED_MARKERS = (
    r"\newcommand{\FormalResultHash}{542abf4003633c7c}",
    r"\newcommand{\FormalMeasuredRuns}{190}",
    r"\newcommand{\FormalWarmupRuns}{57}",
    r"\newcommand{\CpuBestQueryMedian}{61.414}",
    r"\newcommand{\HybridBestQueryMedian}{222.832}",
)
PROVENANCE_FILE = "paper.provenance.json"
PROVENANCE_INPUTS = ("paper.tex", "generated/results.tex", "paper.pdf")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_paper_provenance(repo_root: Path) -> Path:
    paper_dir = repo_root.resolve() / "docs/paper"
    payload = {
        "schema_version": 1,
        "sha256": {name: _sha256(paper_dir / name) for name in PROVENANCE_INPUTS},
    }
    output = paper_dir / PROVENANCE_FILE
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return output


def check_paper_provenance(repo_root: Path) -> list[str]:
    paper_dir = repo_root.resolve() / "docs/paper"
    provenance = paper_dir / PROVENANCE_FILE
    if not provenance.is_file():
        return [f"missing publication file: docs/paper/{PROVENANCE_FILE}"]
    try:
        payload = json.loads(provenance.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"invalid paper provenance: {error}"]
    errors: list[str] = []
    if payload.get("schema_version") != 1 or not isinstance(payload.get("sha256"), dict):
        return ["invalid paper provenance schema"]
    for name in PROVENANCE_INPUTS:
        path = paper_dir / name
        if not path.is_file():
            errors.append(f"paper provenance input missing: {name}")
        elif payload["sha256"].get(name) != _sha256(path):
            errors.append(f"paper provenance checksum mismatch: {name}")
    return errors


def check_source_text(text: str) -> list[str]:
    errors: list[str] = []
    if re.search(r"\b(?:pending|tbd|todo)\b", text, flags=re.IGNORECASE):
        errors.append("paper contains a pending/TBD/TODO marker")
    if STALE_RESULT_HASH in text:
        errors.append(f"paper contains stale result hash: {STALE_RESULT_HASH}")
    return errors


def check_paper(repo_root: Path) -> list[str]:
    paper_dir = repo_root / "docs/paper"
    source = paper_dir / "paper.tex"
    generated = paper_dir / "generated/results.tex"
    pdf = paper_dir / "paper.pdf"
    evidence_manifest = repo_root / "docs/artifacts/v5_sf1/manifest.json"
    evidence_digest = repo_root / "docs/artifacts/v5_sf1/manifest.sha256"
    errors: list[str] = []

    for path in (source, generated, pdf, evidence_manifest, evidence_digest):
        if not path.is_file():
            errors.append(f"missing publication file: {path.relative_to(repo_root)}")
    if errors:
        return errors

    source_text = source.read_text(encoding="utf-8")
    generated_text = generated.read_text(encoding="utf-8")
    errors.extend(check_source_text(source_text + "\n" + generated_text))
    if r"\input{generated/results.tex}" not in source_text:
        errors.append("paper does not import generated/results.tex")
    for marker in REQUIRED_GENERATED_MARKERS:
        if marker not in generated_text:
            errors.append(f"generated results missing marker: {marker}")

    manifest_sha = hashlib.sha256(evidence_manifest.read_bytes()).hexdigest()
    expected_digest = f"{manifest_sha}  manifest.json"
    if evidence_digest.read_text(encoding="ascii").strip() != expected_digest:
        errors.append("formal evidence manifest digest mismatch")
    if f"% manifest_sha256={manifest_sha}" not in generated_text:
        errors.append("generated results do not identify the formal evidence manifest")

    pdf_bytes = pdf.read_bytes()
    if len(pdf_bytes) < 10_000 or not pdf_bytes.startswith(b"%PDF"):
        errors.append("paper.pdf is missing or does not look like a built PDF")
    errors.extend(check_paper_provenance(repo_root))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the evidence-linked paper")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--write-provenance", action="store_true")
    args = parser.parse_args()
    if args.write_provenance:
        output = write_paper_provenance(args.repo_root)
        print(f"wrote {output}")
        return 0
    errors = check_paper(args.repo_root.resolve())
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("paper check ok: evidence-linked source and built PDF")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
