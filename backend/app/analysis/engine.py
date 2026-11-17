"""Static analysis engine: Semgrep + Tree-sitter → normalized, diff-scoped findings."""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.analysis import treesitter
from app.analysis.semgrep import SemgrepMatch, SemgrepRunner, resolve_semgrep
from app.analysis.source import SourceFile
from app.analysis.workspace import UnsafePathError, Workspace
from app.core.config import Settings
from app.core.logging import get_logger
from app.models import ReviewRule
from app.models.enums import FindingCategory, FindingSource, Severity
from app.schemas.finding import Finding

logger = get_logger(__name__)

_CONFIDENCE = {"high": 0.9, "medium": 0.75, "low": 0.55}
_SEMGREP_SEVERITY = {"ERROR": Severity.HIGH, "WARNING": Severity.MEDIUM, "INFO": Severity.LOW}


@dataclass(slots=True)
class RuleConfig:
    """Organization/repository rule overrides (``review_rules``)."""

    disabled: set[str] = field(default_factory=set)
    severity: dict[str, Severity] = field(default_factory=dict)

    @classmethod
    def from_rules(cls, rules: list[ReviewRule]) -> RuleConfig:
        cfg = cls()
        # Repository-level rules win over organization-wide ones.
        for rule in sorted(rules, key=lambda r: r.repository_id is not None):
            if rule.enabled:
                cfg.disabled.discard(rule.rule_key)
            else:
                cfg.disabled.add(rule.rule_key)
            if rule.severity_override is not None:
                cfg.severity[rule.rule_key] = rule.severity_override
            else:
                cfg.severity.pop(rule.rule_key, None)
        return cfg

    def apply(self, finding: Finding) -> Finding | None:
        if finding.rule_id in self.disabled:
            return None
        override = self.severity.get(finding.rule_id or "")
        return finding.model_copy(update={"severity": override}) if override else finding


@dataclass(slots=True)
class AnalysisResult:
    findings: list[Finding]
    stats: dict[str, Any]


def semgrep_to_finding(match: SemgrepMatch, src: SourceFile) -> Finding | None:
    meta = match.metadata
    try:
        severity = Severity(str(meta.get("severity", "")).lower())
    except ValueError:
        severity = _SEMGREP_SEVERITY.get(match.severity.upper(), Severity.MEDIUM)
    try:
        category = FindingCategory(str(meta.get("category", "security")).lower())
    except ValueError:
        category = FindingCategory.SECURITY
    cwe = meta.get("cwe")
    description = match.message + (f" ({cwe})" if cwe else "")
    try:
        return Finding(
            title=str(meta.get("title") or match.rule_id)[:255],
            category=category,
            severity=severity,
            confidence=_CONFIDENCE.get(str(meta.get("confidence", "medium")).lower(), 0.75),
            file_path=src.path,
            start_line=match.start_line,
            end_line=max(match.end_line, match.start_line),
            description=description[:4000],
            impact=(str(meta["impact"])[:2000] if meta.get("impact") else None),
            suggested_fix=(str(meta["fix"])[:4000] if meta.get("fix") else None),
            code_suggestion=match.fix[:8000] if match.fix else None,
            source=FindingSource.SEMGREP,
            evidence=src.snippet(match.start_line, match.end_line),
            rule_id=match.rule_id,
        )
    except ValidationError as exc:
        logger.warning("semgrep_finding_invalid", rule_id=match.rule_id, errors=exc.error_count())
        return None


def dedupe(findings: list[Finding]) -> list[tuple[Finding, str]]:
    """Drop exact duplicates (same rule, file and line) and assign stable fingerprints."""
    seen: set[tuple[str, str | None, int]] = set()
    occurrences: Counter[str] = Counter()
    out: list[tuple[Finding, str]] = []
    for f in sorted(findings, key=lambda f: (f.file_path, f.start_line, f.rule_id or "")):
        key = (f.file_path, f.rule_id or f.title, f.start_line)
        if key in seen:
            continue
        seen.add(key)
        base = f.fingerprint(0)
        out.append((f, f.fingerprint(occurrences[base])))
        occurrences[base] += 1
    return out


class StaticAnalysisEngine:
    def __init__(self, semgrep: SemgrepRunner) -> None:
        self.semgrep = semgrep

    @classmethod
    def from_settings(cls, settings: Settings) -> StaticAnalysisEngine:
        return cls(
            SemgrepRunner(
                resolve_semgrep(settings.semgrep_bin),
                timeout_seconds=settings.semgrep_timeout_seconds,
                max_memory_mb=settings.semgrep_max_memory_mb,
            )
        )

    async def run_semgrep(
        self, files: list[SourceFile], config: RuleConfig
    ) -> tuple[list[Finding], dict[str, Any]]:
        by_path = {f.path: f for f in files}
        with Workspace() as ws:
            for src in files:
                try:
                    ws.write(src.path, src.content)
                except UnsafePathError:
                    logger.warning("unsafe_path_skipped", path=src.path)
            result = await self.semgrep.scan(ws.root, exclude_rules=config.disabled)
        findings = []
        for match in result.matches:
            matched = by_path.get(match.path)
            if matched is None or not matched.diff.touches(match.start_line, match.end_line):
                continue  # only report on lines this PR changed
            finding = semgrep_to_finding(match, matched)
            if finding is not None:
                findings.append(finding)
        stats = {
            "status": result.status,
            "duration_ms": result.duration_ms,
            "raw_matches": len(result.matches),
            "errors": result.errors[:5],
        }
        return findings, stats

    async def run_tree_sitter(
        self, files: list[SourceFile]
    ) -> tuple[list[Finding], dict[str, Any]]:
        started = time.perf_counter()

        def _analyze() -> tuple[list[Finding], int]:
            out: list[Finding] = []
            failures = 0
            for src in files:
                try:
                    out.extend(treesitter.analyze_file(src))
                except Exception:  # a parser crash must not sink the whole review
                    failures += 1
                    logger.exception("tree_sitter_failed", path=src.path)
            return out, failures

        findings, failures = await asyncio.to_thread(_analyze)
        return findings, {
            "status": "ok" if not failures else "partial",
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "files_failed": failures,
        }

    @staticmethod
    def finalize(findings: list[Finding], config: RuleConfig) -> list[tuple[Finding, str]]:
        configured = [f2 for f in findings if (f2 := config.apply(f)) is not None]
        return dedupe(configured)
