"""Scripted LLM behaviours for tests and offline evals (no network, deterministic)."""

from __future__ import annotations

import re
from typing import Any

from app.ai.evals.harness import load_cases
from app.ai.types import LLMRequest

CATEGORY_TO_REVIEWER = {"security": "security", "bug": "bugs", "performance": "performance"}
_FILE_LABEL = re.compile(r"<<<UNTRUSTED_CODE_[0-9a-f]+ (\S+) \(")


def _files_in(request: LLMRequest) -> set[str]:
    return set(_FILE_LABEL.findall(request.user))


def noisy_reviewer(request: LLMRequest, model: str) -> dict[str, Any]:
    """A model that finds the real issues but also hallucinates in every common way.

    The pipeline's grounding, validation and dedupe must remove all of the noise.
    """
    if request.stage == "validate":
        n = int(request.metadata.get("candidate_count", 0))
        return {
            "verdicts": [{"index": i, "verdict": "confirm", "confidence": 0.9} for i in range(n)]
        }
    if request.stage == "summary":
        return {"summary": "Scripted summary.", "risk_level": "medium", "highlights": ["scripted"]}

    reviewer = request.stage.removeprefix("review.")
    files = _files_in(request)
    findings: list[dict[str, Any]] = []
    for case in load_cases():
        by_path = {f["path"]: f for f in case["files"]}
        if not files & set(by_path):
            continue
        for exp in case.get("expected", []):
            if CATEGORY_TO_REVIEWER.get(exp["category"]) != reviewer:
                continue
            content = by_path[exp["file"]]["content"].splitlines()
            lo, hi = exp["lines"]
            evidence = "\n".join(content[lo - 1 : hi])
            real = {
                "title": f"{exp['keywords'][0].title()} issue",
                "category": exp["category"],
                "severity": "high",
                "confidence": 0.92,
                "file_path": exp["file"],
                "start_line": lo,
                "end_line": hi,
                "description": f"Real problem: {' '.join(exp['keywords'])}.",
                "evidence": evidence,
            }
            findings.append(real)
            # Off-by-one line with correct evidence: must be corrected, not duplicated.
            findings.append({**real, "start_line": lo + 1, "end_line": hi + 1, "confidence": 0.8})
        path = next(iter(by_path))
        n_lines = len(by_path[path]["content"].splitlines())
        findings += [
            {  # invented file
                "title": "Hardcoded password",
                "category": "security",
                "severity": "critical",
                "confidence": 0.95,
                "file_path": "config/secrets.py",
                "start_line": 1,
                "end_line": 1,
                "description": "Password in source.",
                "evidence": "PASSWORD = 'hunter2'",
            },
            {  # invented evidence on a real file
                "title": "Use of eval",
                "category": "security",
                "severity": "high",
                "confidence": 0.9,
                "file_path": path,
                "start_line": 1,
                "end_line": 1,
                "description": "eval on user input.",
                "evidence": "eval(request.args['code'])",
            },
            {  # line number beyond the end of the file
                "title": "Null dereference",
                "category": "bug",
                "severity": "medium",
                "confidence": 0.9,
                "file_path": path,
                "start_line": n_lines + 40,
                "end_line": n_lines + 40,
                "description": "Possible None.",
                "evidence": "x.y",
            },
        ]
        for file in case["files"]:
            added = set(file.get("added") or [])
            if not added:
                continue
            lines = file["content"].splitlines()
            unchanged = next(
                i for i in range(len(lines), 0, -1) if i not in added and lines[i - 1].strip()
            )
            findings.append(
                {  # real code, but on a line the PR did not change
                    "title": "Command injection",
                    "category": "security",
                    "severity": "critical",
                    "confidence": 0.97,
                    "file_path": file["path"],
                    "start_line": unchanged,
                    "end_line": unchanged,
                    "description": "Shell injection.",
                    "evidence": lines[unchanged - 1].strip(),
                }
            )
    if reviewer == "quality":
        # Cross-category duplicate of a security issue: dedupe must drop it.
        for case in load_cases():
            for exp in case.get("expected", []):
                if exp["category"] == "security" and exp["file"] in files:
                    content = next(f["content"] for f in case["files"] if f["path"] == exp["file"])
                    lo, hi = exp["lines"]
                    findings.append(
                        {
                            "title": f"{exp['keywords'][0].title()} {exp['keywords'][1]} risk",
                            "category": "best_practice",
                            "severity": "medium",
                            "confidence": 0.85,
                            "file_path": exp["file"],
                            "start_line": lo,
                            "end_line": hi,
                            "description": "Duplicate of the security finding.",
                            "evidence": "\n".join(content.splitlines()[lo - 1 : hi]),
                        }
                    )
    return {"findings": findings}
