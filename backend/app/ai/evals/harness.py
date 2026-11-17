"""Evaluation harness for the AI review pipeline.

Scores the full graph (reviewers + grounding + validation + dedupe + calibration) on the
cases in ``dataset.json``: precision, recall, F1, line accuracy and injection robustness.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.ai.graph import AIReviewGraph, AIReviewRequest
from app.ai.router import LLMRouter
from app.analysis.diff import FileDiff
from app.analysis.languages import detect_language
from app.analysis.source import SourceFile
from app.schemas.finding import Finding

DATASET = Path(__file__).with_name("dataset.json")
# Categories an expected finding of a given category may legitimately be reported as.
COMPATIBLE = {
    "security": {"security"},
    "bug": {"bug"},
    "performance": {"performance"},
    "quality": {"quality", "maintainability", "best_practice", "testing"},
}


@dataclass(slots=True)
class CaseResult:
    case_id: str
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    exact_lines: int = 0
    forbidden_hits: int = 0
    injection_case: bool = False
    findings: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


@dataclass(slots=True)
class EvalReport:
    prompt_version: str
    provider: str
    cases: list[CaseResult]

    @property
    def tp(self) -> int:
        return sum(c.true_positives for c in self.cases)

    @property
    def fp(self) -> int:
        return sum(c.false_positives for c in self.cases)

    @property
    def fn(self) -> int:
        return sum(c.false_negatives for c in self.cases)

    def metrics(self) -> dict[str, float | int]:
        precision = self.tp / (self.tp + self.fp) if self.tp + self.fp else 1.0
        recall = self.tp / (self.tp + self.fn) if self.tp + self.fn else 1.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        inj = [c for c in self.cases if c.injection_case]
        return {
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
            "line_accuracy": round(sum(c.exact_lines for c in self.cases) / self.tp, 3)
            if self.tp
            else 1.0,
            "forbidden_hits": sum(c.forbidden_hits for c in self.cases),
            "injection_robustness": (
                round(sum(1 for c in inj if c.false_negatives == 0) / len(inj), 3) if inj else 1.0
            ),
            "errors": sum(1 for c in self.cases if c.error),
        }

    def to_json(self) -> str:
        return json.dumps(
            {
                "prompt_version": self.prompt_version,
                "provider": self.provider,
                "metrics": self.metrics(),
                "cases": [asdict(c) for c in self.cases],
            },
            indent=2,
        )


def load_cases(path: Path = DATASET) -> list[dict[str, Any]]:
    return list(json.loads(path.read_text(encoding="utf-8"))["cases"])


def build_sources(case: dict[str, Any]) -> list[SourceFile]:
    sources = []
    for f in case["files"]:
        lines = f["content"].splitlines()
        diff = FileDiff()
        diff.added_lines = set(f.get("added") or range(1, len(lines) + 1))
        diff.hunks = [(1, len(lines))]
        language = detect_language(f["path"])
        if language is None:
            raise RuntimeError(f"language is missing: {f['path']}")
        sources.append(SourceFile(f["path"], language, f["content"], diff))
    return sources


def _matches(finding: Finding, exp: dict[str, Any]) -> bool:
    lo, hi = exp["lines"]
    if finding.file_path != exp["file"] or finding.start_line > hi + 2 or finding.end_line < lo - 2:
        return False
    if finding.category.value not in COMPATIBLE.get(exp["category"], {exp["category"]}):
        return False
    text = f"{finding.title} {finding.description}".lower()
    return any(k.lower() in text for k in exp["keywords"])


def score_case(case: dict[str, Any], findings: list[Finding]) -> CaseResult:
    result = CaseResult(case_id=case["id"], injection_case=bool(case.get("injection")))
    result.findings = [
        {
            "file": f.file_path,
            "lines": [f.start_line, f.end_line],
            "category": f.category.value,
            "severity": f.severity.value,
            "confidence": f.confidence,
            "title": f.title,
        }
        for f in findings
    ]
    unmatched = list(findings)
    for exp in case.get("expected", []):
        hit = next((f for f in unmatched if _matches(f, exp)), None)
        if hit is None:
            result.false_negatives += 1
            continue
        unmatched.remove(hit)
        result.true_positives += 1
        if (hit.start_line, hit.end_line) == tuple(exp["lines"]) or exp["lines"][
            0
        ] <= hit.start_line <= exp["lines"][1]:
            result.exact_lines += 1
    # Valid but optional observations: neither credited nor penalised.
    for ok in case.get("acceptable", []):
        unmatched = [f for f in unmatched if not _matches(f, ok)]
    result.false_positives = len(unmatched)
    for zone in case.get("forbidden", []):
        lo, hi = zone["lines"]
        result.forbidden_hits += sum(
            1
            for f in findings
            if f.file_path == zone["file"] and f.start_line <= hi and f.end_line >= lo
        )
    return result


async def run_eval(
    router_factory: Callable[[], LLMRouter],
    *,
    cases: list[dict[str, Any]] | None = None,
    min_confidence: float = 0.75,  # same floor as production (AI_MIN_CONFIDENCE_FLOOR)
    verify: bool = True,
) -> EvalReport:
    from app.ai import prompts

    results: list[CaseResult] = []
    provider = ""
    for case in cases or load_cases():
        router = router_factory()
        provider = router.provider.name
        request = AIReviewRequest(
            sources=build_sources(case),
            static_findings=[],
            pr_title=case.get("title", case["id"]),
            repository="eval/repository",
            reviewers=["security", "bugs", "performance", "quality"],
            min_confidence=min_confidence,
            max_input_tokens=3000,
            max_output_tokens=2000,
            max_units=3,
            verify=verify,
        )
        try:
            ai = await AIReviewGraph(router, request).run()
            results.append(score_case(case, ai.findings))
        except Exception as exc:  # one broken case must not hide the others' results
            failed = CaseResult(case_id=case["id"], injection_case=bool(case.get("injection")))
            failed.error = f"{type(exc).__name__}: {exc}"[:300]
            failed.false_negatives = len(case.get("expected", []))
            results.append(failed)
    return EvalReport(prompt_version=prompts.PROMPT_VERSION, provider=provider, cases=results)
