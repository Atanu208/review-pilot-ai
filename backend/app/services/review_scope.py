"""Decide which changed files are in scope for a review (pure functions, no I/O)."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any

# Paths that are never worth reviewing: generated, vendored, lock or binary-like files.
DEFAULT_EXCLUDES: tuple[str, ...] = (
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "Pipfile.lock",
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.snap",
    "node_modules/*",
    "vendor/*",
    "dist/*",
    "build/*",
    "*/node_modules/*",
    "*/vendor/*",
    "*/dist/*",
    "*.pb.go",
    "*_pb2.py",
    "*.generated.*",
)
MAX_FILES_IN_STATS = 300


@dataclass(slots=True)
class ScopedFile:
    path: str
    status: str
    additions: int
    deletions: int
    patch: str | None
    previous_path: str | None
    in_scope: bool
    skip_reason: str | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "additions": self.additions,
            "deletions": self.deletions,
            "in_scope": self.in_scope,
            "skip_reason": self.skip_reason,
        }


@dataclass(slots=True)
class ReviewScope:
    files: list[ScopedFile]
    max_files: int
    max_diff_lines: int
    truncated: bool = False
    skipped: dict[str, int] = field(default_factory=dict)

    @property
    def in_scope(self) -> list[ScopedFile]:
        return [f for f in self.files if f.in_scope]

    def stats(self) -> dict[str, Any]:
        scoped = self.in_scope
        return {
            "files_total": len(self.files),
            "files_in_scope": len(scoped),
            "files_skipped": dict(sorted(self.skipped.items())),
            "additions": sum(f.additions for f in self.files),
            "deletions": sum(f.deletions for f in self.files),
            "diff_lines_in_scope": sum(f.additions + f.deletions for f in scoped),
            "truncated": self.truncated,
            "limits": {"max_files": self.max_files, "max_diff_lines": self.max_diff_lines},
            "files": [f.summary() for f in self.files[:MAX_FILES_IN_STATS]],
        }


def _matches(path: str, patterns: tuple[str, ...] | list[str]) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(name, p) for p in patterns)


def build_scope(
    raw_files: list[dict[str, Any]],
    *,
    ignored_paths: list[str],
    max_files: int,
    max_diff_lines: int,
) -> ReviewScope:
    """Classify GitHub ``pulls/{n}/files`` entries; never mutates the input."""
    scope = ReviewScope(files=[], max_files=max_files, max_diff_lines=max_diff_lines)
    budget_files, budget_lines = max_files, max_diff_lines

    def skip(entry: ScopedFile, reason: str) -> None:
        entry.in_scope = False
        entry.skip_reason = reason
        scope.skipped[reason] = scope.skipped.get(reason, 0) + 1

    for raw in raw_files:
        entry = ScopedFile(
            path=str(raw.get("filename", "")),
            status=str(raw.get("status", "modified")),
            additions=int(raw.get("additions") or 0),
            deletions=int(raw.get("deletions") or 0),
            patch=raw.get("patch"),
            previous_path=raw.get("previous_filename"),
            in_scope=True,
        )
        scope.files.append(entry)
        lines = entry.additions + entry.deletions
        if entry.status == "removed":
            skip(entry, "removed")
        elif _matches(entry.path, ignored_paths):
            skip(entry, "ignored_path")
        elif _matches(entry.path, DEFAULT_EXCLUDES):
            skip(entry, "generated_or_vendored")
        elif not entry.patch:
            skip(entry, "no_patch")  # binary, renamed-only, or too large for GitHub to diff
        elif budget_files <= 0 or lines > budget_lines:
            skip(entry, "over_limit")
            scope.truncated = True
        else:
            budget_files -= 1
            budget_lines -= lines
    return scope
