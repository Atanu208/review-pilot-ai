"""Unified-diff parsing: which new-file lines a pull request actually added or changed.

Findings are only reported on these lines, so we never comment on code the author
didn't touch, and line numbers always come from the diff, never from a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass(slots=True)
class FileDiff:
    added_lines: set[int] = field(default_factory=set)
    # (start, end) inclusive ranges of each hunk on the new side (context included).
    hunks: list[tuple[int, int]] = field(default_factory=list)

    def touches(self, start: int, end: int) -> bool:
        """True if any line in [start, end] was added/modified by the PR."""
        return any(line in self.added_lines for line in range(start, end + 1))

    def in_hunk(self, line: int) -> bool:
        return any(a <= line <= b for a, b in self.hunks)


def parse_patch(patch: str | None) -> FileDiff:
    diff = FileDiff()
    if not patch:
        return diff
    new_line = 0
    for raw in patch.splitlines():
        header = _HUNK.match(raw)
        if header:
            new_line = int(header.group(3))
            length = int(header.group(4)) if header.group(4) is not None else 1
            if length > 0:
                diff.hunks.append((new_line, new_line + length - 1))
            continue
        if not diff.hunks and new_line == 0:
            continue  # preamble before the first hunk
        if raw.startswith("+"):
            diff.added_lines.add(new_line)
            new_line += 1
        elif raw.startswith("-"):
            continue  # removed line: no position on the new side
        elif raw.startswith("\\"):
            continue  # "\ No newline at end of file"
        else:
            new_line += 1  # context line
    return diff
