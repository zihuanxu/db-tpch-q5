#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from pathlib import Path

try:
    from scripts.import_paper_evidence import import_v7_evidence
except ModuleNotFoundError:
    from import_paper_evidence import import_v7_evidence


STALE_RESULT_HASH = "9f1f5f7578dd816e"
REQUIRED_GENERATED_MARKERS = (
    r"\newcommand{\VSevenSfOneResultHash}{542abf4003633c7c}",
    r"\newcommand{\VSevenSfTenResultHash}{b1351a421ba8dcfd}",
    r"\newcommand{\VSevenSfOneMeasuredRuns}{180}",
    r"\newcommand{\VSevenSfTenMeasuredRuns}{180}",
    r"\newcommand{\SfOneGpuCopyMedian}{1.267}",
    r"\newcommand{\SfTenHybridBestMedian}{10.054}",
    r"\newcommand{\SfOneHybridAutoRegretPercent}{33.89}",
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
    for stale in ("正式实验只做SF1", "没有继续做SF10", "resident 生命周期未实现"):
        if stale in text:
            errors.append(f"paper contains stale V5-only statement: {stale}")
    return errors


def check_generated_results(repo_root: Path, generated: Path) -> list[str]:
    try:
        with tempfile.TemporaryDirectory(prefix="memq5-paper-evidence-") as directory:
            regenerated = import_v7_evidence(
                repo_root / "docs/artifacts/v7_sf1_resident",
                repo_root / "docs/artifacts/v7_sf10_resident",
                repo_root / "docs/artifacts/v7_hybrid_model/memq5-v7-hybrid-model.json",
                repo_root / "docs/research/CLAIM_LEDGER.md",
                Path(directory),
                repo_root,
            )
            if generated.read_bytes() != regenerated.read_bytes():
                return ["generated/results.tex does not match audited evidence regeneration"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [f"cannot regenerate generated/results.tex from audited evidence: {error}"]
    return []


def check_paper(repo_root: Path) -> list[str]:
    paper_dir = repo_root / "docs/paper"
    source = paper_dir / "paper.tex"
    generated = paper_dir / "generated/results.tex"
    pdf = paper_dir / "paper.pdf"
    evidence = (
        repo_root / "docs/artifacts/v7_sf1_resident",
        repo_root / "docs/artifacts/v7_sf10_resident",
    )
    model = repo_root / "docs/artifacts/v7_hybrid_model/memq5-v7-hybrid-model.json"
    errors: list[str] = []

    required = [source, generated, pdf, model]
    for directory in evidence:
        required.extend([directory / "manifest.json", directory / "manifest.sha256"])
    for path in required:
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
    errors.extend(check_generated_results(repo_root, generated))

    for label, directory in (("sf1", evidence[0]), ("sf10", evidence[1])):
        manifest = directory / "manifest.json"
        digest = directory / "manifest.sha256"
        manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
        expected_digest = f"{manifest_sha}  manifest.json"
        if digest.read_text(encoding="ascii").strip() != expected_digest:
            errors.append(f"{label} formal evidence manifest digest mismatch")
        if f"% {label}_manifest_sha256={manifest_sha}" not in generated_text:
            errors.append(f"generated results do not identify the {label} evidence manifest")
    model_sha = _sha256(model)
    if f"% model_sha256={model_sha}" not in generated_text:
        errors.append("generated results do not identify the hybrid model")

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
