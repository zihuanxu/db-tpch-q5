from pathlib import Path

from scripts.check_learning_links import check_learning_materials


def test_repository_learning_course_is_complete() -> None:
    root = Path(__file__).resolve().parents[2]
    errors = check_learning_materials(root / "docs/learning", root)
    assert not errors, "\n".join(errors)
