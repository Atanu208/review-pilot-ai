"""Deterministic grounding, deduplication and calibration of AI findings.

Nothing a model says is trusted until it is checked against the real source and diff:
invented files, invented line numbers, quotes that don't exist, and findings on unchanged
code are all rejected here, before any second-pass LLM validation.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.ai.schemas import CandidateFinding
from app.analysis.source import SourceFile
from app.models.enums import FindingCategory, FindingSource, Severity
from app.schemas.finding import Finding

_WS = re.compile(r"\s+")
_WORD = re.compile(r"[a-z0-9]+")
LINE_SEARCH_RADIUS = 3
MIN_EVIDENCE_CHARS = 10  # non-whitespace characters
# Some categories should never be reported as more severe than this.
SEVERITY_CAP: dict[FindingCategory, Severity] = {
    FindingCategory.QUALITY: Severity.MEDIUM,
    FindingCategory.MAINTAINABILITY: Severity.MEDIUM,
    FindingCategory.BEST_PRACTICE: Severity.MEDIUM,
    FindingCategory.TESTING: Severity.MEDIUM,
    FindingCategory.PERFORMANCE: Severity.HIGH,
}


REVIEWER_CATEGORIES: dict[str, frozenset[FindingCategory]] = {
    "security": frozenset({FindingCategory.SECURITY}),
    "bugs": frozenset({FindingCategory.BUG}),
    "performance": frozenset({FindingCategory.PERFORMANCE}),
    "quality": frozenset(
        {
            FindingCategory.QUALITY,
            FindingCategory.MAINTAINABILITY,
            FindingCategory.TESTING,
            FindingCategory.BEST_PRACTICE,
        }
    ),
}
CATEGORY_PRIORITY: dict[FindingCategory, int] = {
    FindingCategory.SECURITY: 3,
    FindingCategory.BUG: 2,
    FindingCategory.PERFORMANCE: 1,
}


@dataclass(slots=True)
class Grounded:
    candidate: CandidateFinding
    reviewer: str
    start_line: int
    end_line: int
    evidence: str  # exact source text (not the model's paraphrase)


@dataclass(slots=True)
class GroundingReport:
    grounded: list[Grounded] = field(default_factory=list)
    rejected: Counter[str] = field(default_factory=Counter)


def _norm(text: str) -> str:
    # Models often re-space code when quoting it; compare without any whitespace. The match
    # is still confined to a few lines around the cited location, which keeps it precise.
    return _WS.sub("", text).lower()


def _shrink(needle: str, src: SourceFile, first: int, last: int) -> tuple[int, int]:
    """Narrow [first, last] to the smallest line span that still contains the evidence."""
    while first < last and needle in _norm(" ".join(src.lines[first:last])):
        first += 1
    while last > first and needle in _norm(" ".join(src.lines[first - 1 : last - 1])):
        last -= 1
    return first, last


def _locate(evidence: str, src: SourceFile, start: int, end: int) -> tuple[int, int] | None:
    """Find the claimed evidence at (or within a few lines of) the cited range."""
    needle = _norm(evidence)
    if not needle:
        return None
    span = max(end - start, 0)
    candidates = [start] + [start + d for r in range(1, LINE_SEARCH_RADIUS + 1) for d in (-r, r)]
    for first in candidates:
        last = first + span
        if first < 1 or last > len(src.lines):
            continue
        window = _norm(" ".join(src.lines[first - 1 : last]))
        if needle in window:
            return _shrink(needle, src, first, last)
        if window and window in needle and len(window) > 10:
            return first, last
    # Multi-line evidence quoted against a single cited line.
    n_lines = evidence.count("\n") + 1
    if n_lines > 1:
        for first in candidates:
            last = first + n_lines - 1
            if (
                first >= 1
                and last <= len(src.lines)
                and needle == _norm(" ".join(src.lines[first - 1 : last]))
            ):
                return first, last
    return None


def ground(
    candidates: list[tuple[str, CandidateFinding]],
    sources: dict[str, SourceFile],
    reviewed_paths: set[str],
) -> GroundingReport:
    report = GroundingReport()
    for reviewer, cand in candidates:
        allowed = REVIEWER_CATEGORIES.get(reviewer)
        if allowed is not None and cand.category not in allowed:
            # Out of this reviewer's lane: the specialist reviewer covers it. Accepting it
            # would create mislabelled, cross-category duplicates.
            report.rejected["out_of_scope"] += 1
            continue
        src = sources.get(cand.file_path)
        if src is None or cand.file_path not in reviewed_paths:
            report.rejected["unknown_file"] += 1
            continue
        if cand.end_line < cand.start_line:
            report.rejected["bad_line_range"] += 1
            continue
        if len(_norm(cand.evidence)) < MIN_EVIDENCE_CHARS:
            # e.g. "import os": too generic to prove where (or that) the problem is.
            report.rejected["evidence_too_vague"] += 1
            continue
        located = _locate(cand.evidence, src, cand.start_line, cand.end_line)
        if located is None:
            report.rejected["evidence_not_found"] += 1
            continue
        start, end = located
        if not src.diff.touches(start, end):
            report.rejected["unchanged_code"] += 1
            continue
        report.grounded.append(Grounded(cand, reviewer, start, end, src.snippet(start, end)))
    return report


_STOPWORDS = frozenset(
    {
        "potential",
        "possible",
        "the",
        "and",
        "for",
        "via",
        "with",
        "missing",
        "function",
        "use",
        "using",
        "issue",
        "risk",
        "improper",
        "unsafe",
        "leading",
        "not",
        "when",
        "without",
        "into",
        "from",
        "code",
        "call",
    }
)


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 2}


def similar(a_title: str, b_title: str) -> bool:
    ta, tb = _tokens(a_title) - _STOPWORDS, _tokens(b_title) - _STOPWORDS
    if not ta or not tb:
        return False
    shared = len(ta & tb)
    # Callers also require overlapping lines, so two shared keywords is a strong signal.
    return shared >= 2 or shared / len(ta | tb) >= 0.4


def shared_keywords(a_title: str, b_title: str) -> int:
    return len((_tokens(a_title) - _STOPWORDS) & (_tokens(b_title) - _STOPWORDS))


def overlaps(a: tuple[str, int, int], b: tuple[str, int, int], slack: int = 2) -> bool:
    return a[0] == b[0] and a[1] <= b[2] + slack and b[1] <= a[2] + slack


def dedupe_against(items: list[Grounded], static: list[Finding]) -> tuple[list[Grounded], int]:
    """Drop AI findings duplicating static-analysis ones, and duplicates among themselves."""
    kept: list[Grounded] = []
    dropped = 0
    # On a tie, keep the most specific domain (security > bug > performance > quality).
    for g in sorted(
        items,
        key=lambda x: (-CATEGORY_PRIORITY.get(x.candidate.category, 0), -x.candidate.confidence),
    ):
        loc = (g.candidate.file_path, g.start_line, g.end_line)
        dup_static = any(
            overlaps(loc, (s.file_path, s.start_line, s.end_line))
            and (s.category == g.candidate.category or similar(s.title, g.candidate.title))
            for s in static
        )
        # Between AI reviewers: same category on overlapping lines, a similar title, or the
        # *exact same* lines plus a shared keyword (one reviewer re-labelling another's
        # defect). Partial overlaps need the stricter title similarity, so distinct issues in
        # one function (e.g. an N+1 query inside a view missing auth) both survive.
        dup_ai = any(
            overlaps(loc, (k.candidate.file_path, k.start_line, k.end_line))
            and (
                k.candidate.category == g.candidate.category
                or similar(k.candidate.title, g.candidate.title)
                or (
                    (k.start_line, k.end_line) == (g.start_line, g.end_line)
                    and shared_keywords(k.candidate.title, g.candidate.title) >= 1
                )
            )
            for k in kept
        )
        if dup_static or dup_ai:
            dropped += 1
        else:
            kept.append(g)
    return kept, dropped


def calibrate(
    g: Grounded,
    *,
    verifier_confidence: float | None,
    verifier_severity: Severity | None,
) -> Finding | None:
    """Combine model + verifier opinions into the final, normalized finding."""
    c = g.candidate
    confidence = (
        c.confidence if verifier_confidence is None else min(c.confidence, verifier_confidence)
    )
    severity = c.severity
    if verifier_severity is not None and verifier_severity.rank < severity.rank:
        severity = verifier_severity  # the verifier may only lower severity, never raise it
    cap = SEVERITY_CAP.get(c.category)
    if cap is not None and severity.rank > cap.rank:
        severity = cap
    try:
        return Finding(
            title=c.title[:255],
            category=c.category,
            severity=severity,
            confidence=round(confidence, 3),
            file_path=c.file_path,
            start_line=g.start_line,
            end_line=g.end_line,
            description=c.description,
            impact=c.impact,
            suggested_fix=c.suggested_fix,
            code_suggestion=c.code_suggestion,
            source=FindingSource.AI,
            evidence=g.evidence,
            rule_id=f"ai.{g.reviewer}",
        )
    except ValidationError:
        return None
