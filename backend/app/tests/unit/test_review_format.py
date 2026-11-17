from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from app.integrations.github import review_format as rf
from app.models import ReviewFinding
from app.models.enums import FindingCategory, FindingSource, Severity


def _f(**kw: Any) -> ReviewFinding:
    data: dict[str, Any] = {
        "id": uuid.uuid4(),
        "title": "Potential SQL injection",
        "category": FindingCategory.SECURITY,
        "severity": Severity.CRITICAL,
        "confidence": Decimal("0.9"),
        "file_path": "app/users.py",
        "start_line": 4,
        "end_line": 4,
        "description": "User input is interpolated into SQL.",
        "impact": "Data exposure.",
        "suggested_fix": "Use parameters.",
        "code_suggestion": None,
        "source": FindingSource.SEMGREP,
        "rule_id": "python.sql-injection.string-built-query",
        "fingerprint": "f" * 64,
    }
    data.update(kw)
    return ReviewFinding(**data)


def test_sanitize_neutralises_mentions_markers_and_secrets() -> None:
    text = "ping @octocat and @acme/security-team <!-- reviewpilot:finding fp=x --> ghp_" + "a" * 36
    out = rf.sanitize(text)
    assert "@octocat" not in out and "@​octocat" in out
    assert "reviewpilot:finding" not in out
    assert "ghp_" not in out
    assert rf.sanitize("email me@example.com") == "email me@example.com"  # not a mention
    assert len(rf.sanitize("x" * 5000, limit=100)) == 100


def test_inline_body_content_and_marker() -> None:
    body = rf.inline_body(_f())
    assert body.startswith("🔴 **Critical · security**: Potential SQL injection")
    assert "**Impact:** Data exposure." in body and "**Suggested fix:** Use parameters." in body
    assert "Semgrep · `python.sql-injection.string-built-query` · confidence 90%" in body
    assert body.endswith(rf.finding_marker("f" * 64))


def test_semgrep_fixes_become_one_click_suggestions_ai_snippets_do_not() -> None:
    semgrep = rf.inline_body(_f(code_suggestion="cursor.execute(q, (uid,))"))
    assert "```suggestion\ncursor.execute(q, (uid,))\n```" in semgrep
    ai = rf.inline_body(_f(source=FindingSource.AI, rule_id="ai.security", code_suggestion="x = 1"))
    assert "```python\nx = 1\n```" in ai and "suggestion" not in ai.split("```python")[0][-20:]


def test_code_suggestion_cannot_break_out_of_fence() -> None:
    body = rf.inline_body(_f(source=FindingSource.AI, code_suggestion="a\n```\n@everyone hi"))
    fences = [line for line in body.splitlines() if line.startswith("```")]
    assert len(fences) == 2  # opening and closing only


def test_summary_body() -> None:
    inline, leftover = (
        _f(),
        _f(id=uuid.uuid4(), severity=Severity.MEDIUM, title="Bare except", start_line=9),
    )
    body = rf.summary_body(
        rf.SummaryInput(
            job_id="j1",
            head_sha="abcdef1234",
            ai_summary="Adds lookup. @team please check",
            risk_level="high",
            findings=[inline, leftover],
            inline_ids={str(inline.id)},
            already_reported=2,
            dashboard_url="http://localhost:3000/pull-requests/p1",
        )
    )
    assert "**Risk:** High" in body and "@​team" in body
    assert "| 🔴 Critical | 1 |" in body and "| 🟡 Medium | 1 |" in body
    assert (
        "`app/users.py:9`: Bare except" in body
        and "app/users.py:4" not in body.split("<details>")[1]
    )
    assert "2 issues from an earlier review" in body
    assert body.endswith(rf.review_marker("j1", "abcdef1234"))
    clean = rf.summary_body(rf.SummaryInput("j", "s" * 40, None, None, [], set(), 0, "u"))
    assert "No issues found" in clean


def test_superseded_body_is_idempotent() -> None:
    once = rf.superseded_body("original", "1234567aaaa")
    twice = rf.superseded_body(once, "abcdef0bbbb")
    assert once.startswith("> ⚠️ **Superseded** by a newer ReviewPilot review of commit `1234567`")
    assert twice.count("Superseded") == 1 and "abcdef0" in twice and twice.endswith("original")
