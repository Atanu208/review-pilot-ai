from __future__ import annotations

from typing import Any

from app.models import Repository, RepositorySettings
from app.services.review_scope import ReviewScope, build_scope
from app.services.reviews import effective_settings

PATCH = "@@ -1 +1 @@\n-a\n+b"


def f(
    path: str, adds: int = 3, dels: int = 1, patch: str | None = PATCH, status: str = "modified"
) -> dict[str, Any]:
    return {
        "filename": path,
        "status": status,
        "additions": adds,
        "deletions": dels,
        "patch": patch,
    }


def scope(
    files: list[dict[str, Any]],
    ignored: list[str] | None = None,
    max_files: int = 50,
    max_lines: int = 3000,
) -> ReviewScope:
    return build_scope(
        files, ignored_paths=ignored or [], max_files=max_files, max_diff_lines=max_lines
    )


def test_regular_source_files_are_in_scope() -> None:
    s = scope([f("app/main.py"), f("src/App.tsx")])
    assert [x.path for x in s.in_scope] == ["app/main.py", "src/App.tsx"]
    assert s.stats()["files_skipped"] == {}


def test_removed_generated_and_binary_files_are_skipped() -> None:
    s = scope(
        [
            f("old.py", status="removed"),
            f("package-lock.json"),
            f("web/node_modules/x/index.js"),
            f("static/app.min.js"),
            f("logo.png", patch=None),
            f("api/schema_pb2.py"),
            f("ok.py"),
        ]
    )
    reasons = {x.path: x.skip_reason for x in s.files}
    assert reasons == {
        "old.py": "removed",
        "package-lock.json": "generated_or_vendored",
        "web/node_modules/x/index.js": "generated_or_vendored",
        "static/app.min.js": "generated_or_vendored",
        "logo.png": "no_patch",
        "api/schema_pb2.py": "generated_or_vendored",
        "ok.py": None,
    }


def test_repository_ignored_paths_apply_to_path_and_basename() -> None:
    s = scope(
        [f("migrations/0001.py"), f("app/settings_local.py"), f("app/views.py")],
        ignored=["migrations/*", "*_local.py"],
    )
    assert [x.path for x in s.in_scope] == ["app/views.py"]
    assert s.skipped == {"ignored_path": 2}


def test_file_limit_truncates() -> None:
    s = scope([f(f"m{i}.py") for i in range(5)], max_files=3)
    assert len(s.in_scope) == 3
    assert s.truncated is True
    assert s.skipped == {"over_limit": 2}


def test_diff_line_budget_truncates_but_keeps_smaller_files() -> None:
    s = scope(
        [
            f("big.py", adds=90, dels=0),
            f("huge.py", adds=500, dels=0),
            f("small.py", adds=5, dels=0),
        ],
        max_lines=100,
    )
    assert [x.path for x in s.in_scope] == ["big.py", "small.py"]
    stats = s.stats()
    assert stats["diff_lines_in_scope"] == 95 and stats["truncated"] is True


def test_stats_shape() -> None:
    stats = scope([f("a.py", 2, 2), f("b.lock")]).stats()
    assert stats["files_total"] == 2 and stats["files_in_scope"] == 1
    assert stats["additions"] == 5 and stats["deletions"] == 3
    assert stats["files"][1] == {
        "path": "b.lock",
        "status": "modified",
        "additions": 3,
        "deletions": 1,
        "in_scope": False,
        "skip_reason": "generated_or_vendored",
    }


def test_effective_settings_defaults_without_row() -> None:
    repo = Repository(full_name="o/r")
    s = effective_settings(repo)
    assert s.auto_review_enabled is True and s.review_drafts is False
    assert s.max_files == 50 and s.max_diff_lines == 3000
    assert s.enabled_languages == ["python", "java", "javascript", "typescript"]
    assert s.ignored_paths == []


def test_effective_settings_uses_existing_row() -> None:
    repo = Repository(full_name="o/r")
    repo.settings = RepositorySettings(max_files=7)
    assert effective_settings(repo).max_files == 7
