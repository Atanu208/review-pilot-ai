"""Tree-sitter parsing: syntax errors and structural checks on changed code.

Uses manual tree walks (not queries) so behaviour is stable across tree-sitter releases.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import cache

import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language as TSLanguage
from tree_sitter import Node, Parser

from app.analysis.languages import Language, is_tsx
from app.analysis.source import SourceFile
from app.models.enums import FindingCategory, FindingSource, Severity
from app.schemas.finding import Finding

MAX_ERRORS_PER_FILE = 3


@cache
def _language(name: str) -> TSLanguage:
    loaders: dict[str, Callable[[], object]] = {
        "python": tree_sitter_python.language,
        "java": tree_sitter_java.language,
        "javascript": tree_sitter_javascript.language,
        "typescript": tree_sitter_typescript.language_typescript,
        "tsx": tree_sitter_typescript.language_tsx,
    }
    return TSLanguage(loaders[name]())


def parse(source: SourceFile) -> Node:
    grammar = "tsx" if source.language == "typescript" and is_tsx(source.path) else source.language
    if source.language == "javascript":
        grammar = "javascript"  # the JS grammar handles JSX
    parser = Parser(_language(grammar))
    return parser.parse(source.content.encode("utf-8")).root_node


def walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.children))


def _line(node: Node) -> tuple[int, int]:
    return node.start_point[0] + 1, node.end_point[0] + 1


@dataclass(slots=True, frozen=True)
class Check:
    rule_id: str
    title: str
    category: FindingCategory
    severity: Severity
    confidence: float
    description: str
    impact: str
    suggested_fix: str


SYNTAX_ERROR = Check(
    "tree-sitter.syntax-error",
    "Syntax error in changed code",
    FindingCategory.BUG,
    Severity.HIGH,
    0.9,
    "The parser could not understand this code; it will fail to compile or import.",
    "The module cannot be loaded, breaking every code path that imports it.",
    "Fix the syntax near this line (unbalanced brackets, missing colon/semicolon, stray text).",
)
BARE_EXCEPT = Check(
    "tree-sitter.python.bare-except",
    "Bare except clause",
    FindingCategory.BUG,
    Severity.MEDIUM,
    0.85,
    "`except:` catches every exception, including SystemExit and KeyboardInterrupt.",
    "Genuine errors and shutdown signals are silently swallowed, hiding bugs.",
    "Catch the specific exceptions you expect (e.g. `except ValueError:`), or `except Exception:`.",
)
MUTABLE_DEFAULT = Check(
    "tree-sitter.python.mutable-default-argument",
    "Mutable default argument",
    FindingCategory.BUG,
    Severity.MEDIUM,
    0.9,
    "A list, dict or set default is created once and shared by every call of the function.",
    "State leaks between calls, causing data to accumulate unexpectedly.",
    "Default to None and create the container inside the function.",
)
EMPTY_HANDLER = Check(
    "tree-sitter.empty-exception-handler",
    "Exception silently ignored",
    FindingCategory.QUALITY,
    Severity.MEDIUM,
    0.8,
    "This exception handler does nothing, so failures disappear without a trace.",
    "Errors go unnoticed and are much harder to diagnose in production.",
    "Handle the error, log it, or re-raise it; add a comment if ignoring is truly intended.",
)


def _finding(
    check: Check, src: SourceFile, start: int, end: int, *, code: str | None = None
) -> Finding:
    return Finding(
        title=check.title,
        category=check.category,
        severity=check.severity,
        confidence=check.confidence,
        file_path=src.path,
        start_line=start,
        end_line=end,
        description=check.description,
        impact=check.impact,
        suggested_fix=check.suggested_fix,
        code_suggestion=code,
        source=FindingSource.TREE_SITTER,
        evidence=src.snippet(start, end),
        rule_id=check.rule_id,
    )


def _syntax_errors(root: Node, src: SourceFile) -> list[Finding]:
    if not root.has_error:
        return []
    out: list[Finding] = []
    for node in walk(root):
        if node.is_error or node.is_missing:
            start, _ = _line(node)
            # Report a single line: error nodes can swallow the rest of the file.
            if src.diff.touches(start, start) and not any(f.start_line == start for f in out):
                out.append(_finding(SYNTAX_ERROR, src, start, start))
            if len(out) >= MAX_ERRORS_PER_FILE:
                break
    return out


def _is_noop_block(block: Node | None, comment_types: tuple[str, ...]) -> bool:
    if block is None:
        return False
    body = [c for c in block.named_children if c.type not in comment_types]
    has_comment = any(c.type in comment_types for c in block.named_children)
    if has_comment:
        return False  # an explanatory comment signals intent
    return not body or all(c.type == "pass_statement" for c in body)


def _python_checks(root: Node, src: SourceFile) -> list[Finding]:
    out: list[Finding] = []
    for node in walk(root):
        if node.type == "except_clause":
            start, _ = _line(node)
            block = next((c for c in node.named_children if c.type == "block"), None)
            others = [c for c in node.named_children if c.type not in ("block", "comment")]
            # tree-sitter-python attaches comments to the clause, not always to its block.
            commented = any(n.type == "comment" for n in walk(node))
            if not others and src.diff.touches(start, start):
                out.append(_finding(BARE_EXCEPT, src, start, start))
            elif (
                not commented
                and _is_noop_block(block, ("comment",))
                and src.diff.touches(start, start)
            ):
                out.append(_finding(EMPTY_HANDLER, src, start, start))
        elif node.type in ("default_parameter", "typed_default_parameter"):
            value = node.child_by_field_name("value")
            if value is not None and value.type in ("list", "dictionary", "set"):
                start, end = _line(node)
                if src.diff.touches(start, end):
                    out.append(_finding(MUTABLE_DEFAULT, src, start, end))
    return out


def _catch_checks(root: Node, src: SourceFile, comment_types: tuple[str, ...]) -> list[Finding]:
    out: list[Finding] = []
    for node in walk(root):
        if node.type == "catch_clause":
            body = node.child_by_field_name("body")
            start, _ = _line(node)
            if _is_noop_block(body, comment_types) and src.diff.touches(start, start):
                out.append(_finding(EMPTY_HANDLER, src, start, start))
    return out


_LANGUAGE_CHECKS: dict[Language, Callable[[Node, SourceFile], list[Finding]]] = {
    "python": _python_checks,
    "java": lambda root, src: _catch_checks(root, src, ("line_comment", "block_comment")),
    "javascript": lambda root, src: _catch_checks(root, src, ("comment",)),
    "typescript": lambda root, src: _catch_checks(root, src, ("comment",)),
}


def analyze_file(src: SourceFile) -> list[Finding]:
    root = parse(src)
    findings = _syntax_errors(root, src)
    # Structural checks on a broken tree produce noise; report the syntax error only.
    if not findings:
        findings.extend(_LANGUAGE_CHECKS[src.language](root, src))
    return findings


def enclosing_scope(src: SourceFile, line: int) -> tuple[int, int] | None:
    """Line range of the innermost function/method/class containing ``line`` (for AI context)."""
    scope_types = {
        "function_definition",
        "class_definition",  # python
        "method_declaration",
        "constructor_declaration",
        "class_declaration",  # java
        "function_declaration",
        "method_definition",
        "arrow_function",
        "function_expression",  # js/ts
    }
    best: tuple[int, int] | None = None
    for node in walk(parse(src)):
        if node.type in scope_types:
            start, end = _line(node)
            if start <= line <= end and (best is None or end - start < best[1] - best[0]):
                best = (start, end)
    return best
