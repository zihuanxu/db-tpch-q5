#!/usr/bin/env python3

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

try:
    from scripts.release_audit import audit_compact_profiler_evidence
except ModuleNotFoundError:
    from release_audit import audit_compact_profiler_evidence


EXCLUDED_ROOTS = {
    ".git",
    ".superpowers",
    "data",
    "dist",
    "hashjoin-cpu",
    "results",
}
EXCLUDED_DOC_PREFIXES = (
    Path("docs/assets"),
    Path("docs/superpowers"),
    Path("docs/artifacts/mvp_sf1"),
)
EXCLUDED_DOCS = {
    Path("docs/FINAL_REPORT.docx"),
    Path("docs/FINAL_REPORT.md"),
    Path("docs/FINAL_REPORT.pdf"),
    Path("docs/FINAL_REPORT_DRAFT.md"),
    Path("docs/INTEGRATED_SUBMISSION.docx"),
    Path("docs/INTEGRATED_SUBMISSION.md"),
}
REQUIRED_SUBMISSION_PATHS = (
    Path("README.md"),
    Path("LICENSE"),
    Path("CITATION.cff"),
    Path("docs/paper/paper.pdf"),
    Path("docs/paper/paper.provenance.json"),
    Path("docs/paper/paper.tex"),
    Path("docs/paper/generated/results.tex"),
    Path("docs/artifacts/v7_sf1_resident/manifest.json"),
    Path("docs/artifacts/v7_sf1_resident/manifest.sha256"),
    Path("docs/artifacts/v7_sf10_resident/manifest.json"),
    Path("docs/artifacts/v7_sf10_resident/manifest.sha256"),
    Path("docs/artifacts/v7_hybrid_model/memq5-v7-hybrid-model.json"),
    Path("docs/artifacts/v7_profiler/summary.json"),
    Path("docs/artifacts/v7_profiler/checksums.sha256"),
    Path("docs/research/CLAIM_LEDGER.md"),
    Path("scripts/release_audit.py"),
)
COMPACT_PROFILER_ROOT = Path("docs/artifacts/v7_profiler")


def _under(path: Path, prefix: Path) -> bool:
    return path == prefix or prefix in path.parents


def excluded_from_submission(path: Path) -> bool:
    if not path.parts:
        return True
    root = path.parts[0]
    if root in EXCLUDED_ROOTS or root == "build" or root.startswith("build-"):
        return True
    if path in EXCLUDED_DOCS or any(_under(path, prefix) for prefix in EXCLUDED_DOC_PREFIXES):
        return True
    if _under(path, Path("docs/paper")) and path.suffix in {".aux", ".log", ".out"}:
        return True
    return False


def tracked_files(repo_root: Path = Path.cwd()) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
    )
    files: list[Path] = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = Path(os.fsdecode(raw_path))
        source = repo_root / path
        if source.is_file() and not source.is_symlink() and not excluded_from_submission(path):
            files.append(path)
    return sorted(set(files), key=lambda value: value.as_posix())


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _add_bytes(archive: tarfile.TarFile, name: str, data: bytes, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    archive.addfile(info, io.BytesIO(data))


def _audit_compact_source(repo_root: Path, files: list[Path]) -> None:
    compact = repo_root / COMPACT_PROFILER_ROOT
    selected = set(files)
    selected_compact = {path for path in selected if _under(path, COMPACT_PROFILER_ROOT)}
    if not compact.exists() and not selected_compact:
        return
    errors = audit_compact_profiler_evidence(compact)
    if errors:
        raise ValueError(f"compact profiler preflight failed: {'; '.join(errors)}")
    source_compact = {
        COMPACT_PROFILER_ROOT / path.relative_to(compact)
        for path in compact.rglob("*")
        if path.is_file()
    }
    omitted = sorted(source_compact - selected, key=lambda path: path.as_posix())
    if omitted:
        raise ValueError(f"submission omits compact profiler file: {omitted[0]}")


def create_archive(
    repo_root: Path,
    output: Path,
    root_name: str,
    files: list[Path],
) -> None:
    _audit_compact_source(repo_root, files)
    output.parent.mkdir(parents=True, exist_ok=True)
    payloads: list[tuple[Path, bytes, int]] = []
    manifest_lines: list[str] = []
    for path in sorted(set(files), key=lambda value: value.as_posix()):
        if path.is_absolute() or ".." in path.parts or excluded_from_submission(path):
            raise ValueError(f"unsafe submission path: {path}")
        source = repo_root / path
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"submission source is not a regular file: {path}")
        data = source.read_bytes()
        mode = 0o755 if source.stat().st_mode & 0o100 else 0o644
        payloads.append((path, data, mode))
        manifest_lines.append(f"{_sha256(data)}  {path.as_posix()}")
    manifest = ("\n".join(manifest_lines) + "\n").encode("utf-8")

    with output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for path, data, mode in payloads:
                    _add_bytes(archive, f"{root_name}/{path.as_posix()}", data, mode)
                _add_bytes(archive, f"{root_name}/MANIFEST.sha256", manifest)

    digest = _sha256(output.read_bytes())
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{digest}  {output.name}\n", encoding="ascii"
    )


