"""A SemgrepRunner stand-in returning canned results (no binary needed)."""

from __future__ import annotations

from pathlib import Path

from app.analysis.semgrep import SemgrepMatch, SemgrepResult, SemgrepRunner


class FakeSemgrep(SemgrepRunner):
    def __init__(self, matches: list[SemgrepMatch] | None = None, status: str = "ok") -> None:
        super().__init__(binary="fake-semgrep")
        self.matches = matches or []
        self.status = status
        self.scanned: list[list[str]] = []
        self.excluded: list[set[str]] = []

    async def scan(self, target: Path, exclude_rules: set[str] | None = None) -> SemgrepResult:
        files = sorted(
            p.relative_to(target).as_posix()
            for p in target.rglob("*")
            if p.is_file() and p.name != ".semgrepignore"
        )
        self.scanned.append(files)
        self.excluded.append(set(exclude_rules or ()))
        if self.status != "ok":
            return SemgrepResult(status=self.status, errors=["fake failure"])
        present = [
            m for m in self.matches if m.path in files and m.rule_id not in (exclude_rules or ())
        ]
        return SemgrepResult(status="ok", matches=present, duration_ms=1)


def match(rule_id: str, path: str, line: int, *, severity: str = "critical") -> SemgrepMatch:
    return SemgrepMatch(
        rule_id=rule_id,
        path=path,
        start_line=line,
        end_line=line,
        message="SQL query is built from strings at runtime.",
        severity="ERROR",
        metadata={
            "title": "Potential SQL injection",
            "category": "security",
            "severity": severity,
            "confidence": "high",
            "cwe": "CWE-89",
            "impact": "An attacker may manipulate database queries.",
            "fix": "Use parameterised queries.",
        },
        fix=None,
    )
