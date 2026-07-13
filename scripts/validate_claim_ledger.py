#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


VALID_STATES = {"PLANNED", "IMPLEMENTED", "VERIFIED", "REJECTED", "SUPERSEDED"}
CLAIM_RE = re.compile(r"^C\d{3}$")


@dataclass(frozen=True)
class Claim:
    claim_id: str
    research_question: str
    statement: str
    state: str
    code: str
    test: str
    evidence: str
    paper_location: str
    limitation: str


@dataclass(frozen=True)
class LedgerReport:
    claims: tuple[Claim, ...]
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def _clean(value: str) -> str:
    value = value.strip()
    if value.startswith("`") and value.endswith("`"):
        value = value[1:-1]
    return value


def parse_ledger(path: Path) -> tuple[Claim, ...]:
    claims: list[Claim] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 9 or not CLAIM_RE.fullmatch(cells[0]):
            continue
        claims.append(Claim(*(_clean(cell) for cell in cells)))
    if not claims:
        raise ValueError("claim ledger contains no claim rows")
    return tuple(claims)


def _resolve(repo_root: Path, value: str) -> Path:
    return (repo_root / value).resolve()


def _check_path(repo_root: Path, claim: Claim, field: str, value: str) -> str | None:
    if value == "-":
        return None
    path = _resolve(repo_root, value)
    try:
        path.relative_to(repo_root.resolve())
    except ValueError:
        return f"{claim.claim_id} {field} leaves repository: {value}"
    if not path.exists():
        return f"{claim.claim_id} missing {field}: {value}"
    return None


def _check_evidence_digest(repo_root: Path, claim: Claim) -> str | None:
    evidence = _resolve(repo_root, claim.evidence)
    if not evidence.is_dir():
        return None
    manifest = evidence / "manifest.json"
    digest_file = evidence / "manifest.sha256"
    if not manifest.is_file() or not digest_file.is_file():
        return f"{claim.claim_id} evidence directory lacks manifest checksum"
    expected = f"{hashlib.sha256(manifest.read_bytes()).hexdigest()}  manifest.json"
    if digest_file.read_text(encoding="ascii").strip() != expected:
        return f"{claim.claim_id} evidence manifest digest mismatch"
    return None


def validate_ledger(path: Path, repo_root: Path) -> LedgerReport:
    try:
        claims = parse_ledger(path)
    except (OSError, ValueError) as error:
        return LedgerReport((), (str(error),))
    errors: list[str] = []
    ids = [claim.claim_id for claim in claims]
    if len(ids) != len(set(ids)):
        errors.append("claim ids must be unique")
    for claim in claims:
        if claim.state not in VALID_STATES:
            errors.append(f"{claim.claim_id} invalid state: {claim.state}")
            continue
        if claim.state in {"IMPLEMENTED", "VERIFIED"}:
            if claim.code == "-" or claim.test == "-":
                errors.append(f"{claim.claim_id} {claim.state} requires code and test")
        if claim.state in {"VERIFIED", "REJECTED"}:
            if claim.evidence == "-":
                errors.append(f"{claim.claim_id} {claim.state} requires evidence")
            if claim.limitation == "-" or not claim.limitation:
                errors.append(f"{claim.claim_id} {claim.state} requires limitation")
        if claim.state == "PLANNED" and claim.paper_location != "-":
            errors.append(f"{claim.claim_id} PLANNED cannot have a paper location")
        if claim.state == "SUPERSEDED" and not CLAIM_RE.search(claim.limitation):
            errors.append(f"{claim.claim_id} SUPERSEDED must name its replacement")
        for field, value in (
            ("code", claim.code),
            ("test", claim.test),
            ("evidence", claim.evidence),
            ("paper", claim.paper_location),
        ):
            error = _check_path(repo_root, claim, field, value)
            if error:
                errors.append(error)
        if claim.evidence != "-" and _resolve(repo_root, claim.evidence).exists():
            error = _check_evidence_digest(repo_root, claim)
            if error:
                errors.append(error)
    return LedgerReport(claims, tuple(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the research claim ledger")
    parser.add_argument("ledger", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    report = validate_ledger(args.ledger.resolve(), args.repo_root.resolve())
    if report.ok:
        print(f"claim ledger ok: {len(report.claims)} claims")
        return 0
    for error in report.errors:
        print(f"ERROR: {error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