def audit_archive(
    archive_path: Path,
    root_name: str,
    required_paths: tuple[Path, ...] = (),
) -> list[str]:
    errors: list[str] = []
    prefix = f"{root_name}/"
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)):
            errors.append("archive contains duplicate paths")
        for member in members:
            pure = PurePosixPath(member.name)
            if member.name.startswith("/") or ".." in pure.parts or not member.isfile():
                errors.append(f"unsafe archive member: {member.name}")
            if not member.name.startswith(prefix):
                errors.append(f"archive member outside root: {member.name}")

        manifest_name = f"{root_name}/MANIFEST.sha256"
        try:
            manifest_file = archive.extractfile(manifest_name)
            assert manifest_file is not None
            lines = manifest_file.read().decode("utf-8").splitlines()
        except (KeyError, UnicodeDecodeError, AssertionError):
            return errors + ["archive lacks a readable MANIFEST.sha256"]
        expected: dict[str, str] = {}
        for line in lines:
            digest, separator, path = line.partition("  ")
            if not separator or len(digest) != 64:
                errors.append(f"invalid manifest line: {line}")
                continue
            expected[path] = digest
        actual_relative = {
            name[len(prefix) :]
            for name in names
            if name != manifest_name and name.startswith(prefix)
        }
        if set(expected) != actual_relative:
            errors.append("archive manifest file set does not match payload")
        for path, digest in expected.items():
            try:
                member_file = archive.extractfile(f"{root_name}/{path}")
            except KeyError:
                errors.append(f"missing archive payload: {path}")
                continue
            if member_file is None or _sha256(member_file.read()) != digest:
                errors.append(f"archive checksum mismatch: {path}")
        for required in required_paths:
            if required.as_posix() not in actual_relative:
                errors.append(f"archive missing required path: {required}")
        for path in actual_relative:
            if excluded_from_submission(Path(path)):
                errors.append(f"archive contains excluded path: {path}")

        compact_prefix = f"{COMPACT_PROFILER_ROOT.as_posix()}/"
        requires_compact = any(
            _under(required, COMPACT_PROFILER_ROOT) for required in required_paths
        )
        compact_members = [
            member
            for member in members
            if member.isfile()
            and member.name.startswith(prefix + compact_prefix)
            and ".." not in PurePosixPath(member.name).parts
        ]
        if compact_members or requires_compact:
            with tempfile.TemporaryDirectory(prefix="memq5-compact-audit-") as temporary:
                compact_root = Path(temporary)
                for member in compact_members:
                    relative_name = member.name[len(prefix + compact_prefix) :]
                    relative = PurePosixPath(relative_name)
                    if not relative_name or relative.is_absolute() or ".." in relative.parts:
                        continue
                    member_file = archive.extractfile(member)
                    if member_file is None:
                        errors.append(f"missing archive payload: {member.name[len(prefix):]}")
                        continue
                    destination = compact_root.joinpath(*relative.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(member_file.read())
                errors.extend(
                    f"archive {error}"
                    for error in audit_compact_profiler_evidence(compact_root)
                )

    sidecar = archive_path.with_suffix(archive_path.suffix + ".sha256")
    expected_sidecar = f"{_sha256(archive_path.read_bytes())}  {archive_path.name}"
    if not sidecar.is_file() or sidecar.read_text(encoding="ascii").strip() != expected_sidecar:
        errors.append("archive sidecar checksum is missing or incorrect")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an auditable MEMQ5 V7 archive")
    parser.add_argument("--output", type=Path, default=Path("dist/memory-db-tpch-q5-v7.tar.gz"))
    parser.add_argument("--root-name", default="memory-db-tpch-q5")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.repo_root.resolve()
    files = tracked_files(root)
    missing = [path for path in REQUIRED_SUBMISSION_PATHS if path not in files]
    if missing:
        for path in missing:
            print(f"ERROR: required submission path is missing: {path}")
        return 1
    create_archive(root, args.output, args.root_name, files)
    errors = audit_archive(args.output, args.root_name, REQUIRED_SUBMISSION_PATHS)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"wrote {args.output} ({len(files)} files, archive audit passed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
