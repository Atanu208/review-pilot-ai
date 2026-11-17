"""Static analysis building blocks: diff parsing, languages, workspace, schema, engine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.analysis import treesitter
from app.analysis.diff import FileDiff, parse_patch
from app.analysis.engine import RuleConfig, StaticAnalysisEngine, dedupe, semgrep_to_finding
from app.analysis.languages import detect_language
from app.analysis.semgrep import known_rule_ids, parse_output
from app.analysis.source import SourceFile
from app.analysis.workspace import UnsafePathError, Workspace, safe_relative_path
from app.models import ReviewRule
from app.models.enums import FindingCategory, FindingSource, RuleEngine, Severity
from app.schemas.finding import Finding
from app.tests.fakes.semgrep import FakeSemgrep, match

# ---- diff parsing ----------------------------------------------------------------------


def test_parse_patch_tracks_added_lines_on_new_side() -> None:
    patch = "\n".join(
        [
            "@@ -1,4 +1,5 @@",
            " import os",
            "-old = 1",
            "+new = 1",
            "+extra = 2",
            " keep = 3",
            "@@ -20,2 +21,3 @@ def f():",
            " a",
            "+b",
            " c",
            "\\ No newline at end of file",
        ]
    )
    diff = parse_patch(patch)
    assert diff.added_lines == {2, 3, 22}
    assert diff.hunks == [(1, 5), (21, 23)]
    assert diff.touches(1, 2) and not diff.touches(4, 4)
    assert diff.in_hunk(23) and not diff.in_hunk(10)


def test_parse_patch_new_file_and_empty() -> None:
    assert parse_patch("@@ -0,0 +1,2 @@\n+a\n+b").added_lines == {1, 2}
    assert parse_patch(None).added_lines == set()
    assert parse_patch("@@ -1,2 +0,0 @@\n-a\n-b").added_lines == set()


# ---- languages -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "lang"),
    [
        ("app/main.py", "python"),
        ("src/Main.java", "java"),
        ("web/app.jsx", "javascript"),
        ("lib/x.mjs", "javascript"),
        ("ui/App.tsx", "typescript"),
        ("types/index.d.ts", None),
        ("README.md", None),
        ("Dockerfile", None),
    ],
)
def test_detect_language(path: str, lang: str | None) -> None:
    assert detect_language(path) == lang


def test_detect_language_from_shebang() -> None:
    assert detect_language("bin/tool", "#!/usr/bin/env python3\nprint(1)") == "python"
    assert detect_language("bin/cli", "#!/usr/bin/env node\n") == "javascript"


# ---- workspace safety ------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad", ["../etc/passwd", "/etc/passwd", "a/../../x", "C:/Windows/x", "a\\b.py", "", "a//b"]
)
def test_unsafe_paths_rejected(bad: str) -> None:
    with pytest.raises(UnsafePathError):
        safe_relative_path(bad)


def test_workspace_writes_inside_root_and_cleans_up() -> None:
    with Workspace() as ws:
        written = ws.write("pkg/mod.py", "x = 1\n")
        assert written.read_text() == "x = 1\n"
        assert ws.relative(str(written)) == "pkg/mod.py"
        root = ws.root
    assert not root.exists()


# ---- finding schema --------------------------------------------------------------------


def _finding(**kw: object) -> Finding:
    data: dict[str, object] = {
        "title": "Potential SQL Injection",
        "category": "security",
        "severity": "critical",
        "confidence": 0.96,
        "file_path": "app/repository.py",
        "start_line": 15,
        "end_line": 15,
        "description": "User-controlled input is interpolated into SQL.",
        "impact": "An attacker may manipulate database queries.",
        "suggested_fix": "Use parameterized queries.",
        "code_suggestion": "cursor.execute('... %s', (x,))",
        "source": "ai",
        "evidence": "cursor.execute(f'... {x}')",
    }
    data.update(kw)
    return Finding.model_validate(data)


def test_spec_example_finding_validates() -> None:
    f = _finding()
    assert f.severity == Severity.CRITICAL and f.source == FindingSource.AI


@pytest.mark.parametrize(
    "bad",
    [
        {"confidence": 1.2},
        {"start_line": 0},
        {"start_line": 10, "end_line": 9},
        {"file_path": "../secrets.txt"},
        {"file_path": "/abs/path.py"},
        {"severity": "urgent"},
        {"category": "vibes"},
        {"unexpected": "field"},
    ],
)
def test_invalid_findings_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _finding(**bad)


def test_fingerprint_ignores_line_numbers_and_whitespace() -> None:
    a = _finding(start_line=15, end_line=15, evidence="x  =  f(y)")
    b = _finding(start_line=40, end_line=40, evidence="x = f(y)")
    assert a.fingerprint() == b.fingerprint()
    assert a.fingerprint(0) != a.fingerprint(1)
    assert a.fingerprint() != _finding(file_path="other.py").fingerprint()


# ---- semgrep output + engine -----------------------------------------------------------


def test_all_rule_ids_are_namespaced_and_unique() -> None:
    ids = known_rule_ids()
    assert len(ids) >= 25
    assert len(ids) == len(set(ids))
    assert all(i.split(".")[0] in ("python", "js", "java") for i in ids)


def test_parse_output_maps_prefixed_check_ids_and_drops_unknown_rules() -> None:
    raw = json.dumps(
        {
            "results": [
                {
                    "check_id": "tmp.rules.python.sql-injection.string-built-query",
                    "path": "./app/db.py",
                    "start": {"line": 4},
                    "end": {"line": 4},
                    "extra": {"message": "msg", "severity": "ERROR", "metadata": {"title": "T"}},
                },
                {
                    "check_id": "injected.rule.not-ours",
                    "path": "app/db.py",
                    "start": {"line": 1},
                    "end": {"line": 1},
                    "extra": {},
                },
            ],
            "errors": [{"message": "parse error"}],
        }
    ).encode()
    matches, errors = parse_output(raw, ["python.sql-injection.string-built-query"])
    assert [(m.rule_id, m.path, m.start_line) for m in matches] == [
        ("python.sql-injection.string-built-query", "app/db.py", 4)
    ]
    assert errors == ["parse error"]


def _src(path: str, content: str, added: set[int] | None = None) -> SourceFile:
    diff = FileDiff()
    diff.added_lines = added if added is not None else set(range(1, len(content.splitlines()) + 1))
    lang = detect_language(path)
    assert lang is not None
    return SourceFile(path, lang, content, diff)


def test_semgrep_match_normalized_with_evidence_from_source() -> None:
    src = _src("app/db.py", "import x\ncursor.execute(f'SELECT {uid}')\n")
    f = semgrep_to_finding(match("python.sql-injection.string-built-query", "app/db.py", 2), src)
    assert f is not None
    assert (f.severity, f.category, f.confidence) == (
        Severity.CRITICAL,
        FindingCategory.SECURITY,
        0.9,
    )
    assert f.evidence == "cursor.execute(f'SELECT {uid}')"
    assert f.description.endswith("(CWE-89)")
    assert f.suggested_fix == "Use parameterised queries."


async def test_engine_reports_only_on_changed_lines() -> None:
    src = _src("app/db.py", "a = 1\nb = 2\nc = 3\n", added={2})
    sg = FakeSemgrep([match("r.x", "app/db.py", 1), match("r.x", "app/db.py", 2)])
    sg.rule_ids = ["r.x"]
    findings, stats = await StaticAnalysisEngine(sg).run_semgrep([src], RuleConfig())
    assert [f.start_line for f in findings] == [2]
    assert stats["status"] == "ok" and stats["raw_matches"] == 2
    assert sg.scanned == [["app/db.py"]]


async def test_engine_passes_disabled_rules_to_semgrep() -> None:
    sg = FakeSemgrep()
    await StaticAnalysisEngine(sg).run_semgrep(
        [_src("a.py", "x = 1\n")], RuleConfig(disabled={"r.off"})
    )
    assert sg.excluded == [{"r.off"}]


async def test_engine_reports_semgrep_unavailable() -> None:
    _, stats = await StaticAnalysisEngine(FakeSemgrep(status="unavailable")).run_semgrep(
        [_src("a.py", "x = 1\n")], RuleConfig()
    )
    assert stats["status"] == "unavailable"


def test_rule_config_repo_overrides_org_and_applies() -> None:
    org_rule = ReviewRule(
        rule_key="r.a", engine=RuleEngine.SEMGREP, enabled=False, repository_id=None
    )
    import uuid

    repo_rule = ReviewRule(
        rule_key="r.a", engine=RuleEngine.SEMGREP, enabled=True, repository_id=uuid.uuid4()
    )
    sev_rule = ReviewRule(
        rule_key="r.b",
        engine=RuleEngine.TREE_SITTER,
        enabled=True,
        severity_override=Severity.LOW,
        repository_id=None,
    )
    ai_rule = ReviewRule(rule_key="r.c", engine=RuleEngine.AI, enabled=False, repository_id=None)
    cfg = RuleConfig.from_rules([repo_rule, org_rule, sev_rule, ai_rule])
    assert cfg.disabled == {"r.c"}  # repo rule re-enables r.a; AI rules (r.c) apply too
    assert cfg.apply(_finding(rule_id="r.b", severity="high")).severity == Severity.LOW  # type: ignore[union-attr]
    assert RuleConfig(disabled={"r.z"}).apply(_finding(rule_id="r.z")) is None


def test_dedupe_drops_duplicates_and_gives_unique_fingerprints() -> None:
    same = _finding(rule_id="r", start_line=3, end_line=3)
    twin_code_elsewhere = _finding(rule_id="r", start_line=9, end_line=9)
    out = dedupe([same, same, twin_code_elsewhere])
    assert [f.start_line for f, _ in out] == [3, 9]
    assert len({fp for _, fp in out}) == 2


# ---- tree-sitter -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "code", "expected"),
    [
        (
            "a.py",
            "def f(x=[]):\n    return x\n",
            [("tree-sitter.python.mutable-default-argument", 1)],
        ),
        ("a.py", "try:\n    g()\nexcept:\n    raise\n", [("tree-sitter.python.bare-except", 3)]),
        (
            "a.py",
            "try:\n    g()\nexcept ValueError:\n    pass\n",
            [("tree-sitter.empty-exception-handler", 3)],
        ),
        ("a.py", "try:\n    g()\nexcept ValueError:\n    # expected\n    pass\n", []),
        ("a.py", "def ok(x=None, y=()):\n    return x\n", []),
        ("a.py", "def broken(:\n    pass\n", [("tree-sitter.syntax-error", 1)]),
        (
            "A.java",
            "class A { void m() { try { x(); } catch (Exception e) { } } }\n",
            [("tree-sitter.empty-exception-handler", 1)],
        ),
        ("a.js", "try { a() } catch (e) { console.error(e) }\n", []),
        ("a.ts", "try { a() } catch (e) {}\n", [("tree-sitter.empty-exception-handler", 1)]),
        ("a.tsx", "const A = () => <div>{1}</div>;\n", []),
    ],
)
def test_tree_sitter_checks(path: str, code: str, expected: list[tuple[str, int]]) -> None:
    found = [(f.rule_id, f.start_line) for f in treesitter.analyze_file(_src(path, code))]
    assert found == expected


def test_tree_sitter_ignores_unchanged_lines() -> None:
    assert treesitter.analyze_file(_src("a.py", "def f(x=[]):\n    return x\n", added={2})) == []


def test_enclosing_scope_finds_innermost_function() -> None:
    src = _src("a.py", "class C:\n    def m(self):\n        x = 1\n        return x\n")
    assert treesitter.enclosing_scope(src, 3) == (2, 4)


def test_rules_directory_ships_with_package() -> None:
    from app.analysis.semgrep import RULES_DIR

    assert {p.name for p in Path(RULES_DIR).glob("*.yml")} == {
        "python.yml",
        "javascript.yml",
        "java.yml",
    }
