from pathlib import Path

from scripts.validate_process_docs import validate_process_docs


def test_repository_process_record_is_complete() -> None:
    root = Path(__file__).resolve().parents[2]
    errors = validate_process_docs(root / "docs/process")
    assert not errors, "\n".join(errors)
