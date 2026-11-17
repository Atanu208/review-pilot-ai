"""Language detection from file path (and shebang as a fallback)."""

from __future__ import annotations

from typing import Literal

Language = Literal["python", "java", "javascript", "typescript"]
SUPPORTED_LANGUAGES: tuple[Language, ...] = ("python", "java", "javascript", "typescript")

_EXTENSIONS: dict[str, Language] = {
    ".py": "python",
    ".pyi": "python",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
}


def detect_language(path: str, content: str | None = None) -> Language | None:
    name = path.rsplit("/", 1)[-1].lower()
    if name.endswith(".d.ts"):
        return None  # type declarations carry no runtime behaviour worth reviewing
    dot = name.rfind(".")
    if dot != -1:
        lang = _EXTENSIONS.get(name[dot:])
        if lang:
            return lang
    if content and content.startswith("#!"):
        first = content.split("\n", 1)[0]
        if "python" in first:
            return "python"
        if "node" in first:
            return "javascript"
    return None


def is_tsx(path: str) -> bool:
    return path.lower().endswith(".tsx")
