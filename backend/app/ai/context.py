"""Context retrieval: numbered, diff-focused excerpts packed into token-budgeted batches."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.ai.router import estimate_tokens
from app.ai.safety import Fence, redact_secrets
from app.analysis import treesitter
from app.analysis.source import SourceFile

CONTEXT_LINES = 3
MAX_LINE_CHARS = 300


@dataclass(slots=True)
class FileExcerpt:
    path: str
    language: str
    text: str  # rendered "<n> <+| > <code>" lines with "..." gaps
    added_lines: set[int]

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


@dataclass(slots=True)
class ReviewUnit:
    excerpts: list[FileExcerpt] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return sum(e.tokens for e in self.excerpts) + 40 * len(self.excerpts)

    @property
    def languages(self) -> list[str]:
        return sorted({e.language for e in self.excerpts})

    def render(self, fence: Fence) -> str:
        return "\n\n".join(fence.wrap(f"{e.path} ({e.language})", e.text) for e in self.excerpts)


def _windows(lines: set[int], total: int, extra: set[int]) -> list[tuple[int, int]]:
    spans = sorted(
        (max(1, n - CONTEXT_LINES), min(total, n + CONTEXT_LINES)) for n in lines if n <= total
    )
    spans += [(n, n) for n in extra if 1 <= n <= total]
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def excerpt(src: SourceFile, *, max_tokens: int) -> list[FileExcerpt]:
    """Render ``src`` around its changed lines, split into pieces under ``max_tokens``."""
    added = {n for n in src.diff.added_lines if 1 <= n <= len(src.lines)}
    if not added:
        return []
    headers: set[int] = set()
    try:
        for line in sorted(added)[:50]:
            scope = treesitter.enclosing_scope(src, line)
            if scope:
                headers.add(scope[0])  # show the enclosing def/class signature line
    except Exception:  # context is best-effort; parsing failures must not block review
        headers = set()

    pieces: list[FileExcerpt] = []
    current: list[str] = []
    current_added: set[int] = set()
    budget_chars = max_tokens * 3

    def flush() -> None:
        if current:
            pieces.append(
                FileExcerpt(src.path, src.language, "\n".join(current), set(current_added))
            )
            current.clear()
            current_added.clear()

    previous_end = 0
    used = 0
    for start, end in _windows(added, len(src.lines), headers):
        if previous_end and start > previous_end + 1 and current:
            current.append("   ...")
        # Line by line, so even one huge window (e.g. a new 2,000-line file) is split.
        for n in range(start, end + 1):
            code = redact_secrets(src.lines[n - 1])[:MAX_LINE_CHARS]
            row = f"{n:>5} {'+' if n in added else ' '} {code}"
            if current and used + len(row) > budget_chars:
                flush()
                used = 0
            current.append(row)
            used += len(row) + 1
            if n in added:
                current_added.add(n)
        previous_end = end
    flush()
    return pieces


def build_units(
    sources: list[SourceFile], *, max_tokens: int, max_units: int
) -> tuple[list[ReviewUnit], int]:
    """Greedy-pack file excerpts into units; returns (units, excerpts dropped over the cap)."""
    units: list[ReviewUnit] = []
    current = ReviewUnit()
    dropped = 0
    for src in sources:
        for piece in excerpt(src, max_tokens=max_tokens):
            if current.excerpts and current.tokens + piece.tokens > max_tokens:
                units.append(current)
                current = ReviewUnit()
            current.excerpts.append(piece)
    if current.excerpts:
        units.append(current)
    if len(units) > max_units:
        dropped = sum(len(u.excerpts) for u in units[max_units:])
        units = units[:max_units]
    return units, dropped
