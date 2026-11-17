"""Materialise untrusted repository files into an isolated temporary directory.

The directory holds only the reviewed files (never a full checkout), paths are validated
so nothing can escape it, and it is deleted afterwards. Files are data for analysers;
nothing in it is ever executed.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path, PurePosixPath
from types import TracebackType


class UnsafePathError(ValueError):
    pass


def safe_relative_path(path: str) -> PurePosixPath:
    if not path or "\x00" in path or "\\" in path or "//" in path:
        raise UnsafePathError(f"unsafe path: {path!r}")
    p = PurePosixPath(path)
    if p.is_absolute() or any(part in ("..", "") for part in p.parts) or ":" in p.parts[0]:
        raise UnsafePathError(f"unsafe path: {path!r}")
    return p


class Workspace:
    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="reviewpilot-"))

    def write(self, path: str, content: str) -> Path:
        rel = safe_relative_path(path)
        target = (self.root / Path(*rel.parts)).resolve()
        if self.root.resolve() not in target.parents:
            raise UnsafePathError(f"path escapes workspace: {path!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
        return target

    def relative(self, absolute: str) -> str:
        return Path(absolute).resolve().relative_to(self.root.resolve()).as_posix()

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> Workspace:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.cleanup()
