"""A changed source file as seen by analysers."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.analysis.diff import FileDiff
from app.analysis.languages import Language


@dataclass(slots=True)
class SourceFile:
    path: str
    language: Language
    content: str
    diff: FileDiff
    lines: list[str] = field(init=False)

    def __post_init__(self) -> None:
        self.lines = self.content.splitlines()

    def snippet(self, start: int, end: int, max_lines: int = 6, max_chars: int = 600) -> str:
        """Exact source text for [start, end] (1-based), trimmed for storage."""
        chunk = self.lines[max(start - 1, 0) : min(end, start - 1 + max_lines)]
        text = "\n".join(chunk)
        return text if len(text) <= max_chars else text[:max_chars] + "…"
