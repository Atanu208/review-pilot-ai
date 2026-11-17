"""Render findings as GitHub review comments (pure functions, no I/O).

Model-written text is untrusted: @-mentions are neutralised (no pinging users or teams),
HTML comments that could forge our markers are stripped, secrets are redacted and every
body is length-capped (GitHub's limit is 65,536 characters).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.logging import redact_text
from app.models import ReviewFinding
from app.models.enums import FindingSource, Severity

MARKER = "reviewpilot"
MAX_BODY = 60_000
MAX_FIELD = 2_000
MAX_SUGGESTION_LINES = 20

SEVERITY_ICON = {
    Severity.CRITICAL: "🔴",
    Severity.HIGH: "🟠",
    Severity.MEDIUM: "🟡",
    Severity.LOW: "🔵",
    Severity.INFO: "⚪",
}
_MENTION = re.compile(r"(?<![\w`])@(?=[A-Za-z0-9-])")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_FENCE = re.compile(r"`{3,}")
LANG_HINT = {
    ".py": "python",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
}


def sanitize(text: str | None, limit: int = MAX_FIELD) -> str:
    if not text:
        return ""
    text = redact_text(text)
    text = _HTML_COMMENT.sub("", text)
    text = _MENTION.sub("@​", text)  # zero-width space: renders "@" without a mention
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _code(text: str) -> str:
    # Keep model text from closing our code fence early.
    return _FENCE.sub("``​`", redact_text(text))


def finding_marker(fingerprint: str) -> str:
    return f"<!-- {MARKER}:finding fp={fingerprint} -->"


def review_marker(job_id: str, head_sha: str) -> str:
    return f"<!-- {MARKER}:review job={job_id} sha={head_sha} -->"


def _label(f: ReviewFinding) -> str:
    icon = SEVERITY_ICON.get(f.severity, "")
    category = f.category.value.replace("_", " ")
    return f"{icon} **{f.severity.value.capitalize()} · {category}**"


def inline_body(f: ReviewFinding) -> str:
    parts = [f"{_label(f)}: {sanitize(f.title, 255)}", "", sanitize(f.description)]
    if f.impact:
        parts += ["", f"**Impact:** {sanitize(f.impact)}"]
    if f.suggested_fix:
        parts += ["", f"**Suggested fix:** {sanitize(f.suggested_fix)}"]
    if f.code_suggestion:
        lines = f.code_suggestion.strip("\n").splitlines()
        if len(lines) <= MAX_SUGGESTION_LINES:
            # Semgrep autofixes replace exactly the matched lines, so they can be applied with
            # one click. AI snippets are shown as plain code: they may not be exact replacements.
            if f.source == FindingSource.SEMGREP:
                lang = "suggestion"
            else:
                lang = next((v for k, v in LANG_HINT.items() if f.file_path.endswith(k)), "")
            parts += ["", f"```{lang}", _code("\n".join(lines)), "```"]
    source = {"ai": "AI review", "semgrep": "Semgrep", "tree_sitter": "Tree-sitter"}.get(
        f.source.value, f.source.value
    )
    meta = f"<sub>{source}" + (f" · `{sanitize(f.rule_id, 120)}`" if f.rule_id else "")
    meta += f" · confidence {float(f.confidence):.0%} · ReviewPilot AI</sub>"
    parts += ["", meta, finding_marker(f.fingerprint)]
    body = "\n".join(parts)
    return body if len(body) <= MAX_BODY else body[:MAX_BODY]


@dataclass(slots=True)
class SummaryInput:
    job_id: str
    head_sha: str
    ai_summary: str | None
    risk_level: str | None
    findings: Sequence[ReviewFinding]  # everything considered for publication
    inline_ids: set[str]  # finding ids posted as inline comments
    already_reported: int  # unchanged findings already posted on this PR earlier
    dashboard_url: str


def summary_body(s: SummaryInput) -> str:
    counts = Counter(f.severity for f in s.findings)
    lines = ["## 🛩️ ReviewPilot AI review", ""]
    if s.risk_level:
        lines += [f"**Risk:** {s.risk_level.capitalize()}", ""]
    if s.ai_summary:
        lines += [sanitize(s.ai_summary, 3000), ""]
    if s.findings:
        order = sorted(Severity, key=lambda x: -x.rank)
        lines += [
            "| Severity | Findings |",
            "|---|---|",
            *[
                f"| {SEVERITY_ICON[sev]} {sev.value.capitalize()} | {counts[sev]} |"
                for sev in order
                if counts[sev]
            ],
            "",
        ]
    else:
        lines += ["No issues found in the changed code. ✅", ""]

    leftovers = [f for f in s.findings if str(f.id) not in s.inline_ids]
    if leftovers:
        lines += ["<details><summary>Findings not posted inline</summary>", ""]
        for f in sorted(leftovers, key=lambda x: -x.severity.rank)[:50]:
            loc = f"`{sanitize(f.file_path, 300)}:{f.start_line}`"
            lines.append(f"- {SEVERITY_ICON.get(f.severity, '')} {loc}: {sanitize(f.title, 255)}")
        lines += ["", "</details>", ""]
    if s.already_reported:
        noun = "issue" if s.already_reported == 1 else "issues"
        lines += [
            f"_{s.already_reported} {noun} from an earlier review of this PR are still "
            "present and were not repeated._",
            "",
        ]
    lines += [
        f"<sub>Commit {s.head_sha[:7]} · [Open in ReviewPilot]({s.dashboard_url}) · "
        "Automated review: verify before acting.</sub>",
        review_marker(s.job_id, s.head_sha),
    ]
    body = "\n".join(lines)
    return body if len(body) <= MAX_BODY else body[:MAX_BODY]


def superseded_body(previous: str, new_sha: str) -> str:
    note = f"> ⚠️ **Superseded** by a newer ReviewPilot review of commit `{new_sha[:7]}`.\n\n"
    if previous.startswith("> ⚠️ **Superseded**"):
        previous = previous.split("\n\n", 1)[-1]
    return (note + previous)[:MAX_BODY]
